import math
import struct

import pytest

from autoforge.kernel import analysis, engine, io as kio, occ
from autoforge.kernel import schema as S
from autoforge.kernel.builder import DocBuilder
from autoforge.kernel.tessellate import tessellate_body


def plate_doc(w="width", h="40", t="thick", extra=None):
    b = DocBuilder()
    b.param("width", "80").param("thick", "10")
    sk = b.sketch(name="Base")
    r = sk.rect(0, 0, 80, 40, wexpr=w, hexpr=h)
    sid = b.commit(sk)
    ext = b.feature(S.ExtrudeF, "Plate", sketch=sid, distance=t, body="Plate")
    return b, sid, ext, r


def vol(res, body):
    return occ.volume_props(res.ctx.bodies[body].shape).Mass()


def assert_ok(res):
    bad = [f.to_dict() for f in res.features if f.status not in ("ok", "warning", "suppressed")]
    assert not bad, bad


def test_extrude_volume_validity_and_names():
    b, sid, ext, r = plate_doc()
    res = engine.recompute(b.build(), use_cache=False)
    assert_ok(res)
    assert math.isclose(vol(res, "Plate"), 80 * 40 * 10, rel_tol=1e-9)
    body = res.ctx.bodies["Plate"]
    assert occ.is_valid(body.shape)
    names = {n for _, ns in body.names_by_face() for n in ns}
    assert {f"{ext}:start", f"{ext}:end", f"{ext}:side:{r['bottom']}", f"{ext}:side:{r['top']}"} <= names
    assert all(not n.startswith("Plate:face#") for n in names)


def test_parameter_change_recomputes_only_downstream():
    engine.clear_cache()
    b, sid, ext, r = plate_doc()
    # An independent second body that does not use 'thick'.
    sk2 = b.sketch(S.PlaneRef(name="XY", offset=100))
    sk2.rect(0, 0, 10, 10, wexpr=10, hexpr=10)
    s2 = b.commit(sk2)
    e2 = b.feature(S.ExtrudeF, sketch=s2, distance=5, body="Other")
    doc = b.build()
    engine.recompute(doc)
    doc.parameters[1].expression = "12"  # thick
    res = engine.recompute(doc)
    assert_ok(res)
    by = {f.id: f for f in res.features}
    assert by[sid].cached and by[s2].cached and by[e2].cached
    assert not by[ext].cached
    assert math.isclose(vol(res, "Plate"), 80 * 40 * 12, rel_tol=1e-9)


def test_cut_boolean_and_cut_warning():
    b, sid, ext, _ = plate_doc()
    sk = b.sketch(S.PlaneRef(kind="face", body="Plate", face=f"{ext}:end"))
    sk.circle(40, 20, 5, dexpr=10)
    s2 = b.commit(sk)
    b.feature(S.ExtrudeF, sketch=s2, distance=10, direction="reverse", operation="cut", body="Plate")
    res = engine.recompute(b.build(), use_cache=False)
    assert_ok(res)
    assert math.isclose(vol(res, "Plate"), 80 * 40 * 10 - math.pi * 25 * 10, rel_tol=1e-6)
    # Cutting in the wrong direction removes nothing and must warn, not silently succeed.
    doc = b.build()
    doc.features[-1].params.direction = "normal"
    res2 = engine.recompute(doc, use_cache=False)
    last = res2.features[-1]
    assert last.status == "warning" and "did not remove" in last.warnings[0]


def test_fillet_reference_survives_upstream_dimension_change():
    b, sid, ext, r = plate_doc()
    fil = b.feature(S.FilletF, body="Plate", radius=3,
                    edges=[S.EdgeRef(kind="between", faces=sorted([f"{ext}:end", f"{ext}:side:{r['right']}"]))])
    doc = b.build()
    res = engine.recompute(doc, use_cache=False)
    assert_ok(res)
    loss = (3 * 3 - math.pi * 9 / 4) * 40  # quarter-round removed along 40 mm edge
    assert math.isclose(vol(res, "Plate"), 80 * 40 * 10 - loss, rel_tol=1e-6)
    names = {n for _, ns in res.ctx.bodies["Plate"].names_by_face() for n in ns}
    assert any(n.startswith(f"{fil}:fillet[") for n in names)
    doc.parameters[0].expression = "120"
    res2 = engine.recompute(doc)
    assert_ok(res2)
    assert math.isclose(vol(res2, "Plate"), 120 * 40 * 10 - loss, rel_tol=1e-6)


