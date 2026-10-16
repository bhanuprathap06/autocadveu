"""Drawing content schema and GD&T semantic validation.

Standards are configured per drawing (``sheet.standard``). Annotations may not
silently mix standards: an annotation declaring another standard is an error.

GD&T rules implemented (subset; see docs/gdt.md for sources and status):
* Form tolerances (straightness, flatness, circularity, cylindricity) never
  reference datums.
* Orientation, location and runout tolerances require at least one datum.
* ASME Y14.5-2018 removed concentricity and symmetry -> rejected under ASME
  (ISO 1101 still defines them).
* Material condition modifiers (M/L) are not permitted on flatness,
  circularity, cylindricity, profile, runout, concentricity or symmetry.
* The diameter zone symbol is permitted only for characteristics that can
  control an axis (position, straightness, orientation, concentricity).
* Every referenced datum letter must be defined by a datum feature symbol.
* Tolerance values must be positive.
"""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator

from ..kernel.schema import EdgeRef

Standard = Literal["ISO", "ASME"]
STANDARD_NAMES = {"ISO": "ISO GPS (ISO 1101:2017, ISO 129-1, ISO 128)", "ASME": "ASME Y14.5-2018"}

FORM = {"straightness", "flatness", "circularity", "cylindricity"}
PROFILE = {"profile_line", "profile_surface"}
ORIENTATION = {"parallelism", "perpendicularity", "angularity"}
LOCATION = {"position", "concentricity", "symmetry"}
RUNOUT = {"circular_runout", "total_runout"}
CHARACTERISTICS = FORM | PROFILE | ORIENTATION | LOCATION | RUNOUT
SYMBOLS = {
    "straightness": "⏤", "flatness": "⏥", "circularity": "○", "cylindricity": "⌭",
    "profile_line": "⌒", "profile_surface": "⌓", "parallelism": "∥", "perpendicularity": "⟂",
    "angularity": "∠", "position": "⌖", "concentricity": "◎", "symmetry": "⌯",
    "circular_runout": "↗", "total_runout": "⌰",
}
NO_MODIFIER = {"flatness", "circularity", "cylindricity", "profile_line", "profile_surface", "circular_runout", "total_runout",
               "concentricity", "symmetry"}
DIAMETER_OK = {"position", "straightness", "parallelism", "perpendicularity", "angularity", "concentricity"}
SHEETS = {"A4": (297, 210), "A3": (420, 297), "A2": (594, 420), "A1": (841, 594), "A0": (1189, 841)}
STD_SCALES = [10, 5, 2, 1, 0.5, 0.2, 0.1, 0.05, 0.02, 0.01]


class _M(BaseModel):
    model_config = ConfigDict(extra="forbid", json_schema_serialization_defaults_required=True)


class Sheet(_M):
    size: Literal["A4", "A3", "A2", "A1", "A0"] = "A3"
    orientation: Literal["landscape", "portrait"] = "landscape"
    scale: float | None = Field(default=None, gt=0, le=100)  # None = automatic standard scale
    standard: Standard = "ISO"
    projection: Literal["first_angle", "third_angle"] | None = None  # default: ISO first, ASME third
    general_tolerance: str = Field(default="ISO 2768-m", max_length=80)


class TitleBlock(_M):
    title: str = Field(default="", max_length=120)
    drawing_number: str = Field(default="", max_length=60)
    revision: str = Field(default="A", max_length=10)
    company: str = Field(default="", max_length=120)
    drawn_by: str = Field(default="", max_length=60)
    checked_by: str = Field(default="", max_length=60)
    approved_by: str = Field(default="", max_length=60)
    date: str = Field(default="", max_length=20)
    material: str = Field(default="", max_length=120)
    finish: str = Field(default="", max_length=120)


class View(_M):
    id: str = Field(pattern=r"^[A-Za-z0-9_\-]{1,32}$")
    kind: Literal["front", "top", "right", "left", "bottom", "back", "iso"]
    show_hidden: bool = True
    offset: tuple[float, float] = (0.0, 0.0)  # manual nudge in sheet mm


class Tolerance(_M):
    kind: Literal["symmetric", "deviation", "limits", "fit"] = "symmetric"
    plus: float | None = None
    minus: float | None = None
    fit: str | None = Field(default=None, pattern=r"^[A-Za-z]{1,2}\d{1,2}$")


class Dimension(_M):
    id: str = Field(pattern=r"^[A-Za-z0-9_\-]{1,32}$")
    view: str
    kind: Literal["length", "diameter", "radius", "distance"]
    edge: EdgeRef | None = None  # length / diameter / radius of an edge
    edge_b: EdgeRef | None = None  # distance between two edges (closest points)
    body: str | None = None
    offset: float = 10.0
    tolerance: Tolerance | None = None
    prefix: str = Field(default="", max_length=10)


class DatumRef(_M):
    letter: str = Field(pattern=r"^[A-HJ-NP-Z]{1,2}$")  # I, O, Q excluded per ASME/ISO practice
    modifier: Literal["M", "L"] | None = None


class FeatureControlFrame(_M):
    kind: Literal["gdt"] = "gdt"
    id: str = Field(pattern=r"^[A-Za-z0-9_\-]{1,32}$")
    view: str
    at: tuple[float, float]  # view coordinates (model mm) of the leader target
    characteristic: str
    tolerance: float
    diameter_zone: bool = False
    modifier: Literal["M", "L"] | None = None
    datums: list[DatumRef] = Field(default_factory=list, max_length=3)
    standard: Standard | None = None
    target_face: str | None = None


