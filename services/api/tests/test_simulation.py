"""Solver-backed simulation tests (real gmsh + CalculiX) and job-queue resilience.

Benchmarks (documented in docs/validation/simulation-benchmarks.md):
* Cantilever 200x20x20 mm steel, tip shear 1 kN: Timoshenko deflection 0.9597 mm.
* Same bar, axial 10 kN: FL/EA = 0.02381 mm.
* Bar 100x10x10, k = 50 W/mK: flux 0.01 W/mm^2 -> 40.0 degC; convection -> 46.667 degC.
"""

import math
import shutil
import uuid

import pytest

from autoforge.jobs import queue
from autoforge.jobs.worker import Worker
from autoforge.kernel import schema as S
from autoforge.kernel.builder import DocBuilder

pytestmark = pytest.mark.skipif(shutil.which("ccx") is None, reason="CalculiX not installed")


def beam_doc(dims=(200, 20, 20)):
    b = DocBuilder()
    box = b.feature(S.PrimitiveF, shape="box", size=dims, body="Beam")
    return b.build().model_dump(mode="json"), box


def _setup(box, load, size=5, conv=False):
    return {"analysis": "static", "body": "Beam",
            "material": {"name": "Steel (benchmark)", "youngs_modulus_mpa": 210000, "poisson_ratio": 0.3, "yield_strength_mpa": 250,
                         "source": "benchmark definition"},
            "mesh": {"size_mm": size}, "supports": [{"faces": [f"{box}:-x"]}],
            "loads": [{"type": "force", "faces": [f"{box}:+x"], "vector_n": load}], "convergence_study": conv}


def _project_with_beam(client):
    client.register()
    p = client.post("/projects", {"name": "Beam validation"}).json()["project"]
    content, box = beam_doc()
    d = client.post(f"/projects/{p['id']}/documents", {"name": "Beam", "content": content}).json()["document"]
    return p, d, box


def test_cantilever_benchmark_end_to_end(client):
    p, d, box = _project_with_beam(client)
    key = "bench-" + uuid.uuid4().hex
    r = client.post(f"/projects/{p['id']}/simulations", {"document_id": d["id"], "name": "Tip load", "idempotency_key": key,
                                                         "setup": _setup(box, (0, 0, -1000), conv=True)})
    assert r.status_code == 201, r.text
    sim = r.json()["simulation"]
    assert sim["status"] == "queued"
    dup = client.post(f"/projects/{p['id']}/simulations", {"document_id": d["id"], "name": "Tip load", "idempotency_key": key,
                                                           "setup": _setup(box, (0, 0, -1000))}).json()
    assert dup["duplicate"] and dup["simulation"]["id"] == sim["id"]
    assert Worker("test-worker").process_one()
    full = client.get(f"/simulations/{sim['id']}").json()["simulation"]
    assert full["status"] == "succeeded", full
    s = full["result"]["summary"]
    E, G, L, A, I = 210000, 210000 / 2.6, 200, 400, 20**4 / 12
    kappa = 10 * 1.3 / (12 + 11 * 0.3)
    exact = 1000 * L**3 / (3 * E * I) + 1000 * L / (kappa * G * A)
    assert abs(s["max_displacement_mm"] - exact) / exact < 0.02
    assert s["equilibrium"]["ok"] and abs(s["equilibrium"]["reaction_n"][2] - 1000) < 1e-3
    assert s["units"]["stress"] == "MPa" and full["solver_version"].startswith("CalculiX")
    assert full["result"]["mesh"]["element_type"].startswith("C3D10")
    conv = full["result"]["convergence"]
    assert conv["changes"]["max_displacement_mm"]["relative_change"] < 0.02
    field = client.get(f"/simulations/{sim['id']}/field").json()
    assert len(field["positions"]) // 3 == len(field["von_mises"]) and field["von_mises_range"][1] > 100
    inp = client.get(f"/simulations/{sim['id']}/artifacts/model.inp")
    assert inp.status_code == 200 and b"C3D10" in inp.content
    rep = client.get(f"/simulations/{sim['id']}/report").text
    assert "Assumptions and limitations" in rep and "Mesh sensitivity" in rep


