"""Assembly services: resolve component sources, solve mates, BOM, models."""

from __future__ import annotations

import gzip
import hashlib
import json
import uuid
from typing import Any

import numpy as np
from sqlalchemy import delete, select
from sqlalchemy.orm import Session

from ..core.errors import Unprocessable
from ..core.storage import get_storage
from ..db import models as M
from ..kernel import expressions, occ
from ..kernel_pool import KernelError, get_pool
from . import solver
from .schema import AssemblyDocument


def _canon(content: dict[str, Any]) -> str:
    return hashlib.sha256(json.dumps(content, sort_keys=True, separators=(",", ":")).encode()).hexdigest()


def resolve_sources(db: Session, project_id: uuid.UUID, doc: AssemblyDocument) -> dict[str, dict[str, Any]]:
    """instance id -> {part_key, content, prefix, label, provenance} or {error}."""
    proj = db.get(M.Project, project_id)
    out: dict[str, dict[str, Any]] = {}
    for inst in doc.instances:
        s = inst.source
        try:
            if s.kind == "document":
                try:
                    did = uuid.UUID(s.document_id or "")
                except ValueError:
                    raise LookupError("invalid document id")
                d = db.get(M.CADDocument, did)
                if d is None or d.deleted_at is not None or d.project_id != project_id:
                    raise LookupError("Part document not found in this project")
                if d.kind != "part":
                    raise LookupError("Nested assemblies are referenced through groups, not as parts")
                n = s.revision or d.head_revision
                r = db.scalar(select(M.CADRevision).where(M.CADRevision.document_id == d.id, M.CADRevision.number == n))
                if r is None:
                    raise LookupError(f"Revision {n} not found")
                out[inst.id] = {"part_key": f"doc:{d.id}:{n}", "content": r.content, "prefix": f"projects/{project_id}/",
                                "label": d.name, "provenance": {"kind": "document", "document_id": str(d.id), "revision": n,
                                                                "pinned": s.revision is not None, "name": d.name}}
            else:
                try:
                    iid = uuid.UUID(s.item_id or "")
                except ValueError:
                    raise LookupError("invalid catalog item id")
                item = db.get(M.ComponentCatalogItem, iid)
                if item is None or (item.org_id is not None and proj is not None and item.org_id != proj.org_id):
                    raise LookupError("Catalog item not found")
                if not item.cad_content:
                    raise LookupError("Catalog item has no 3D geometry available for insertion")
                out[inst.id] = {"part_key": f"cat:{item.id}", "content": item.cad_content, "prefix": "catalog/",
                                "label": item.name, "provenance": {
                                    "kind": "catalog", "item_id": str(item.id), "name": item.name,
                                    "manufacturer": item.manufacturer, "part_number": item.part_number,
                                    "asset_class": item.asset_class, "verification_status": item.verification_status,
                                    "source_url": item.source_url, "material": item.material, "mass_kg": item.mass_kg,
                                    "mass_source": item.mass_source}}
        except LookupError as exc:
            out[inst.id] = {"error": str(exc)}
    return out


def part_model(src: dict[str, Any], quality: str = "normal") -> dict[str, Any]:
    sha = _canon(src["content"])
    kv = hashlib.sha256(occ.kernel_version().encode()).hexdigest()[:12]
    base = src["prefix"].rstrip("/")
    key = f"{base}/cache/models/{sha}-{quality}-{kv}.json.gz"
    st = get_storage()
    if st.exists(key):
        try:
            return json.loads(gzip.decompress(st.get(key)))
        except Exception:
            pass
    data = get_pool().call("recompute", affinity=src["part_key"], doc=src["content"], quality=quality,
                           blob_prefix=src["prefix"])
    st.put(key, gzip.compress(json.dumps(data).encode()), "application/gzip")
    return data


def _env(doc: AssemblyDocument) -> dict[str, float]:
    vals, _ = expressions.resolve_parameters([(p.name, p.expression) for p in doc.parameters])
    return vals


