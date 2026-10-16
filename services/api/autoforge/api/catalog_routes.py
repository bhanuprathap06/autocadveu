"""Automotive component library: search, provenance, comparison, favourites."""

from __future__ import annotations

import re
import uuid
from datetime import datetime
from typing import Any, Literal

from fastapi import APIRouter, Depends, Query
from fastapi.responses import Response
from pydantic import BaseModel, Field, HttpUrl, ValidationError, model_validator
from sqlalchemy import Float, and_, case, func, or_, select
from sqlalchemy.orm import Session

from ..catalog.compare import compare, report_markdown
from ..catalog.seed import completeness
from ..core import audit
from ..core.authz import require_project
from ..core.errors import Conflict, Forbidden, NotFound, Unprocessable
from ..db import models as M
from ..kernel import schema as S
from .deps import current_user, get_db

router = APIRouter(tags=["catalog"])
SPEC_FILTER = re.compile(r"^([a-z0-9_]{1,40}):(-?\d+(?:\.\d+)?)?\.\.(-?\d+(?:\.\d+)?)?$")


def _orgs(db: Session, user: M.User) -> list[uuid.UUID]:
    return list(db.scalars(select(M.Membership.org_id).where(M.Membership.user_id == user.id)))


def _visible(db: Session, user: M.User):
    return or_(M.ComponentCatalogItem.org_id.is_(None), M.ComponentCatalogItem.org_id.in_(_orgs(db, user)))


def item_out(i: M.ComponentCatalogItem, lic: M.AssetLicense | None = None, full: bool = False) -> dict[str, Any]:
    out = {
        "id": str(i.id), "name": i.name, "category": i.category, "subcategory": i.subcategory, "domain": i.domain,
        "asset_class": i.asset_class, "manufacturer": i.manufacturer, "part_number": i.part_number, "standard": i.standard,
        "material": i.material, "mass_kg": i.mass_kg, "mass_source": i.mass_source, "mass_confidence": i.mass_confidence,
        "verification_status": i.verification_status, "completeness": i.completeness, "safety_critical": i.safety_critical,
        "tags": i.tags, "has_cad": bool(i.cad_content or i.cad_blob_key), "image_attribution": i.image_attribution,
        "source_url": i.source_url, "source_retrieved_at": i.source_retrieved_at.isoformat() if i.source_retrieved_at else None,
        "org_scoped": i.org_id is not None, "license": lic.key if lic else None,
    }
    if full:
        out.update({"description": i.description, "specs": i.specs, "spec_source": i.spec_source, "units": i.units,
                    "measurement_basis": i.measurement_basis, "compatibility_notes": i.compatibility_notes, "revision": i.revision,
                    "cad_format": i.cad_format, "material_source": i.material_source,
                    "license_detail": {"name": lic.name, "spdx_id": lic.spdx_id, "url": lic.url,
                                       "redistribution_allowed": lic.redistribution_allowed,
                                       "attribution_required": lic.attribution_required,
                                       "commercial_use_allowed": lic.commercial_use_allowed, "notes": lic.notes} if lic else None,
                    "created_at": i.created_at.isoformat(), "updated_at": i.updated_at.isoformat()})
    return out


def _get(db: Session, user: M.User, item_id: str) -> M.ComponentCatalogItem:
    try:
        iid = uuid.UUID(item_id)
    except ValueError as exc:
        raise NotFound("Catalog item not found") from exc
    i = db.get(M.ComponentCatalogItem, iid)
    if i is None or (i.org_id is not None and i.org_id not in _orgs(db, user)):
        raise NotFound("Catalog item not found")
    return i


