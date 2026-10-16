"""Administrator console: health, jobs, users, outbox, catalog moderation, audit."""

from __future__ import annotations

import uuid
from datetime import datetime, timedelta, timezone
from typing import Any

from fastapi import APIRouter, Depends, Query
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from ..core import audit
from ..core.errors import NotFound
from ..db import models as M
from ..jobs import queue
from .deps import admin_user, get_db
from .system import readiness

router = APIRouter(prefix="/admin", tags=["admin"])


@router.get("/overview")
def overview(user: M.User = Depends(admin_user), db: Session = Depends(get_db)) -> dict[str, Any]:
    ok, checks = readiness()
    day = datetime.now(timezone.utc) - timedelta(days=1)
    jobs = dict(db.execute(select(M.Job.status, func.count()).group_by(M.Job.status)).all())
    return {
        "ready": ok, "checks": checks,
        "counts": {"users": db.scalar(select(func.count()).select_from(M.User)),
                   "projects": db.scalar(select(func.count()).where(M.Project.deleted_at.is_(None))),
                   "documents": db.scalar(select(func.count()).where(M.CADDocument.deleted_at.is_(None))),
                   "catalog_items": db.scalar(select(func.count()).select_from(M.ComponentCatalogItem)),
                   "jobs_by_status": jobs,
                   "failed_jobs_24h": db.scalar(select(func.count()).where(M.Job.status.in_(["failed", "timed_out"]),
                                                                         M.Job.finished_at >= day)),
                   "unsent_emails": db.scalar(select(func.count()).where(M.EmailOutbox.sent_at.is_(None)))},
    }


@router.get("/jobs")
def jobs(status: str | None = None, limit: int = Query(100, le=500), user: M.User = Depends(admin_user),
         db: Session = Depends(get_db)) -> dict[str, Any]:
    stmt = select(M.Job).order_by(M.Job.created_at.desc()).limit(limit)
    if status:
        stmt = stmt.where(M.Job.status == status)
    return {"items": [{"id": str(j.id), "kind": j.kind, "status": j.status, "attempts": j.attempts, "error": j.error,
                       "project_id": str(j.project_id) if j.project_id else None, "locked_by": j.locked_by,
                       "created_at": j.created_at.isoformat(), "started_at": j.started_at.isoformat() if j.started_at else None,
                       "finished_at": j.finished_at.isoformat() if j.finished_at else None, "progress": j.progress}
                      for j in db.scalars(stmt)]}


@router.post("/jobs/{job_id}/cancel")
def cancel_job(job_id: str, user: M.User = Depends(admin_user), db: Session = Depends(get_db)) -> dict[str, Any]:
    try:
        j = db.get(M.Job, uuid.UUID(job_id))
    except ValueError:
        j = None
    if j is None:
        raise NotFound("Job not found")
    st = queue.request_cancel(db, j)
    audit.record(db, "admin.job_cancel", user, target_type="job", target_id=j.id)
    return {"status": st}


@router.get("/users")
def users(q: str | None = None, user: M.User = Depends(admin_user), db: Session = Depends(get_db)) -> dict[str, Any]:
    stmt = select(M.User).order_by(M.User.created_at.desc()).limit(200)
    if q:
        stmt = stmt.where(M.User.email.ilike(f"%{q}%"))
    return {"items": [{"id": str(u.id), "email": u.email, "display_name": u.display_name, "verified": bool(u.email_verified_at),
                       "mfa": u.mfa_enabled, "admin": u.is_admin, "disabled": bool(u.disabled_at),
                       "created_at": u.created_at.isoformat()} for u in db.scalars(stmt)]}


@router.post("/users/{user_id}/disable")
def disable_user(user_id: str, user: M.User = Depends(admin_user), db: Session = Depends(get_db)) -> dict[str, Any]:
    try:
        target = db.get(M.User, uuid.UUID(user_id))
    except ValueError:
        target = None
    if target is None or target.id == user.id:
        raise NotFound("User not found")
    target.disabled_at = datetime.now(timezone.utc)
    db.query(M.Session).filter(M.Session.user_id == target.id).delete()
    audit.record(db, "admin.user_disable", user, target_type="user", target_id=target.id)
    return {"ok": True}


@router.get("/outbox")
def outbox(user: M.User = Depends(admin_user), db: Session = Depends(get_db)) -> dict[str, Any]:
    rows = db.scalars(select(M.EmailOutbox).order_by(M.EmailOutbox.created_at.desc()).limit(100))
    return {"items": [{"to": e.to_email, "subject": e.subject, "kind": e.kind, "sent": bool(e.sent_at), "attempts": e.attempts,
                       "error": e.last_error, "created_at": e.created_at.isoformat()} for e in rows]}


@router.get("/catalog/review")
def catalog_review(user: M.User = Depends(admin_user), db: Session = Depends(get_db)) -> dict[str, Any]:
    """Records needing attention: low completeness, missing source on externally sourced classes, org uploads."""
    C = M.ComponentCatalogItem
    rows = db.scalars(select(C).where((C.completeness < 0.5) | (C.asset_class == "user_upload") |
                                      ((C.asset_class.in_(["manufacturer", "verified_public"])) & C.source_url.is_(None)))
                      .order_by(C.updated_at.desc()).limit(200))
    return {"items": [{"id": str(i.id), "name": i.name, "asset_class": i.asset_class, "completeness": i.completeness,
                       "verification_status": i.verification_status, "org_scoped": i.org_id is not None} for i in rows]}


@router.get("/audit")
def audit_search(action: str | None = None, limit: int = Query(200, le=1000), user: M.User = Depends(admin_user),
                 db: Session = Depends(get_db)) -> dict[str, Any]:
    stmt = select(M.AuditEvent).order_by(M.AuditEvent.at.desc()).limit(limit)
    if action:
        stmt = stmt.where(M.AuditEvent.action.like(f"{action}%"))
    return {"items": [{"at": e.at.isoformat(), "actor_id": str(e.actor_id) if e.actor_id else None, "action": e.action,
                       "project_id": str(e.project_id) if e.project_id else None, "target": f"{e.target_type}:{e.target_id}",
                       "ip": e.ip, "request_id": e.request_id, "details": e.details} for e in db.scalars(stmt)]}