def test_fillet_too_large_is_actionable_error():
    b, sid, ext, r = plate_doc()
    b.feature(S.FilletF, body="Plate", radius=50, edges=[S.EdgeRef(kind="between", faces=sorted([f"{ext}:end", f"{ext}:side:{r['right']}"]))])
    res = engine.recompute(b.build(), use_cache=False)
    last = res.features[-1]
    assert last.status == "error"
    assert "radius" in last.message.lower()
    # Body is left unchanged (no partial/invalid geometry committed).
    assert math.isclose(vol(res, "Plate"), 80 * 40 * 10, rel_tol=1e-9)


def test_unresolvable_reference_reports_reselect():
    b, sid, ext, r = plate_doc()
    b.feature(S.ChamferF, body="Plate", distance=1, edges=[S.EdgeRef(kind="between", faces=["nope:a", "nope:b"])])
    res = engine.recompute(b.build(), use_cache=False)
    assert res.features[-1].status == "error" and res.features[-1].code == "unresolved_reference"


def test_chamfer_all_edges_of_face():
    b, sid, ext, r = plate_doc()
    b.feature(S.ChamferF, body="Plate", distance=1, edges=[S.EdgeRef(kind="of_face", face=f"{ext}:end")])
    res = engine.recompute(b.build(), use_cache=False)
    assert_ok(res)
    # 4 chamfers 1x1 along perimeter 2*(80+40) minus corner overlaps (small): volume decreases by ~0.5*240
    v = vol(res, "Plate")
    assert 32000 - 0.5 * 240 - 2 < v < 32000 - 0.5 * 240 + 2


def test_hole_counterbore_and_circular_pattern():
    b = DocBuilder()
    sk = b.sketch()
    sk.circle(0, 0, 50, dexpr=100)
    sid = b.commit(sk)
    ext = b.feature(S.ExtrudeF, sketch=sid, distance=12, body="Disc")
    hole = b.feature(S.HoleF, body="Disc", plane=S.PlaneRef(kind="face", body="Disc", face=f"{ext}:end"),
                     positions=[(35, 0)], diameter=9, end="through_all", cbore_diameter=15, cbore_depth=4)
    b.feature(S.CircularPatternF, body="Disc", features=[hole], axis=S.AxisRef(kind="standard", name="Z"), count=5)
    res = engine.recompute(b.build(), use_cache=False)
    assert_ok(res)
    one = math.pi * (4.5**2) * 8 + math.pi * (7.5**2) * 4
    assert math.isclose(vol(res, "Disc"), math.pi * 2500 * 12 - 5 * one, rel_tol=1e-6)
    names = {n for _, ns in res.ctx.bodies["Disc"].names_by_face() for n in ns}
    assert f"{hole}:wall:0" in names and f"{hole}:cbore_floor:0" in names


def test_revolve_cylinder_and_sweep_and_loft():
    b = DocBuilder()
    sk = b.sketch(S.PlaneRef(name="XZ"))
    pts = sk.polyline([(0, 0), (20, 0), (20, 50), (0, 50)])
    sid = b.commit(sk)
    b.feature(S.RevolveF, sketch=sid, axis=S.AxisRef(kind="sketch_line", sketch=sid, entity=pts[3]), angle=360, body="Shaft")
    # Sweep a circle along a straight path
    prof = b.sketch(S.PlaneRef(name="XY", offset=100))
    prof.circle(0, 0, 5, dexpr=10)
    ps = b.commit(prof)
    path = b.sketch(S.PlaneRef(name="XZ"))
    a, c = path.point(0, 100), path.point(0, 160)
    path.line(a, c)
    pp = b.commit(path)
    b.feature(S.SweepF, sketch=ps, path_sketch=pp, body="Rod")
    # Loft square 20 -> 10 over 30
    s1 = b.sketch(S.PlaneRef(name="XY", offset=200))
    s1.rect(-10, -10, 20, 20)
    l1 = b.commit(s1)
    s2 = b.sketch(S.PlaneRef(name="XY", offset=230))
    s2.rect(-5, -5, 10, 10)
    l2 = b.commit(s2)
    b.feature(S.LoftF, sketches=[l1, l2], ruled=True, body="Frustum")
    res = engine.recompute(b.build(), use_cache=False)
    assert_ok(res)
    assert math.isclose(vol(res, "Shaft"), math.pi * 400 * 50, rel_tol=1e-6)
    assert math.isclose(vol(res, "Rod"), math.pi * 25 * 60, rel_tol=1e-6)
    assert math.isclose(vol(res, "Frustum"), 30 / 3 * (400 + 100 + math.sqrt(400 * 100)), rel_tol=1e-6)


