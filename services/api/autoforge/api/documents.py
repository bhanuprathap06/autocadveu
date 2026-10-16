"""CAD documents, immutable revisions, recompute, export, measurement.

Concurrency model
-----------------
* Every save names the revision it was based on (``base_revision``). The
  server accepts it only if that is still the head (optimistic concurrency);
  otherwise it returns 409 with the current head so the client can reload,
  re-apply its local operations, or save a copy.
* ``op_id`` is an idempotency key: retrying the same save returns the same
  revision instead of creating a duplicate.
* The server validates every mutation: schema validation, reference checks
  (imported blobs must belong to the project) and a kernel recompute whose
  summary is stored with the revision.
"""

from __future__ import annotations

import gzip
from datetime import datetime, timezone
import hashlib
import json
import uuid
from typing import Any, Literal

from fastapi import APIRouter, Depends, Query, Request
from fastapi.responses import Response
from pydantic import BaseModel, Field, ValidationError
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from ..core import audit, events, ratelimit
from ..core.authz import require_document, require_project
from ..core.errors import ApiError, Conflict, NotFound, Unprocessable
from ..core.storage import get_storage
from ..db import models as M
from ..kernel import occ
from ..kernel import schema as S
from ..kernel_pool import get_pool
from .deps import current_user, get_db

router = APIRouter(tags=["documents"])

EXPORT_TYPES = {
    "step": ("application/step", "step"), "iges": ("application/iges", "igs"), "brep": ("application/octet-stream", "brep"),
    "stl": ("model/stl", "stl"), "obj": ("model/obj", "obj"), "glb": ("model/gltf-binary", "glb"),
}


def canonical(content: dict[str, Any]) -> tuple[str, bytes]:
    raw = json.dumps(content, sort_keys=True, separators=(",", ":")).encode()
    return hashlib.sha256(raw).hexdigest(), raw


def validate_content(kind: str, content: dict[str, Any], project_id: uuid.UUID) -> dict[str, Any]:
    try:
        if kind == "part":
            doc = S.Document.model_validate(content)
            prefix = f"projects/{project_id}/"
            for f in doc.features:
                if isinstance(f, S.ImportF) and not f.params.blob_key.startswith(prefix):
                    raise Unprocessable(f"Import feature {f.id} references a file outside this project", code="forbidden_reference")
            return doc.model_dump(mode="json")
        from ..assembly.schema import AssemblyDocument

        return AssemblyDocument.model_validate(content).model_dump(mode="json")
    except ValidationError as exc:
        raise Unprocessable("Document failed schema validation", code="invalid_document",
                            details=json.loads(exc.json(include_url=False))[:50]) from exc


def compact_summary(full: dict[str, Any]) -> dict[str, Any]:
    return {
        "ok": full.get("ok"),
        "kernel": full.get("kernel"),
        "elapsed_ms": full.get("elapsed_ms"),
        "parameter_errors": full.get("parameter_errors", {}),
        "features": [{k: f[k] for k in ("id", "type", "status", "message", "code", "warnings")} for f in full.get("features", [])],
        "bodies": [{"name": b["name"], "mass": b.get("mass")} for b in full.get("bodies", [])],
    }


def _recompute_for_save(kind: str, content: dict[str, Any], project_id: uuid.UUID, affinity: str, db: Session) -> dict[str, Any]:
    if kind == "part":
        full = get_pool().call("recompute", affinity=affinity, doc=content, tessellate=False,
                               blob_prefix=f"projects/{project_id}/")
        return compact_summary(full)
    from ..assembly.service import validate_assembly_summary

    return validate_assembly_summary(db, content, project_id)


def _index_features(db: Session, rev: M.CADRevision, kind: str, summary: dict[str, Any]) -> None:
    if kind != "part":
        return
    status = {f["id"]: f for f in summary.get("features", [])}
    for i, f in enumerate(rev.content.get("features", [])):
        st = status.get(f["id"], {})
        db.add(M.Feature(revision_id=rev.id, feature_id=f["id"], position=i, type=f["type"], name=f.get("name", ""),
                         status=st.get("status", "unknown"), message=st.get("message", "")[:2000]))


