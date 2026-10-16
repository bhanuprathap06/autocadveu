"""Regeneration engine.

``recompute(doc)`` walks the feature history in order and executes each
feature. Results are memoised in a process-local LRU keyed by a content hash of

* the feature definition,
* the values of the parameters it references, and
* the cache keys of the features it depends on (graph edges).

Changing a dimension therefore only re-executes the edited feature and its
downstream dependants; independent branches are restored from cache. The
cache stores *state deltas* (bodies, sketches, datums, tools written by the
feature), never partial shapes.
"""

from __future__ import annotations

import hashlib
import json
import threading
import time
import traceback
from collections import OrderedDict
from dataclasses import dataclass, field
from typing import Any, Callable

from . import expressions, graph, occ
from . import schema as S
from .features import HANDLERS, NEEDS_NEW_BODY, Context
from .naming import FeatureError

ENGINE_VERSION = "1"
_CACHE_MAX = 512
_cache: "OrderedDict[str, dict[str, Any]]" = OrderedDict()
_lock = threading.Lock()
_stats = {"hits": 0, "misses": 0}


def cache_stats() -> dict[str, int]:
    return dict(_stats, size=len(_cache))


def clear_cache() -> None:
    with _lock:
        _cache.clear()
        _stats.update(hits=0, misses=0)


@dataclass
class FeatureResult:
    id: str
    type: str
    status: str  # ok | warning | error | suppressed | skipped
    message: str = ""
    code: str = ""
    warnings: list[str] = field(default_factory=list)
    cached: bool = False
    elapsed_ms: float = 0.0
    info: Any = None

    def to_dict(self) -> dict[str, Any]:
        return {
            "id": self.id, "type": self.type, "status": self.status, "message": self.message, "code": self.code,
            "warnings": self.warnings, "cached": self.cached, "elapsed_ms": round(self.elapsed_ms, 3), "info": self.info,
        }


@dataclass
class RecomputeResult:
    ctx: Context
    features: list[FeatureResult]
    parameters: dict[str, float]
    parameter_errors: dict[str, str]
    graph: graph.Graph
    elapsed_ms: float
    rollback: str | None = None

    @property
    def ok(self) -> bool:
        return not self.parameter_errors and all(f.status in ("ok", "warning", "suppressed") for f in self.features)

    def summary(self) -> dict[str, Any]:
        return {
            "ok": self.ok,
            "kernel": occ.kernel_version(),
            "engine_version": ENGINE_VERSION,
            "elapsed_ms": round(self.elapsed_ms, 3),
            "rollback": self.rollback,
            "parameters": self.parameters,
            "parameter_errors": self.parameter_errors,
            "features": [f.to_dict() for f in self.features],
            "sketches": {k: v.to_dict() for k, v in self.ctx.sketches.items()},
            "datums": {k: _datum_dict(v) for k, v in self.ctx.datums.items()},
            "graph": {"deps": {k: sorted(v) for k, v in self.graph.deps.items()}, "errors": self.graph.errors},
            "cache": cache_stats(),
        }


def _datum_dict(d: dict[str, Any]) -> dict[str, Any]:
    if d["kind"] == "plane":
        return {"kind": "plane", **d["plane"].to_dict()}
    return {"kind": "axis", **d["axis"].to_dict()}


def _key(f: Any, params: dict[str, float], dep_keys: list[str], new_body: str) -> str:
    raw = f.model_dump(mode="json")
    used = {k: params[k] for k in sorted(S.identifiers_in(raw.get("params")) & params.keys())}
    blob = json.dumps({"v": ENGINE_VERSION, "f": raw, "p": used, "d": dep_keys, "nb": new_body}, sort_keys=True)
    return hashlib.sha256(blob.encode()).hexdigest()


def _snapshot(ctx: Context) -> dict[str, dict[str, Any]]:
    return {k: dict(getattr(ctx, k)) for k in ("bodies", "sketches", "datums", "tools")}


def _delta(before: dict[str, dict[str, Any]], ctx: Context) -> dict[str, dict[str, Any]]:
    out: dict[str, dict[str, Any]] = {}
    for k in before:
        now = getattr(ctx, k)
        d = {n: v for n, v in now.items() if before[k].get(n) is not v}
        d.update({n: None for n in before[k] if n not in now})
        if d:
            out[k] = d
    return out


