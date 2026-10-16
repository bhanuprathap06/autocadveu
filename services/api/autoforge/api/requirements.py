"""Engineering problem statements, requirements, AI-assisted concepts and proposals.

Approval checkpoints:
1. Extracted requirements are *proposed*; a human approves or rejects each.
2. The problem is approved only when no proposed requirement remains.
3. A CAD proposal is built and kernel-validated but NOT saved until the user
   explicitly applies it.
"""

from __future__ import annotations

import uuid
from datetime import datetime, timezone
from typing import Any, Literal

from fastapi import APIRouter, Depends
from pydantic import BaseModel, Field, ValidationError
from sqlalchemy import delete, func, select
from sqlalchemy.orm import Session

from ..ai.providers import AIError, get_provider, provider_status
from ..ai.schemas import CadProposal
from ..catalog.templates import DOMAINS, TEMPLATES, build_template
from ..core import audit, ratelimit
from ..core.authz import require_document, require_project
from ..core.errors import ApiError, Conflict, NotFound, Unprocessable
from ..db import models as M
from ..kernel import schema as S
from ..kernel_pool import KernelError, get_pool
from .deps import current_user, get_db

router = APIRouter(tags=["requirements"])


class ProblemIn(BaseModel):
    title: str = Field(min_length=1, max_length=200)
    statement: str = Field(min_length=10, max_length=20000)
    domain: str | None = None


class ExtractIn(BaseModel):
    allow_external_ai: bool = False


class RequirementIn(BaseModel):
    kind: Literal["functional", "constraint", "target_metric", "assumption", "open_question"]
    text: str = Field(min_length=1, max_length=2000)
    metric: str | None = Field(default=None, max_length=120)
    comparator: Literal["<=", ">=", "=", "<", ">"] | None = None
    target_value: float | None = None
    unit: str | None = Field(default=None, max_length=32)
    problem_id: str | None = None


class RequirementPatch(BaseModel):
    status: Literal["proposed", "approved", "rejected"] | None = None
    text: str | None = Field(default=None, max_length=2000)
    metric: str | None = None
    comparator: Literal["<=", ">=", "=", "<", ">"] | None = None
    target_value: float | None = None
    unit: str | None = None


class ProposalIn(BaseModel):
    template: str
    allow_external_ai: bool = False


class ApplyIn(BaseModel):
    proposal: dict[str, Any]


class VerifyIn(BaseModel):
    document_id: str | None = None
    simulation_id: str | None = None


def req_out(r: M.Requirement) -> dict[str, Any]:
    return {"id": str(r.id), "problem_id": str(r.problem_id) if r.problem_id else None, "kind": r.kind, "text": r.text,
            "metric": r.metric, "comparator": r.comparator, "target_value": r.target_value, "unit": r.unit, "source": r.source,
            "claim_class": r.claim_class, "status": r.status, "verification": r.verification,
            "created_at": r.created_at.isoformat()}


def prob_out(p: M.EngineeringProblem, reqs: list[M.Requirement] | None = None) -> dict[str, Any]:
    out = {"id": str(p.id), "project_id": str(p.project_id), "title": p.title, "statement": p.statement, "domain": p.domain,
           "status": p.status, "extraction": p.extraction, "extraction_provider": p.extraction_provider,
           "created_at": p.created_at.isoformat(), "approved_at": p.approved_at.isoformat() if p.approved_at else None}
    if reqs is not None:
        out["requirements"] = [req_out(r) for r in reqs]
    return out


def _problem(db: Session, user: M.User, pid: str, cap: str = "view") -> tuple[M.EngineeringProblem, M.Project]:
    try:
        u = uuid.UUID(pid)
    except ValueError as exc:
        raise NotFound("Problem not found") from exc
    p = db.get(M.EngineeringProblem, u)
    if p is None:
        raise NotFound("Problem not found")
    try:
        project, _ = require_project(db, user, p.project_id, cap)
    except NotFound as exc:
        raise NotFound("Problem not found") from exc
    return p, project


def _reqs(db: Session, problem_id: uuid.UUID) -> list[M.Requirement]:
    return list(db.scalars(select(M.Requirement).where(M.Requirement.problem_id == problem_id).order_by(M.Requirement.created_at)))


