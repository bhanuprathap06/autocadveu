"""Operations CLI.

  python -m autoforge.cli migrate            # alembic upgrade head
  python -m autoforge.cli ensure-bucket      # create the S3 bucket if missing (S3-compatible stores)
  python -m autoforge.cli seed               # reference data (materials, licences, standard catalog)
  python -m autoforge.cli seed-demo          # demo account + sample projects (refused in production)
  python -m autoforge.cli create-admin EMAIL # grant administrator role to an existing user
  python -m autoforge.cli verify-user EMAIL  # mark a user's email as verified (operator check done out of band)
  python -m autoforge.cli purge              # apply retention policy (soft-deleted projects, old audit events)
"""

from __future__ import annotations

import os
import sys
from datetime import datetime, timedelta, timezone

from sqlalchemy import delete, select


def migrate() -> None:
    from alembic import command
    from alembic.config import Config

    here = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    cfg = Config(os.path.join(here, "alembic.ini"))
    cfg.set_main_option("script_location", os.path.join(here, "migrations"))
    command.upgrade(cfg, "head")


def ensure_bucket() -> None:
    from .core.config import get_settings
    from .core.storage import get_storage

    if get_settings().storage_backend != "s3":
        print("local storage backend: nothing to do")
        return
    created = get_storage().ensure_bucket()  # type: ignore[attr-defined]
    print("bucket created" if created else "bucket exists")


def seed() -> None:
    from .catalog.seed import seed_reference_data
    from .db.session import session_scope

    with session_scope() as db:
        print(seed_reference_data(db))


def seed_demo() -> None:
    """Non-production demo data. Password comes from AF_DEMO_PASSWORD (required)."""
    from .core.config import get_settings

    s = get_settings()
    if s.env == "production":
        sys.exit("Refusing to create demo accounts in production")
    pw = os.environ.get("AF_DEMO_PASSWORD")
    if not pw or len(pw) < 12:
        sys.exit("Set AF_DEMO_PASSWORD (>= 12 characters) to create the demo account")
    from .catalog.templates import TEMPLATES, build_template
    from .core.security import hash_password
    from .db import models as M
    from .db.session import session_scope
    from .api.documents import create_document_row

    seed()
    with session_scope() as db:
        email = "demo@autoforge.local"
        user = db.scalar(select(M.User).where(M.User.email == email))
        if user is None:
            user = M.User(email=email, display_name="Demo Engineer", password_hash=hash_password(pw),
                          email_verified_at=datetime.now(timezone.utc), preferences={"units": "mm"})
            db.add(user)
            db.flush()
            org = M.Organization(name="Demo workspace", personal=True)
            db.add(org)
            db.flush()
            db.add(M.Membership(org_id=org.id, user_id=user.id, role="owner"))
        org_id = db.scalar(select(M.Membership.org_id).where(M.Membership.user_id == user.id))
        if db.scalar(select(M.Project.id).where(M.Project.created_by == user.id)):
            print("demo data already present")
            return
        p = M.Project(org_id=org_id, name="Front brake rotor study", description="Sample: rotor geometry, drawing and analysis",
                      domain="braking_steering", template="brake_rotor", created_by=user.id)
        db.add(p)
        db.flush()
        db.add(M.ProjectMember(project_id=p.id, user_id=user.id, role="owner"))
        create_document_row(db, user, p, TEMPLATES["brake_rotor"]["name"], "part",
                            build_template("brake_rotor").model_dump(mode="json"), "Demo seed")
        db.add(M.EngineeringProblem(project_id=p.id, title="Lighter front rotor", created_by=user.id, domain="braking_steering",
                                    statement="The front brake rotor must weigh less than 8 kg. The outer diameter must be at most "
                                              "330 mm and the friction ring thickness at least 28 mm. Assume a peak operating "
                                              "temperature of 600 °C. What is the required clamp load?"))
        q = M.Project(org_id=org_id, name="EV module enclosure", description="Sample: battery enclosure and cooling plate",
                      domain="ev_battery", created_by=user.id)
        db.add(q)
        db.flush()
        db.add(M.ProjectMember(project_id=q.id, user_id=user.id, role="owner"))
        for key in ("battery_enclosure", "cooling_plate"):
            create_document_row(db, user, q, TEMPLATES[key]["name"], "part", build_template(key).model_dump(mode="json"), "Demo seed")
        print(f"demo user {email} created with two sample projects")


def create_admin(email: str) -> None:
    from .db import models as M
    from .db.session import session_scope

    with session_scope() as db:
        u = db.scalar(select(M.User).where(M.User.email == email.lower()))
        if u is None:
            sys.exit(f"No user {email}")
        u.is_admin = True
        print(f"{email} is now an administrator")


def verify_user(email: str) -> None:
    from .core import audit
    from .db import models as M
    from .db.session import session_scope

    with session_scope() as db:
        u = db.scalar(select(M.User).where(M.User.email == email.lower()))
        if u is None:
            sys.exit(f"No user {email}")
        if u.email_verified_at is None:
            u.email_verified_at = datetime.now(timezone.utc)
            audit.record(db, "user.email_verified", u, details={"by": "operator-cli"})
        print(f"{email} is verified")


def purge() -> None:
    from .core.config import get_settings
    from .db import models as M
    from .db.session import session_scope

    s = get_settings()
    now = datetime.now(timezone.utc)
    with session_scope() as db:
        old = db.scalars(select(M.Project).where(M.Project.deleted_at < now - timedelta(days=s.project_retention_days))).all()
        from .core.storage import get_storage

        removed = 0
        for p in old:
            removed += get_storage().delete_prefix(f"projects/{p.id}/")  # files, exports, caches, simulation artefacts
            db.delete(p)  # cascades to documents, revisions, jobs, comments
        audit_cut = db.execute(delete(M.AuditEvent).where(M.AuditEvent.at < now - timedelta(days=s.audit_retention_days)))
        db.execute(delete(M.Session).where(M.Session.expires_at < now))
        db.execute(delete(M.AuthToken).where(M.AuthToken.expires_at < now - timedelta(days=7)))
        print(f"purged projects={len(old)} objects={removed} audit_events={audit_cut.rowcount}")
        return [str(p.id) for p in old]


def main() -> None:
    cmd = sys.argv[1] if len(sys.argv) > 1 else ""
    if cmd == "migrate":
        migrate()
    elif cmd == "ensure-bucket":
        ensure_bucket()
    elif cmd == "seed":
        seed()
    elif cmd == "seed-demo":
        seed_demo()
    elif cmd == "create-admin" and len(sys.argv) == 3:
        create_admin(sys.argv[2])
    elif cmd == "verify-user" and len(sys.argv) == 3:
        verify_user(sys.argv[2])
    elif cmd == "purge":
        purge()
    else:
        print(__doc__)
        sys.exit(1)


if __name__ == "__main__":
    main()