class DatumFeature(_M):
    kind: Literal["datum"] = "datum"
    id: str = Field(pattern=r"^[A-Za-z0-9_\-]{1,32}$")
    view: str
    at: tuple[float, float]
    letter: str = Field(pattern=r"^[A-HJ-NP-Z]{1,2}$")
    target_face: str | None = None


class Note(_M):
    kind: Literal["note"] = "note"
    id: str = Field(pattern=r"^[A-Za-z0-9_\-]{1,32}$")
    text: str = Field(max_length=2000)
    position: tuple[float, float] | None = None  # sheet mm; None -> notes area
    view: str | None = None
    at: tuple[float, float] | None = None  # optional leader target in view coords


class SurfaceFinish(_M):
    kind: Literal["surface_finish"] = "surface_finish"
    id: str = Field(pattern=r"^[A-Za-z0-9_\-]{1,32}$")
    view: str
    at: tuple[float, float]
    ra_um: float = Field(gt=0, le=100)
    process: Literal["any", "removal_required", "removal_prohibited"] = "removal_required"
    standard: Standard | None = None


Annotation = FeatureControlFrame | DatumFeature | Note | SurfaceFinish


class RevisionRow(_M):
    rev: str = Field(max_length=10)
    date: str = Field(max_length=20)
    description: str = Field(max_length=200)
    by: str = Field(max_length=60)


class DrawingContent(_M):
    schema_version: Literal[1] = 1
    sheet: Sheet = Sheet()
    title_block: TitleBlock = TitleBlock()
    views: list[View] = Field(default_factory=lambda: [View(id="v1", kind="front"), View(id="v2", kind="top"),
                                                        View(id="v3", kind="right"), View(id="v4", kind="iso", show_hidden=False)])
    auto_dimensions: bool = True
    dimensions: list[Dimension] = Field(default_factory=list, max_length=500)
    annotations: list[Annotation] = Field(default_factory=list, max_length=500)
    revisions: list[RevisionRow] = Field(default_factory=list, max_length=50)
    show_bom: bool = True

    @model_validator(mode="after")
    def _ids(self) -> "DrawingContent":
        vids = [v.id for v in self.views]
        if len(set(vids)) != len(vids):
            raise ValueError("duplicate view ids")
        kinds = [v.kind for v in self.views]
        if len(set(kinds)) != len(kinds):
            raise ValueError("each view kind may appear once")
        for d in self.dimensions:
            if d.view not in vids:
                raise ValueError(f"dimension {d.id} references unknown view {d.view}")
        for a in self.annotations:
            v = getattr(a, "view", None)
            if v is not None and v not in vids:
                raise ValueError(f"annotation {a.id} references unknown view {v}")
        return self


def validate_gdt(content: DrawingContent) -> list[dict[str, str]]:
    """Semantic validation. Returns a list of {id, severity, message}."""
    issues: list[dict[str, str]] = []
    std = content.sheet.standard
    datums = {a.letter for a in content.annotations if isinstance(a, DatumFeature)}
    letters = [a.letter for a in content.annotations if isinstance(a, DatumFeature)]
    for dup in {x for x in letters if letters.count(x) > 1}:
        issues.append({"id": dup, "severity": "error", "message": f"Datum {dup} is defined more than once"})

    def err(a, msg):
        issues.append({"id": a.id, "severity": "error", "message": msg})

    for a in content.annotations:
        if isinstance(a, (FeatureControlFrame, SurfaceFinish)) and a.standard and a.standard != std:
            err(a, f"Annotation declares {a.standard} but the drawing standard is {std}; standards must not be mixed")
        if not isinstance(a, FeatureControlFrame):
            continue
        c = a.characteristic
        if c not in CHARACTERISTICS:
            err(a, f"Unknown geometric characteristic {c!r}")
            continue
        if a.tolerance <= 0:
            err(a, "Tolerance value must be positive")
        if c in FORM and a.datums:
            err(a, f"{c} is a form tolerance and cannot reference datums")
        if (c in ORIENTATION or c in LOCATION or c in RUNOUT) and not a.datums:
            err(a, f"{c} requires at least one datum reference")
        if std == "ASME" and c in ("concentricity", "symmetry"):
            err(a, f"{c} was removed in ASME Y14.5-2018; use position or profile instead")
        if a.modifier and c in NO_MODIFIER:
            err(a, f"Material condition modifier {a.modifier} is not applicable to {c}")
        if a.diameter_zone and c not in DIAMETER_OK:
            err(a, f"A diameter tolerance zone is not applicable to {c}")
        if c in RUNOUT and any(d.modifier for d in a.datums):
            err(a, "Datum material modifiers are not applicable to runout")
        refs = [d.letter for d in a.datums]
        if len(set(refs)) != len(refs):
            err(a, "A datum letter may appear only once in a feature control frame")
        for d in a.datums:
            if d.letter not in datums:
                err(a, f"Datum {d.letter} is referenced but no datum feature symbol defines it")
    return issues


def default_projection(sheet: Sheet) -> str:
    return sheet.projection or ("third_angle" if sheet.standard == "ASME" else "first_angle")