def create_document_row(db: Session, user: M.User, project: M.Project, name: str, kind: str, content: dict[str, Any],
                        message: str = "Initial revision") -> tuple[M.CADDocument, M.CADRevision]:
    content = validate_content(kind, content, project.id)
    doc = M.CADDocument(project_id=project.id, name=name, kind=kind, created_by=user.id, head_revision=1)
    db.add(doc)
    db.flush()
    summary = _recompute_for_save(kind, content, project.id, str(doc.id), db)
    sha, _ = canonical(content)
    rev = M.CADRevision(document_id=doc.id, number=1, content=content, content_sha256=sha, schema_version=S.SCHEMA_VERSION,
                        kernel_version=occ.kernel_version(), message=message, op_id=f"create:{uuid.uuid4()}",
                        recompute=summary, created_by=user.id)
    db.add(rev)
    db.flush()
    _index_features(db, rev, kind, summary)
    if kind == "assembly":
        db.add(M.Assembly(document_id=doc.id))
        from ..assembly.service import index_instances

        index_instances(db, rev)
    audit.record(db, "document.create", user, project_id=project.id, target_type="document", target_id=doc.id,
                 details={"name": name, "kind": kind})
    return doc, rev


def rev_out(r: M.CADRevision, include_content: bool = False, author: str | None = None) -> dict[str, Any]:
    out = {"number": r.number, "parent_number": r.parent_number, "restored_from": r.restored_from, "message": r.message,
           "content_sha256": r.content_sha256, "schema_version": r.schema_version, "kernel_version": r.kernel_version,
           "created_by": str(r.created_by), "author": author, "created_at": r.created_at.isoformat(),
           "ok": (r.recompute or {}).get("ok"), "recompute": r.recompute}
    if include_content:
        out["content"] = r.content
    return out


def doc_out(d: M.CADDocument, role: str | None = None) -> dict[str, Any]:
    return {"id": str(d.id), "project_id": str(d.project_id), "name": d.name, "kind": d.kind, "head_revision": d.head_revision,
            "created_at": d.created_at.isoformat(), "updated_at": d.updated_at.isoformat(), "role": role}


def _get_rev(db: Session, doc: M.CADDocument, number: int | None) -> M.CADRevision:
    n = doc.head_revision if number is None else number
    r = db.scalar(select(M.CADRevision).where(M.CADRevision.document_id == doc.id, M.CADRevision.number == n))
    if r is None:
        raise NotFound(f"Revision {n} not found")
    return r


# --------------------------------------------------------------------------- routes
class DocumentIn(BaseModel):
    name: str = Field(min_length=1, max_length=160)
    kind: Literal["part", "assembly"] = "part"
    content: dict[str, Any] | None = None
    template: str | None = None


class SaveIn(BaseModel):
    base_revision: int = Field(ge=0)
    content: dict[str, Any]
    op_id: str = Field(min_length=8, max_length=80, pattern=r"^[A-Za-z0-9_\-:.]+$")
    message: str = Field(default="", max_length=500)


class RestoreIn(BaseModel):
    revision: int = Field(ge=1)
    base_revision: int = Field(ge=1)
    op_id: str = Field(min_length=8, max_length=80, pattern=r"^[A-Za-z0-9_\-:.]+$")


class RecomputeIn(BaseModel):
    content: dict[str, Any] | None = None
    revision: int | None = None
    rollback_to: str | None = None
    quality: Literal["draft", "normal", "fine"] = "normal"


class SolveIn(BaseModel):
    sketch: dict[str, Any]
    parameters: list[dict[str, Any]] = Field(default_factory=list)
    drag: dict[str, list[float]] | None = None


class ExportIn(BaseModel):
    format: Literal["step", "iges", "brep", "stl", "obj", "glb", "native"]
    revision: int | None = None
    bodies: list[str] | None = None


class MeasureIn(BaseModel):
    content: dict[str, Any] | None = None
    revision: int | None = None
    a: dict[str, Any]
    b: dict[str, Any] | None = None


class RenameIn(BaseModel):
    name: str = Field(min_length=1, max_length=160)


