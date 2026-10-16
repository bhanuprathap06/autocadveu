"""Reference files and CAD imports.

* Size limit enforced while streaming the upload (never buffers more than limit+1).
* Extension whitelist + content sniffing; HTML/SVG/scripts are rejected outright.
* CAD files are parsed only inside an isolated, memory-limited kernel worker.
* Downloads are always ``Content-Disposition: attachment`` with ``nosniff``.
* Malware scanning is an external integration (ClamAV); without it files are
  marked ``not_scanned`` and that status is shown to users.
"""

from __future__ import annotations

import hashlib
import re
import uuid
from typing import Any

from fastapi import APIRouter, Depends, File, Form, UploadFile
from fastapi.responses import Response
from sqlalchemy import select
from sqlalchemy.orm import Session

from ..core import audit, events
from ..core.authz import require_project
from ..core.config import get_settings
from ..core.errors import ApiError, NotFound, Unprocessable
from ..core.storage import blob_key, get_storage
from ..db import models as M
from ..kernel import schema as S
from ..kernel_pool import KernelError, get_pool
from .deps import current_user, get_db
from .documents import create_document_row, doc_out, rev_out

router = APIRouter(tags=["files"])

ALLOWED_EXT = {
    "step": "application/step", "stp": "application/step", "iges": "application/iges", "igs": "application/iges",
    "brep": "application/octet-stream", "stl": "model/stl", "obj": "model/obj", "glb": "model/gltf-binary",
    "pdf": "application/pdf", "png": "image/png", "jpg": "image/jpeg", "jpeg": "image/jpeg", "csv": "text/csv",
    "txt": "text/plain", "json": "application/json", "dxf": "image/vnd.dxf",
}
CAD_FORMATS = {"step": "step", "stp": "step", "iges": "iges", "igs": "iges", "brep": "brep"}
MAGIC = {"pdf": b"%PDF-", "png": b"\x89PNG\r\n\x1a\n", "jpg": b"\xff\xd8\xff", "jpeg": b"\xff\xd8\xff", "glb": b"glTF"}
FORBIDDEN_SNIFF = (b"<html", b"<!doctype", b"<script", b"<svg", b"MZ", b"\x7fELF", b"#!")


def _safe_name(name: str) -> str:
    base = name.replace("\\", "/").rsplit("/", 1)[-1]
    base = re.sub(r"[^A-Za-z0-9._ \-]", "_", base).strip(" .")[:200]
    return base or "file"


async def read_limited(upload: UploadFile) -> bytes:
    limit = get_settings().max_upload_mb * 1024 * 1024
    chunks, size = [], 0
    while True:
        chunk = await upload.read(1024 * 1024)
        if not chunk:
            break
        size += len(chunk)
        if size > limit:
            raise ApiError(f"File exceeds the {get_settings().max_upload_mb} MB upload limit", code="file_too_large", status=413)
        chunks.append(chunk)
    data = b"".join(chunks)
    if not data:
        raise Unprocessable("Uploaded file is empty", code="empty_file")
    return data


def validate_upload(filename: str, data: bytes) -> tuple[str, str]:
    name = _safe_name(filename)
    ext = name.lower().rsplit(".", 1)[-1] if "." in name else ""
    if ext not in ALLOWED_EXT:
        raise Unprocessable(f"File type .{ext or '?'} is not allowed. Allowed: {', '.join(sorted(ALLOWED_EXT))}",
                            code="file_type_not_allowed")
    head = data[:512].lstrip().lower()
    if any(head.startswith(sig.lower()) for sig in FORBIDDEN_SNIFF):
        raise Unprocessable("File content is not permitted (executable or active content detected)", code="file_rejected")
    if ext in MAGIC and not data.startswith(MAGIC[ext]):
        raise Unprocessable(f"File content does not match the .{ext} extension", code="format_mismatch")
    return name, ext


def file_out(f: M.StoredFile) -> dict[str, Any]:
    return {"id": str(f.id), "filename": f.filename, "content_type": f.content_type, "size": f.size, "sha256": f.sha256,
            "kind": f.kind, "scan_status": f.scan_status, "diagnostics": f.diagnostics, "created_at": f.created_at.isoformat()}


@router.post("/projects/{project_id}/files", status_code=201)
async def upload_file(project_id: str, file: UploadFile = File(...), user: M.User = Depends(current_user),
                      db: Session = Depends(get_db)) -> dict[str, Any]:
    project, _ = require_project(db, user, project_id, "edit")
    data = await read_limited(file)
    name, ext = validate_upload(file.filename or "file", data)
    sha = hashlib.sha256(data).hexdigest()
    key = blob_key(str(project.id), sha)
    if get_storage().put(key, data, ALLOWED_EXT[ext]) != sha:
        raise ApiError("Upload integrity check failed", code="checksum_mismatch")
    row = M.StoredFile(project_id=project.id, key=key, filename=name, content_type=ALLOWED_EXT[ext], size=len(data),
                       sha256=sha, kind="reference", created_by=user.id)
    db.add(row)
    db.flush()
    audit.record(db, "file.upload", user, project_id=project.id, target_type="file", target_id=row.id,
                 details={"filename": name, "sha256": sha, "size": len(data)})
    return {"file": file_out(row)}


