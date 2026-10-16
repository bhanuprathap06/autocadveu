"""Phase 6: private rooms, invitations, comments, WebSocket presence, AI assistant, calculators, admin, reports."""

import re
import uuid
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace

import pytest


def _invite_token(client, room_id, email, role="reviewer", days=7):
    r = client.post(f"/rooms/{room_id}/invitations", {"email": email, "role": role, "expires_in_days": days})
    assert r.status_code == 201, r.text
    return r.json()["dev_link"].rsplit("/", 1)[-1], r.json()["invitation"]["id"]


def test_room_invitation_lifecycle_and_access(client, make_client):
    client.register(email="owner@example.com")
    p = client.post("/projects", {"name": "Shared suspension", "template": "control_arm_plate"}).json()
    project, doc = p["project"], p["document"]
    room = client.post("/rooms", {"name": "Suspension review"}).json()["room"]
    assert client.post(f"/rooms/{room['id']}/projects", {"project_id": project["id"]}).status_code == 201

    bob = make_client()
    bob.register(email="bob@example.com")
    assert bob.get(f"/projects/{project['id']}").status_code == 404  # private until invited
    eve = make_client()
    eve.register(email="eve@example.com")

    token, inv_id = _invite_token(client, room["id"], "bob@example.com", "reviewer")
    # wrong account cannot use bob's invitation
    r = eve.post(f"/invitations/{token}/accept")
    assert r.status_code == 403 and r.json()["error"]["code"] == "invitation_email_mismatch"
    assert eve.get(f"/rooms/{room['id']}").status_code == 404
    # bob accepts; reviewer role gives view + comment, not edit
    assert bob.post(f"/invitations/{token}/accept").status_code == 200
    assert bob.post(f"/invitations/{token}/accept").status_code == 410  # single use
    assert bob.get(f"/projects/{project['id']}").json()["project"]["role"] == "reviewer"
    assert bob.post(f"/projects/{project['id']}/comments", {"target_type": "document", "target_id": doc["id"],
                                                           "body": "Check bushing bore wall thickness",
                                                           "anchor": {"face": "f3:wall:0", "point": [40, 20, 10]}}).status_code == 201
    content = bob.get(f"/documents/{doc['id']}").json()["revision"]["content"]
    r = bob.post(f"/documents/{doc['id']}/revisions", {"base_revision": 1, "content": content, "op_id": "op-" + uuid.uuid4().hex})
    assert r.status_code == 403
    # members cannot escalate themselves
    me_id = bob.get("/auth/me").json()["user"]["id"]
    assert bob.c.put(f"/api/v1/rooms/{room['id']}/members/{me_id}", json={"role": "owner"},
                     headers=bob._h()).status_code == 403
    # revocation and expiry
    tok2, inv2 = _invite_token(client, room["id"], "eve@example.com", "viewer")
    assert client.delete(f"/rooms/{room['id']}/invitations/{inv2}").json()["invitation"]["state"] == "revoked"
    assert eve.post(f"/invitations/{tok2}/accept").status_code == 410
    tok3, inv3 = _invite_token(client, room["id"], "eve@example.com", "viewer")
    from autoforge.db import models as M
    from autoforge.db.session import session_scope

    with session_scope() as db:
        db.get(M.Invitation, uuid.UUID(inv3)).expires_at = datetime.now(timezone.utc) - timedelta(minutes=1)
    r = eve.post(f"/invitations/{tok3}/accept")
    assert r.status_code == 410 and r.json()["error"]["code"] == "invitation_expired"
    assert eve.post("/invitations/not-a-real-token-xyz/accept").status_code == 404
    # owner sees invitation states; audit has the trail
    states = {i["email"] + ":" + i["state"] for i in client.get(f"/rooms/{room['id']}").json()["invitations"]}
    assert {"bob@example.com:accepted", "eve@example.com:revoked", "eve@example.com:expired"} <= states
    actions = {a["action"] for a in client.get(f"/rooms/{room['id']}/audit").json()["items"]}
    assert {"room.invite", "room.invite_accept", "room.invite_revoke", "room.invite_wrong_account"} <= actions
    # removing the share removes access
    assert client.delete(f"/rooms/{room['id']}/projects/{project['id']}").status_code == 200
    assert bob.get(f"/projects/{project['id']}").status_code == 404