@router.post("/projects/{project_id}/documents", status_code=201)
def create_document(project_id: str, body: DocumentIn, user: M.User = Depends(current_user), db: Session = Depends(get_db)
                    ) -> dict[str, Any]:
    project, role = require_project(db, user, project_id, "edit")
    if body.template:
        from ..catalog.templates import TEMPLATES, build_template

        if body.template not in TEMPLATES:
            raise Unprocessable("Unknown template")
        content = build_template(body.template).model_dump(mode="json")
    elif body.content is not None:
        content = body.content
    else:
        content = S.Document().model_dump(mode="json") if body.kind == "part" else {"schema_version": 1, "instances": [], "mates": []}
    doc, rev = create_document_row(db, user, project, body.name, body.kind, content)
    events.publish(f"project:{project.id}", {"type": "document.created", "document_id": str(doc.id), "by": str(user.id)})
    return {"document": doc_out(doc, role), "revision": rev_out(rev, True)}


@router.get("/documents/{document_id}")
def get_document(document_id: str, revision: int | None = Query(None, ge=1), user: M.User = Depends(current_user),
                 db: Session = Depends(get_db)) -> dict[str, Any]:
    doc, project, role = require_document(db, user, document_id)
    rev = _get_rev(db, doc, revision)
    return {"document": doc_out(doc, role), "project": {"id": str(project.id), "name": project.name},
            "revision": rev_out(rev, True)}


@router.patch("/documents/{document_id}")
def rename_document(document_id: str, body: RenameIn, user: M.User = Depends(current_user), db: Session = Depends(get_db)
                    ) -> dict[str, Any]:
    doc, project, role = require_document(db, user, document_id, "edit")
    old = doc.name
    doc.name = body.name
    audit.record(db, "document.rename", user, project_id=project.id, target_type="document", target_id=doc.id,
                 details={"from": old, "to": body.name})
    return {"document": doc_out(doc, role)}


@router.delete("/documents/{document_id}")
def delete_document(document_id: str, user: M.User = Depends(current_user), db: Session = Depends(get_db)) -> dict[str, Any]:
    doc, project, _ = require_document(db, user, document_id, "edit")
    doc.deleted_at = datetime.now(timezone.utc)
    audit.record(db, "document.delete", user, project_id=project.id, target_type="document", target_id=doc.id)
    events.publish(f"project:{project.id}", {"type": "document.deleted", "document_id": str(doc.id)})
    return {"ok": True}


@router.get("/documents/{document_id}/revisions")
def list_revisions(document_id: str, limit: int = Query(100, ge=1, le=500), offset: int = Query(0, ge=0),
                   user: M.User = Depends(current_user), db: Session = Depends(get_db)) -> dict[str, Any]:
    doc, _, _ = require_document(db, user, document_id)
    rows = db.execute(select(M.CADRevision, M.User.display_name).join(M.User, M.User.id == M.CADRevision.created_by)
                      .where(M.CADRevision.document_id == doc.id).order_by(M.CADRevision.number.desc())
                      .limit(limit).offset(offset)).all()
    total = db.scalar(select(func.count()).where(M.CADRevision.document_id == doc.id))
    return {"items": [rev_out(r, author=a) for r, a in rows], "total": total, "head": doc.head_revision}


@router.get("/documents/{document_id}/revisions/{number}")
def get_revision(document_id: str, number: int, user: M.User = Depends(current_user), db: Session = Depends(get_db)
                 ) -> dict[str, Any]:
    doc, _, _ = require_document(db, user, document_id)
    return {"revision": rev_out(_get_rev(db, doc, number), True)}


