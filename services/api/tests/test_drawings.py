"""Drawings: HLR views, model-linked dimensions, GD&T validation, exports, BOM."""

import io
import uuid

import pytest

from autoforge.drawing.schema import DrawingContent, validate_gdt


def _content(**kw):
    base = {"views": [{"id": "v1", "kind": "front"}, {"id": "v2", "kind": "top"}, {"id": "v3", "kind": "iso", "show_hidden": False}]}
    base.update(kw)
    return base


def test_gdt_rules():
    c = DrawingContent.model_validate(_content(annotations=[
        {"kind": "datum", "id": "dA", "view": "v1", "at": [0, 0], "letter": "A"},
        {"kind": "gdt", "id": "g1", "view": "v1", "at": [0, 0], "characteristic": "flatness", "tolerance": 0.05,
         "datums": [{"letter": "A"}]},
        {"kind": "gdt", "id": "g2", "view": "v1", "at": [0, 0], "characteristic": "position", "tolerance": 0.1, "diameter_zone": True},
        {"kind": "gdt", "id": "g3", "view": "v1", "at": [0, 0], "characteristic": "perpendicularity", "tolerance": 0.1,
         "datums": [{"letter": "B"}]},
        {"kind": "gdt", "id": "g4", "view": "v1", "at": [0, 0], "characteristic": "circularity", "tolerance": 0.01, "modifier": "M"},
        {"kind": "gdt", "id": "g5", "view": "v1", "at": [0, 0], "characteristic": "position", "tolerance": 0.2, "diameter_zone": True,
         "modifier": "M", "datums": [{"letter": "A"}], "standard": "ISO"},
    ]))
    msgs = {i["id"]: i["message"] for i in validate_gdt(c)}
    assert "cannot reference datums" in msgs["g1"]
    assert "requires at least one datum" in msgs["g2"]
    assert "no datum feature symbol defines it" in msgs["g3"]
    assert "not applicable to circularity" in msgs["g4"]
    assert "g5" not in msgs  # valid
    asme = DrawingContent.model_validate(_content(sheet={"standard": "ASME"}, annotations=[
        {"kind": "datum", "id": "dA", "view": "v1", "at": [0, 0], "letter": "A"},
        {"kind": "gdt", "id": "c1", "view": "v1", "at": [0, 0], "characteristic": "concentricity", "tolerance": 0.02,
         "datums": [{"letter": "A"}]},
        {"kind": "gdt", "id": "c2", "view": "v1", "at": [0, 0], "characteristic": "flatness", "tolerance": 0.02, "standard": "ISO"},
    ]))
    m2 = {i["id"]: i["message"] for i in validate_gdt(asme)}
    assert "removed in ASME Y14.5-2018" in m2["c1"]
    assert "must not be mixed" in m2["c2"]
    with pytest.raises(Exception):
        DrawingContent.model_validate(_content(annotations=[{"kind": "datum", "id": "x", "view": "v1", "at": [0, 0], "letter": "I"}]))


def _setup(client, template="flange_coupling"):
    client.register()
    r = client.post("/projects", {"name": "Drawing test", "template": template}).json()
    return r["project"], r["document"]