def test_comment_permissions_and_threads(client, make_client):
    client.register()
    p = client.post("/projects", {"name": "C"}).json()["project"]
    viewer = make_client()
    viewer.register(email="viewer@example.com")
    client.post(f"/projects/{p['id']}/members", {"email": "viewer@example.com", "role": "viewer"})
    assert viewer.post(f"/projects/{p['id']}/comments", {"target_type": "project", "target_id": p["id"], "body": "hi"}).status_code == 403
    c = client.post(f"/projects/{p['id']}/comments", {"target_type": "project", "target_id": p["id"], "body": "Root"}).json()["comment"]
    reply = client.post(f"/projects/{p['id']}/comments", {"target_type": "project", "target_id": p["id"], "body": "Reply",
                                                           "parent_id": c["id"]}).json()["comment"]
    assert reply["parent_id"] == c["id"]
    assert client.patch(f"/comments/{c['id']}", {"resolved": True}).json()["comment"]["resolved"]
    items = viewer.get(f"/projects/{p['id']}/comments", params={"include_resolved": False}).json()["items"]
    assert [x["body"] for x in items] == ["Reply"]


def test_websocket_auth_origin_and_presence(client, app, make_client):
    from starlette.websockets import WebSocketDisconnect

    client.register()
    p = client.post("/projects", {"name": "WS"}).json()["project"]
    tc = client.c
    with tc.websocket_connect(f"/api/v1/ws?project_id={p['id']}", headers={"origin": "http://localhost:3000"}) as ws:
        hello = ws.receive_json()
        assert hello["type"] == "hello" and len(hello["presence"]) == 1
        ws.send_json({"type": "presence", "document_id": "d1", "camera": {"position": [1, 2, 3], "target": [0, 0, 0]},
                      "selection": ["f1:end"]})
        ws.send_json({"type": "ping"})
        assert ws.receive_json()["type"] == "pong"
    # foreign origin rejected
    with pytest.raises(WebSocketDisconnect) as e:
        with tc.websocket_connect(f"/api/v1/ws?project_id={p['id']}", headers={"origin": "https://evil.example"}) as ws:
            ws.receive_json()
    assert e.value.code == 4403
    # unauthenticated / unauthorised users rejected
    stranger = make_client()
    stranger.register()
    with pytest.raises(WebSocketDisconnect) as e2:
        with stranger.c.websocket_connect(f"/api/v1/ws?project_id={p['id']}") as ws:
            ws.receive_json()
    assert e2.value.code == 4401