def build(db: Session, project_id: uuid.UUID, content: dict[str, Any], quality: str = "normal",
          drive: dict[str, float] | None = None) -> dict[str, Any]:
    doc = AssemblyDocument.model_validate(content)
    sources = resolve_sources(db, project_id, doc)
    models: dict[str, dict[str, Any]] = {}
    inst_errors: dict[str, str] = {}
    part_for: dict[str, dict[str, Any]] = {}
    for inst in doc.instances:
        src = sources[inst.id]
        if "error" in src:
            inst_errors[inst.id] = src["error"]
            continue
        if src["part_key"] not in models:
            try:
                models[src["part_key"]] = part_model(src, quality)
            except KernelError as exc:
                inst_errors[inst.id] = f"Part failed to regenerate: {exc.message}"
                continue
        part_for[inst.id] = models[src["part_key"]]
    usable = doc.model_copy(update={"instances": [i for i in doc.instances if i.id in part_for]})
    sol = solver.solve(usable, part_for, _env(doc), drive)
    return {"doc": doc, "sources": sources, "models": models, "part_for": part_for, "solve": sol, "inst_errors": inst_errors}


def bom(doc: AssemblyDocument, sources: dict[str, dict[str, Any]], models: dict[str, dict[str, Any]]) -> list[dict[str, Any]]:
    rows: dict[tuple, dict[str, Any]] = {}
    for inst in doc.instances:
        if inst.suppressed:
            continue
        src = sources.get(inst.id, {})
        if "error" in src:
            continue
        key = (src["part_key"], inst.source.body)
        model = models.get(src["part_key"], {})
        bodies = [b for b in model.get("bodies", []) if not inst.source.body or b["name"] == inst.source.body]
        mass = [b.get("mass", {}).get("mass_kg") for b in bodies]
        mats = sorted({(b.get("material") or {}).get("name") for b in bodies if b.get("material")})
        prov = src["provenance"]
        if key not in rows:
            unit_mass = sum(m for m in mass if m is not None) if mass and all(m is not None for m in mass) else None
            mass_basis = "computed from CAD volume x assigned density" if unit_mass is not None else None
            if prov["kind"] == "catalog" and prov.get("mass_kg") is not None:
                unit_mass, mass_basis = prov["mass_kg"], f"catalog record ({prov.get('mass_source') or 'source not stated'})"
            rows[key] = {
                "item": len(rows) + 1, "name": src["label"] + (f" / {inst.source.body}" if inst.source.body else ""),
                "quantity": 0, "source": prov, "part_number": prov.get("part_number"), "manufacturer": prov.get("manufacturer"),
                "material": ", ".join(m for m in mats if m) or prov.get("material") or None,
                "unit_mass_kg": unit_mass, "mass_basis": mass_basis, "instances": [],
            }
        rows[key]["quantity"] += 1
        rows[key]["instances"].append(inst.id)
    out = list(rows.values())
    for r in out:
        r["total_mass_kg"] = r["unit_mass_kg"] * r["quantity"] if r["unit_mass_kg"] is not None else None
    return out


def _inst_out(doc: AssemblyDocument, b: dict[str, Any]) -> list[dict[str, Any]]:
    sol: solver.SolveResult = b["solve"]
    out = []
    for inst in doc.instances:
        T = sol.placements.get(inst.id)
        if T is None:
            T = np.eye(4)
            T[:3, :3] = solver.euler_to_matrix(*inst.placement.rotation)
            T[:3, 3] = inst.placement.position
        src = b["sources"].get(inst.id, {})
        out.append({"id": inst.id, "name": inst.name, "group": inst.group, "part_key": src.get("part_key"),
                    "provenance": src.get("provenance"), "matrix": np.asarray(T).tolist(),
                    "placement": {"position": [float(v) for v in np.asarray(T)[:3, 3]],
                                  "rotation": list(solver.matrix_to_euler(np.asarray(T)[:3, :3]))},
                    "fixed": inst.fixed, "suppressed": inst.suppressed, "color": inst.color,
                    "dof": sol.instance_dof.get(inst.id), "error": b["inst_errors"].get(inst.id)})
    return out


