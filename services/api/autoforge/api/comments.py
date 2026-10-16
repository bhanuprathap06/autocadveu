"""Comments and annotations on models, drawings, requirements and simulations."""

from __future__ import annotations

import uuid
from datetime import datetime, timezone
from typing import Any, Literal

from fastapi import APIRouter, Depends, Query
from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.orm import Session

from ..core import audit, events
from ..core.authz import rank, require_project
from ..core.errors import Forbidden, NotFound, Unprocessable
from ..db import models as M
from .deps import current_user, get_db

router = APIRouter(tags=["comments"])
TARGETS = Literal["project", "document", "revision", "drawing", "requirement", "simulation", "problem"]


class Anchor(BaseModel):
    face: str | None = Field(default=None, max_length=200)
    body: str | None = Field(default=None, max_length=64)
    point: tuple[float, float, float] | None = None
    camera: dict[str, Any] | None = None
    revision: int | None = None


class CommentIn(BaseModel):
    target_type: TARGETS
    target_id: str = Field(max_length=64)
    body: str = Field(min_length=1, max_length=10000)
    anchor: Anchor | None = None
    parent_id: str | None = None


class CommentPatch(BaseModel):
    body: str | None = Field(default=None, min_length=1, max_length=10000)
    resolved: bool | None = None


def c_out(c: M.Comment, author: M.User) -> dict[str, Any]:
    return {"id": str(c.id), "target_type": c.target_type, "target_id": c.target_id, "anchor": c.anchor, "body": c.body,
            "author": {"id": str(author.id), "display_name": author.display_name}, "parent_id": str(c.parent_id) if c.parent_id else None,
            "resolved": c.resolved_at is not None, "created_at": c.created_at.isoformat(),
            "edited_at": c.edited_at.isoformat() if c.edited_at else None}


@router.get("/projects/{project_id}/comments")
def list_comments(project_id: str, target_type: str | None = None, target_id: str | None = None,
                  include_resolved: bool = Query(True), user: M.User = Depends(current_user), db: Session = Depends(get_db)
                  ) -> dict[str, Any]:
    p, _ = require_project(db, user, project_id)
    stmt = select(M.Comment, M.User).join(M.User, M.User.id == M.Comment.author_id).where(M.Comment.project_id == p.id)
    if target_type:
        stmt = stmt.where(M.Comment.target_type == target_type)
    if target_id:
        stmt = stmt.where(M.Comment.target_id == target_id)
    if not include_resolved:
        stmt = stmt.where(M.Comment.resolved_at.is_(None))
    return {"items": [c_out(c, u) for c, u in db.execute(stmt.order_by(M.Comment.created_at)).all()]}


@router.post("/projects/{project_id}/comments", status_code=201)
def create_comment(project_id: str, body: CommentIn, user: M.User = Depends(current_user), db: Session = Depends(get_db)
                   ) -> dict[str, Any]:
    p, _ = require_project(db, user, project_id, "comment")
    parent = None
    if body.parent_id:
        try:
            parent = db.get(M.Comment, uuid.UUID(body.parent_id))
        except ValueError:
            parent = None
        if parent is None or parent.project_id != p.id:
            raise Unprocessable("Parent comment not found in this project")
    c = M.Comment(project_id=p.id, target_type=body.target_type, target_id=body.target_id, body=body.body,
                  anchor=body.anchor.model_dump() if body.anchor else None, author_id=user.id,
                  parent_id=parent.id if parent else None)
    db.add(c)
    db.flush()
    audit.record(db, "comment.create", user, project_id=p.id, target_type="comment", target_id=c.id,
                 details={"on": f"{body.target_type}:{body.target_id}"})
    out = c_out(c, user)
    events.publish(f"project:{p.id}", {"type": "comment.created", "comment": out})
    return {"comment": out}


@router.patch("/comments/{comment_id}")
def patch_comment(comment_id: str, body: CommentPatch, user: M.User = Depends(current_user), db: Session = Depends(get_db)
                  ) -> dict[str, Any]:
    try:
        c = db.get(M.Comment, uuid.UUID(comment_id))
    except ValueError:
        c = None
    if c is None:
        raise NotFound("Comment not found")
    try:
        p, role = require_project(db, user, c.project_id, "comment")
    except NotFound as exc:
        raise NotFound("Comment not found") from exc
    if body.body is not None:
        if c.author_id != user.id:
            raise Forbidden("Only the author can edit a comment")
        c.body, c.edited_at = body.body, datetime.now(timezone.utc)
    if body.resolved is not None:
        c.resolved_at = datetime.now(timezone.utc) if body.resolved else None
        c.resolved_by = user.id if body.resolved else None
    audit.record(db, "comment.update", user, project_id=p.id, target_type="comment", target_id=c.id)
    out = c_out(c, db.get(M.User, c.author_id))
    events.publish(f"project:{p.id}", {"type": "comment.updated", "comment": out})
    return {"comment": out}


@router.delete("/comments/{comment_id}")
def delete_comment(comment_id: str, user: M.User = Depends(current_user), db: Session = Depends(get_db)) -> dict[str, Any]:
    try:
        c = db.get(M.Comment, uuid.UUID(comment_id))
    except ValueError:
        c = None
    if c is None:
        raise NotFound("Comment not found")
    try:
        p, role = require_project(db, user, c.project_id, "comment")
    except NotFound as exc:
        raise NotFound("Comment not found") from exc
    if c.author_id != user.id and rank(role) < rank("owner"):
        raise Forbidden("Only the author or a project owner can delete a comment")
    db.delete(c)
    audit.record(db, "comment.delete", user, project_id=p.id, target_type="comment", target_id=comment_id)
    events.publish(f"project:{p.id}", {"type": "comment.deleted", "comment_id": comment_id})
    return {"ok": True}
