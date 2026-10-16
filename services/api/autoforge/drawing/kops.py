"""Hidden-line-removed projections for technical drawings (runs in kernel workers)."""

from __future__ import annotations

from typing import Any

import numpy as np
from OCP.BRepAdaptor import BRepAdaptor_Curve
from OCP.GCPnts import GCPnts_TangentialDeflection
from OCP.gp import gp_Ax2, gp_Dir, gp_Pnt
from OCP.HLRAlgo import HLRAlgo_Projector
from OCP.HLRBRep import HLRBRep_Algo, HLRBRep_HLRToShape

from ..kernel import engine, occ
from ..kernel import schema as S
from ..kernel.features import _compound
from ..kernel.naming import Body, FeatureError, edge_refs
from ..kernel.ops import _loader

# view -> (normal pointing at the viewer, screen x direction)
VIEW_DIRS = {
    "front": ((0, -1, 0), (1, 0, 0)),
    "back": ((0, 1, 0), (-1, 0, 0)),
    "top": ((0, 0, 1), (1, 0, 0)),
    "bottom": ((0, 0, -1), (1, 0, 0)),
    "right": ((1, 0, 0), (0, 1, 0)),
    "left": ((-1, 0, 0), (0, -1, 0)),
    "iso": ((1 / 3**0.5, -1 / 3**0.5, 1 / 3**0.5), (1 / 2**0.5, 1 / 2**0.5, 0)),
}


def frame(view: str) -> tuple[np.ndarray, np.ndarray, np.ndarray]:
    n, x = (np.array(v, float) for v in VIEW_DIRS[view])
    y = np.cross(n, x)
    return n, x, y


def _polylines(compound: Any, lin: float) -> list[list[list[float]]]:
    out = []
    if compound is None or compound.IsNull():
        return out
    for e in occ.edges(compound):
        try:
            ad = BRepAdaptor_Curve(e)
            disc = GCPnts_TangentialDeflection(ad, 0.15, lin)
            pts = [[disc.Value(i).X(), disc.Value(i).Y()] for i in range(1, disc.NbPoints() + 1)]
        except Exception:
            continue
        if len(pts) >= 2:
            out.append([[round(x, 5), round(y, 5)] for x, y in pts])
    return out


def project_shape(shape: Any, view: str, hidden: bool = True) -> dict[str, Any]:
    n, x, _ = frame(view)
    algo = HLRBRep_Algo()
    algo.Add(shape)
    algo.Projector(HLRAlgo_Projector(gp_Ax2(gp_Pnt(0, 0, 0), gp_Dir(*n), gp_Dir(*x))))
    algo.Update()
    algo.Hide()
    h = HLRBRep_HLRToShape(algo)
    bb = occ.bbox(shape)
    diag = float(np.linalg.norm(np.subtract(bb[1], bb[0]))) if bb else 100.0
    lin = max(diag * 0.0008, 0.005)
    visible = _polylines(h.VCompound(), lin) + _polylines(h.OutLineVCompound(), lin)
    smooth = _polylines(h.Rg1LineVCompound(), lin)
    hid = (_polylines(h.HCompound(), lin) + _polylines(h.OutLineHCompound(), lin)) if hidden and view != "iso" else []
    allpts = [p for pl in visible + smooth for p in pl] or [[0, 0]]
    a = np.array(allpts)
    return {"visible": visible, "smooth": smooth, "hidden": hid,
            "bbox": [float(a[:, 0].min()), float(a[:, 1].min()), float(a[:, 0].max()), float(a[:, 1].max())]}


def to_view(p: np.ndarray, view: str) -> list[float]:
    _, x, y = frame(view)
    return [float(np.dot(p, x)), float(np.dot(p, y))]


def features_for_view(bodies: list[Body], view: str) -> dict[str, Any]:
    """Circles seen end-on (centre marks + diameter callouts) and cylinder axes seen side-on (centrelines)."""
    n, x, y = frame(view)
    circles, centerlines = [], []
    seen: set[tuple] = set()
    for body in bodies:
        for e, ref in edge_refs(body):
            g = occ.edge_geometry(e)
            if g["curve"] != "circle":
                continue
            d = np.array(g["axis_dir"])
            if abs(abs(np.dot(d, n)) - 1) > 1e-6:
                continue
            c2 = to_view(np.array(g["center"]), view)
            key = (round(c2[0], 4), round(c2[1], 4), round(g["radius"], 4))
            if key in seen:
                continue
            seen.add(key)
            full = abs(g["length"] - 2 * np.pi * g["radius"]) < 1e-6 * max(g["length"], 1)
            circles.append({"center": c2, "radius": g["radius"], "full": full, "ref": ref, "body": body.name})
        for f, names in body.names_by_face():
            g = occ.face_geometry(f)
            if g["surface"] != "cylinder":
                continue
            d = np.array(g["axis_dir"])
            if abs(np.dot(d, n)) > 1e-6:
                continue
            bb = occ.bbox(f)
            corners = np.array([[bb[i][0], bb[j][1], bb[k][2]] for i in (0, 1) for j in (0, 1) for k in (0, 1)])
            o = np.array(g["axis_origin"])
            t = (corners - o) @ d
            a, b = o + d * t.min(), o + d * t.max()
            pa, pb = to_view(a, view), to_view(b, view)
            key = (round(pa[0], 3), round(pa[1], 3), round(pb[0], 3), round(pb[1], 3))
            if key not in seen:
                seen.add(key)
                centerlines.append({"a": pa, "b": pb, "face": names[0], "radius": g.get("radius")})
    return {"circles": circles, "centerlines": centerlines}


