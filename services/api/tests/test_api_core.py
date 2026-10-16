"""Phase 1 API tests: auth, authorization, documents, revisions, persistence, import/export."""

import json
import re
import uuid

import pyotp
import pytest

from autoforge.kernel import schema as S
from autoforge.kernel.builder import DocBuilder


def test_register_login_logout_me(client, make_client):
    u = client.register(email="Alice@Example.com")
    assert u["email"] == "alice@example.com" and not u["email_verified"]
    assert client.get("/auth/me").status_code == 200
    assert client.post("/auth/logout").status_code == 200
    assert client.get("/auth/me").status_code == 401
    r = client.post("/auth/login", {"email": "alice@example.com", "password": "wrong-password-1"})
    assert r.status_code == 401 and r.json()["error"]["code"] == "invalid_credentials"
    assert r.json()["error"]["request_id"]
    r = client.post("/auth/login", {"email": "ALICE@example.com", "password": "correct-horse-battery"})
    assert r.status_code == 200
    me = client.get("/auth/me").json()
    assert me["organizations"][0]["personal"] is True
    # duplicate + weak password
    c2 = make_client()
    assert c2.post("/auth/register", {"email": "alice@example.com", "password": "another-good-one",
                                      "display_name": "x"}).status_code == 409
    r = c2.post("/auth/register", {"email": "bob@example.com", "password": "short", "display_name": "x"})
    assert r.status_code == 422 and r.json()["error"]["code"] == "weak_password"


def test_csrf_required_for_mutations(client):
    client.register()
    r = client.c.post("/api/v1/projects", json={"name": "No CSRF"})
    assert r.status_code == 403 and r.json()["error"]["code"] == "csrf_failed"
    r = client.c.post("/api/v1/projects", json={"name": "Bad CSRF"}, headers={"x-csrf-token": "nope"})
    assert r.status_code == 403


def test_login_rate_limited(client, make_client):
    client.register(email="rl@example.com")
    c = make_client()
    codes = [c.post("/auth/login", {"email": "rl@example.com", "password": "bad-password-x"}).status_code for _ in range(12)]
    assert codes[:10] == [401] * 10 and 429 in codes[10:]


def test_email_verification_and_password_reset(client, make_client):
    client.register(email="v@example.com")
    out = client.get("/dev/outbox", params={"to": "v@example.com"}).json()["items"]
    tok = re.search(r"token=([A-Za-z0-9_\-]+)", out[0]["body"]).group(1)
    assert client.post("/auth/verify-email", {"token": tok}).status_code == 200
    assert client.get("/auth/me").json()["user"]["email_verified"]
    assert client.post("/auth/verify-email", {"token": tok}).status_code == 400  # single use
    c = make_client()
    assert c.post("/auth/forgot-password", {"email": "v@example.com"}).status_code == 200
    assert c.post("/auth/forgot-password", {"email": "nobody@example.com"}).status_code == 200  # no enumeration
    mails = client.get("/dev/outbox", params={"to": "v@example.com"}).json()["items"]
    rt = re.search(r"token=([A-Za-z0-9_\-]+)", next(m for m in mails if m["kind"] == "reset_password")["body"]).group(1)
    assert c.post("/auth/reset-password", {"token": rt, "password": "new-strong-password-9"}).status_code == 200
    assert client.get("/auth/me").status_code == 401  # all sessions revoked
    assert c.post("/auth/login", {"email": "v@example.com", "password": "new-strong-password-9"}).status_code == 200


def test_mfa_totp_flow(client, make_client):
    client.register(email="mfa@example.com")
    secret = client.post("/auth/mfa/setup").json()["secret"]
    assert client.post("/auth/mfa/enable", {"code": "000000"}).status_code == 401
    assert client.post("/auth/mfa/enable", {"code": pyotp.TOTP(secret).now()}).status_code == 200
    c = make_client()
    r = c.post("/auth/login", {"email": "mfa@example.com", "password": "correct-horse-battery"})
    assert r.json()["mfa_required"] is True
    assert c.get("/auth/me").status_code == 401
    assert c.post("/auth/mfa/verify", {"code": "123456"}).status_code == 401
    assert c.post("/auth/mfa/verify", {"code": pyotp.TOTP(secret).now()}).status_code == 200
    assert c.get("/auth/me").status_code == 200