def assembly_model(db: Session, project: M.Project, content: dict[str, Any], quality: str = "normal") -> dict[str, Any]:
    b = build(db, project.id, content, quality)
    doc: AssemblyDocument = b["doc"]
    sol: solver.SolveResult = b["solve"]
    return {
        "kind": "assembly",
        "ok": sol.converged and not b["inst_errors"] and not sol.errors,
        "parts": {k: {"bodies": v.get("bodies", []), "ok": v.get("ok"),
                      "features": [f for f in v.get("features", []) if f["status"] == "error"]} for k, v in b["models"].items()},
        "instances": _inst_out(doc, b),
        "groups": [g.model_dump() for g in doc.groups],
        "solve": {k: v for k, v in sol.to_dict().items() if k != "placements"},
        "bom": bom(doc, b["sources"], b["models"]),
    }


def validate_assembly_summary(db: Session, content: dict[str, Any], project_id: uuid.UUID) -> dict[str, Any]:
    b = build(db, project_id, content, "draft")
    sol: solver.SolveResult = b["solve"]
    return {"ok": sol.converged and not b["inst_errors"] and not sol.errors, "kind": "assembly", "kernel": occ.kernel_version(),
            "solve": {k: v for k, v in sol.to_dict().items() if k != "placements"}, "instance_errors": b["inst_errors"],
            "instances": len(b["doc"].instances), "mates": len(b["doc"].mates), "features": []}


def index_instances(db: Session, rev: M.CADRevision) -> None:
    doc = AssemblyDocument.model_validate(rev.content)
    for inst in doc.instances:
        s = inst.source
        T = np.eye(4)
        T[:3, :3] = solver.euler_to_matrix(*inst.placement.rotation)
        T[:3, 3] = inst.placement.position
        db.add(M.AssemblyInstance(
            revision_id=rev.id, instance_id=inst.id, name=inst.name,
            source_document_id=_uuid_or_none(s.document_id) if s.kind == "document" else None,
            source_revision=s.revision, catalog_item_id=_uuid_or_none(s.item_id) if s.kind == "catalog" else None,
            transform=T.flatten().tolist()))
    asm = db.scalar(select(M.Assembly).where(M.Assembly.document_id == rev.document_id))
    if asm is not None and rev.recompute:
        asm.dof = (rev.recompute.get("solve") or {}).get("dof")
        asm.solve_status = (rev.recompute.get("solve") or {}).get("status")


def _uuid_or_none(v: str | None) -> uuid.UUID | None:
    try:
        return uuid.UUID(v) if v else None
    except ValueError:
        return None


def kernel_instances(b: dict[str, Any]) -> tuple[dict[str, Any], list[dict[str, Any]], dict[str, str]]:
    doc: AssemblyDocument = b["doc"]
    sol: solver.SolveResult = b["solve"]
    parts, insts, prefixes = {}, [], {}
    for inst in doc.instances:
        if inst.suppressed or inst.id not in b["part_for"]:
            continue
        src = b["sources"][inst.id]
        parts[src["part_key"]] = src["content"]
        prefixes[src["part_key"]] = src["prefix"]
        insts.append({"id": inst.id, "name": inst.name, "part_key": src["part_key"], "part_name": src["label"],
                      "body": inst.source.body, "matrix": np.asarray(sol.placements[inst.id]).tolist()})
    return parts, insts, prefixes


def export_assembly(db: Session, project: M.Project, doc_row: M.CADDocument, content: dict[str, Any], fmt: str) -> dict[str, Any]:
    b = build(db, project.id, content, "draft")
    if b["inst_errors"]:
        raise Unprocessable("Some components could not be resolved: " + "; ".join(b["inst_errors"].values()),
                            code="unresolved_components")
    parts, insts, prefixes = kernel_instances(b)
    if not insts:
        raise Unprocessable("Assembly has no components to export", code="nothing_to_export")
    return get_pool().call("export_assembly", timeout=300, parts=parts, instances=insts, prefixes=prefixes, fmt=fmt,
                           name=doc_row.name)