@router.post("/projects/{project_id}/imports", status_code=201)
async def import_cad(project_id: str, file: UploadFile = File(...), name: str | None = Form(None),
                     user: M.User = Depends(current_user), db: Session = Depends(get_db)) -> dict[str, Any]:
    project, role = require_project(db, user, project_id, "edit")
    data = await read_limited(file)
    fname, ext = validate_upload(file.filename or "model.step", data)
    if ext not in CAD_FORMATS:
        raise Unprocessable("Only STEP, IGES and BREP can be imported as editable solids. Mesh formats (STL/OBJ/GLB) do not "
                            "contain B-rep geometry; upload them as reference files instead.", code="unsupported_format")
    fmt = CAD_FORMATS[ext]
    try:
        insp = get_pool().call("inspect_import", timeout=120, data=data, fmt=fmt, filename=fname)
    except KernelError as exc:
        audit.record(db, "file.import_rejected", user, project_id=project.id, details={"filename": fname, "error": exc.message})
        db.commit()
        raise Unprocessable(f"Import failed: {exc.message}", code=exc.code) from exc
    sha = hashlib.sha256(data).hexdigest()
    key = blob_key(str(project.id), sha)
    get_storage().put(key, data, ALLOWED_EXT[ext])
    frow = M.StoredFile(project_id=project.id, key=key, filename=fname, content_type=ALLOWED_EXT[ext], size=len(data), sha256=sha,
                        kind="cad_import", created_by=user.id, diagnostics=insp["diagnostics"])
    db.add(frow)
    content = S.Document(features=[S.ImportF(id="imp1", name=f"Import {fname}",
                                             params=S.ImportParams(blob_key=key, sha256=sha, filename=fname, format=fmt,
                                                                   body="Imported"))]).model_dump(mode="json")
    doc, rev = create_document_row(db, user, project, (name or fname.rsplit(".", 1)[0])[:160], "part", content,
                                   f"Imported {fname}")
    audit.record(db, "file.import", user, project_id=project.id, target_type="document", target_id=doc.id,
                 details={"filename": fname, "sha256": sha, "format": fmt})
    events.publish(f"project:{project.id}", {"type": "document.created", "document_id": str(doc.id)})
    return {"document": doc_out(doc, role), "revision": rev_out(rev), "diagnostics": insp["diagnostics"]}


@router.get("/projects/{project_id}/files")
def list_files(project_id: str, kind: str | None = None, user: M.User = Depends(current_user), db: Session = Depends(get_db)
               ) -> dict[str, Any]:
    project, _ = require_project(db, user, project_id)
    stmt = select(M.StoredFile).where(M.StoredFile.project_id == project.id)
    if kind:
        stmt = stmt.where(M.StoredFile.kind == kind)
    rows = db.scalars(stmt.order_by(M.StoredFile.created_at.desc()).limit(500)).all()
    return {"items": [file_out(f) for f in rows]}


def _file(db: Session, user: M.User, file_id: str, capability: str = "view") -> tuple[M.StoredFile, M.Project]:
    try:
        fid = uuid.UUID(file_id)
    except ValueError as exc:
        raise NotFound("File not found") from exc
    f = db.get(M.StoredFile, fid)
    if f is None:
        raise NotFound("File not found")
    try:
        project, _ = require_project(db, user, f.project_id, capability)
    except NotFound as exc:
        raise NotFound("File not found") from exc
    return f, project


@router.get("/files/{file_id}/download")
def download(file_id: str, user: M.User = Depends(current_user), db: Session = Depends(get_db)) -> Response:
    f, project = _file(db, user, file_id)
    data = get_storage().get(f.key, expected_sha256=f.sha256)
    audit.record(db, "file.download", user, project_id=project.id, target_type="file", target_id=f.id)
    return Response(content=data, media_type="application/octet-stream" if f.content_type.startswith("text/") else f.content_type,
                    headers={"Content-Disposition": f'attachment; filename="{f.filename}"', "X-Content-SHA256": f.sha256,
                             "X-Content-Type-Options": "nosniff", "Cache-Control": "private, no-store"})


@router.delete("/files/{file_id}")
def delete_file(file_id: str, user: M.User = Depends(current_user), db: Session = Depends(get_db)) -> dict[str, Any]:
    f, project = _file(db, user, file_id, "edit")
    referenced = db.scalar(select(M.StoredFile.id).where(M.StoredFile.key == f.key, M.StoredFile.id != f.id))
    db.delete(f)
    if not referenced and f.kind == "reference":
        get_storage().delete(f.key)
    audit.record(db, "file.delete", user, project_id=project.id, target_type="file", target_id=file_id)
    return {"ok": True}