def test_part_drawing_render_dimensions_exports_and_model_link(client):
    p, d = _setup(client)
    content = client.get(f"/documents/{d['id']}").json()["revision"]["content"]
    bore = next(f for f in content["features"] if f["name"] == "Bore")
    hub = next(f for f in content["features"] if f["name"] == "Hub" and f["type"] == "extrude")
    dims = [{"id": "dBore", "view": "v2", "kind": "diameter", "tolerance": {"kind": "fit", "fit": "H7"},
             "edge": {"kind": "between", "faces": sorted([f"{bore['id']}:wall:0", f"{hub['id']}:end"])}}]
    r = client.post(f"/projects/{p['id']}/drawings", {"document_id": d["id"], "name": "Coupling drawing",
                                                      "content": _content(dimensions=dims)})
    assert r.status_code == 201, r.text
    did = r.json()["drawing"]["id"]
    g = client.get(f"/drawings/{did}").json()
    assert g["issues"] == [] and g["warnings"] == [], g["warnings"]
    svg = g["svg"]
    assert svg.startswith("<svg") and "FRONT" in svg and "⌀30 H7" in svg  # bore dimension from the model
    assert "⌀120" in svg or "120" in svg  # overall flange diameter
    assert g["projection"] == "first_angle" and g["scale"] in (1, 0.5, 2)
    pdf = client.get(f"/drawings/{did}/export", params={"format": "pdf"})
    assert pdf.status_code == 200 and pdf.content.startswith(b"%PDF")
    dxf = client.get(f"/drawings/{did}/export", params={"format": "dxf"})
    import ezdxf

    doc = ezdxf.read(io.StringIO(dxf.content.decode()))
    layers = {l.dxf.name for l in doc.layers}
    assert {"VISIBLE", "HIDDEN", "CENTER", "DIM"} <= layers
    assert len(doc.modelspace().query("LWPOLYLINE")) > 10
    # Model changes propagate: bore 30 -> 35
    content["parameters"] = [dict(x, expression="35") if x["name"] == "bore" else x for x in content["parameters"]]
    client.post(f"/documents/{d['id']}/revisions", {"base_revision": 1, "content": content, "op_id": "op-" + uuid.uuid4().hex})
    g2 = client.get(f"/drawings/{did}").json()
    assert "⌀35 H7" in g2["svg"] and g2["model_revision"] == 2 and not g2["stale"]
    # Pinning to revision 1 keeps the old geometry and flags staleness
    s = client.put(f"/drawings/{did}", {"content": g2["drawing"]["content"], "version": 1, "revision_pin": 1})
    assert s.status_code == 200
    g3 = client.get(f"/drawings/{did}").json()
    assert "⌀30 H7" in g3["svg"] and g3["stale"]
    assert client.put(f"/drawings/{did}", {"content": g2["drawing"]["content"], "version": 1}).status_code == 409


def test_export_blocked_on_invalid_gdt(client):
    p, d = _setup(client, "l_bracket")
    bad = _content(annotations=[{"kind": "gdt", "id": "g", "view": "v1", "at": [0, 0], "characteristic": "parallelism",
                                 "tolerance": 0.1}])
    did = client.post(f"/projects/{p['id']}/drawings", {"document_id": d["id"], "name": "x", "content": bad}).json()["drawing"]["id"]
    r = client.get(f"/drawings/{did}/export", params={"format": "pdf"})
    assert r.status_code == 422 and r.json()["error"]["code"] == "drawing_invalid"


def test_assembly_drawing_has_bom_and_balloons(client):
    from tests.test_assembly import pin_part, plate_part

    client.register()
    p = client.post("/projects", {"name": "Asm drawing"}).json()["project"]
    pd, box, hole = plate_part()
    nd, cyl = pin_part()
    pid = client.post(f"/projects/{p['id']}/documents", {"name": "Plate", "content": pd.model_dump(mode="json")}).json()["document"]["id"]
    nid = client.post(f"/projects/{p['id']}/documents", {"name": "Pin", "content": nd.model_dump(mode="json")}).json()["document"]["id"]
    content = {"instances": [{"id": "a", "name": "Plate", "source": {"kind": "document", "document_id": pid}, "fixed": True},
                             {"id": "b", "name": "Pin", "source": {"kind": "document", "document_id": nid},
                              "placement": {"position": [30, 30, -5], "rotation": [0, 0, 0]}}]}
    asm = client.post(f"/projects/{p['id']}/documents", {"name": "Hinge", "kind": "assembly", "content": content}).json()["document"]
    did = client.post(f"/projects/{p['id']}/drawings", {"document_id": asm["id"], "name": "Hinge GA"}).json()["drawing"]["id"]
    g = client.get(f"/drawings/{did}").json()
    assert "ITEM" in g["svg"] and "Plate" in g["svg"] and "Pin" in g["svg"]
    assert g["svg"].count("<circle") >= 2  # balloons
