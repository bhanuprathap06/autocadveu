"""Private collaboration rooms and invitations."""

from __future__ import annotations

import uuid
from datetime import datetime, timedelta, timezone
from typing import Any, Literal

from fastapi import APIRouter, Depends
from pydantic import BaseModel, EmailStr, Field
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from ..core import audit, events, mail, ratelimit
from ..core.authz import require_project, require_room
from ..core.config import get_settings
from ..core.errors import ApiError, Conflict, Forbidden, NotFound, Unprocessable
from ..core.security import new_token, token_hash
from ..db import models as M
from .deps import current_user, get_db, verified_user

router = APIRouter(tags=["collaboration"])


class RoomIn(BaseModel):
    name: str = Field(min_length=1, max_length=160)
    description: str = Field(default="", max_length=2000)
    org_id: uuid.UUID | None = None


class InviteIn(BaseModel):
    email: EmailStr
    role: Literal["editor", "reviewer", "viewer"]
    expires_in_days: int = Field(default=7, ge=1, le=30)


class ShareIn(BaseModel):
    project_id: str


class RoleIn(BaseModel):
    role: Literal["owner", "editor", "reviewer", "viewer"]


def room_out(r: M.CollaborationRoom, role: str | None = None) -> dict[str, Any]:
    return {"id": str(r.id), "name": r.name, "description": r.description, "org_id": str(r.org_id), "role": role,
            "archived": r.archived_at is not None, "created_at": r.created_at.isoformat()}


def inv_out(i: M.Invitation) -> dict[str, Any]:
    now = datetime.now(timezone.utc)
    state = ("revoked" if i.revoked_at else "accepted" if i.accepted_at else "expired" if i.expires_at <= now else "pending")
    return {"id": str(i.id), "email": i.email, "role": i.role, "state": state, "expires_at": i.expires_at.isoformat(),
            "created_at": i.created_at.isoformat()}


@router.post("/rooms", status_code=201)
def create_room(body: RoomIn, user: M.User = Depends(current_user), db: Session = Depends(get_db)) -> dict[str, Any]:
    if body.org_id:
        if not db.scalar(select(M.Membership.id).where(M.Membership.org_id == body.org_id, M.Membership.user_id == user.id)):
            raise NotFound("Organisation not found")
        org_id = body.org_id
    else:
        org_id = db.scalar(select(M.Organization.id).join(M.Membership, M.Membership.org_id == M.Organization.id)
                           .where(M.Membership.user_id == user.id, M.Organization.personal.is_(True)))
    room = M.CollaborationRoom(org_id=org_id, name=body.name, description=body.description, created_by=user.id)
    db.add(room)
    db.flush()
    db.add(M.RoomMember(room_id=room.id, user_id=user.id, role="owner"))
    audit.record(db, "room.create", user, room_id=room.id, target_type="room", target_id=room.id)
    return {"room": room_out(room, "owner")}


@router.get("/rooms")
def list_rooms(user: M.User = Depends(current_user), db: Session = Depends(get_db)) -> dict[str, Any]:
    rows = db.execute(select(M.CollaborationRoom, M.RoomMember.role).join(M.RoomMember, M.RoomMember.room_id == M.CollaborationRoom.id)
                      .where(M.RoomMember.user_id == user.id).order_by(M.CollaborationRoom.created_at.desc())).all()
    return {"items": [room_out(r, role) for r, role in rows]}


@router.get("/rooms/{room_id}")
def get_room(room_id: str, user: M.User = Depends(current_user), db: Session = Depends(get_db)) -> dict[str, Any]:
    room, role = require_room(db, user, room_id)
    members = db.execute(select(M.RoomMember, M.User).join(M.User, M.User.id == M.RoomMember.user_id)
                         .where(M.RoomMember.room_id == room.id).order_by(M.RoomMember.created_at)).all()
    projects = db.execute(select(M.Project).join(M.RoomProject, M.RoomProject.project_id == M.Project.id)
                          .where(M.RoomProject.room_id == room.id, M.Project.deleted_at.is_(None))).scalars().all()
    out = {"room": room_out(room, role),
           "members": [{"user_id": str(u.id), "display_name": u.display_name, "email": u.email if role == "owner" else None,
                        "role": m.role} for m, u in members],
           "projects": [{"id": str(p.id), "name": p.name, "domain": p.domain} for p in projects]}
    if role == "owner":
        invs = db.scalars(select(M.Invitation).where(M.Invitation.room_id == room.id).order_by(M.Invitation.created_at.desc()))
        out["invitations"] = [inv_out(i) for i in invs]
    return out


