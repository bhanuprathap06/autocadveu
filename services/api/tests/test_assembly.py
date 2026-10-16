"""Assembly mate solver, kinematics, interference and BOM."""

import math
import uuid

import numpy as np
import pytest

from autoforge.assembly import solver
from autoforge.assembly.schema import AssemblyDocument
from autoforge.kernel import schema as S
from autoforge.kernel.builder import DocBuilder
from autoforge.kernel.ops import recompute


def plate_part():
    b = DocBuilder()
    box = b.feature(S.PrimitiveF, shape="box", size=(100, 60, 10), body="Plate")
    hole = b.feature(S.HoleF, body="Plate", plane=S.PlaneRef(kind="face", body="Plate", face=f"{box}:+z"), positions=[(30, 30)],
                     diameter=20.2)
    b.material("Plate", S.MaterialAssignment(name="steel", density_kg_m3=7850, source="test"))
    return b.build(), box, hole


def pin_part():
    b = DocBuilder()
    cyl = b.feature(S.PrimitiveF, shape="cylinder", size=(10, 40, 0), body="Pin")
    return b.build(), cyl


def _parts():
    pd, box, hole = plate_part()
    nd, cyl = pin_part()
    return recompute(pd.model_dump(mode="json")), recompute(nd.model_dump(mode="json")), box, hole, cyl


def asm(box, hole, cyl, pin_pos=(5, 7, 50), pin_rot=(10, 0, 0), extra_mates=()):
    return AssemblyDocument.model_validate({
        "instances": [
            {"id": "plate", "name": "Plate", "source": {"kind": "document", "document_id": str(uuid.uuid4())}, "fixed": True},
            {"id": "pin", "name": "Pin", "source": {"kind": "document", "document_id": str(uuid.uuid4())},
             "placement": {"position": pin_pos, "rotation": pin_rot}},
        ],
        "mates": [
            {"id": "m1", "type": "revolute", "a": {"instance": "plate", "kind": "face", "face": f"{hole}:wall:0"},
             "b": {"instance": "pin", "kind": "face", "face": f"{cyl}:side"}, "value": 0, "limits": [-90, 90]},
            *extra_mates,
        ],
    })


def test_revolute_mate_solves_and_reports_one_dof():
    plate, pin, box, hole, cyl = _parts()
    doc = asm(box, hole, cyl)
    r = solver.solve(doc, {"plate": plate, "pin": pin})
    assert r.converged, r.to_dict()
    assert r.status == "under_constrained" and r.dof == 1 and r.instance_dof["pin"] == 1
    T = r.placements["pin"]
    axis = T[:3, :3] @ np.array([0, 0, 1.0])
    assert abs(abs(axis[2]) - 1) < 1e-6  # pin axis aligned with hole axis (Z)
    assert np.allclose(T[:2, 3], [30, 30], atol=1e-5)  # on the hole axis


def test_fully_constrained_and_conflict_detection():
    plate, pin, box, hole, cyl = _parts()
    lock = {"id": "m2", "type": "angle", "a": {"instance": "plate", "kind": "face", "face": f"{box}:+x"},
            "b": {"instance": "pin", "kind": "origin", "plane": "YZ"}, "value": 0}
    r = solver.solve(asm(box, hole, cyl, extra_mates=[lock]), {"plate": plate, "pin": pin})
    assert r.converged and r.dof == 0 and r.status == "fully_constrained", r.to_dict()
    # Contradiction: pin also concentric with an axis offset from the hole
    bad = {"id": "bad", "type": "distance", "a": {"instance": "plate", "kind": "face", "face": f"{box}:+x"},
           "b": {"instance": "pin", "kind": "origin", "axis": "Z", "point": True}, "value": 5}
    r2 = solver.solve(asm(box, hole, cyl, extra_mates=[bad]), {"plate": plate, "pin": pin})
    assert r2.status == "conflicting" and r2.conflicting == ["bad"]


def test_kinematic_sweep_and_limits():
    plate, pin, box, hole, cyl = _parts()
    doc = asm(box, hole, cyl, pin_pos=(30, 30, 0), pin_rot=(0, 0, 0))
    out = solver.kinematic_sweep(doc, {"plate": plate, "pin": pin}, "m1", 0, 120, 13, 1.0)
    assert not out["failed_steps"]
    vals = [f["value"] for f in out["frames"]]
    assert vals[0] == 0 and vals[-1] == 120
    viol = {round(v["value"]) for v in out["limit_violations"]}
    assert viol == {100, 110, 120}
    # pin origin lies on the rotation axis: zero translational speed
    assert out["kinematics"]["pin"]["speed_max_mm_s"] < 1e-3


