"""Closed-profile (region) extraction and validation for solved sketches.

Regions are found the way a planar arrangement is traversed: every open curve is
a pair of half-edges; at each vertex outgoing half-edges are sorted by departure
angle and each face is traced by turning to the next half-edge clockwise from
the twin. Bounded faces have positive signed area. Separate connected
components (e.g. a circle inside a rectangle) are related by containment, which
produces holes and nesting depth.

Region ids are derived from the entity ids on their outer boundary, so they are
stable when dimensions change.

Limitations (documented): curves must meet only at endpoints; crossing curves
are reported as self-intersections rather than split automatically (trim first).
"""

from __future__ import annotations

import math
from dataclasses import dataclass, field
from typing import Any

import numpy as np

from ..kernel import schema as S
from .solver import SolveResult

ARC_SEG_DEG = 4.0


@dataclass
class Curve:
    id: str
    kind: str  # line | arc | circle | ellipse | spline
    closed: bool
    poly: np.ndarray  # (k, 2) polyline in natural direction
    start: str | None = None  # point ids (open curves)
    end: str | None = None
    data: dict[str, Any] = field(default_factory=dict)


@dataclass
class Loop:
    edges: list[tuple[str, bool]]  # (entity id, reversed)
    poly: np.ndarray

    @property
    def area(self) -> float:
        return _signed_area(self.poly)


@dataclass
class Region:
    id: str
    outer: Loop
    holes: list[Loop]
    depth: int

    @property
    def area(self) -> float:
        return abs(self.outer.area) - sum(abs(h.area) for h in self.holes)

    def to_dict(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "depth": self.depth,
            "area": self.area,
            "outer": [[e, r] for e, r in self.outer.edges],
            "holes": [[[e, r] for e, r in h.edges] for h in self.holes],
            "polygon": np.round(self.outer.poly, 6).tolist(),
            "hole_polygons": [np.round(h.poly, 6).tolist() for h in self.holes],
        }


@dataclass
class ProfileResult:
    curves: dict[str, Curve]
    regions: list[Region]
    errors: list[dict[str, Any]]
    warnings: list[dict[str, Any]]
    open_chains: list[list[tuple[str, bool]]]
    vertex_of: dict[str, str] = field(default_factory=dict)  # point id -> merged vertex id

    def default_regions(self) -> list[Region]:
        return [r for r in self.regions if r.depth % 2 == 0]

    def to_dict(self) -> dict[str, Any]:
        return {
            "regions": [r.to_dict() for r in self.regions],
            "default_regions": [r.id for r in self.default_regions()],
            "errors": self.errors,
            "warnings": self.warnings,
            "open_chains": [[[e, r] for e, r in c] for c in self.open_chains],
        }


def _signed_area(p: np.ndarray) -> float:
    if len(p) < 3:
        return 0.0
    x, y = p[:, 0], p[:, 1]
    return 0.5 * float(np.dot(x, np.roll(y, -1)) - np.dot(y, np.roll(x, -1)))


def _catmull_rom(pts: np.ndarray, closed: bool, per_seg: int = 16) -> np.ndarray:
    if len(pts) == 2 and not closed:
        return pts.copy()
    P = np.vstack([pts[-1], pts, pts[0], pts[1]]) if closed else np.vstack([2 * pts[0] - pts[1], pts, 2 * pts[-1] - pts[-2]])
    out = []
    nseg = len(pts) if closed else len(pts) - 1
    for i in range(nseg):
        p0, p1, p2, p3 = P[i], P[i + 1], P[i + 2], P[i + 3]
        for t in np.linspace(0, 1, per_seg, endpoint=False):
            t2, t3 = t * t, t * t * t
            out.append(0.5 * ((2 * p1) + (-p0 + p2) * t + (2 * p0 - 5 * p1 + 4 * p2 - p3) * t2 + (-p0 + 3 * p1 - 3 * p2 + p3) * t3))
    out.append(pts[0] if closed else pts[-1])
    return np.array(out)


