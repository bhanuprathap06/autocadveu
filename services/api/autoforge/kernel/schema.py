"""Persistent parametric document schema (schema_version 1).

Conventions
-----------
* Internal length unit is the millimetre and angle unit the degree. Bare numeric
  literals in expressions are therefore mm / deg. ``Document.units`` is a display
  preference; clients convert typed input such as ``2 in`` into explicit unit
  literals so the stored document is unambiguous.
* Right-handed coordinates, +Z up. Standard planes: XY (normal +Z), XZ "front"
  (normal -Y, x +X, y +Z), YZ "right" (normal +X, x +Y, y +Z).
* Features form an ordered history. References may only point backwards; the
  dependency graph (``graph.py``) verifies this and detects cycles.
* Topological references never use transient indices. Faces are referenced by
  persistent names generated from feature history (e.g. ``f3:side:l2``), edges by
  the pair of faces they separate plus an optional geometric hint.
"""

from __future__ import annotations

import re
from typing import Annotated, Any, Literal, Union

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

SCHEMA_VERSION = 1
ID_RE = r"^[A-Za-z0-9_\-:.]{1,64}$"
NAME_RE = r"^[A-Za-z_][A-Za-z0-9_]{0,63}$"

Expr = Union[float, int, str]
Vec3 = tuple[float, float, float]
Vec2 = tuple[float, float]


class _M(BaseModel):
    model_config = ConfigDict(extra="forbid")


def _check_expr(v: Any) -> Any:
    if isinstance(v, str) and len(v) > 512:
        raise ValueError("expression too long")
    return v


# ---------------------------------------------------------------- references
class PlaneRef(_M):
    kind: Literal["standard", "face", "datum"] = "standard"
    name: Literal["XY", "XZ", "YZ"] | None = "XY"
    body: str | None = None
    face: str | None = None
    datum: str | None = None
    offset: Expr = 0
    flip: bool = False

    @model_validator(mode="after")
    def _req(self) -> "PlaneRef":
        if self.kind == "standard" and not self.name:
            raise ValueError("standard plane requires name")
        if self.kind == "face" and not (self.body and self.face):
            raise ValueError("face plane requires body and face")
        if self.kind == "datum" and not self.datum:
            raise ValueError("datum plane requires datum feature id")
        return self


class AxisRef(_M):
    kind: Literal["standard", "datum", "face", "edge", "custom", "sketch_line"] = "standard"
    name: Literal["X", "Y", "Z"] | None = "Z"
    datum: str | None = None
    body: str | None = None
    face: str | None = None
    edge: "EdgeRef | None" = None
    sketch: str | None = None
    entity: str | None = None
    origin: Vec3 | None = None
    direction: Vec3 | None = None
    reverse: bool = False


class EdgeRef(_M):
    """Persistent edge reference.

    ``between``: edges shared by faces named ``faces[0]`` and ``faces[1]``.
    ``of_face``: every edge bounding the named face.
    ``all``: every edge of the body.
    ``nearest``: geometric fallback (fragile; flagged with a warning).
    ``hint`` disambiguates ``between`` when several edges match, and is used as
    a fallback when the named faces no longer exist.
    """

    kind: Literal["between", "of_face", "all", "nearest"] = "between"
    faces: list[str] | None = None
    face: str | None = None
    hint: Vec3 | None = None


AxisRef.model_rebuild()


# ---------------------------------------------------------------- sketch
class SkPoint(_M):
    id: str = Field(pattern=ID_RE)
    type: Literal["point"] = "point"
    x: float
    y: float
    construction: bool = False


class SkLine(_M):
    id: str = Field(pattern=ID_RE)
    type: Literal["line"] = "line"
    p1: str
    p2: str
    construction: bool = False


class SkCircle(_M):
    id: str = Field(pattern=ID_RE)
    type: Literal["circle"] = "circle"
    center: str
    r: float = Field(gt=0)
    construction: bool = False