def _part_bodies(content: dict[str, Any], prefix: str | None) -> list[Body]:
    res = engine.recompute(S.Document.model_validate(content), blob_loader=_loader(prefix))
    return list(res.ctx.bodies.values())


def eval_dims(bodies: list[Body], dims: list[dict[str, Any]]) -> dict[str, Any]:
    from ..kernel import analysis
    from ..kernel.naming import resolve_edges

    out: dict[str, Any] = {}
    by = {b.name: b for b in bodies}
    for d in dims:
        try:
            body = by.get(d.get("body") or "") or bodies[0]
            view = d["view_kind"]
            n, _, _ = frame(view)
            e = resolve_edges(body, S.EdgeRef(**d["edge"]), [])[0][0]
            g = occ.edge_geometry(e)
            if d["kind"] == "length":
                if g["curve"] != "line":
                    raise FeatureError("Length dimensions need a straight edge", "invalid_reference")
                a, b = np.array(g["start"]), np.array(g["end"])
                out[d["id"]] = {"value": g["length"], "a": to_view(a, view), "b": to_view(b, view),
                                "foreshortened": abs(float(np.dot((b - a) / np.linalg.norm(b - a), n))) > 1e-6}
            elif d["kind"] in ("diameter", "radius"):
                if g["curve"] != "circle":
                    raise FeatureError("Diameter/radius dimensions need a circular edge", "invalid_reference")
                r = g["radius"]
                out[d["id"]] = {"value": 2 * r if d["kind"] == "diameter" else r, "center": to_view(np.array(g["center"]), view),
                                "radius": r}
            else:
                e2 = resolve_edges(body, S.EdgeRef(**d["edge_b"]), [])[0][0]
                m = analysis.distance(e, e2)
                out[d["id"]] = {"value": m["distance_mm"], "a": to_view(np.array(m["point_a"]), view),
                                "b": to_view(np.array(m["point_b"]), view)}
        except FeatureError as exc:
            out[d["id"]] = {"error": str(exc)}
        except Exception as exc:
            out[d["id"]] = {"error": f"Could not evaluate dimension: {exc}"}
    return out


def drawing_views(views: list[str], content: dict[str, Any] | None = None, blob_prefix: str | None = None,
                  parts: dict[str, Any] | None = None, instances: list[dict[str, Any]] | None = None,
                  prefixes: dict[str, str] | None = None, hidden: bool = True,
                  dims: list[dict[str, Any]] | None = None) -> dict[str, Any]:
    from ..assembly.kops import _instance_shape, _part_shapes

    inst_centroids: dict[str, list[float]] = {}
    if content is not None:
        bodies = _part_bodies(content, blob_prefix)
        if not bodies:
            raise FeatureError("The model has no bodies to draw", "nothing_to_draw")
        shape = bodies[0].shape if len(bodies) == 1 else _compound([b.shape for b in bodies])
        mass = {b.name: occ.volume_props(b.shape).Mass() for b in bodies}
    else:
        shapes = _part_shapes(parts or {}, prefixes or {})
        located = []
        for inst in instances or []:
            s = _instance_shape(shapes, inst)
            located.append(s)
            c = occ.volume_props(s).CentreOfMass()
            inst_centroids[inst["id"]] = [c.X(), c.Y(), c.Z()]
        if not located:
            raise FeatureError("The assembly has no components to draw", "nothing_to_draw")
        shape = _compound(located)
        bodies = []
        mass = {}
    out: dict[str, Any] = {"views": {}, "mass_volume_mm3": mass, "bbox3d": occ.bbox(shape),
                           "dims": eval_dims(bodies, dims or []) if bodies else {}}
    for v in views:
        if v not in VIEW_DIRS:
            raise FeatureError(f"Unknown view {v!r}", "invalid_view")
        pv = project_shape(shape, v, hidden)
        if bodies:
            pv.update(features_for_view(bodies, v))
        else:
            pv.update({"circles": [], "centerlines": []})
        pv["instances"] = {k: to_view(np.array(c), v) for k, c in inst_centroids.items()}
        out["views"][v] = pv
    return out
