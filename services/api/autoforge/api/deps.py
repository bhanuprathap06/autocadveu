"""FastAPI dependencies: DB session, authentication, CSRF."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from typing import Iterator

from fastapi import Depends, Request
from sqlalchemy import select
from sqlalchemy.orm import Session

from ..core.config import get_settings
from ..core.errors import Forbidden, Unauthorized
from ..core.security import consteq, token_hash
from ..db import models as M
from ..db.session import SessionLocal

SESSION_COOKIE = "af_session"
CSRF_COOKIE = "af_csrf"
CSRF_HEADER = "x-csrf-token"
SAFE_METHODS = {"GET", "HEAD", "OPTIONS"}


def get_db() -> Iterator[Session]:
    db = SessionLocal()
    try:
        yield db
        db.commit()
    except Exception:
        db.rollback()
        raise
    finally:
        db.close()


def _load_session(db: Session, request: Request) -> M.Session | None:
    tok = request.cookies.get(SESSION_COOKIE)
    if not tok or len(tok) > 200:
        return None
    sess = db.scalar(select(M.Session).where(M.Session.token_hash == token_hash(tok)))
    if sess is None:
        return None
    now = datetime.now(timezone.utc)
    if sess.expires_at <= now:
        db.delete(sess)
        return None
    if now - sess.last_seen_at > timedelta(minutes=5):
        sess.last_seen_at = now
    return sess


def check_csrf(request: Request, sess: M.Session) -> None:
    if request.method in SAFE_METHODS:
        return
    header = request.headers.get(CSRF_HEADER, "")
    if not header or not consteq(header, sess.csrf_token):
        raise Forbidden("Missing or invalid CSRF token", code="csrf_failed")


def optional_session(request: Request, db: Session = Depends(get_db)) -> M.Session | None:
    return _load_session(db, request)


def current_session(request: Request, db: Session = Depends(get_db), allow_mfa_pending: bool = False) -> M.Session:
    sess = _load_session(db, request)
    if sess is None:
        raise Unauthorized("Sign in required")
    user = db.get(M.User, sess.user_id)
    if user is None or user.disabled_at is not None:
        raise Unauthorized("Account disabled")
    if user.mfa_enabled and not sess.mfa_passed:
        raise Unauthorized("Multi-factor verification required", code="mfa_required")
    check_csrf(request, sess)
    request.state.user = user
    request.state.session = sess
    return sess


def current_user(request: Request, sess: M.Session = Depends(current_session)) -> M.User:
    return request.state.user


def admin_user(user: M.User = Depends(current_user)) -> M.User:
    if not user.is_admin:
        raise Forbidden("Administrator access required")
    return user


def verified_user(user: M.User = Depends(current_user)) -> M.User:
    if user.email_verified_at is None and get_settings().env in ("staging", "production"):
        raise Forbidden("Verify your email address to continue", code="email_unverified")
    return user


def client_ip(request: Request) -> str:
    if get_settings().trust_proxy_headers:
        fwd = request.headers.get("x-forwarded-for")
        if fwd:
            return fwd.split(",")[0].strip()[:64]
    return (request.client.host if request.client else "unknown")[:64]
