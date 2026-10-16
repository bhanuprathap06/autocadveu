"""Geometric constraint solver for 2D sketches.

Method
------
Unknowns are point coordinates plus circle radii and ellipse semi-axes. Every
constraint contributes one or more scalar residual equations. We solve
``F(x) = 0`` with damped Gauss-Newton using the *minimum-norm* least-squares
step (``lstsq``), so variables that are not pinned by constraints move as
little as possible from where the user drew them. Levenberg-Marquardt damping
is used as fallback when a full step does not reduce the residual.

Diagnostics
-----------
* DOF = number of unknowns - rank(J) at the solution (SVD, relative tolerance).
* Free variables are those with a non-zero component in the null space of J;
  they are mapped back to entities to report what is still under-constrained.
* Redundant constraints are found by incremental Gram-Schmidt over Jacobian rows
  (a constraint whose rows add no rank is redundant).
* Conflicts: if the system cannot be satisfied we re-solve adding constraints in
  order and report the first constraint that makes the system unsatisfiable,
  together with the constraints left unsatisfied by the least-squares solution.

The Jacobian is computed per constraint by central finite differences over only
the variables that constraint touches, which keeps cost proportional to the
number of constraints rather than unknowns x constraints.
"""

from __future__ import annotations

import math
import time
from dataclasses import dataclass, field
from typing import Any, Callable

import numpy as np

from ..kernel import expressions
from ..kernel import schema as S

TOL = 1e-8
MAX_ITER = 100
EPS = 1e-12


class SketchError(ValueError):
    pass


@dataclass
class Equation:
    cid: str
    idx: np.ndarray
    fn: Callable[[np.ndarray], np.ndarray]


@dataclass
class SolveResult:
    status: str  # empty | fully_constrained | under_constrained | over_constrained | conflicting
    dof: int
    converged: bool
    residual: float
    iterations: int
    points: dict[str, tuple[float, float]]
    radii: dict[str, float]
    ellipses: dict[str, tuple[float, float]]
    free_entities: list[str] = field(default_factory=list)
    redundant: list[str] = field(default_factory=list)
    conflicting: list[str] = field(default_factory=list)
    unsatisfied: list[str] = field(default_factory=list)
    constraint_errors: dict[str, str] = field(default_factory=dict)
    elapsed_ms: float = 0.0

    def to_dict(self) -> dict[str, Any]:
        return {
            "status": self.status,
            "dof": self.dof,
            "converged": self.converged,
            "residual": self.residual,
            "iterations": self.iterations,
            "free_entities": self.free_entities,
            "redundant": self.redundant,
            "conflicting": self.conflicting,
            "unsatisfied": self.unsatisfied,
            "constraint_errors": self.constraint_errors,
            "elapsed_ms": round(self.elapsed_ms, 3),
        }


class _Vars:
    def __init__(self, entities: list[Any]):
        self.values: list[float] = []
        self.owner: list[str] = []
        self.pt: dict[str, tuple[int, int]] = {}
        self.rad: dict[str, int] = {}
        self.ell: dict[str, tuple[int, int]] = {}
        self.ent: dict[str, Any] = {e.id: e for e in entities}
        for e in entities:
            if isinstance(e, S.SkPoint):
                self.pt[e.id] = (self._add(e.x, e.id), self._add(e.y, e.id))
        for e in entities:
            if isinstance(e, S.SkCircle):
                self.rad[e.id] = self._add(e.r, e.id)
            elif isinstance(e, S.SkEllipse):
                self.ell[e.id] = (self._add(e.rx, e.id), self._add(e.ry, e.id))
        # Validate references from curves to points.
        for e in entities:
            for pid in _point_refs(e):
                if pid not in self.pt:
                    raise SketchError(f"Entity {e.id!r} references unknown point {pid!r}")

    def _add(self, v: float, owner: str) -> int:
        self.values.append(float(v))
        self.owner.append(owner)
        return len(self.values) - 1

    def kind(self, eid: str) -> str:
        e = self.ent.get(eid)
        if e is None:
            raise SketchError(f"Unknown sketch entity {eid!r}")
        return e.type

    def P(self, pid: str) -> list[int]:
        if pid not in self.pt:
            raise SketchError(f"{pid!r} is not a point")
        return list(self.pt[pid])

    def L(self, lid: str) -> list[int]:
        e = self.ent.get(lid)
        if not isinstance(e, S.SkLine):
            raise SketchError(f"{lid!r} is not a line")
        return self.P(e.p1) + self.P(e.p2)

    def C(self, cid: str) -> tuple[list[int], str]:
        """Circle-like: returns indices and layout ('circle': cx,cy,r | 'arc': cx,cy,sx,sy)."""
        e = self.ent.get(cid)
        if isinstance(e, S.SkCircle):
            return self.P(e.center) + [self.rad[cid]], "circle"
        if isinstance(e, S.SkArc):
            return self.P(e.center) + self.P(e.start), "arc"
        raise SketchError(f"{cid!r} is not a circle or arc")


