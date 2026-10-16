"""Rigid-body mate solver and kinematic sweep.

Unknowns: for every non-fixed, non-suppressed instance a translation (3) and
a rotation increment (3, rotation vector applied on the left of the current
orientation). Each mate contributes residual equations on world-space
primitives (points, unit directions) obtained from the instance's part-frame
geometry. Gauss-Newton with minimum-norm steps keeps unconstrained components
where the user placed them. DOF = 6 * movable instances - rank(J).

The same machinery drives kinematic studies: a revolute angle or slider
travel is added as a driving equation and swept; positions are differentiated
numerically for velocity/acceleration.
"""

from __future__ import annotations

import math
from dataclasses import dataclass, field
from typing import Any, Callable

import numpy as np

from .schema import AssemblyDocument, Mate, MateRef

TOL = 1e-7


def euler_to_matrix(rx: float, ry: float, rz: float) -> np.ndarray:
    a, b, c = (math.radians(v) for v in (rx, ry, rz))
    Rx = np.array([[1, 0, 0], [0, math.cos(a), -math.sin(a)], [0, math.sin(a), math.cos(a)]])
    Ry = np.array([[math.cos(b), 0, math.sin(b)], [0, 1, 0], [-math.sin(b), 0, math.cos(b)]])
    Rz = np.array([[math.cos(c), -math.sin(c), 0], [math.sin(c), math.cos(c), 0], [0, 0, 1]])
    return Rz @ Ry @ Rx


def matrix_to_euler(R: np.ndarray) -> tuple[float, float, float]:
    sy = -R[2, 0]
    sy = max(-1.0, min(1.0, sy))
    ry = math.asin(sy)
    if abs(math.cos(ry)) > 1e-9:
        rx = math.atan2(R[2, 1], R[2, 2])
        rz = math.atan2(R[1, 0], R[0, 0])
    else:  # gimbal lock
        rx = math.atan2(-R[1, 2], R[1, 1])
        rz = 0.0
    return tuple(round(math.degrees(v), 10) for v in (rx, ry, rz))  # type: ignore[return-value]


def rotvec(w: np.ndarray) -> np.ndarray:
    th = float(np.linalg.norm(w))
    if th < 1e-15:
        return np.eye(3)
    k = w / th
    K = np.array([[0, -k[2], k[1]], [k[2], 0, -k[0]], [-k[1], k[0], 0]])
    return np.eye(3) + math.sin(th) * K + (1 - math.cos(th)) * K @ K


@dataclass
class Prim:
    """Local (part frame) primitive."""

    point: np.ndarray
    direction: np.ndarray | None  # plane normal or axis direction
    kind: str  # plane | axis | point
    ref_dir: np.ndarray | None = None  # a direction perpendicular to `direction` (for rotation locks)


def _perp(d: np.ndarray) -> np.ndarray:
    h = np.array([1.0, 0, 0]) if abs(d[0]) < 0.9 else np.array([0, 1.0, 0])
    v = h - np.dot(h, d) * d
    return v / np.linalg.norm(v)


class GeometryError(ValueError):
    pass