def test_project_authorization_and_idor(client, make_client):
    client.register()
    p = client.post("/projects", {"name": "Brake upgrade", "domain": "braking_steering"}).json()["project"]
    other = make_client()
    other.register()
    assert other.get(f"/projects/{p['id']}").status_code == 404  # not 403: no existence leak
    assert other.patch(f"/projects/{p['id']}", {"name": "pwned"}).status_code == 404
    assert other.get(f"/projects/{uuid.uuid4()}").status_code == 404
    assert other.get("/projects/not-a-uuid").status_code == 404
    assert client.post(f"/projects/{p['id']}/members", {"email": other.email, "role": "viewer"}).status_code == 201
    assert other.get(f"/projects/{p['id']}").status_code == 200
    r = other.patch(f"/projects/{p['id']}", {"name": "nope"})
    assert r.status_code == 403 and r.json()["error"]["details"]["required"] == "editor"
    r = other.post(f"/projects/{p['id']}/documents", {"name": "x"})
    assert r.status_code == 403
    assert [x["id"] for x in other.get("/projects").json()["items"]] == [p["id"]]


def _create_from_template(client, template="l_bracket"):
    r = client.post("/projects", {"name": "Bracket project", "template": template})
    assert r.status_code == 201, r.text
    body = r.json()
    return body["project"], body["document"]


def test_template_document_and_revision_workflow(client, make_client):
    client.register()
    project, d = _create_from_template(client)
    doc = client.get(f"/documents/{d['id']}").json()
    rev1 = doc["revision"]
    assert rev1["number"] == 1 and rev1["ok"] is True
    assert all(f["status"] == "ok" for f in rev1["recompute"]["features"])
    content = rev1["content"]
    content["parameters"][0]["expression"] = "150"  # L
    op = "op-" + uuid.uuid4().hex
    r = client.post(f"/documents/{d['id']}/revisions", {"base_revision": 1, "content": content, "op_id": op, "message": "Longer base"})
    assert r.status_code == 201, r.text
    assert r.json()["revision"]["number"] == 2
    # idempotent retry
    r2 = client.post(f"/documents/{d['id']}/revisions", {"base_revision": 1, "content": content, "op_id": op})
    assert r2.status_code == 201 and r2.json()["duplicate"] and r2.json()["revision"]["number"] == 2
    # stale base -> conflict with head details
    content["parameters"][1]["expression"] = "100"
    r3 = client.post(f"/documents/{d['id']}/revisions", {"base_revision": 1, "content": content, "op_id": "op-" + uuid.uuid4().hex})
    assert r3.status_code == 409 and r3.json()["error"]["details"]["head_revision"] == 2
    # same op id, different content
    r4 = client.post(f"/documents/{d['id']}/revisions", {"base_revision": 2, "content": content, "op_id": op})
    assert r4.status_code == 409 and r4.json()["error"]["code"] == "idempotency_mismatch"
    # diff and restore
    diff = client.get(f"/documents/{d['id']}/diff", params={"from": 1, "to": 2}).json()["diff"]
    assert diff["parameters"]["changed"] == [{"name": "L", "from": "120", "to": "150"}]
    r5 = client.post(f"/documents/{d['id']}/restore", {"revision": 1, "base_revision": 2, "op_id": "op-" + uuid.uuid4().hex})
    assert r5.status_code == 201 and r5.json()["revision"]["number"] == 3 and r5.json()["revision"]["restored_from"] == 1
    assert r5.json()["revision"]["content_sha256"] == rev1["content_sha256"]
    revs = client.get(f"/documents/{d['id']}/revisions").json()
    assert [x["number"] for x in revs["items"]] == [3, 2, 1] and revs["head"] == 3
    audit = client.get(f"/projects/{project['id']}/audit").json()["items"]
    assert {"document.save", "document.restore", "project.create", "document.create"} <= {a["action"] for a in audit}


