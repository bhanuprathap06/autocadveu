"""Simulation center endpoints."""

from __future__ import annotations

import json
import shutil
import uuid
from typing import Any

from fastapi import APIRouter, Depends, Query
from fastapi.responses import Response
from pydantic import BaseModel, Field, ValidationError
from sqlalchemy import select
from sqlalchemy.orm import Session

from ..core import audit, events, ratelimit
from ..core.authz import require_document, require_project
from ..core.config import get_settings
from ..core.errors import ApiError, NotFound, Unprocessable
from ..core.storage import get_storage
from ..db import models as M
from ..jobs import queue
from ..sim.schema import StaticSetup, bc_faces, parse_setup
from .deps import current_user, get_db
from .documents import _get_rev, model_for_content

router = APIRouter(tags=["simulation"])

CAPABILITIES = [
    {"key": "static", "name": "Linear static structural", "solver": "CalculiX", "status": "implemented_and_verified",
     "limits": "Small displacement, linear elastic isotropic material, single solid body, fixed supports, pressure/force/gravity."},
    {"key": "thermal", "name": "Steady-state heat conduction", "solver": "CalculiX", "status": "implemented_and_verified",
     "limits": "Isotropic conductivity, fixed temperature, heat flux, convection (film) boundary conditions."},
    {"key": "transient_thermal", "name": "Transient thermal", "solver": "CalculiX", "status": "planned", "limits": ""},
    {"key": "thermo_mechanical", "name": "Coupled thermo-mechanical", "solver": "CalculiX", "status": "planned",
     "limits": "Requires separate validation before release."},
    {"key": "modal", "name": "Modal (natural frequencies)", "solver": "CalculiX", "status": "planned", "limits": ""},
    {"key": "contact_nonlinear", "name": "Contact / nonlinear", "solver": "CalculiX", "status": "planned",
     "limits": "Only after verification against benchmarks."},
    {"key": "cfd", "name": "CFD / aerodynamics", "solver": "OpenFOAM (adapter)", "status": "external_integration_required",
     "limits": "No CFD solver is configured. AutoForge never displays CFD-like results without a real solver run."},
    {"key": "kinematics", "name": "Mechanism kinematics", "solver": "AutoForge mate solver", "status": "implemented_with_limitations",
     "limits": "Quasi-static position sweeps; no dynamics (inertia, forces)."},
]


class SimIn(BaseModel):
    document_id: str
    revision: int | None = None
    name: str = Field(min_length=1, max_length=160)
    setup: dict[str, Any]
    idempotency_key: str | None = Field(default=None, min_length=8, max_length=100, pattern=r"^[A-Za-z0-9_\-:.]+$")


def sim_out(sim: M.SimulationJob, job: M.Job, res: M.SimulationResult | None = None, full: bool = False) -> dict[str, Any]:
    out = {"id": str(sim.id), "name": sim.name, "analysis": sim.analysis, "body": sim.body, "document_id": str(sim.document_id),
           "revision": sim.revision_number, "solver": sim.solver, "solver_version": sim.solver_version, "status": job.status,
           "progress": job.progress, "error": job.error, "created_at": sim.created_at.isoformat(),
           "started_at": job.started_at.isoformat() if job.started_at else None,
           "finished_at": job.finished_at.isoformat() if job.finished_at else None, "cancel_requested": job.cancel_requested}
    if full:
        out["setup"] = sim.setup
        out["job"] = {"id": str(job.id), "attempts": job.attempts, "timeout_s": job.timeout_s}
        out["run"] = job.result
        if res is not None:
            out["result"] = {"summary": res.summary, "mesh": res.mesh, "convergence": res.convergence, "warnings": res.warnings,
                             "artifacts": {k: {kk: vv for kk, vv in v.items() if kk != "key"} for k, v in res.artifacts.items()}}
        elif job.result and job.result.get("artifacts"):
            out["result"] = {"artifacts": {k: {kk: vv for kk, vv in v.items() if kk != "key"}
                                           for k, v in job.result["artifacts"].items()}}
    return out


@router.get("/simulation/capabilities")
def capabilities() -> dict[str, Any]:
    ccx = shutil.which(get_settings().ccx_binary) is not None
    return {"items": CAPABILITIES, "calculix_available": ccx}


