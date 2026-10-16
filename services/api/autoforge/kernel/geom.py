"""Reference geometry resolution and sketch -> B-rep conversion."""

from __future__ import annotations

import math
from dataclasses import dataclass
from typing import Any

import numpy as np
from OCP.BRepBuilderAPI import (
    BRepBuilderAPI_MakeEdge,
    BRepBuilderAPI_MakeFace,
    BRepBuilderAPI_MakeVertex,
    BRepBuilderAPI_MakeWire,
)
from OCP.GC import GC_MakeArcOfCircle  # noqa: F401  (kept for future trimmed-arc construction)
from OCP.Geom import Geom_Circle
from OCP.GeomAPI import GeomAPI_Interpolate
from OCP.gp import gp_Ax1, gp_Ax2, gp_Circ, gp_Dir, gp_Elips, gp_Pln, gp_Pnt, gp_Vec
from OCP.ShapeFix import ShapeFix_Face

from . import occ
from . import schema as S
from .naming import FeatureError

STANDARD_PLANES = {
    "XY": ((0, 0, 0), (0, 0, 1), (1, 0, 0)),
    "XZ": ((0, 0, 0), (0, -1, 0), (1, 0, 0)),
    "YZ": ((0, 0, 0), (1, 0, 0), (0, 1, 0)),
}
STANDARD_AXES = {"X": (1, 0, 0), "Y": (0, 1, 0), "Z": (0, 0, 1)}


def _unit(v) -> np.ndarray:
    v = np.asarray(v, float)
    n = np.linalg.norm(v)
    if n < 1e-12:
        raise FeatureError("Zero-length direction vector", "invalid_geometry")
    return v / n


@dataclass
class Plane:
    origin: np.ndarray
    normal: np.ndarray
    x_dir: np.ndarray

    @property
    def y_dir(self) -> np.ndarray:
        return np.cross(self.normal, self.x_dir)

    def to3d(self, x: float, y: float) -> np.ndarray:
        return self.origin + x * self.x_dir + y * self.y_dir

    def pnt(self, x: float, y: float) -> gp_Pnt:
        return gp_Pnt(*self.to3d(x, y))

    def ax2(self, origin: np.ndarray | None = None, x_dir: np.ndarray | None = None) -> gp_Ax2:
        o = self.origin if origin is None else origin
        xd = self.x_dir if x_dir is None else x_dir
        return gp_Ax2(gp_Pnt(*o), gp_Dir(*self.normal), gp_Dir(*xd))

    def gp_pln(self) -> gp_Pln:
        return gp_Pln(self.ax2().Location(), self.ax2().Direction())

    def offset(self, d: float) -> "Plane":
        return Plane(self.origin + d * self.normal, self.normal, self.x_dir)

    def to_dict(self) -> dict[str, Any]:
        return {"origin": self.origin.tolist(), "normal": self.normal.tolist(), "x_dir": self.x_dir.tolist(),
                "y_dir": self.y_dir.tolist()}

    @staticmethod
    def make(origin, normal, x_hint=None) -> "Plane":
        n = _unit(normal)
        xh = np.array(x_hint if x_hint is not None else (1, 0, 0), float)
        if np.linalg.norm(np.cross(n, xh)) < 1e-6:
            xh = np.array((0, 1, 0), float) if abs(n[1]) < 0.9 else np.array((0, 0, 1), float)
        x = xh - np.dot(xh, n) * n
        return Plane(np.asarray(origin, float), n, _unit(x))


@dataclass
class Axis:
    origin: np.ndarray
    direction: np.ndarray

    def gp(self) -> gp_Ax1:
        return gp_Ax1(gp_Pnt(*self.origin), gp_Dir(*self.direction))

    def to_dict(self) -> dict[str, Any]:
        return {"origin": self.origin.tolist(), "direction": self.direction.tolist()}


def resolve_plane(ref: S.PlaneRef, ctx: Any) -> Plane:
    off = ctx.num(ref.offset)
    if ref.kind == "standard":
        o, n, x = STANDARD_PLANES[ref.name or "XY"]
        pl = Plane(np.array(o, float), np.array(n, float), np.array(x, float))
    elif ref.kind == "datum":
        d = ctx.datums.get(ref.datum)
        if d is None or d["kind"] != "plane":
            raise FeatureError(f"Datum plane {ref.datum!r} is not available", "unresolved_reference")
        pl = d["plane"]
    else:
        body = ctx.body(ref.body)
        fs = body.faces_named(ref.face)
        if not fs:
            raise FeatureError(f"Face {ref.face!r} not found on {ref.body!r}; re-select the sketch plane", "unresolved_reference")
        g = occ.face_geometry(fs[0])
        if g["surface"] != "plane":
            raise FeatureError(f"Face {ref.face!r} is not planar ({g['surface']})", "invalid_reference")
        n = _unit(g["normal"])
        # Origin: projection of the world origin onto the plane (stable as the face resizes).
        c = np.array(g["centroid"])
        o = np.dot(c, n) * n  # point on the plane closest to the world origin
        pl = Plane.make(o, n, (1, 0, 0))
    if ref.flip:
        pl = Plane(pl.origin, -pl.normal, pl.x_dir)
    return pl.offset(off) if off else pl


