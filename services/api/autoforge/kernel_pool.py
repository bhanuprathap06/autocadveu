"""Pool of isolated kernel worker processes.

Why processes: OCCT is native code. A crash (segfault), runaway memory or an
infinite loop in a geometry operation must not take down the API. Each worker
runs with an address-space limit (RLIMIT_AS); every call has a wall-clock
timeout. On timeout or crash the worker is killed and replaced, and the caller
gets a structured error. Workers are long-lived so the ~0.9 s OCCT import cost
and the incremental recompute cache are amortised.

Requests are routed by an affinity key (document id) so repeated edits of the
same document hit the same worker's cache.
"""

from __future__ import annotations

import logging
import multiprocessing as mp
import os
import threading
import time
import zlib
from typing import Any

from .core.config import get_settings
from .core.errors import ApiError, ServiceUnavailable

log = logging.getLogger("autoforge.kernel_pool")


class KernelError(ApiError):
    status = 422
    code = "kernel_error"


class KernelTimeout(ApiError):
    status = 504
    code = "kernel_timeout"


class KernelCrashed(ApiError):
    status = 502
    code = "kernel_crashed"


def _worker_main(conn: Any, mem_mb: int, allow_test_ops: bool) -> None:
    try:
        import resource

        lim = mem_mb * 1024 * 1024
        resource.setrlimit(resource.RLIMIT_AS, (lim, lim))
    except Exception:
        pass
    from .kernel import ops, occ
    from .kernel.naming import FeatureError

    occ.quiet()
    table = dict(ops.OPS)
    if allow_test_ops:
        table.update(ops.TEST_OPS)
    while True:
        try:
            msg = conn.recv()
        except (EOFError, OSError):
            return
        if msg is None:
            return
        op, kwargs = msg
        try:
            fn = table.get(op)
            if fn is None:
                conn.send((False, {"code": "unknown_op", "message": f"Unknown kernel operation {op!r}"}))
                continue
            conn.send((True, fn(**kwargs)))
        except FeatureError as exc:
            conn.send((False, {"code": exc.code, "message": str(exc), "details": exc.details}))
        except MemoryError:
            conn.send((False, {"code": "kernel_out_of_memory",
                               "message": "The geometry operation exceeded the worker memory limit"}))
        except Exception as exc:
            conn.send((False, {"code": "kernel_exception", "message": f"{type(exc).__name__}: {exc}"}))


class _Worker:
    def __init__(self, idx: int, mem_mb: int, allow_test_ops: bool):
        self.idx = idx
        self.mem_mb = mem_mb
        self.allow_test_ops = allow_test_ops
        self.lock = threading.Lock()
        self.proc: Any = None
        self.conn: Any = None
        self.restarts = 0
        self.calls = 0
        self.start()

    def start(self) -> None:
        ctx = mp.get_context("spawn")
        parent, child = ctx.Pipe()
        self.proc = ctx.Process(target=_worker_main, args=(child, self.mem_mb, self.allow_test_ops), daemon=True,
                                name=f"af-kernel-{self.idx}")
        self.proc.start()
        child.close()
        self.conn = parent

    def kill(self) -> None:
        try:
            if self.proc and self.proc.is_alive():
                self.proc.kill()
                self.proc.join(5)
        finally:
            try:
                self.conn.close()
            except Exception:
                pass

    def restart(self) -> None:
        self.kill()
        self.restarts += 1
        self.start()

    def call(self, op: str, kwargs: dict[str, Any], timeout: float) -> Any:
        with self.lock:
            if not self.proc.is_alive():
                self.restart()
            self.calls += 1
            try:
                self.conn.send((op, kwargs))
                if not self.conn.poll(timeout):
                    log.warning("kernel worker %s timed out on %s after %.1fs; restarting", self.idx, op, timeout)
                    self.restart()
                    raise KernelTimeout(f"Geometry operation '{op}' exceeded {timeout:.0f} s and was cancelled. "
                                        "Simplify the model or split the operation.")
                ok, payload = self.conn.recv()
            except (EOFError, BrokenPipeError, ConnectionResetError, OSError) as exc:
                code = self.proc.exitcode
                log.error("kernel worker %s died during %s (exit %s); restarting", self.idx, op, code)
                self.restart()
                raise KernelCrashed(f"The geometry worker crashed during '{op}' (exit code {code}). "
                                    "The operation was not applied; please retry or report the model.") from exc
        if ok:
            return payload
        raise KernelError(payload.get("message", "Kernel error"), code=payload.get("code"), details=payload.get("details"))


class KernelPool:
    def __init__(self, n: int, mem_mb: int, timeout: float, allow_test_ops: bool = False):
        self.timeout = timeout
        self.workers = [_Worker(i, mem_mb, allow_test_ops) for i in range(max(1, n))]
        self.started_at = time.time()

    def call(self, op: str, affinity: str | None = None, timeout: float | None = None, **kwargs: Any) -> Any:
        if affinity:
            w = self.workers[zlib.crc32(affinity.encode()) % len(self.workers)]
        else:
            free = [w for w in self.workers if not w.lock.locked()]
            w = free[0] if free else min(self.workers, key=lambda x: x.calls)
        return w.call(op, kwargs, timeout or self.timeout)

    def status(self) -> list[dict[str, Any]]:
        return [{"index": w.idx, "pid": w.proc.pid, "alive": w.proc.is_alive(), "busy": w.lock.locked(),
                 "calls": w.calls, "restarts": w.restarts} for w in self.workers]

    def shutdown(self) -> None:
        for w in self.workers:
            try:
                w.conn.send(None)
            except Exception:
                pass
            w.kill()


class InlineKernel:
    """Runs kernel ops in-process. Only for unit tests (no isolation)."""

    timeout = 0.0

    def call(self, op: str, affinity: str | None = None, timeout: float | None = None, **kwargs: Any) -> Any:
        from .kernel import ops
        from .kernel.naming import FeatureError

        try:
            return ops.OPS[op](**kwargs)
        except FeatureError as exc:
            raise KernelError(str(exc), code=exc.code, details=exc.details) from exc

    def status(self) -> list[dict[str, Any]]:
        return [{"index": 0, "pid": os.getpid(), "alive": True, "busy": False, "calls": 0, "restarts": 0, "inline": True}]

    def shutdown(self) -> None:
        pass


_pool: KernelPool | InlineKernel | None = None
_pool_lock = threading.Lock()


def get_pool() -> KernelPool | InlineKernel:
    global _pool
    with _pool_lock:
        if _pool is None:
            s = get_settings()
            if s.kernel_workers <= 0:
                if s.env == "production":
                    raise ServiceUnavailable("Inline kernel mode is not allowed in production")
                _pool = InlineKernel()
            else:
                _pool = KernelPool(s.kernel_workers, s.kernel_memory_mb, s.kernel_timeout_s, allow_test_ops=s.env == "test")
        return _pool


def shutdown_pool() -> None:
    global _pool
    with _pool_lock:
        if _pool is not None:
            _pool.shutdown()
            _pool = None