@router.post("/rooms/{room_id}/archive")
def archive_room(room_id: str, user: M.User = Depends(current_user), db: Session = Depends(get_db)) -> dict[str, Any]:
    room, _ = require_room(db, user, room_id, "owner")
    room.archived_at = datetime.now(timezone.utc)
    audit.record(db, "room.archive", user, room_id=room.id)
    return {"room": room_out(room, "owner")}


@router.post("/rooms/{room_id}/projects", status_code=201)
def share_project(room_id: str, body: ShareIn, user: M.User = Depends(current_user), db: Session = Depends(get_db)
                  ) -> dict[str, Any]:
    room, _ = require_room(db, user, room_id, "owner")
    project, _ = require_project(db, user, body.project_id, "manage")
    if db.scalar(select(M.RoomProject.id).where(M.RoomProject.room_id == room.id, M.RoomProject.project_id == project.id)):
        raise Conflict("Project already shared in this room")
    db.add(M.RoomProject(room_id=room.id, project_id=project.id, shared_by=user.id))
    audit.record(db, "room.project_share", user, room_id=room.id, project_id=project.id)
    events.publish(f"room:{room.id}", {"type": "room.project_shared", "project_id": str(project.id)})
    return {"ok": True}


@router.delete("/rooms/{room_id}/projects/{project_id}")
def unshare_project(room_id: str, project_id: str, user: M.User = Depends(current_user), db: Session = Depends(get_db)
                    ) -> dict[str, Any]:
    room, _ = require_room(db, user, room_id, "owner")
    try:
        pid = uuid.UUID(project_id)
    except ValueError as exc:
        raise NotFound("Not shared") from exc
    row = db.scalar(select(M.RoomProject).where(M.RoomProject.room_id == room.id, M.RoomProject.project_id == pid))
    if row is None:
        raise NotFound("Not shared")
    db.delete(row)
    audit.record(db, "room.project_unshare", user, room_id=room.id, project_id=pid)
    return {"ok": True}


@router.post("/rooms/{room_id}/invitations", status_code=201)
def invite(room_id: str, body: InviteIn, user: M.User = Depends(verified_user), db: Session = Depends(get_db)) -> dict[str, Any]:
    room, _ = require_room(db, user, room_id, "owner")
    ratelimit.hit(f"invite:{user.id}", 50, 3600)
    email = body.email.lower()
    existing_member = db.scalar(select(M.RoomMember.id).join(M.User, M.User.id == M.RoomMember.user_id)
                                .where(M.RoomMember.room_id == room.id, func.lower(M.User.email) == email))
    if existing_member:
        raise Conflict("That person is already a member")
    tok = new_token()
    inv = M.Invitation(room_id=room.id, email=email, role=body.role, token_hash=token_hash(tok), invited_by=user.id,
                       expires_at=datetime.now(timezone.utc) + timedelta(days=body.expires_in_days))
    db.add(inv)
    db.flush()
    link = f"{get_settings().public_base_url}/invite/{tok}"
    mail.enqueue(db, email, f"{user.display_name} invited you to '{room.name}' on AutoForge",
                 f"You have been invited as {body.role} to the private engineering room '{room.name}'.\n\nAccept: {link}\n\n"
                 f"This invitation expires on {inv.expires_at:%Y-%m-%d %H:%M} UTC and works only for {email}.", "invitation")
    audit.record(db, "room.invite", user, room_id=room.id, target_type="invitation", target_id=inv.id,
                 details={"email": email, "role": body.role, "expires_at": inv.expires_at.isoformat()})
    out = {"invitation": inv_out(inv)}
    if get_settings().is_dev:
        out["dev_link"] = link  # convenience for local development only
    return out


@router.delete("/rooms/{room_id}/invitations/{inv_id}")
def revoke(room_id: str, inv_id: str, user: M.User = Depends(current_user), db: Session = Depends(get_db)) -> dict[str, Any]:
    room, _ = require_room(db, user, room_id, "owner")
    try:
        iid = uuid.UUID(inv_id)
    except ValueError as exc:
        raise NotFound("Invitation not found") from exc
    inv = db.get(M.Invitation, iid)
    if inv is None or inv.room_id != room.id:
        raise NotFound("Invitation not found")
    if inv.accepted_at:
        raise Conflict("Invitation was already accepted; remove the member instead")
    inv.revoked_at = datetime.now(timezone.utc)
    audit.record(db, "room.invite_revoke", user, room_id=room.id, target_type="invitation", target_id=inv.id)
    return {"invitation": inv_out(inv)}


