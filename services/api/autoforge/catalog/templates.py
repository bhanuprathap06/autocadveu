"""Starter templates: editable, fully dimensioned parametric documents.

Templates are *application-generated example geometry*. They are illustrative
starting points with plausible proportions; they are NOT manufacturer designs
and carry no load rating, fit or safety validation. Each template exposes its
driving dimensions as named parameters so it can be edited like any other part.
"""

from __future__ import annotations

from typing import Any, Callable

from ..kernel import schema as S
from ..kernel.builder import DocBuilder, SketchBuilder

STEEL = S.MaterialAssignment(name="Structural steel (generic, typical values)", density_kg_m3=7850, youngs_modulus_mpa=200000,
                             poisson_ratio=0.29, yield_strength_mpa=250, thermal_conductivity_w_mk=50, specific_heat_j_kgk=490,
                             source="Generic handbook typical values; replace with your supplier's certified data",
                             material_id="steel-generic")
ALU = S.MaterialAssignment(name="Aluminium 6061-T6 (typical values)", density_kg_m3=2700, youngs_modulus_mpa=68900,
                           poisson_ratio=0.33, yield_strength_mpa=276, thermal_conductivity_w_mk=167, specific_heat_j_kgk=896,
                           source="Typical published values for 6061-T6; verify against certified material data",
                           material_id="al-6061-t6")
GREY_IRON = S.MaterialAssignment(name="Grey cast iron (typical values)", density_kg_m3=7200, youngs_modulus_mpa=110000,
                                 poisson_ratio=0.26, thermal_conductivity_w_mk=46, specific_heat_j_kgk=490,
                                 source="Typical published values for grey iron; grade-dependent", material_id="grey-iron")

COMMON_CAVEAT = ("Illustrative application-generated geometry. Not a manufacturer design; dimensions, fits and strength are not "
                 "validated. Independent engineering verification is required before any physical use.")


def _poly_constrained(sk: SketchBuilder, pts: list[tuple[float, float]], coords: list[tuple[S.Expr, S.Expr]]) -> list[str]:
    """Closed polyline where every vertex is dimensioned by coordinate expressions."""
    ids = [sk.point(x, y) for x, y in pts]
    lines = [sk.line(ids[i], ids[(i + 1) % len(ids)]) for i in range(len(ids))]
    for pid, (cx, cy) in zip(ids, coords):
        sk.con("coordinate_x", [pid], cx)
        sk.con("coordinate_y", [pid], cy)
    return lines


def l_bracket() -> S.Document:
    b = DocBuilder()
    b.param("L", "120", description="Base leg length").param("H", "80", description="Upright height")
    b.param("W", "60", description="Bracket width").param("t", "8", description="Plate thickness")
    b.param("hole_d", "9", description="Bolt clearance hole (M8 medium fit per ISO 273)")
    b.param("bend_r", "6", description="Inside corner fillet radius")
    sk = b.sketch(S.PlaneRef(name="XZ"), name="L profile")
    p = [sk.point(x, y) for x, y in [(0, 0), (120, 0), (120, 8), (8, 8), (8, 80), (0, 80)]]
    l = [sk.line(p[i], p[(i + 1) % 6]) for i in range(6)]
    for i in (0, 2, 4):
        sk.con("horizontal", [l[i]])
    for i in (1, 3, 5):
        sk.con("vertical", [l[i]])
    sk.con("coordinate_x", [p[0]], 0)
    sk.con("coordinate_y", [p[0]], 0)
    sk.con("length", [l[0]], "L")
    sk.con("length", [l[5]], "H")
    sk.con("length", [l[1]], "t")
    sk.con("length", [l[4]], "t")
    sid = b.commit(sk)
    ext = b.feature(S.ExtrudeF, "Bracket body", sketch=sid, distance="W", body="Bracket")
    b.feature(S.FilletF, "Inside bend fillet", body="Bracket", radius="bend_r",
              edges=[S.EdgeRef(kind="between", faces=sorted([f"{ext}:side:{l[2]}", f"{ext}:side:{l[3]}"]))])
    b.feature(S.HoleF, "Base holes", body="Bracket", plane=S.PlaneRef(kind="face", body="Bracket", face=f"{ext}:side:{l[2]}"),
              positions=[("L - 20", "-W/4"), ("L - 20", "-3*W/4")], diameter="hole_d")
    b.feature(S.HoleF, "Upright holes", body="Bracket", plane=S.PlaneRef(kind="face", body="Bracket", face=f"{ext}:side:{l[3]}"),
              positions=[("-W/4", "H - 20"), ("-3*W/4", "H - 20")], diameter="hole_d")
    b.material("Bracket", STEEL)
    return b.build()


