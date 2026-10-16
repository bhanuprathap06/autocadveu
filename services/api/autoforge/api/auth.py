"""Authentication & account management."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from typing import Any

from fastapi import APIRouter, Depends, Request, Response
from pydantic import BaseModel, EmailStr, Field
from sqlalchemy import delete, select
from sqlalchemy.orm import Session

from ..core import audit, mail, ratelimit
from ..core.config import get_settings
from ..core.errors import ApiError, Conflict, NotFound, Unauthorized, Unprocessable
from ..core.security import (
    hash_password,
    needs_rehash,
    new_token,
    new_totp_secret,
    password_problems,
    token_hash,
    totp_uri,
    verify_password,
    verify_totp,
)
from ..db import models as M
from .deps import CSRF_COOKIE, SESSION_COOKIE, check_csrf, client_ip, current_user, get_db, _load_session

router = APIRouter(prefix="/auth", tags=["auth"])
DUMMY_HASH = hash_password("timing-equaliser-not-a-real-password")


class RegisterIn(BaseModel):
    email: EmailStr
    password: str = Field(min_length=1, max_length=256)
    display_name: str = Field(min_length=1, max_length=120)


class LoginIn(BaseModel):
    email: EmailStr
    password: str = Field(max_length=256)


class TokenIn(BaseModel):
    token: str = Field(min_length=10, max_length=200)


class ResetIn(BaseModel):
    token: str = Field(min_length=10, max_length=200)
    password: str = Field(max_length=256)


class EmailIn(BaseModel):
    email: EmailStr


class CodeIn(BaseModel):
    code: str = Field(min_length=6, max_length=8)


class PasswordChangeIn(BaseModel):
    current_password: str = Field(max_length=256)
    new_password: str = Field(max_length=256)


class PreferencesIn(BaseModel):
    preferences: dict[str, Any]


def user_out(u: M.User) -> dict[str, Any]:
    return {
        "id": str(u.id), "email": u.email, "display_name": u.display_name,
        "email_verified": u.email_verified_at is not None, "mfa_enabled": u.mfa_enabled, "is_admin": u.is_admin,
        "preferences": u.preferences or {}, "created_at": u.created_at.isoformat() if u.created_at else None,
    }


def _issue_session(db: Session, response: Response, request: Request, user: M.User, mfa_passed: bool) -> M.Session:
    s = get_settings()
    tok = new_token()
    csrf = new_token(24)
    sess = M.Session(
        token_hash=token_hash(tok), csrf_token=csrf, user_id=user.id, mfa_passed=mfa_passed,
        expires_at=datetime.now(timezone.utc) + timedelta(hours=s.session_ttl_hours),
        ip=client_ip(request), user_agent=(request.headers.get("user-agent") or "")[:300],
    )
    db.add(sess)
    max_age = s.session_ttl_hours * 3600
    response.set_cookie(SESSION_COOKIE, tok, max_age=max_age, httponly=True, secure=s.cookie_secure, samesite="lax", path="/")
    # CSRF token is readable by our own JS (double-submit) and bound to the session server-side.
    response.set_cookie(CSRF_COOKIE, csrf, max_age=max_age, httponly=False, secure=s.cookie_secure, samesite="lax", path="/")
    return sess


def _token(db: Session, user: M.User, kind: str, hours: int) -> str:
    tok = new_token()
    db.add(M.AuthToken(user_id=user.id, kind=kind, token_hash=token_hash(tok),
                       expires_at=datetime.now(timezone.utc) + timedelta(hours=hours)))
    return tok


def _send_verification(db: Session, user: M.User) -> None:
    tok = _token(db, user, "verify_email", 48)
    link = f"{get_settings().public_base_url}/verify-email?token={tok}"
    mail.enqueue(db, user.email, "Verify your AutoForge account",
                 f"Hello {user.display_name},\n\nConfirm your email address: {link}\n\nThis link expires in 48 hours.",
                 "verify_email")


def _consume(db: Session, tok: str, kind: str) -> M.AuthToken:
    row = db.scalar(select(M.AuthToken).where(M.AuthToken.token_hash == token_hash(tok), M.AuthToken.kind == kind))
    if row is None or row.used_at is not None or row.expires_at <= datetime.now(timezone.utc):
        raise ApiError("This link is invalid or has expired. Request a new one.", code="invalid_token")
    row.used_at = datetime.now(timezone.utc)
    return row


@router.post("/register", status_code=201)
def register(body: RegisterIn, request: Request, response: Response, db: Session = Depends(get_db)) -> dict[str, Any]:
    ratelimit.hit(f"register:{client_ip(request)}", 10, 3600)
    email = body.email.lower()
    probs = password_problems(body.password, email)
    if probs:
        raise Unprocessable("; ".join(probs), code="weak_password")
    if db.scalar(select(M.User.id).where(M.User.email.ilike(email))):
        raise Conflict("An account with this email already exists", code="email_taken")
    user = M.User(email=email, display_name=body.display_name.strip(), password_hash=hash_password(body.password),
                  preferences={"units": "mm", "theme": "dark"})
    db.add(user)
    db.flush()
    org = M.Organization(name=f"{user.display_name}'s workspace", personal=True)
    db.add(org)
    db.flush()
    db.add(M.Membership(org_id=org.id, user_id=user.id, role="owner"))
    _send_verification(db, user)
    _issue_session(db, response, request, user, mfa_passed=True)
    audit.record(db, "user.register", user, org_id=org.id, target_type="user", target_id=user.id)
    return {"user": user_out(user)}


@router.post("/login")
def login(body: LoginIn, request: Request, response: Response, db: Session = Depends(get_db)) -> dict[str, Any]:
    email = body.email.lower()
    ratelimit.hit(f"login:ip:{client_ip(request)}", ratelimit.login_limit() * 3, 900)
    ratelimit.hit(f"login:email:{email}", ratelimit.login_limit(), 900)
    user = db.scalar(select(M.User).where(M.User.email.ilike(email)))
    ok = verify_password(user.password_hash if user else DUMMY_HASH, body.password)
    if not user or not ok or user.disabled_at is not None:
        audit.record(db, "user.login_failed", user, details={"email": email})
        db.commit()  # keep the audit record even though the request fails
        raise Unauthorized("Incorrect email or password", code="invalid_credentials")
    if needs_rehash(user.password_hash):
        user.password_hash = hash_password(body.password)
    _issue_session(db, response, request, user, mfa_passed=not user.mfa_enabled)
    audit.record(db, "user.login", user)
    return {"user": user_out(user), "mfa_required": user.mfa_enabled}


@router.post("/mfa/verify")
def mfa_verify(body: CodeIn, request: Request, db: Session = Depends(get_db)) -> dict[str, Any]:
    sess = _load_session(db, request)
    if sess is None:
        raise Unauthorized("Sign in first")
    check_csrf(request, sess)
    ratelimit.hit(f"mfa:{sess.id}", 6, 900)
    user = db.get(M.User, sess.user_id)
    if not user or not user.mfa_enabled or not user.mfa_secret:
        raise ApiError("MFA is not enabled for this account", code="mfa_not_enabled")
    if not verify_totp(user.mfa_secret, body.code):
        audit.record(db, "user.mfa_failed", user)
        db.commit()
        raise Unauthorized("Invalid verification code", code="invalid_mfa_code")
    sess.mfa_passed = True
    audit.record(db, "user.mfa_passed", user)
    return {"user": user_out(user)}


@router.post("/logout")
def logout(request: Request, response: Response, db: Session = Depends(get_db)) -> dict[str, Any]:
    sess = _load_session(db, request)
    if sess is not None:
        check_csrf(request, sess)
        audit.record(db, "user.logout", db.get(M.User, sess.user_id))
        db.delete(sess)
    response.delete_cookie(SESSION_COOKIE, path="/")
    response.delete_cookie(CSRF_COOKIE, path="/")
    return {"ok": True}


@router.get("/me")
def me(user: M.User = Depends(current_user), db: Session = Depends(get_db)) -> dict[str, Any]:
    orgs = db.execute(
        select(M.Organization, M.Membership.role).join(M.Membership, M.Membership.org_id == M.Organization.id)
        .where(M.Membership.user_id == user.id)
    ).all()
    return {"user": user_out(user),
            "organizations": [{"id": str(o.id), "name": o.name, "personal": o.personal, "role": r} for o, r in orgs]}


@router.post("/verify-email")
def verify_email(body: TokenIn, db: Session = Depends(get_db)) -> dict[str, Any]:
    row = _consume(db, body.token, "verify_email")
    user = db.get(M.User, row.user_id)
    user.email_verified_at = datetime.now(timezone.utc)
    audit.record(db, "user.email_verified", user)
    return {"ok": True}


@router.post("/resend-verification")
def resend_verification(user: M.User = Depends(current_user), db: Session = Depends(get_db)) -> dict[str, Any]:
    ratelimit.hit(f"verify:{user.id}", 5, 3600)
    if user.email_verified_at is None:
        _send_verification(db, user)
    return {"ok": True}


@router.post("/forgot-password")
def forgot_password(body: EmailIn, request: Request, db: Session = Depends(get_db)) -> dict[str, Any]:
    ratelimit.hit(f"forgot:{client_ip(request)}", 10, 3600)
    user = db.scalar(select(M.User).where(M.User.email.ilike(body.email.lower())))
    if user and user.disabled_at is None:
        tok = _token(db, user, "reset_password", 1)
        link = f"{get_settings().public_base_url}/reset-password?token={tok}"
        mail.enqueue(db, user.email, "Reset your AutoForge password",
                     f"A password reset was requested. Reset it here (valid 1 hour): {link}\n\n"
                     "If you did not request this, ignore this email.", "reset_password")
        audit.record(db, "user.password_reset_requested", user)
    # Same response whether or not the account exists (no account enumeration).
    return {"ok": True}


@router.post("/reset-password")
def reset_password(body: ResetIn, db: Session = Depends(get_db)) -> dict[str, Any]:
    row = _consume(db, body.token, "reset_password")
    user = db.get(M.User, row.user_id)
    probs = password_problems(body.password, user.email)
    if probs:
        raise Unprocessable("; ".join(probs), code="weak_password")
    user.password_hash = hash_password(body.password)
    db.execute(delete(M.Session).where(M.Session.user_id == user.id))  # sign out everywhere
    audit.record(db, "user.password_reset", user)
    return {"ok": True}


@router.post("/change-password")
def change_password(body: PasswordChangeIn, request: Request, user: M.User = Depends(current_user),
                    db: Session = Depends(get_db)) -> dict[str, Any]:
    if not verify_password(user.password_hash, body.current_password):
        raise Unauthorized("Current password is incorrect", code="invalid_credentials")
    probs = password_problems(body.new_password, user.email)
    if probs:
        raise Unprocessable("; ".join(probs), code="weak_password")
    user.password_hash = hash_password(body.new_password)
    keep = request.state.session.id
    db.execute(delete(M.Session).where(M.Session.user_id == user.id, M.Session.id != keep))
    audit.record(db, "user.password_changed", user)
    return {"ok": True}


@router.post("/mfa/setup")
def mfa_setup(user: M.User = Depends(current_user)) -> dict[str, Any]:
    if user.mfa_enabled:
        raise Conflict("MFA is already enabled")
    user.mfa_secret = new_totp_secret()
    return {"secret": user.mfa_secret, "otpauth_uri": totp_uri(user.mfa_secret, user.email)}


@router.post("/mfa/enable")
def mfa_enable(body: CodeIn, user: M.User = Depends(current_user), db: Session = Depends(get_db)) -> dict[str, Any]:
    if not user.mfa_secret or not verify_totp(user.mfa_secret, body.code):
        raise Unauthorized("Invalid verification code", code="invalid_mfa_code")
    user.mfa_enabled = True
    audit.record(db, "user.mfa_enabled", user)
    return {"user": user_out(user)}


@router.post("/mfa/disable")
def mfa_disable(body: CodeIn, user: M.User = Depends(current_user), db: Session = Depends(get_db)) -> dict[str, Any]:
    if not user.mfa_enabled or not verify_totp(user.mfa_secret or "", body.code):
        raise Unauthorized("Invalid verification code", code="invalid_mfa_code")
    user.mfa_enabled = False
    user.mfa_secret = None
    audit.record(db, "user.mfa_disabled", user)
    return {"user": user_out(user)}


@router.get("/sessions")
def sessions(request: Request, user: M.User = Depends(current_user), db: Session = Depends(get_db)) -> dict[str, Any]:
    rows = db.scalars(select(M.Session).where(M.Session.user_id == user.id).order_by(M.Session.created_at.desc())).all()
    cur = request.state.session.id
    return {"items": [{"id": str(s.id), "created_at": s.created_at.isoformat(), "last_seen_at": s.last_seen_at.isoformat(),
                       "ip": s.ip, "user_agent": s.user_agent, "current": s.id == cur} for s in rows]}


@router.delete("/sessions/{session_id}")
def revoke_session(session_id: str, user: M.User = Depends(current_user), db: Session = Depends(get_db)) -> dict[str, Any]:
    row = db.scalar(select(M.Session).where(M.Session.user_id == user.id, M.Session.id == _uuid(session_id)))
    if row is None:
        raise NotFound("Session not found")
    db.delete(row)
    audit.record(db, "user.session_revoked", user, target_type="session", target_id=session_id)
    return {"ok": True}


@router.put("/preferences")
def set_preferences(body: PreferencesIn, user: M.User = Depends(current_user)) -> dict[str, Any]:
    allowed = {"units", "theme", "reduced_motion", "navigation", "grid", "snap", "show_edges"}
    bad = set(body.preferences) - allowed
    if bad:
        raise Unprocessable(f"Unknown preference keys: {sorted(bad)}")
    user.preferences = {**(user.preferences or {}), **body.preferences}
    return {"user": user_out(user)}


def _uuid(v: str):
    import uuid

    try:
        return uuid.UUID(v)
    except ValueError as exc:
        raise NotFound("Not found") from exc