def resolve_prim(ref: MateRef, part: dict[str, Any]) -> Prim:
    """Turn a MateRef into a part-frame primitive using tessellation metadata."""
    if ref.kind == "origin":
        if ref.axis:
            d = np.eye(3)[{"X": 0, "Y": 1, "Z": 2}[ref.axis]]
            return Prim(np.zeros(3), d, "point" if ref.point else "axis", _perp(d))
        n = {"XY": np.array([0, 0, 1.0]), "XZ": np.array([0, -1.0, 0]), "YZ": np.array([1.0, 0, 0])}[ref.plane or "XY"]
        return Prim(np.zeros(3), n, "point" if ref.point else "plane", _perp(n))
    bodies = part.get("bodies", [])
    cand = [b for b in bodies if not ref.body or b["name"] == ref.body]
    if ref.kind == "face":
        for b in cand:
            for f in b.get("faces", []):
                if ref.face in f.get("names", [f.get("name")]):
                    g = f.get("geometry") or {}
                    if g.get("surface") == "plane":
                        n = np.array(g["normal"], float)
                        return Prim(np.array(g["centroid"], float), n, "point" if ref.point else "plane",
                                    np.array(g.get("x_dir") or _perp(n), float))
                    if "axis_dir" in g:
                        d = np.array(g["axis_dir"], float)
                        return Prim(np.array(g["axis_origin"], float), d, "axis", _perp(d))
                    raise GeometryError(f"Face {ref.face} ({g.get('surface')}) cannot be used in a mate")
        raise GeometryError(f"Face {ref.face!r} not found on instance {ref.instance}")
    if ref.kind == "edge" and ref.edge is not None:
        want = sorted(ref.edge.faces or [])
        best = None
        for b in cand:
            for e in b.get("edges", []):
                if sorted(e["ref"]["faces"]) == want:
                    g = e.get("geometry") or {}
                    if best is None or (ref.edge.hint is not None and np.linalg.norm(
                            np.subtract(g.get("midpoint", [0, 0, 0]), ref.edge.hint)) < np.linalg.norm(
                            np.subtract(best[1].get("midpoint", [0, 0, 0]), ref.edge.hint))):
                        best = (e, g)
        if best is None:
            raise GeometryError(f"Edge between {want} not found on instance {ref.instance}")
        e, g = best
        if e["curve"] == "circle":
            d = np.array(g["axis_dir"], float)
            return Prim(np.array(g["center"], float), d, "point" if ref.point else "axis", _perp(d))
        if e["curve"] == "line":
            a, b2 = np.array(g["start"], float), np.array(g["end"], float)
            d = (b2 - a) / np.linalg.norm(b2 - a)
            return Prim((a + b2) / 2 if ref.point else a, d, "point" if ref.point else "axis", _perp(d))
        raise GeometryError("Only line and circle edges can be used in mates")
    raise GeometryError("Invalid mate reference")


@dataclass
class SolveResult:
    status: str  # fully_constrained | under_constrained | over_constrained | conflicting | empty
    converged: bool
    dof: int
    residual: float
    iterations: int
    placements: dict[str, np.ndarray]  # 4x4
    instance_dof: dict[str, int] = field(default_factory=dict)
    redundant: list[str] = field(default_factory=list)
    conflicting: list[str] = field(default_factory=list)
    errors: dict[str, str] = field(default_factory=dict)
    joint_values: dict[str, float] = field(default_factory=dict)
    limit_violations: list[dict[str, Any]] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return {"status": self.status, "converged": self.converged, "dof": self.dof, "residual": self.residual,
                "iterations": self.iterations, "instance_dof": self.instance_dof, "redundant": self.redundant,
                "conflicting": self.conflicting, "errors": self.errors, "joint_values": self.joint_values,
                "limit_violations": self.limit_violations,
                "placements": {k: v.tolist() for k, v in self.placements.items()}}