def _point_refs(e: Any) -> list[str]:
    if isinstance(e, S.SkLine):
        return [e.p1, e.p2]
    if isinstance(e, (S.SkCircle, S.SkEllipse)):
        return [e.center]
    if isinstance(e, S.SkArc):
        return [e.center, e.start, e.end]
    if isinstance(e, S.SkSpline):
        return list(e.points)
    return []


def _radius(v: np.ndarray, layout: str) -> float:
    if layout == "circle":
        return v[2]
    return math.hypot(v[2] - v[0], v[3] - v[1])


def _cross(ax, ay, bx, by):
    return ax * by - ay * bx


def _line_point_signed(v: np.ndarray) -> float:
    """v = [ax, ay, bx, by, px, py] -> signed distance of p from line ab."""
    dx, dy = v[2] - v[0], v[3] - v[1]
    n = math.hypot(dx, dy) + EPS
    return _cross(dx, dy, v[4] - v[0], v[5] - v[1]) / n


def _sgn(x: float) -> float:
    return -1.0 if x < 0 else 1.0


def compile_constraints(
    vars_: _Vars, constraints: list[S.SkConstraint], env: dict[str, float]
) -> tuple[list[Equation], dict[str, str]]:
    x0 = np.array(vars_.values)
    eqs: list[Equation] = []
    errors: dict[str, str] = {}

    def add(cid: str, idx: list[int], fn: Callable[[np.ndarray], Any]) -> None:
        eqs.append(Equation(cid, np.array(idx, dtype=int), lambda v, fn=fn: np.atleast_1d(np.asarray(fn(v), float))))

    for c in constraints:
        try:
            val = expressions.evaluate(c.value, env) if c.value is not None else None
            t, r = c.type, c.refs
            k = [vars_.kind(x) for x in r]
            if t == "coincident":
                if k == ["point", "point"]:
                    add(c.id, vars_.P(r[0]) + vars_.P(r[1]), lambda v: [v[0] - v[2], v[1] - v[3]])
                elif k[0] == "point" and k[1] == "line":
                    add(c.id, vars_.L(r[1]) + vars_.P(r[0]), _line_point_signed)
                elif k[0] == "point" and k[1] in ("circle", "arc"):
                    idx, lay = vars_.C(r[1])
                    add(c.id, idx + vars_.P(r[0]), lambda v, lay=lay: math.hypot(v[-2] - v[0], v[-1] - v[1]) - _radius(v[:-2], lay))
                else:
                    raise SketchError("coincident needs (point, point|line|circle)")
            elif t in ("horizontal", "vertical"):
                j = 1 if t == "horizontal" else 0
                idx = vars_.L(r[0]) if k == ["line"] else vars_.P(r[0]) + vars_.P(r[1])
                add(c.id, idx, lambda v, j=j: v[j] - v[2 + j])
            elif t in ("parallel", "perpendicular"):
                idx = vars_.L(r[0]) + vars_.L(r[1])

                def f(v, t=t):
                    d1x, d1y, d2x, d2y = v[2] - v[0], v[3] - v[1], v[6] - v[4], v[7] - v[5]
                    n = math.hypot(d1x, d1y) * math.hypot(d2x, d2y) + EPS
                    return (_cross(d1x, d1y, d2x, d2y) if t == "parallel" else d1x * d2x + d1y * d2y) / n

                add(c.id, idx, f)
            elif t == "tangent":
                if "line" in k:
                    li, ci = (r[0], r[1]) if k[0] == "line" else (r[1], r[0])
                    cidx, lay = vars_.C(ci)
                    idx = vars_.L(li) + cidx
                    s = _sgn(_line_point_signed(x0[idx[:6]]))
                    add(c.id, idx, lambda v, s=s, lay=lay: s * _line_point_signed(v[:6]) - _radius(v[4:], lay))
                else:
                    i1, l1 = vars_.C(r[0])
                    i2, l2 = vars_.C(r[1])
                    v0 = x0[i1 + i2]
                    n1 = len(i1)
                    d0 = math.hypot(v0[0] - v0[n1], v0[1] - v0[n1 + 1])
                    r1, r2 = _radius(v0[:n1], l1), _radius(v0[n1:], l2)
                    internal = d0 < max(r1, r2)
                    s = _sgn(r1 - r2)

                    def f(v, n1=n1, l1=l1, l2=l2, internal=internal, s=s):
                        d = math.hypot(v[0] - v[n1], v[1] - v[n1 + 1])
                        a, b = _radius(v[:n1], l1), _radius(v[n1:], l2)
                        return d - s * (a - b) if internal else d - (a + b)

                    add(c.id, i1 + i2, f)
            elif t == "concentric":
                i1, _ = vars_.C(r[0])
                i2, _ = vars_.C(r[1])
                add(c.id, i1[:2] + i2[:2], lambda v: [v[0] - v[2], v[1] - v[3]])
            elif t == "equal":
                if k == ["line", "line"]:
                    add(c.id, vars_.L(r[0]) + vars_.L(r[1]),
                        lambda v: math.hypot(v[2] - v[0], v[3] - v[1]) - math.hypot(v[6] - v[4], v[7] - v[5]))
                elif all(x in ("circle", "arc") for x in k):
                    i1, l1 = vars_.C(r[0])
                    i2, l2 = vars_.C(r[1])
                    n1 = len(i1)
                    add(c.id, i1 + i2, lambda v, n1=n1, l1=l1, l2=l2: _radius(v[:n1], l1) - _radius(v[n1:], l2))
                else:
                    raise SketchError("equal needs two lines or two circles/arcs")
            elif t == "symmetric":
                idx = vars_.P(r[0]) + vars_.P(r[1]) + vars_.L(r[2])

                def f(v):
                    mx, my = (v[0] + v[2]) / 2, (v[1] + v[3]) / 2
                    ax, ay, bx, by = v[4:8]
                    n = math.hypot(bx - ax, by - ay) + EPS
                    return [_cross(bx - ax, by - ay, mx - ax, my - ay) / n,
                            ((v[2] - v[0]) * (bx - ax) + (v[3] - v[1]) * (by - ay)) / n]

                add(c.id, idx, f)
            elif t == "midpoint":
                add(c.id, vars_.P(r[0]) + vars_.L(r[1]),
                    lambda v: [v[0] - (v[2] + v[4]) / 2, v[1] - (v[3] + v[5]) / 2])
            elif t == "point_on_line":
                add(c.id, vars_.L(r[1]) + vars_.P(r[0]), _line_point_signed)
            elif t == "point_on_circle":
                idx, lay = vars_.C(r[1])
                add(c.id, idx + vars_.P(r[0]),
                    lambda v, lay=lay: math.hypot(v[-2] - v[0], v[-1] - v[1]) - _radius(v[:-2], lay))
            elif t == "fix":
                pts: list[str] = []
                for eid in r:
                    pts.extend([eid] if vars_.kind(eid) == "point" else _point_refs(vars_.ent[eid]))
                for pid in pts:
                    ix, iy = vars_.P(pid)
                    tx, ty = x0[ix], x0[iy]
                    add(c.id, [ix, iy], lambda v, tx=tx, ty=ty: [v[0] - tx, v[1] - ty])
                for eid in r:
                    if eid in vars_.rad:
                        ri = vars_.rad[eid]
                        add(c.id, [ri], lambda v, t0=x0[ri]: v[0] - t0)
            elif t in ("coordinate_x", "coordinate_y"):
                j = 0 if t == "coordinate_x" else 1
                add(c.id, [vars_.P(r[0])[j]], lambda v, val=val: v[0] - val)
            elif t == "distance":
                if val is None or val < 0:
                    raise SketchError("distance must be >= 0")
                if k == ["point", "point"]:
                    add(c.id, vars_.P(r[0]) + vars_.P(r[1]), lambda v, val=val: math.hypot(v[2] - v[0], v[3] - v[1]) - val)
                elif sorted(k) == ["line", "point"]:
                    pi, li = (r[0], r[1]) if k[0] == "point" else (r[1], r[0])
                    idx = vars_.L(li) + vars_.P(pi)
                    s = _sgn(_line_point_signed(x0[idx]))
                    add(c.id, idx, lambda v, s=s, val=val: s * _line_point_signed(v) - val)
                elif k == ["line", "line"]:
                    l2 = vars_.ent[r[1]]
                    idx = vars_.L(r[0]) + vars_.P(l2.p1)
                    s = _sgn(_line_point_signed(x0[idx]))
                    add(c.id, idx, lambda v, s=s, val=val: s * _line_point_signed(v) - val)
                else:
                    raise SketchError("distance needs (point, point), (point, line) or (line, line)")
            elif t in ("horizontal_distance", "vertical_distance"):
                j = 0 if t == "horizontal_distance" else 1
                idx = vars_.P(r[0]) + vars_.P(r[1]) if len(r) == 2 else vars_.L(r[0])
                s = _sgn(x0[idx[2 + j]] - x0[idx[j]])
                add(c.id, idx, lambda v, j=j, s=s, val=val: s * (v[2 + j] - v[j]) - val)
            elif t == "length":
                if val is None or val <= 0:
                    raise SketchError("length must be > 0")
                add(c.id, vars_.L(r[0]), lambda v, val=val: math.hypot(v[2] - v[0], v[3] - v[1]) - val)
            elif t in ("radius", "diameter"):
                if val is None or val <= 0:
                    raise SketchError(f"{t} must be > 0")
                target = val if t == "radius" else val / 2
                if k[0] == "ellipse":
                    raise SketchError("use rx/ry dimensions for ellipses (not supported as constraints yet)")
                idx, lay = vars_.C(r[0])
                add(c.id, idx, lambda v, lay=lay, target=target: _radius(v, lay) - target)
            elif t == "angle":
                th = math.radians(val)
                idx = vars_.L(r[0]) + vars_.L(r[1])

                def f(v, th=th):
                    d1x, d1y, d2x, d2y = v[2] - v[0], v[3] - v[1], v[6] - v[4], v[7] - v[5]
                    n = math.hypot(d1x, d1y) * math.hypot(d2x, d2y) + EPS
                    cr, dt = _cross(d1x, d1y, d2x, d2y), d1x * d2x + d1y * d2y
                    return (cr * math.cos(th) - dt * math.sin(th)) / n

                add(c.id, idx, f)
            else:  # pragma: no cover - schema restricts types
                raise SketchError(f"Unsupported constraint type {t}")
        except (SketchError, expressions.ExpressionError, IndexError) as exc:
            errors[c.id] = str(exc) if not isinstance(exc, IndexError) else "wrong number of references"
    return eqs, errors