@router.get("/catalog/items")
def search(q: str | None = Query(None, max_length=200), category: str | None = None, domain: str | None = None,
           asset_class: str | None = None, verification: str | None = None, manufacturer: str | None = None,
           material: str | None = None, has_cad: bool | None = None, license: str | None = None,
           min_completeness: float | None = Query(None, ge=0, le=1), mass_min: float | None = None,
           mass_max: float | None = None, tag: str | None = None, spec: list[str] = Query(default_factory=list),
           sort: Literal["relevance", "name", "completeness", "updated"] = "relevance",
           limit: int = Query(50, ge=1, le=200), offset: int = Query(0, ge=0),
           user: M.User = Depends(current_user), db: Session = Depends(get_db)) -> dict[str, Any]:
    C = M.ComponentCatalogItem
    stmt = select(C, M.AssetLicense).outerjoin(M.AssetLicense, M.AssetLicense.id == C.license_id).where(_visible(db, user))
    rank = None
    if q:
        tsq = func.plainto_tsquery("english", q)
        like = f"%{q.replace('%', '').replace('_', '')}%"
        stmt = stmt.where(or_(C.search.op("@@")(tsq), C.part_number.ilike(like), C.name.ilike(like)))
        rank = func.ts_rank(C.search, tsq)
    for col, val in ((C.category, category), (C.domain, domain), (C.asset_class, asset_class),
                     (C.verification_status, verification)):
        if val:
            stmt = stmt.where(col == val)
    if manufacturer:
        stmt = stmt.where(C.manufacturer.ilike(f"%{manufacturer}%"))
    if material:
        stmt = stmt.where(C.material.ilike(f"%{material}%"))
    if has_cad is not None:
        stmt = stmt.where((C.cad_content.isnot(None) | C.cad_blob_key.isnot(None)) if has_cad else
                          (C.cad_content.is_(None) & C.cad_blob_key.is_(None)))
    if license:
        stmt = stmt.where(M.AssetLicense.key == license)
    if min_completeness is not None:
        stmt = stmt.where(C.completeness >= min_completeness)
    if mass_min is not None:
        stmt = stmt.where(C.mass_kg >= mass_min)
    if mass_max is not None:
        stmt = stmt.where(C.mass_kg <= mass_max)
    if tag:
        stmt = stmt.where(C.tags.any(tag))
    for sf in spec:
        m = SPEC_FILTER.match(sf)
        if not m:
            raise Unprocessable(f"Invalid spec filter {sf!r}; use key:min..max (e.g. bore_d:20..30)")
        key, lo, hi = m.groups()
        v = C.specs[key]["value"]
        num = case((func.jsonb_typeof(v) == "number", v.astext.cast(Float)), else_=None)
        cond = [num.isnot(None)]
        if lo is not None:
            cond.append(num >= float(lo))
        if hi is not None:
            cond.append(num <= float(hi))
        stmt = stmt.where(and_(*cond))
    total = db.scalar(select(func.count()).select_from(stmt.subquery()))
    order = {"name": [C.name], "completeness": [C.completeness.desc(), C.name], "updated": [C.updated_at.desc()]}.get(sort)
    if order is None:
        order = [rank.desc(), C.name] if rank is not None else [C.category, C.name]
    rows = db.execute(stmt.order_by(*order).limit(limit).offset(offset)).all()
    return {"items": [item_out(i, lic) for i, lic in rows], "total": total,
            "next_offset": offset + limit if offset + limit < (total or 0) else None}


@router.get("/catalog/facets")
def facets(user: M.User = Depends(current_user), db: Session = Depends(get_db)) -> dict[str, Any]:
    C = M.ComponentCatalogItem
    out = {}
    for name, col in (("category", C.category), ("domain", C.domain), ("asset_class", C.asset_class),
                      ("verification_status", C.verification_status), ("manufacturer", C.manufacturer)):
        out[name] = [{"value": v, "count": n} for v, n in db.execute(
            select(col, func.count()).where(_visible(db, user)).group_by(col).order_by(func.count().desc())) if v]
    return out


@router.get("/catalog/items/{item_id}")
def get_item(item_id: str, user: M.User = Depends(current_user), db: Session = Depends(get_db)) -> dict[str, Any]:
    i = _get(db, user, item_id)
    lic = db.get(M.AssetLicense, i.license_id) if i.license_id else None
    fav = db.scalar(select(M.Favorite.id).where(M.Favorite.user_id == user.id, M.Favorite.item_id == i.id)) is not None
    return {"item": item_out(i, lic, full=True), "favorite": fav, "similar": similar(db, user, i)}