def build_curves(params: S.SketchParams, sol: SolveResult) -> dict[str, Curve]:
    P = {k: np.array(v) for k, v in sol.points.items()}
    out: dict[str, Curve] = {}
    for e in params.entities:
        if e.construction or isinstance(e, S.SkPoint):
            continue
        if isinstance(e, S.SkLine):
            out[e.id] = Curve(e.id, "line", False, np.array([P[e.p1], P[e.p2]]), e.p1, e.p2)
        elif isinstance(e, S.SkCircle):
            c, r = P[e.center], sol.radii[e.id]
            t = np.linspace(0, 2 * math.pi, 73)
            out[e.id] = Curve(e.id, "circle", True, np.c_[c[0] + r * np.cos(t), c[1] + r * np.sin(t)], data={"c": c, "r": r})
        elif isinstance(e, S.SkArc):
            c, s, en = P[e.center], P[e.start], P[e.end]
            r = float(np.linalg.norm(s - c))
            a0 = math.atan2(s[1] - c[1], s[0] - c[0])
            a1 = math.atan2(en[1] - c[1], en[0] - c[0])
            while a1 <= a0 + 1e-12:
                a1 += 2 * math.pi
            n = max(4, int(math.ceil(math.degrees(a1 - a0) / ARC_SEG_DEG)))
            t = np.linspace(a0, a1, n + 1)
            poly = np.c_[c[0] + r * np.cos(t), c[1] + r * np.sin(t)]
            poly[0], poly[-1] = s, en
            out[e.id] = Curve(e.id, "arc", False, poly, e.start, e.end, {"c": c, "r": r, "a0": a0, "a1": a1})
        elif isinstance(e, S.SkEllipse):
            c = P[e.center]
            rx, ry = sol.ellipses[e.id]
            rot = math.radians(e.rotation)
            t = np.linspace(0, 2 * math.pi, 73)
            x, y = rx * np.cos(t), ry * np.sin(t)
            poly = np.c_[c[0] + x * math.cos(rot) - y * math.sin(rot), c[1] + x * math.sin(rot) + y * math.cos(rot)]
            out[e.id] = Curve(e.id, "ellipse", True, poly, data={"c": c, "rx": rx, "ry": ry, "rot": e.rotation})
        elif isinstance(e, S.SkSpline):
            pts = np.array([P[p] for p in e.points])
            poly = _catmull_rom(pts, e.closed)
            if e.closed:
                out[e.id] = Curve(e.id, "spline", True, poly, data={"points": pts})
            else:
                out[e.id] = Curve(e.id, "spline", False, poly, e.points[0], e.points[-1], {"points": pts})
    return out


def _seg_intersections(a: np.ndarray, b: np.ndarray, tol: float) -> np.ndarray:
    """Return intersection points of polylines a and b (vectorised)."""
    p, r = a[:-1], a[1:] - a[:-1]
    q, s = b[:-1], b[1:] - b[:-1]
    P, R = p[:, None, :], r[:, None, :]
    Q, Sv = q[None, :, :], s[None, :, :]
    rxs = R[..., 0] * Sv[..., 1] - R[..., 1] * Sv[..., 0]
    qp = Q - P
    with np.errstate(divide="ignore", invalid="ignore"):
        t = (qp[..., 0] * Sv[..., 1] - qp[..., 1] * Sv[..., 0]) / rxs
        u = (qp[..., 0] * R[..., 1] - qp[..., 1] * R[..., 0]) / rxs
    eps = 1e-9
    hit = (np.abs(rxs) > 1e-14) & (t >= -eps) & (t <= 1 + eps) & (u >= -eps) & (u <= 1 + eps)
    if not hit.any():
        return np.zeros((0, 2))
    tt = np.where(hit, t, 0)
    pts = P + tt[..., None] * R
    return pts[hit]


def _point_in_poly(pt: np.ndarray, poly: np.ndarray) -> bool:
    x, y = pt
    xs, ys = poly[:, 0], poly[:, 1]
    xj, yj = np.roll(xs, 1), np.roll(ys, 1)
    cond = (ys > y) != (yj > y)
    with np.errstate(divide="ignore", invalid="ignore"):
        xint = (xj - xs) * (y - ys) / (yj - ys) + xs
    return bool(np.sum(cond & (x < xint)) % 2 == 1)


class _UF:
    def __init__(self) -> None:
        self.p: dict[str, str] = {}

    def find(self, a: str) -> str:
        self.p.setdefault(a, a)
        while self.p[a] != a:
            self.p[a] = self.p[self.p[a]]
            a = self.p[a]
        return a

    def union(self, a: str, b: str) -> None:
        ra, rb = self.find(a), self.find(b)
        if ra != rb:
            self.p[max(ra, rb)] = min(ra, rb)