def _implicit(vars_: _Vars) -> list[Equation]:
    """Arcs keep start and end on the same circle."""
    out = []
    for e in vars_.ent.values():
        if isinstance(e, S.SkArc):
            idx = vars_.P(e.center) + vars_.P(e.start) + vars_.P(e.end)
            out.append(Equation(f"__arc:{e.id}", np.array(idx),
                                lambda v: np.atleast_1d(math.hypot(v[2] - v[0], v[3] - v[1]) - math.hypot(v[4] - v[0], v[5] - v[1]))))
    return out


def _residual(eqs: list[Equation], x: np.ndarray) -> np.ndarray:
    if not eqs:
        return np.zeros(0)
    return np.concatenate([e.fn(x[e.idx]) for e in eqs])


def _jacobian(eqs: list[Equation], x: np.ndarray, n: int) -> np.ndarray:
    rows = []
    for e in eqs:
        loc = x[e.idx].astype(float)
        f0 = e.fn(loc)
        block = np.zeros((len(f0), n))
        for j, gi in enumerate(e.idx):
            h = 1e-7 * max(1.0, abs(loc[j]))
            lp, lm = loc.copy(), loc.copy()
            lp[j] += h
            lm[j] -= h
            block[:, gi] += (e.fn(lp) - e.fn(lm)) / (2 * h)
        rows.append(block)
    return np.vstack(rows) if rows else np.zeros((0, n))


