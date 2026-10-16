"""Kernel-side assembly operations (run inside isolated workers)."""

from __future__ import annotations

from typing import Any

import numpy as np
from OCP.gp import gp_Trsf
from OCP.TopLoc import TopLoc_Location

from ..kernel import analysis, engine, io as kio, occ
from ..kernel import schema as S
from ..kernel.features import _compound
from ..kernel.naming import FeatureError
from ..kernel.ops import _loader


def _trsf(T: list[list[float]]) -> gp_Trsf:
    M = np.array(T, float)
    t = gp_Trsf()
    t.SetValues(*M[0, :4], *M[1, :4], *M[2, :4])
    return t


def _part_shapes(parts: dict[str, dict[str, Any]], prefixes: dict[str, str]) -> dict[str, dict[str, Any]]:
    out = {}
    for key, content in parts.items():
        d = S.Document.model_validate(content)
        res = engine.recompute(d, blob_loader=_loader(prefixes.get(key)))
        out[key] = {n: b.shape for n, b in res.ctx.bodies.items()}
    return out


def _instance_shape(shapes: dict[str, dict[str, Any]], inst: dict[str, Any]) -> Any:
    bodies = shapes.get(inst["part_key"], {})
    sel = [s for n, s in bodies.items() if not inst.get("body") or n == inst["body"]]
    if not sel:
        raise FeatureError(f"Instance {inst['name']} has no geometry", "empty_instance")
    base = sel[0] if len(sel) == 1 else _compound(sel)
    return base.Moved(TopLoc_Location(_trsf(inst["matrix"])))


def assembly_interference(parts: dict[str, dict[str, Any]], instances: list[dict[str, Any]], prefixes: dict[str, str],
                          clearance_mm: float = 0.0) -> dict[str, Any]:
    shapes = _part_shapes(parts, prefixes)
    items = [(i["id"], _instance_shape(shapes, i)) for i in instances]
    return {"pairs": analysis.interference(items, clearance_mm), "clearance_mm": clearance_mm,
            "method": "Exact B-rep common volume (BRepAlgoAPI_Common) with bounding-box prefilter; minimum distance "
                      "via BRepExtrema_DistShapeShape"}


def collision_sweep(parts: dict[str, dict[str, Any]], instances: list[dict[str, Any]], frames: list[dict[str, Any]],
                    prefixes: dict[str, str]) -> dict[str, Any]:
    shapes = _part_shapes(parts, prefixes)
    base = {i["id"]: i for i in instances}
    hits = []
    for f in frames:
        if not f.get("ok"):
            continue
        items = []
        for iid, inst in base.items():
            T = f["placements"].get(iid, inst["matrix"])
            items.append((iid, _instance_shape(shapes, {**inst, "matrix": T})))
        for r in analysis.interference(items):
            if r["interferes"]:
                hits.append({"value": f["value"], "a": r["a"], "b": r["b"], "volume_mm3": r["volume_mm3"]})
    return {"collisions": hits, "frames_checked": sum(1 for f in frames if f.get("ok"))}


def export_assembly(parts: dict[str, dict[str, Any]], instances: list[dict[str, Any]], prefixes: dict[str, str], fmt: str,
                    name: str) -> dict[str, Any]:
    shapes = _part_shapes(parts, prefixes)
    if fmt == "step":
        keys = sorted({i["part_key"] for i in instances})
        items, idx = [], {}
        for k in keys:
            bodies = list(shapes[k].values())
            label = next((i["part_name"] for i in instances if i["part_key"] == k), k)
            idx[k] = len(items)
            items.append((label, bodies[0] if len(bodies) == 1 else _compound(bodies)))
        placements = [(i["name"], idx[i["part_key"]], _trsf(i["matrix"])) for i in instances]
        data = kio.export_step(items, name, placements)
        shape, diag = kio.read_shape_bytes(data, "step")
        return {"data": data, "sha256": kio.sha256(data), "verification": {
            "reimport_valid": diag["valid"], "solids": diag["solids"], "instances": len(instances),
            "retains": "assembly structure, instance names and placements (STEP AP214 via XDE)"}}
    located = [_instance_shape(shapes, i) for i in instances]
    comp = _compound(located)
    if fmt == "stl":
        data = kio.export_stl(comp)
    elif fmt == "glb":
        data = kio.export_glb([(i["name"], s, (0.62, 0.66, 0.72)) for i, s in zip(instances, located)])
    elif fmt == "obj":
        data = kio.export_obj([(i["name"], s) for i, s in zip(instances, located)])
    else:
        raise FeatureError(f"Assembly export to {fmt} is not supported", "unsupported_format")
    return {"data": data, "sha256": kio.sha256(data), "verification": {"instances": len(instances),
                                                                        "retains": "merged mesh only"}}
