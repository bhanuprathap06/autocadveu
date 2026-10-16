"""Audit log access (project owners, room owners, self, administrators)."""

from __future__ import annotations

from datetime import datetime
from typing import Any

from fastapi import APIRouter, Depends, Query
from sqlalchemy import select
from sqlalchemy.orm import Session

from ..core.authz import require_project, require_room
from ..db import models as M
from .deps import current_user, get_db

router = APIRouter(tags=["audit"])


def event_out(e: M.AuditEvent, actor: str | None = None) -> dict[str, Any]:
    return {"id": str(e.id), "at": e.at.isoformat(), "actor_id": str(e.actor_id) if e.actor_id else None, "actor": actor,
            "action": e.action, "target_type": e.target_type, "target_id": e.target_id, "project_id": str(e.project_id) if e.project_id else None,
            "request_id": e.request_id, "details": e.details}


def _page(db: Session, stmt, limit: int, before: datetime | None) -> dict[str, Any]:
    if before:
        stmt = stmt.where(M.AuditEvent.at < before)
    rows = db.execute(stmt.order_by(M.AuditEvent.at.desc()).limit(limit + 1)).all()
    items = [event_out(e, a) for e, a in rows[:limit]]
    return {"items": items, "next_before": items[-1]["at"] if len(rows) > limit else None}


def _base():
    return select(M.AuditEvent, M.User.display_name).outerjoin(M.User, M.User.id == M.AuditEvent.actor_id)


@router.get("/projects/{project_id}/audit")
def project_audit(project_id: str, limit: int = Query(100, ge=1, le=500), before: datetime | None = None,
                  user: M.User = Depends(current_user), db: Session = Depends(get_db)) -> dict[str, Any]:
    p, _ = require_project(db, user, project_id, "manage")
    return _page(db, _base().where(M.AuditEvent.project_id == p.id), limit, before)


@router.get("/rooms/{room_id}/audit")
def room_audit(room_id: str, limit: int = Query(100, ge=1, le=500), before: datetime | None = None,
               user: M.User = Depends(current_user), db: Session = Depends(get_db)) -> dict[str, Any]:
    room, _ = require_room(db, user, room_id, "owner")
    return _page(db, _base().where(M.AuditEvent.room_id == room.id), limit, before)


@router.get("/me/audit")
def my_audit(limit: int = Query(100, ge=1, le=500), before: datetime | None = None, user: M.User = Depends(current_user),
             db: Session = Depends(get_db)) -> dict[str, Any]:
    return _page(db, _base().where(M.AuditEvent.actor_id == user.id), limit, before)
