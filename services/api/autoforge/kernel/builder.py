"""Programmatic construction of parametric documents.

Used by starter templates, the AI command registry (which may only emit
operations expressible through this builder) and test fixtures. Everything
produced is an ordinary editable :class:`schema.Document`.
"""

from __future__ import annotations

import math
from typing import Any

from . import schema as S


class SketchBuilder:
    def __init__(self, fid: str):
        self.fid = fid
        self.entities: list[Any] = []
        self.constraints: list[S.SkConstraint] = []
        self._n = 0

    def _id(self, p: str) -> str:
        self._n += 1
        return f"{p}{self._n}"

    def point(self, x: float, y: float, construction: bool = False) -> str:
        i = self._id("p")
        self.entities.append(S.SkPoint(id=i, x=x, y=y, construction=construction))
        return i

    def line(self, a: str, b: str, construction: bool = False) -> str:
        i = self._id("l")
        self.entities.append(S.SkLine(id=i, p1=a, p2=b, construction=construction))
        return i

    def con(self, type_: str, refs: list[str], value: S.Expr | None = None) -> str:
        i = self._id("c")
        self.constraints.append(S.SkConstraint(id=i, type=type_, refs=refs, value=value))
        return i

    def rect(self, x0: float, y0: float, w: float, h: float, wexpr: S.Expr | None = None, hexpr: S.Expr | None = None,
             fix_origin: bool = True, centered: bool = False) -> dict[str, str]:
        if centered:
            x0, y0 = x0 - w / 2, y0 - h / 2
        a, b = self.point(x0, y0), self.point(x0 + w, y0)
        c, d = self.point(x0 + w, y0 + h), self.point(x0, y0 + h)
        l1, l2, l3, l4 = self.line(a, b), self.line(b, c), self.line(c, d), self.line(d, a)
        self.con("horizontal", [l1])
        self.con("horizontal", [l3])
        self.con("vertical", [l2])
        self.con("vertical", [l4])
        if wexpr is not None:
            self.con("length", [l1], wexpr)
        if hexpr is not None:
            self.con("length", [l2], hexpr)
        if centered:
            o = self.point(0, 0, construction=True)
            self.con("coordinate_x", [o], 0)
            self.con("coordinate_y", [o], 0)
            diag = self.line(a, c, construction=True)
            self.con("midpoint", [o, diag])
        elif fix_origin:
            self.con("coordinate_x", [a], x0)
            self.con("coordinate_y", [a], y0)
        return {"a": a, "b": b, "c": c, "d": d, "bottom": l1, "right": l2, "top": l3, "left": l4}

    def circle(self, cx: float, cy: float, r: float, dexpr: S.Expr | None = None, fix_center: bool = True,
               cx_expr: S.Expr | None = None, cy_expr: S.Expr | None = None) -> str:
        c = self.point(cx, cy)
        i = self._id("k")
        self.entities.append(S.SkCircle(id=i, center=c, r=r))
        if dexpr is not None:
            self.con("diameter", [i], dexpr)
        if fix_center:
            self.con("coordinate_x", [c], cx_expr if cx_expr is not None else cx)
            self.con("coordinate_y", [c], cy_expr if cy_expr is not None else cy)
        return i

    def polyline(self, pts: list[tuple[float, float]], closed: bool = True) -> list[str]:
        ids = [self.point(x, y) for x, y in pts]
        lines = [self.line(ids[i], ids[i + 1]) for i in range(len(ids) - 1)]
        if closed:
            lines.append(self.line(ids[-1], ids[0]))
        return lines

    def polygon(self, n: int, r: float, cx: float = 0, cy: float = 0) -> list[str]:
        pts = [(cx + r * math.cos(2 * math.pi * k / n), cy + r * math.sin(2 * math.pi * k / n)) for k in range(n)]
        lines = self.polyline(pts)
        for i in range(1, len(lines)):
            self.con("equal", [lines[0], lines[i]])
        return lines

    def build(self, plane: S.PlaneRef | None = None, name: str = "") -> S.SketchF:
        return S.SketchF(id=self.fid, name=name or self.fid,
                         params=S.SketchParams(plane=plane or S.PlaneRef(), entities=self.entities, constraints=self.constraints))


class DocBuilder:
    def __init__(self, units: str = "mm"):
        self.doc = S.Document(units=S.Units(length=units))
        self._n = 0

    def fid(self, prefix: str = "f") -> str:
        self._n += 1
        return f"{prefix}{self._n}"

    def param(self, name: str, expr: str, kind: str = "length", description: str = "") -> "DocBuilder":
        self.doc.parameters.append(S.Parameter(name=name, expression=expr, kind=kind, description=description))
        return self

    def add(self, feature: Any) -> Any:
        self.doc.features.append(feature)
        return feature

    def sketch(self, plane: S.PlaneRef | None = None, name: str = "") -> SketchBuilder:
        sb = SketchBuilder(self.fid("sk"))
        sb._plane = plane  # type: ignore[attr-defined]
        sb._name = name  # type: ignore[attr-defined]
        return sb

    def commit(self, sb: SketchBuilder) -> str:
        self.add(sb.build(getattr(sb, "_plane", None), getattr(sb, "_name", "")))
        return sb.fid

    def feature(self, cls: type, name: str = "", **params: Any) -> str:
        fid = self.fid("f")
        pcls = cls.model_fields["params"].annotation
        self.add(cls(id=fid, name=name or fid, params=pcls(**params)))
        return fid

    def material(self, body: str, mat: S.MaterialAssignment) -> "DocBuilder":
        self.doc.materials[body] = mat
        return self

    def build(self) -> S.Document:
        return S.Document.model_validate(self.doc.model_dump())
