"""Assembly workbench endpoints."""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Depends
from pydantic import BaseModel, Field, ValidationError
from sqlalchemy.orm import Session

from ..assembly import service, solver
from ..assembly.schema import AssemblyDocument, KinematicStudy
from ..core import audit, ratelimit
from ..core.authz import require_document
from ..core.errors import Unprocessable
from ..db import models as M
from ..kernel_pool import get_pool
from .deps import current_user, get_db
from .documents import _get_rev

router = APIRouter(tags=["assemblies"])


class AsmIn(BaseModel):
    content: dict[str, Any] | None = None
    revision: int | None = None


class InterferenceIn(AsmIn):
    clearance_mm: float = Field(default=0.0, ge=0, le=1000)


class StudyIn(AsmIn):
    study: KinematicStudy


def _load(db: Session, user: M.User, document_id: str, body: AsmIn) -> tuple[M.CADDocument, M.Project, dict[str, Any]]:
    doc, project, _ = require_document(db, user, document_id)
    if doc.kind != "assembly":
        raise Unprocessable("Document is not an assembly", code="not_an_assembly")
    content = body.content if body.content is not None else _get_rev(db, doc, body.revision).content
    try:
        AssemblyDocument.model_validate(content)
    except ValidationError as exc:
        raise Unprocessable("Invalid assembly document", code="invalid_document",
                            details=[{"loc": list(e["loc"]), "msg": e["msg"]} for e in exc.errors()][:30]) from exc
    return doc, project, content


@router.post("/documents/{document_id}/assembly/solve")
def solve(document_id: str, body: AsmIn, user: M.User = Depends(current_user), db: Session = Depends(get_db)) -> dict[str, Any]:
    ratelimit.hit(f"asm:{user.id}", 1200, 600)
    _, project, content = _load(db, user, document_id, body)
    b = service.build(db, project.id, content, "draft")
    solved = solver.apply_placements(b["doc"], b["solve"].placements) if b["solve"].converged else b["doc"]
    return {"solve": {k: v for k, v in b["solve"].to_dict().items() if k != "placements"},
            "instance_errors": b["inst_errors"], "content": solved.model_dump(mode="json")}


@router.post("/documents/{document_id}/assembly/interference")
def interference(document_id: str, body: InterferenceIn, user: M.User = Depends(current_user), db: Session = Depends(get_db)
                 ) -> dict[str, Any]:
    doc, project, content = _load(db, user, document_id, body)
    b = service.build(db, project.id, content, "draft")
    parts, insts, prefixes = service.kernel_instances(b)
    if len(insts) < 2:
        return {"pairs": [], "note": "Fewer than two resolvable components"}
    res = get_pool().call("assembly_interference", timeout=300, parts=parts, instances=insts, prefixes=prefixes,
                          clearance_mm=body.clearance_mm)
    names = {i["id"]: i["name"] for i in insts}
    for p in res["pairs"]:
        p["a_name"], p["b_name"] = names[p["a"]], names[p["b"]]
    res["interfering"] = [p for p in res["pairs"] if p["interferes"]]
    if body.clearance_mm:
        res["clearance_violations"] = [p for p in res["pairs"] if p.get("clearance_ok") is False]
    audit.record(db, "assembly.interference_check", user, project_id=project.id, target_type="document", target_id=doc.id,
                 details={"pairs": len(res["pairs"]), "interfering": len(res["interfering"])})
    return res


@router.post("/documents/{document_id}/assembly/kinematics")
def kinematics(document_id: str, body: StudyIn, user: M.User = Depends(current_user), db: Session = Depends(get_db)
               ) -> dict[str, Any]:
    ratelimit.hit(f"kin:{user.id}", 60, 600)
    doc, project, content = _load(db, user, document_id, body)
    b = service.build(db, project.id, content, "draft")
    st = body.study
    try:
        out = solver.kinematic_sweep(b["doc"].model_copy(update={"instances": [i for i in b["doc"].instances
                                                                               if i.id in b["part_for"]]}),
                                     b["part_for"], st.mate, st.start, st.end, st.steps, st.duration_s, service._env(b["doc"]))
    except solver.GeometryError as exc:
        raise Unprocessable(str(exc), code="invalid_study") from exc
    if st.check_collisions:
        parts, insts, prefixes = service.kernel_instances(b)
        sweep = get_pool().call("collision_sweep", timeout=300, parts=parts, instances=insts, frames=out["frames"],
                                prefixes=prefixes)
        out["collisions"] = sweep["collisions"]
        out["collision_frames_checked"] = sweep["frames_checked"]
    else:
        out["collisions"] = None
    return out


@router.get("/documents/{document_id}/bom")
def bill_of_materials(document_id: str, revision: int | None = None, user: M.User = Depends(current_user),
                      db: Session = Depends(get_db)) -> dict[str, Any]:
    doc, project, _ = require_document(db, user, document_id)
    rev = _get_rev(db, doc, revision)
    if doc.kind != "assembly":
        from .documents import model_for_content

        m = model_for_content(db, project, doc, rev.content, "draft")
        rows = [{"item": i + 1, "name": f"{doc.name} / {b['name']}", "quantity": 1,
                 "material": (b.get("material") or {}).get("name"), "unit_mass_kg": b.get("mass", {}).get("mass_kg"),
                 "total_mass_kg": b.get("mass", {}).get("mass_kg"),
                 "mass_basis": "computed from CAD volume x assigned density" if b.get("mass", {}).get("mass_kg") else None,
                 "source": {"kind": "document", "document_id": str(doc.id), "revision": rev.number}}
                for i, b in enumerate(m.get("bodies", []))]
    else:
        b = service.build(db, project.id, rev.content, "draft")
        rows = service.bom(b["doc"], b["sources"], b["models"])
    total = sum(r["total_mass_kg"] for r in rows if r.get("total_mass_kg") is not None)
    missing = [r["name"] for r in rows if r.get("total_mass_kg") is None]
    return {"document": {"id": str(doc.id), "name": doc.name, "revision": rev.number}, "rows": rows,
            "total_mass_kg": total, "mass_incomplete_for": missing,
            "note": "Masses are derived from CAD volume and assigned material density unless a catalog source is cited. "
                    "Rows without a material have no mass."}
