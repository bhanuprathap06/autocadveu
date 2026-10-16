"""Event bus for realtime notifications (WebSocket fan-out).

With Redis configured, events are published on Redis pub/sub so API replicas
and background workers share one bus. Without Redis (single-process dev/test)
an in-process hub is used. Events are *notifications*; the database remains
the source of truth (clients refetch durable state on receipt).
"""

from __future__ import annotations

import asyncio
import json
import logging
import threading
from collections import defaultdict
from typing import Any

from .config import get_settings

log = logging.getLogger("autoforge.events")
_local: dict[str, set[asyncio.Queue]] = defaultdict(set)
_loop: asyncio.AbstractEventLoop | None = None
_redis = None
_lock = threading.Lock()


def _redis_client():
    global _redis
    url = get_settings().redis_url
    if not url:
        return None
    if _redis is None:
        import redis

        _redis = redis.Redis.from_url(url, socket_timeout=2, socket_connect_timeout=2)
    return _redis


def publish(channel: str, event: dict[str, Any]) -> None:
    payload = json.dumps({"channel": channel, **event}, default=str)
    r = _redis_client()
    if r is not None:
        try:
            r.publish(f"af:{channel}", payload)
            return
        except Exception as exc:  # degrade to local delivery, never fail the request
            log.warning("redis publish failed: %s", exc)
    _deliver_local(channel, payload)


def _deliver_local(channel: str, payload: str) -> None:
    with _lock:
        queues = list(_local.get(channel, ()))
    for q in queues:
        if _loop is not None and _loop.is_running():
            _loop.call_soon_threadsafe(_safe_put, q, payload)


def _safe_put(q: asyncio.Queue, payload: str) -> None:
    try:
        q.put_nowait(payload)
    except asyncio.QueueFull:
        pass  # slow consumer: drop ephemeral event; durable state is refetched


class Subscription:
    def __init__(self, channels: list[str]):
        self.channels = channels
        self.queue: asyncio.Queue = asyncio.Queue(maxsize=500)
        self._task: asyncio.Task | None = None
        self._pubsub = None

    async def __aenter__(self) -> "Subscription":
        global _loop
        _loop = asyncio.get_running_loop()
        r = _redis_client()
        if r is not None:
            import redis.asyncio as aioredis

            self._ar = aioredis.Redis.from_url(get_settings().redis_url)
            self._pubsub = self._ar.pubsub()
            await self._pubsub.subscribe(*[f"af:{c}" for c in self.channels])
            self._task = asyncio.create_task(self._pump())
        with _lock:
            for c in self.channels:
                _local[c].add(self.queue)
        return self

    async def _pump(self) -> None:
        assert self._pubsub is not None
        async for msg in self._pubsub.listen():
            if msg.get("type") == "message":
                data = msg["data"]
                _safe_put(self.queue, data.decode() if isinstance(data, bytes) else data)

    async def __aexit__(self, *exc: Any) -> None:
        with _lock:
            for c in self.channels:
                _local[c].discard(self.queue)
        if self._task:
            self._task.cancel()
        if self._pubsub is not None:
            try:
                await self._pubsub.unsubscribe()
                await self._pubsub.aclose()
                await self._ar.aclose()
            except Exception:
                pass