@router.get("/ai/status")
def ai_status(user: M.User = Depends(current_user)) -> dict[str, Any]:
    return provider_status()


@router.post("/projects/{project_id}/problems", status_code=201)
def create_problem(project_id: str, body: ProblemIn, user: M.User = Depends(current_user), db: Session = Depends(get_db)
                   ) -> dict[str, Any]:
    project, _ = require_project(db, user, project_id, "edit")
    if body.domain and body.domain not in DOMAINS:
        raise Unprocessable("Unknown domain")
    p = M.EngineeringProblem(project_id=project.id, title=body.title, statement=body.statement, domain=body.domain,
                             created_by=user.id)
    db.add(p)
    db.flush()
    audit.record(db, "problem.create", user, project_id=project.id, target_type="problem", target_id=p.id)
    return {"problem": prob_out(p, [])}


@router.get("/projects/{project_id}/problems")
def list_problems(project_id: str, user: M.User = Depends(current_user), db: Session = Depends(get_db)) -> dict[str, Any]:
    project, _ = require_project(db, user, project_id)
    rows = db.scalars(select(M.EngineeringProblem).where(M.EngineeringProblem.project_id == project.id)
                      .order_by(M.EngineeringProblem.created_at.desc())).all()
    return {"items": [prob_out(p, _reqs(db, p.id)) for p in rows]}


@router.get("/problems/{problem_id}")
def get_problem(problem_id: str, user: M.User = Depends(current_user), db: Session = Depends(get_db)) -> dict[str, Any]:
    p, _ = _problem(db, user, problem_id)
    return {"problem": prob_out(p, _reqs(db, p.id))}


@router.post("/problems/{problem_id}/extract")
def extract(problem_id: str, body: ExtractIn, user: M.User = Depends(current_user), db: Session = Depends(get_db)) -> dict[str, Any]:
    p, project = _problem(db, user, problem_id, "edit")
    ratelimit.hit(f"ai:{user.id}", 60, 3600)
    if p.status == "approved":
        raise Conflict("Problem is already approved; create a new problem revision to re-extract")
    provider = get_provider(body.allow_external_ai)
    try:
        ext = provider.extract(p.statement, p.domain)
    except AIError as exc:
        raise ApiError(exc.args[0], code=exc.code, status=502) from exc
    db.execute(delete(M.Requirement).where(M.Requirement.problem_id == p.id, M.Requirement.status == "proposed",
                                           M.Requirement.source != "user"))
    source = "ai" if provider.external else "heuristic"
    for r in ext.requirements:
        db.add(M.Requirement(project_id=project.id, problem_id=p.id, kind=r.kind, text=r.text, metric=r.metric,
                             comparator=r.comparator, target_value=r.target_value, unit=r.unit, source=source,
                             claim_class=r.claim_class, status="proposed", created_by=user.id))
    p.extraction = {**ext.model_dump(), "provider": provider.name, "at": datetime.now(timezone.utc).isoformat()}
    p.extraction_provider = provider.name
    if not p.domain and ext.domain_guess in DOMAINS:
        p.domain = ext.domain_guess
    p.status = "extracted"
    db.flush()
    audit.record(db, "problem.extract", user, project_id=project.id, target_type="problem", target_id=p.id,
                 details={"provider": provider.name, "external": provider.external, "count": len(ext.requirements)})
    return {"problem": prob_out(p, _reqs(db, p.id))}


@router.post("/projects/{project_id}/requirements", status_code=201)
def add_requirement(project_id: str, body: RequirementIn, user: M.User = Depends(current_user), db: Session = Depends(get_db)
                    ) -> dict[str, Any]:
    project, _ = require_project(db, user, project_id, "edit")
    pid = None
    if body.problem_id:
        prob, pproj = _problem(db, user, body.problem_id, "edit")
        if pproj.id != project.id:
            raise Unprocessable("Problem belongs to another project")
        pid = prob.id
    r = M.Requirement(project_id=project.id, problem_id=pid, kind=body.kind, text=body.text, metric=body.metric,
                      comparator=body.comparator, target_value=body.target_value, unit=body.unit, source="user",
                      claim_class="source_supported_fact", status="approved", created_by=user.id)
    db.add(r)
    db.flush()
    return {"requirement": req_out(r)}