def test_invalid_document_rejected_and_geometry_errors_reported(client):
    client.register()
    project, d = _create_from_template(client)
    head = client.get(f"/documents/{d['id']}").json()["revision"]
    bad = dict(head["content"], features=[{"id": "x", "type": "teleport", "params": {}}])
    r = client.post(f"/documents/{d['id']}/revisions", {"base_revision": 1, "content": bad, "op_id": "op-" + uuid.uuid4().hex})
    assert r.status_code == 422 and r.json()["error"]["code"] == "invalid_document"
    content = head["content"]
    fil = next(f for f in content["features"] if f["type"] == "fillet")
    fil["params"]["radius"] = "200"
    r = client.post(f"/documents/{d['id']}/revisions", {"base_revision": 1, "content": content, "op_id": "op-" + uuid.uuid4().hex})
    assert r.status_code == 201  # work-in-progress may be saved...
    rev = r.json()["revision"]
    assert rev["ok"] is False  # ...but is flagged
    failed = [f for f in rev["recompute"]["features"] if f["status"] == "error"]
    assert failed and failed[0]["type"] == "fillet" and "radius" in failed[0]["message"].lower()


def test_model_recompute_cache_and_unsaved_preview(client):
    client.register()
    project, d = _create_from_template(client, "flange_coupling")
    m1 = client.post(f"/documents/{d['id']}/recompute", {}).json()
    assert m1["ok"] and m1["bodies"][0]["triangle_count"] > 100 and m1["cache"] == "miss"
    assert m1["bodies"][0]["mass"]["mass_kg"] > 0
    m2 = client.post(f"/documents/{d['id']}/recompute", {}).json()
    assert m2["cache"] == "hit"
    content = client.get(f"/documents/{d['id']}").json()["revision"]["content"]
    content["parameters"][1]["expression"] = "20"
    m3 = client.post(f"/documents/{d['id']}/recompute", {"content": content}).json()
    assert m3["bodies"][0]["mass"]["volume_mm3"] > m1["bodies"][0]["mass"]["volume_mm3"]


def test_sketch_solve_endpoint(client):
    client.register()
    project, d = _create_from_template(client)
    content = client.get(f"/documents/{d['id']}").json()["revision"]["content"]
    sk = content["features"][0]["params"]
    r = client.post(f"/documents/{d['id']}/sketch/solve", {"sketch": sk, "parameters": content["parameters"]})
    assert r.status_code == 200
    assert r.json()["solve"]["status"] == "fully_constrained"
    assert r.json()["profile"]["regions"][0]["depth"] == 0


def test_export_step_native_and_reimport(client):
    client.register()
    project, d = _create_from_template(client, "stepped_shaft")
    r = client.post(f"/documents/{d['id']}/export", {"format": "step"})
    assert r.status_code == 200 and r.content.startswith(b"ISO-10303-21")
    ver = json.loads(r.headers["x-export-verification"])
    assert ver["reimport_valid"] and ver["volume_rel_error"] < 1e-6
    # Import the exported STEP into a new document via upload
    r2 = client.c.post(f"/api/v1/projects/{project['id']}/imports", files={"file": ("shaft.step", r.content, "application/step")},
                       headers=client._h())
    assert r2.status_code == 201, r2.text
    assert r2.json()["diagnostics"]["solids"] == 1
    imported = client.post(f"/documents/{r2.json()['document']['id']}/recompute", {}).json()
    assert abs(imported["bodies"][0]["mass"]["volume_mm3"] - ver["volume_source_mm3"]) < 1e-3
    # Native export preserves full parametric content
    n = client.post(f"/documents/{d['id']}/export", {"format": "native"})
    native = n.json()
    assert native["manifest"]["retains"][0] == "parametric history"
    r3 = client.post(f"/projects/{project['id']}/documents/import-native", native)
    assert r3.status_code == 201
    back = client.get(f"/documents/{r3.json()['document']['id']}").json()["revision"]
    assert back["content_sha256"] == native["manifest"]["content_sha256"]
    native["content"]["parameters"][0]["expression"] = "999"
    assert client.post(f"/projects/{project['id']}/documents/import-native", native).status_code == 422
    files = client.get(f"/projects/{project['id']}/files", params={"kind": "export"}).json()["items"]
    assert len(files) == 2
    dl = client.get(f"/files/{files[0]['id']}/download")
    assert dl.status_code == 200 and dl.headers["content-disposition"].startswith("attachment")