def _invitation(db: Session, token: str) -> M.Invitation:
    if not token or len(token) > 200:
        raise NotFound("Invitation not found")
    inv = db.scalar(select(M.Invitation).where(M.Invitation.token_hash == token_hash(token)))
    if inv is None:
        raise NotFound("Invitation not found")
    return inv


@router.get("/invitations/{token}")
def preview(token: str, user: M.User = Depends(current_user), db: Session = Depends(get_db)) -> dict[str, Any]:
    inv = _invitation(db, token)
    room = db.get(M.CollaborationRoom, inv.room_id)
    inviter = db.get(M.User, inv.invited_by)
    return {"invitation": inv_out(inv), "room": {"name": room.name}, "invited_by": inviter.display_name,
            "email_matches": user.email.lower() == inv.email}


@router.post("/invitations/{token}/accept")
def accept(token: str, user: M.User = Depends(verified_user), db: Session = Depends(get_db)) -> dict[str, Any]:
    ratelimit.hit(f"accept:{user.id}", 30, 3600)
    inv = _invitation(db, token)
    st = inv_out(inv)["state"]
    if st != "pending":
        raise ApiError(f"This invitation is {st}", code=f"invitation_{st}", status=410)
    if user.email.lower() != inv.email:
        audit.record(db, "room.invite_wrong_account", user, room_id=inv.room_id, target_type="invitation", target_id=inv.id)
        db.commit()
        raise Forbidden("This invitation was issued to a different email address", code="invitation_email_mismatch")
    room = db.get(M.CollaborationRoom, inv.room_id)
    if room.archived_at:
        raise ApiError("The room has been archived", code="room_archived", status=410)
    if not db.scalar(select(M.RoomMember.id).where(M.RoomMember.room_id == room.id, M.RoomMember.user_id == user.id)):
        db.add(M.RoomMember(room_id=room.id, user_id=user.id, role=inv.role))
    inv.accepted_at, inv.accepted_by = datetime.now(timezone.utc), user.id
    audit.record(db, "room.invite_accept", user, room_id=room.id, target_type="invitation", target_id=inv.id,
                 details={"role": inv.role})
    events.publish(f"room:{room.id}", {"type": "room.member_joined", "user_id": str(user.id), "name": user.display_name})
    return {"room": room_out(room, inv.role)}


@router.put("/rooms/{room_id}/members/{user_id}")
def set_role(room_id: str, user_id: str, body: RoleIn, user: M.User = Depends(current_user), db: Session = Depends(get_db)
             ) -> dict[str, Any]:
    room, _ = require_room(db, user, room_id, "owner")
    try:
        uid = uuid.UUID(user_id)
    except ValueError as exc:
        raise NotFound("Member not found") from exc
    m = db.scalar(select(M.RoomMember).where(M.RoomMember.room_id == room.id, M.RoomMember.user_id == uid))
    if m is None:
        raise NotFound("Member not found")
    if m.role == "owner" and body.role != "owner":
        owners = db.scalar(select(func.count()).where(M.RoomMember.room_id == room.id, M.RoomMember.role == "owner"))
        if owners <= 1:
            raise Forbidden("A room needs at least one owner")
    old, m.role = m.role, body.role
    audit.record(db, "room.role_change", user, room_id=room.id, target_type="user", target_id=uid,
                 details={"from": old, "to": body.role})
    return {"ok": True}


@router.delete("/rooms/{room_id}/members/{user_id}")
def remove_member(room_id: str, user_id: str, user: M.User = Depends(current_user), db: Session = Depends(get_db)) -> dict[str, Any]:
    try:
        uid = uuid.UUID(user_id)
    except ValueError as exc:
        raise NotFound("Member not found") from exc
    if uid == user.id:
        room, role = require_room(db, user, room_id)  # anyone may leave
    else:
        room, role = require_room(db, user, room_id, "owner")
    m = db.scalar(select(M.RoomMember).where(M.RoomMember.room_id == room.id, M.RoomMember.user_id == uid))
    if m is None:
        raise NotFound("Member not found")
    if m.role == "owner":
        owners = db.scalar(select(func.count()).where(M.RoomMember.room_id == room.id, M.RoomMember.role == "owner"))
        if owners <= 1:
            raise Forbidden("A room needs at least one owner")
    db.delete(m)
    audit.record(db, "room.member_remove", user, room_id=room.id, target_type="user", target_id=uid)
    return {"ok": True}