def _save(db: Session, user: M.User, doc: M.CADDocument, project: M.Project, content: dict[str, Any], base: int, op_id: str,
          message: str, restored_from: int | None = None) -> tuple[M.CADRevision, bool]:
    content = validate_content(doc.kind, content, project.id)
    sha, raw = canonical(content)
    if len(raw) > 20 * 1024 * 1024:
        raise Unprocessable("Document exceeds 20 MB; split it into multiple documents")
    existing = db.scalar(select(M.CADRevision).where(M.CADRevision.document_id == doc.id, M.CADRevision.op_id == op_id))
    if existing is not None:
        if existing.content_sha256 != sha:
            raise Conflict("op_id was already used for different content", code="idempotency_mismatch")
        return existing, True
    # Lock the document row so concurrent saves serialise on the head check.
    locked = db.scalar(select(M.CADDocument).where(M.CADDocument.id == doc.id).with_for_update())
    if locked.head_revision != base:
        head = _get_rev(db, locked, None)
        raise Conflict(
            f"Document changed since revision {base} (head is now {locked.head_revision}). Reload or merge your changes.",
            code="revision_conflict",
            details={"head_revision": locked.head_revision, "head_sha256": head.content_sha256,
                     "head_author": str(head.created_by), "head_message": head.message},
        )
    summary = _recompute_for_save(doc.kind, content, project.id, str(doc.id), db)
    rev = M.CADRevision(document_id=doc.id, number=base + 1, parent_number=base or None, restored_from=restored_from,
                        content=content, content_sha256=sha, schema_version=S.SCHEMA_VERSION,
                        kernel_version=occ.kernel_version(), message=message, op_id=op_id, recompute=summary,
                        created_by=user.id)
    db.add(rev)
    locked.head_revision = base + 1
    db.flush()
    _index_features(db, rev, doc.kind, summary)
    if doc.kind == "assembly":
        from ..assembly.service import index_instances

        index_instances(db, rev)
    return rev, False


@router.post("/documents/{document_id}/revisions", status_code=201)
def save_revision(document_id: str, body: SaveIn, user: M.User = Depends(current_user), db: Session = Depends(get_db)
                  ) -> dict[str, Any]:
    doc, project, _ = require_document(db, user, document_id, "edit")
    ratelimit.hit(f"save:{user.id}", 600, 600)
    rev, dup = _save(db, user, doc, project, body.content, body.base_revision, body.op_id, body.message)
    if not dup:
        audit.record(db, "document.save", user, project_id=project.id, target_type="document", target_id=doc.id,
                     details={"revision": rev.number, "sha256": rev.content_sha256})
        events.publish(f"project:{project.id}", {"type": "revision.created", "document_id": str(doc.id),
                                                  "revision": rev.number, "by": str(user.id), "name": user.display_name})
    return {"revision": rev_out(rev), "duplicate": dup}


@router.post("/documents/{document_id}/restore", status_code=201)
def restore_revision(document_id: str, body: RestoreIn, user: M.User = Depends(current_user), db: Session = Depends(get_db)
                     ) -> dict[str, Any]:
    doc, project, _ = require_document(db, user, document_id, "edit")
    old = _get_rev(db, doc, body.revision)
    rev, dup = _save(db, user, doc, project, old.content, body.base_revision, body.op_id,
                     f"Restored revision {body.revision}", restored_from=body.revision)
    if not dup:
        audit.record(db, "document.restore", user, project_id=project.id, target_type="document", target_id=doc.id,
                     details={"restored": body.revision, "new_revision": rev.number})
        events.publish(f"project:{project.id}", {"type": "revision.created", "document_id": str(doc.id),
                                                  "revision": rev.number, "by": str(user.id), "restored_from": body.revision})
    return {"revision": rev_out(rev, True), "duplicate": dup}


def diff_content(a: dict[str, Any], b: dict[str, Any]) -> dict[str, Any]:
    pa = {p["name"]: p for p in a.get("parameters", [])}
    pb = {p["name"]: p for p in b.get("parameters", [])}
    fa = {f["id"]: (i, f) for i, f in enumerate(a.get("features", []))}
    fb = {f["id"]: (i, f) for i, f in enumerate(b.get("features", []))}
    changed = []
    for fid in fa.keys() & fb.keys():
        (ia, x), (ib, y) = fa[fid], fb[fid]
        if x != y or ia != ib:
            keys = sorted(k for k in set(x.get("params", {})) | set(y.get("params", {}))
                          if x.get("params", {}).get(k) != y.get("params", {}).get(k))
            changed.append({"id": fid, "name": y.get("name"), "type": y["type"], "params": keys,
                            "moved": ia != ib, "suppression": x.get("suppressed") != y.get("suppressed"),
                            "renamed": x.get("name") != y.get("name")})
    return {
        "parameters": {
            "added": sorted(pb.keys() - pa.keys()), "removed": sorted(pa.keys() - pb.keys()),
            "changed": [{"name": n, "from": pa[n]["expression"], "to": pb[n]["expression"]}
                        for n in sorted(pa.keys() & pb.keys()) if pa[n]["expression"] != pb[n]["expression"]],
        },
        "features": {
            "added": [{"id": i, "type": fb[i][1]["type"], "name": fb[i][1].get("name")} for i in fb.keys() - fa.keys()],
            "removed": [{"id": i, "type": fa[i][1]["type"], "name": fa[i][1].get("name")} for i in fa.keys() - fb.keys()],
            "changed": changed,
        },
        "materials": sorted(k for k in set(a.get("materials", {})) | set(b.get("materials", {}))
                            if a.get("materials", {}).get(k) != b.get("materials", {}).get(k)),
        "instances": _diff_list(a.get("instances"), b.get("instances")),
        "mates": _diff_list(a.get("mates"), b.get("mates")),
    }


