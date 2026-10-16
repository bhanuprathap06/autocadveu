"""Projects, membership, templates."""

from __future__ import annotations

import uuid
from datetime import datetime, timezone
from typing import Any, Literal

from fastapi import APIRouter, Depends, Query
from pydantic import BaseModel, EmailStr, Field
from sqlalchemy import func, or_, select
from sqlalchemy.orm import Session

from ..catalog.templates import DOMAINS, TEMPLATES, build_template, list_templates
from ..core import audit
from ..core.authz import require_project
from ..core.errors import Conflict, Forbidden, NotFound, Unprocessable
from ..db import models as M
from .deps import current_user, get_db
from .documents import create_document_row

router = APIRouter(tags=["projects"])


class ProjectIn(BaseModel):
    name: str = Field(min_length=1, max_length=160)
    description: str = Field(default="", max_length=5000)
    domain: str | None = Field(default=None, max_length=64)
    org_id: uuid.UUID | None = None
    template: str | None = None


class ProjectPatch(BaseModel):
    name: str | None = Field(default=None, min_length=1, max_length=160)
    description: str | None = Field(default=None, max_length=5000)
    domain: str | None = Field(default=None, max_length=64)


class MemberIn(BaseModel):
    email: EmailStr
    role: Literal["owner", "editor", "reviewer", "viewer"]


def project_out(p: M.Project, role: str | None = None, extra: dict[str, Any] | None = None) -> dict[str, Any]:
    return {"id": str(p.id), "org_id": str(p.org_id), "name": p.name, "description": p.description, "domain": p.domain,
            "template": p.template, "created_at": p.created_at.isoformat(), "updated_at": p.updated_at.isoformat(),
            "role": role, **(extra or {})}


def _accessible_project_ids(db: Session, user: M.User):
    direct = select(M.ProjectMember.project_id).where(M.ProjectMember.user_id == user.id)
    org = select(M.Project.id).join(M.Membership, M.Membership.org_id == M.Project.org_id).where(
        M.Membership.user_id == user.id, M.Membership.role.in_(["owner", "admin"]))
    room = (select(M.RoomProject.project_id).join(M.RoomMember, M.RoomMember.room_id == M.RoomProject.room_id)
            .join(M.CollaborationRoom, M.CollaborationRoom.id == M.RoomProject.room_id)
            .where(M.RoomMember.user_id == user.id, M.CollaborationRoom.archived_at.is_(None)))
    return direct.union(org, room)


@router.get("/domains")
def domains() -> dict[str, Any]:
    return {"items": [{"key": k, "name": v} for k, v in DOMAINS.items()]}


@router.get("/templates")
def templates() -> dict[str, Any]:
    return {"items": list_templates()}


@router.get("/projects")
def list_projects(q: str | None = Query(None, max_length=100), limit: int = Query(50, ge=1, le=200),
                  offset: int = Query(0, ge=0), user: M.User = Depends(current_user), db: Session = Depends(get_db)
                  ) -> dict[str, Any]:
    ids = _accessible_project_ids(db, user).subquery()
    stmt = select(M.Project).where(M.Project.id.in_(select(ids)), M.Project.deleted_at.is_(None))
    if q:
        like = f"%{q.replace('%', '').replace('_', '')}%"
        stmt = stmt.where(or_(M.Project.name.ilike(like), M.Project.description.ilike(like)))
    total = db.scalar(select(func.count()).select_from(stmt.subquery()))
    rows = db.scalars(stmt.order_by(M.Project.updated_at.desc()).limit(limit).offset(offset)).all()
    from ..core.authz import project_role

    items = []
    for p in rows:
        n_docs = db.scalar(select(func.count()).where(M.CADDocument.project_id == p.id, M.CADDocument.deleted_at.is_(None)))
        items.append(project_out(p, project_role(db, user, p), {"document_count": n_docs}))
    return {"items": items, "total": total, "next_offset": offset + limit if offset + limit < (total or 0) else None}


@router.post("/projects", status_code=201)
def create_project(body: ProjectIn, user: M.User = Depends(current_user), db: Session = Depends(get_db)) -> dict[str, Any]:
    if body.org_id:
        role = db.scalar(select(M.Membership.role).where(M.Membership.org_id == body.org_id, M.Membership.user_id == user.id))
        if role is None:
            raise NotFound("Organisation not found")
        org_id = body.org_id
    else:
        org_id = db.scalar(select(M.Organization.id).join(M.Membership, M.Membership.org_id == M.Organization.id)
                           .where(M.Membership.user_id == user.id, M.Organization.personal.is_(True)))
    if body.domain and body.domain not in DOMAINS:
        raise Unprocessable(f"Unknown domain {body.domain!r}")
    if body.template and body.template not in TEMPLATES:
        raise Unprocessable(f"Unknown template {body.template!r}")
    p = M.Project(org_id=org_id, name=body.name.strip(), description=body.description, domain=body.domain,
                  template=body.template, created_by=user.id)
    db.add(p)
    db.flush()
    db.add(M.ProjectMember(project_id=p.id, user_id=user.id, role="owner"))
    audit.record(db, "project.create", user, project_id=p.id, org_id=org_id, target_type="project", target_id=p.id)
    doc_out = None
    if body.template:
        t = TEMPLATES[body.template]
        content = build_template(body.template).model_dump(mode="json")
        doc, rev = create_document_row(db, user, p, t["name"], "part", content, "Created from template " + body.template)
        doc_out = {"id": str(doc.id), "name": doc.name}
    return {"project": project_out(p, "owner"), "document": doc_out}