def similar(db: Session, user: M.User, i: M.ComponentCatalogItem, n: int = 6) -> list[dict[str, Any]]:
    """Same category, ranked by normalised distance over shared numeric specs."""
    C = M.ComponentCatalogItem
    cands = db.scalars(select(C).where(_visible(db, user), C.category == i.category, C.id != i.id).limit(500)).all()
    base = {k: v["value"] for k, v in (i.specs or {}).items() if isinstance(v.get("value"), (int, float))}
    scored = []
    for c in cands:
        other = {k: v["value"] for k, v in (c.specs or {}).items() if isinstance(v.get("value"), (int, float))}
        shared = base.keys() & other.keys()
        if not shared:
            continue
        d = sum(abs(base[k] - other[k]) / max(abs(base[k]), abs(other[k]), 1e-9) for k in shared) / len(shared)
        scored.append((d, c, sorted(shared)))
    scored.sort(key=lambda t: t[0])
    return [{**item_out(c), "distance": round(d, 4), "compared_on": keys} for d, c, keys in scored[:n]]


class CompareIn(BaseModel):
    ids: list[str] = Field(min_length=2, max_length=8)


def _compare_items(db: Session, user: M.User, ids: list[str]) -> list[dict[str, Any]]:
    items = []
    for x in ids:
        i = _get(db, user, x)
        lic = db.get(M.AssetLicense, i.license_id) if i.license_id else None
        items.append(item_out(i, lic, full=True))
    return items


@router.post("/catalog/compare")
def compare_items(body: CompareIn, user: M.User = Depends(current_user), db: Session = Depends(get_db)) -> dict[str, Any]:
    return compare(_compare_items(db, user, body.ids))


@router.post("/catalog/compare/report")
def compare_report(body: CompareIn, project_id: str | None = None, user: M.User = Depends(current_user),
                   db: Session = Depends(get_db)) -> Response:
    cmp = compare(_compare_items(db, user, body.ids))
    md = report_markdown(cmp)
    if project_id:
        p, _ = require_project(db, user, project_id)
        audit.record(db, "catalog.compare_report", user, project_id=p.id, details={"items": body.ids})
    return Response(md, media_type="text/markdown; charset=utf-8",
                    headers={"Content-Disposition": 'attachment; filename="component-comparison.md"'})


@router.get("/catalog/items/{item_id}/model")
def item_model(item_id: str, quality: Literal["draft", "normal", "fine"] = "normal", user: M.User = Depends(current_user),
               db: Session = Depends(get_db)) -> dict[str, Any]:
    i = _get(db, user, item_id)
    if not i.cad_content:
        raise NotFound("No 3D geometry is available for this item" +
                       (" (licence does not permit redistribution; follow the source link)" if i.cad_blob_key is None and
                        i.source_url else ""))
    from ..assembly.service import part_model

    return part_model({"content": i.cad_content, "prefix": "catalog/", "part_key": f"cat:{i.id}"}, quality)


@router.post("/catalog/items/{item_id}/favorite")
def favorite(item_id: str, user: M.User = Depends(current_user), db: Session = Depends(get_db)) -> dict[str, Any]:
    i = _get(db, user, item_id)
    if not db.scalar(select(M.Favorite.id).where(M.Favorite.user_id == user.id, M.Favorite.item_id == i.id)):
        db.add(M.Favorite(user_id=user.id, item_id=i.id))
    return {"favorite": True}


@router.delete("/catalog/items/{item_id}/favorite")
def unfavorite(item_id: str, user: M.User = Depends(current_user), db: Session = Depends(get_db)) -> dict[str, Any]:
    i = _get(db, user, item_id)
    f = db.scalar(select(M.Favorite).where(M.Favorite.user_id == user.id, M.Favorite.item_id == i.id))
    if f:
        db.delete(f)
    return {"favorite": False}


@router.get("/catalog/favorites")
def favorites(user: M.User = Depends(current_user), db: Session = Depends(get_db)) -> dict[str, Any]:
    rows = db.scalars(select(M.ComponentCatalogItem).join(M.Favorite, M.Favorite.item_id == M.ComponentCatalogItem.id)
                      .where(M.Favorite.user_id == user.id).order_by(M.Favorite.created_at.desc())).all()
    return {"items": [item_out(i) for i in rows]}


class ProjectComponentIn(BaseModel):
    item_id: str
    note: str = Field(default="", max_length=2000)