@router.post("/projects/{project_id}/simulations", status_code=201)
def submit(project_id: str, body: SimIn, user: M.User = Depends(current_user), db: Session = Depends(get_db)) -> dict[str, Any]:
    project, _ = require_project(db, user, project_id, "run_simulation")
    ratelimit.hit(f"sim:{user.id}", 30, 3600)
    doc, dproject, _ = require_document(db, user, body.document_id)
    if dproject.id != project.id or doc.kind != "part":
        raise Unprocessable("Simulations run on part documents of this project", code="invalid_document")
    if body.idempotency_key:
        existing = db.scalar(select(M.Job).where(M.Job.idempotency_key == f"sim:{user.id}:{body.idempotency_key}"))
        if existing is not None:
            sim = db.scalar(select(M.SimulationJob).where(M.SimulationJob.job_id == existing.id))
            return {"simulation": sim_out(sim, existing), "duplicate": True}
    try:
        setup = parse_setup(body.setup)
    except ValidationError as exc:
        raise Unprocessable("Invalid simulation setup", code="invalid_setup",
                            details=[{"loc": list(e["loc"]), "msg": e["msg"]} for e in exc.errors()][:30]) from exc
    rev = _get_rev(db, doc, body.revision)
    model = model_for_content(db, project, doc, rev.content, "draft")
    bodies = {b["name"]: b for b in model.get("bodies", [])}
    if setup.body not in bodies:
        raise Unprocessable(f"Body {setup.body!r} does not exist in revision {rev.number}", code="invalid_setup")
    names = {n for f in bodies[setup.body]["faces"] for n in f["names"]}
    missing = [f for f in bc_faces(setup) if f not in names]
    if missing:
        raise Unprocessable(f"Boundary-condition faces not found on {setup.body}: {missing}", code="invalid_setup")
    if model.get("ok") is False:
        failed = [f["id"] for f in model.get("features", []) if f["status"] == "error"]
        raise Unprocessable(f"Model has failing features {failed}; fix them before simulating", code="model_has_errors")
    s = get_settings()
    job, _ = queue.enqueue(db, "simulation", {"content": rev.content, "setup": setup.model_dump(mode="json"),
                                              "blob_prefix": f"projects/{project.id}/", "timeout_s": s.sim_max_runtime_s},
                           project_id=project.id, user_id=user.id,
                           idempotency_key=f"sim:{user.id}:{body.idempotency_key}" if body.idempotency_key else None,
                           timeout_s=s.sim_max_runtime_s + 120)
    sim = M.SimulationJob(project_id=project.id, document_id=doc.id, revision_number=rev.number, body=setup.body,
                          analysis=setup.analysis, name=body.name, setup=setup.model_dump(mode="json"), solver="calculix",
                          job_id=job.id, created_by=user.id)
    db.add(sim)
    db.flush()
    audit.record(db, "simulation.submit", user, project_id=project.id, target_type="simulation", target_id=sim.id,
                 details={"document_id": str(doc.id), "revision": rev.number, "analysis": setup.analysis})
    events.publish(f"project:{project.id}", {"type": "job.status", "job_id": str(job.id), "status": "queued"})
    return {"simulation": sim_out(sim, job), "duplicate": False}


@router.get("/projects/{project_id}/simulations")
def list_sims(project_id: str, user: M.User = Depends(current_user), db: Session = Depends(get_db)) -> dict[str, Any]:
    project, _ = require_project(db, user, project_id)
    rows = db.execute(select(M.SimulationJob, M.Job).join(M.Job, M.Job.id == M.SimulationJob.job_id)
                      .where(M.SimulationJob.project_id == project.id).order_by(M.SimulationJob.created_at.desc())).all()
    return {"items": [sim_out(s, j) for s, j in rows]}


def _sim(db: Session, user: M.User, sim_id: str, cap: str = "view") -> tuple[M.SimulationJob, M.Job, M.Project]:
    try:
        sid = uuid.UUID(sim_id)
    except ValueError as exc:
        raise NotFound("Simulation not found") from exc
    sim = db.get(M.SimulationJob, sid)
    if sim is None:
        raise NotFound("Simulation not found")
    try:
        project, _ = require_project(db, user, sim.project_id, cap)
    except NotFound as exc:
        raise NotFound("Simulation not found") from exc
    return sim, db.get(M.Job, sim.job_id), project


@router.get("/simulations/{sim_id}")
def get_sim(sim_id: str, user: M.User = Depends(current_user), db: Session = Depends(get_db)) -> dict[str, Any]:
    sim, job, _ = _sim(db, user, sim_id)
    res = db.scalar(select(M.SimulationResult).where(M.SimulationResult.simulation_id == sim.id))
    return {"simulation": sim_out(sim, job, res, full=True)}


@router.post("/simulations/{sim_id}/cancel")
def cancel(sim_id: str, user: M.User = Depends(current_user), db: Session = Depends(get_db)) -> dict[str, Any]:
    sim, job, project = _sim(db, user, sim_id, "run_simulation")
    st = queue.request_cancel(db, job)
    audit.record(db, "simulation.cancel", user, project_id=project.id, target_type="simulation", target_id=sim.id)
    events.publish(f"project:{project.id}", {"type": "job.status", "job_id": str(job.id), "status": st})
    return {"status": st}