class _System:
    def __init__(self, doc: AssemblyDocument, parts: dict[str, dict[str, Any]], env: dict[str, float]):
        from ..kernel import expressions

        self.doc = doc
        self.active = [i for i in doc.instances if not i.suppressed]
        self.R0: dict[str, np.ndarray] = {}
        self.t0: dict[str, np.ndarray] = {}
        for i in self.active:
            self.R0[i.id] = euler_to_matrix(*i.placement.rotation)
            self.t0[i.id] = np.array(i.placement.position, float)
        fixed = {i.id for i in self.active if i.fixed}
        fixed |= {m.a.instance for m in doc.mates if m.type == "fixed" and not m.suppressed}
        if not fixed and self.active:
            fixed = {self.active[0].id}  # ground the first component so the system is not free-floating
        self.fixed = fixed
        self.movable = [i.id for i in self.active if i.id not in fixed]
        self.index = {iid: k for k, iid in enumerate(self.movable)}
        self.errors: dict[str, str] = {}
        self.eqs: list[tuple[str, Callable[[dict], np.ndarray]]] = []
        self.joints: dict[str, Callable[[dict], float]] = {}
        active_ids = {i.id for i in self.active}
        src = {i.id: parts.get(i.id) for i in self.active}
        for m in doc.mates:
            if m.suppressed or m.type == "fixed":
                continue
            if m.a.instance not in active_ids or (m.b and m.b.instance not in active_ids):
                continue
            try:
                val = expressions.evaluate(m.value, env) if m.value is not None else None
                pa = resolve_prim(m.a, src[m.a.instance] or {})
                pb = resolve_prim(m.b, src[m.b.instance] or {})  # type: ignore[arg-type]
                self._compile(m, pa, pb, val)
            except (GeometryError, expressions.ExpressionError) as exc:
                self.errors[m.id] = str(exc)

    # world-space helpers -------------------------------------------------
    def pose(self, x: np.ndarray, iid: str) -> tuple[np.ndarray, np.ndarray]:
        if iid not in self.index:
            return self.R0[iid], self.t0[iid]
        k = self.index[iid] * 6
        return rotvec(x[k + 3:k + 6]) @ self.R0[iid], self.t0[iid] + x[k:k + 3]

    def _compile(self, m: Mate, pa: Prim, pb: Prim, val: float | None) -> None:
        A, B = m.a.instance, m.b.instance  # type: ignore[union-attr]

        def W(x, iid, p: Prim):
            R, t = self.pose(x, iid)
            d = R @ p.direction if p.direction is not None else None
            r = R @ p.ref_dir if p.ref_dir is not None else None
            return R @ p.point + t, d, r

        sgn = 1.0 if m.flip else -1.0
        t = m.type
        kinds = (pa.kind, pb.kind)

        def eq(fn):
            self.eqs.append((m.id, fn))

        if t == "coincident":
            if kinds == ("plane", "plane"):
                eq(lambda x: np.cross(W(x, A, pa)[1], W(x, B, pb)[1]))
                eq(lambda x: np.atleast_1d(np.dot(W(x, A, pa)[1], W(x, B, pb)[1]) - sgn) * 0.5)
                eq(lambda x: np.atleast_1d(np.dot(W(x, B, pb)[0] - W(x, A, pa)[0], W(x, A, pa)[1])))
            elif "plane" in kinds:
                pl, pt, P, Q = (pa, pb, A, B) if pa.kind == "plane" else (pb, pa, B, A)
                eq(lambda x: np.atleast_1d(np.dot(W(x, Q, pt)[0] - W(x, P, pl)[0], W(x, P, pl)[1])))
            elif kinds == ("axis", "axis"):
                self._concentric(m.id, A, B, pa, pb, W)
            else:
                eq(lambda x: W(x, A, pa)[0] - W(x, B, pb)[0])
        elif t == "concentric":
            if pa.kind == "plane" or pb.kind == "plane":
                raise GeometryError("Concentric requires cylindrical faces, circular edges or axes")
            self._concentric(m.id, A, B, pa, pb, W)
        elif t == "parallel":
            eq(lambda x: np.cross(W(x, A, pa)[1], W(x, B, pb)[1]))
        elif t == "perpendicular":
            eq(lambda x: np.atleast_1d(np.dot(W(x, A, pa)[1], W(x, B, pb)[1])))
        elif t == "distance":
            if kinds == ("plane", "plane"):
                eq(lambda x: np.cross(W(x, A, pa)[1], W(x, B, pb)[1]))
                eq(lambda x: np.atleast_1d(np.dot(W(x, A, pa)[1], W(x, B, pb)[1]) - sgn) * 0.5)
                eq(lambda x: np.atleast_1d(np.dot(W(x, B, pb)[0] - W(x, A, pa)[0], W(x, A, pa)[1]) - val))
            elif "plane" in kinds:
                pl, pt, P, Q = (pa, pb, A, B) if pa.kind == "plane" else (pb, pa, B, A)
                eq(lambda x: np.atleast_1d(np.dot(W(x, Q, pt)[0] - W(x, P, pl)[0], W(x, P, pl)[1]) - val))
            else:
                eq(lambda x: np.atleast_1d(np.linalg.norm(W(x, A, pa)[0] - W(x, B, pb)[0]) - val))
        elif t == "angle":
            th = math.radians(val or 0)
            c = math.cos(th)
            if abs(math.sin(th)) < 1e-9:
                # dot - cos(theta) has zero gradient at 0/180 deg; use the parallel form plus orientation.
                eq(lambda x: np.cross(W(x, A, pa)[1], W(x, B, pb)[1]))
                eq(lambda x, c=c: np.atleast_1d(np.dot(W(x, A, pa)[1], W(x, B, pb)[1]) - c) * 0.5)
            else:
                eq(lambda x: np.atleast_1d(np.dot(W(x, A, pa)[1], W(x, B, pb)[1]) - c))
        elif t == "revolute":
            self._concentric(m.id, A, B, pa, pb, W)
            off = val or 0.0
            eq(lambda x: np.atleast_1d(np.dot(W(x, B, pb)[0] - W(x, A, pa)[0], W(x, A, pa)[1]) - off))

            def angle(x):
                _, da, ra = W(x, A, pa)
                _, _, rb = W(x, B, pb)
                rb = rb - np.dot(rb, da) * da
                return math.degrees(math.atan2(np.dot(np.cross(ra, rb), da), np.dot(ra, rb)))

            self.joints[m.id] = angle
        elif t == "slider":
            self._concentric(m.id, A, B, pa, pb, W)

            def lock(x):
                _, da, ra = W(x, A, pa)
                _, _, rb = W(x, B, pb)
                return np.atleast_1d(np.dot(rb, np.cross(da, ra)))

            eq(lock)
            self.joints[m.id] = lambda x: float(np.dot(W(x, B, pb)[0] - W(x, A, pa)[0], W(x, A, pa)[1]))

    def _concentric(self, mid, A, B, pa, pb, W) -> None:
        self.eqs.append((mid, lambda x: np.cross(W(x, A, pa)[1], W(x, B, pb)[1])))

        def online(x):
            oa, da, _ = W(x, A, pa)
            ob, _, _ = W(x, B, pb)
            return np.cross(ob - oa, da)

        self.eqs.append((mid, online))

    # numerics -------------------------------------------------------------
    def F(self, x: np.ndarray, eqs=None) -> np.ndarray:
        eqs = self.eqs if eqs is None else eqs
        return np.concatenate([np.atleast_1d(f(x)) for _, f in eqs]) if eqs else np.zeros(0)

    def J(self, x: np.ndarray, eqs=None) -> np.ndarray:
        n = len(x)
        f0 = self.F(x, eqs)
        J = np.zeros((len(f0), n))
        for j in range(n):
            h = 1e-7
            xp, xm = x.copy(), x.copy()
            xp[j] += h
            xm[j] -= h
            J[:, j] = (self.F(xp, eqs) - self.F(xm, eqs)) / (2 * h)
        return J

    def newton(self, x: np.ndarray, eqs=None, max_iter: int = 60) -> tuple[np.ndarray, bool, int]:
        for it in range(1, max_iter + 1):
            f = self.F(x, eqs)
            if not len(f) or np.max(np.abs(f)) < TOL:
                return x, True, it - 1
            J = self.J(x, eqs)
            dx = np.linalg.lstsq(J, -f, rcond=1e-12)[0]
            f2, step, ok = float(f @ f), 1.0, False
            while step > 1e-4:
                xn = x + step * dx
                fn = self.F(xn, eqs)
                if float(fn @ fn) < f2:
                    x, ok = xn, True
                    break
                step /= 2
            if not ok:
                return x, False, it
            # Re-linearise rotations: fold increments into R0/t0 and reset.
            self._fold(x)
            x = np.zeros_like(x)
        f = self.F(x, eqs)
        return x, (not len(f) or float(np.max(np.abs(f))) < TOL), max_iter

    def _fold(self, x: np.ndarray) -> None:
        for iid, k in self.index.items():
            R, t = self.pose(x, iid)
            self.R0[iid], self.t0[iid] = R, t

    def snapshot(self) -> tuple[dict, dict]:
        return {k: v.copy() for k, v in self.R0.items()}, {k: v.copy() for k, v in self.t0.items()}

    def restore(self, snap) -> None:
        self.R0 = {k: v.copy() for k, v in snap[0].items()}
        self.t0 = {k: v.copy() for k, v in snap[1].items()}


