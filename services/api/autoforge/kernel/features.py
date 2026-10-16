"""Feature implementations on top of OCCT.

Every handler takes the feature and a :class:`Context`, mutates the context
(bodies, sketches, datums, tools) and raises :class:`FeatureError` with an
actionable message on failure. Handlers never leave an invalid shape in the
context: results are checked with ``BRepCheck_Analyzer`` before commit.
"""

from __future__ import annotations

import math
import os
import tempfile
from dataclasses import dataclass, field
from typing import Any, Callable

import numpy as np
from OCP.BRepAlgoAPI import BRepAlgoAPI_Common, BRepAlgoAPI_Cut, BRepAlgoAPI_Fuse
from OCP.BRepBuilderAPI import BRepBuilderAPI_Transform
from OCP.BRepFilletAPI import BRepFilletAPI_MakeChamfer, BRepFilletAPI_MakeFillet
from OCP.BRepOffsetAPI import BRepOffsetAPI_MakePipe, BRepOffsetAPI_MakeThickSolid, BRepOffsetAPI_ThruSections
from OCP.BRepPrimAPI import (
    BRepPrimAPI_MakeBox,
    BRepPrimAPI_MakeCone,
    BRepPrimAPI_MakeCylinder,
    BRepPrimAPI_MakePrism,
    BRepPrimAPI_MakeRevol,
    BRepPrimAPI_MakeSphere,
)
from OCP.BRep import BRep_Builder
from OCP.gp import gp_Ax1, gp_Ax2, gp_Dir, gp_Pnt, gp_Trsf, gp_Vec
from OCP.TopAbs import TopAbs_WIRE
from OCP.TopoDS import TopoDS_Compound

from . import expressions, occ
from . import schema as S
from .geom import Axis, Plane, SketchBuild, resolve_axis, resolve_plane, vec
from .naming import Body, FeatureError, generated, keep_present, remap, resolve_edges, resolve_faces

EPS_TOOL = 0.01  # mm: tools start slightly outside the face to avoid coplanar booleans


@dataclass
class Context:
    params: dict[str, float]
    bodies: dict[str, Body] = field(default_factory=dict)
    sketches: dict[str, SketchBuild] = field(default_factory=dict)
    datums: dict[str, dict[str, Any]] = field(default_factory=dict)
    tools: dict[str, dict[str, Any]] = field(default_factory=dict)
    warnings: list[str] = field(default_factory=list)
    blob_loader: Callable[[str, str], bytes] | None = None

    def num(self, e: S.Expr | None, what: str = "value") -> float:
        if e is None:
            raise FeatureError(f"Missing {what}", "invalid_parameter")
        try:
            return expressions.evaluate(e, self.params)
        except expressions.ExpressionError as exc:
            raise FeatureError(f"Invalid {what} {e!r}: {exc}", "invalid_expression") from exc

    def pos(self, e: S.Expr, what: str) -> float:
        v = self.num(e, what)
        if v <= 0:
            raise FeatureError(f"{what} must be greater than zero (got {v:g})", "invalid_parameter")
        return v

    def body(self, name: str | None) -> Body:
        if not name or name not in self.bodies:
            raise FeatureError(f"Body {name!r} does not exist at this point in the history", "missing_body")
        return self.bodies[name]


# ------------------------------------------------------------------ helpers
def _compound(shapes: list[Any]) -> Any:
    comp = TopoDS_Compound()
    b = BRep_Builder()
    b.MakeCompound(comp)
    for s in shapes:
        b.Add(comp, s)
    return comp


def _check(shape: Any, what: str) -> Any:
    if shape is None or shape.IsNull():
        raise FeatureError(f"{what} produced no geometry", "empty_result")
    if not occ.solids(shape):
        raise FeatureError(f"{what} produced no solid", "empty_result")
    if not occ.is_valid(shape):
        raise FeatureError(f"{what} produced invalid geometry (BRepCheck failed). Adjust dimensions or references.",
                           "invalid_geometry")
    return shape


