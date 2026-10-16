"""Engineering design review report and export centre."""

from __future__ import annotations

import io
from datetime import datetime, timezone
from typing import Any, Literal

from fastapi import APIRouter, Depends
from fastapi.responses import Response
from sqlalchemy import select
from sqlalchemy.orm import Session

from ..core import audit
from ..core.authz import require_project
from ..db import models as M
from .deps import current_user, get_db

router = APIRouter(tags=["reports"])


def _fmt(v: Any) -> str:
    if isinstance(v, float):
        return f"{v:.4g}"
    return str(v)


def build_report(db: Session, project: M.Project, user: M.User) -> str:
    L = [f"# Design review report: {project.name}", "",
         f"Generated {datetime.now(timezone.utc):%Y-%m-%d %H:%M} UTC by {user.display_name}. Project domain: {project.domain or 'n/a'}.",
         "", "> This report compiles project records. It does not certify safety, compliance or fitness for purpose. "
             "Every result must be read with the assumptions and limitations stated in its section.", ""]
    problems = db.scalars(select(M.EngineeringProblem).where(M.EngineeringProblem.project_id == project.id)).all()
    L += ["## 1. Problem statements and requirements", ""]
    if not problems:
        L.append("No problem statement recorded.")
    for p in problems:
        L += [f"### {p.title} ({p.status})", "", p.statement, ""]
        if p.extraction_provider:
            L.append(f"_Requirements extracted with {p.extraction_provider}; each item was reviewed by a person before approval._")
        reqs = db.scalars(select(M.Requirement).where(M.Requirement.problem_id == p.id)).all()
        if reqs:
            L += ["", "| Kind | Requirement | Target | Status | Verification |", "|---|---|---|---|---|"]
            for r in reqs:
                tgt = f"{r.metric} {r.comparator} {_fmt(r.target_value)} {r.unit or ''}" if r.target_value is not None else ""
                ver = r.verification or {}
                vtxt = f"{ver.get('status')} ({_fmt(ver.get('value'))} {ver.get('unit') or ''}, {ver.get('type')})" if ver else "not verified"
                L.append(f"| {r.kind} | {r.text.replace('|', '/')} | {tgt} | {r.status} | {vtxt} |")
        L.append("")
    docs = db.scalars(select(M.CADDocument).where(M.CADDocument.project_id == project.id, M.CADDocument.deleted_at.is_(None))).all()
    L += ["## 2. CAD documents", "", "| Document | Kind | Head revision | Regenerates | Bodies / mass |", "|---|---|---|---|---|"]
    for d in docs:
        rev = db.scalar(select(M.CADRevision).where(M.CADRevision.document_id == d.id, M.CADRevision.number == d.head_revision))
        rc = rev.recompute or {} if rev else {}
        bodies = "; ".join(f"{b['name']}: {_fmt((b.get('mass') or {}).get('mass_kg'))} kg" if (b.get("mass") or {}).get("mass_kg")
                           else f"{b['name']}: no material" for b in rc.get("bodies", [])) or "-"
        L.append(f"| {d.name} | {d.kind} | r{d.head_revision} ({rev.created_at:%Y-%m-%d} ) | {'yes' if rc.get('ok') else 'NO'} | {bodies} |")
    L.append("")
    sims = db.execute(select(M.SimulationJob, M.Job).join(M.Job, M.Job.id == M.SimulationJob.job_id)
                      .where(M.SimulationJob.project_id == project.id)).all()
    L += ["## 3. Simulations", ""]
    if not sims:
        L.append("No simulations run.")
    for s, j in sims:
        res = db.scalar(select(M.SimulationResult).where(M.SimulationResult.simulation_id == s.id))
        L += [f"### {s.name} - {s.analysis} - {j.status}", "",
              f"- Model revision r{s.revision_number}, body `{s.body}`, solver {s.solver_version or s.solver}",
              f"- Material: {s.setup['material']['name']} (source: {s.setup['material'].get('source') or 'not stated'})"]
        if res:
            L += [f"- Mesh: {res.mesh['elements']} {res.mesh['element_type']}, min quality {res.mesh['quality_min']:.3f}"]
            L += [f"- {k}: {_fmt(v)}" for k, v in res.summary.items() if isinstance(v, (int, float)) and v is not None]
            if res.convergence:
                L.append(f"- Mesh sensitivity converged within 5%: {res.convergence['converged_5pct']}")
            L += [f"- Warning: {w}" for w in res.warnings]
        elif j.error:
            L.append(f"- Error: {j.error}")
        L.append("")
    drawings = db.scalars(select(M.Drawing).where(M.Drawing.project_id == project.id)).all()
    L += ["## 4. Drawings", ""] + ([f"- {d.name} (version {d.version}, model revision "
                                    f"{'r' + str(d.revision_pin) if d.revision_pin else 'head'})" for d in drawings] or ["None."])
    comps = db.execute(select(M.ComponentCatalogItem).join(M.ProjectComponent, M.ProjectComponent.item_id == M.ComponentCatalogItem.id)
                       .where(M.ProjectComponent.project_id == project.id)).scalars().all()
    L += ["", "## 5. Selected catalog components", ""] + ([f"- {c.name} ({c.asset_class}, {c.verification_status}; source: "
                                                           f"{c.source_url or 'n/a'})" for c in comps] or ["None."])
    files = db.scalars(select(M.StoredFile).where(M.StoredFile.project_id == project.id, M.StoredFile.kind == "export")
                       .order_by(M.StoredFile.created_at.desc()).limit(50)).all()
    L += ["", "## 6. Exports (traceability)", "", "| File | SHA-256 | Created |", "|---|---|---|"]
    L += [f"| {f.filename} | `{f.sha256[:16]}...` | {f.created_at:%Y-%m-%d %H:%M} |" for f in files]
    L += ["", "## 7. Open items", ""]
    open_reqs = db.scalars(select(M.Requirement).where(M.Requirement.project_id == project.id,
                                                       M.Requirement.status.in_(["proposed"]))).all()
    unverified = db.scalars(select(M.Requirement).where(M.Requirement.project_id == project.id, M.Requirement.status == "approved",
                                                        M.Requirement.kind == "target_metric", M.Requirement.verification.is_(None))).all()
    L += [f"- Requirement awaiting review: {r.text}" for r in open_reqs]
    L += [f"- Approved target not yet verified: {r.text}" for r in unverified]
    if not open_reqs and not unverified:
        L.append("- None recorded.")
    return "\n".join(L) + "\n"


