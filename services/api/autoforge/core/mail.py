"""Transactional email via an outbox table.

Messages are inserted in the same DB transaction as the action that triggers
them; the worker delivers them over SMTP, or over the Resend HTTPS API where
outbound SMTP is blocked. Without either configured (dev), they remain in the
outbox and are visible in the admin console / dev endpoint.
"""

from __future__ import annotations

import json
import smtplib
import ssl
import urllib.error
import urllib.request
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
    if not s.mail_configured:
        return 0
    rows = db.scalars(
        select(M.EmailOutbox).where(M.EmailOutbox.sent_at.is_(None), M.EmailOutbox.attempts < 5)
        .order_by(M.EmailOutbox.created_at).limit(limit).with_for_update(skip_locked=True)
    ).all()
    sent = 0
    for row in rows:
        row.attempts += 1
        try:
            if s.resend_api_key:
                _send_resend(s.resend_api_key, s.smtp_from, row.to_email, row.subject, row.body)
            else:
                _send_smtp(row.to_email, row.subject, row.body)
            row.sent_at = datetime.now(timezone.utc)
            sent += 1
        except Exception as exc:
            row.last_error = str(exc)[:1000]
    return sent


def _send_smtp(to: str, subject: str, body: str) -> None:
    s = get_settings()
    msg = EmailMessage()
    msg["From"] = s.smtp_from
    msg["To"] = to
    msg["Subject"] = subject
    msg.set_content(body)
    with smtplib.SMTP(s.smtp_host, s.smtp_port, timeout=15) as smtp:  # type: ignore[arg-type]
        if s.smtp_starttls:
            smtp.starttls(context=ssl.create_default_context())
        if s.smtp_user:
            smtp.login(s.smtp_user, s.smtp_password or "")
        smtp.send_message(msg)


RESEND_URL = "https://api.resend.com/emails"


def _send_resend(api_key: str, sender: str, to: str, subject: str, body: str) -> None:
    payload = json.dumps({"from": sender, "to": [to], "subject": subject, "text": body}).encode()
    req = urllib.request.Request(RESEND_URL, data=payload, method="POST", headers={
        "Authorization": f"Bearer {api_key}", "Content-Type": "application/json", "User-Agent": "AutoForge"})
    try:
        with urllib.request.urlopen(req, timeout=15, context=ssl.create_default_context()) as resp:
            if resp.status >= 300:
                raise RuntimeError(f"Resend returned HTTP {resp.status}")
    except urllib.error.HTTPError as exc:
        # Resend explains rejections (e.g. unverified sender domain) in the body; keep it for the outbox record.
        raise RuntimeError(f"Resend returned HTTP {exc.code}: {exc.read()[:300].decode(errors='replace')}") from None
