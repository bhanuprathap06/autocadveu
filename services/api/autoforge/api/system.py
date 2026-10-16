"""System status: health, readiness, feature status, dev outbox."""

from __future__ import annotations

import shutil
import subprocess
from functools import lru_cache
from typing import Any

from fastapi import APIRouter, Depends
from sqlalchemy import select, text
from sqlalchemy.orm import Session

from ..core.config import get_settings
from ..core.errors import NotFound
from ..core.storage import get_storage
from ..db import models as M
from ..db.session import get_engine
from .deps import get_db

router = APIRouter(tags=["system"])


@lru_cache
def ccx_version() -> str | None:
    exe = shutil.which(get_settings().ccx_binary)
    if not exe:
        return None
    try:
        out = subprocess.run([exe, "-v"], capture_output=True, text=True, timeout=10).stdout
        return out.strip().splitlines()[-1] if out.strip() else "unknown"
    except Exception:
        return None


def readiness() -> tuple[bool, dict[str, Any]]:
    checks: dict[str, Any] = {}
    try:
        with get_engine().connect() as c:
            c.execute(text("select 1"))
            rev = c.execute(text("select version_num from alembic_version")).scalar()
        checks["database"] = {"ok": True, "migration": rev}
    except Exception as exc:
        checks["database"] = {"ok": False, "error": type(exc).__name__}
    try:
        checks["storage"] = {"ok": bool(get_storage().ping()), "backend": get_settings().storage_backend}
    except Exception as exc:
        checks["storage"] = {"ok": False, "error": type(exc).__name__}
    if get_settings().redis_url:
        try:
            from ..core.events import _redis_client

            checks["redis"] = {"ok": bool(_redis_client().ping())}
        except Exception as exc:
            checks["redis"] = {"ok": False, "error": type(exc).__name__}
    try:
        from ..kernel_pool import get_pool

        st = get_pool().status()
        checks["kernel"] = {"ok": all(w["alive"] for w in st), "workers": st}
    except Exception as exc:
        checks["kernel"] = {"ok": False, "error": str(exc)}
    ok = all(c.get("ok") for c in checks.values())
    return ok, checks


@router.get("/status")
def status() -> dict[str, Any]:
    from ..kernel.occ import kernel_version

    s = get_settings()
    return {
        "env": s.env,
        "kernel": kernel_version(),
        "solvers": {"calculix": ccx_version()},
        "ai": {"provider": s.ai_provider, "configured": s.ai_provider != "none" and bool(s.anthropic_api_key)},
        "smtp_configured": bool(s.smtp_host),
        "malware_scanning": "external integration required (not configured)",
    }


@router.get("/dev/outbox")
def dev_outbox(to: str | None = None, db: Session = Depends(get_db)) -> dict[str, Any]:
    """Development/test only: lets local users and E2E tests read verification emails."""
    if not get_settings().is_dev:
        raise NotFound("Not found")
    stmt = select(M.EmailOutbox).order_by(M.EmailOutbox.created_at.desc()).limit(50)
    if to:
        stmt = stmt.where(M.EmailOutbox.to_email == to.lower())
    return {"items": [{"to": e.to_email, "subject": e.subject, "body": e.body, "kind": e.kind,
                       "created_at": e.created_at.isoformat()} for e in db.scalars(stmt)]}