def _bop(kind: str, a: Any, b: Any) -> Any:
    cls = {"join": BRepAlgoAPI_Fuse, "cut": BRepAlgoAPI_Cut, "intersect": BRepAlgoAPI_Common}[kind]
    mk = cls(a, b)
    if not mk.IsDone() or (hasattr(mk, "HasErrors") and mk.HasErrors()):
        raise FeatureError(f"Boolean {kind} failed in the kernel", "boolean_failed")
    return mk


def _fuse_many(items: list[tuple[Any, list[tuple[Any, str]]]]) -> tuple[Any, list[tuple[Any, str]]]:
    shape, names = items[0]
    for s, n in items[1:]:
        mk = _bop("join", shape, s)
        res = mk.Shape()
        names = remap(names + n, mk, res)
        shape = res
    return shape, names


def apply_operation(ctx: Context, fid: str, op: str, body_name: str, tool: Any, tool_names: list[tuple[Any, str]],
                    what: str) -> None:
    ctx.tools[fid] = {"op": op, "body": body_name, "shape": tool, "names": tool_names}
    if op == "new":
        if body_name in ctx.bodies:
            raise FeatureError(f"Body {body_name!r} already exists", "duplicate_body")
        _check(tool, what)
        ctx.bodies[body_name] = Body(body_name, tool, keep_present(tool_names, tool))
        return
    target = ctx.body(body_name)
    v0 = occ.volume_props(target.shape).Mass()
    mk = _bop(op, target.shape, tool)
    res = mk.Shape()
    if op == "intersect" and (res.IsNull() or not occ.solids(res)):
        raise FeatureError(f"{what}: intersection is empty", "empty_result")
    if op == "cut" and not occ.solids(res):
        raise FeatureError(f"{what}: cut removes the entire body", "empty_result")
    _check(res, what)
    v1 = occ.volume_props(res).Mass()
    if op == "cut" and abs(v1 - v0) <= 1e-9 * max(1.0, v0):
        ctx.warnings.append(f"{what}: cut did not remove any material (tool does not intersect body {body_name!r})")
    if op == "join":
        n_before = len(occ.solids(target.shape))
        n_after = len(occ.solids(res))
        if n_after > n_before:
            ctx.warnings.append(f"{what}: joined geometry does not touch body {body_name!r} (body now has {n_after} lumps)")
    ctx.bodies[body_name] = Body(body_name, res, remap(target.names + tool_names, mk, res))


def _body_name(f: Any, default: str) -> str:
    return f.params.body or default


def _sketch(ctx: Context, sid: str) -> SketchBuild:
    sk = ctx.sketches.get(sid)
    if sk is None:
        raise FeatureError(f"Sketch {sid!r} is not available (failed or suppressed)", "unresolved_reference")
    return sk


def _diag_of(ctx: Context, extra: list[np.ndarray] | None = None) -> float:
    pts = []
    for b in ctx.bodies.values():
        bb = occ.bbox(b.shape)
        if bb:
            pts += [np.array(bb[0]), np.array(bb[1])]
    pts += extra or []
    if not pts:
        return 1000.0
    a = np.vstack(pts)
    return float(np.linalg.norm(a.max(0) - a.min(0))) + 1.0


# ------------------------------------------------------------------ sketch
def f_sketch(f: S.SketchF, ctx: Context) -> dict[str, Any]:
    from ..sketch import profile, solver

    plane = resolve_plane(f.params.plane, ctx)
    try:
        sol = solver.solve(f.params, ctx.params)
    except solver.SketchError as exc:
        raise FeatureError(str(exc), "invalid_sketch") from exc
    prof = profile.analyze(f.params, sol)
    sb = SketchBuild(f.id, f.params, plane, sol, prof)
    ctx.sketches[f.id] = sb
    for cid, msg in sol.constraint_errors.items():
        ctx.warnings.append(f"Constraint {cid}: {msg}")
    if sol.status == "conflicting":
        ctx.warnings.append("Sketch has conflicting constraints: " + ", ".join(sol.conflicting or sol.unsatisfied))
    elif sol.status == "over_constrained":
        ctx.warnings.append("Redundant constraints: " + ", ".join(sol.redundant))
    for w in prof.warnings:
        ctx.warnings.append(w["message"])
    for e in prof.errors:
        ctx.warnings.append(e["message"])
    return {"status": sol.status, "dof": sol.dof}


