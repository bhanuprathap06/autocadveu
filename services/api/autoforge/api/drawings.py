"""Technical drawing workspace endpoints."""

from __future__ import annotations

import gzip
import hashlib
import json
import uuid
from typing import Any, Literal

from fastapi import APIRouter, Depends, Query
from fastapi.responses import Response
from pydantic import BaseModel, Field, ValidationError
from sqlalchemy import select
from sqlalchemy.orm import Session

from ..core import audit, events
from ..core.authz import require_document, require_project
from ..core.errors import Conflict, NotFound, Unprocessable
from ..core.storage import get_storage
from ..db import models as M
from ..drawing import render
from ..drawing.layout import Layout, fmt
from ..drawing.schema import DrawingContent, TitleBlock, validate_gdt
from ..kernel import occ
from ..kernel_pool import get_pool
from .deps import current_user, get_db
from .documents import _get_rev, model_for_content

router = APIRouter(tags=["drawings"])


class DrawingIn(BaseModel):
    document_id: str
    name: str = Field(min_length=1, max_length=160)
    revision_pin: int | None = Field(default=None, ge=1)
    content: dict[str, Any] | None = None


class DrawingSave(BaseModel):
    content: dict[str, Any]
    version: int = Field(ge=1)
    revision_pin: int | None = Field(default=None, ge=1)
    unpin: bool = False


def _validate(content: dict[str, Any]) -> DrawingContent:
    try:
        return DrawingContent.model_validate(content)
    except ValidationError as exc:
        raise Unprocessable("Invalid drawing content", code="invalid_drawing",
                            details=[{"loc": list(e["loc"]), "msg": e["msg"]} for e in exc.errors()][:30]) from exc


def drawing_out(d: M.Drawing, doc: M.CADDocument) -> dict[str, Any]:
    return {"id": str(d.id), "project_id": str(d.project_id), "document_id": str(d.document_id), "document_name": doc.name,
            "document_kind": doc.kind, "name": d.name, "revision_pin": d.revision_pin, "version": d.version, "content": d.content,
            "updated_at": d.updated_at.isoformat(), "head_revision": doc.head_revision}


def render_drawing(db: Session, project: M.Project, d: M.Drawing) -> dict[str, Any]:
    doc = db.get(M.CADDocument, d.document_id)
    rev = _get_rev(db, doc, d.revision_pin)
    content = _validate(d.content)
    issues = validate_gdt(content)
    sha = hashlib.sha256(json.dumps({"c": d.content, "r": rev.content_sha256, "k": occ.kernel_version(), "v": 3},
                                    sort_keys=True).encode()).hexdigest()
    key = f"projects/{project.id}/cache/drawings/{sha}.json.gz"
    st = get_storage()
    if st.exists(key):
        try:
            layout = json.loads(gzip.decompress(st.get(key)))
            return {"layout": layout, "issues": issues, "revision": rev.number, "stale": d.revision_pin is not None and
                    d.revision_pin != doc.head_revision}
        except Exception:
            pass
    kinds = {v.id: v.kind for v in content.views}
    dims = [{**dm.model_dump(mode="json"), "view_kind": kinds[dm.view]} for dm in content.dimensions]
    meta: dict[str, Any] = {"title": d.name or doc.name}
    if doc.kind == "part":
        proj = get_pool().call("drawing_views", affinity=str(doc.id), timeout=180, views=list(kinds.values()),
                               content=rev.content, blob_prefix=f"projects/{project.id}/", dims=dims)
        mats = rev.content.get("materials") or {}
        if mats:
            meta["material"] = ", ".join(sorted({m["name"] for m in mats.values()}))[:120]
        model = model_for_content(db, project, doc, rev.content, "draft")
        masses = [b.get("mass", {}).get("mass_kg") for b in model.get("bodies", [])]
        if masses and all(m is not None for m in masses):
            meta["mass_text"] = fmt(sum(masses)) + " kg (calc.)"
        else:
            meta["mass_text"] = "n/a (no material)"
    else:
        from ..assembly import service

        b = service.build(db, project.id, rev.content, "draft")
        parts, insts, prefixes = service.kernel_instances(b)
        proj = get_pool().call("drawing_views", timeout=300, views=list(kinds.values()), parts=parts, instances=insts,
                               prefixes=prefixes)
        meta["bom"] = service.bom(b["doc"], b["sources"], b["models"])
        total = [r["total_mass_kg"] for r in meta["bom"]]
        meta["mass_text"] = (fmt(sum(total)) + " kg (calc.)") if total and all(t is not None for t in total) else "incomplete"
    lay = Layout(content, proj["views"], proj.get("dims", {}), meta).build()
    lay["svg"] = render.to_svg(lay)
    st.put(key, gzip.compress(json.dumps(lay).encode()), "application/gzip")
    return {"layout": lay, "issues": issues, "revision": rev.number,
            "stale": d.revision_pin is not None and d.revision_pin != doc.head_revision}


def _drawing(db: Session, user: M.User, drawing_id: str, cap: str = "view") -> tuple[M.Drawing, M.Project, M.CADDocument]:
    try:
        did = uuid.UUID(drawing_id)
    except ValueError as exc:
        raise NotFound("Drawing not found") from exc
    d = db.get(M.Drawing, did)
    if d is None:
        raise NotFound("Drawing not found")
    try:
        project, _ = require_project(db, user, d.project_id, cap)
    except NotFound as exc:
        raise NotFound("Drawing not found") from exc
    return d, project, db.get(M.CADDocument, d.document_id)