def test_euler_roundtrip():
    for e in [(10, 20, 30), (-45, 80, 170), (0, 0, 0)]:
        R = solver.euler_to_matrix(*e)
        assert np.allclose(solver.euler_to_matrix(*solver.matrix_to_euler(R)), R, atol=1e-9)


# ---------------------------------------------------------------- API level

def _part_doc(client, project_id, name, doc):
    r = client.post(f"/projects/{project_id}/documents", {"name": name, "content": doc.model_dump(mode="json")})
    assert r.status_code == 201, r.text
    return r.json()["document"]["id"]


def test_assembly_api_bom_interference_kinematics_export(client):
    client.register()
    p = client.post("/projects", {"name": "Hinge"}).json()["project"]
    pd, box, hole = plate_part()
    nd, cyl = pin_part()
    plate_id = _part_doc(client, p["id"], "Plate", pd)
    pin_id = _part_doc(client, p["id"], "Pin", nd)
    content = {
        "instances": [
            {"id": "plate", "name": "Plate", "source": {"kind": "document", "document_id": plate_id}, "fixed": True},
            {"id": "pin", "name": "Pin", "source": {"kind": "document", "document_id": pin_id, "revision": 1},
             "placement": {"position": [0, 0, 80], "rotation": [0, 0, 0]}},
            {"id": "pin2", "name": "Pin 2", "source": {"kind": "document", "document_id": pin_id},
             "placement": {"position": [70, 30, -5], "rotation": [0, 0, 0]}},
        ],
        "mates": [{"id": "m1", "type": "revolute", "a": {"instance": "plate", "kind": "face", "face": f"{hole}:wall:0"},
                   "b": {"instance": "pin", "kind": "face", "face": f"{cyl}:side"}, "value": 15}],
    }
    r = client.post(f"/projects/{p['id']}/documents", {"name": "Hinge assembly", "kind": "assembly", "content": content})
    assert r.status_code == 201, r.text
    asm_id = r.json()["document"]["id"]
    assert r.json()["revision"]["recompute"]["solve"]["converged"]
    model = client.post(f"/documents/{asm_id}/recompute", {}).json()
    assert model["kind"] == "assembly" and len(model["instances"]) == 3 and len(model["parts"]) == 2
    pin_inst = next(i for i in model["instances"] if i["id"] == "pin")
    assert np.allclose(pin_inst["placement"]["position"][:2], [30, 30], atol=1e-5)
    assert abs(pin_inst["placement"]["position"][2] - (10 + 0.01 - 15)) < 1e-5  # hole axis points -Z from the top face
    bom = client.get(f"/documents/{asm_id}/bom").json()
    by = {r["name"]: r for r in bom["rows"]}
    assert by["Pin"]["quantity"] == 2 and by["Plate"]["quantity"] == 1
    assert math.isclose(by["Plate"]["unit_mass_kg"], (100 * 60 * 10 - math.pi * 10.1**2 * 10) * 7850e-9, rel_tol=1e-6)
    assert "Pin" in bom["mass_incomplete_for"]  # no material on the pin: mass not invented
    itf = client.post(f"/documents/{asm_id}/assembly/interference", {"clearance_mm": 1}).json()
    pairs = {(x["a"], x["b"]): x for x in itf["pairs"]}
    assert pairs[("plate", "pin2")]["interferes"]  # pin2 placed through the plate body
    assert not pairs[("plate", "pin")]["interferes"]  # 0.1 mm radial clearance
    assert pairs[("plate", "pin")]["clearance_ok"] is False  # but less than 1 mm
    kin = client.post(f"/documents/{asm_id}/assembly/kinematics",
                      {"study": {"mate": "m1", "start": 0, "end": 90, "steps": 4, "check_collisions": True}}).json()
    assert len(kin["frames"]) == 4 and kin["collisions"] is not None
    assert {(c["a"], c["b"]) for c in kin["collisions"]} == {("plate", "pin2")}
    step = client.post(f"/documents/{asm_id}/export", {"format": "step"})
    assert step.status_code == 200 and step.content.count(b"NEXT_ASSEMBLY_USAGE_OCCURRENCE") == 3
    # Unknown/foreign part reference is reported, not silently dropped
    content["instances"].append({"id": "ghost", "name": "Ghost", "source": {"kind": "document",
                                                                             "document_id": str(uuid.uuid4())}})
    m2 = client.post(f"/documents/{asm_id}/recompute", {"content": content}).json()
    ghost = next(i for i in m2["instances"] if i["id"] == "ghost")
    assert "not found" in ghost["error"] and m2["ok"] is False