def analyze(params: S.SketchParams, sol: SolveResult) -> ProfileResult:
    curves = build_curves(params, sol)
    errors: list[dict[str, Any]] = []
    warnings: list[dict[str, Any]] = []
    if not curves:
        return ProfileResult(curves, [], [], [{"code": "no_geometry", "message": "Sketch has no non-construction geometry"}], [])
    allpts = np.vstack([c.poly for c in curves.values()])
    scale = float(np.max(np.ptp(allpts, axis=0))) or 1.0
    tol = 1e-6 * max(1.0, scale)

    # Merge endpoints that coincide after solving.
    uf = _UF()
    ends = [(cid, pid) for cid, c in curves.items() if not c.closed for pid in (c.start, c.end)]
    pos = {pid: np.array(sol.points[pid]) for _, pid in ends}
    ids = sorted(pos)
    for i, a in enumerate(ids):
        uf.find(a)
        for b in ids[i + 1:]:
            if np.linalg.norm(pos[a] - pos[b]) <= tol:
                uf.union(a, b)
    vtx = lambda pid: uf.find(pid)  # noqa: E731

    # Self-intersection: curves may only touch at shared endpoints.
    cl = list(curves.values())
    bbs = {c.id: (c.poly.min(axis=0) - tol, c.poly.max(axis=0) + tol) for c in cl}
    for i, a in enumerate(cl):
        for b in cl[i:]:
            (amin, amax), (bmin, bmax) = bbs[a.id], bbs[b.id]
            if np.any(amax < bmin) or np.any(bmax < amin):
                continue
            if a.id == b.id:
                if a.kind != "spline" or len(a.poly) < 4:
                    continue
                hits = []
                for k in range(len(a.poly) - 3):
                    h = _seg_intersections(a.poly[k:k + 2], a.poly[k + 2:], tol)
                    hits.extend(h.tolist())
                hits = [h for h in hits if not (a.closed and np.linalg.norm(np.array(h) - a.poly[0]) < 10 * tol)]
                if hits:
                    errors.append({"code": "self_intersection", "entities": [a.id], "at": hits[0],
                                   "message": f"Spline {a.id} intersects itself"})
                continue
            pts = _seg_intersections(a.poly, b.poly, tol)
            if len(pts) == 0:
                continue
            shared = []
            if not a.closed and not b.closed:
                for pa in (a.start, a.end):
                    for pb in (b.start, b.end):
                        if vtx(pa) == vtx(pb):
                            shared.append(pos[pa])
            bad = [p for p in pts if all(np.linalg.norm(p - s) > 1e3 * tol for s in shared)]
            if bad:
                errors.append({"code": "self_intersection", "entities": [a.id, b.id], "at": [float(bad[0][0]), float(bad[0][1])],
                               "message": f"{a.id} and {b.id} cross; split or trim them so curves meet only at endpoints"})
    if errors:
        return ProfileResult(curves, [], errors, warnings, [], {p: vtx(p) for p in ids})

    # Graph of open curves.
    adj: dict[str, list[str]] = {}
    open_curves = {c.id: c for c in cl if not c.closed}
    for c in open_curves.values():
        if vtx(c.start) == vtx(c.end):
            # A single open curve closing on itself (e.g. spline loop) - treat as closed.
            c.closed = True
            continue
        adj.setdefault(vtx(c.start), []).append(c.id)
        adj.setdefault(vtx(c.end), []).append(c.id)
    open_curves = {k: c for k, c in open_curves.items() if not c.closed}

    # Chains (for sweep paths / diagnostics) before pruning.
    open_chains = _chains(open_curves, vtx)

    # Prune dangling edges iteratively.
    alive = set(open_curves)
    changed = True
    while changed:
        changed = False
        deg: dict[str, int] = {}
        for cid in alive:
            c = open_curves[cid]
            deg[vtx(c.start)] = deg.get(vtx(c.start), 0) + 1
            deg[vtx(c.end)] = deg.get(vtx(c.end), 0) + 1
        for cid in sorted(alive):
            c = open_curves[cid]
            if deg[vtx(c.start)] == 1 or deg[vtx(c.end)] == 1:
                alive.discard(cid)
                changed = True
    dangling = sorted(set(open_curves) - alive)
    if dangling:
        warnings.append({"code": "open_profile", "entities": dangling,
                         "message": "Open (dangling) geometry is not part of any closed region: " + ", ".join(dangling)})

    # Half-edge traversal per vertex.
    out_edges: dict[str, list[tuple[float, str, bool]]] = {}
    for cid in alive:
        c = open_curves[cid]
        for rev in (False, True):
            poly = c.poly[::-1] if rev else c.poly
            origin = vtx(c.end if rev else c.start)
            d = poly[min(1, len(poly) - 1)] - poly[0]
            if len(poly) > 2:
                d = poly[1] - poly[0]
            ang = math.atan2(d[1], d[0])
            out_edges.setdefault(origin, []).append((ang, cid, rev))
    for v in out_edges:
        out_edges[v].sort()

    def dest(cid: str, rev: bool) -> str:
        c = open_curves[cid]
        return vtx(c.start if rev else c.end)

    used: set[tuple[str, bool]] = set()
    faces: list[Loop] = []
    for v in sorted(out_edges):
        for _, cid, rev in out_edges[v]:
            if (cid, rev) in used:
                continue
            loop: list[tuple[str, bool]] = []
            cur = (cid, rev)
            guard = 0
            while cur not in used and guard < 100000:
                guard += 1
                used.add(cur)
                loop.append(cur)
                at = dest(*cur)
                lst = out_edges[at]
                twin_i = next(i for i, (_, c2, r2) in enumerate(lst) if c2 == cur[0] and r2 != cur[1])
                _, nc, nr = lst[(twin_i - 1) % len(lst)]
                cur = (nc, nr)
            polys = [(open_curves[e].poly[::-1] if r else open_curves[e].poly)[:-1] for e, r in loop]
            faces.append(Loop(loop, np.vstack(polys)))

    # Components: group faces by connectivity of their edges.
    comp_uf = _UF()
    for cid in alive:
        c = open_curves[cid]
        comp_uf.union("v:" + vtx(c.start), "c:" + cid)
        comp_uf.union("v:" + vtx(c.end), "c:" + cid)
    components: dict[str, dict[str, Any]] = {}
    for f in faces:
        key = comp_uf.find("c:" + f.edges[0][0])
        comp = components.setdefault(key, {"bounded": [], "outer": None})
        if f.area > 1e-12 * scale * scale:
            comp["bounded"].append(f)
        else:
            if comp["outer"] is None or abs(f.area) > abs(comp["outer"].area):
                comp["outer"] = f
    for c in cl:
        if c.closed:
            poly = c.poly[:-1] if np.linalg.norm(c.poly[0] - c.poly[-1]) < tol else c.poly
            loop = Loop([(c.id, False)], poly)
            if loop.area < 0:
                loop = Loop([(c.id, True)], poly[::-1])
            components["closed:" + c.id] = {"bounded": [loop], "outer": loop}

    # Containment between components to build holes and depths.
    atomic: list[tuple[str, Loop]] = [(k, f) for k, comp in components.items() for f in comp["bounded"]]
    parent: dict[str, tuple[str, Loop] | None] = {}
    for k, comp in components.items():
        outer = comp["outer"]
        if outer is None:
            parent[k] = None
            continue
        probe = outer.poly[0]
        best: tuple[str, Loop] | None = None
        for k2, f in atomic:
            if k2 == k:
                continue
            if _point_in_poly(probe, f.poly) and (best is None or abs(f.area) < abs(best[1].area)):
                best = (k2, f)
        parent[k] = best

    def depth_of(k: str) -> int:
        d, cur, seen = 0, parent.get(k), set()
        while cur is not None and cur[0] not in seen:
            seen.add(cur[0])
            d += 1
            cur = parent.get(cur[0])
        return d

    regions: list[Region] = []
    used_ids: set[str] = set()
    for k, f in atomic:
        holes = []
        for k2, par in parent.items():
            if par is not None and par[1] is f:
                o = components[k2]["outer"]
                holes.append(Loop([(e, not r) for e, r in reversed(o.edges)], o.poly[::-1]))
        rid = "r:" + "+".join(sorted({e for e, _ in f.edges}))
        base, n = rid, 2
        while rid in used_ids:
            rid = f"{base}#{n}"
            n += 1
        used_ids.add(rid)
        regions.append(Region(rid, f, holes, depth_of(k)))
    regions.sort(key=lambda r: (r.depth, r.id))
    if not regions:
        warnings.append({"code": "no_closed_region", "message": "Sketch contains no closed region"})
    return ProfileResult(curves, regions, errors, warnings, open_chains, {p: vtx(p) for p in ids})


def _chains(open_curves: dict[str, Curve], vtx) -> list[list[tuple[str, bool]]]:
    adj: dict[str, list[str]] = {}
    for c in open_curves.values():
        adj.setdefault(vtx(c.start), []).append(c.id)
        adj.setdefault(vtx(c.end), []).append(c.id)
    seen: set[str] = set()
    chains = []
    starts = [v for v, es in sorted(adj.items()) if len(es) == 1] + sorted(adj)
    for v0 in starts:
        for cid in adj.get(v0, []):
            if cid in seen:
                continue
            chain, v = [], v0
            cur: str | None = cid
            while cur is not None and cur not in seen:
                seen.add(cur)
                c = open_curves[cur]
                rev = vtx(c.start) != v
                chain.append((cur, rev))
                v = vtx(c.start) if rev else vtx(c.end)
                nxt = [e for e in adj.get(v, []) if e not in seen]
                cur = nxt[0] if len(adj.get(v, [])) == 2 and nxt else None
            chains.append(chain)
    return chains