# ------------------------------------------------------------------ sweeps
def f_extrude(f: S.ExtrudeF, ctx: Context, new_body: str) -> None:
    p = f.params
    sk = _sketch(ctx, p.sketch)
    if p.extent == "through_all":
        d = 2 * _diag_of(ctx, [sk.plane.origin])
    else:
        d = ctx.pos(p.distance, "Extrude distance")
    plane = sk.plane
    n = plane.normal
    if p.direction == "symmetric":
        plane = plane.offset(-d / 2) if p.extent == "blind" else plane.offset(-d)
        v = n * (d if p.extent == "blind" else 2 * d)
    elif p.direction == "reverse":
        v = -n * d
    else:
        v = n * d
    solids: list[tuple[Any, list[tuple[Any, str]]]] = []
    for face, emap in sk.faces(p.regions, plane):
        mk = BRepPrimAPI_MakePrism(face, vec(v), True)
        if not mk.IsDone():
            raise FeatureError("Extrusion failed", "extrude_failed")
        shape = mk.Shape()
        names = [(mk.FirstShape(), f"{f.id}:start"), (mk.LastShape(), f"{f.id}:end")]
        for e, eid in emap:
            names += [(g, f"{f.id}:side:{eid}") for g in generated(mk, e)]
        solids.append((shape, names))
    tool, names = _fuse_many(solids)
    apply_operation(ctx, f.id, p.operation, _body_name(f, new_body), tool, names, f"Extrude {f.name or f.id}")


def f_revolve(f: S.RevolveF, ctx: Context, new_body: str) -> None:
    p = f.params
    sk = _sketch(ctx, p.sketch)
    ang = ctx.num(p.angle, "Revolve angle")
    if not (0 < ang <= 360):
        raise FeatureError("Revolve angle must be in (0, 360] degrees", "invalid_parameter")
    ax = resolve_axis(p.axis, ctx)
    if abs(np.dot(ax.direction, sk.plane.normal)) > 1e-6:
        raise FeatureError("Revolve axis must lie in the sketch plane", "invalid_reference")
    solids = []
    for face, emap in sk.faces(p.regions):
        mk = BRepPrimAPI_MakeRevol(face, ax.gp(), math.radians(ang), True)
        if not mk.IsDone():
            raise FeatureError("Revolve failed", "revolve_failed")
        names = []
        if ang < 360:
            names += [(mk.FirstShape(), f"{f.id}:start"), (mk.LastShape(), f"{f.id}:end")]
        for e, eid in emap:
            names += [(g, f"{f.id}:side:{eid}") for g in generated(mk, e)]
        shape = mk.Shape()
        if not occ.is_valid(shape):
            raise FeatureError("Revolve produced invalid geometry: the axis probably crosses the profile", "invalid_geometry")
        solids.append((shape, names))
    tool, names = _fuse_many(solids)
    apply_operation(ctx, f.id, p.operation, _body_name(f, new_body), tool, names, f"Revolve {f.name or f.id}")


def f_sweep(f: S.SweepF, ctx: Context, new_body: str) -> None:
    p = f.params
    sk = _sketch(ctx, p.sketch)
    path = _sketch(ctx, p.path_sketch).path_wire(p.path_entities)
    solids = []
    for face, emap in sk.faces(p.regions):
        mk = BRepOffsetAPI_MakePipe(path, face)
        mk.Build()
        if not mk.IsDone():
            raise FeatureError("Sweep failed: check that the profile is not tangent to or crossing the path", "sweep_failed")
        names = [(mk.FirstShape(), f"{f.id}:start"), (mk.LastShape(), f"{f.id}:end")]
        for e, eid in emap:
            names += [(g, f"{f.id}:side:{eid}") for g in generated(mk, e)]
        solids.append((mk.Shape(), names))
    tool, names = _fuse_many(solids)
    apply_operation(ctx, f.id, p.operation, _body_name(f, new_body), tool, names, f"Sweep {f.name or f.id}")