@router.get("/projects/{project_id}")
def get_project(project_id: str, user: M.User = Depends(current_user), db: Session = Depends(get_db)) -> dict[str, Any]:
    p, role = require_project(db, user, project_id)
    docs = db.scalars(select(M.CADDocument).where(M.CADDocument.project_id == p.id, M.CADDocument.deleted_at.is_(None))
                      .order_by(M.CADDocument.updated_at.desc())).all()
    members = db.execute(select(M.ProjectMember, M.User).join(M.User, M.User.id == M.ProjectMember.user_id)
                         .where(M.ProjectMember.project_id == p.id)).all()
    return {
        "project": project_out(p, role),
        "documents": [{"id": str(d.id), "name": d.name, "kind": d.kind, "head_revision": d.head_revision,
                       "updated_at": d.updated_at.isoformat()} for d in docs],
        "members": [{"user_id": str(u.id), "email": u.email, "display_name": u.display_name, "role": m.role} for m, u in members],
    }


@router.patch("/projects/{project_id}")
def patch_project(project_id: str, body: ProjectPatch, user: M.User = Depends(current_user), db: Session = Depends(get_db)
                  ) -> dict[str, Any]:
    p, role = require_project(db, user, project_id, "edit")
    changes = body.model_dump(exclude_unset=True)
    if "domain" in changes and changes["domain"] and changes["domain"] not in DOMAINS:
        raise Unprocessable("Unknown domain")
    for k, v in changes.items():
        setattr(p, k, v)
    audit.record(db, "project.update", user, project_id=p.id, details=changes)
    return {"project": project_out(p, role)}


@router.delete("/projects/{project_id}")
def delete_project(project_id: str, user: M.User = Depends(current_user), db: Session = Depends(get_db)) -> dict[str, Any]:
    p, _ = require_project(db, user, project_id, "manage")
    p.deleted_at = datetime.now(timezone.utc)
    audit.record(db, "project.delete", user, project_id=p.id, details={"mode": "soft", "purge_after_days": "retention policy"})
    return {"ok": True, "deleted_at": p.deleted_at.isoformat()}


@router.post("/projects/{project_id}/restore")
def restore_project(project_id: str, user: M.User = Depends(current_user), db: Session = Depends(get_db)) -> dict[str, Any]:
    try:
        pid = uuid.UUID(project_id)
    except ValueError as exc:
        raise NotFound("Project not found") from exc
    p = db.get(M.Project, pid)
    if p is None or p.deleted_at is None:
        raise NotFound("Project not found")
    owner = db.scalar(select(M.ProjectMember.role).where(M.ProjectMember.project_id == pid, M.ProjectMember.user_id == user.id))
    if owner != "owner":
        raise NotFound("Project not found")
    p.deleted_at = None
    audit.record(db, "project.restore", user, project_id=p.id)
    return {"project": project_out(p, "owner")}


@router.post("/projects/{project_id}/members", status_code=201)
def add_member(project_id: str, body: MemberIn, user: M.User = Depends(current_user), db: Session = Depends(get_db)
               ) -> dict[str, Any]:
    p, _ = require_project(db, user, project_id, "manage")
    target = db.scalar(select(M.User).where(M.User.email.ilike(body.email.lower())))
    if target is None:
        raise NotFound("No user with that email. Invite them through a collaboration room instead.")
    existing = db.scalar(select(M.ProjectMember).where(M.ProjectMember.project_id == p.id, M.ProjectMember.user_id == target.id))
    if existing:
        raise Conflict("User is already a member")
    db.add(M.ProjectMember(project_id=p.id, user_id=target.id, role=body.role))
    audit.record(db, "project.member_add", user, project_id=p.id, target_type="user", target_id=target.id,
                 details={"role": body.role})
    return {"ok": True}


@router.delete("/projects/{project_id}/members/{user_id}")
def remove_member(project_id: str, user_id: str, user: M.User = Depends(current_user), db: Session = Depends(get_db)
                  ) -> dict[str, Any]:
    p, _ = require_project(db, user, project_id, "manage")
    m = db.scalar(select(M.ProjectMember).where(M.ProjectMember.project_id == p.id, M.ProjectMember.user_id == _uuid(user_id)))
    if m is None:
        raise NotFound("Member not found")
    if m.role == "owner":
        owners = db.scalar(select(func.count()).where(M.ProjectMember.project_id == p.id, M.ProjectMember.role == "owner"))
        if owners <= 1:
            raise Forbidden("Cannot remove the last owner")
    db.delete(m)
    audit.record(db, "project.member_remove", user, project_id=p.id, target_type="user", target_id=user_id)
    return {"ok": True}


def _uuid(v: str) -> uuid.UUID:
    try:
        return uuid.UUID(v)
    except ValueError as exc:
        raise NotFound("Not found") from exc