def _newton(eqs: list[Equation], x: np.ndarray) -> tuple[np.ndarray, bool, int]:
    n = len(x)
    lam = 1e-3
    for it in range(1, MAX_ITER + 1):
        F = _residual(eqs, x)
        norm = float(np.max(np.abs(F))) if len(F) else 0.0
        if norm < TOL:
            return x, True, it - 1
        J = _jacobian(eqs, x, n)
        dx = np.linalg.lstsq(J, -F, rcond=1e-12)[0]
        f2 = float(F @ F)
        step, accepted = 1.0, False
        while step > 1e-3:
            xn = x + step * dx
            Fn = _residual(eqs, xn)
            if float(Fn @ Fn) < f2:
                x, accepted = xn, True
                break
            step *= 0.5
        if not accepted:
            for _ in range(12):
                A = J.T @ J + lam * np.eye(n)
                dx = np.linalg.solve(A, -J.T @ F)
                xn = x + dx
                Fn = _residual(eqs, xn)
                if float(Fn @ Fn) < f2:
                    x, accepted = xn, True
                    lam = max(lam / 10, 1e-9)
                    break
                lam *= 10
        if not accepted:
            return x, False, it
    F = _residual(eqs, x)
    return x, (float(np.max(np.abs(F))) if len(F) else 0.0) < TOL, MAX_ITER


