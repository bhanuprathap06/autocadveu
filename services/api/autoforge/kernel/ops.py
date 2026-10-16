"""Kernel operations executed inside isolated worker processes.

Inputs and outputs are plain data (dicts / bytes) so they cross the process
boundary safely. The API never passes Python callables or shapes across.
"""

from __future__ import annotations

import os
import time
from typing import Any

from . import analysis, engine, io as kio, occ
from . import schema as S
from .naming import FeatureError
from .tessellate import tessellate_body


def _loader(allowed_prefix: str | None):
    def load(key: str, sha: str) -> bytes:
        if not allowed_prefix or not key.startswith(allowed_prefix):
            # Prevents a crafted document from reading another project's blobs.
            raise FeatureError("Referenced file does not belong to this project", "forbidden_reference")
        from ..core.storage import StorageError, get_storage

        try:
            return get_storage().get(key, expected_sha256=sha)
        except StorageError as exc:
            raise FeatureError(f"Referenced file unavailable: {exc}", "storage_error") from exc

    return load


def recompute(doc: dict[str, Any], rollback_to: str | None = None, quality: str = "normal", tessellate: bool = True,
              blob_prefix: str | None = None) -> dict[str, Any]:
    d = S.Document.model_validate(doc)
    t0 = time.perf_counter()
    res = engine.recompute(d, rollback_to=rollback_to, blob_loader=_loader(blob_prefix))
    out = res.summary()
    bodies = []
    t1 = time.perf_counter()
    for name, body in res.ctx.bodies.items():
        entry: dict[str, Any] = tessellate_body(body, quality) if tessellate else {"name": name}
        mat = d.materials.get(name)
        try:
            entry["mass"] = analysis.mass_properties(body.shape, mat.density_kg_m3 if mat else None)
        except Exception as exc:  # surfaces/no-solid bodies
            entry["mass"] = {"error": str(exc)}
        entry["material"] = mat.model_dump() if mat else None
        bodies.append(entry)
    out["bodies"] = bodies
    out["timing_ms"] = {"recompute": (t1 - t0) * 1000, "tessellate": (time.perf_counter() - t1) * 1000}
    return out


def solve_sketch(sketch: dict[str, Any], parameters: list[dict[str, Any]] | None = None,
                 drag: dict[str, list[float]] | None = None) -> dict[str, Any]:
    from ..kernel import expressions
    from ..sketch import profile, solver

    params = S.SketchParams.model_validate(sketch)
    env, perrs = expressions.resolve_parameters([(p["name"], p["expression"]) for p in parameters or []])
    sol = solver.solve(params, env, {k: (v[0], v[1]) for k, v in (drag or {}).items()} or None)
    solved = solver.apply_solution(params, sol) if sol.converged else params
    prof = profile.analyze(solved, sol)
    return {"solve": sol.to_dict(), "profile": prof.to_dict(), "sketch": solved.model_dump(mode="json"),
            "parameter_errors": perrs}


def export(doc: dict[str, Any], fmt: str, bodies: list[str] | None = None, name: str = "AutoForge",
           blob_prefix: str | None = None) -> dict[str, Any]:
    d = S.Document.model_validate(doc)
    res = engine.recompute(d, blob_loader=_loader(blob_prefix))
    failed = [f.to_dict() for f in res.features if f.status == "error"]
    sel = [(n, b.shape) for n, b in res.ctx.bodies.items() if not bodies or n in bodies]
    if not sel:
        raise FeatureError("Nothing to export: the model has no bodies" + (" (some features failed)" if failed else ""),
                           "nothing_to_export")
    if fmt == "step":
        data = kio.export_step(sel, name)
    elif fmt == "iges":
        data = kio.export_iges([s for _, s in sel])
    elif fmt == "brep":
        if len(sel) != 1:
            raise FeatureError("BREP export supports one body at a time", "invalid_parameter")
        data = kio.export_brep(sel[0][1])
    elif fmt == "stl":
        from .features import _compound

        data = kio.export_stl(sel[0][1] if len(sel) == 1 else _compound([s for _, s in sel]))
    elif fmt == "obj":
        data = kio.export_obj(sel)
    elif fmt == "glb":
        data = kio.export_glb([(n, s, (0.62, 0.66, 0.72)) for n, s in sel])
    else:
        raise FeatureError(f"Unsupported export format {fmt!r}", "unsupported_format")
    # Export verification: re-read exact formats and compare volume.
    verification: dict[str, Any] = {"bodies": [n for n, _ in sel]}
    if fmt in ("step", "iges", "brep"):
        shape, diag = kio.read_shape_bytes(data, fmt)
        verification["reimport_valid"] = diag["valid"]
        if diag.get("volume_mm3") is not None:
            src = sum(occ.volume_props(s).Mass() for _, s in sel if occ.solids(s))
            verification["volume_source_mm3"] = src
            verification["volume_reimport_mm3"] = diag["volume_mm3"]
            verification["volume_rel_error"] = abs(diag["volume_mm3"] - src) / max(src, 1e-12)
            if verification["volume_rel_error"] > 1e-4:
                raise FeatureError("Export verification failed: re-imported volume differs from the model", "export_mismatch",
                                   verification)
    elif fmt == "stl":
        n, _ = kio.parse_stl_binary(data)
        verification["triangles"] = n
    verification["failed_features"] = failed
    return {"data": data, "sha256": kio.sha256(data), "verification": verification}


def inspect_import(data: bytes, fmt: str | None, filename: str) -> dict[str, Any]:
    from .naming import Body

    shape, diag = kio.read_shape_bytes(data, fmt, filename)
    body = Body("Import", shape, [])
    t = tessellate_body(body, "draft", include_geometry=False)
    return {"diagnostics": diag, "preview": t}


def measure(doc: dict[str, Any], a: dict[str, Any], b: dict[str, Any] | None = None,
            blob_prefix: str | None = None) -> dict[str, Any]:
    d = S.Document.model_validate(doc)
    res = engine.recompute(d, blob_loader=_loader(blob_prefix))
    for ref in (a, b):
        if ref and ref.get("body") and ref["body"] not in res.ctx.bodies:
            raise FeatureError(f"Body {ref['body']!r} not found", "unresolved_reference")
    return analysis.measure(res.ctx.bodies, a, b)


def ping() -> dict[str, Any]:
    return {"pid": os.getpid(), "kernel": occ.kernel_version(), "cache": engine.cache_stats()}


def _test_sleep(seconds: float) -> dict[str, Any]:
    time.sleep(seconds)
    return {"slept": seconds}


def _test_crash() -> None:
    os._exit(3)


OPS = {
    "recompute": recompute,
    "solve_sketch": solve_sketch,
    "export": export,
    "inspect_import": inspect_import,
    "measure": measure,
    "ping": ping,
}
TEST_OPS = {"_test_sleep": _test_sleep, "_test_crash": _test_crash}


def register(name: str, fn) -> None:
    OPS[name] = fn
