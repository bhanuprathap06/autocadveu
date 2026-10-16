"""Kernel benchmark fixtures (Phase 0 / performance budget evidence).

Run: ``python -m autoforge.bench`` -> prints JSON with timings.

Fixtures
--------
small_part        plate + 4 holes + fillets (typical bracket)
pattern_part      disc with 60-hole circular pattern (expensive boolean)
param_edit        re-run of small_part after a dimension change (incremental cache)
step_roundtrip    STEP export + import of pattern_part
invalid_file      malformed STEP rejected
assembly_medium   20 bodies interference matrix (190 pairs)
"""

from __future__ import annotations

import json
import math
import resource
import sys
import time

from .kernel import analysis, engine, io as kio
from .kernel import schema as S
from .kernel.builder import DocBuilder
from .kernel.tessellate import tessellate_body


def small_part() -> S.Document:
    b = DocBuilder()
    b.param("w", "120").param("h", "60").param("t", "8")
    sk = b.sketch()
    sk.rect(0, 0, 120, 60, wexpr="w", hexpr="h")
    sid = b.commit(sk)
    ext = b.feature(S.ExtrudeF, sketch=sid, distance="t", body="Bracket")
    b.feature(S.HoleF, body="Bracket", plane=S.PlaneRef(kind="face", body="Bracket", face=f"{ext}:end"),
              positions=[(10, 10), ("w-10", 10), ("w-10", "h-10"), (10, "h-10")], diameter=6.6)
    b.feature(S.FilletF, body="Bracket", radius=2, edges=[S.EdgeRef(kind="of_face", face=f"{ext}:end")])
    return b.build()


def pattern_part(n: int = 60) -> S.Document:
    b = DocBuilder()
    sk = b.sketch()
    sk.circle(0, 0, 150, dexpr=300)
    sid = b.commit(sk)
    ext = b.feature(S.ExtrudeF, sketch=sid, distance=20, body="Disc")
    h = b.feature(S.HoleF, body="Disc", plane=S.PlaneRef(kind="face", body="Disc", face=f"{ext}:end"), positions=[(120, 0)],
                  diameter=8)
    b.feature(S.CircularPatternF, body="Disc", features=[h], axis=S.AxisRef(name="Z"), count=n)
    return b.build()


def _t(fn):
    t = time.perf_counter()
    out = fn()
    return out, (time.perf_counter() - t) * 1000


def run() -> dict:
    out: dict = {"kernel": __import__("autoforge.kernel.occ", fromlist=["x"]).kernel_version()}
    engine.clear_cache()
    doc = small_part()
    r, ms = _t(lambda: engine.recompute(doc))
    tess, tms = _t(lambda: tessellate_body(r.ctx.bodies["Bracket"]))
    out["small_part"] = {"recompute_ms": ms, "tessellate_ms": tms, "triangles": tess["triangle_count"], "ok": r.ok}
    doc.parameters[2].expression = "10"
    r2, ms2 = _t(lambda: engine.recompute(doc))
    out["param_edit"] = {"recompute_ms": ms2, "cached_features": sum(f.cached for f in r2.features),
                         "total_features": len(r2.features), "ok": r2.ok}
    pdoc = pattern_part()
    rp, pms = _t(lambda: engine.recompute(pdoc, use_cache=False))
    tp, ptms = _t(lambda: tessellate_body(rp.ctx.bodies["Disc"]))
    out["pattern_part_60_holes"] = {"recompute_ms": pms, "tessellate_ms": ptms, "triangles": tp["triangle_count"], "ok": rp.ok}
    shape = rp.ctx.bodies["Disc"].shape
    step, ems = _t(lambda: kio.export_step([("Disc", shape)]))
    (_, diag), ims = _t(lambda: kio.read_shape_bytes(step, "step"))
    out["step_roundtrip"] = {"export_ms": ems, "import_ms": ims, "bytes": len(step), "valid": diag["valid"]}
    try:
        kio.read_shape_bytes(b"ISO-10303-21;\nGARBAGE", "step")
        out["invalid_file"] = {"rejected": False}
    except kio.ImportError_ as exc:
        out["invalid_file"] = {"rejected": True, "code": exc.code}
    b = DocBuilder()
    for i in range(20):
        b.feature(S.PrimitiveF, shape="box", size=(20, 20, 20), origin=(i * 15, (i % 3) * 5, 0), body=f"P{i}")
    ra = engine.recompute(b.build(), use_cache=False)
    items = [(n, bd.shape) for n, bd in ra.ctx.bodies.items()]
    rep, ims2 = _t(lambda: analysis.interference(items))
    out["assembly_medium_interference"] = {"pairs": len(rep), "ms": ims2, "interfering": sum(r["interferes"] for r in rep)}
    out["peak_rss_mb"] = resource.getrusage(resource.RUSAGE_SELF).ru_maxrss / 1024
    return out


if __name__ == "__main__":
    json.dump(run(), sys.stdout, indent=2, default=lambda o: round(o, 2) if isinstance(o, float) else str(o))
    print()
