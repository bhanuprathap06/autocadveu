"""Fixed-window rate limiting (Redis when configured, in-memory otherwise)."""

from __future__ import annotations

import threading
import time

from .config import get_settings
from .errors import RateLimited
from .events import _redis_client

_mem: dict[str, tuple[int, float]] = {}
_lock = threading.Lock()


def hit(bucket: str, limit: int, window_s: int) -> int:
    """Count a hit; raise RateLimited when over the limit. Returns current count."""
    key = f"af:rl:{bucket}:{int(time.time() // window_s)}"
    r = _redis_client()
    count: int
    if r is not None:
        try:
            pipe = r.pipeline()
            pipe.incr(key)
            pipe.expire(key, window_s + 5)
            count = int(pipe.execute()[0])
        except Exception:
            count = _mem_hit(key, window_s)
    else:
        count = _mem_hit(key, window_s)
    if count > limit:
        raise RateLimited(f"Too many requests. Try again in up to {window_s // 60 or 1} minute(s).",
                          details={"limit": limit, "window_s": window_s})
    return count


def _mem_hit(key: str, window_s: int) -> int:
    now = time.time()
    with _lock:
        for k in [k for k, (_, exp) in _mem.items() if exp < now]:
            _mem.pop(k, None)
        c, exp = _mem.get(key, (0, now + window_s))
        _mem[key] = (c + 1, exp)
        return c + 1


def reset() -> None:
    """Clear all counters (in-memory and Redis). Used by tests and operational resets."""
    with _lock:
        _mem.clear()
    r = _redis_client()
    if r is not None:
        try:
            keys = list(r.scan_iter(match="af:rl:*", count=1000))
            if keys:
                r.delete(*keys)
        except Exception:
            pass


def login_limit() -> int:
    return get_settings().login_rate_limit_per_15min
