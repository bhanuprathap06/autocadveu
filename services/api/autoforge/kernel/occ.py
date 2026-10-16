"""Thin compatibility layer over the OCP (OCCT) Python bindings.

OCP renamed most static methods with an ``_s`` suffix, but a few (notably the
``TopoDS`` down-casts) are exposed without it and the convention has changed
between releases.  Every static call in the kernel goes through :func:`st` so a
binding upgrade is a one-line fix instead of a hunt through the codebase.
"""

from __future__ import annotations

from typing import Any, Iterator

from OCP.BRep import BRep_Tool
from OCP.BRepAdaptor import BRepAdaptor_Curve, BRepAdaptor_Surface
from OCP.BRepBndLib import BRepBndLib
from OCP.BRepCheck import BRepCheck_Analyzer
from OCP.BRepGProp import BRepGProp
from OCP.Bnd import Bnd_Box
from OCP.GeomAbs import (
    GeomAbs_BSplineCurve,
    GeomAbs_BSplineSurface,
    GeomAbs_Circle,
    GeomAbs_Cone,
    GeomAbs_Cylinder,
    GeomAbs_Ellipse,
    GeomAbs_Line,
    GeomAbs_Plane,
    GeomAbs_Sphere,
    GeomAbs_Torus,
)
from OCP.GProp import GProp_GProps
from OCP.TopAbs import TopAbs_EDGE, TopAbs_FACE, TopAbs_SOLID, TopAbs_VERTEX, TopAbs_ShapeEnum
from OCP.TopExp import TopExp, TopExp_Explorer
from OCP.TopoDS import TopoDS, TopoDS_Shape
try:  # OCP <= 7.x naming
    from OCP.TopTools import (  # type: ignore[attr-defined]
        TopTools_IndexedDataMapOfShapeListOfShape,
        TopTools_IndexedMapOfShape,
        TopTools_ListOfShape,
    )
    from OCP.TColgp import TColgp_HArray1OfPnt as HArray1OfPnt  # type: ignore[attr-defined]
except ImportError:  # OCP 8 moved NCollection instantiations to OCP.collections
    from OCP.collections import (  # type: ignore[no-redef]
        HArray1_gp_Pnt as HArray1OfPnt,
        IndexedDataMap_TopoDS_Shape_List_TopoDS_Shape_TopTools_ShapeMapHasher as TopTools_IndexedDataMapOfShapeListOfShape,
        IndexedMap_TopoDS_Shape_TopTools_ShapeMapHasher as TopTools_IndexedMapOfShape,
        List_TopoDS_Shape as TopTools_ListOfShape,
    )

KERNEL_NAME = "OCCT"
BINDING_NAME = "cadquery-ocp"


def kernel_version() -> str:
    try:
        from importlib.metadata import version

        return f"{KERNEL_NAME} via {BINDING_NAME} {version('cadquery-ocp')}"
    except Exception:  # pragma: no cover - metadata missing in odd installs
        return f"{KERNEL_NAME} via {BINDING_NAME} (unknown version)"


def st(cls: Any, name: str) -> Any:
    """Return static method ``name`` of ``cls`` regardless of ``_s`` suffix convention."""
    fn = getattr(cls, name + "_s", None)
    if fn is None:
        fn = getattr(cls, name)
    return fn


def cast(shape: TopoDS_Shape, kind: str) -> Any:
    return st(TopoDS, kind)(shape)


def explore(shape: TopoDS_Shape, kind: TopAbs_ShapeEnum) -> Iterator[TopoDS_Shape]:
    ex = TopExp_Explorer(shape, kind)
    while ex.More():
        yield ex.Current()
        ex.Next()


def indexed_map(shape: TopoDS_Shape, kind: TopAbs_ShapeEnum) -> TopTools_IndexedMapOfShape:
    m = TopTools_IndexedMapOfShape()
    st(TopExp, "MapShapes")(shape, kind, m)
    return m


def unique(shape: TopoDS_Shape, kind: TopAbs_ShapeEnum) -> list[TopoDS_Shape]:
    """Unique sub-shapes (by IsSame) in deterministic exploration order."""
    m = indexed_map(shape, kind)
    return [m.FindKey(i) for i in range(1, m.Size() + 1)]


def faces(shape: TopoDS_Shape) -> list[Any]:
    return [cast(f, "Face") for f in unique(shape, TopAbs_FACE)]


def edges(shape: TopoDS_Shape) -> list[Any]:
    return [cast(e, "Edge") for e in unique(shape, TopAbs_EDGE)]


def solids(shape: TopoDS_Shape) -> list[Any]:
    return [cast(s, "Solid") for s in unique(shape, TopAbs_SOLID)]


def vertices(shape: TopoDS_Shape) -> list[Any]:
    return [cast(v, "Vertex") for v in unique(shape, TopAbs_VERTEX)]