def f_loft(f: S.LoftF, ctx: Context, new_body: str) -> None:
    p = f.params
    mk = BRepOffsetAPI_ThruSections(True, p.ruled, 1e-6)
    firsts: list[tuple[Any, str]] = []
    for i, sid in enumerate(p.sketches):
        sk = _sketch(ctx, sid)
        regs = sk.select_regions(None)
        if len(regs) != 1 or regs[0].holes:
            raise FeatureError(f"Loft section {sid} must contain exactly one closed profile without holes", "invalid_profile")
        face, emap = sk.faces([regs[0].id])[0]
        wire = occ.cast(occ.unique(face, TopAbs_WIRE)[0], "Wire")
        mk.AddWire(wire)
        if i == 0:
            firsts = emap
    mk.CheckCompatibility(False)
    mk.Build()
    if not mk.IsDone():
        raise FeatureError("Loft failed: sections may be incompatible or self-intersecting", "loft_failed")
    names = [(mk.FirstShape(), f"{f.id}:start"), (mk.LastShape(), f"{f.id}:end")]
    for e, eid in firsts:
        try:
            g = mk.GeneratedFace(e)
            if not g.IsNull():
                names.append((g, f"{f.id}:side:{eid}"))
        except Exception:
            pass
    apply_operation(ctx, f.id, p.operation, _body_name(f, new_body), mk.Shape(), names, f"Loft {f.name or f.id}")


# ------------------------------------------------------------------ primitives
def _local_frame(axis) -> tuple[np.ndarray, np.ndarray, np.ndarray]:
    z = np.array(axis, float)
    z /= np.linalg.norm(z)
    pl = Plane.make((0, 0, 0), z, (1, 0, 0))
    return pl.x_dir, pl.y_dir, z


def f_primitive(f: S.PrimitiveF, ctx: Context, new_body: str) -> None:
    p = f.params
    o = np.array([ctx.num(v, "origin") for v in p.origin])
    x, y, z = _local_frame(p.axis)
    ax2 = gp_Ax2(gp_Pnt(*o), gp_Dir(*z), gp_Dir(*x))
    names: list[tuple[Any, str]] = []
    if p.shape == "box":
        dx, dy, dz = (ctx.pos(v, "Box size") for v in p.size)
        mk = BRepPrimAPI_MakeBox(ax2, dx, dy, dz)
        shape = mk.Shape()
        for face in occ.faces(shape):
            g = occ.face_geometry(face)
            nrm = np.array(g["normal"])
            comps = {"x": np.dot(nrm, x), "y": np.dot(nrm, y), "z": np.dot(nrm, z)}
            k = max(comps, key=lambda c: abs(comps[c]))
            names.append((face, f"{f.id}:{'+' if comps[k] > 0 else '-'}{k}"))
    elif p.shape in ("cylinder", "cone"):
        if p.shape == "cylinder":
            r, h = ctx.pos(p.size[0], "Radius"), ctx.pos(p.size[1], "Height")
            shape = BRepPrimAPI_MakeCylinder(ax2, r, h).Shape()
        else:
            r1, r2, h = ctx.num(p.size[0]), ctx.num(p.size[1]), ctx.pos(p.size[2], "Height")
            if r1 < 0 or r2 < 0 or (r1 == 0 and r2 == 0) or r1 == r2:
                raise FeatureError("Cone radii must be non-negative and different", "invalid_parameter")
            shape = BRepPrimAPI_MakeCone(ax2, r1, r2, h).Shape()
        for face in occ.faces(shape):
            g = occ.face_geometry(face)
            if g["surface"] == "plane":
                t = float(np.dot(np.array(g["centroid"]) - o, z))
                names.append((face, f"{f.id}:{'top' if t > 1e-9 else 'bottom'}"))
            else:
                names.append((face, f"{f.id}:side"))
    else:
        r = ctx.pos(p.size[0], "Radius")
        shape = BRepPrimAPI_MakeSphere(ax2, r).Shape()
        names = [(fc, f"{f.id}:surface") for fc in occ.faces(shape)]
    apply_operation(ctx, f.id, p.operation, _body_name(f, new_body), shape, names, f"Primitive {f.name or f.id}")