def brake_rotor() -> S.Document:
    b = DocBuilder()
    for n, e, d in [("outer_d", "330", "Rotor outer diameter"), ("disc_t", "28", "Friction ring thickness"),
                    ("hat_d", "180", "Hat outer diameter"), ("wall", "6", "Hat wall thickness"),
                    ("hat_h", "50", "Hat height"), ("top_t", "8", "Mounting face thickness"),
                    ("bore_d", "70", "Centre bore"), ("pcd", "120", "Bolt pitch circle diameter"),
                    ("bolt_d", "14", "Wheel stud hole diameter")]:
        b.param(n, e, description=d)
    b.param("bolts", "5", kind="count", description="Number of wheel studs")
    sk = b.sketch(S.PlaneRef(name="XZ"), name="Rotor section")
    pts = [(35, 42), (84, 42), (84, 0), (165, 0), (165, 28), (90, 28), (90, 50), (35, 50)]
    coords = [("bore_d/2", "hat_h - top_t"), ("hat_d/2 - wall", "hat_h - top_t"), ("hat_d/2 - wall", 0), ("outer_d/2", 0),
              ("outer_d/2", "disc_t"), ("hat_d/2", "disc_t"), ("hat_d/2", "hat_h"), ("bore_d/2", "hat_h")]
    lines = _poly_constrained(sk, pts, coords)
    a0, a1 = sk.point(0, 0, construction=True), sk.point(0, 10, construction=True)
    axis = sk.line(a0, a1, construction=True)
    sk.con("coordinate_x", [a0], 0)
    sk.con("coordinate_y", [a0], 0)
    sk.con("vertical", [axis])
    sk.con("length", [axis], 10)
    sid = b.commit(sk)
    rev = b.feature(S.RevolveF, "Rotor body", sketch=sid, axis=S.AxisRef(kind="sketch_line", sketch=sid, entity=axis), body="Rotor")
    hole = b.feature(S.HoleF, "Stud hole", body="Rotor",
                     plane=S.PlaneRef(kind="face", body="Rotor", face=f"{rev}:side:{lines[6]}"),
                     positions=[("pcd/2", 0)], diameter="bolt_d")
    b.feature(S.CircularPatternF, "Stud pattern", body="Rotor", features=[hole], axis=S.AxisRef(name="Z"), count="bolts")
    b.material("Rotor", GREY_IRON)
    return b.build()


def stepped_shaft() -> S.Document:
    b = DocBuilder()
    b.param("d1", "40", description="Bearing seat diameter").param("l1", "60", description="Bearing seat length")
    b.param("d2", "30", description="Output diameter").param("l2", "90", description="Output length")
    b.param("c", "1.5", description="End chamfer")
    sk = b.sketch(S.PlaneRef(name="XZ"), name="Shaft half-section")
    pts = [(0, 0), (20, 0), (20, 60), (15, 60), (15, 150), (0, 150)]
    coords = [(0, 0), ("d1/2", 0), ("d1/2", "l1"), ("d2/2", "l1"), ("d2/2", "l1 + l2"), (0, "l1 + l2")]
    lines = _poly_constrained(sk, pts, coords)
    sid = b.commit(sk)
    rev = b.feature(S.RevolveF, "Shaft", sketch=sid, axis=S.AxisRef(kind="sketch_line", sketch=sid, entity=lines[5]), body="Shaft")
    b.feature(S.ChamferF, "End chamfers", body="Shaft", distance="c", edges=[
        S.EdgeRef(kind="between", faces=sorted([f"{rev}:side:{lines[0]}", f"{rev}:side:{lines[1]}"])),
        S.EdgeRef(kind="between", faces=sorted([f"{rev}:side:{lines[3]}", f"{rev}:side:{lines[4]}"])),
    ])
    b.material("Shaft", STEEL)
    return b.build()


