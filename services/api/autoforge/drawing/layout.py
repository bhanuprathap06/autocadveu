"""Sheet composition: views + annotations -> renderer-independent primitives.

Primitive kinds (sheet millimetres, origin bottom-left, y up):
  line      {a, b, layer}
  poly      {pts, layer, closed}
  circle    {c, r, layer, fill}
  text      {p, text, size, anchor(start|middle|end), rotate, layer}
  arrow     {tip, dir, layer}
Layers: border, visible, smooth, hidden, center, dim, text, annot, table.
"""

from __future__ import annotations

import math
from typing import Any

import numpy as np

from .schema import (SHEETS, STANDARD_NAMES, STD_SCALES, SYMBOLS, DatumFeature, DrawingContent, FeatureControlFrame, Note,
                     SurfaceFinish, default_projection)

GAP = 22.0
TEXT_H = 3.5
TB_W, TB_H = 180.0, 56.0


class Canvas:
    def __init__(self) -> None:
        self.items: list[dict[str, Any]] = []

    def line(self, a, b, layer="visible"):
        self.items.append({"k": "line", "a": [float(a[0]), float(a[1])], "b": [float(b[0]), float(b[1])], "layer": layer})

    def poly(self, pts, layer="visible", closed=False):
        self.items.append({"k": "poly", "pts": [[float(x), float(y)] for x, y in pts], "layer": layer, "closed": closed})

    def circle(self, c, r, layer="visible", fill=False):
        self.items.append({"k": "circle", "c": [float(c[0]), float(c[1])], "r": float(r), "layer": layer, "fill": fill})

    def text(self, p, s, size=TEXT_H, anchor="start", rotate=0.0, layer="text"):
        self.items.append({"k": "text", "p": [float(p[0]), float(p[1])], "text": str(s), "size": size, "anchor": anchor,
                           "rotate": rotate, "layer": layer})

    def arrow(self, tip, direction, layer="dim"):
        d = np.asarray(direction, float)
        d = d / (np.linalg.norm(d) or 1)
        self.items.append({"k": "arrow", "tip": [float(tip[0]), float(tip[1])], "dir": d.tolist(), "layer": layer})


def fmt(v: float) -> str:
    s = f"{v:.3f}".rstrip("0").rstrip(".")
    return s if s != "-0" else "0"


def scale_label(s: float) -> str:
    return f"{fmt(s)}:1" if s >= 1 else f"1:{fmt(1 / s)}"


def tol_text(t) -> str:
    if t is None:
        return ""
    if t.kind == "fit":
        return f" {t.fit}"
    if t.kind == "symmetric" and t.plus is not None:
        return f" ±{fmt(t.plus)}"
    if t.kind in ("deviation", "limits"):
        return f" +{fmt(t.plus or 0)}/-{fmt(abs(t.minus or 0))}"
    return ""