class SkArc(_M):
    """Counter-clockwise arc from ``start`` to ``end`` about ``center``."""

    id: str = Field(pattern=ID_RE)
    type: Literal["arc"] = "arc"
    center: str
    start: str
    end: str
    construction: bool = False


class SkEllipse(_M):
    id: str = Field(pattern=ID_RE)
    type: Literal["ellipse"] = "ellipse"
    center: str
    rx: float = Field(gt=0)
    ry: float = Field(gt=0)
    rotation: float = 0.0
    construction: bool = False


class SkSpline(_M):
    """Interpolating B-spline through the given points."""

    id: str = Field(pattern=ID_RE)
    type: Literal["spline"] = "spline"
    points: list[str] = Field(min_length=2, max_length=256)
    closed: bool = False
    construction: bool = False


SketchEntity = Annotated[
    Union[SkPoint, SkLine, SkCircle, SkArc, SkEllipse, SkSpline], Field(discriminator="type")
]

CONSTRAINT_TYPES = (
    "coincident",
    "horizontal",
    "vertical",
    "parallel",
    "perpendicular",
    "tangent",
    "concentric",
    "equal",
    "symmetric",
    "midpoint",
    "point_on_line",
    "point_on_circle",
    "fix",
    "coordinate_x",
    "coordinate_y",
    "distance",
    "horizontal_distance",
    "vertical_distance",
    "length",
    "radius",
    "diameter",
    "angle",
)
DIMENSIONAL = {
    "coordinate_x",
    "coordinate_y",
    "distance",
    "horizontal_distance",
    "vertical_distance",
    "length",
    "radius",
    "diameter",
    "angle",
}


class SkConstraint(_M):
    id: str = Field(pattern=ID_RE)
    type: Literal[CONSTRAINT_TYPES]  # type: ignore[valid-type]
    refs: list[str] = Field(min_length=1, max_length=3)
    value: Expr | None = None
    # Position where the dimension label is drawn (sketch coords). UI only.
    label: Vec2 | None = None

    _v = field_validator("value")(_check_expr)

    @model_validator(mode="after")
    def _dim(self) -> "SkConstraint":
        if self.type in DIMENSIONAL and self.value is None:
            raise ValueError(f"{self.type} constraint requires a value")
        return self


class SketchParams(_M):
    plane: PlaneRef = PlaneRef()
    entities: list[SketchEntity] = Field(default_factory=list, max_length=5000)
    constraints: list[SkConstraint] = Field(default_factory=list, max_length=5000)


# ---------------------------------------------------------------- features
Operation = Literal["new", "join", "cut", "intersect"]


class _Solid(_M):
    operation: Operation = "new"
    body: str | None = Field(default=None, max_length=64)


class ExtrudeParams(_Solid):
    sketch: str
    regions: list[str] | None = None
    distance: Expr = 10
    direction: Literal["normal", "reverse", "symmetric"] = "normal"
    extent: Literal["blind", "through_all"] = "blind"
    _v = field_validator("distance")(_check_expr)


class RevolveParams(_Solid):
    sketch: str
    regions: list[str] | None = None
    axis: AxisRef
    angle: Expr = 360


class SweepParams(_Solid):
    sketch: str
    regions: list[str] | None = None
    path_sketch: str
    path_entities: list[str] | None = None


class LoftParams(_Solid):
    sketches: list[str] = Field(min_length=2, max_length=32)
    ruled: bool = False


class HoleParams(_M):
    body: str
    plane: PlaneRef
    positions: list[tuple[Expr, Expr]] = Field(min_length=1, max_length=500)
    diameter: Expr = 6.6
    end: Literal["blind", "through_all"] = "through_all"
    depth: Expr | None = None
    cbore_diameter: Expr | None = None
    cbore_depth: Expr | None = None
    csink_diameter: Expr | None = None
    csink_angle: Expr = 90


class FilletParams(_M):
    body: str
    edges: list[EdgeRef] = Field(min_length=1, max_length=2000)
    radius: Expr = 1