def flange_coupling() -> S.Document:
    b = DocBuilder()
    b.param("D", "120", description="Flange diameter").param("tf", "16", description="Flange thickness")
    b.param("hub_d", "60", description="Hub diameter").param("hub_l", "40", description="Hub length")
    b.param("bore", "30", description="Bore diameter").param("pcd", "90", description="Bolt circle")
    b.param("bolt_d", "11", description="Bolt clearance (M10 medium)").param("n", "6", kind="count", description="Bolt count")
    sk = b.sketch(name="Flange")
    sk.circle(0, 0, 60, dexpr="D")
    s1 = b.commit(sk)
    e1 = b.feature(S.ExtrudeF, "Flange", sketch=s1, distance="tf", body="Coupling")
    sk2 = b.sketch(S.PlaneRef(kind="face", body="Coupling", face=f"{e1}:end"), name="Hub")
    sk2.circle(0, 0, 30, dexpr="hub_d")
    s2 = b.commit(sk2)
    e2 = b.feature(S.ExtrudeF, "Hub", sketch=s2, distance="hub_l", operation="join", body="Coupling")
    b.feature(S.HoleF, "Bore", body="Coupling", plane=S.PlaneRef(kind="face", body="Coupling", face=f"{e2}:end"),
              positions=[(0, 0)], diameter="bore")
    h = b.feature(S.HoleF, "Bolt hole", body="Coupling", plane=S.PlaneRef(kind="face", body="Coupling", face=f"{e1}:end"),
                  positions=[("pcd/2", 0)], diameter="bolt_d")
    b.feature(S.CircularPatternF, "Bolt pattern", body="Coupling", features=[h], axis=S.AxisRef(name="Z"), count="n")
    b.material("Coupling", STEEL)
    return b.build()


def battery_enclosure() -> S.Document:
    b = DocBuilder()
    b.param("L", "400").param("W", "250").param("H", "120").param("wall", "3").param("corner_r", "12")
    box = b.feature(S.PrimitiveF, "Enclosure block", shape="box", size=("L", "W", "H"), body="Enclosure")
    vert = [S.EdgeRef(kind="between", faces=sorted([f"{box}:{a}", f"{box}:{c}"])) for a in ("+x", "-x") for c in ("+y", "-y")]
    b.feature(S.FilletF, "Corner radii", body="Enclosure", radius="corner_r", edges=vert)
    b.feature(S.ShellF, "Shell (open top)", body="Enclosure", remove_faces=[f"{box}:+z"], thickness="wall")
    b.material("Enclosure", ALU)
    return b.build()


def sensor_housing() -> S.Document:
    b = DocBuilder()
    b.param("D", "40").param("H", "30").param("wall", "2.5").param("tab_l", "25").param("tab_t", "4").param("screw_d", "4.5")
    cyl = b.feature(S.PrimitiveF, "Housing", shape="cylinder", size=("D/2", "H", 0), body="Housing")
    b.feature(S.ShellF, "Hollow", body="Housing", remove_faces=[f"{cyl}:top"], thickness="wall")
    tab = b.feature(S.PrimitiveF, "Mounting tab", shape="box", size=("D/2 + tab_l", 16, "tab_t"), origin=(0, -8, 0),
                    operation="join", body="Housing")
    b.feature(S.HoleF, "Screw hole", body="Housing", plane=S.PlaneRef(kind="standard", name="XY", offset="tab_t"),
              positions=[("D/2 + tab_l/2 + 2", 0)], diameter="screw_d")
    b.material("Housing", ALU)
    return b.build()


def control_arm_plate() -> S.Document:
    b = DocBuilder()
    b.param("span", "320").param("reach", "260").param("t", "10").param("bush_d", "30").param("ball_d", "22")
    sk = b.sketch(name="Arm outline")
    _poly_constrained(sk, [(0, 0), (320, 0), (160, 260)], [(0, 0), ("span", 0), ("span/2", "reach")])
    s = b.commit(sk)
    e = b.feature(S.ExtrudeF, "Arm plate", sketch=s, distance="t", body="Arm")
    face = S.PlaneRef(kind="face", body="Arm", face=f"{e}:end")
    b.feature(S.HoleF, "Bushing bores", body="Arm", plane=face, positions=[(40, 20), ("span - 40", 20)], diameter="bush_d")
    b.feature(S.HoleF, "Ball joint bore", body="Arm", plane=face, positions=[("span/2", "reach - 45")], diameter="ball_d")
    b.material("Arm", STEEL)
    return b.build()