def test_problem_to_requirements_to_validated_proposal(client):
    client.register()
    p = client.post("/projects", {"name": "Lightweight bracket"}).json()["project"]
    stmt = ("Design a steel mounting bracket for an ECU. The bracket must weigh less than 1.5 kg. The base length shall be "
            "at most 150 mm. Assume a vibration load of 5 g. What corrosion protection is required? It must be bolted with M8 bolts.")
    prob = client.post(f"/projects/{p['id']}/problems", {"title": "ECU bracket", "statement": stmt}).json()["problem"]
    ext = client.post(f"/problems/{prob['id']}/extract", {"allow_external_ai": True}).json()["problem"]  # no key -> heuristic
    assert ext["extraction_provider"] == "heuristic-rules-v1"
    reqs = ext["requirements"]
    kinds = {r["kind"] for r in reqs}
    assert {"target_metric", "assumption", "open_question", "functional"} <= kinds
    mass = next(r for r in reqs if r["metric"] == "mass" and r["unit"] == "kg")
    assert mass["comparator"] == "<=" and mass["target_value"] == 1.5
    assert all(r["status"] == "proposed" and r["claim_class"] for r in reqs)
    assert ext["domain"] in ("mechanical", "electronics")
    # approval gate
    assert client.post(f"/problems/{prob['id']}/approve").status_code == 409
    for r in reqs:
        st = "rejected" if r["kind"] == "open_question" else "approved"
        assert client.patch(f"/requirements/{r['id']}", {"status": st}).status_code == 200
    assert client.post(f"/problems/{prob['id']}/approve").json()["problem"]["status"] == "approved"
    con = client.post(f"/problems/{prob['id']}/concepts", {}).json()
    assert con["concepts"][0]["template"] == "l_bracket"
    assert all(c["claim_class"] in ("source_supported_fact", "calculation", "engineering_hypothesis", "design_recommendation",
                                    "unverified_suggestion") for c in con["concepts"][0]["claims"])
    prop = client.post(f"/problems/{prob['id']}/proposal", {"template": "l_bracket"}).json()
    assert prop["requires_approval"] and prop["validation"]["valid"]
    # a tampered proposal with unknown parameters is rejected, never executed
    bad = dict(prop["proposal"], parameters=[{"name": "__import__", "expression": "1"}])
    assert client.post(f"/problems/{prob['id']}/proposal/apply", {"proposal": bad}).status_code == 422
    applied = client.post(f"/problems/{prob['id']}/proposal/apply", {"proposal": prop["proposal"]})
    assert applied.status_code == 201
    doc_id = applied.json()["document"]["id"]
    v = client.post(f"/requirements/{mass['id']}/verify", {"document_id": doc_id}).json()["requirement"]["verification"]
    assert v["status"] == "pass" and 0.6 < v["value"] < 0.8 and v["type"] == "cad"  # ~0.71 kg steel bracket
    rep = client.get(f"/projects/{p['id']}/report")
    assert rep.status_code == 200 and "pass (" in rep.text and "Design review report" in rep.text
    pdf = client.get(f"/projects/{p['id']}/report", params={"format": "pdf"})
    assert pdf.content.startswith(b"%PDF")


def test_anthropic_provider_uses_structured_outputs_and_handles_refusal(monkeypatch):
    from autoforge.ai import providers
    from autoforge.ai.schemas import Extraction
    from autoforge.core.config import get_settings

    monkeypatch.setattr(get_settings(), "anthropic_api_key", "sk-test")
    calls = {}

    class FakeMessages:
        def __init__(self, resp):
            self.resp = resp

        def parse(self, **kw):
            calls.update(kw)
            return self.resp

    ok = SimpleNamespace(stop_reason="end_turn", parsed_output=Extraction(summary="s", requirements=[], clarifying_questions=["q"]))
    prov = providers.AnthropicProvider()
    prov.client = SimpleNamespace(beta=SimpleNamespace(messages=FakeMessages(ok)))
    out = prov.extract("The part must weigh < 2 kg", None)
    assert out.clarifying_questions == ["q"]
    assert calls["model"] == "claude-opus-5-5" and calls["output_format"] is Extraction
    assert calls["fallbacks"] == "default" and "tool_choice" not in calls
    assert "<problem_statement>" in calls["messages"][0]["content"]
    refused = SimpleNamespace(stop_reason="refusal", stop_details=SimpleNamespace(category="cyber"), parsed_output=None)
    prov.client = SimpleNamespace(beta=SimpleNamespace(messages=FakeMessages(refused)))
    with pytest.raises(providers.AIError) as e:
        prov.extract("x", None)
    assert e.value.code == "ai_refused"


def test_calculators_endpoint_and_admin_guard(client):
    client.register(email="adm@example.com")
    lst = client.get("/calculators").json()
    assert any(c["key"] == "iso_fit" for c in lst["items"])
    r = client.post("/calculators/iso_fit", {"nominal_mm": 25, "hole": "H7", "shaft": "g6"})
    assert r.json()["outputs"]["fit_type"]["value"] == "clearance"
    assert client.post("/calculators/beam", {"case": "nope", "length_mm": 1, "load": 1, "youngs_modulus_mpa": 1,
                                              "section": {"type": "circle", "d_mm": 1}}).status_code == 422
    assert client.get("/admin/overview").status_code == 403
    from autoforge.cli import create_admin

    create_admin("adm@example.com")
    ov = client.get("/admin/overview").json()
    assert ov["counts"]["users"] == 1 and "database" in ov["checks"]