def _req(db: Session, user: M.User, rid: str, cap: str) -> tuple[M.Requirement, M.Project]:
    try:
        u = uuid.UUID(rid)
    except ValueError as exc:
        raise NotFound("Requirement not found") from exc
    r = db.get(M.Requirement, u)
    if r is None:
        raise NotFound("Requirement not found")
    try:
        project, _ = require_project(db, user, r.project_id, cap)
    except NotFound as exc:
        raise NotFound("Requirement not found") from exc
    return r, project


@router.patch("/requirements/{req_id}")
def patch_requirement(req_id: str, body: RequirementPatch, user: M.User = Depends(current_user), db: Session = Depends(get_db)
                      ) -> dict[str, Any]:
    r, project = _req(db, user, req_id, "edit")
    changes = body.model_dump(exclude_unset=True)
    for k, v in changes.items():
        setattr(r, k, v)
    if set(changes) - {"status"} and r.source != "user":
        r.source = "user"  # edited by a human: no longer purely machine-generated
        r.claim_class = "source_supported_fact"
    audit.record(db, "requirement.update", user, project_id=project.id, target_type="requirement", target_id=r.id, details=changes)
    return {"requirement": req_out(r)}


@router.post("/problems/{problem_id}/approve")
def approve(problem_id: str, user: M.User = Depends(current_user), db: Session = Depends(get_db)) -> dict[str, Any]:
    p, project = _problem(db, user, problem_id, "edit")
    pending = db.scalar(select(func.count()).where(M.Requirement.problem_id == p.id, M.Requirement.status == "proposed"))
    if pending:
        raise Conflict(f"{pending} requirement(s) still need review (approve or reject each)", code="requirements_pending")
    if not db.scalar(select(func.count()).where(M.Requirement.problem_id == p.id, M.Requirement.status == "approved")):
        raise Conflict("Approve at least one requirement first", code="no_requirements")
    p.status, p.approved_by, p.approved_at = "approved", user.id, datetime.now(timezone.utc)
    audit.record(db, "problem.approve", user, project_id=project.id, target_type="problem", target_id=p.id)
    return {"problem": prob_out(p, _reqs(db, p.id))}


def _approved(db: Session, p: M.EngineeringProblem) -> list[dict[str, Any]]:
    return [req_out(r) for r in _reqs(db, p.id) if r.status == "approved"]


@router.post("/problems/{problem_id}/concepts")
def concepts(problem_id: str, body: ExtractIn, user: M.User = Depends(current_user), db: Session = Depends(get_db)) -> dict[str, Any]:
    p, project = _problem(db, user, problem_id)
    ratelimit.hit(f"ai:{user.id}", 60, 3600)
    provider = get_provider(body.allow_external_ai)
    try:
        cs = provider.concepts(p.statement, _approved(db, p), p.domain)
    except AIError as exc:
        raise ApiError(exc.args[0], code=exc.code, status=502) from exc
    from .catalog_routes import _visible, item_out

    C = M.ComponentCatalogItem
    out = []
    for c in cs.concepts:
        if c.template and c.template not in TEMPLATES:
            c.template = None  # never surface a template the registry does not have
        refs = []
        for q in c.catalog_queries[:3]:
            tsq = func.plainto_tsquery("english", q)
            rows = db.scalars(select(C).where(_visible(db, user), C.search.op("@@")(tsq)).limit(5)).all()
            refs += [item_out(i) for i in rows if all(i.id != uuid.UUID(x["id"]) for x in refs)]
        out.append({**c.model_dump(), "catalog_matches": refs[:8]})
    return {"provider": provider.name, "concepts": out,
            "note": "Concepts are suggestions for review. Claims are classified; none constitutes verification."}