class ChamferParams(_M):
    body: str
    edges: list[EdgeRef] = Field(min_length=1, max_length=2000)
    distance: Expr = 1


class ShellParams(_M):
    body: str
    remove_faces: list[str] = Field(default_factory=list)
    thickness: Expr = 2


class BooleanParams(_M):
    operation: Literal["join", "cut", "intersect"] = "join"
    target: str
    tools: list[str] = Field(min_length=1)
    keep_tools: bool = False


class LinearPatternParams(_M):
    body: str
    features: list[str] | None = None  # None => pattern the whole body
    direction: AxisRef
    count: Expr = 2
    spacing: Expr = 10
    direction2: AxisRef | None = None
    count2: Expr = 1
    spacing2: Expr = 10


class CircularPatternParams(_M):
    body: str
    features: list[str] | None = None
    axis: AxisRef
    count: Expr = 4
    angle: Expr = 360


class MirrorParams(_M):
    body: str
    features: list[str] | None = None
    plane: PlaneRef


class DatumPlaneParams(_M):
    plane: PlaneRef


class DatumAxisParams(_M):
    axis: AxisRef


class PrimitiveParams(_Solid):
    shape: Literal["box", "cylinder", "sphere", "cone"]
    size: tuple[Expr, Expr, Expr] = (10, 10, 10)  # box: dx,dy,dz; cyl: r,h,-; sphere: r; cone: r1,r2,h
    origin: tuple[Expr, Expr, Expr] = (0, 0, 0)
    axis: Vec3 = (0, 0, 1)


class TransformParams(_M):
    body: str
    translate: tuple[Expr, Expr, Expr] = (0, 0, 0)
    rotate_axis: AxisRef | None = None
    rotate_angle: Expr = 0
    copy_to: str | None = None


class ImportParams(_M):
    blob_key: str = Field(max_length=512)
    sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    filename: str = Field(max_length=255)
    format: Literal["step", "iges", "brep"] = "step"
    body: str | None = None


class _F(_M):
    id: str = Field(pattern=ID_RE)
    name: str = Field(default="", max_length=128)
    suppressed: bool = False


class SketchF(_F):
    type: Literal["sketch"] = "sketch"
    params: SketchParams


class ExtrudeF(_F):
    type: Literal["extrude"] = "extrude"
    params: ExtrudeParams


class RevolveF(_F):
    type: Literal["revolve"] = "revolve"
    params: RevolveParams


class SweepF(_F):
    type: Literal["sweep"] = "sweep"
    params: SweepParams


class LoftF(_F):
    type: Literal["loft"] = "loft"
    params: LoftParams


class HoleF(_F):
    type: Literal["hole"] = "hole"
    params: HoleParams


class FilletF(_F):
    type: Literal["fillet"] = "fillet"
    params: FilletParams


class ChamferF(_F):
    type: Literal["chamfer"] = "chamfer"
    params: ChamferParams


class ShellF(_F):
    type: Literal["shell"] = "shell"
    params: ShellParams


class BooleanF(_F):
    type: Literal["boolean"] = "boolean"
    params: BooleanParams


class LinearPatternF(_F):
    type: Literal["linear_pattern"] = "linear_pattern"
    params: LinearPatternParams


class CircularPatternF(_F):
    type: Literal["circular_pattern"] = "circular_pattern"
    params: CircularPatternParams


class MirrorF(_F):
    type: Literal["mirror"] = "mirror"
    params: MirrorParams


class DatumPlaneF(_F):
    type: Literal["datum_plane"] = "datum_plane"
    params: DatumPlaneParams


class DatumAxisF(_F):
    type: Literal["datum_axis"] = "datum_axis"
    params: DatumAxisParams


class PrimitiveF(_F):
    type: Literal["primitive"] = "primitive"
    params: PrimitiveParams


class TransformF(_F):
    type: Literal["transform"] = "transform"
    params: TransformParams