def _diff_list(a: list[dict] | None, b: list[dict] | None) -> dict[str, Any] | None:
    if a is None and b is None:
        return None
    ma = {x["id"]: x for x in a or []}
    mb = {x["id"]: x for x in b or []}
    return {"added": sorted(mb.keys() - ma.keys()), "removed": sorted(ma.keys() - mb.keys()),
            "changed": sorted(k for k in ma.keys() & mb.keys() if ma[k] != mb[k])}


@router.get("/documents/{document_id}/diff")
def diff(document_id: str, from_: int = Query(..., alias="from", ge=1), to: int = Query(..., ge=1),
         user: M.User = Depends(current_user), db: Session = Depends(get_db)) -> dict[str, Any]:
    doc, _, _ = require_document(db, user, document_id)
    a, b = _get_rev(db, doc, from_), _get_rev(db, doc, to)
    return {"from": from_, "to": to, "diff": diff_content(a.content, b.content)}


def _model_cache_key(project_id: uuid.UUID, sha: str, quality: str) -> str:
    kv = hashlib.sha256(occ.kernel_version().encode()).hexdigest()[:12]
    return f"projects/{project_id}/cache/models/{sha}-{quality}-{kv}.json.gz"


def model_for_content(db: Session, project: M.Project, doc: M.CADDocument, content: dict[str, Any], quality: str,
                      rollback_to: str | None = None) -> dict[str, Any]:
    if doc.kind == "assembly":
        from ..assembly.service import assembly_model

        return assembly_model(db, project, content, quality)
    content = validate_content("part", content, project.id)
    sha, _ = canonical(content)
    storage = get_storage()
    key = _model_cache_key(project.id, sha, quality) if rollback_to is None else None
    if key and storage.exists(key):
        try:
            data = json.loads(gzip.decompress(storage.get(key)))
            data["cache"] = "hit"
            return data
        except Exception:
            pass
    data = get_pool().call("recompute", affinity=str(doc.id), doc=content, rollback_to=rollback_to, quality=quality,
                           blob_prefix=f"projects/{project.id}/")
    data["content_sha256"] = sha
    if key:
        storage.put(key, gzip.compress(json.dumps(data).encode()), "application/gzip")
    data["cache"] = "miss"
    return data


@router.post("/documents/{document_id}/recompute")
def recompute(document_id: str, body: RecomputeIn, user: M.User = Depends(current_user), db: Session = Depends(get_db)
              ) -> dict[str, Any]:
    doc, project, _ = require_document(db, user, document_id)
    ratelimit.hit(f"recompute:{user.id}", 1200, 600)
    content = body.content if body.content is not None else _get_rev(db, doc, body.revision).content
    return model_for_content(db, project, doc, content, body.quality, body.rollback_to)


@router.post("/documents/{document_id}/sketch/solve")
def solve(document_id: str, body: SolveIn, user: M.User = Depends(current_user), db: Session = Depends(get_db)) -> dict[str, Any]:
    require_document(db, user, document_id)
    ratelimit.hit(f"solve:{user.id}", 3000, 600)
    try:
        S.SketchParams.model_validate(body.sketch)
    except ValidationError as exc:
        raise Unprocessable("Invalid sketch", code="invalid_sketch", details=json.loads(exc.json(include_url=False))[:20]) from exc
    return get_pool().call("solve_sketch", affinity=document_id, timeout=20, sketch=body.sketch, parameters=body.parameters,
                           drag=body.drag)