def test_upload_validation(client, make_client, monkeypatch):
    client.register()
    project, _ = _create_from_template(client)
    up = lambda name, data: client.c.post(f"/api/v1/projects/{project['id']}/files", files={"file": (name, data)},  # noqa: E731
                                          headers=client._h())
    assert up("notes.txt", b"torque spec").status_code == 201
    assert up("evil.html", b"<html><script>").json()["error"]["code"] == "file_type_not_allowed"
    assert up("fake.pdf", b"not a pdf").json()["error"]["code"] == "format_mismatch"
    assert up("x.txt", b"<script>alert(1)</script>").json()["error"]["code"] == "file_rejected"
    from autoforge.core.config import get_settings

    monkeypatch.setattr(get_settings(), "max_upload_mb", 1)
    assert up("big.txt", b"a" * (1024 * 1024 + 1)).status_code == 413
    bad = client.c.post(f"/api/v1/projects/{project['id']}/imports", files={"file": ("broken.step", b"ISO-10303-21;\nGARBAGE")},
                        headers=client._h())
    assert bad.status_code == 422 and bad.json()["error"]["code"] in ("parse_failed", "no_geometry")
    mesh = client.c.post(f"/api/v1/projects/{project['id']}/imports", files={"file": ("m.stl", b"solid a\nendsolid a")},
                         headers=client._h())
    assert mesh.status_code == 422 and mesh.json()["error"]["code"] == "unsupported_format"
    # other users cannot download
    files = client.get(f"/projects/{project['id']}/files").json()["items"]
    other = make_client()
    other.register()
    assert other.get(f"/files/{files[0]['id']}/download").status_code == 404


def test_cross_project_blob_reference_rejected(client):
    client.register()
    p1, _ = _create_from_template(client)
    p2 = client.post("/projects", {"name": "Second"}).json()["project"]
    content = S.Document(features=[S.ImportF(id="i", params=S.ImportParams(
        blob_key=f"projects/{p1['id']}/blobs/{'a' * 64}", sha256="a" * 64, filename="x.step"))]).model_dump(mode="json")
    r = client.post(f"/projects/{p2['id']}/documents", {"name": "steal", "content": content})
    assert r.status_code == 422 and r.json()["error"]["code"] == "forbidden_reference"


def test_document_survives_restart(client, make_client):
    client.register(email="persist@example.com")
    project, d = _create_from_template(client, "brake_rotor")
    sha = client.get(f"/documents/{d['id']}").json()["revision"]["content_sha256"]
    # Simulate a service restart: new app instance, cleared kernel caches, fresh login.
    from autoforge.api.main import create_app
    from autoforge.kernel import engine

    engine.clear_cache()
    from tests.conftest import Client

    fresh = Client(create_app())
    assert fresh.post("/auth/login", {"email": "persist@example.com", "password": "correct-horse-battery"}).status_code == 200
    again = fresh.get(f"/documents/{d['id']}").json()["revision"]
    assert again["content_sha256"] == sha
    m = fresh.post(f"/documents/{d['id']}/recompute", {}).json()
    assert m["ok"]


def test_soft_delete_and_restore_project(client):
    client.register()
    p = client.post("/projects", {"name": "Temp"}).json()["project"]
    assert client.delete(f"/projects/{p['id']}").status_code == 200
    assert client.get(f"/projects/{p['id']}").status_code == 404
    assert client.post(f"/projects/{p['id']}/restore").status_code == 200
    assert client.get(f"/projects/{p['id']}").status_code == 200


def test_health_readiness_metrics_and_errors(client, app):
    from fastapi.testclient import TestClient

    c = TestClient(app)
    assert c.get("/healthz").json()["status"] == "ok"
    rd = c.get("/readyz").json()
    assert rd["checks"]["database"]["ok"] and rd["checks"]["storage"]["ok"]
    assert "af_http_requests_total" in c.get("/metrics").text
    r = c.get("/api/v1/nope", headers={"x-request-id": "abcdef123456"})
    assert r.status_code == 404 and r.json()["error"]["request_id"] == "abcdef123456"
    assert r.headers["x-content-type-options"] == "nosniff"
    spec = c.get("/api/v1/openapi.json").json()
    assert "/api/v1/documents/{document_id}/revisions" in spec["paths"]