def cooling_plate() -> S.Document:
    b = DocBuilder()
    b.param("L", "400").param("W", "200").param("T", "10").param("ch_w", "8").param("ch_d", "5").param("pitch", "22")
    b.param("n", "8", kind="count")
    box = b.feature(S.PrimitiveF, "Plate", shape="box", size=("L", "W", "T"), body="Plate")
    sk = b.sketch(S.PlaneRef(kind="face", body="Plate", face=f"{box}:+z"), name="Channel")
    sk.rect(20, 23, 360, 8, wexpr="L - 40", hexpr="ch_w")
    s = b.commit(sk)
    cut = b.feature(S.ExtrudeF, "Channel cut", sketch=s, distance="ch_d", direction="reverse", operation="cut", body="Plate")
    b.feature(S.LinearPatternF, "Channel pattern", body="Plate", features=[cut], direction=S.AxisRef(name="Y"), count="n",
              spacing="pitch")
    b.material("Plate", ALU)
    return b.build()


TEMPLATES: dict[str, dict[str, Any]] = {
    "l_bracket": {"name": "L mounting bracket", "domain": "mechanical", "category": "bracket", "build": l_bracket,
                  "description": "Formed-style L bracket with inside fillet and clearance holes on both legs."},
    "brake_rotor": {"name": "Solid brake rotor (hat type)", "domain": "braking_steering", "category": "brake_disc",
                    "build": brake_rotor,
                    "description": "Revolved hat-type solid rotor with centre bore and patterned stud holes. No cooling vanes."},
    "stepped_shaft": {"name": "Stepped shaft", "domain": "powertrain", "category": "shaft", "build": stepped_shaft,
                      "description": "Two-diameter revolved shaft with end chamfers."},
    "flange_coupling": {"name": "Flange coupling half", "domain": "mechanical", "category": "coupling", "build": flange_coupling,
                        "description": "Flange with hub, bore and patterned bolt holes."},
    "battery_enclosure": {"name": "Battery module enclosure", "domain": "ev_battery", "category": "enclosure",
                          "build": battery_enclosure, "description": "Open-top shelled enclosure with corner radii."},
    "sensor_housing": {"name": "Sensor housing", "domain": "electronics", "category": "housing", "build": sensor_housing,
                       "description": "Cylindrical cup housing with a mounting tab and screw hole."},
    "control_arm_plate": {"name": "Control arm plate (simplified)", "domain": "chassis_suspension", "category": "control_arm",
                          "build": control_arm_plate,
                          "description": "Planar triangulated arm with two bushing bores and a ball-joint bore."},
    "cooling_plate": {"name": "Battery cooling plate (straight channels)", "domain": "ev_battery", "category": "cooling_plate",
                      "build": cooling_plate, "description": "Plate with a pattern of milled straight coolant channels."},
}

DOMAINS = {
    "mechanical": "Mechanical components",
    "chassis_suspension": "Vehicle chassis and suspension",
    "powertrain": "Powertrain and transmission",
    "ev_battery": "EV and battery systems",
    "combustion_cooling": "Engine combustion and cooling",
    "aero_body": "Aerodynamics and vehicle body",
    "braking_steering": "Braking and steering",
    "electronics": "Electronics, sensors and controls",
    "manufacturing": "Manufacturing and tolerancing",
}


def list_templates() -> list[dict[str, Any]]:
    return [{"key": k, "name": v["name"], "domain": v["domain"], "category": v["category"], "description": v["description"],
             "caveat": COMMON_CAVEAT, "status": "implemented_with_limitations"} for k, v in TEMPLATES.items()]


def build_template(key: str) -> S.Document:
    t = TEMPLATES.get(key)
    if t is None:
        raise KeyError(key)
    doc: S.Document = t["build"]()
    doc.metadata.update({"template": key, "caveat": COMMON_CAVEAT})
    return doc


BUILDERS: dict[str, Callable[[], S.Document]] = {k: v["build"] for k, v in TEMPLATES.items()}