def test_invalid_inputs_rejected_before_queueing(client):
    p, d, box = _project_with_beam(client)
    bad_face = _setup(box, (0, 0, -1000))
    bad_face["supports"] = [{"faces": ["nope:face"]}]
    r = client.post(f"/projects/{p['id']}/simulations", {"document_id": d["id"], "name": "x", "setup": bad_face})
    assert r.status_code == 422 and "nope:face" in r.json()["error"]["message"]
    no_e = _setup(box, (0, 0, -1))
    no_e["material"].pop("youngs_modulus_mpa")
    r = client.post(f"/projects/{p['id']}/simulations", {"document_id": d["id"], "name": "x", "setup": no_e})
    assert r.status_code == 422 and r.json()["error"]["code"] == "invalid_setup"


def test_thermal_benchmark_via_api(client):
    client.register()
    p = client.post("/projects", {"name": "Thermal"}).json()["project"]
    content, box = beam_doc((100, 10, 10))
    d = client.post(f"/projects/{p['id']}/documents", {"name": "Bar", "content": content}).json()["document"]
    setup = {"analysis": "thermal", "body": "Beam", "material": {"name": "k50", "thermal_conductivity_w_mk": 50},
             "mesh": {"size_mm": 4}, "temperatures": [{"type": "temperature", "faces": [f"{box}:-x"], "value_c": 100}],
             "convection": [{"type": "convection", "faces": [f"{box}:+x"], "h_w_mm2k": 0.001, "ambient_c": 20}]}
    sim = client.post(f"/projects/{p['id']}/simulations", {"document_id": d["id"], "name": "Fin", "setup": setup}).json()["simulation"]
    Worker("t").process_one()
    s = client.get(f"/simulations/{sim['id']}").json()["simulation"]["result"]["summary"]
    assert math.isclose(s["min_temperature_c"], 20 + (80 / 3000) / 0.001, rel_tol=1e-4)


def _diag_job(db_payload, timeout_s=30):
    from autoforge.db.session import session_scope

    with session_scope() as db:
        job, _ = queue.enqueue(db, "diagnostic_sleep", db_payload, timeout_s=timeout_s)
        return job.id


def _job(jid):
    from autoforge.db import models as M
    from autoforge.db.session import session_scope

    with session_scope() as db:
        j = db.get(M.Job, jid)
        return j.status, j.error


def test_job_timeout_crash_failure_cancel_and_stale_recovery(db_clean):
    from autoforge.db import models as M
    from autoforge.db.session import session_scope

    w = Worker("t")
    jid = _diag_job({"seconds": 30}, timeout_s=2)
    w.process_one()
    st, err = _job(jid)
    assert st == "timed_out" and "time limit" in err
    jid = _diag_job({"seconds": 0.1, "crash": True})
    w.process_one()
    st, err = _job(jid)
    assert st == "failed" and "terminated unexpectedly" in err
    jid = _diag_job({"seconds": 0.1, "fail": True})
    w.process_one()
    assert _job(jid) == ("failed", "requested failure")
    # cancel while queued
    jid = _diag_job({"seconds": 1})
    with session_scope() as db:
        assert queue.request_cancel(db, db.get(M.Job, jid)) == "cancelled"
    assert w.process_one() is False
    assert _job(jid)[0] == "cancelled"
    # stale running job (worker died) is re-queued once then failed
    jid = _diag_job({"seconds": 1})
    with session_scope() as db:
        j = queue.claim(db, "dead-worker")
        j.max_attempts = 2
        j.heartbeat_at = queue.now().replace(year=2000)
    with session_scope() as db:
        assert jid in queue.recover_stale(db, 60)
    assert _job(jid)[0] == "queued"
    with session_scope() as db:
        j = queue.claim(db, "dead-worker")
        j.heartbeat_at = queue.now().replace(year=2000)
    with session_scope() as db:
        queue.recover_stale(db, 60)
    st, err = _job(jid)
    assert st == "failed" and "stopped responding" in err


def test_cancel_running_simulation(client):
    import threading
    import time

    p, d, box = _project_with_beam(client)
    sim = client.post(f"/projects/{p['id']}/simulations", {"document_id": d["id"], "name": "Fine",
                                                           "setup": _setup(box, (0, 0, -1000), size=1.2)}).json()["simulation"]
    t = threading.Thread(target=Worker("t").process_one)
    t.start()
    for _ in range(100):
        s = client.get(f"/simulations/{sim['id']}").json()["simulation"]
        if s["status"] == "running":
            break
        time.sleep(0.1)
    assert client.post(f"/simulations/{sim['id']}/cancel").json()["status"] == "cancelling"
    t.join(60)
    assert client.get(f"/simulations/{sim['id']}").json()["simulation"]["status"] == "cancelled"