def edge_face_map(shape: TopoDS_Shape) -> TopTools_IndexedDataMapOfShapeListOfShape:
    m = TopTools_IndexedDataMapOfShapeListOfShape()
    st(TopExp, "MapShapesAndAncestors")(shape, TopAbs_EDGE, TopAbs_FACE, m)
    return m


def list_of_shapes(lst: TopTools_ListOfShape) -> list[TopoDS_Shape]:
    return list(lst)


def is_valid(shape: TopoDS_Shape) -> bool:
    return bool(BRepCheck_Analyzer(shape).IsValid())


def bbox(shape: TopoDS_Shape) -> tuple[list[float], list[float]] | None:
    box = Bnd_Box()
    st(BRepBndLib, "Add")(shape, box, True)
    if box.IsVoid():
        return None
    a, b = box.CornerMin(), box.CornerMax()
    return [a.X(), a.Y(), a.Z()], [b.X(), b.Y(), b.Z()]


def volume_props(shape: TopoDS_Shape) -> GProp_GProps:
    p = GProp_GProps()
    st(BRepGProp, "VolumeProperties")(shape, p)
    return p


def surface_props(shape: TopoDS_Shape) -> GProp_GProps:
    p = GProp_GProps()
    st(BRepGProp, "SurfaceProperties")(shape, p)
    return p


def linear_props(shape: TopoDS_Shape) -> GProp_GProps:
    p = GProp_GProps()
    st(BRepGProp, "LinearProperties")(shape, p)
    return p


SURFACE_TYPES = {
    GeomAbs_Plane: "plane",
    GeomAbs_Cylinder: "cylinder",
    GeomAbs_Cone: "cone",
    GeomAbs_Sphere: "sphere",
    GeomAbs_Torus: "torus",
    GeomAbs_BSplineSurface: "bspline",
}

CURVE_TYPES = {
    GeomAbs_Line: "line",
    GeomAbs_Circle: "circle",
    GeomAbs_Ellipse: "ellipse",
    GeomAbs_BSplineCurve: "bspline",
}


def surface_type(face: Any) -> str:
    return SURFACE_TYPES.get(BRepAdaptor_Surface(face).GetType(), "other")


def curve_type(edge: Any) -> str:
    return CURVE_TYPES.get(BRepAdaptor_Curve(edge).GetType(), "other")


def face_geometry(face: Any) -> dict[str, Any]:
    """Analytic description of a face usable for mates, sketch planes and BCs."""
    ad = BRepAdaptor_Surface(face)
    kind = SURFACE_TYPES.get(ad.GetType(), "other")
    props = surface_props(face)
    c = props.CentreOfMass()
    out: dict[str, Any] = {"surface": kind, "area": props.Mass(), "centroid": [c.X(), c.Y(), c.Z()]}
    if kind == "plane":
        pln = ad.Plane()
        ax = pln.Position()
        n = ax.Direction()
        xd = ax.XDirection()
        sign = -1.0 if face.Orientation().name.endswith("REVERSED") else 1.0
        out["normal"] = [sign * n.X(), sign * n.Y(), sign * n.Z()]
        out["x_dir"] = [xd.X(), xd.Y(), xd.Z()]
        out["origin"] = out["centroid"]
    elif kind in ("cylinder", "cone"):
        geom = ad.Cylinder() if kind == "cylinder" else ad.Cone()
        ax = geom.Axis()
        loc, d = ax.Location(), ax.Direction()
        out["axis_origin"] = [loc.X(), loc.Y(), loc.Z()]
        out["axis_dir"] = [d.X(), d.Y(), d.Z()]
        if kind == "cylinder":
            out["radius"] = geom.Radius()
    elif kind == "sphere":
        sph = ad.Sphere()
        loc = sph.Location()
        out["center"] = [loc.X(), loc.Y(), loc.Z()]
        out["radius"] = sph.Radius()
    return out


def edge_geometry(edge: Any) -> dict[str, Any]:
    ad = BRepAdaptor_Curve(edge)
    kind = CURVE_TYPES.get(ad.GetType(), "other")
    props = linear_props(edge)
    c = props.CentreOfMass()
    out: dict[str, Any] = {"curve": kind, "length": props.Mass(), "midpoint": [c.X(), c.Y(), c.Z()]}
    if kind == "circle":
        circ = ad.Circle()
        loc = circ.Location()
        d = circ.Axis().Direction()
        out["center"] = [loc.X(), loc.Y(), loc.Z()]
        out["axis_dir"] = [d.X(), d.Y(), d.Z()]
        out["radius"] = circ.Radius()
    elif kind == "line":
        p0 = ad.Value(ad.FirstParameter())
        p1 = ad.Value(ad.LastParameter())
        out["start"] = [p0.X(), p0.Y(), p0.Z()]
        out["end"] = [p1.X(), p1.Y(), p1.Z()]
    return out


def vertex_point(v: Any) -> list[float]:
    p = st(BRep_Tool, "Pnt")(v)
    return [p.X(), p.Y(), p.Z()]
