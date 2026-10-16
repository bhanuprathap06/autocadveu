"""Mass properties, measurement and interference checks (exact B-rep based)."""

from __future__ import annotations

import math
from typing import Any

import numpy as np
from OCP.BRepAlgoAPI import BRepAlgoAPI_Common
from OCP.BRepExtrema import BRepExtrema_DistShapeShape

from . import occ
from .naming import Body, FeatureError, resolve_edges
from . import schema as S


def mass_properties(shape: Any, density_kg_m3: float | None = None) -> dict[str, Any]:
    """Volume/area/centroid/inertia. Inertia is about the centroid, in kg*mm^2 if density given."""
    vp = occ.volume_props(shape)
    sp = occ.surface_props(shape)
    vol = vp.Mass()
    c = vp.CentreOfMass()
    m = vp.MatrixOfInertia()  # about centre of mass, for unit density (mm^5)
    I = np.array([[m.Value(i, j) for j in (1, 2, 3)] for i in (1, 2, 3)])
    out: dict[str, Any] = {
        "volume_mm3": vol,
        "surface_area_mm2": sp.Mass(),
        "centroid_mm": [c.X(), c.Y(), c.Z()],
        "bbox_mm": occ.bbox(shape),
        "solids": len(occ.solids(shape)),
        "valid": occ.is_valid(shape),
    }
    if density_kg_m3:
        rho = density_kg_m3 * 1e-9  # kg/mm^3
        out["density_kg_m3"] = density_kg_m3
        out["mass_kg"] = vol * rho
        out["center_of_mass_mm"] = out["centroid_mm"]
        out["inertia_kg_mm2"] = (I * rho).tolist()
        w = np.linalg.eigvalsh(I * rho)
        out["principal_moments_kg_mm2"] = sorted(w.tolist())
    return out


def distance(a: Any, b: Any) -> dict[str, Any]:
    d = BRepExtrema_DistShapeShape(a, b)
    d.Perform()
    if not d.IsDone():
        raise FeatureError("Distance computation failed", "measure_failed")
    p1, p2 = d.PointOnShape1(1), d.PointOnShape2(1)
    return {"distance_mm": d.Value(), "point_a": [p1.X(), p1.Y(), p1.Z()], "point_b": [p2.X(), p2.Y(), p2.Z()],
            "solutions": d.NbSolution()}


def _angle(u, v) -> float:
    u, v = np.asarray(u, float), np.asarray(v, float)
    c = np.dot(u, v) / (np.linalg.norm(u) * np.linalg.norm(v))
    return math.degrees(math.acos(max(-1.0, min(1.0, c))))


def resolve_entity(body: Body, ref: dict[str, Any]) -> tuple[str, Any]:
    kind = ref.get("kind")
    if kind == "face":
        fs = body.faces_named(ref["name"])
        if not fs:
            raise FeatureError(f"Face {ref['name']!r} not found", "unresolved_reference")
        return "face", fs[0]
    if kind == "edge":
        es = resolve_edges(body, S.EdgeRef(**ref["ref"]), [])
        return "edge", es[0][0]
    if kind == "body":
        return "body", body.shape
    if kind == "point":
        from OCP.BRepBuilderAPI import BRepBuilderAPI_MakeVertex
        from OCP.gp import gp_Pnt

        return "point", BRepBuilderAPI_MakeVertex(gp_Pnt(*ref["xyz"])).Vertex()
    raise FeatureError(f"Unknown measure entity kind {kind!r}", "invalid_reference")


def measure(bodies: dict[str, Body], a: dict[str, Any], b: dict[str, Any] | None = None) -> dict[str, Any]:
    ka, sa = resolve_entity(bodies[a["body"]] if a.get("body") else next(iter(bodies.values())), a)
    out: dict[str, Any] = {"a": ka}
    if ka == "face":
        g = occ.face_geometry(sa)
        out.update({"area_mm2": g["area"], "surface": g["surface"]})
        if "radius" in g:
            out["radius_mm"] = g["radius"]
            out["diameter_mm"] = 2 * g["radius"]
    elif ka == "edge":
        g = occ.edge_geometry(sa)
        out.update({"length_mm": g["length"], "curve": g["curve"]})
        if "radius" in g:
            out["radius_mm"] = g["radius"]
            out["diameter_mm"] = 2 * g["radius"]
    elif ka == "body":
        out.update(mass_properties(sa))
    if b is not None:
        kb, sb = resolve_entity(bodies[b["body"]] if b.get("body") else next(iter(bodies.values())), b)
        out["b"] = kb
        out.update(distance(sa, sb))
        ga = occ.face_geometry(sa) if ka == "face" else occ.edge_geometry(sa) if ka == "edge" else None
        gb = occ.face_geometry(sb) if kb == "face" else occ.edge_geometry(sb) if kb == "edge" else None
        da = _direction(ga)
        db = _direction(gb)
        if da is not None and db is not None:
            out["angle_deg"] = _angle(da, db)
    return out


def _direction(g: dict[str, Any] | None):
    if not g:
        return None
    if g.get("surface") == "plane":
        return g["normal"]
    if g.get("curve") == "line":
        return np.subtract(g["end"], g["start"])
    if "axis_dir" in g:
        return g["axis_dir"]
    return None


def interference(items: list[tuple[str, Any]], clearance_mm: float = 0.0) -> list[dict[str, Any]]:
    """Pairwise interference volume and minimum clearance between solids."""
    out = []
    bbs = [occ.bbox(s) for _, s in items]
    for i in range(len(items)):
        for j in range(i + 1, len(items)):
            (na, sa), (nb, sb) = items[i], items[j]
            ba, bb = bbs[i], bbs[j]
            pad = clearance_mm + 1e-6
            separated = ba and bb and any(ba[1][k] + pad < bb[0][k] or bb[1][k] + pad < ba[0][k] for k in range(3))
            rec: dict[str, Any] = {"a": na, "b": nb}
            if separated:
                rec.update({"interferes": False, "volume_mm3": 0.0, "checked": "bbox"})
                if clearance_mm > 0:
                    rec["clearance_ok"] = True
                out.append(rec)
                continue
            common = BRepAlgoAPI_Common(sa, sb)
            vol = occ.volume_props(common.Shape()).Mass() if common.IsDone() else float("nan")
            rec["volume_mm3"] = vol
            rec["interferes"] = bool(vol > 1e-6)
            rec["checked"] = "exact"
            if not rec["interferes"]:
                d = distance(sa, sb)
                rec["min_distance_mm"] = d["distance_mm"]
                rec["touching"] = d["distance_mm"] < 1e-6
                if clearance_mm > 0:
                    rec["clearance_ok"] = d["distance_mm"] >= clearance_mm
            out.append(rec)
    return out