# ------------------------------------------------------------------ dress-up
def f_hole(f: S.HoleF, ctx: Context) -> None:
    p = f.params
    body = ctx.body(p.body)
    plane = resolve_plane(p.plane, ctx)
    d = ctx.pos(p.diameter, "Hole diameter")
    cb_d = ctx.num(p.cbore_diameter) if p.cbore_diameter is not None else None
    cb_h = ctx.num(p.cbore_depth) if p.cbore_depth is not None else None
    cs_d = ctx.num(p.csink_diameter) if p.csink_diameter is not None else None
    cs_a = ctx.num(p.csink_angle)
    if cb_d is not None and (cb_d <= d or not cb_h or cb_h <= 0):
        raise FeatureError("Counterbore diameter must exceed hole diameter and depth must be > 0", "invalid_parameter")
    if cs_d is not None and (cs_d <= d or not (0 < cs_a < 180)):
        raise FeatureError("Countersink diameter must exceed hole diameter; angle in (0, 180)", "invalid_parameter")
    if cb_d is not None and cs_d is not None:
        raise FeatureError("A hole can have a counterbore or a countersink, not both", "invalid_parameter")
    if p.end == "blind":
        depth = ctx.pos(p.depth, "Hole depth")
    else:
        depth = 2 * _diag_of(ctx, [plane.origin])
    if cb_d is not None and cb_h >= depth:
        raise FeatureError("Counterbore depth must be smaller than hole depth", "invalid_parameter")
    n = plane.normal
    down = gp_Dir(*(-n))
    seen: list[np.ndarray] = []
    tools: list[tuple[Any, list[tuple[Any, str]]]] = []
    for i, (u, v) in enumerate(p.positions):
        P = plane.to3d(ctx.num(u, "hole x"), ctx.num(v, "hole y"))
        if any(np.linalg.norm(P - q) < 1e-6 for q in seen):
            raise FeatureError(f"Hole position {i} duplicates another position", "invalid_parameter")
        seen.append(P)
        top = P + n * EPS_TOOL
        ax = gp_Ax2(gp_Pnt(*top), down)
        shape = BRepPrimAPI_MakeCylinder(ax, d / 2, depth + EPS_TOOL).Shape()
        if cb_d is not None:
            shape = _bop("join", shape, BRepPrimAPI_MakeCylinder(ax, cb_d / 2, cb_h + EPS_TOOL).Shape()).Shape()
        if cs_d is not None:
            t = math.tan(math.radians(cs_a / 2))
            h = (cs_d - d) / 2 / t
            shape = _bop("join", shape, BRepPrimAPI_MakeCone(ax, cs_d / 2 + EPS_TOOL * t, d / 2, h + EPS_TOOL).Shape()).Shape()
        names = []
        for face in occ.faces(shape):
            g = occ.face_geometry(face)
            if g["surface"] == "cylinder":
                tag = "wall" if abs(g.get("radius", 0) - d / 2) < 1e-6 else "cbore"
            elif g["surface"] == "cone":
                tag = "csink"
            elif g["surface"] == "plane":
                s = float(np.dot(P - np.array(g["centroid"]), n))
                if s > (cb_h or 0) + 1e-6:
                    tag = "bottom"
                elif cb_h is not None and abs(s - cb_h) < 1e-6:
                    tag = "cbore_floor"
                else:
                    tag = "top"
            else:
                tag = "face"
            names.append((face, f"{f.id}:{tag}:{i}"))
        tools.append((shape, names))
    tool = _compound([t for t, _ in tools])
    tnames = [n_ for _, ns in tools for n_ in ns]
    apply_operation(ctx, f.id, "cut", body.name, tool, tnames, f"Hole {f.name or f.id}")