@router.get("/projects/{project_id}/components")
def project_components(project_id: str, user: M.User = Depends(current_user), db: Session = Depends(get_db)) -> dict[str, Any]:
    p, _ = require_project(db, user, project_id)
    rows = db.execute(select(M.ProjectComponent, M.ComponentCatalogItem)
                      .join(M.ComponentCatalogItem, M.ComponentCatalogItem.id == M.ProjectComponent.item_id)
                      .where(M.ProjectComponent.project_id == p.id)).all()
    return {"items": [{**item_out(i), "note": pc.note, "added_at": pc.created_at.isoformat()} for pc, i in rows]}


@router.post("/projects/{project_id}/components", status_code=201)
def add_project_component(project_id: str, body: ProjectComponentIn, user: M.User = Depends(current_user),
                          db: Session = Depends(get_db)) -> dict[str, Any]:
    p, _ = require_project(db, user, project_id, "edit")
    i = _get(db, user, body.item_id)
    if db.scalar(select(M.ProjectComponent.id).where(M.ProjectComponent.project_id == p.id, M.ProjectComponent.item_id == i.id)):
        raise Conflict("Component already in project list")
    db.add(M.ProjectComponent(project_id=p.id, item_id=i.id, note=body.note, added_by=user.id))
    audit.record(db, "project.component_add", user, project_id=p.id, target_type="catalog_item", target_id=i.id)
    return {"ok": True}


@router.delete("/projects/{project_id}/components/{item_id}")
def remove_project_component(project_id: str, item_id: str, user: M.User = Depends(current_user),
                             db: Session = Depends(get_db)) -> dict[str, Any]:
    p, _ = require_project(db, user, project_id, "edit")
    row = db.scalar(select(M.ProjectComponent).where(M.ProjectComponent.project_id == p.id,
                                                     M.ProjectComponent.item_id == _get(db, user, item_id).id))
    if row is None:
        raise NotFound("Not in project list")
    db.delete(row)
    return {"ok": True}


class SpecValue(BaseModel):
    value: float | int | str | None
    unit: str | None = None
    basis: Literal["standard", "manufacturer", "measured", "computed", "nominal", "missing"] = "manufacturer"
    source: str | None = Field(default=None, max_length=500)
    verified: bool = False
    note: str = Field(default="", max_length=500)


class ItemIn(BaseModel):
    """Create a catalog record. Provenance is mandatory for externally sourced classes."""

    name: str = Field(min_length=2, max_length=200)
    category: str = Field(pattern=r"^[a-z0-9_]{2,64}$")
    subcategory: str | None = Field(default=None, pattern=r"^[a-z0-9_]{2,64}$")
    domain: str = Field(pattern=r"^[a-z0-9_]{2,64}$")
    asset_class: Literal["verified_public", "manufacturer", "user_upload"]
    org_id: uuid.UUID | None = None
    manufacturer: str | None = Field(default=None, max_length=120)
    part_number: str | None = Field(default=None, max_length=120)
    standard: str | None = Field(default=None, max_length=120)
    description: str = Field(default="", max_length=5000)
    specs: dict[str, SpecValue] = Field(default_factory=dict)
    material: str | None = Field(default=None, max_length=120)
    mass_kg: float | None = Field(default=None, gt=0)
    mass_source: str | None = Field(default=None, max_length=300)
    source_url: HttpUrl | None = None
    source_retrieved_at: datetime | None = None
    license_key: str
    compatibility_notes: str = Field(default="", max_length=5000)
    safety_critical: bool = False
    tags: list[str] = Field(default_factory=list, max_length=30)
    cad_content: dict[str, Any] | None = None

    @model_validator(mode="after")
    def _provenance(self) -> "ItemIn":
        if self.asset_class in ("verified_public", "manufacturer"):
            if not self.source_url or not self.source_retrieved_at:
                raise ValueError("source_url and source_retrieved_at are required for externally sourced items")
        if self.asset_class == "manufacturer" and not (self.manufacturer and self.part_number):
            raise ValueError("manufacturer and part_number are required for manufacturer records")
        if self.mass_kg is not None and not self.mass_source:
            raise ValueError("mass_source is required when mass_kg is given")
        for k, v in self.specs.items():
            if v.verified and not v.source:
                raise ValueError(f"spec {k}: verified values must cite a source")
        return self


