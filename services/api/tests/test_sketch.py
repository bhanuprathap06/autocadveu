import math

import pytest

from autoforge.kernel import schema as S
from autoforge.sketch import profile, solver


def rect_sketch(w=40.0, h=20.0, dims=True, sloppy=True):
    """Rectangle drawn slightly off, with H/V constraints and optional dims."""
    j = 0.7 if sloppy else 0.0
    ents = [
        S.SkPoint(id="a", x=0 + j, y=0 - j),
        S.SkPoint(id="b", x=w - j, y=0 + j),
        S.SkPoint(id="c", x=w + j, y=h - j),
        S.SkPoint(id="d", x=0 - j, y=h + j),
        S.SkLine(id="l1", p1="a", p2="b"),
        S.SkLine(id="l2", p1="b", p2="c"),
        S.SkLine(id="l3", p1="c", p2="d"),
        S.SkLine(id="l4", p1="d", p2="a"),
    ]
    cons = [
        S.SkConstraint(id="h1", type="horizontal", refs=["l1"]),
        S.SkConstraint(id="h2", type="horizontal", refs=["l3"]),
        S.SkConstraint(id="v1", type="vertical", refs=["l2"]),
        S.SkConstraint(id="v2", type="vertical", refs=["l4"]),
    ]
    if dims:
        cons += [
            S.SkConstraint(id="fx", type="fix", refs=["a"]),
            S.SkConstraint(id="w", type="length", refs=["l1"], value=w),
            S.SkConstraint(id="h", type="length", refs=["l2"], value="height"),
        ]
    return S.SketchParams(entities=ents, constraints=cons)


def test_rectangle_fully_constrained_with_parameter():
    sk = rect_sketch(w=40, h=20)
    r = solver.solve(sk, {"height": 25.0})
    assert r.converged
    assert r.status == "fully_constrained", r.to_dict()
    assert r.dof == 0
    a, b, c, d = (r.points[k] for k in "abcd")
    assert math.isclose(b[0] - a[0], 40, abs_tol=1e-7)
    assert math.isclose(c[1] - b[1], 25, abs_tol=1e-7)
    assert math.isclose(a[1], b[1], abs_tol=1e-9) and math.isclose(c[0], b[0], abs_tol=1e-9)


def test_under_constrained_reports_dof_and_free_entities():
    sk = rect_sketch(dims=False)
    r = solver.solve(sk)
    assert r.converged
    assert r.status == "under_constrained"
    # 8 coordinates, 4 independent H/V constraints -> 4 DOF (x0, y0, width, height)
    assert r.dof == 4
    assert "l1" in r.free_entities


def test_conflicting_constraints_detected_and_isolated():
    sk = rect_sketch()
    sk.constraints.append(S.SkConstraint(id="bad", type="length", refs=["l3"], value=55))
    r = solver.solve(sk, {"height": 20})
    assert not r.converged
    assert r.status == "conflicting"
    assert r.conflicting == ["bad"]
    # Unsolved sketch must not return moved geometry.
    assert r.points["a"] == (0.7, -0.7)


def test_redundant_but_consistent_is_over_constrained():
    sk = rect_sketch()
    sk.constraints.append(S.SkConstraint(id="dup", type="length", refs=["l3"], value=40))
    r = solver.solve(sk, {"height": 20})
    assert r.converged
    assert r.status == "over_constrained"
    assert "dup" in r.redundant


def test_circle_tangent_and_radius():
    sk = S.SketchParams(
        entities=[
            S.SkPoint(id="p1", x=0, y=0), S.SkPoint(id="p2", x=50, y=1),
            S.SkLine(id="l", p1="p1", p2="p2"),
            S.SkPoint(id="c", x=20, y=9), S.SkCircle(id="k", center="c", r=5),
        ],
        constraints=[
            S.SkConstraint(id="f", type="fix", refs=["p1"]),
            S.SkConstraint(id="hz", type="horizontal", refs=["l"]),
            S.SkConstraint(id="t", type="tangent", refs=["l", "k"]),
            S.SkConstraint(id="r", type="diameter", refs=["k"], value=16),
        ],
    )
    r = solver.solve(sk)
    assert r.converged
    assert math.isclose(r.radii["k"], 8, abs_tol=1e-8)
    assert math.isclose(r.points["c"][1] - r.points["p1"][1], 8, abs_tol=1e-7)


def test_angle_perpendicular_symmetric_midpoint():
    sk = S.SketchParams(
        entities=[
            S.SkPoint(id="o", x=0, y=0), S.SkPoint(id="x", x=10, y=0.5),
            S.SkPoint(id="q", x=1, y=8), S.SkLine(id="lx", p1="o", p2="x"), S.SkLine(id="lq", p1="o", p2="q"),
            S.SkPoint(id="s1", x=3, y=2), S.SkPoint(id="s2", x=4, y=-3), S.SkPoint(id="m", x=5, y=1),
        ],
        constraints=[
            S.SkConstraint(id="f", type="fix", refs=["o"]),
            S.SkConstraint(id="h", type="horizontal", refs=["lx"]),
            S.SkConstraint(id="a", type="angle", refs=["lx", "lq"], value=30),
            S.SkConstraint(id="sy", type="symmetric", refs=["s1", "s2", "lx"]),
            S.SkConstraint(id="mp", type="midpoint", refs=["m", "lx"]),
        ],
    )
    r = solver.solve(sk)
    assert r.converged
    o, q = r.points["o"], r.points["q"]
    ang = math.degrees(math.atan2(q[1] - o[1], q[0] - o[0]))
    assert math.isclose(ang % 180, 30, abs_tol=1e-6)
    assert math.isclose(r.points["s1"][1], -r.points["s2"][1], abs_tol=1e-7)
    assert math.isclose(r.points["s1"][0], r.points["s2"][0], abs_tol=1e-7)
    assert math.isclose(r.points["m"][0], r.points["x"][0] / 2, abs_tol=1e-7)


