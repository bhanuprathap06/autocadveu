"""Feature dependency DAG.

Edges come from two sources:

* explicit references (a sketch id, a datum id, patterned feature ids, ...)
* body state: a feature that reads or modifies body ``B`` depends on the last
  feature that wrote ``B``.

References must point to earlier features in the history. Anything else is a
graph error (forward reference, unknown id, or cycle) reported per feature.
"""

from __future__ import annotations

from collections import defaultdict, deque
from dataclasses import dataclass, field

from . import schema as S


@dataclass
class FeatureIO:
    refs: set[str] = field(default_factory=set)  # referenced feature ids
    reads: set[str] = field(default_factory=set)  # bodies read (and not written)
    writes: set[str] = field(default_factory=set)  # bodies created or modified
    error: str | None = None


@dataclass
class Graph:
    order: list[str]
    deps: dict[str, set[str]]
    io: dict[str, FeatureIO]
    errors: dict[str, str]
    body_names: dict[str, str]  # feature id -> body name it produced/modified (primary)

    def downstream(self, fid: str) -> list[str]:
        """Features that must recompute if ``fid`` changes (in history order)."""
        rev: dict[str, set[str]] = defaultdict(set)
        for f, ds in self.deps.items():
            for d in ds:
                rev[d].add(f)
        seen: set[str] = set()
        q = deque([fid])
        while q:
            cur = q.popleft()
            for n in rev.get(cur, ()):
                if n not in seen:
                    seen.add(n)
                    q.append(n)
        return [f for f in self.order if f in seen]


def _plane_refs(p: S.PlaneRef, io: FeatureIO) -> None:
    if p.kind == "datum" and p.datum:
        io.refs.add(p.datum)
    if p.kind == "face" and p.body:
        io.reads.add(p.body)


def _axis_refs(a: S.AxisRef | None, io: FeatureIO) -> None:
    if a is None:
        return
    if a.kind == "datum" and a.datum:
        io.refs.add(a.datum)
    if a.kind in ("face", "edge") and a.body:
        io.reads.add(a.body)
    if a.kind == "sketch_line" and a.sketch:
        io.refs.add(a.sketch)


def default_body_name(n: int) -> str:
    return f"Body{n}"


def feature_io(f: S.Feature, new_body_name: str) -> FeatureIO:
    io = FeatureIO()
    p = f.params
    if isinstance(f, S.SketchF):
        _plane_refs(p.plane, io)
    elif isinstance(f, (S.ExtrudeF, S.RevolveF, S.SweepF, S.LoftF, S.PrimitiveF)):
        if isinstance(f, S.LoftF):
            io.refs.update(p.sketches)
        elif not isinstance(f, S.PrimitiveF):
            io.refs.add(p.sketch)
        if isinstance(f, S.SweepF):
            io.refs.add(p.path_sketch)
        if isinstance(f, S.RevolveF):
            _axis_refs(p.axis, io)
        body = p.body or new_body_name
        if p.operation != "new":
            if not p.body:
                io.error = f"{p.operation} requires a target body"
            io.reads.add(body)
        io.writes.add(body)
    elif isinstance(f, (S.HoleF, S.FilletF, S.ChamferF, S.ShellF)):
        if isinstance(f, S.HoleF):
            _plane_refs(p.plane, io)
        io.writes.add(p.body)
    elif isinstance(f, S.BooleanF):
        io.writes.add(p.target)
        io.reads.update(p.tools)
        if not p.keep_tools:
            io.writes.update(p.tools)
    elif isinstance(f, (S.LinearPatternF, S.CircularPatternF, S.MirrorF)):
        if p.features:
            io.refs.update(p.features)
        io.writes.add(p.body)
        if isinstance(f, S.LinearPatternF):
            _axis_refs(p.direction, io)
            _axis_refs(p.direction2, io)
        elif isinstance(f, S.CircularPatternF):
            _axis_refs(p.axis, io)
        else:
            _plane_refs(p.plane, io)
    elif isinstance(f, S.DatumPlaneF):
        _plane_refs(p.plane, io)
    elif isinstance(f, S.DatumAxisF):
        _axis_refs(p.axis, io)
    elif isinstance(f, S.TransformF):
        _axis_refs(p.rotate_axis, io)
        if p.copy_to:
            io.reads.add(p.body)
            io.writes.add(p.copy_to)
        else:
            io.writes.add(p.body)
    elif isinstance(f, S.ImportF):
        io.writes.add(p.body or new_body_name)
    io.reads -= io.writes
    return io


