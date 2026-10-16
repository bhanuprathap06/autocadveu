"""WebSocket channel for presence, camera/selection sharing and live notifications.

Security: the session cookie authenticates the socket; the Origin header must
be an allowed origin (prevents cross-site WebSocket hijacking); project access
is re-checked server-side. Messages are size- and rate-limited.

Durability: presence, cameras and selections are ephemeral (Redis with TTL or
process memory). Durable changes (revisions, comments, jobs) are only ever
announced here; clients refetch them through the REST API.
"""

from __future__ import annotations

import asyncio
import json
import time
import uuid
from typing import Any

from fastapi import APIRouter, WebSocket, WebSocketDisconnect
from starlette.concurrency import run_in_threadpool

from ..core import events
from ..core.authz import require_project
from ..core.config import get_settings
from ..core.errors import ApiError
from ..core.security import token_hash
from ..db import models as M
from ..db.session import SessionLocal
from .deps import SESSION_COOKIE

router = APIRouter(tags=["realtime"])
PRESENCE_TTL = 60
MAX_MSG = 8192
_presence: dict[str, dict[str, dict[str, Any]]] = {}


def _authorize(token: str | None, project_id: str) -> dict[str, Any] | None:
    if not token:
        return None
    db = SessionLocal()
    try:
        from datetime import datetime, timezone

        from sqlalchemy import select

        sess = db.scalar(select(M.Session).where(M.Session.token_hash == token_hash(token)))
        if sess is None or sess.expires_at <= datetime.now(timezone.utc):
            return None
        user = db.get(M.User, sess.user_id)
        if user is None or user.disabled_at is not None or (user.mfa_enabled and not sess.mfa_passed):
            return None
        try:
            project, role = require_project(db, user, project_id)
        except ApiError:
            return None
        return {"user_id": str(user.id), "name": user.display_name, "project_id": str(project.id), "role": role}
    finally:
        db.close()


def _presence_set(pid: str, key: str, value: dict[str, Any] | None) -> None:
    r = events._redis_client()
    if r is not None:
        try:
            if value is None:
                r.hdel(f"af:presence:{pid}", key)
            else:
                r.hset(f"af:presence:{pid}", key, json.dumps(value))
                r.expire(f"af:presence:{pid}", PRESENCE_TTL * 10)
            return
        except Exception:
            pass
    bucket = _presence.setdefault(pid, {})
    if value is None:
        bucket.pop(key, None)
    else:
        bucket[key] = value


def _presence_all(pid: str) -> list[dict[str, Any]]:
    now = time.time()
    r = events._redis_client()
    vals: list[dict[str, Any]] = []
    if r is not None:
        try:
            vals = [json.loads(v) for v in r.hgetall(f"af:presence:{pid}").values()]
        except Exception:
            vals = list(_presence.get(pid, {}).values())
    else:
        vals = list(_presence.get(pid, {}).values())
    return [v for v in vals if now - v.get("ts", 0) < PRESENCE_TTL]


@router.websocket("/ws")
async def ws(websocket: WebSocket) -> None:
    s = get_settings()
    origin = websocket.headers.get("origin")
    if origin and origin not in s.allowed_origins:
        await websocket.close(code=4403, reason="origin not allowed")
        return
    project_id = websocket.query_params.get("project_id", "")
    try:
        uuid.UUID(project_id)
    except ValueError:
        await websocket.close(code=4400, reason="project_id required")
        return
    ident = await run_in_threadpool(_authorize, websocket.cookies.get(SESSION_COOKIE), project_id)
    if ident is None:
        await websocket.close(code=4401, reason="unauthorized")
        return
    await websocket.accept()
    conn = uuid.uuid4().hex[:12]
    key = f"{ident['user_id']}:{conn}"
    channel = f"project:{project_id}"
    me = {"user_id": ident["user_id"], "name": ident["name"], "role": ident["role"], "conn": conn, "ts": time.time(),
          "document_id": None, "camera": None, "selection": []}
    _presence_set(project_id, key, me)
    events.publish(channel, {"type": "presence.join", "presence": me})
    await websocket.send_json({"type": "hello", "you": me, "presence": _presence_all(project_id)})
    window, count = time.time(), 0

    async with events.Subscription([channel]) as sub:
        async def pump() -> None:
            while True:
                msg = await sub.queue.get()
                try:
                    data = json.loads(msg)
                except json.JSONDecodeError:
                    continue
                if data.get("type", "").startswith("presence.") and data.get("presence", {}).get("conn") == conn:
                    continue  # don't echo our own presence
                await websocket.send_text(msg)

        task = asyncio.create_task(pump())
        try:
            while True:
                raw = await asyncio.wait_for(websocket.receive_text(), timeout=PRESENCE_TTL)
                if len(raw) > MAX_MSG:
                    await websocket.close(code=4413, reason="message too large")
                    break
                now = time.time()
                if now - window > 1:
                    window, count = now, 0
                count += 1
                if count > 30:
                    continue  # drop excess ephemeral updates
                try:
                    msg = json.loads(raw)
                except json.JSONDecodeError:
                    continue
                t = msg.get("type")
                if t == "ping":
                    me["ts"] = now
                    _presence_set(project_id, key, me)
                    await websocket.send_json({"type": "pong"})
                elif t == "presence":
                    doc = msg.get("document_id")
                    me.update({"ts": now, "document_id": str(doc)[:64] if doc else None,
                               "camera": _clean_camera(msg.get("camera")),
                               "selection": [str(x)[:200] for x in (msg.get("selection") or [])][:50]})
                    _presence_set(project_id, key, me)
                    events.publish(channel, {"type": "presence.update", "presence": me})
        except (WebSocketDisconnect, asyncio.TimeoutError, RuntimeError):
            pass
        finally:
            task.cancel()
            _presence_set(project_id, key, None)
            events.publish(channel, {"type": "presence.leave", "presence": {"conn": conn, "user_id": ident["user_id"]}})


def _clean_camera(c: Any) -> dict[str, Any] | None:
    if not isinstance(c, dict):
        return None
    out = {}
    for k in ("position", "target", "up"):
        v = c.get(k)
        if isinstance(v, list) and len(v) == 3 and all(isinstance(x, (int, float)) for x in v):
            out[k] = [float(x) for x in v]
    if isinstance(c.get("zoom"), (int, float)):
        out["zoom"] = float(c["zoom"])
    return out or None
