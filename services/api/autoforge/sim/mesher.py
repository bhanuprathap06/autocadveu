"""Gmsh tetrahedral mesher, run as a separate process.

Usage: python -m autoforge.sim.mesher <request.json>

Gmsh ships its own OpenCASCADE build, so it never shares a process with the
OCP kernel. The request names the BREP file, the target element size, and the
named boundary-condition faces as (centroid, area) signatures computed by the
kernel. Each gmsh surface is matched to a signature; unmatched or ambiguous
faces are reported as errors instead of being guessed.

Output: an .npz with nodes, C3D10 connectivity (CalculiX/Abaqus node order),
boundary triangles, per-named-face triangle sets and mesh quality metrics.
"""

from __future__ import annotations

import json
import sys

import numpy as np

# Gmsh 10-node tet -> Abaqus/CalculiX C3D10 node order (edges 1-3 and 2-3 swapped)
GMSH_TO_CCX_TET10 = [0, 1, 2, 3, 4, 5, 6, 7, 9, 8]


def run(req: dict) -> dict:
    import gmsh

    gmsh.initialize(["-nopopup"])
    try:
        gmsh.option.setNumber("General.Terminal", 0)
        gmsh.option.setNumber("General.NumThreads", int(req.get("threads", 1)))
        gmsh.model.add("af")
        gmsh.model.occ.importShapes(req["brep"])
        gmsh.model.occ.synchronize()
        vols = gmsh.model.getEntities(3)
        if not vols:
            raise RuntimeError("Geometry contains no volume to mesh")
        size = float(req["size_mm"])
        gmsh.option.setNumber("Mesh.MeshSizeMax", size)
        gmsh.option.setNumber("Mesh.MeshSizeMin", float(req.get("min_size_mm") or size / 10))
        gmsh.option.setNumber("Mesh.MeshSizeFromCurvature", 12)
        gmsh.option.setNumber("Mesh.ElementOrder", 2)
        gmsh.option.setNumber("Mesh.SecondOrderLinear", 0)
        gmsh.option.setNumber("Mesh.HighOrderOptimize", 1 if req.get("optimize", True) else 0)
        gmsh.option.setNumber("Mesh.Optimize", 1 if req.get("optimize", True) else 0)
        gmsh.option.setNumber("Mesh.Algorithm3D", 1)  # Delaunay
        gmsh.model.mesh.generate(3)

        tags, coords, _ = gmsh.model.mesh.getNodes()
        nodes = np.asarray(coords).reshape(-1, 3)
        node_ids = np.asarray(tags, dtype=np.int64)
        etypes, etags, enodes = gmsh.model.mesh.getElements(3)
        if 11 not in list(etypes):
            raise RuntimeError("Mesher did not produce quadratic tetrahedra")
        k = list(etypes).index(11)
        tets = np.asarray(enodes[k], dtype=np.int64).reshape(-1, 10)[:, GMSH_TO_CCX_TET10]
        tet_ids = np.asarray(etags[k], dtype=np.int64)
        if len(tets) > int(req.get("max_elements", 2_000_000)):
            raise RuntimeError("Mesh exceeds the element limit; increase element size")
        qual = np.asarray(gmsh.model.mesh.getElementQualities(tet_ids.tolist(), "minSICN"))

        # Surface signatures and matching
        diag = float(np.linalg.norm(nodes.max(0) - nodes.min(0)))
        tol = max(1e-6 * diag, 1e-6)
        surfaces = []
        for dim, tag in gmsh.model.getEntities(2):
            c = np.array(gmsh.model.occ.getCenterOfMass(dim, tag))
            a = gmsh.model.occ.getMass(dim, tag)
            surfaces.append((tag, c, a))
        face_sets: dict[str, list[int]] = {}
        errors = []
        for name, sigs in req["faces"].items():
            tags_for_name = []
            for sig in sigs:
                sc, sa = np.array(sig["centroid"]), float(sig["area"])
                cands = [t for t, c, a in surfaces if np.linalg.norm(c - sc) <= 1e3 * tol and abs(a - sa) <= 1e-4 * max(sa, 1e-9)]
                if len(cands) != 1:
                    errors.append(f"Face {name}: {'no' if not cands else 'ambiguous'} match in mesh geometry")
                    continue
                tags_for_name.append(cands[0])
            face_sets[name] = tags_for_name
        if errors:
            raise RuntimeError("; ".join(errors))

        def tris_of(tag: int) -> np.ndarray:
            ts, _, ns = gmsh.model.mesh.getElements(2, tag)
            if 9 not in list(ts):
                return np.zeros((0, 6), dtype=np.int64)
            j = list(ts).index(9)
            return np.asarray(ns[j], dtype=np.int64).reshape(-1, 6)

        all_tris = np.vstack([tris_of(t) for t, _, _ in surfaces]) if surfaces else np.zeros((0, 6), np.int64)
        named = {name: np.vstack([tris_of(t) for t in tags]) if tags else np.zeros((0, 6), np.int64)
                 for name, tags in face_sets.items()}
        out = {"nodes": nodes, "node_ids": node_ids, "tets": tets, "tet_ids": tet_ids, "boundary_tris": all_tris}
        for i, (name, arr) in enumerate(named.items()):
            out[f"face_{i}"] = arr
        np.savez_compressed(req["out"], **out)
        meta = {
            "nodes": int(len(nodes)), "elements": int(len(tets)), "element_type": "C3D10 (10-node quadratic tetrahedron)",
            "size_mm": size, "min_size_mm": float(req.get("min_size_mm") or size / 10),
            "quality_metric": "minSICN (1 = ideal, <=0 = inverted)",
            "quality_min": float(qual.min()), "quality_mean": float(qual.mean()),
            "quality_below_0_2": int(np.sum(qual < 0.2)), "face_names": list(named.keys()),
            "gmsh_version": gmsh.__version__,
        }
        return meta
    finally:
        gmsh.finalize()


def main() -> None:
    with open(sys.argv[1]) as fh:
        req = json.load(fh)
    try:
        meta = run(req)
        print(json.dumps({"ok": True, "meta": meta}))
    except Exception as exc:  # report structured failure to the parent
        print(json.dumps({"ok": False, "error": str(exc)}))
        sys.exit(2)


if __name__ == "__main__":
    main()
