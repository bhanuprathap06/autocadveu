"""Tessellation of B-rep bodies into viewport meshes.

The mesh is a *derived cache* of the B-rep, never the model of record. Each
triangle range is tagged with the persistent face name so the viewport can
select faces and send stable references back; edges carry their persistent
reference (adjacent face names + hint).
"""

from __future__ import annotations

import base64
from typing import Any

import numpy as np
from OCP.BRep import BRep_Tool
from OCP.BRepAdaptor import BRepAdaptor_Curve
from OCP.BRepLib import BRepLib_ToolTriangulatedShape
from OCP.BRepMesh import BRepMesh_IncrementalMesh
from OCP.GCPnts import GCPnts_TangentialDeflection
from OCP.TopAbs import TopAbs_REVERSED
from OCP.TopLoc import TopLoc_Location

from . import occ
from .naming import Body, edge_refs


def b64(arr: np.ndarray) -> str:
    return base64.b64encode(np.ascontiguousarray(arr).tobytes()).decode("ascii")


def mesh_params(shape: Any, quality: str = "normal") -> tuple[float, float]:
    bb = occ.bbox(shape)
    diag = float(np.linalg.norm(np.subtract(bb[1], bb[0]))) if bb else 100.0
    factor = {"draft": 0.004, "normal": 0.0015, "fine": 0.0005}.get(quality, 0.0015)
    ang = {"draft": 0.6, "normal": 0.35, "fine": 0.15}.get(quality, 0.35)
    return float(min(max(diag * factor, 0.005), 5.0)), ang


def mesh_shape(shape: Any, quality: str = "normal") -> tuple[float, float]:
    lin, ang = mesh_params(shape, quality)
    BRepMesh_IncrementalMesh(shape, lin, False, ang, True)
    return lin, ang


def face_triangles(face: Any) -> tuple[np.ndarray, np.ndarray, np.ndarray] | None:
    loc = TopLoc_Location()
    tri = occ.st(BRep_Tool, "Triangulation")(face, loc)
    if tri is None:
        return None
    trsf = loc.Transformation()
    if not tri.HasNormals():
        occ.st(BRepLib_ToolTriangulatedShape, "ComputeNormals")(face, tri)
    n = tri.NbNodes()
    pts = np.empty((n, 3))
    nrm = np.empty((n, 3))
    ident = loc.IsIdentity()
    for i in range(1, n + 1):
        p = tri.Node(i)
        d = tri.Normal(i)
        if not ident:
            p = p.Transformed(trsf)
            d = d.Transformed(trsf)
        pts[i - 1] = (p.X(), p.Y(), p.Z())
        nrm[i - 1] = (d.X(), d.Y(), d.Z())
    m = tri.NbTriangles()
    idx = np.empty((m, 3), dtype=np.int64)
    for t in range(1, m + 1):
        a, b, c = tri.Triangle(t).Get()
        idx[t - 1] = (a - 1, b - 1, c - 1)
    if face.Orientation() == TopAbs_REVERSED:
        idx = idx[:, [0, 2, 1]]
        nrm = -nrm
    return pts, nrm, idx


def edge_polyline(edge: Any, lin: float, ang: float) -> np.ndarray | None:
    if occ.st(BRep_Tool, "Degenerated")(edge):
        return None
    ad = BRepAdaptor_Curve(edge)
    disc = GCPnts_TangentialDeflection(ad, ang, lin)
    k = disc.NbPoints()
    if k < 2:
        return None
    out = np.empty((k, 3))
    for i in range(1, k + 1):
        p = disc.Value(i)
        out[i - 1] = (p.X(), p.Y(), p.Z())
    return out


def tessellate_body(body: Body, quality: str = "normal", include_geometry: bool = True) -> dict[str, Any]:
    lin, ang = mesh_shape(body.shape, quality)
    P, N, I = [], [], []
    faces_meta = []
    base = 0
    tri_start = 0
    for face, names in body.names_by_face():
        r = face_triangles(face)
        if r is None:
            continue
        pts, nrm, idx = r
        P.append(pts)
        N.append(nrm)
        I.append(idx + base)
        meta = {"name": names[0], "names": names, "start": tri_start, "count": int(len(idx)),
                "stable": not names[0].startswith(f"{body.name}:face#")}
        if include_geometry:
            g = occ.face_geometry(face)
            meta["geometry"] = {k: (round(v, 9) if isinstance(v, float) else v) for k, v in g.items()}
        faces_meta.append(meta)
        base += len(pts)
        tri_start += len(idx)
    positions = np.vstack(P).astype(np.float32) if P else np.zeros((0, 3), np.float32)
    normals = np.vstack(N).astype(np.float32) if N else np.zeros((0, 3), np.float32)
    indices = np.vstack(I).astype(np.uint32) if I else np.zeros((0, 3), np.uint32)

    edges_meta = []
    for e, ref in edge_refs(body):
        pl = edge_polyline(e, lin, ang)
        if pl is None:
            continue
        g = occ.edge_geometry(e)
        geo = {k: g[k] for k in ("center", "axis_dir", "start", "end", "midpoint") if k in g}
        edges_meta.append({"ref": ref, "points": b64(pl.astype(np.float32)), "n": int(len(pl)), "curve": g["curve"],
                           "length": g["length"], "radius": g.get("radius"), "geometry": geo})
    verts = [occ.vertex_point(v) for v in occ.vertices(body.shape)]
    bb = occ.bbox(body.shape)
    return {
        "name": body.name,
        "positions": b64(positions),
        "normals": b64(normals),
        "indices": b64(indices),
        "vertex_count": int(len(positions)),
        "triangle_count": int(len(indices)),
        "faces": faces_meta,
        "edges": edges_meta,
        "vertices": verts,
        "bbox": bb,
        "deflection": {"linear_mm": lin, "angular_rad": ang},
    }


def mesh_arrays(shape: Any, quality: str = "normal") -> tuple[np.ndarray, np.ndarray, np.ndarray]:
    """Plain arrays for exporters (STL/OBJ/GLB) and FE pre-processing."""
    mesh_shape(shape, quality)
    P, N, I, base = [], [], [], 0
    for face in occ.faces(shape):
        r = face_triangles(face)
        if r is None:
            continue
        pts, nrm, idx = r
        P.append(pts)
        N.append(nrm)
        I.append(idx + base)
        base += len(pts)
    if not P:
        return np.zeros((0, 3)), np.zeros((0, 3)), np.zeros((0, 3), dtype=np.int64)
    return np.vstack(P), np.vstack(N), np.vstack(I)