def _artifact(db: Session, sim: M.SimulationJob, job: M.Job, name: str) -> tuple[bytes, dict[str, Any]]:
    res = db.scalar(select(M.SimulationResult).where(M.SimulationResult.simulation_id == sim.id))
    arts = res.artifacts if res else (job.result or {}).get("artifacts", {})
    a = arts.get(name)
    if a is None:
        raise NotFound(f"Artifact {name!r} not available")
    return get_storage().get(a["key"], expected_sha256=a["sha256"]), a


@router.get("/simulations/{sim_id}/field")
def field(sim_id: str, user: M.User = Depends(current_user), db: Session = Depends(get_db)) -> Response:
    sim, job, _ = _sim(db, user, sim_id)
    data, _ = _artifact(db, sim, job, "field.json")
    return Response(data, media_type="application/json", headers={"Cache-Control": "private, max-age=3600"})


@router.get("/simulations/{sim_id}/artifacts/{name}")
def artifact(sim_id: str, name: str, user: M.User = Depends(current_user), db: Session = Depends(get_db)) -> Response:
    if name not in ("model.inp", "model.frd", "model.dat", "solver.log", "field.json"):
        raise NotFound("Unknown artifact")
    sim, job, project = _sim(db, user, sim_id)
    data, a = _artifact(db, sim, job, name)
    audit.record(db, "simulation.artifact_download", user, project_id=project.id, target_type="simulation", target_id=sim.id,
                 details={"artifact": name})
    return Response(data, media_type="application/octet-stream",
                    headers={"Content-Disposition": f'attachment; filename="{sim.name[:40]}-{name}"', "X-Content-SHA256": a["sha256"]})


def report_markdown(sim: M.SimulationJob, job: M.Job, res: M.SimulationResult | None, doc: M.CADDocument) -> str:
    st = sim.setup
    L = [f"# Simulation report: {sim.name}", "", f"- Status: **{job.status}**", f"- Analysis: {sim.analysis}",
         f"- Model: {doc.name}, revision {sim.revision_number}, body `{sim.body}`",
         f"- Solver: {sim.solver_version or 'CalculiX'} ({(job.result or {}).get('solver', {}).get('mesher', 'gmsh')})",
         f"- Submitted: {sim.created_at.isoformat()}  Finished: {job.finished_at.isoformat() if job.finished_at else 'n/a'}", ""]
    m = st["material"]
    L += ["## Material", "", f"- {m['name']} (source: {m.get('source') or 'not stated'})"]
    for k in ("youngs_modulus_mpa", "poisson_ratio", "density_kg_m3", "yield_strength_mpa", "thermal_conductivity_w_mk"):
        if m.get(k) is not None:
            L.append(f"- {k}: {m[k]}")
    L += ["", "## Boundary conditions and loads", ""]
    for group in ("supports", "loads", "temperatures", "fluxes", "convection"):
        for g in st.get(group, []):
            L.append(f"- {group[:-1] if group.endswith('s') else group}: `{json.dumps(g)}`")
    if res is None:
        L += ["", "## Result", "", f"No results: {job.error or 'job not finished'}"]
        return "\n".join(L) + "\n"
    me = res.mesh
    L += ["", "## Mesh", "", f"- {me['elements']} x {me['element_type']}, {me['nodes']} nodes, target size {me['size_mm']:.3g} mm",
          f"- Quality ({me['quality_metric']}): min {me['quality_min']:.3f}, mean {me['quality_mean']:.3f}"]
    L += ["", "## Results", ""] + [f"- {k}: {v}" for k, v in res.summary.items() if k != "units"]
    L.append(f"- Units: {res.summary.get('units')}")
    if res.convergence:
        L += ["", "## Mesh sensitivity", "", f"- Converged within 5%: {res.convergence['converged_5pct']}"]
        L += [f"- {k}: coarse {v['coarse']:.6g}, fine {v['fine']:.6g} ({100 * v['relative_change']:.2f}% change)"
              for k, v in res.convergence["changes"].items()]
    L += ["", "## Warnings", ""] + ([f"- {w}" for w in res.warnings] or ["- none"])
    L += ["", "## Assumptions and limitations", "",
          "- Linear analysis: small displacements, linear elastic (or linear conduction) material, no contact.",
          "- Results depend on the mesh, idealised boundary conditions and material data stated above.",
          "- This report is not a certification of strength, durability or safety. Independent engineering verification is "
          "required before physical implementation."]
    return "\n".join(L) + "\n"


@router.get("/simulations/{sim_id}/report")
def report(sim_id: str, user: M.User = Depends(current_user), db: Session = Depends(get_db)) -> Response:
    sim, job, _ = _sim(db, user, sim_id)
    res = db.scalar(select(M.SimulationResult).where(M.SimulationResult.simulation_id == sim.id))
    md = report_markdown(sim, job, res, db.get(M.CADDocument, sim.document_id))
    return Response(md, media_type="text/markdown; charset=utf-8",
                    headers={"Content-Disposition": f'attachment; filename="simulation-{sim.id}.md"'})
