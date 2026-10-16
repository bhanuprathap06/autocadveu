"""Durable job queue on PostgreSQL.

* Enqueue happens in the same transaction as the domain row that needs the job
  (transactional-outbox guarantee: no job without its record and vice versa).
* Workers claim with ``SELECT ... FOR UPDATE SKIP LOCKED`` so any number of
  workers can poll safely.
* Running jobs heartbeat; a job whose heartbeat is stale (worker crash, node
  loss) is re-queued if attempts remain, otherwise failed with a clear reason.
* States: queued, running, succeeded, failed, cancelled, timed_out.
"""

from __future__ import annotations

import uuid
from datetime import datetime, timedelta, timezone
from typing import Any

from sqlalchemy import select, update
from sqlalchemy.orm import Session

from ..db import models as M

TERMINAL = {"succeeded", "failed", "cancelled", "timed_out"}


def now() -> datetime:
    return datetime.now(timezone.utc)


def enqueue(db: Session, kind: str, payload: dict[str, Any], *, project_id: uuid.UUID | None = None,
            user_id: uuid.UUID | None = None, idempotency_key: str | None = None, timeout_s: int = 900,
            max_attempts: int = 1, priority: int = 100) -> tuple[M.Job, bool]:
    if idempotency_key:
        existing = db.scalar(select(M.Job).where(M.Job.idempotency_key == idempotency_key))
        if existing is not None:
            return existing, True
    job = M.Job(kind=kind, payload=payload, project_id=project_id, created_by=user_id, idempotency_key=idempotency_key,
                timeout_s=timeout_s, max_attempts=max_attempts, priority=priority)
    db.add(job)
    db.flush()
    return job, False


def claim(db: Session, worker_id: str, kinds: list[str] | None = None) -> M.Job | None:
    stmt = (select(M.Job).where(M.Job.status == "queued", M.Job.run_after <= now())
            .order_by(M.Job.priority, M.Job.created_at).limit(1).with_for_update(skip_locked=True))
    if kinds:
        stmt = stmt.where(M.Job.kind.in_(kinds))
    job = db.scalar(stmt)
    if job is None:
        return None
    t = now()
    job.status, job.locked_by, job.locked_at, job.heartbeat_at, job.started_at = "running", worker_id, t, t, t
    job.attempts += 1
    db.flush()
    return job


def heartbeat(db: Session, job_id: uuid.UUID, progress: dict[str, Any] | None = None) -> tuple[bool, str]:
    """Returns (cancel_requested, status)."""
    vals: dict[str, Any] = {"heartbeat_at": now()}
    if progress is not None:
        vals["progress"] = progress
    db.execute(update(M.Job).where(M.Job.id == job_id, M.Job.status == "running").values(**vals))
    row = db.execute(select(M.Job.cancel_requested, M.Job.status).where(M.Job.id == job_id)).one()
    return bool(row[0]), row[1]


def finish(db: Session, job: M.Job, status: str, result: dict[str, Any] | None = None, error: str | None = None) -> None:
    assert status in TERMINAL
    job.status = status
    job.result = result
    job.error = error
    job.finished_at = now()
    job.locked_by = None


def request_cancel(db: Session, job: M.Job) -> str:
    if job.status in TERMINAL:
        return job.status
    if job.status == "queued":
        finish(db, job, "cancelled", error="Cancelled before start")
        return "cancelled"
    job.cancel_requested = True
    return "cancelling"


def recover_stale(db: Session, stale_after_s: float = 60) -> list[uuid.UUID]:
    cutoff = now() - timedelta(seconds=stale_after_s)
    rows = db.scalars(select(M.Job).where(M.Job.status == "running", M.Job.heartbeat_at < cutoff)
                      .with_for_update(skip_locked=True)).all()
    out = []
    for j in rows:
        if j.cancel_requested:
            finish(db, j, "cancelled", error="Cancelled (worker lost while cancelling)")
        elif j.attempts < j.max_attempts:
            j.status, j.locked_by, j.run_after = "queued", None, now()
            j.error = f"Re-queued after worker loss (attempt {j.attempts})"
        else:
            finish(db, j, "failed", error="Worker stopped responding (crash or node loss); no attempts left. Resubmit the job.")
        out.append(j.id)
    return out
