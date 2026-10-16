"""Audit trail for sensitive and consequential actions."""

from __future__ import annotations

import uuid
from typing import Any

from sqlalchemy.orm import Session

from ..db import models as M
from .context import current_ip, current_request_id


def record(
    db: Session,
    action: str,
    actor: M.User | None,
    *,
    project_id: uuid.UUID | None = None,
    org_id: uuid.UUID | None = None,
    room_id: uuid.UUID | None = None,
    target_type: str | None = None,
    target_id: Any = None,
    details: dict[str, Any] | None = None,
) -> None:
    db.add(
        M.AuditEvent(
            actor_id=actor.id if actor else None,
            action=action,
            project_id=project_id,
            org_id=org_id,
            room_id=room_id,
            target_type=target_type,
            target_id=str(target_id) if target_id is not None else None,
            ip=current_ip(),
            request_id=current_request_id(),
            details=details or {},
        )
    )
