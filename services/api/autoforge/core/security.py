"""Password hashing, opaque tokens, TOTP."""

from __future__ import annotations

import hashlib
import hmac
import secrets

import pyotp
from argon2 import PasswordHasher
from argon2.exceptions import InvalidHashError, VerificationError, VerifyMismatchError

_ph = PasswordHasher()  # argon2id with library defaults (RFC 9106 low-memory profile)

MIN_PASSWORD_LEN = 10
COMMON_PASSWORDS = {"password", "password1", "password123", "1234567890", "qwertyuiop", "letmein123", "autoforge123"}


def hash_password(pw: str) -> str:
    return _ph.hash(pw)


def verify_password(hash_: str, pw: str) -> bool:
    try:
        return _ph.verify(hash_, pw)
    except (VerifyMismatchError, VerificationError, InvalidHashError):
        return False


def needs_rehash(hash_: str) -> bool:
    return _ph.check_needs_rehash(hash_)


def password_problems(pw: str, email: str = "") -> list[str]:
    probs = []
    if len(pw) < MIN_PASSWORD_LEN:
        probs.append(f"Password must be at least {MIN_PASSWORD_LEN} characters")
    if len(pw) > 256:
        probs.append("Password is too long")
    if pw.lower() in COMMON_PASSWORDS:
        probs.append("Password is too common")
    if email and email.split("@")[0].lower() in pw.lower() and len(email.split("@")[0]) >= 4:
        probs.append("Password must not contain your email name")
    return probs


def new_token(nbytes: int = 32) -> str:
    return secrets.token_urlsafe(nbytes)


def token_hash(token: str) -> str:
    return hashlib.sha256(token.encode()).hexdigest()


def consteq(a: str, b: str) -> bool:
    return hmac.compare_digest(a.encode(), b.encode())


def new_totp_secret() -> str:
    return pyotp.random_base32()


def totp_uri(secret: str, email: str) -> str:
    return pyotp.TOTP(secret).provisioning_uri(name=email, issuer_name="AutoForge Engineering Studio")


def verify_totp(secret: str, code: str) -> bool:
    code = (code or "").strip().replace(" ", "")
    if not code.isdigit() or len(code) != 6:
        return False
    return pyotp.TOTP(secret).verify(code, valid_window=1)