@router.post("/documents/{document_id}/measure")
def measure(document_id: str, body: MeasureIn, user: M.User = Depends(current_user), db: Session = Depends(get_db)
            ) -> dict[str, Any]:
    doc, project, _ = require_document(db, user, document_id)
    content = body.content if body.content is not None else _get_rev(db, doc, body.revision).content
    content = validate_content("part", content, project.id)
    return get_pool().call("measure", affinity=str(doc.id), doc=content, a=body.a, b=body.b,
                           blob_prefix=f"projects/{project.id}/")


@router.post("/documents/{document_id}/export")
def export(document_id: str, body: ExportIn, request: Request, user: M.User = Depends(current_user),
           db: Session = Depends(get_db)) -> Response:
    doc, project, _ = require_document(db, user, document_id, "export")
    ratelimit.hit(f"export:{user.id}", 120, 600)
    rev = _get_rev(db, doc, body.revision)
    safe = "".join(c if c.isalnum() or c in "-_" else "_" for c in doc.name)[:60] or "model"
    if body.format == "native":
        manifest = {"format": "autoforge-native", "format_version": 1, "kind": doc.kind, "name": doc.name,
                    "revision": rev.number, "schema_version": rev.schema_version, "kernel_version": rev.kernel_version,
                    "content_sha256": rev.content_sha256, "exported_at": datetime.now(timezone.utc).isoformat(),
                    "retains": ["parametric history", "parameters", "sketches+constraints", "materials", "annotations"],
                    "references": "Imported geometry is referenced by project blob key and is not embedded."}
        data = json.dumps({"manifest": manifest, "content": rev.content}, indent=2, sort_keys=True).encode()
        ctype, ext, verification = "application/json", "afdoc.json", {"content_sha256": rev.content_sha256}
    else:
        if doc.kind == "assembly":
            from ..assembly.service import export_assembly

            res = export_assembly(db, project, doc, rev.content, body.format)
        else:
            res = get_pool().call("export", affinity=str(doc.id), timeout=180, doc=rev.content, fmt=body.format,
                                  bodies=body.bodies, name=doc.name, blob_prefix=f"projects/{project.id}/")
        data, verification = res["data"], res["verification"]
        ctype, ext = EXPORT_TYPES[body.format]
    sha = hashlib.sha256(data).hexdigest()
    key = f"projects/{project.id}/exports/{sha}.{ext.split('.')[-1]}"
    get_storage().put(key, data, ctype)
    db.add(M.StoredFile(project_id=project.id, key=key, filename=f"{safe}_r{rev.number}.{ext}", content_type=ctype,
                        size=len(data), sha256=sha, kind="export", created_by=user.id,
                        diagnostics={"document_id": str(doc.id), "revision": rev.number, "format": body.format,
                                     "verification": verification}))
    audit.record(db, "document.export", user, project_id=project.id, target_type="document", target_id=doc.id,
                 details={"revision": rev.number, "format": body.format, "sha256": sha, "bytes": len(data)})
    headers = {"Content-Disposition": f'attachment; filename="{safe}_r{rev.number}.{ext}"', "X-Content-SHA256": sha,
               "X-Export-Verification": json.dumps(verification, default=str)[:4000]}
    return Response(content=data, media_type=ctype, headers=headers)


@router.post("/projects/{project_id}/documents/import-native", status_code=201)
def import_native(project_id: str, body: dict[str, Any], user: M.User = Depends(current_user), db: Session = Depends(get_db)
                  ) -> dict[str, Any]:
    project, role = require_project(db, user, project_id, "edit")
    manifest, content = body.get("manifest") or {}, body.get("content")
    if manifest.get("format") != "autoforge-native" or not isinstance(content, dict):
        raise Unprocessable("Not an AutoForge native document", code="invalid_file")
    sha, _ = canonical(content)
    if manifest.get("content_sha256") and manifest["content_sha256"] != sha:
        raise Unprocessable("Checksum mismatch: the file was modified or corrupted", code="checksum_mismatch")
    kind = manifest.get("kind", "part")
    if kind not in ("part", "assembly"):
        raise Unprocessable("Unknown document kind")
    doc, rev = create_document_row(db, user, project, str(manifest.get("name") or "Imported document")[:160], kind, content,
                                   "Imported from native file")
    return {"document": doc_out(doc, role), "revision": rev_out(rev)}
