"""Transactional email via an outbox table.

Messages are inserted in the same DB transaction as the action that triggers
them; the worker delivers them over SMTP. Without SMTP configured (dev), they
remain in the outbox and are visible in the admin console / dev endpoint.
"""

from __future__ import annotations

import smtplib
from datetime import datetime, timezone
from email.message import EmailMessage

from sqlalchemy import select
from sqlalchemy.orm import Session

from ..db import models as M
from .config import get_settings


def enqueue(db: Session, to: str, subject: str, body: str, kind: str) -> None:
    db.add(M.EmailOutbox(to_email=to, subject=subject, body=body, kind=kind))


def deliver_pending(db: Session, limit: int = 20) -> int:
    s = get_settings()
    if not s.smtp_host:
        return 0
    rows = db.scalars(
        select(M.EmailOutbox).where(M.EmailOutbox.sent_at.is_(None), M.EmailOutbox.attempts < 5)
        .order_by(M.EmailOutbox.created_at).limit(limit).with_for_update(skip_locked=True)
    ).all()
    sent = 0
    for row in rows:
        row.attempts += 1
        try:
            msg = EmailMessage()
            msg["From"] = s.smtp_from
            msg["To"] = row.to_email
            msg["Subject"] = row.subject
            msg.set_content(row.body)
            with smtplib.SMTP(s.smtp_host, s.smtp_port, timeout=15) as smtp:
                smtp.starttls()
                if s.smtp_user:
                    smtp.login(s.smtp_user, s.smtp_password or "")
                smtp.send_message(msg)
            row.sent_at = datetime.now(timezone.utc)
            sent += 1
        except Exception as exc:
            row.last_error = str(exc)[:1000]
    return sent