def test_shell_linear_pattern_mirror_primitive_boolean():
    b = DocBuilder()
    box = b.feature(S.PrimitiveF, shape="box", size=(100, 60, 40), body="Case")
    b.feature(S.ShellF, body="Case", remove_faces=[f"{box}:+z"], thickness=2)
    res = engine.recompute(b.build(), use_cache=False)
    assert_ok(res)
    assert math.isclose(vol(res, "Case"), 100 * 60 * 40 - 96 * 56 * 38, rel_tol=1e-6)

    b2 = DocBuilder()
    bx = b2.feature(S.PrimitiveF, shape="box", size=(100, 20, 10), body="Bar")
    cyl = b2.feature(S.PrimitiveF, shape="cylinder", size=(2, 20, 0), origin=(10, 10, -5), operation="cut", body="Bar")
    b2.feature(S.LinearPatternF, body="Bar", features=[cyl], direction=S.AxisRef(name="X"), count=4, spacing=20)
    b2.feature(S.MirrorF, body="Bar", plane=S.PlaneRef(name="YZ"))
    t = b2.feature(S.PrimitiveF, shape="box", size=(10, 10, 10), origin=(-5, 5, 10), body="Tool")
    b2.feature(S.BooleanF, operation="join", target="Bar", tools=["Tool"])
    res2 = engine.recompute(b2.build(), use_cache=False)
    assert_ok(res2)
    one = 100 * 20 * 10 - 4 * math.pi * 4 * 10
    assert math.isclose(vol(res2, "Bar"), 2 * one + 1000, rel_tol=1e-6)
    assert "Tool" not in res2.ctx.bodies


def test_graph_errors_forward_reference_and_param_cycle():
    b, sid, ext, _ = plate_doc()
    doc = b.build()
    doc.features[1].params.sketch = "doesnotexist"
    res = engine.recompute(doc, use_cache=False)
    assert res.features[1].status == "error" and "unknown feature" in res.features[1].message
    doc2 = plate_doc()[0].build()
    doc2.features.reverse()  # extrude before its sketch
    res2 = engine.recompute(doc2, use_cache=False)
    assert res2.features[0].status == "error" and "forward reference" in res2.features[0].message
    doc3 = plate_doc()[0].build()
    doc3.parameters[0].expression = "thick * 2"
    doc3.parameters[1].expression = "width / 2"
    res3 = engine.recompute(doc3, use_cache=False)
    assert "Circular reference" in res3.parameter_errors["width"]
    assert not res3.ok


def test_conflicting_sketch_blocks_extrude():
    b, sid, ext, r = plate_doc()
    doc = b.build()
    doc.features[0].params.constraints.append(S.SkConstraint(id="bad", type="length", refs=[r["top"]], value=10))
    res = engine.recompute(doc, use_cache=False)
    assert res.features[0].status == "warning"
    assert res.features[1].status == "error" and res.features[1].code == "sketch_conflict"


def test_suppression_and_rollback():
    b, sid, ext, r = plate_doc()
    fil = b.feature(S.FilletF, body="Plate", radius=2, edges=[S.EdgeRef(kind="of_face", face=f"{ext}:end")])
    doc = b.build()
    doc.features[-1].suppressed = True
    res = engine.recompute(doc, use_cache=False)
    assert res.features[-1].status == "suppressed"
    assert math.isclose(vol(res, "Plate"), 32000, rel_tol=1e-9)
    doc.features[-1].suppressed = False
    res2 = engine.recompute(doc, rollback_to=ext, use_cache=False)
    assert res2.features[-1].status == "skipped"


def test_step_iges_stl_obj_glb_roundtrip_and_verification():
    b, sid, ext, r = plate_doc()
    b.feature(S.HoleF, body="Plate", plane=S.PlaneRef(kind="face", body="Plate", face=f"{ext}:end"), positions=[(20, 20)], diameter=8)
    res = engine.recompute(b.build(), use_cache=False)
    shape = res.ctx.bodies["Plate"].shape
    v0 = occ.volume_props(shape).Mass()
    step = kio.export_step([("Plate", shape)], "TestPart")
    assert step.startswith(b"ISO-10303-21")
    assert b"Plate" in step
    s2, diag = kio.read_shape_bytes(step, "step")
    assert diag["valid"] and diag["solids"] == 1
    assert math.isclose(occ.volume_props(s2).Mass(), v0, rel_tol=1e-6)
    bb0, bb1 = occ.bbox(shape), occ.bbox(s2)
    assert all(math.isclose(a, c, abs_tol=1e-4) for a, c in zip(bb0[0] + bb0[1], bb1[0] + bb1[1]))
    iges = kio.export_iges([shape])
    s3, d3 = kio.read_shape_bytes(iges, "iges")
    assert d3["faces"] == len(occ.faces(shape))
    stl = kio.export_stl(shape)
    n, verts = kio.parse_stl_binary(stl)
    assert n > 10
    assert math.isclose(float(verts[:, 0].max()), 80, abs_tol=1e-3)
    obj = kio.export_obj([("Plate", shape)])
    assert obj.count(b"\nf ") > 10
    glb = kio.export_glb([("Plate", shape, (0.6, 0.6, 0.65))])
    magic, ver, total = struct.unpack("<4sII", glb[:12])
    assert magic == b"glTF" and ver == 2 and total == len(glb)