def _rank(J: np.ndarray) -> tuple[int, np.ndarray]:
    if J.size == 0:
        return 0, np.eye(J.shape[1])
    _, s, vt = np.linalg.svd(J)
    tol = max(J.shape) * (s[0] if len(s) else 0) * 1e-9 + 1e-10
    r = int(np.sum(s > tol))
    return r, vt[r:].T  # null space basis (n x dof)


def _redundant(eqs: list[Equation], J: np.ndarray) -> list[str]:
    basis: list[np.ndarray] = []
    contributes: dict[str, bool] = {}
    row = 0
    for e in eqs:
        k = len(e.fn(np.zeros(len(e.idx)) + 1.0))  # number of rows (shape only)
        contributes.setdefault(e.cid, False)
        for rr in J[row:row + k]:
            v = rr.copy()
            nv = np.linalg.norm(v)
            if nv < 1e-12:
                continue
            for b in basis:
                v -= (b @ v) * b
            if np.linalg.norm(v) > 1e-7 * nv:
                basis.append(v / np.linalg.norm(v))
                contributes[e.cid] = True
        row += k
    # A constraint is redundant only if none of its equations add rank (multi-equation constraints
    # such as 'fix' on several points are judged as a whole).
    return [c for c, ok in contributes.items() if not ok and not c.startswith("__")]