def build(doc: S.Document) -> Graph:
    order: list[str] = []
    position: dict[str, int] = {}
    deps: dict[str, set[str]] = {}
    ios: dict[str, FeatureIO] = {}
    errors: dict[str, str] = {}
    body_names: dict[str, str] = {}
    last_writer: dict[str, str] = {}
    new_count = 0
    all_ids = {f.id for f in doc.features}

    for idx, f in enumerate(doc.features):
        order.append(f.id)
        position[f.id] = idx
        creates_new = getattr(f.params, "operation", None) == "new" or isinstance(f, S.ImportF)
        if creates_new:
            new_count += 1
        name = default_body_name(new_count)
        io = feature_io(f, name)
        ios[f.id] = io
        if io.error:
            errors[f.id] = io.error
        d: set[str] = set()
        for r in io.refs:
            if r not in all_ids:
                errors.setdefault(f.id, f"References unknown feature {r!r}")
            elif r == f.id:
                errors.setdefault(f.id, "Feature references itself")
            elif position.get(r, 10**9) > idx:
                errors.setdefault(f.id, f"References later feature {r!r} (forward reference)")
            else:
                d.add(r)
        for b in io.reads | io.writes:
            if b in last_writer:
                d.add(last_writer[b])
            elif b in io.reads:
                errors.setdefault(f.id, f"Body {b!r} does not exist at this point in the history")
        if f.suppressed:
            # Suppressed features keep their references but do not write bodies.
            deps[f.id] = d
            continue
        if creates_new:
            target = io.writes and next(iter(io.writes))
            if target in last_writer and not isinstance(f, S.ImportF):
                errors.setdefault(f.id, f"Body {target!r} already exists; choose a new name or a join/cut operation")
        for b in io.writes:
            last_writer[b] = f.id
            body_names[f.id] = b
        deps[f.id] = d

    cyc = _find_cycle(order, deps)
    if cyc:
        for n in cyc:
            errors.setdefault(n, "Circular dependency: " + " -> ".join(cyc))
    return Graph(order=order, deps=deps, io=ios, errors=errors, body_names=body_names)


def _find_cycle(order: list[str], deps: dict[str, set[str]]) -> list[str] | None:
    indeg = {n: 0 for n in order}
    rev: dict[str, list[str]] = defaultdict(list)
    for n, ds in deps.items():
        for d in ds:
            if d in indeg:
                indeg[n] += 1
                rev[d].append(n)
    q = deque([n for n in order if indeg[n] == 0])
    seen = 0
    while q:
        n = q.popleft()
        seen += 1
        for m in rev[n]:
            indeg[m] -= 1
            if indeg[m] == 0:
                q.append(m)
    if seen == len(order):
        return None
    # Extract one cycle among remaining nodes.
    remaining = {n for n, k in indeg.items() if k > 0}
    start = next(n for n in order if n in remaining)
    path, cur, visited = [], start, {}
    while cur not in visited:
        visited[cur] = len(path)
        path.append(cur)
        cur = next(d for d in sorted(deps[cur]) if d in remaining)
    return path[visited[cur]:] + [cur]


def topo_order(nodes: list[str], deps: dict[str, set[str]]) -> list[str]:
    """Kahn topological sort preserving input order among independent nodes."""
    cyc = _find_cycle(nodes, deps)
    if cyc:
        raise ValueError("Circular dependency: " + " -> ".join(cyc))
    indeg = {n: len([d for d in deps.get(n, ()) if d in nodes]) for n in nodes}
    rev: dict[str, list[str]] = defaultdict(list)
    for n in nodes:
        for d in deps.get(n, ()):
            rev[d].append(n)
    out: list[str] = []
    ready = [n for n in nodes if indeg[n] == 0]
    pos = {n: i for i, n in enumerate(nodes)}
    while ready:
        ready.sort(key=pos.__getitem__)
        n = ready.pop(0)
        out.append(n)
        for m in rev[n]:
            indeg[m] -= 1
            if indeg[m] == 0:
                ready.append(m)
    return out