def _placements(sys_: _System) -> dict[str, np.ndarray]:
    out = {}
    for iid in sys_.R0:
        T = np.eye(4)
        T[:3, :3] = sys_.R0[iid]
        T[:3, 3] = sys_.t0[iid]
        out[iid] = T
    return out


def solve(doc: AssemblyDocument, parts: dict[str, dict[str, Any]], env: dict[str, float] | None = None,
          drive: dict[str, float] | None = None) -> SolveResult:
    sys_ = _System(doc, parts, env or {})
    n = 6 * len(sys_.movable)
    eqs = list(sys_.eqs)
    for mid, target in (drive or {}).items():
        jf = sys_.joints.get(mid)
        if jf is None:
            sys_.errors[mid] = "Only revolute and slider mates can be driven"
            continue
        mate = next(m for m in doc.mates if m.id == mid)
        if mate.type == "revolute":
            eqs.append((f"__drive:{mid}", lambda x, jf=jf, t=target: np.atleast_1d(
                math.sin(math.radians(jf(x) - t)))))
        else:
            eqs.append((f"__drive:{mid}", lambda x, jf=jf, t=target: np.atleast_1d(jf(x) - t)))
    start = sys_.snapshot()
    x = np.zeros(n)
    x, ok, it = sys_.newton(x, eqs)
    f = sys_.F(x, eqs)
    res = float(np.max(np.abs(f))) if len(f) else 0.0
    J = sys_.J(np.zeros(n), eqs) if n else np.zeros((0, 0))
    rank = int(np.linalg.matrix_rank(J, tol=1e-6)) if J.size else 0
    dof = n - rank
    inst_dof: dict[str, int] = {}
    if n and J.size:
        _, s, vt = np.linalg.svd(J)
        null = vt[rank:].T if rank < n else np.zeros((n, 0))
        for iid, k in sys_.index.items():
            block = null[k * 6:(k + 1) * 6]
            inst_dof[iid] = int(np.linalg.matrix_rank(block, tol=1e-6)) if block.size else 0
    elif n:
        inst_dof = {iid: 6 for iid in sys_.index}
    for iid in sys_.fixed:
        inst_dof[iid] = 0
    redundant = _redundant(sys_, eqs, n)
    conflicting: list[str] = []
    if not ok:
        sys_.restore(start)
        conflicting = _isolate(sys_, eqs, n)
        sys_.restore(start)
    if not doc.instances:
        status = "empty"
    elif not ok:
        status = "conflicting"
    elif redundant:
        status = "over_constrained"
    elif dof == 0:
        status = "fully_constrained"
    else:
        status = "under_constrained"
    joint_values = {mid: float(fn(np.zeros(n))) for mid, fn in sys_.joints.items()} if ok else {}
    violations = []
    for m in doc.mates:
        if m.limits and m.id in joint_values:
            lo, hi = m.limits
            v = joint_values[m.id]
            if v < lo - 1e-9 or v > hi + 1e-9:
                violations.append({"mate": m.id, "value": v, "limits": [lo, hi]})
    return SolveResult(status, ok, dof, res, it, _placements(sys_), inst_dof, redundant, conflicting, sys_.errors,
                       joint_values, violations)