def _apply(ctx: Context, delta: dict[str, dict[str, Any]]) -> None:
    for k, d in delta.items():
        target = getattr(ctx, k)
        for n, v in d.items():
            if v is None:
                target.pop(n, None)
            else:
                target[n] = v


def recompute(
    doc: S.Document,
    rollback_to: str | None = None,
    blob_loader: Callable[[str, str], bytes] | None = None,
    use_cache: bool = True,
) -> RecomputeResult:
    t0 = time.perf_counter()
    values, perrs = expressions.resolve_parameters([(p.name, p.expression) for p in doc.parameters])
    g = graph.build(doc)
    ctx = Context(params=values, blob_loader=blob_loader)
    results: list[FeatureResult] = []
    keys: dict[str, str] = {}
    failed: set[str] = set()
    new_count = 0
    stop = False

    for f in doc.features:
        if getattr(f.params, "operation", None) == "new" or f.type == "import":
            new_count += 1
        new_body = graph.default_body_name(new_count)
        if stop:
            results.append(FeatureResult(f.id, f.type, "skipped", "After rollback bar"))
            continue
        if f.suppressed:
            results.append(FeatureResult(f.id, f.type, "suppressed"))
            keys[f.id] = "suppressed:" + f.id
            failed.add(f.id)
        elif f.id in g.errors:
            results.append(FeatureResult(f.id, f.type, "error", g.errors[f.id], "graph_error"))
            keys[f.id] = "error:" + f.id
            failed.add(f.id)
        else:
            deps = sorted(g.deps.get(f.id, ()))
            bad_refs = [d for d in deps if d in failed and d in g.io[f.id].refs]
            key = _key(f, values, [keys.get(d, "") for d in deps], new_body)
            keys[f.id] = key
            ft = time.perf_counter()
            if bad_refs:
                results.append(FeatureResult(f.id, f.type, "error",
                                             f"Depends on failed or suppressed feature(s): {', '.join(bad_refs)}",
                                             "upstream_failed"))
                failed.add(f.id)
            elif use_cache and key in _cache:
                with _lock:
                    entry = _cache[key]
                    _cache.move_to_end(key)
                    _stats["hits"] += 1
                _apply(ctx, entry["delta"])
                r = entry["result"]
                results.append(FeatureResult(**{**r.__dict__, "cached": True, "elapsed_ms": (time.perf_counter() - ft) * 1000}))
                if r.status == "error":
                    failed.add(f.id)
            else:
                before = _snapshot(ctx)
                ctx.warnings = []
                handler = HANDLERS[f.type]
                try:
                    info = handler(f, ctx, new_body) if f.type in NEEDS_NEW_BODY else handler(f, ctx)
                    status = "warning" if ctx.warnings else "ok"
                    res = FeatureResult(f.id, f.type, status, warnings=list(ctx.warnings), info=info)
                except FeatureError as exc:
                    # Roll back any partial state written before the failure.
                    for k, snap in before.items():
                        setattr(ctx, k, dict(snap))
                    res = FeatureResult(f.id, f.type, "error", str(exc), exc.code, list(ctx.warnings), info=exc.details or None)
                    failed.add(f.id)
                except Exception as exc:  # OCCT Standard_Failure and friends
                    for k, snap in before.items():
                        setattr(ctx, k, dict(snap))
                    res = FeatureResult(f.id, f.type, "error", f"Kernel exception: {type(exc).__name__}: {exc}",
                                        "kernel_exception", info={"trace": traceback.format_exc(limit=3)})
                    failed.add(f.id)
                res.elapsed_ms = (time.perf_counter() - ft) * 1000
                results.append(res)
                if use_cache:
                    with _lock:
                        _stats["misses"] += 1
                        _cache[key] = {"delta": _delta(before, ctx), "result": res}
                        while len(_cache) > _CACHE_MAX:
                            _cache.popitem(last=False)
        if rollback_to and f.id == rollback_to:
            stop = True

    return RecomputeResult(ctx, results, values, perrs, g, (time.perf_counter() - t0) * 1000, rollback_to)