@router.post("/projects/{project_id}/drawings", status_code=201)
def create(project_id: str, body: DrawingIn, user: M.User = Depends(current_user), db: Session = Depends(get_db)) -> dict[str, Any]:
    project, _ = require_project(db, user, project_id, "edit")
    doc, dproj, _ = require_document(db, user, body.document_id)
    if dproj.id != project.id:
        raise Unprocessable("Document belongs to another project")
    if body.revision_pin is not None:
        _get_rev(db, doc, body.revision_pin)
    content = _validate(body.content) if body.content else DrawingContent(
        title_block=TitleBlock(title=doc.name, drawn_by=user.display_name[:60], revision="A"))
    d = M.Drawing(project_id=project.id, document_id=doc.id, revision_pin=body.revision_pin, name=body.name,
                  content=content.model_dump(mode="json"), created_by=user.id)
    db.add(d)
    db.flush()
    audit.record(db, "drawing.create", user, project_id=project.id, target_type="drawing", target_id=d.id)
    return {"drawing": drawing_out(d, doc)}


@router.get("/projects/{project_id}/drawings")
def list_drawings(project_id: str, user: M.User = Depends(current_user), db: Session = Depends(get_db)) -> dict[str, Any]:
    project, _ = require_project(db, user, project_id)
    rows = db.execute(select(M.Drawing, M.CADDocument).join(M.CADDocument, M.CADDocument.id == M.Drawing.document_id)
                      .where(M.Drawing.project_id == project.id).order_by(M.Drawing.updated_at.desc())).all()
    return {"items": [drawing_out(d, doc) for d, doc in rows]}


@router.get("/drawings/{drawing_id}")
def get_drawing(drawing_id: str, render_sheet: bool = Query(True, alias="render"), user: M.User = Depends(current_user),
                db: Session = Depends(get_db)) -> dict[str, Any]:
    d, project, doc = _drawing(db, user, drawing_id)
    out: dict[str, Any] = {"drawing": drawing_out(d, doc)}
    if render_sheet:
        r = render_drawing(db, project, d)
        out.update({"svg": r["layout"]["svg"], "issues": r["issues"], "warnings": r["layout"]["warnings"],
                    "scale": r["layout"]["scale"], "projection": r["layout"]["projection"], "model_revision": r["revision"],
                    "stale": r["stale"], "sheet": {"width": r["layout"]["width"], "height": r["layout"]["height"]},
                    "views": r["layout"]["views"]})
    return out


@router.put("/drawings/{drawing_id}")
def save_drawing(drawing_id: str, body: DrawingSave, user: M.User = Depends(current_user), db: Session = Depends(get_db)
                 ) -> dict[str, Any]:
    d, project, doc = _drawing(db, user, drawing_id, "edit")
    locked = db.scalar(select(M.Drawing).where(M.Drawing.id == d.id).with_for_update())
    if locked.version != body.version:
        raise Conflict(f"Drawing changed since version {body.version} (now {locked.version}); reload before saving",
                       code="version_conflict", details={"version": locked.version})
    content = _validate(body.content)
    if body.revision_pin is not None:
        _get_rev(db, doc, body.revision_pin)
        locked.revision_pin = body.revision_pin
    if body.unpin:
        locked.revision_pin = None
    locked.content = content.model_dump(mode="json")
    locked.version += 1
    audit.record(db, "drawing.save", user, project_id=project.id, target_type="drawing", target_id=d.id,
                 details={"version": locked.version})
    events.publish(f"project:{project.id}", {"type": "drawing.saved", "drawing_id": str(d.id), "version": locked.version})
    return {"drawing": drawing_out(locked, doc), "issues": validate_gdt(content)}


@router.get("/drawings/{drawing_id}/export")
def export_drawing(drawing_id: str, format: Literal["pdf", "dxf", "svg"] = "pdf", user: M.User = Depends(current_user),
                   db: Session = Depends(get_db)) -> Response:
    d, project, doc = _drawing(db, user, drawing_id, "export")
    r = render_drawing(db, project, d)
    errors = [i for i in r["issues"] if i["severity"] == "error"]
    if errors:
        raise Unprocessable("Fix GD&T validation errors before exporting: " + "; ".join(e["message"] for e in errors[:3]),
                            code="drawing_invalid", details=errors)
    lay = r["layout"]
    if format == "pdf":
        data, ctype = render.to_pdf(lay, d.name), "application/pdf"
    elif format == "dxf":
        data, ctype = render.to_dxf(lay), "image/vnd.dxf"
    else:
        data, ctype = lay["svg"].encode(), "image/svg+xml"
    sha = hashlib.sha256(data).hexdigest()
    key = f"projects/{project.id}/exports/{sha}.{format}"
    get_storage().put(key, data, ctype)
    safe = "".join(ch if ch.isalnum() or ch in "-_" else "_" for ch in d.name)[:60] or "drawing"
    db.add(M.StoredFile(project_id=project.id, key=key, filename=f"{safe}_v{d.version}.{format}", content_type=ctype,
                        size=len(data), sha256=sha, kind="export", created_by=user.id,
                        diagnostics={"drawing_id": str(d.id), "model_revision": r["revision"], "version": d.version}))
    audit.record(db, "drawing.export", user, project_id=project.id, target_type="drawing", target_id=d.id,
                 details={"format": format, "sha256": sha, "model_revision": r["revision"]})
    headers = {"Content-Disposition": f'attachment; filename="{safe}_v{d.version}.{format}"', "X-Content-SHA256": sha}
    if format == "svg":
        headers["Content-Security-Policy"] = "default-src 'none'; style-src 'unsafe-inline'"
    return Response(data, media_type=ctype, headers=headers)