def solve(
    params: S.SketchParams,
    env: dict[str, float] | None = None,
    drag: dict[str, tuple[float, float]] | None = None,
) -> SolveResult:
    t0 = time.perf_counter()
    vars_ = _Vars(params.entities)
    n = len(vars_.values)
    env = env or {}
    eqs, errors = compile_constraints(vars_, params.constraints, env)
    eqs = _implicit(vars_) + eqs
    x0 = np.array(vars_.values, dtype=float)

    if drag:
        # Move dragged points first, then solve with them pinned; if pinning makes
        # the system unsolvable, fall back to an unpinned solve from the drag pose.
        xd = x0.copy()
        pins = []
        for pid, (px, py) in drag.items():
            ix, iy = vars_.P(pid)
            xd[ix], xd[iy] = px, py
            pins.append(Equation(f"__drag:{pid}", np.array([ix, iy]),
                                 lambda v, px=px, py=py: np.array([v[0] - px, v[1] - py])))
        x, ok, it = _newton(eqs + pins, xd)
        if not ok:
            x, ok, it = _newton(eqs, xd)
    else:
        x, ok, it = _newton(eqs, x0)

    F = _residual(eqs, x)
    res = float(np.max(np.abs(F))) if len(F) else 0.0
    J = _jacobian(eqs, x, n)
    rank, null = _rank(J)
    dof = n - rank
    free_vars = [i for i in range(n) if null.shape[1] and np.linalg.norm(null[i]) > 1e-6]
    free_entities = sorted({vars_.owner[i] for i in free_vars})
    # Curves are free if any of their defining points/values are free.
    for e in params.entities:
        if e.id not in free_entities and any(p in free_entities for p in _point_refs(e)):
            free_entities.append(e.id)

    redundant = _redundant(eqs, J) if eqs else []
    unsatisfied: list[str] = []
    conflicting: list[str] = []
    if not ok:
        row = 0
        for e in eqs:
            k = len(e.fn(x[e.idx]))
            if np.max(np.abs(F[row:row + k])) > 1e-6 and not e.cid.startswith("__"):
                unsatisfied.append(e.cid)
            row += k
        conflicting = _isolate_conflict(eqs, x0) if len(params.constraints) <= 300 else unsatisfied[:1]

    if not params.entities:
        status = "empty"
    elif not ok:
        status = "conflicting"
    elif redundant:
        status = "over_constrained"
    elif dof == 0:
        status = "fully_constrained"
    else:
        status = "under_constrained"

    pts = {pid: (float(x[i]), float(x[j])) for pid, (i, j) in vars_.pt.items()}
    rad = {cid: float(x[i]) for cid, i in vars_.rad.items()}
    ell = {eid: (float(x[i]), float(x[j])) for eid, (i, j) in vars_.ell.items()}
    if not ok:
        # Never hand back a half-solved sketch as if it were valid geometry.
        pts = {pid: (float(x0[i]), float(x0[j])) for pid, (i, j) in vars_.pt.items()}
        rad = {cid: float(x0[i]) for cid, i in vars_.rad.items()}
        ell = {eid: (float(x0[i]), float(x0[j])) for eid, (i, j) in vars_.ell.items()}
    bad_radius = [c for c, r in rad.items() if r <= 0] + [e for e, (a, b) in ell.items() if a <= 0 or b <= 0]
    for b in bad_radius:
        errors[b] = "Solved radius is not positive"
        status = "conflicting"
    return SolveResult(
        status=status,
        dof=dof,
        converged=ok,
        residual=res,
        iterations=it,
        points=pts,
        radii=rad,
        ellipses=ell,
        free_entities=sorted(free_entities),
        redundant=redundant,
        conflicting=conflicting,
        unsatisfied=unsatisfied,
        constraint_errors=errors,
        elapsed_ms=(time.perf_counter() - t0) * 1000,
    )


def _isolate_conflict(eqs: list[Equation], x0: np.ndarray) -> list[str]:
    """Add constraints in order; return the first one that breaks solvability."""
    groups: dict[str, list[Equation]] = {}
    for e in eqs:
        groups.setdefault(e.cid, []).append(e)
    active: list[Equation] = []
    x = x0.copy()
    for cid, grp in groups.items():
        trial = active + grp
        xn, ok, _ = _newton(trial, x)
        if not ok:
            return [cid] if not cid.startswith("__") else []
        active, x = trial, xn
    return []


def apply_solution(params: S.SketchParams, result: SolveResult) -> S.SketchParams:
    """Return a copy of the sketch with solved positions written back."""
    ents = []
    for e in params.entities:
        if isinstance(e, S.SkPoint) and e.id in result.points:
            x, y = result.points[e.id]
            ents.append(e.model_copy(update={"x": x, "y": y}))
        elif isinstance(e, S.SkCircle) and e.id in result.radii:
            ents.append(e.model_copy(update={"r": result.radii[e.id]}))
        elif isinstance(e, S.SkEllipse) and e.id in result.ellipses:
            rx, ry = result.ellipses[e.id]
            ents.append(e.model_copy(update={"rx": rx, "ry": ry}))
        else:
            ents.append(e)
    return params.model_copy(update={"entities": ents})