def f_fillet(f: S.FilletF, ctx: Context) -> None:
    p = f.params
    body = ctx.body(p.body)
    r = ctx.pos(p.radius, "Fillet radius")
    edges = []
    for ref in p.edges:
        for e, label in resolve_edges(body, ref, ctx.warnings):
            if not any(e.IsSame(x) for x, _ in edges):
                edges.append((e, label))
    mk = BRepFilletAPI_MakeFillet(body.shape)
    for e, _ in edges:
        mk.Add(r, e)
    try:
        mk.Build()
    except Exception as exc:
        raise FeatureError(f"Fillet failed ({exc}). The radius may be too large for adjacent faces.", "fillet_failed") from exc
    if not mk.IsDone():
        raise FeatureError(f"Fillet of radius {r:g} mm failed on {len(edges)} edge(s): radius may exceed adjacent face size.",
                           "fillet_failed", {"edges": [l for _, l in edges]})
    res = _check(mk.Shape(), "Fillet")
    names = remap(body.names, mk, res)
    for e, label in edges:
        names += [(g, f"{f.id}:fillet[{label}]") for g in generated(mk, e)]
    ctx.bodies[body.name] = Body(body.name, res, keep_present(names, res))


def f_chamfer(f: S.ChamferF, ctx: Context) -> None:
    p = f.params
    body = ctx.body(p.body)
    dist = ctx.pos(p.distance, "Chamfer distance")
    edges = []
    for ref in p.edges:
        for e, label in resolve_edges(body, ref, ctx.warnings):
            if not any(e.IsSame(x) for x, _ in edges):
                edges.append((e, label))
    mk = BRepFilletAPI_MakeChamfer(body.shape)
    for e, _ in edges:
        mk.Add(dist, e)
    try:
        mk.Build()
    except Exception as exc:
        raise FeatureError(f"Chamfer failed ({exc}).", "chamfer_failed") from exc
    if not mk.IsDone():
        raise FeatureError(f"Chamfer of {dist:g} mm failed: distance may exceed adjacent face size.", "chamfer_failed")
    res = _check(mk.Shape(), "Chamfer")
    names = remap(body.names, mk, res)
    for e, label in edges:
        names += [(g, f"{f.id}:chamfer[{label}]") for g in generated(mk, e)]
    ctx.bodies[body.name] = Body(body.name, res, keep_present(names, res))


def f_shell(f: S.ShellF, ctx: Context) -> None:
    p = f.params
    body = ctx.body(p.body)
    t = ctx.pos(p.thickness, "Shell thickness")
    remove = resolve_faces(body, p.remove_faces)
    lst = occ.TopTools_ListOfShape()
    for fc in remove:
        lst.Append(fc)
    mk = BRepOffsetAPI_MakeThickSolid()
    try:
        mk.MakeThickSolidByJoin(body.shape, lst, -t, 1e-4)
        mk.Build()
    except Exception as exc:
        raise FeatureError(f"Shell failed ({exc}); thickness may exceed local curvature radius.", "shell_failed") from exc
    if not mk.IsDone():
        raise FeatureError("Shell failed; thickness may exceed local feature size.", "shell_failed")
    res = _check(mk.Shape(), "Shell")
    names = []
    for fc, n in body.names:
        if any(fc.IsSame(r) for r in remove):
            continue
        names += [(g, f"{f.id}:inner:{n}") for g in generated(mk, fc)]
        mods = _mods(mk, fc)
        names += [(m, n) for m in mods] if mods else [(fc, n)]
    ctx.bodies[body.name] = Body(body.name, res, keep_present(names, res))


def _mods(mk: Any, s: Any) -> list[Any]:
    try:
        return list(mk.Modified(s))
    except Exception:
        return []