def markdown_pdf(md: str, title: str) -> bytes:
    from reportlab.lib.pagesizes import A4
    from reportlab.lib.styles import getSampleStyleSheet
    from reportlab.platypus import Paragraph, SimpleDocTemplate, Spacer
    from xml.sax.saxutils import escape

    styles = getSampleStyleSheet()
    buf = io.BytesIO()
    doc = SimpleDocTemplate(buf, pagesize=A4, title=title, author="AutoForge Engineering Studio")
    story = []
    for line in md.splitlines():
        if line.startswith("# "):
            story.append(Paragraph(escape(line[2:]), styles["Title"]))
        elif line.startswith("## "):
            story.append(Paragraph(escape(line[3:]), styles["Heading2"]))
        elif line.startswith("### "):
            story.append(Paragraph(escape(line[4:]), styles["Heading3"]))
        elif line.startswith("|---"):
            continue
        elif line.strip():
            story.append(Paragraph(escape(line.strip("> ")).replace("**", ""), styles["BodyText"]))
        else:
            story.append(Spacer(1, 4))
    doc.build(story)
    return buf.getvalue()


@router.get("/projects/{project_id}/report")
def report(project_id: str, format: Literal["md", "pdf"] = "md", user: M.User = Depends(current_user),
           db: Session = Depends(get_db)) -> Response:
    project, _ = require_project(db, user, project_id, "export")
    md = build_report(db, project, user)
    audit.record(db, "project.report", user, project_id=project.id, details={"format": format})
    if format == "pdf":
        return Response(markdown_pdf(md, project.name), media_type="application/pdf",
                        headers={"Content-Disposition": 'attachment; filename="design-review.pdf"'})
    return Response(md, media_type="text/markdown; charset=utf-8",
                    headers={"Content-Disposition": 'attachment; filename="design-review.md"'})