def resolve_axis(ref: S.AxisRef, ctx: Any) -> Axis:
    if ref.kind == "standard":
        ax = Axis(np.zeros(3), np.array(STANDARD_AXES[ref.name or "Z"], float))
    elif ref.kind == "datum":
        d = ctx.datums.get(ref.datum)
        if d is None or d["kind"] != "axis":
            raise FeatureError(f"Datum axis {ref.datum!r} is not available", "unresolved_reference")
        ax = d["axis"]
    elif ref.kind == "face":
        body = ctx.body(ref.body)
        fs = body.faces_named(ref.face or "")
        if not fs:
            raise FeatureError(f"Face {ref.face!r} not found on {ref.body!r}", "unresolved_reference")
        g = occ.face_geometry(fs[0])
        if "axis_dir" not in g:
            raise FeatureError(f"Face {ref.face!r} has no axis ({g['surface']})", "invalid_reference")
        ax = Axis(np.array(g["axis_origin"]), _unit(g["axis_dir"]))
    elif ref.kind == "edge":
        from .naming import resolve_edges

        body = ctx.body(ref.body)
        edges = resolve_edges(body, ref.edge, ctx.warnings)
        g = occ.edge_geometry(edges[0][0])
        if g["curve"] == "line":
            ax = Axis(np.array(g["start"]), _unit(np.array(g["end"]) - np.array(g["start"])))
        elif g["curve"] == "circle":
            ax = Axis(np.array(g["center"]), _unit(g["axis_dir"]))
        else:
            raise FeatureError("Axis edge must be a line or circle", "invalid_reference")
    elif ref.kind == "sketch_line":
        sk = ctx.sketches.get(ref.sketch)
        if sk is None:
            raise FeatureError(f"Sketch {ref.sketch!r} is not available", "unresolved_reference")
        ent = sk.entity(ref.entity)
        if not isinstance(ent, S.SkLine):
            raise FeatureError(f"{ref.entity!r} is not a sketch line", "invalid_reference")
        a = sk.plane.to3d(*sk.solve.points[ent.p1])
        b = sk.plane.to3d(*sk.solve.points[ent.p2])
        ax = Axis(a, _unit(b - a))
    else:
        if ref.origin is None or ref.direction is None:
            raise FeatureError("Custom axis requires origin and direction", "invalid_reference")
        ax = Axis(np.array(ref.origin, float), _unit(ref.direction))
    if ref.reverse:
        ax = Axis(ax.origin, -ax.direction)
    return ax