def test_unknown_reference_is_reported_not_crashing():
    sk = rect_sketch(dims=False)
    sk.constraints.append(S.SkConstraint(id="oops", type="horizontal", refs=["nope"]))
    r = solver.solve(sk)
    assert "oops" in r.constraint_errors


def test_drag_moves_unconstrained_geometry_only():
    sk = rect_sketch(sloppy=False)
    r = solver.solve(sk, {"height": 20}, drag={"c": (100.0, 100.0)})
    # Fully constrained: drag cannot move it; solver falls back to constraints.
    assert r.converged and math.isclose(r.points["c"][0], 40, abs_tol=1e-6)
    sk2 = rect_sketch(dims=False, sloppy=False)
    r2 = solver.solve(sk2, drag={"c": (60.0, 30.0)})
    assert r2.converged
    assert math.isclose(r2.points["c"][0], 60, abs_tol=1e-6) and math.isclose(r2.points["b"][0], 60, abs_tol=1e-6)


# ------------------------------------------------------------ profiles

def test_profile_rectangle_with_hole_and_island():
    sk = rect_sketch(sloppy=False)
    sk.entities += [S.SkPoint(id="cc", x=20, y=10), S.SkCircle(id="hole", center="cc", r=4),
                    S.SkPoint(id="cc2", x=20, y=10), S.SkCircle(id="isl", center="cc2", r=2)]
    r = solver.solve(sk, {"height": 20})
    pr = profile.analyze(sk, r)
    assert not pr.errors
    ids = {g.id: g for g in pr.regions}
    outer = ids["r:l1+l2+l3+l4"]
    assert outer.depth == 0 and len(outer.holes) == 1
    assert math.isclose(outer.area, 800 - math.pi * 16, rel_tol=2e-3)
    assert {g.id for g in pr.default_regions()} == {"r:l1+l2+l3+l4", "r:isl"}


def test_profile_shared_edge_two_regions():
    # Rectangle split by a vertical line into two regions.
    ents = [S.SkPoint(id=n, x=x, y=y) for n, x, y in
            [("a", 0, 0), ("m", 20, 0), ("b", 40, 0), ("c", 40, 20), ("n", 20, 20), ("d", 0, 20)]]
    ents += [S.SkLine(id="e1", p1="a", p2="m"), S.SkLine(id="e2", p1="m", p2="b"), S.SkLine(id="e3", p1="b", p2="c"),
             S.SkLine(id="e4", p1="c", p2="n"), S.SkLine(id="e5", p1="n", p2="d"), S.SkLine(id="e6", p1="d", p2="a"),
             S.SkLine(id="mid", p1="m", p2="n")]
    sk = S.SketchParams(entities=ents)
    pr = profile.analyze(sk, solver.solve(sk))
    assert sorted(round(g.area) for g in pr.regions) == [400, 400]


def test_profile_detects_crossing_and_open():
    ents = [S.SkPoint(id="a", x=0, y=0), S.SkPoint(id="b", x=10, y=10), S.SkPoint(id="c", x=0, y=10),
            S.SkPoint(id="d", x=10, y=0), S.SkLine(id="x1", p1="a", p2="b"), S.SkLine(id="x2", p1="c", p2="d")]
    sk = S.SketchParams(entities=ents)
    pr = profile.analyze(sk, solver.solve(sk))
    assert pr.errors and pr.errors[0]["code"] == "self_intersection"

    sk2 = S.SketchParams(entities=ents[:3] + [S.SkLine(id="o1", p1="a", p2="b"), S.SkLine(id="o2", p1="b", p2="c")])
    pr2 = profile.analyze(sk2, solver.solve(sk2))
    assert not pr2.regions
    assert any(w["code"] == "open_profile" for w in pr2.warnings)
    assert pr2.open_chains and len(pr2.open_chains[0]) == 2


def test_profile_arc_slot():
    ents = [S.SkPoint(id=n, x=x, y=y) for n, x, y in
            [("a", 0, -5), ("b", 30, -5), ("c", 30, 5), ("d", 0, 5), ("c1", 30, 0), ("c2", 0, 0)]]
    ents += [S.SkLine(id="l1", p1="a", p2="b"), S.SkArc(id="ar1", center="c1", start="b", end="c"),
             S.SkLine(id="l2", p1="c", p2="d"), S.SkArc(id="ar2", center="c2", start="d", end="a")]
    sk = S.SketchParams(entities=ents)
    pr = profile.analyze(sk, solver.solve(sk))
    assert len(pr.regions) == 1
    assert math.isclose(pr.regions[0].area, 300 + math.pi * 25, rel_tol=2e-3)
