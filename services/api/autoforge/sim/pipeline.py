"""End-to-end simulation pipeline (runs inside an isolated job subprocess).

kernel recompute -> BREP + boundary-face signatures -> gmsh (separate process)
-> CalculiX deck -> ccx (resource-limited) -> parse .frd/.dat -> results.

Every result carries: units, solver + version, mesh statistics and quality,
material and boundary conditions used, an equilibrium check comparing applied
loads with support reactions, and warnings. A requested mesh-sensitivity study
re-runs on a refined mesh and reports the change in key results.
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
import time
from typing import Any, Callable

import numpy as np

from ..kernel import io as kio
from ..kernel import occ
from ..kernel import schema as S
from ..kernel.engine import recompute
from ..kernel.naming import FeatureError
from . import calculix as C
from .schema import StaticSetup, ThermalSetup, bc_faces, parse_setup

Progress = Callable[[str, float], None]


def _noop(stage: str, frac: float) -> None:
    pass


def prepare_geometry(content: dict[str, Any], setup: Any, workdir: str, blob_loader=None) -> dict[str, Any]:
    doc = S.Document.model_validate(content)
    res = recompute(doc, blob_loader=blob_loader, use_cache=False)
    body = res.ctx.bodies.get(setup.body)
    if body is None:
        failed = [f.message for f in res.features if f.status == "error"]
        raise C.SolverError(f"Body {setup.body!r} is not available in this revision" + (f" ({failed[0]})" if failed else ""),
                            "invalid_setup")
    if len(occ.solids(body.shape)) != 1:
        raise C.SolverError("Simulation requires a single closed solid body", "invalid_setup")
    if not occ.is_valid(body.shape):
        raise C.SolverError("Body geometry is invalid; fix the model before simulating", "invalid_setup")
    sigs: dict[str, list[dict[str, Any]]] = {}
    for name in bc_faces(setup):
        faces = body.faces_named(name)
        if not faces:
            raise C.SolverError(f"Boundary condition face {name!r} does not exist on {setup.body!r}", "invalid_setup")
        sigs[name] = []
        for f in faces:
            g = occ.face_geometry(f)
            sigs[name].append({"centroid": g["centroid"], "area": g["area"]})
    brep = os.path.join(workdir, "body.brep")
    with open(brep, "wb") as fh:
        fh.write(kio.export_brep(body.shape))
    bb = occ.bbox(body.shape)
    diag = float(np.linalg.norm(np.subtract(bb[1], bb[0])))
    mp = occ.volume_props(body.shape)
    return {"brep": brep, "faces": sigs, "diag": diag, "volume_mm3": mp.Mass(), "bbox": bb}


def run_mesher(geo: dict[str, Any], size: float, min_size: float | None, optimize: bool, workdir: str, tag: str,
               timeout_s: int, max_elements: int) -> dict[str, Any]:
    req = {"brep": geo["brep"], "faces": geo["faces"], "size_mm": size, "min_size_mm": min_size, "optimize": optimize,
           "out": os.path.join(workdir, f"mesh_{tag}.npz"), "max_elements": max_elements}
    rp = os.path.join(workdir, f"mesh_{tag}.json")
    with open(rp, "w") as fh:
        json.dump(req, fh)
    pkg_root = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
    env = {**os.environ, "PYTHONPATH": pkg_root, "OMP_NUM_THREADS": "1"}
    try:
        p = subprocess.run([sys.executable, "-m", "autoforge.sim.mesher", rp], capture_output=True, text=True, timeout=timeout_s,
                           env=env, cwd=workdir)
    except subprocess.TimeoutExpired as exc:
        raise C.SolverError(f"Meshing exceeded {timeout_s} s; increase the element size", "timed_out") from exc
    line = (p.stdout.strip().splitlines() or ["{}"])[-1]
    try:
        out = json.loads(line)
    except json.JSONDecodeError:
        out = {"ok": False, "error": (p.stderr or "mesher crashed")[-500:]}
    if not out.get("ok"):
        raise C.SolverError(f"Meshing failed: {out.get('error')}", "mesh_failed")
    meta = out["meta"]
    meta["path"] = req["out"]
    return meta


def _solve_once(setup: Any, geo: dict[str, Any], size: float, workdir: str, tag: str, ccx: str, timeout_s: int, mem_mb: int,
                max_nodes: int, cancel_check, progress: Progress) -> dict[str, Any]:
    progress(f"meshing ({tag})", 0.2)
    meta = run_mesher(geo, size, setup.mesh.min_size_mm, setup.mesh.optimize, workdir, tag, timeout_s, max_nodes * 4)
    if meta["nodes"] > max_nodes:
        raise C.SolverError(f"Mesh has {meta['nodes']} nodes, above the {max_nodes} node quota. Increase element size.",
                            "quota_exceeded")
    mesh = C.Mesh.load(meta["path"], meta["face_names"])
    progress(f"writing deck ({tag})", 0.35)
    if isinstance(setup, StaticSetup):
        deck, info = C.write_static_deck(mesh, setup)
    else:
        deck, info = C.write_thermal_deck(mesh, setup)
    job = f"job_{tag}"
    with open(os.path.join(workdir, job + ".inp"), "w") as fh:
        fh.write(deck)
    progress(f"solving ({tag})", 0.5)
    run = C.run_ccx(workdir, job, ccx, timeout_s, mem_mb, cancel_check)
    progress(f"post-processing ({tag})", 0.8)
    frd = C.parse_frd(os.path.join(workdir, job + ".frd"))
    totals = C.parse_dat_totals(os.path.join(workdir, job + ".dat"))
    ids = mesh.node_ids
    P = mesh.nodes
    out: dict[str, Any] = {"mesh": {k: v for k, v in meta.items() if k != "path"}, "solver_run": {
        "wall_s": run["wall_s"], "warnings": run["warnings"]}, "deck_info": info, "job": job}
    warnings: list[str] = []
    if meta["quality_min"] < 0.1 or meta["quality_below_0_2"] > 0.01 * meta["elements"]:
        warnings.append(f"Poor element quality (min minSICN {meta['quality_min']:.3f}); results near distorted elements are "
                        "unreliable. Refine or simplify geometry.")
    if meta["elements"] < 500:
        warnings.append("Very coarse mesh (<500 elements); run a mesh-sensitivity study before relying on stresses.")
    surf = np.unique(mesh.boundary_tris[:, :3].ravel())
    idx = {int(n): i for i, n in enumerate(ids)}
    if isinstance(setup, StaticSetup):
        U = frd.get("DISP")
        Sx = frd.get("STRESS")
        if U is None or Sx is None:
            raise C.SolverError("Solver output is missing displacement or stress results", "solver_failed")
        u = np.array([U.get(int(n), np.zeros(3))[:3] for n in ids])
        s = np.array([Sx.get(int(n), np.zeros(6))[:6] for n in ids])
        vm = C.von_mises(s)
        umag = np.linalg.norm(u, axis=1)
        iu, iv = int(np.argmax(umag)), int(np.argmax(vm))
        rf = totals.get("total force (fx,fy,fz)")
        applied = np.array(info.get("applied_nodal_force_n", [0, 0, 0]), float)
        grav = [l for l in setup.loads if l.type == "gravity"]
        if grav and setup.material.density_kg_m3:
            g = np.array(grav[0].vector_mm_s2)
            applied = applied + geo["volume_mm3"] * setup.material.density_kg_m3 * 1e-12 * g
        eq = None
        if rf is not None:
            resid = np.array(rf) + applied
            scale = max(float(np.linalg.norm(applied)), 1e-9)
            eq = {"applied_n": applied.tolist(), "reaction_n": rf, "imbalance_n": resid.tolist(),
                  "relative_imbalance": float(np.linalg.norm(resid) / scale), "ok": bool(np.linalg.norm(resid) / scale < 1e-3)}
            if not eq["ok"]:
                warnings.append("Reaction forces do not balance the applied loads within 0.1%; check supports and loads.")
        sy = setup.material.yield_strength_mpa
        out["summary"] = {
            "analysis": "linear static", "units": {"length": "mm", "force": "N", "stress": "MPa"},
            "max_displacement_mm": float(umag[iu]), "max_displacement_at_mm": P[iu].tolist(),
            "max_von_mises_mpa": float(vm[iv]), "max_von_mises_at_mm": P[iv].tolist(),
            "max_surface_von_mises_mpa": float(vm[[idx[int(n)] for n in surf]].max()) if len(surf) else None,
            "equilibrium": eq,
            "yield_strength_mpa": sy, "safety_factor_vs_yield": (sy / float(vm[iv])) if sy and vm[iv] > 0 else None,
            "stress_note": "Nodal von Mises stresses are extrapolated from integration points; peak values at sharp re-entrant "
                           "corners and point-like supports are mesh-dependent singularities.",
        }
        if out["summary"]["safety_factor_vs_yield"] is not None:
            out["summary"]["safety_factor_note"] = ("Ratio of material yield strength to peak von Mises stress for this load case "
                                                    "only. Not a design approval: fatigue, buckling, contact, tolerances and "
                                                    "load uncertainty are not considered.")
        field = {"displacement": u, "von_mises": vm}
    else:
        T = frd.get("NDTEMP")
        if T is None:
            raise C.SolverError("Solver output is missing temperatures", "solver_failed")
        t = np.array([T.get(int(n), np.zeros(1))[0] for n in ids])
        it_max, it_min = int(np.argmax(t)), int(np.argmin(t))
        rfl = totals.get("total heat generation (fx)") or next((v for k, v in totals.items() if "heat" in k), None)
        out["summary"] = {
            "analysis": "steady-state heat conduction", "units": {"length": "mm", "temperature": "degC", "power": "W"},
            "max_temperature_c": float(t[it_max]), "max_temperature_at_mm": P[it_max].tolist(),
            "min_temperature_c": float(t[it_min]), "min_temperature_at_mm": P[it_min].tolist(),
            "heat_through_fixed_temperature_faces_w": rfl, "applied_heat_w": info.get("applied_heat_w"),
        }
        field = {"temperature": t}
    # Compact surface field for visualisation: corner triangles of the boundary mesh.
    tris = mesh.boundary_tris[:, :3]
    used = np.unique(tris.ravel())
    remap = {int(n): i for i, n in enumerate(used)}
    viz: dict[str, Any] = {
        "positions": P[[idx[int(n)] for n in used]].astype(np.float32).ravel().tolist(),
        "indices": [remap[int(n)] for n in tris.ravel()],
    }
    for k, v in field.items():
        sel = v[[idx[int(n)] for n in used]]
        viz[k] = sel.astype(np.float32).ravel().tolist()
        viz[f"{k}_range"] = [float(np.min(sel if sel.ndim == 1 else np.linalg.norm(sel, axis=1))),
                             float(np.max(sel if sel.ndim == 1 else np.linalg.norm(sel, axis=1)))]
    out["viz"] = viz
    out["warnings"] = warnings
    out["log"] = run["log"]
    return out


def run(content: dict[str, Any], setup_d: dict[str, Any], workdir: str, ccx: str = "ccx", timeout_s: int = 900,
        mem_mb: int = 6144, max_nodes: int = 400_000, blob_loader=None, cancel_check=None,
        progress: Progress = _noop) -> dict[str, Any]:
    t0 = time.time()
    setup = parse_setup(setup_d)
    version = C.ccx_version(ccx)
    progress("preparing geometry", 0.05)
    try:
        geo = prepare_geometry(content, setup, workdir, blob_loader)
    except FeatureError as exc:
        raise C.SolverError(str(exc), "invalid_setup") from exc
    size = setup.mesh.size_mm or geo["diag"] / 25.0
    primary = _solve_once(setup, geo, size, workdir, "a", ccx, timeout_s, mem_mb, max_nodes, cancel_check, progress)
    result = primary
    convergence = None
    if setup.convergence_study:
        refined = _solve_once(setup, geo, size / 1.6, workdir, "b", ccx, timeout_s, mem_mb, max_nodes, cancel_check, progress)
        keys = ["max_displacement_mm", "max_von_mises_mpa"] if isinstance(setup, StaticSetup) else ["max_temperature_c",
                                                                                                    "min_temperature_c"]
        changes = {}
        for k in keys:
            a, b = primary["summary"][k], refined["summary"][k]
            changes[k] = {"coarse": a, "fine": b, "relative_change": abs(b - a) / max(abs(b), 1e-12)}
        ok = all(c["relative_change"] < 0.05 for c in changes.values())
        convergence = {"sizes_mm": [size, size / 1.6], "changes": changes, "converged_5pct": ok,
                       "note": "Two-level refinement study. Peak stress at singularities may not converge with refinement."}
        if not ok:
            refined["warnings"].append("Key results changed by more than 5% on refinement: the mesh is not converged.")
        result = refined
    result["convergence"] = convergence
    result["solver"] = {"name": "CalculiX ccx", "version": version, "mesher": f"gmsh {result['mesh'].get('gmsh_version')}",
                        "element": "C3D10", "status": "completed", "total_wall_s": time.time() - t0,
                        "convergence": "Linear system solved directly (SPOOLES); no iterative convergence criterion applies."
                        if isinstance(setup, StaticSetup) else "Linear steady-state conduction solved directly."}
    result["inputs"] = {"setup": setup.model_dump(mode="json"), "volume_mm3": geo["volume_mm3"], "bbox_mm": geo["bbox"]}
    return result
