"""Assembly document schema (schema_version 1).

An assembly is a tree of groups (sub-assemblies) and instances. Every instance
references a part document revision (pinned or following head) or a catalog
item, and carries a rigid placement. Mates constrain placements; the mate
solver writes solved placements back. Components keep their references and
revision pins when the assembly is saved, so history is reproducible.
"""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator

from ..kernel.schema import ID_RE, EdgeRef, Expr, Parameter, Vec3


class _M(BaseModel):
    model_config = ConfigDict(extra="forbid", json_schema_serialization_defaults_required=True)


class Placement(_M):
    position: Vec3 = (0.0, 0.0, 0.0)  # mm
    rotation: Vec3 = (0.0, 0.0, 0.0)  # extrinsic XYZ Euler angles, degrees (R = Rz*Ry*Rx)


class InstanceSource(_M):
    kind: Literal["document", "catalog"]
    document_id: str | None = None
    revision: int | None = None  # None = follow head revision
    body: str | None = None  # None = all bodies of the part as one rigid component
    item_id: str | None = None

    @model_validator(mode="after")
    def _req(self) -> "InstanceSource":
        if self.kind == "document" and not self.document_id:
            raise ValueError("document source requires document_id")
        if self.kind == "catalog" and not self.item_id:
            raise ValueError("catalog source requires item_id")
        return self


class Instance(_M):
    id: str = Field(pattern=ID_RE)
    name: str = Field(max_length=160)
    source: InstanceSource
    placement: Placement = Placement()
    group: str | None = None
    fixed: bool = False
    suppressed: bool = False
    color: str | None = Field(default=None, pattern=r"^#[0-9a-fA-F]{6}$")


class Group(_M):
    id: str = Field(pattern=ID_RE)
    name: str = Field(max_length=160)
    parent: str | None = None


class MateRef(_M):
    """Geometry on an instance used by a mate, expressed in the part frame.

    kind=face: a named face (planar -> plane; cylindrical/conical -> axis).
    kind=edge: a persistent edge reference (line -> axis; circle -> axis + centre).
    kind=origin: the part origin / axes (``axis`` X|Y|Z, ``plane`` XY|XZ|YZ).
    """

    instance: str
    kind: Literal["face", "edge", "origin"]
    face: str | None = None
    edge: EdgeRef | None = None
    body: str | None = None
    axis: Literal["X", "Y", "Z"] | None = None
    plane: Literal["XY", "XZ", "YZ"] | None = None
    point: bool = False  # use the point (circle centre / origin) rather than the axis/plane


MATE_TYPES = ("fixed", "coincident", "concentric", "parallel", "perpendicular", "distance", "angle", "slider", "revolute")


class Mate(_M):
    id: str = Field(pattern=ID_RE)
    name: str = Field(default="", max_length=160)
    type: Literal[MATE_TYPES]  # type: ignore[valid-type]
    a: MateRef
    b: MateRef | None = None
    value: Expr | None = None  # distance (mm) / angle (deg) / revolute axial offset
    flip: bool = False  # coincident/distance planes: aligned instead of opposed normals
    limits: tuple[float, float] | None = None  # revolute: deg, slider: mm
    suppressed: bool = False

    @model_validator(mode="after")
    def _req(self) -> "Mate":
        if self.type != "fixed" and self.b is None:
            raise ValueError(f"{self.type} mate requires two references")
        if self.type in ("distance", "angle") and self.value is None:
            raise ValueError(f"{self.type} mate requires a value")
        return self


class KinematicStudy(_M):
    mate: str  # revolute or slider mate being driven
    start: float
    end: float
    steps: int = Field(default=24, ge=2, le=120)
    duration_s: float = Field(default=1.0, gt=0)
    check_collisions: bool = True


class AssemblyDocument(_M):
    schema_version: Literal[1] = 1
    parameters: list[Parameter] = Field(default_factory=list)
    groups: list[Group] = Field(default_factory=list, max_length=500)
    instances: list[Instance] = Field(default_factory=list, max_length=2000)
    mates: list[Mate] = Field(default_factory=list, max_length=5000)
    studies: list[KinematicStudy] = Field(default_factory=list, max_length=20)
    metadata: dict[str, str] = Field(default_factory=dict)

    @model_validator(mode="after")
    def _refs(self) -> "AssemblyDocument":
        ids = [i.id for i in self.instances]
        dup = {i for i in ids if ids.count(i) > 1}
        if dup:
            raise ValueError(f"duplicate instance ids {sorted(dup)}")
        gids = {g.id for g in self.groups}
        for i in self.instances:
            if i.group and i.group not in gids:
                raise ValueError(f"instance {i.id} references unknown group {i.group}")
        # group parent cycle check
        parent = {g.id: g.parent for g in self.groups}
        for g in self.groups:
            seen, cur = set(), g.id
            while cur:
                if cur in seen:
                    raise ValueError(f"group hierarchy cycle at {g.id}")
                seen.add(cur)
                cur = parent.get(cur)
                if cur and cur not in gids:
                    raise ValueError(f"group {g.id} has unknown parent")
        iset = set(ids)
        mids = [m.id for m in self.mates]
        if len(set(mids)) != len(mids):
            raise ValueError("duplicate mate ids")
        for m in self.mates:
            for r in (m.a, m.b):
                if r is not None and r.instance not in iset:
                    raise ValueError(f"mate {m.id} references unknown instance {r.instance}")
            if m.b is not None and m.a.instance == m.b.instance:
                raise ValueError(f"mate {m.id} references the same instance twice")
        return self