# ------------------------------------------------------------------ booleans / patterns
def f_boolean(f: S.BooleanF, ctx: Context) -> None:
    p = f.params
    target = ctx.body(p.target)
    if p.target in p.tools:
        raise FeatureError("Target body cannot also be a tool", "invalid_parameter")
    tools = [ctx.body(t) for t in p.tools]
    shape = tools[0].shape if len(tools) == 1 else _compound([t.shape for t in tools])
    tnames = [n for t in tools for n in t.names]
    apply_operation(ctx, f.id, p.operation, target.name, shape, tnames, f"Boolean {f.name or f.id}")
    if not p.keep_tools:
        for t in p.tools:
            ctx.bodies.pop(t, None)


def _transform(shape: Any, names: list[tuple[Any, str]], trsf: gp_Trsf, prefix: str) -> tuple[Any, list[tuple[Any, str]]]:
    mk = BRepBuilderAPI_Transform(shape, trsf, True)
    res = mk.Shape()
    out = []
    for fc, n in names:
        mods = _mods(mk, fc)
        out += [(m, f"{prefix}{n}") for m in mods]
    return res, keep_present(out, res)


def _pattern(f: Any, ctx: Context, trsfs: list[gp_Trsf]) -> None:
    p = f.params
    body = ctx.body(p.body)
    if not trsfs:
        raise FeatureError("Pattern produces no instances", "invalid_parameter")
    if p.features:
        for src in p.features:
            tool = ctx.tools.get(src)
            if tool is None:
                raise FeatureError(f"Feature {src!r} has no patternable result (only solid features can be patterned)",
                                   "invalid_reference")
            if tool["op"] not in ("join", "cut") and not (tool["op"] == "new" and tool["body"] == body.name):
                raise FeatureError(f"Feature {src!r} uses operation {tool['op']!r}; only join/cut features can be patterned",
                                   "invalid_reference")
            op = "join" if tool["op"] == "new" else tool["op"]
            copies, cnames = [], []
            for k, t in enumerate(trsfs, start=1):
                s, n = _transform(tool["shape"], tool["names"], t, f"{f.id}:{k}:")
                copies.append(s)
                cnames += n
            apply_operation(ctx, f"{f.id}:{src}", op, body.name, _compound(copies), cnames, f"Pattern {f.name or f.id}")
    else:
        copies, cnames = [], []
        for k, t in enumerate(trsfs, start=1):
            s, n = _transform(body.shape, body.names, t, f"{f.id}:{k}:")
            copies.append(s)
            cnames += n
        apply_operation(ctx, f.id, "join", body.name, _compound(copies), cnames, f"Pattern {f.name or f.id}")


def f_linear_pattern(f: S.LinearPatternF, ctx: Context) -> None:
    p = f.params
    n1 = int(round(ctx.num(p.count, "count")))
    n2 = int(round(ctx.num(p.count2, "count2")))
    if n1 < 1 or n2 < 1 or n1 * n2 > 1000:
        raise FeatureError("Pattern counts must be >= 1 and at most 1000 instances", "invalid_parameter")
    d1 = resolve_axis(p.direction, ctx).direction * ctx.num(p.spacing, "spacing")
    d2 = resolve_axis(p.direction2, ctx).direction * ctx.num(p.spacing2, "spacing2") if p.direction2 else np.zeros(3)
    trsfs = []
    for i in range(n1):
        for j in range(n2):
            if i == 0 and j == 0:
                continue
            t = gp_Trsf()
            t.SetTranslation(vec(i * d1 + j * d2))
            trsfs.append(t)
    _pattern(f, ctx, trsfs)