# ------------------------------------------------------------------ sketches
@dataclass
class SketchBuild:
    """Solved sketch with 3D placement; builds B-rep faces/wires on demand."""

    fid: str
    params: S.SketchParams
    plane: Plane
    solve: Any  # SolveResult
    profile: Any  # ProfileResult

    def entity(self, eid: str) -> Any:
        for e in self.params.entities:
            if e.id == eid:
                return e
        raise FeatureError(f"Sketch {self.fid}: entity {eid!r} not found", "unresolved_reference")

    def _edges(self, plane: Plane) -> dict[str, Any]:
        """One OCCT edge per non-construction curve, sharing vertices exactly."""
        verts: dict[str, Any] = {}

        def V(pid: str) -> Any:
            root = self.profile.vertex_of.get(pid, pid)
            if root not in verts:
                x, y = self.solve.points[root] if root in self.solve.points else self.solve.points[pid]
                verts[root] = BRepBuilderAPI_MakeVertex(plane.pnt(x, y)).Vertex()
            return verts[root]

        out: dict[str, Any] = {}
        for cid, c in self.profile.curves.items():
            e = self.entity(cid)
            if isinstance(e, S.SkLine):
                mk = BRepBuilderAPI_MakeEdge(V(e.p1), V(e.p2))
            elif isinstance(e, S.SkCircle):
                cx, cy = self.solve.points[e.center]
                mk = BRepBuilderAPI_MakeEdge(gp_Circ(plane.ax2(plane.to3d(cx, cy)), self.solve.radii[e.id]))
            elif isinstance(e, S.SkArc):
                cx, cy = self.solve.points[e.center]
                r = c.data["r"]
                circ = Geom_Circle(gp_Circ(plane.ax2(plane.to3d(cx, cy)), r))
                mk = BRepBuilderAPI_MakeEdge(circ, V(e.start), V(e.end))
            elif isinstance(e, S.SkEllipse):
                cx, cy = self.solve.points[e.center]
                rx, ry = self.solve.ellipses[e.id]
                rot = math.radians(e.rotation)
                if ry > rx:
                    rx, ry, rot = ry, rx, rot + math.pi / 2
                xd = math.cos(rot) * plane.x_dir + math.sin(rot) * plane.y_dir
                mk = BRepBuilderAPI_MakeEdge(gp_Elips(plane.ax2(plane.to3d(cx, cy), xd), rx, ry))
            elif isinstance(e, S.SkSpline):
                pts = [self.solve.points[p] for p in e.points]
                closed = c.closed
                if closed and len(pts) > 2 and np.linalg.norm(np.subtract(pts[0], pts[-1])) < 1e-9:
                    pts = pts[:-1]
                arr = occ.HArray1OfPnt(1, len(pts))
                for i, (x, y) in enumerate(pts):
                    arr.SetValue(i + 1, plane.pnt(x, y))
                interp = GeomAPI_Interpolate(arr, closed, 1e-6)
                interp.Perform()
                if not interp.IsDone():
                    raise FeatureError(f"Spline {e.id} interpolation failed", "invalid_geometry")
                if closed:
                    mk = BRepBuilderAPI_MakeEdge(interp.Curve())
                else:
                    mk = BRepBuilderAPI_MakeEdge(interp.Curve(), V(e.points[0]), V(e.points[-1]))
            else:
                continue
            if not mk.IsDone():
                raise FeatureError(f"Could not build edge for {cid}", "invalid_geometry")
            out[cid] = mk.Edge()
        return out

    def _wire(self, edges: dict[str, Any], loop: Any) -> Any:
        mw = BRepBuilderAPI_MakeWire()
        for eid, _rev in loop.edges:
            mw.Add(edges[eid])
        if not mw.IsDone():
            raise FeatureError(f"Sketch {self.fid}: profile loop is not connected", "open_profile")
        return mw.Wire()

    def select_regions(self, region_ids: list[str] | None) -> list[Any]:
        if self.solve.status == "conflicting":
            raise FeatureError(f"Sketch {self.fid} has conflicting constraints; fix the sketch first", "sketch_conflict")
        if self.profile.errors:
            raise FeatureError(self.profile.errors[0]["message"], self.profile.errors[0]["code"])
        if region_ids is None:
            regs = self.profile.default_regions()
        else:
            byid = {r.id: r for r in self.profile.regions}
            missing = [r for r in region_ids if r not in byid]
            if missing:
                raise FeatureError(f"Region(s) {missing} no longer exist in sketch {self.fid}; re-select profiles",
                                   "unresolved_reference")
            regs = [byid[r] for r in region_ids]
        if not regs:
            raise FeatureError(f"Sketch {self.fid} has no closed profile to use", "open_profile")
        return regs

    def faces(self, region_ids: list[str] | None, plane: Plane | None = None) -> list[tuple[Any, list[tuple[Any, str]]]]:
        """Build planar faces for regions. Returns [(face, [(edge, entity_id)])]."""
        plane = plane or self.plane
        regs = self.select_regions(region_ids)
        edges = self._edges(plane)
        out = []
        for reg in regs:
            outer = self._wire(edges, reg.outer)
            mf = BRepBuilderAPI_MakeFace(gp_Pln(plane.ax2().Location(), plane.ax2().Direction()), outer, True)
            for h in reg.holes:
                mf.Add(self._wire(edges, h))
            if not mf.IsDone():
                raise FeatureError(f"Could not build face for region {reg.id}", "invalid_geometry")
            fix = ShapeFix_Face(mf.Face())
            fix.FixOrientation()
            fix.Perform()
            face = occ.cast(fix.Face(), "Face")
            emap = []
            for fe in occ.edges(face):
                eid = next((k for k, e in edges.items() if fe.IsSame(e)), None)
                emap.append((fe, eid or "edge"))
            out.append((face, emap))
        return out

    def path_wire(self, entity_ids: list[str] | None) -> Any:
        if self.profile.errors:
            raise FeatureError(self.profile.errors[0]["message"], self.profile.errors[0]["code"])
        edges = self._edges(self.plane)
        if entity_ids:
            missing = [e for e in entity_ids if e not in edges]
            if missing:
                raise FeatureError(f"Path entities {missing} not found in sketch {self.fid}", "unresolved_reference")
            chain = [(e, False) for e in entity_ids]
        else:
            chains = self.profile.open_chains
            closed = [r for r in self.profile.regions]
            if len(chains) == 1:
                chain = chains[0]
            elif not chains and len(closed) == 1:
                chain = closed[0].outer.edges
            else:
                raise FeatureError(f"Path sketch {self.fid} must contain exactly one connected path", "invalid_path")
        mw = BRepBuilderAPI_MakeWire()
        for eid, _ in chain:
            mw.Add(edges[eid])
        if not mw.IsDone():
            raise FeatureError("Sweep path is not connected", "invalid_path")
        return mw.Wire()

    def to_dict(self) -> dict[str, Any]:
        return {
            "plane": self.plane.to_dict(),
            "solve": self.solve.to_dict(),
            "profile": self.profile.to_dict(),
            "points": {k: list(v) for k, v in self.solve.points.items()},
            "radii": self.solve.radii,
            "ellipses": {k: list(v) for k, v in self.solve.ellipses.items()},
        }


def vec(v) -> gp_Vec:
    return gp_Vec(*[float(x) for x in v])