def _redundant(sys_: _System, eqs, n: int) -> list[str]:
    if not n:
        return []
    J = sys_.J(np.zeros(n), eqs)
    basis: list[np.ndarray] = []
    contributes: dict[str, bool] = {}
    row = 0
    for mid, f in eqs:
        k = len(np.atleast_1d(f(np.zeros(n))))
        contributes.setdefault(mid, False)
        for r in J[row:row + k]:
            v = r.copy()
            nv = np.linalg.norm(v)
            if nv < 1e-10:
                continue
            for b in basis:
                v -= (b @ v) * b
            if np.linalg.norm(v) > 1e-6 * nv:
                basis.append(v / np.linalg.norm(v))
                contributes[mid] = True
        row += k
    # A mate is redundant when none of its equations add rank. Partially dependent rows (e.g. the
    # 3-component cross product used for parallelism) are expected and not reported.
    return [m for m, c in contributes.items() if not c and not m.startswith("__")]


def _isolate(sys_: _System, eqs, n: int) -> list[str]:
    order: list[str] = []
    for mid, _ in eqs:
        if mid not in order:
            order.append(mid)
    active: list = []
    for mid in order:
        trial = active + [e for e in eqs if e[0] == mid]
        snap = sys_.snapshot()
        _, ok, _ = sys_.newton(np.zeros(n), trial)
        if not ok:
            sys_.restore(snap)
            return [mid] if not mid.startswith("__") else []
        active = trial
    return []


