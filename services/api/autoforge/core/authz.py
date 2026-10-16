"""Authorization rules (server-side, single source of truth).

Project access is the maximum of:
* direct project membership role,
* organisation owner/admin -> ``owner`` on every project of the org,
* membership of a collaboration room the project is shared into
  (room owner/editor -> editor, reviewer -> reviewer, viewer -> viewer).

Unauthorised callers receive 404 (not 403) for resources they cannot see, so
identifiers cannot be probed (prevents IDOR enumeration). Callers who can see
a resource but lack the required role receive 403.
"""

from __future__ import annotations

import uuid

from sqlalchemy import select
from sqlalchemy.orm import Session

from ..db import models as M
from .errors import Forbidden, NotFound

ROLE_RANK = {"viewer": 1, "reviewer": 2, "editor": 3, "owner": 4}
ROOM_TO_PROJECT = {"owner": "editor", "editor": "editor", "reviewer": "reviewer", "viewer": "viewer"}

# Capabilities by minimum project role
CAN = {
    "view": "viewer",
    "comment": "reviewer",
    "edit": "editor",
    "run_simulation": "editor",
    "export": "viewer",
    "manage": "owner",
}


def rank(role: str | None) -> int:
    return ROLE_RANK.get(role or "", 0)


def max_role(*roles: str | None) -> str | None:
    best = None
    for r in roles:
        if rank(r) > rank(best):
            best = r
    return best


def project_role(db: Session, user: M.User, project: M.Project) -> str | None:
    direct = db.scalar(select(M.ProjectMember.role).where(M.ProjectMember.project_id == project.id,
                                                          M.ProjectMember.user_id == user.id))
    org = db.scalar(select(M.Membership.role).where(M.Membership.org_id == project.org_id, M.Membership.user_id == user.id))
    org_role = "owner" if org in ("owner", "admin") else None
    room_roles = db.scalars(
        select(M.RoomMember.role)
        .join(M.RoomProject, M.RoomProject.room_id == M.RoomMember.room_id)
        .join(M.CollaborationRoom, M.CollaborationRoom.id == M.RoomMember.room_id)
        .where(M.RoomProject.project_id == project.id, M.RoomMember.user_id == user.id,
               M.CollaborationRoom.archived_at.is_(None))
    ).all()
    via_room = max_role(*[ROOM_TO_PROJECT[r] for r in room_roles])
    return max_role(direct, org_role, via_room)


def require_project(db: Session, user: M.User, project_id: uuid.UUID | str, capability: str = "view") -> tuple[M.Project, str]:
    try:
        pid = uuid.UUID(str(project_id))
    except ValueError as exc:
        raise NotFound("Project not found") from exc
    project = db.get(M.Project, pid)
    if project is None or project.deleted_at is not None:
        raise NotFound("Project not found")
    role = project_role(db, user, project)
    if role is None:
        raise NotFound("Project not found")
    need = CAN[capability]
    if rank(role) < rank(need):
        raise Forbidden(f"This action requires the {need} role on the project (you are {role})",
                        details={"required": need, "role": role})
    return project, role


def require_document(db: Session, user: M.User, document_id: uuid.UUID | str, capability: str = "view"
                     ) -> tuple[M.CADDocument, M.Project, str]:
    try:
        did = uuid.UUID(str(document_id))
    except ValueError as exc:
        raise NotFound("Document not found") from exc
    doc = db.get(M.CADDocument, did)
    if doc is None or doc.deleted_at is not None:
        raise NotFound("Document not found")
    try:
        project, role = require_project(db, user, doc.project_id, capability)
    except NotFound as exc:
        raise NotFound("Document not found") from exc
    return doc, project, role


def room_role(db: Session, user: M.User, room: M.CollaborationRoom) -> str | None:
    return db.scalar(select(M.RoomMember.role).where(M.RoomMember.room_id == room.id, M.RoomMember.user_id == user.id))


def require_room(db: Session, user: M.User, room_id: uuid.UUID | str, min_role: str = "viewer"
                 ) -> tuple[M.CollaborationRoom, str]:
    try:
        rid = uuid.UUID(str(room_id))
    except ValueError as exc:
        raise NotFound("Room not found") from exc
    room = db.get(M.CollaborationRoom, rid)
    if room is None:
        raise NotFound("Room not found")
    role = room_role(db, user, room)
    if role is None:
        raise NotFound("Room not found")
    if rank(role) < rank(min_role):
        raise Forbidden(f"This action requires the {min_role} role in the room (you are {role})")
    if room.archived_at is not None and min_role != "viewer":
        raise Forbidden("Room is archived")
    return room, role