def _build_proposal(prop: CadProposal, db: Session) -> dict[str, Any]:
    if prop.template not in TEMPLATES:
        raise Unprocessable(f"Unknown template {prop.template!r}", code="invalid_proposal")
    doc = build_template(prop.template)
    names = {p.name for p in doc.parameters}
    unknown = [ps.name for ps in prop.parameters if ps.name not in names]
    if unknown:
        raise Unprocessable(f"Proposal sets unknown parameters {unknown}", code="invalid_proposal")
    for ps in prop.parameters:
        for p in doc.parameters:
            if p.name == ps.name:
                p.expression = ps.expression
    if prop.material_key:
        mat = db.scalar(select(M.Material).where(M.Material.key == prop.material_key))
        if mat is None:
            raise Unprocessable(f"Unknown material {prop.material_key!r}", code="invalid_proposal")
        for body in list(doc.materials) or []:
            doc.materials[body] = S.MaterialAssignment(
                name=mat.name, density_kg_m3=mat.density_kg_m3, youngs_modulus_mpa=mat.youngs_modulus_mpa,
                poisson_ratio=mat.poisson_ratio, yield_strength_mpa=mat.yield_strength_mpa,
                thermal_conductivity_w_mk=mat.thermal_conductivity_w_mk, specific_heat_j_kgk=mat.specific_heat_j_kgk,
                source=mat.source[:500], material_id=mat.key)
    content = S.Document.model_validate(doc.model_dump()).model_dump(mode="json")
    try:
        result = get_pool().call("recompute", doc=content, tessellate=False)
    except KernelError as exc:
        return {"content": content, "valid": False, "errors": [exc.message]}
    errors = [f"{f['id']} ({f['type']}): {f['message']}" for f in result["features"] if f["status"] == "error"]
    errors += [f"parameter {k}: {v}" for k, v in result["parameter_errors"].items()]
    return {"content": content, "valid": not errors, "errors": errors,
            "bodies": [{"name": b["name"], "mass": b.get("mass")} for b in result["bodies"]]}


@router.post("/problems/{problem_id}/proposal")
def proposal(problem_id: str, body: ProposalIn, user: M.User = Depends(current_user), db: Session = Depends(get_db)) -> dict[str, Any]:
    p, project = _problem(db, user, problem_id, "edit")
    ratelimit.hit(f"ai:{user.id}", 60, 3600)
    if body.template not in TEMPLATES:
        raise Unprocessable("Unknown template")
    provider = get_provider(body.allow_external_ai)
    tdoc = build_template(body.template)
    tparams = [{"name": x.name, "expression": x.expression, "description": x.description} for x in tdoc.parameters]
    mats = list(db.scalars(select(M.Material.key)))
    try:
        prop = provider.propose(p.statement, _approved(db, p), body.template, tparams, mats)
    except AIError as exc:
        raise ApiError(exc.args[0], code=exc.code, status=502) from exc
    built = _build_proposal(prop, db)
    audit.record(db, "problem.proposal", user, project_id=project.id, target_type="problem", target_id=p.id,
                 details={"provider": provider.name, "template": body.template, "valid": built["valid"]})
    return {"provider": provider.name, "proposal": prop.model_dump(), "validation": {k: v for k, v in built.items() if k != "content"},
            "requires_approval": True}


@router.post("/problems/{problem_id}/proposal/apply", status_code=201)
def apply_proposal(problem_id: str, body: ApplyIn, user: M.User = Depends(current_user), db: Session = Depends(get_db)
                   ) -> dict[str, Any]:
    p, project = _problem(db, user, problem_id, "edit")
    try:
        prop = CadProposal.model_validate(body.proposal)
    except ValidationError as exc:
        raise Unprocessable("Invalid proposal", code="invalid_proposal") from exc
    built = _build_proposal(prop, db)
    if not built["valid"]:
        raise Unprocessable("Proposal does not regenerate cleanly: " + "; ".join(built["errors"][:3]), code="proposal_invalid",
                            details=built["errors"])
    from .documents import create_document_row, doc_out

    content = built["content"]
    content["metadata"]["problem_id"] = str(p.id)
    content["metadata"]["origin"] = "approved proposal (validated by CAD kernel)"
    doc, rev = create_document_row(db, user, project, prop.name, "part", content,
                                   f"Created from approved proposal for problem '{p.title}'")
    audit.record(db, "problem.proposal_applied", user, project_id=project.id, target_type="document", target_id=doc.id)
    return {"document": doc_out(doc)}