def kinematic_sweep(doc: AssemblyDocument, parts: dict[str, dict[str, Any]], mate_id: str, start: float, end: float,
                    steps: int, duration_s: float, env: dict[str, float] | None = None) -> dict[str, Any]:
    mate = next((m for m in doc.mates if m.id == mate_id), None)
    if mate is None or mate.type not in ("revolute", "slider"):
        raise GeometryError("Kinematic studies drive a revolute or slider mate")
    values = np.linspace(start, end, steps)
    dt = duration_s / (steps - 1)
    frames = []
    cur = doc
    for v in values:
        r = solve(cur, parts, env, drive={mate_id: float(v)})
        if not r.converged:
            frames.append({"value": float(v), "ok": False, "status": r.status})
            continue
        frames.append({"value": float(v), "ok": True, "placements": {k: T.tolist() for k, T in r.placements.items()},
                       "limit_violations": r.limit_violations})
        # Warm-start the next step from this solution.
        cur = _with_placements(cur, r.placements)
    # Finite-difference kinematics of instance origins.
    traj: dict[str, list] = {}
    for f in frames:
        if not f["ok"]:
            continue
        for iid, T in f["placements"].items():
            traj.setdefault(iid, []).append(np.array(T)[:3, 3])
    kin = {}
    for iid, pts in traj.items():
        P = np.array(pts)
        if len(P) == len(values) and len(P) >= 3:
            V = np.gradient(P, dt, axis=0)
            A = np.gradient(V, dt, axis=0)
            kin[iid] = {"position_mm": P.tolist(), "velocity_mm_s": V.tolist(), "acceleration_mm_s2": A.tolist(),
                        "speed_max_mm_s": float(np.max(np.linalg.norm(V, axis=1)))}
    lim_viol = [{"value": f["value"], **lv} for f in frames if f.get("ok") for lv in f.get("limit_violations", [])]
    return {"mate": mate_id, "type": mate.type, "unit": "deg" if mate.type == "revolute" else "mm",
            "values": values.tolist(), "dt_s": dt, "frames": frames, "kinematics": kin,
            "failed_steps": [f["value"] for f in frames if not f["ok"]], "limit_violations": lim_viol,
            "method": "Quasi-static: positions solved per step; velocity/acceleration by central finite differences "
                      "of positions at constant drive rate. No dynamics (masses, forces) are simulated."}


def _with_placements(doc: AssemblyDocument, P: dict[str, np.ndarray]) -> AssemblyDocument:
    insts = []
    for i in doc.instances:
        if i.id in P:
            T = P[i.id]
            insts.append(i.model_copy(update={"placement": i.placement.model_copy(update={
                "position": tuple(float(v) for v in T[:3, 3]), "rotation": matrix_to_euler(T[:3, :3])})}))
        else:
            insts.append(i)
    return doc.model_copy(update={"instances": insts})


def apply_placements(doc: AssemblyDocument, P: dict[str, np.ndarray]) -> AssemblyDocument:
    return _with_placements(doc, P)