class Layout:
    def __init__(self, content: DrawingContent, proj: dict[str, Any], dims: dict[str, Any], meta: dict[str, Any]):
        self.c = content
        self.proj = proj
        self.dims = dims
        self.meta = meta
        w, h = SHEETS[content.sheet.size]
        if content.sheet.orientation == "portrait":
            w, h = h, w
        self.W, self.H = float(w), float(h)
        self.cv = Canvas()
        self.view_xf: dict[str, tuple[float, float, float, float, float]] = {}  # id -> (cx, cy, vcx, vcy, scale)
        self.warnings: list[str] = []

    # ---------------------------------------------------------------- frame
    def frame(self) -> tuple[float, float, float, float]:
        cv = self.cv
        x0, y0, x1, y1 = 20.0, 10.0, self.W - 10.0, self.H - 10.0
        cv.poly([(0, 0), (self.W, 0), (self.W, self.H), (0, self.H)], "border", True)
        cv.poly([(x0, y0), (x1, y0), (x1, y1), (x0, y1)], "border", True)
        return x0, y0, x1, y1

    def title_block(self, x1: float, y0: float) -> None:
        cv, tb, s = self.cv, self.c.title_block, self.c.sheet
        x, y = x1 - TB_W, y0
        cv.poly([(x, y), (x1, y), (x1, y + TB_H), (x, y + TB_H)], "table", True)
        rows = [y + 8, y + 16, y + 24, y + 34, y + 44]
        for r in rows:
            cv.line((x, r), (x1, r), "table")
        cv.line((x + 60, y), (x + 60, y + 34), "table")
        cv.line((x + 120, y), (x + 120, y + 34), "table")
        cells = [
            ((x + 2, y + 2), f"DRAWN: {tb.drawn_by}"), ((x + 62, y + 2), f"CHECKED: {tb.checked_by}"),
            ((x + 122, y + 2), f"APPROVED: {tb.approved_by}"),
            ((x + 2, y + 10), f"DATE: {tb.date}"), ((x + 62, y + 10), f"SCALE: {scale_label(self.scale)}"),
            ((x + 122, y + 10), f"SHEET: {s.size} REV {tb.revision}"),
            ((x + 2, y + 18), f"MATERIAL: {tb.material or self.meta.get('material') or '-'}"),
            ((x + 62, y + 18), f"MASS: {self.meta.get('mass_text', '-')}"),
            ((x + 122, y + 18), f"UNITS: mm"),
            ((x + 2, y + 26), "STANDARD: " + ("ISO GPS" if s.standard == "ISO" else "ASME Y14.5-2018")),
            ((x + 62, y + 26), f"GEN. TOL: {s.general_tolerance}"),
            ((x + 122, y + 26), "PROJ: " + ("1st angle" if self.projection == "first_angle" else "3rd angle")),
        ]
        for p, t in cells:
            cv.text(p, t, 2.2)
        cv.text((x + 2, y + 36.5), f"DWG NO: {tb.drawing_number or '-'}", 3.0)
        cv.text((x + 2, y + 47), tb.title or self.meta.get("title", "Untitled"), 5.0)
        cv.text((x1 - 2, y + 47), tb.company, 3.0, "end")
        self._projection_symbol(x + 150, y + 38)

    def _projection_symbol(self, x: float, y: float) -> None:
        cv = self.cv
        first = self.projection == "first_angle"
        cx_circle, cx_cone = (x + 18, x) if first else (x, x + 14)
        cv.circle((cx_circle + 2.5, y + 2.5), 2.5, "table")
        cv.circle((cx_circle + 2.5, y + 2.5), 1.2, "table")
        cv.poly([(cx_cone, y + 1.3), (cx_cone + 8, y), (cx_cone + 8, y + 5), (cx_cone, y + 3.7)], "table", True)

    def revision_table(self, x1: float, y1: float) -> None:
        revs = self.c.revisions
        if not revs:
            return
        cv = self.cv
        w = 140
        x = x1 - w
        top = y1
        bottom = top - 6 * (len(revs) + 1)
        cv.poly([(x, top), (x1, top), (x1, bottom), (x, bottom)], "table", True)
        cols = [0, 14, 40, 120]
        for c in cols[1:]:
            cv.line((x + c, top), (x + c, bottom), "table")
        for j, h in enumerate(("REV", "DATE", "DESCRIPTION", "BY")):
            cv.text((x + cols[j] + 1.5, top - 4.5), h, 2.2)
        for i, r in enumerate(revs, start=1):
            yy = top - 6 * i
            cv.line((x, yy), (x1, yy), "table")
            for j, t in enumerate((r.rev, r.date, r.description[:40], r.by[:10])):
                cv.text((x + cols[j] + 1.5, yy - 4.5), t, 2.2)

    # ---------------------------------------------------------------- views
    def place_views(self, x0, y0, x1, y1) -> None:
        views = {v.kind: v for v in self.c.views}
        bb = {k: self.proj[k]["bbox"] for k in views}
        size = {k: (b[2] - b[0], b[3] - b[1]) for k, b in bb.items()}
        first = self.projection == "first_angle"
        grid = {"front": (1, 1)}
        if first:
            grid.update({"top": (1, 0), "bottom": (1, 2), "right": (0, 1), "left": (2, 1), "back": (3, 1), "iso": (2, 0)})
        else:
            grid.update({"top": (1, 2), "bottom": (1, 0), "right": (2, 1), "left": (0, 1), "back": (3, 1), "iso": (2, 2)})
        used = {k: grid[k] for k in views}
        if "front" not in used:  # a single non-front view is placed centrally
            used = {k: (1, 1) for k in list(views)[:1]} | {k: grid[k] for k in list(views)[1:]}
        cols = sorted({c for c, _ in used.values()})
        rows = sorted({r for _, r in used.values()})
        avail_w = (x1 - x0) - 10
        avail_h = (y1 - y0) - TB_H - 14 - (self._bom_height() if self.meta.get("bom") and self.c.show_bom else 0)

        def need(scale: float) -> tuple[float, float]:
            cw = {c: max([size[k][0] for k, (cc, _) in used.items() if cc == c] + [0]) * scale for c in cols}
            rh = {r: max([size[k][1] for k, (_, rr) in used.items() if rr == r] + [0]) * scale for r in rows}
            return sum(cw.values()) + GAP * (len(cols) + 1), sum(rh.values()) + GAP * (len(rows) + 1)

        if self.c.sheet.scale:
            self.scale = float(self.c.sheet.scale)
            nw, nh = need(self.scale)
            if nw > avail_w or nh > avail_h:
                self.warnings.append(f"Views do not fit the sheet at {scale_label(self.scale)}; choose a larger sheet or scale")
        else:
            self.scale = next((s for s in STD_SCALES if need(s)[0] <= avail_w and need(s)[1] <= avail_h), STD_SCALES[-1])
        cw = {c: max([size[k][0] for k, (cc, _) in used.items() if cc == c] + [0]) * self.scale for c in cols}
        rh = {r: max([size[k][1] for k, (_, rr) in used.items() if rr == r] + [0]) * self.scale for r in rows}
        tw, th = need(self.scale)
        left = x0 + 5 + max(0.0, (avail_w - tw) / 2)
        bottom = y0 + TB_H + 10 + (self._bom_height() if self.meta.get("bom") and self.c.show_bom else 0) + max(0.0, (avail_h - th) / 2)
        cx_of, acc = {}, left + GAP
        for c in cols:
            cx_of[c] = acc + cw[c] / 2
            acc += cw[c] + GAP
        cy_of, acc = {}, bottom + GAP
        for r in rows:
            cy_of[r] = acc + rh[r] / 2
            acc += rh[r] + GAP
        for kind, v in views.items():
            c, r = used[kind]
            b = bb[kind]
            self.view_xf[v.id] = (cx_of[c] + v.offset[0], cy_of[r] + v.offset[1], (b[0] + b[2]) / 2, (b[1] + b[3]) / 2, self.scale)

    def S(self, vid: str, p) -> tuple[float, float]:
        cx, cy, vcx, vcy, s = self.view_xf[vid]
        return cx + (p[0] - vcx) * s, cy + (p[1] - vcy) * s

    def draw_views(self) -> None:
        cv = self.cv
        for v in self.c.views:
            pv = self.proj[v.kind]
            for pl in pv["visible"]:
                cv.poly([self.S(v.id, p) for p in pl], "visible")
            for pl in pv["smooth"]:
                cv.poly([self.S(v.id, p) for p in pl], "smooth")
            if v.show_hidden:
                for pl in pv["hidden"]:
                    cv.poly([self.S(v.id, p) for p in pl], "hidden")
            if v.kind != "iso":
                for c in pv.get("circles", []):
                    if not c["full"]:
                        continue
                    cx, cy = self.S(v.id, c["center"])
                    r = c["radius"] * self.scale
                    ext = r + 2.5
                    cv.line((cx - ext, cy), (cx + ext, cy), "center")
                    cv.line((cx, cy - ext), (cx, cy + ext), "center")
                for cl in pv.get("centerlines", []):
                    a, b = np.array(self.S(v.id, cl["a"])), np.array(self.S(v.id, cl["b"]))
                    d = b - a
                    n = np.linalg.norm(d)
                    if n < 1e-6:
                        continue
                    d = d / n
                    cv.line(a - 2.5 * d, b + 2.5 * d, "center")
            b = pv["bbox"]
            lx, ly = self.S(v.id, ((b[0] + b[2]) / 2, b[1]))
            label_drop = 22 if (self.c.auto_dimensions and v.kind in ("front", "top", "right", "left")) else 10
            cv.text((lx, ly - label_drop), v.kind.upper() + ("" if v.kind != "iso" else f" ({scale_label(self.scale)})"), 3.0,
                    "middle")

    # ---------------------------------------------------------------- dimensions
    def _linear_dim(self, a, b, offset_dir, offset, label):
        cv = self.cv
        a, b = np.array(a, float), np.array(b, float)
        n = np.array(offset_dir, float)
        n = n / (np.linalg.norm(n) or 1)
        a2, b2 = a + n * offset, b + n * offset
        cv.line(a + n * 1.5, a2 + n * 2, "dim")
        cv.line(b + n * 1.5, b2 + n * 2, "dim")
        cv.line(a2, b2, "dim")
        d = b2 - a2
        L = np.linalg.norm(d)
        if L < 1e-9:
            return
        u = d / L
        cv.arrow(a2, -u)
        cv.arrow(b2, u)
        mid = (a2 + b2) / 2 + n * 1.2
        ang = math.degrees(math.atan2(u[1], u[0]))
        if ang > 90 or ang < -90:
            ang += 180
        cv.text(mid, label, TEXT_H, "middle", ang, "dim")

    def auto_dimensions(self) -> None:
        views = {v.kind: v for v in self.c.views}
        done_axes: set[str] = set()
        for kind, axes in (("front", ("x", "z")), ("top", ("x", "y")), ("right", ("y", "z")), ("left", ("y", "z"))):
            v = views.get(kind)
            if v is None:
                continue
            b = self.proj[kind]["bbox"]
            w, h = b[2] - b[0], b[3] - b[1]
            if axes[0] not in done_axes and w > 1e-6:
                self._linear_dim(self.S(v.id, (b[0], b[1])), self.S(v.id, (b[2], b[1])), (0, -1), 9, fmt(w))
                done_axes.add(axes[0])
            if axes[1] not in done_axes and h > 1e-6:
                self._linear_dim(self.S(v.id, (b[0], b[1])), self.S(v.id, (b[0], b[3])), (-1, 0), 9, fmt(h))
                done_axes.add(axes[1])
        # hole/diameter callouts grouped by diameter, once per drawing
        called: set[float] = set()
        for v in self.c.views:
            if v.kind == "iso":
                continue
            groups: dict[float, list] = {}
            for c in self.proj[v.kind].get("circles", []):
                if c["full"]:
                    groups.setdefault(round(2 * c["radius"], 4), []).append(c)
            for dia, cs in sorted(groups.items()):
                if dia in called:
                    continue
                called.add(dia)
                c = cs[0]
                ctr = np.array(self.S(v.id, c["center"]))
                r = c["radius"] * self.scale
                d = np.array([1.0, 1.0]) / math.sqrt(2)
                p0 = ctr + d * r
                p1 = p0 + d * 9
                self.cv.line(p0, p1, "dim")
                self.cv.line(p1, p1 + np.array([12, 0]), "dim")
                self.cv.arrow(p0, -d)
                n = len(cs)
                self.cv.text(p1 + np.array([1, 1]), (f"{n}× " if n > 1 else "") + f"⌀{fmt(dia)}", TEXT_H, "start", 0, "dim")

    def user_dimensions(self) -> None:
        for d in self.c.dimensions:
            r = self.dims.get(d.id)
            if r is None or r.get("error"):
                self.warnings.append(f"Dimension {d.id}: {r.get('error') if r else 'not evaluated'}")
                continue
            label = d.prefix + {"diameter": "⌀", "radius": "R"}.get(d.kind, "") + fmt(r["value"]) + tol_text(d.tolerance)
            if d.kind in ("length", "distance"):
                a, b = self.S(d.view, r["a"]), self.S(d.view, r["b"])
                u = np.subtract(b, a)
                n = np.array([-u[1], u[0]])
                self._linear_dim(a, b, n, d.offset, label)
                if r.get("foreshortened"):
                    self.warnings.append(f"Dimension {d.id} shows true length of an edge that is foreshortened in this view")
            else:
                ctr = np.array(self.S(d.view, r["center"]))
                rr = r["radius"] * self.scale
                dd = np.array([math.cos(math.radians(30)), math.sin(math.radians(30))])
                p0 = ctr + dd * rr
                p1 = p0 + dd * d.offset
                self.cv.line(ctr if d.kind == "radius" else ctr - dd * rr, p1, "dim")
                self.cv.arrow(p0, dd)
                self.cv.text(p1 + np.array([1, 1]), label, TEXT_H, "start", 0, "dim")

    # ---------------------------------------------------------------- annotations
    def annotations(self, x0: float, y0: float) -> None:
        cv = self.cv
        notes = [f"GENERAL TOLERANCES: {self.c.sheet.general_tolerance} UNLESS OTHERWISE SPECIFIED.",
                 "DIMENSIONS IN MILLIMETRES.", f"INTERPRET PER {STANDARD_NAMES[self.c.sheet.standard]}."]
        for a in self.c.annotations:
            if isinstance(a, FeatureControlFrame):
                tgt = np.array(self.S(a.view, a.at))
                origin = tgt + np.array([10.0, 10.0])
                cells = [SYMBOLS.get(a.characteristic, "?"),
                         ("⌀" if a.diameter_zone else "") + fmt(a.tolerance) + (f" ({a.modifier})" if a.modifier else "")]
                cells += [d.letter + (f" ({d.modifier})" if d.modifier else "") for d in a.datums]
                x = origin[0]
                h = 6.0
                for i, t in enumerate(cells):
                    w = 8.0 if i == 0 else max(8.0, 2.0 + 2.1 * len(t))
                    cv.poly([(x, origin[1]), (x + w, origin[1]), (x + w, origin[1] + h), (x, origin[1] + h)], "annot", True)
                    cv.text((x + w / 2, origin[1] + 1.6), t, 3.0, "middle", 0, "annot")
                    x += w
                cv.line(tgt, origin, "annot")
                cv.arrow(tgt, tgt - origin, "annot")
            elif isinstance(a, DatumFeature):
                tgt = np.array(self.S(a.view, a.at))
                box = tgt + np.array([-4.0, -16.0])
                cv.poly([(box[0], box[1]), (box[0] + 8, box[1]), (box[0] + 8, box[1] + 7), (box[0], box[1] + 7)], "annot", True)
                cv.text((box[0] + 4, box[1] + 1.8), a.letter, 3.5, "middle", 0, "annot")
                cv.line((tgt[0], box[1] + 7), (tgt[0], tgt[1] - 2.5), "annot")
                cv.poly([(tgt[0] - 2, tgt[1] - 2.5), (tgt[0] + 2, tgt[1] - 2.5), (tgt[0], tgt[1])], "annot", True)
            elif isinstance(a, SurfaceFinish):
                tgt = np.array(self.S(a.view, a.at))
                p = tgt + np.array([0.0, 0.5])
                cv.poly([(p[0] - 3, p[1] + 4), (p[0], p[1]), (p[0] + 6, p[1] + 10), (p[0] + 14, p[1] + 10)], "annot")
                if a.process == "removal_required":
                    cv.line((p[0] - 3, p[1] + 4), (p[0] + 3, p[1] + 4), "annot")
                if a.process == "removal_prohibited":
                    cv.circle((p[0], p[1] + 2.6), 1.6, "annot")
                cv.text((p[0] + 7, p[1] + 11), f"Ra {fmt(a.ra_um)}", 2.5, "start", 0, "annot")
            elif isinstance(a, Note):
                if a.position:
                    cv.text(a.position, a.text, 3.0)
                    if a.view and a.at:
                        cv.line(self.S(a.view, a.at), a.position, "annot")
                else:
                    notes.append(a.text)
        y = y0 + TB_H + 4 + len(notes) * 5
        if self.meta.get("bom") and self.c.show_bom:
            y += self._bom_height()
        cv.text((x0 + 5, y + 5), "NOTES:", 3.0)
        for i, n in enumerate(notes, 1):
            cv.text((x0 + 5, y - 5 * (i - 1)), f"{i}. {n}", 2.5)

    # ---------------------------------------------------------------- BOM + balloons
    def _bom_height(self) -> float:
        return 6.0 * (len(self.meta.get("bom") or []) + 1) + 4

    def bom(self, x1: float, y0: float) -> None:
        rows = self.meta.get("bom") or []
        if not rows or not self.c.show_bom:
            return
        cv = self.cv
        x = x1 - TB_W
        base = y0 + TB_H
        cols = [0, 12, 24, 94, 134, 162, 180]
        heads = ["ITEM", "QTY", "NAME", "PART NO", "MATERIAL", "MASS kg"]
        top = base + 6 * (len(rows) + 1)
        cv.poly([(x, base), (x1, base), (x1, top), (x, top)], "table", True)
        for c in cols[1:-1]:
            cv.line((x + c, base), (x + c, top), "table")
        for i in range(1, len(rows) + 1):
            cv.line((x, base + 6 * i), (x1, base + 6 * i), "table")
        for j, h in enumerate(heads):
            cv.text((x + cols[j] + 1, top - 4.5), h, 2.2)
        for i, r in enumerate(rows):
            yy = top - 6 * (i + 2) + 1.5
            vals = [str(r["item"]), str(r["quantity"]), r["name"][:34], (r.get("part_number") or "-")[:18],
                    (r.get("material") or "-")[:14], fmt(r["unit_mass_kg"]) if r.get("unit_mass_kg") is not None else "n/a"]
            for j, t in enumerate(vals):
                cv.text((x + cols[j] + 1, yy), t, 2.2)
        main = next((v for v in self.c.views if v.kind == "iso"), self.c.views[0])
        cents = self.proj[main.kind].get("instances", {})
        for r in rows:
            iid = r["instances"][0] if r.get("instances") else None
            if iid not in cents:
                continue
            p = np.array(self.S(main.id, cents[iid]))
            q = p + np.array([14.0, 12.0])
            cv.line(p, q - np.array([3.0, 2.5]), "annot")
            cv.circle(p, 0.6, "annot", True)
            cv.circle(q, 4.0, "annot")
            cv.text((q[0], q[1] - 1.2), str(r["item"]), 3.0, "middle", 0, "annot")

    def build(self) -> dict[str, Any]:
        self.projection = default_projection(self.c.sheet)
        x0, y0, x1, y1 = self.frame()
        self.place_views(x0, y0, x1, y1)
        self.draw_views()
        if self.c.auto_dimensions:
            self.auto_dimensions()
        self.user_dimensions()
        self.bom(x1, y0)
        self.annotations(x0, y0)
        self.title_block(x1, y0)
        self.revision_table(x1, y1)
        return {"width": self.W, "height": self.H, "scale": self.scale, "projection": self.projection,
                "items": self.cv.items, "warnings": self.warnings,
                "views": {vid: {"center": [xf[0], xf[1]], "view_center": [xf[2], xf[3]], "scale": xf[4]}
                          for vid, xf in self.view_xf.items()}}