MASS_UNITS = {"kg": 1.0, "g": 1e-3, "t": 1e3, "lb": 0.45359237}
LEN_UNITS = {"mm": 1.0, "cm": 10.0, "m": 1000.0, "in": 25.4}


def _cmp(val: float, comp: str, target: float) -> bool:
    return {"<=": val <= target + 1e-12, ">=": val >= target - 1e-12, "<": val < target, ">": val > target,
            "=": abs(val - target) <= 1e-6 * max(abs(target), 1)}[comp]


@router.post("/requirements/{req_id}/verify")
def verify(req_id: str, body: VerifyIn, user: M.User = Depends(current_user), db: Session = Depends(get_db)) -> dict[str, Any]:
    """Check a numeric requirement against computed evidence (CAD mass/size or a solver result)."""
    r, project = _req(db, user, req_id, "edit")
    if r.kind not in ("target_metric", "constraint") or r.target_value is None or not r.comparator:
        raise Unprocessable("Only numeric requirements with a comparator can be verified automatically")
    evidence: dict[str, Any] = {"checked_at": datetime.now(timezone.utc).isoformat(), "by": str(user.id)}
    value = None
    if body.simulation_id:
        from .simulation import _sim

        sim, job, _ = _sim(db, user, body.simulation_id)
        res = db.scalar(select(M.SimulationResult).where(M.SimulationResult.simulation_id == sim.id))
        if res is None:
            raise Unprocessable("Simulation has no results")
        key = {"stress": "max_von_mises_mpa", "temperature": "max_temperature_c", "length": "max_displacement_mm"}.get(r.metric or "")
        if key is None or key not in res.summary:
            raise Unprocessable(f"Metric {r.metric!r} cannot be checked against this simulation")
        value = float(res.summary[key])
        if key == "max_von_mises_mpa" and r.unit and r.unit.lower() == "gpa":
            value /= 1000
        evidence.update({"type": "simulation", "simulation_id": str(sim.id), "result_key": key, "solver": sim.solver_version,
                         "caveat": "Single load case, linear analysis; see the simulation report for assumptions"})
    elif body.document_id:
        from .documents import _get_rev, model_for_content

        doc, dproj, _ = require_document(db, user, body.document_id)
        if dproj.id != project.id:
            raise Unprocessable("Document belongs to another project")
        rev = _get_rev(db, doc, None)
        m = model_for_content(db, project, doc, rev.content, "draft")
        if r.metric == "mass" and (r.unit or "kg") in MASS_UNITS:
            masses = [b.get("mass", {}).get("mass_kg") for b in m.get("bodies", [])]
            if not masses or any(x is None for x in masses):
                raise Unprocessable("Assign materials to all bodies before verifying mass")
            value = sum(masses) / MASS_UNITS[r.unit or "kg"]
        elif r.metric == "length" and (r.unit or "mm") in LEN_UNITS:
            bbs = [b["bbox"] for b in m.get("bodies", []) if b.get("bbox")]
            lo = [min(b[0][i] for b in bbs) for i in range(3)]
            hi = [max(b[1][i] for b in bbs) for i in range(3)]
            value = max(h - l for h, l in zip(hi, lo)) / LEN_UNITS[r.unit or "mm"]
            evidence["note"] = "Compared against the largest bounding-box extent"
        else:
            raise Unprocessable(f"Metric {r.metric!r} with unit {r.unit!r} cannot be checked against CAD geometry")
        evidence.update({"type": "cad", "document_id": str(doc.id), "revision": rev.number})
    else:
        raise Unprocessable("Provide document_id or simulation_id as evidence")
    passed = _cmp(value, r.comparator, r.target_value)
    r.verification = {**evidence, "value": value, "unit": r.unit, "status": "pass" if passed else "fail",
                      "classification": "calculation"}
    audit.record(db, "requirement.verify", user, project_id=project.id, target_type="requirement", target_id=r.id,
                 details=r.verification)
    return {"requirement": req_out(r)}
