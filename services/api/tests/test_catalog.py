"""Catalog search, provenance enforcement, comparison and assembly insertion."""

import pytest

from autoforge.catalog.compare import compare, normalise
from autoforge.catalog.seed import seed_reference_data


@pytest.fixture()
def seeded(client):
    from autoforge.db.session import session_scope

    with session_scope() as db:
        counts = seed_reference_data(db)
    with session_scope() as db:  # idempotent
        again = seed_reference_data(db)
    assert counts["items"] == 20 and again == {"materials": 0, "licenses": 0, "items": 0}
    client.register()
    return client


def _ids(c, **params):
    return c.get("/catalog/items", params=params).json()


def test_search_filters_and_facets(seeded):
    c = seeded
    r = _ids(c, q="6205")
    assert r["total"] == 1 and r["items"][0]["part_number"] == "6205"
    assert _ids(c, category="bearing")["total"] == 7
    assert _ids(c, category="bearing", spec="bore_d:25..25")["total"] == 3
    assert _ids(c, spec=["outer_d:50..70", "width:15..20"], category="bearing")["total"] == 3  # 6205, 6305, 6206
    assert _ids(c, q="cap screw M8")["total"] == 2
    assert _ids(c, domain="ev_battery")["total"] == 3
    assert _ids(c, asset_class="manufacturer")["total"] == 0
    assert _ids(c, license="af-generated", has_cad=True)["total"] == 20
    assert c.get("/catalog/items", params={"spec": "bad filter"}).status_code == 422
    f = c.get("/catalog/facets").json()
    assert {x["value"] for x in f["category"]} >= {"bearing", "fastener", "battery_cell"}


def test_item_detail_provenance_and_similar(seeded):
    c = seeded
    item = _ids(c, q="6205")["items"][0]
    d = c.get(f"/catalog/items/{item['id']}").json()
    it = d["item"]
    assert it["specs"]["bore_d"]["value"] == 25 and it["specs"]["bore_d"]["verified"]
    assert it["specs"]["dynamic_load_rating_c"]["value"] is None  # not invented
    assert it["license_detail"]["redistribution_allowed"] is True and it["source_retrieved_at"]
    assert it["completeness"] == pytest.approx(3 / 7, abs=1e-3)
    assert d["similar"][0]["part_number"] in ("6005", "6204", "6305")
    model = c.get(f"/catalog/items/{item['id']}/model").json()
    assert model["bodies"][0]["mass"]["volume_mm3"] == pytest.approx(24492.6, abs=0.1)


def test_compare_distinguishes_missing_and_verdicts(seeded):
    c = seeded
    ids = [_ids(c, q=x)["items"][0]["id"] for x in ("6205", "6305", "6005")]
    cmp = c.post("/catalog/compare", {"ids": ids}).json()
    rows = {r["key"]: r for r in cmp["rows"]}
    assert rows["bore_d"]["status"] == "equal"
    assert rows["outer_d"]["status"] == "differs" and rows["outer_d"]["spread"]["min"] == 47
    assert rows["dynamic_load_rating_c"]["status"] == "all_missing"
    assert all(a["verdict"] == "not_interchangeable_dimensionally" for a in cmp["assessments"])
    assert all(a["is_interchangeability_approval"] is False for a in cmp["assessments"])
    rep = c.post("/catalog/compare/report", {"ids": ids})
    assert rep.status_code == 200 and "**missing**" in rep.text and "https://www.iso.org/standard/70224.html" in rep.text
    cells = _ids(c, category="battery_cell")["items"]
    cc = c.post("/catalog/compare", {"ids": [x["id"] for x in cells[:2]]}).json()
    assert any("safety-critical" in w for w in cc["warnings"])
    assert all(any("Safety-critical" in r for r in a["reasons"]) for a in cc["assessments"])


