"""Renderers for drawing primitives: SVG (viewer), PDF (reportlab), DXF (ezdxf)."""

from __future__ import annotations

import io
import math
from typing import Any
from xml.sax.saxutils import escape

LAYER_STYLE = {
    "border": (0.7, None, "#111"), "visible": (0.5, None, "#111"), "smooth": (0.25, None, "#555"),
    "hidden": (0.25, (3, 1.5), "#444"), "center": (0.18, (8, 1.5, 1.5, 1.5), "#666"), "dim": (0.18, None, "#0b5cad"),
    "text": (0.18, None, "#111"), "annot": (0.25, None, "#7a1f9b"), "table": (0.35, None, "#111"),
}
ARROW_L, ARROW_W = 3.0, 1.0


def _arrow_pts(tip, d):
    tx, ty = tip
    dx, dy = d
    bx, by = tx - dx * ARROW_L, ty - dy * ARROW_L
    nx, ny = -dy * ARROW_W / 2, dx * ARROW_W / 2
    return [(tx, ty), (bx + nx, by + ny), (bx - nx, by - ny)]


def to_svg(layout: dict[str, Any]) -> str:
    W, H = layout["width"], layout["height"]
    Y = lambda y: H - y  # noqa: E731
    out = [f'<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 {W:.3f} {H:.3f}" width="{W}mm" height="{H}mm" '
           f'font-family="DejaVu Sans, Arial, sans-serif"><rect width="100%" height="100%" fill="#fff"/>']
    for it in layout["items"]:
        lw, dash, col = LAYER_STYLE.get(it["layer"], (0.25, None, "#000"))
        ds = f' stroke-dasharray="{",".join(str(x) for x in dash)}"' if dash else ""
        st = f'stroke="{col}" stroke-width="{lw}" fill="none" stroke-linecap="round"{ds} data-layer="{it["layer"]}"'
        k = it["k"]
        if k == "line":
            (x1, y1), (x2, y2) = it["a"], it["b"]
            out.append(f'<line x1="{x1:.3f}" y1="{Y(y1):.3f}" x2="{x2:.3f}" y2="{Y(y2):.3f}" {st}/>')
        elif k == "poly":
            pts = " ".join(f"{x:.3f},{Y(y):.3f}" for x, y in it["pts"])
            tag = "polygon" if it.get("closed") else "polyline"
            out.append(f'<{tag} points="{pts}" {st}/>')
        elif k == "circle":
            fill = col if it.get("fill") else "none"
            out.append(f'<circle cx="{it["c"][0]:.3f}" cy="{Y(it["c"][1]):.3f}" r="{it["r"]:.3f}" stroke="{col}" '
                       f'stroke-width="{lw}" fill="{fill}"/>')
        elif k == "arrow":
            pts = " ".join(f"{x:.3f},{Y(y):.3f}" for x, y in _arrow_pts(it["tip"], it["dir"]))
            out.append(f'<polygon points="{pts}" fill="{col}" stroke="none"/>')
        elif k == "text":
            x, y = it["p"]
            rot = f' transform="rotate({-it["rotate"]:.2f} {x:.3f} {Y(y):.3f})"' if it.get("rotate") else ""
            out.append(f'<text x="{x:.3f}" y="{Y(y):.3f}" font-size="{it["size"]}" text-anchor="{it["anchor"]}" '
                       f'fill="{col}"{rot}>{escape(it["text"])}</text>')
    out.append("</svg>")
    return "\n".join(out)


def to_pdf(layout: dict[str, Any], title: str = "Drawing") -> bytes:
    from reportlab.lib.units import mm
    from reportlab.pdfbase import pdfmetrics
    from reportlab.pdfbase.ttfonts import TTFont
    from reportlab.pdfgen import canvas

    font = "Helvetica"
    for path in ("/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf", "/usr/share/fonts/dejavu/DejaVuSans.ttf"):
        try:
            pdfmetrics.registerFont(TTFont("DejaVu", path))
            font = "DejaVu"
            break
        except Exception:
            continue
    buf = io.BytesIO()
    c = canvas.Canvas(buf, pagesize=(layout["width"] * mm, layout["height"] * mm))
    c.setTitle(title)
    c.setCreator("AutoForge Engineering Studio")
    c.scale(mm, mm)
    for it in layout["items"]:
        lw, dash, col = LAYER_STYLE.get(it["layer"], (0.25, None, "#000"))
        c.setLineWidth(lw)
        c.setStrokeColor(col)
        c.setFillColor(col)
        c.setDash(list(dash) if dash else [])
        k = it["k"]
        if k == "line":
            c.line(*it["a"], *it["b"])
        elif k == "poly":
            p = c.beginPath()
            p.moveTo(*it["pts"][0])
            for pt in it["pts"][1:]:
                p.lineTo(*pt)
            if it.get("closed"):
                p.close()
            c.drawPath(p, stroke=1, fill=0)
        elif k == "circle":
            c.circle(it["c"][0], it["c"][1], it["r"], stroke=1, fill=1 if it.get("fill") else 0)
        elif k == "arrow":
            pts = _arrow_pts(it["tip"], it["dir"])
            p = c.beginPath()
            p.moveTo(*pts[0])
            p.lineTo(*pts[1])
            p.lineTo(*pts[2])
            p.close()
            c.setDash([])
            c.drawPath(p, stroke=0, fill=1)
        elif k == "text":
            c.saveState()
            c.translate(*it["p"])
            if it.get("rotate"):
                c.rotate(it["rotate"])
            c.setFont(font if font == "DejaVu" or it["text"].isascii() else font, it["size"])
            fn = {"middle": c.drawCentredString, "end": c.drawRightString}.get(it["anchor"], c.drawString)
            fn(0, 0, it["text"])
            c.restoreState()
    c.showPage()
    c.save()
    return buf.getvalue()


def to_dxf(layout: dict[str, Any]) -> bytes:
    import ezdxf

    doc = ezdxf.new("R2010", setup=True)
    doc.units = ezdxf.units.MM
    msp = doc.modelspace()
    ltypes = {"hidden": "DASHED", "center": "CENTER"}
    colors = {"border": 7, "visible": 7, "smooth": 8, "hidden": 8, "center": 1, "dim": 5, "text": 7, "annot": 6, "table": 7}
    for name, color in colors.items():
        lt = ltypes.get(name, "CONTINUOUS")
        if lt not in doc.linetypes:
            lt = "CONTINUOUS"
        doc.layers.add(name.upper(), color=color, linetype=lt)
    for it in layout["items"]:
        a = {"layer": it["layer"].upper()}
        k = it["k"]
        if k == "line":
            msp.add_line(it["a"], it["b"], dxfattribs=a)
        elif k == "poly":
            msp.add_lwpolyline(it["pts"], close=bool(it.get("closed")), dxfattribs=a)
        elif k == "circle":
            msp.add_circle(it["c"], it["r"], dxfattribs=a)
        elif k == "arrow":
            pts = _arrow_pts(it["tip"], it["dir"])
            msp.add_solid([pts[0], pts[1], pts[2], pts[2]], dxfattribs=a)
        elif k == "text":
            align = {"middle": "BOTTOM_CENTER", "end": "BOTTOM_RIGHT"}.get(it["anchor"], "BOTTOM_LEFT")
            t = msp.add_text(it["text"], height=it["size"], rotation=it.get("rotate", 0.0), dxfattribs=a)
            from ezdxf.enums import TextEntityAlignment

            t.set_placement(it["p"], align=TextEntityAlignment[align])
    s = io.StringIO()
    doc.write(s)
    return s.getvalue().encode("utf-8")