def test_import_rejects_garbage_and_mesh_formats():
    with pytest.raises(kio.ImportError_) as e:
        kio.read_shape_bytes(b"solid x\nendsolid", None, "part.stl")
    assert e.value.code == "unsupported_format"
    with pytest.raises(kio.ImportError_) as e2:
        kio.read_shape_bytes(b"ISO-10303-21;\nHEADER;garbage", "step")
    assert e2.value.code in ("parse_failed", "no_geometry")
    with pytest.raises(kio.ImportError_):
        kio.read_shape_bytes(b"", "step")


def test_import_feature_uses_blob_loader():
    b, sid, ext, r = plate_doc()
    shape = engine.recompute(b.build(), use_cache=False).ctx.bodies["Plate"].shape
    data = kio.export_step([("P", shape)])
    sha = kio.sha256(data)
    b2 = DocBuilder()
    b2.feature(S.ImportF, blob_key="k", sha256=sha, filename="p.step", body="Imported")
    res = engine.recompute(b2.build(), blob_loader=lambda k, h: data, use_cache=False)
    assert_ok(res)
    assert math.isclose(vol(res, "Imported"), 32000, rel_tol=1e-6)


def test_tessellation_tags_faces_and_edges():
    b, sid, ext, r = plate_doc()
    res = engine.recompute(b.build(), use_cache=False)
    t = tessellate_body(res.ctx.bodies["Plate"])
    assert t["triangle_count"] >= 12
    assert sum(f["count"] for f in t["faces"]) == t["triangle_count"]
    assert {f["name"] for f in t["faces"]} >= {f"{ext}:start", f"{ext}:end"}
    assert len(t["edges"]) == 12
    assert all(len(e["ref"]["faces"]) == 2 for e in t["edges"])
    plane_face = next(f for f in t["faces"] if f["name"] == f"{ext}:end")
    assert plane_face["geometry"]["surface"] == "plane"
    assert math.isclose(plane_face["geometry"]["normal"][2], 1.0)


def test_mass_properties_and_interference():
    b = DocBuilder()
    b.feature(S.PrimitiveF, shape="box", size=(10, 20, 30), body="A")
    b.feature(S.PrimitiveF, shape="box", size=(10, 10, 10), origin=(5, 5, 5), body="B")
    b.feature(S.PrimitiveF, shape="box", size=(10, 10, 10), origin=(50, 0, 0), body="C")
    res = engine.recompute(b.build(), use_cache=False)
    mp = analysis.mass_properties(res.ctx.bodies["A"].shape, 7850)
    assert math.isclose(mp["mass_kg"], 6000e-9 * 7850, rel_tol=1e-9)
    assert all(math.isclose(a, c, abs_tol=1e-9) for a, c in zip(mp["center_of_mass_mm"], [5, 10, 15]))
    # Ixx about COM for box: m(b^2+c^2)/12
    m = mp["mass_kg"]
    assert math.isclose(mp["inertia_kg_mm2"][0][0], m * (20**2 + 30**2) / 12, rel_tol=1e-6)
    items = [(n, res.ctx.bodies[n].shape) for n in "ABC"]
    rep = {(r["a"], r["b"]): r for r in analysis.interference(items, clearance_mm=5)}
    assert rep[("A", "B")]["interferes"] and math.isclose(rep[("A", "B")]["volume_mm3"], 5 * 10 * 10, rel_tol=1e-6)
    assert not rep[("A", "C")]["interferes"] and rep[("A", "C")]["clearance_ok"]
    meas = analysis.measure(res.ctx.bodies, {"body": "A", "kind": "body"}, {"body": "C", "kind": "body"})
    assert math.isclose(meas["distance_mm"], 40, abs_tol=1e-9)


def test_recompute_is_deterministic():
    b, sid, ext, r = plate_doc()
    b.feature(S.FilletF, body="Plate", radius=2, edges=[S.EdgeRef(kind="of_face", face=f"{ext}:end")])
    doc = b.build()
    t1 = tessellate_body(engine.recompute(doc, use_cache=False).ctx.bodies["Plate"])
    t2 = tessellate_body(engine.recompute(doc, use_cache=False).ctx.bodies["Plate"])
    assert [f["name"] for f in t1["faces"]] == [f["name"] for f in t2["faces"]]
    assert t1["positions"] == t2["positions"]