class ImportF(_F):
    type: Literal["import"] = "import"
    params: ImportParams


Feature = Annotated[
    Union[
        SketchF,
        ExtrudeF,
        RevolveF,
        SweepF,
        LoftF,
        HoleF,
        FilletF,
        ChamferF,
        ShellF,
        BooleanF,
        LinearPatternF,
        CircularPatternF,
        MirrorF,
        DatumPlaneF,
        DatumAxisF,
        PrimitiveF,
        TransformF,
        ImportF,
    ],
    Field(discriminator="type"),
]

FEATURE_TYPES = [
    "sketch", "extrude", "revolve", "sweep", "loft", "hole", "fillet", "chamfer", "shell",
    "boolean", "linear_pattern", "circular_pattern", "mirror", "datum_plane", "datum_axis",
    "primitive", "transform", "import",
]


# ---------------------------------------------------------------- document
class Units(_M):
    length: Literal["mm", "cm", "m", "in"] = "mm"
    angle: Literal["deg"] = "deg"
    mass: Literal["kg", "g", "lb"] = "kg"


class Parameter(_M):
    name: str = Field(pattern=NAME_RE)
    expression: str = Field(max_length=512)
    kind: Literal["length", "angle", "count", "unitless"] = "length"
    description: str = Field(default="", max_length=500)


class MaterialAssignment(_M):
    """Material assigned to a body. ``source`` must say where values came from."""

    name: str = Field(max_length=128)
    density_kg_m3: float = Field(gt=0, lt=30000)
    youngs_modulus_mpa: float | None = Field(default=None, gt=0)
    poisson_ratio: float | None = Field(default=None, ge=0, lt=0.5)
    yield_strength_mpa: float | None = Field(default=None, gt=0)
    thermal_conductivity_w_mk: float | None = Field(default=None, gt=0)
    specific_heat_j_kgk: float | None = Field(default=None, gt=0)
    source: str = Field(default="", max_length=500)
    material_id: str | None = None


class Document(_M):
    schema_version: Literal[1] = SCHEMA_VERSION
    units: Units = Units()
    parameters: list[Parameter] = Field(default_factory=list, max_length=1000)
    features: list[Feature] = Field(default_factory=list, max_length=2000)
    materials: dict[str, MaterialAssignment] = Field(default_factory=dict)
    annotations: list[dict[str, Any]] = Field(default_factory=list, max_length=5000)
    metadata: dict[str, str] = Field(default_factory=dict)

    @model_validator(mode="after")
    def _unique(self) -> "Document":
        ids = [f.id for f in self.features]
        dup = {i for i in ids if ids.count(i) > 1}
        if dup:
            raise ValueError(f"duplicate feature ids: {sorted(dup)}")
        names = [p.name for p in self.parameters]
        dupn = {n for n in names if names.count(n) > 1}
        if dupn:
            raise ValueError(f"duplicate parameter names: {sorted(dupn)}")
        for f in self.features:
            if isinstance(f, SketchF):
                eids = [e.id for e in f.params.entities] + [c.id for c in f.params.constraints]
                d = {i for i in eids if eids.count(i) > 1}
                if d:
                    raise ValueError(f"sketch {f.id}: duplicate entity/constraint ids {sorted(d)}")
        return self


def empty_document() -> Document:
    return Document()


_IDENT = re.compile(r"[A-Za-z_][A-Za-z0-9_]*")


def string_leaves(obj: Any) -> list[str]:
    out: list[str] = []
    if isinstance(obj, str):
        out.append(obj)
    elif isinstance(obj, dict):
        for v in obj.values():
            out.extend(string_leaves(v))
    elif isinstance(obj, (list, tuple)):
        for v in obj:
            out.extend(string_leaves(v))
    return out


def identifiers_in(obj: Any) -> set[str]:
    ids: set[str] = set()
    for s in string_leaves(obj):
        ids.update(_IDENT.findall(s))
    return ids