def f_circular_pattern(f: S.CircularPatternF, ctx: Context) -> None:
    p = f.params
    n = int(round(ctx.num(p.count, "count")))
    total = ctx.num(p.angle, "angle")
    if n < 2 or n > 1000:
        raise FeatureError("Circular pattern count must be between 2 and 1000", "invalid_parameter")
    if not (0 < total <= 360):
        raise FeatureError("Circular pattern angle must be in (0, 360]", "invalid_parameter")
    step = total / n if abs(total - 360) < 1e-9 else total / (n - 1)
    ax = resolve_axis(p.axis, ctx)
    trsfs = []
    for k in range(1, n):
        t = gp_Trsf()
        t.SetRotation(ax.gp(), math.radians(step * k))
        trsfs.append(t)
    _pattern(f, ctx, trsfs)


def f_mirror(f: S.MirrorF, ctx: Context) -> None:
    pl = resolve_plane(f.params.plane, ctx)
    t = gp_Trsf()
    t.SetMirror(pl.ax2())
    _pattern(f, ctx, [t])


def f_transform(f: S.TransformF, ctx: Context) -> None:
    p = f.params
    body = ctx.body(p.body)
    t = gp_Trsf()
    tr = np.array([ctx.num(v, "translation") for v in p.translate])
    if p.rotate_axis is not None:
        ang = ctx.num(p.rotate_angle, "rotation angle")
        r = gp_Trsf()
        r.SetRotation(resolve_axis(p.rotate_axis, ctx).gp(), math.radians(ang))
        t = r
    m = gp_Trsf()
    m.SetTranslation(vec(tr))
    t = m.Multiplied(t)
    if p.copy_to:
        if p.copy_to in ctx.bodies:
            raise FeatureError(f"Body {p.copy_to!r} already exists", "duplicate_body")
        s, n = _transform(body.shape, body.names, t, "")
        ctx.bodies[p.copy_to] = Body(p.copy_to, s, n)
    else:
        s, n = _transform(body.shape, body.names, t, "")
        ctx.bodies[body.name] = Body(body.name, s, n)


def f_datum_plane(f: S.DatumPlaneF, ctx: Context) -> dict[str, Any]:
    pl = resolve_plane(f.params.plane, ctx)
    ctx.datums[f.id] = {"kind": "plane", "plane": pl}
    return pl.to_dict()


def f_datum_axis(f: S.DatumAxisF, ctx: Context) -> dict[str, Any]:
    ax = resolve_axis(f.params.axis, ctx)
    ctx.datums[f.id] = {"kind": "axis", "axis": ax}
    return ax.to_dict()


def f_import(f: S.ImportF, ctx: Context, new_body: str) -> dict[str, Any]:
    from . import io as kio

    p = f.params
    if ctx.blob_loader is None:
        raise FeatureError("Imported geometry is not available in this context (no storage configured)", "storage_unavailable")
    data = ctx.blob_loader(p.blob_key, p.sha256)
    shape, diag = kio.read_shape_bytes(data, p.format)
    names = [(fc, f"{f.id}:face{i}") for i, fc in enumerate(occ.faces(shape))]
    if not occ.solids(shape):
        ctx.warnings.append("Imported file contains no closed solids; surfaces are kept for visualization only")
        ctx.bodies[_body_name(f, new_body)] = Body(_body_name(f, new_body), shape, names)
    else:
        apply_operation(ctx, f.id, "new", _body_name(f, new_body), shape, names, "Import")
    for w in diag.get("warnings", []):
        ctx.warnings.append(w)
    return diag


HANDLERS: dict[str, Callable[..., Any]] = {
    "sketch": f_sketch,
    "extrude": f_extrude,
    "revolve": f_revolve,
    "sweep": f_sweep,
    "loft": f_loft,
    "primitive": f_primitive,
    "hole": f_hole,
    "fillet": f_fillet,
    "chamfer": f_chamfer,
    "shell": f_shell,
    "boolean": f_boolean,
    "linear_pattern": f_linear_pattern,
    "circular_pattern": f_circular_pattern,
    "mirror": f_mirror,
    "transform": f_transform,
    "datum_plane": f_datum_plane,
    "datum_axis": f_datum_axis,
    "import": f_import,
}
NEEDS_NEW_BODY = {"extrude", "revolve", "sweep", "loft", "primitive", "import"}