@router.post("/catalog/items", status_code=201)
def create_item(body: ItemIn, user: M.User = Depends(current_user), db: Session = Depends(get_db)) -> dict[str, Any]:
    if body.org_id is None and not user.is_admin:
        raise Forbidden("Only administrators can publish to the global catalog; set org_id for an organisation catalog")
    if body.org_id is not None:
        role = db.scalar(select(M.Membership.role).where(M.Membership.org_id == body.org_id, M.Membership.user_id == user.id))
        if role is None:
            raise NotFound("Organisation not found")
    lic = db.scalar(select(M.AssetLicense).where(M.AssetLicense.key == body.license_key))
    if lic is None:
        raise Unprocessable("Unknown licence")
    if body.cad_content is not None:
        try:
            body.cad_content = S.Document.model_validate(body.cad_content).model_dump(mode="json")
        except ValidationError as exc:
            raise Unprocessable("Invalid CAD content", code="invalid_document") from exc
        if any(f.get("type") == "import" for f in body.cad_content.get("features", [])):
            raise Unprocessable("Catalog geometry must be self-contained (import features are not allowed)")
    specs = {k: v.model_dump() for k, v in body.specs.items()}
    verified = body.asset_class in ("verified_public", "manufacturer") and all(
        v["verified"] for v in specs.values() if v["value"] is not None)
    row = M.ComponentCatalogItem(
        org_id=body.org_id, name=body.name, category=body.category, subcategory=body.subcategory, domain=body.domain,
        asset_class=body.asset_class, manufacturer=body.manufacturer, part_number=body.part_number, standard=body.standard,
        description=body.description, specs=specs, material=body.material, mass_kg=body.mass_kg, mass_source=body.mass_source,
        mass_confidence="stated" if body.mass_kg else None, source_url=str(body.source_url) if body.source_url else None,
        source_retrieved_at=body.source_retrieved_at, license_id=lic.id, compatibility_notes=body.compatibility_notes,
        safety_critical=body.safety_critical, tags=body.tags, cad_content=body.cad_content,
        cad_format="autoforge-parametric" if body.cad_content else None, completeness=completeness(body.category, specs),
        verification_status="source_cited" if verified else ("user_supplied" if body.asset_class == "user_upload" else "partially_sourced"),
        image_attribution="Rendered from catalog geometry" if body.cad_content else None, created_by=user.id)
    db.add(row)
    db.flush()
    audit.record(db, "catalog.item_create", user, org_id=body.org_id, target_type="catalog_item", target_id=row.id,
                 details={"name": body.name, "asset_class": body.asset_class, "source_url": row.source_url})
    return {"item": item_out(row, lic, full=True)}


@router.delete("/catalog/items/{item_id}")
def delete_item(item_id: str, user: M.User = Depends(current_user), db: Session = Depends(get_db)) -> dict[str, Any]:
    i = _get(db, user, item_id)
    if i.org_id is None and not user.is_admin:
        raise Forbidden("Only administrators can remove global catalog items")
    if i.org_id is not None:
        role = db.scalar(select(M.Membership.role).where(M.Membership.org_id == i.org_id, M.Membership.user_id == user.id))
        if role not in ("owner", "admin") and i.created_by != user.id:
            raise Forbidden("Only the creator or an organisation admin can remove this item")
    db.delete(i)
    audit.record(db, "catalog.item_delete", user, org_id=i.org_id, target_type="catalog_item", target_id=item_id)
    return {"ok": True}


@router.get("/materials")
def materials(user: M.User = Depends(current_user), db: Session = Depends(get_db)) -> dict[str, Any]:
    rows = db.scalars(select(M.Material).order_by(M.Material.category, M.Material.name)).all()
    return {"items": [{c.name: getattr(m, c.name) if c.name != "id" else str(m.id) for c in M.Material.__table__.columns}
                      for m in rows]}


@router.get("/licenses")
def licenses(user: M.User = Depends(current_user), db: Session = Depends(get_db)) -> dict[str, Any]:
    rows = db.scalars(select(M.AssetLicense).order_by(M.AssetLicense.name)).all()
    return {"items": [{"key": l.key, "name": l.name, "spdx_id": l.spdx_id, "url": l.url,
                       "redistribution_allowed": l.redistribution_allowed, "attribution_required": l.attribution_required,
                       "commercial_use_allowed": l.commercial_use_allowed, "notes": l.notes} for l in rows]}