def test_compare_pure_units_and_dimensional_match():
    a = {"id": "a", "name": "A", "category": "bearing", "standard": "ISO 15",
         "specs": {"bore_d": {"value": 25, "unit": "mm", "verified": True}, "outer_d": {"value": 52, "unit": "mm", "verified": True},
                   "width": {"value": 15, "unit": "mm", "verified": True}}}
    b = {"id": "b", "name": "B", "category": "bearing", "standard": "ISO 15",
         "specs": {"bore_d": {"value": 2.5, "unit": "cm", "verified": True}, "outer_d": {"value": 52, "unit": "mm", "verified": True},
                   "width": {"value": 15, "unit": "mm", "verified": False},
                   "dynamic_load_rating_c": {"value": 14.8, "unit": "kN", "verified": True}}}
    r = compare([a, b])
    rows = {x["key"]: x for x in r["rows"]}
    assert rows["bore_d"]["status"] == "equal"  # 2.5 cm == 25 mm after normalisation
    assert rows["width"]["has_unverified"]
    assert rows["dynamic_load_rating_c"]["status"] == "equal_partial"
    v = r["assessments"][0]
    assert v["verdict"] == "dimensional_match" and v["is_interchangeability_approval"] is False
    assert any("Performance" in x for x in v["reasons"])
    b["specs"]["bore_d"] = {"value": 1, "unit": "kg"}
    assert {x["key"]: x for x in compare([a, b])["rows"]}["bore_d"]["status"] == "incomparable_units"
    assert normalise(1, "in") == (25.4, "mm", "mm")


def test_provenance_required_for_external_records(seeded):
    c = seeded
    org = c.get("/auth/me").json()["organizations"][0]["id"]
    base = {"name": "Caliper bracket X", "category": "brake_caliper", "domain": "braking_steering", "org_id": org,
            "license_key": "manufacturer-terms"}
    r = c.post("/catalog/items", {**base, "asset_class": "manufacturer", "manufacturer": "ACME"})
    assert r.status_code == 422
    r = c.post("/catalog/items", {**base, "asset_class": "user_upload", "mass_kg": 2.1})
    assert r.status_code == 422  # mass needs a source
    r = c.post("/catalog/items", {**base, "asset_class": "user_upload", "license_key": "user-upload",
                                  "specs": {"bore": {"value": 12, "unit": "mm", "verified": True}}})
    assert r.status_code == 422  # verified spec needs a source
    r = c.post("/catalog/items", {**base, "asset_class": "user_upload", "license_key": "user-upload",
                                  "specs": {"bore": {"value": 12, "unit": "mm", "basis": "measured", "source": "caliper measurement",
                                                     "verified": True}}})
    assert r.status_code == 201 and r.json()["item"]["org_scoped"]
    # global catalog requires admin
    g = {**base, "asset_class": "user_upload", "license_key": "user-upload"}
    g.pop("org_id")
    assert c.post("/catalog/items", g).status_code == 403


def test_insert_catalog_item_into_assembly_and_favourites(seeded):
    c = seeded
    p = c.post("/projects", {"name": "Wheel bearing study"}).json()["project"]
    item = _ids(c, q="6205")["items"][0]
    screw = _ids(c, q="M8x25")["items"][0]
    assert c.post(f"/catalog/items/{item['id']}/favorite").json()["favorite"]
    assert [x["id"] for x in c.get("/catalog/favorites").json()["items"]] == [item["id"]]
    assert c.post(f"/projects/{p['id']}/components", {"item_id": item["id"], "note": "candidate"}).status_code == 201
    assert c.get(f"/projects/{p['id']}/components").json()["items"][0]["note"] == "candidate"
    content = {"instances": [
        {"id": "b1", "name": "Bearing", "source": {"kind": "catalog", "item_id": item["id"]}, "fixed": True},
        {"id": "s1", "name": "Screw", "source": {"kind": "catalog", "item_id": screw["id"]},
         "placement": {"position": [60, 0, 0], "rotation": [0, 0, 0]}}]}
    r = c.post(f"/projects/{p['id']}/documents", {"name": "Bearing assy", "kind": "assembly", "content": content})
    assert r.status_code == 201, r.text
    bom = c.get(f"/documents/{r.json()['document']['id']}/bom").json()
    names = {x["name"]: x for x in bom["rows"]}
    assert names["Deep groove ball bearing 6205 (ISO 15 envelope)"]["source"]["verification_status"] == "standard_derived"
    assert set(bom["mass_incomplete_for"]) == set(names)  # no masses are invented
