"""Persistent topological naming.

Every face of a body carries one or more names derived from the feature that
created it (``f2:side:l1``, ``f2:end``, ``f7:wall:0`` ...). After each modelling
operation the names are propagated using OCCT's own history
(``Modified`` / ``Generated`` / ``IsDeleted`` on the operation object), so a
reference such as "fillet the edge between ``f2:end`` and ``f2:side:l1``" keeps
pointing at the same design intent when upstream dimensions change and face
indices are renumbered.

Edges are not named directly; they are referenced by the pair of named faces
they separate (plus an optional geometric hint used for disambiguation and as a
flagged fallback). This is the reference-resolution strategy described in
docs/adr/0002-topological-naming.md.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Iterable

import numpy as np
from OCP.TopAbs import TopAbs_FACE

from . import occ
from . import schema as S


class FeatureError(Exception):
    def __init__(self, message: str, code: str = "feature_failed", details: dict[str, Any] | None = None):
        super().__init__(message)
        self.code = code
        self.details = details or {}


@dataclass
class Body:
    name: str
    shape: Any
    names: list[tuple[Any, str]] = field(default_factory=list)

    def face_index(self) -> Any:
        return occ.indexed_map(self.shape, TopAbs_FACE)

    def faces_named(self, name: str) -> list[Any]:
        out: list[Any] = []
        for f, n in self.names:
            if n == name and not any(f.IsSame(o) for o in out):
                out.append(f)
        return out

    def names_by_face(self) -> list[tuple[Any, list[str]]]:
        """Faces in deterministic order with all names (falls back to index names)."""
        fmap = self.face_index()
        buckets: dict[int, list[str]] = {}
        for f, n in self.names:
            i = fmap.FindIndex(f)
            if i > 0:
                lst = buckets.setdefault(i, [])
                if n not in lst:
                    lst.append(n)
        out = []
        for i in range(1, fmap.Size() + 1):
            out.append((occ.cast(fmap.FindKey(i), "Face"), buckets.get(i) or [f"{self.name}:face#{i}"]))
        return out


def keep_present(names: Iterable[tuple[Any, str]], result: Any) -> list[tuple[Any, str]]:
    fmap = occ.indexed_map(result, TopAbs_FACE)
    out = []
    for f, n in names:
        idx = fmap.FindIndex(f)
        if idx > 0:
            out.append((occ.cast(fmap.FindKey(idx), "Face"), n))
    return out


def remap(names: Iterable[tuple[Any, str]], mk: Any, result: Any) -> list[tuple[Any, str]]:
    """Propagate face names through an OCCT operation with history."""
    out: list[tuple[Any, str]] = []
    for f, n in names:
        try:
            if mk.IsDeleted(f):
                continue
            mods = list(mk.Modified(f))
        except Exception:
            mods = []
        if mods:
            for m in mods:
                if m.ShapeType() == TopAbs_FACE:
                    out.append((m, n))
        else:
            out.append((f, n))
    return keep_present(out, result)


def generated(mk: Any, src: Any) -> list[Any]:
    try:
        return [g for g in mk.Generated(src) if g.ShapeType() == TopAbs_FACE]
    except Exception:
        return []


def primary_name(names: list[str]) -> str:
    return names[0]


def edge_refs(body: Body) -> list[tuple[Any, dict[str, Any]]]:
    """Every edge with its persistent reference (adjacent face names + hint)."""
    efm = occ.edge_face_map(body.shape)
    named = body.names_by_face()
    fmap = body.face_index()
    face_names = {fmap.FindIndex(f): ns for f, ns in named}
    out = []
    for i in range(1, efm.Size() + 1):
        e = occ.cast(efm.FindKey(i), "Edge")
        adj = [fmap.FindIndex(f) for f in efm.FindFromIndex(i)]
        fn = sorted({primary_name(face_names[a]) for a in adj if a in face_names})
        if len(fn) == 1:
            fn = fn * 2
        g = occ.edge_geometry(e)
        out.append((e, {"kind": "between", "faces": fn, "hint": [round(x, 6) for x in g["midpoint"]]}))
    return out


def resolve_edges(body: Body, ref: S.EdgeRef, warnings: list[str]) -> list[tuple[Any, str]]:
    """Resolve an EdgeRef to concrete edges, each with a stable label."""
    allrefs = edge_refs(body)
    label = lambda r: "|".join(r["faces"])  # noqa: E731
    if ref.kind == "all":
        return [(e, label(r)) for e, r in allrefs]
    if ref.kind == "of_face":
        faces = body.faces_named(ref.face or "")
        if not faces:
            raise FeatureError(f"Face {ref.face!r} no longer exists on body {body.name!r}; re-select it", "unresolved_reference")
        out = []
        for e, r in allrefs:
            if ref.face in r["faces"] or any(_edge_on_face(e, f) for f in faces):
                out.append((e, label(r)))
        return out
    if ref.kind == "between" and ref.faces and len(ref.faces) == 2:
        a, b = ref.faces
        fa, fb = body.faces_named(a), body.faces_named(b)
        hits: list[tuple[Any, dict]] = []
        if fa and fb:
            for e, r in allrefs:
                if _edge_on_any(e, fa) and _edge_on_any(e, fb):
                    hits.append((e, r))
        if hits:
            if ref.hint is not None and len(hits) > 1:
                # Several edges separate the same faces (e.g. a face split by a cut).
                # Keep all of them unless the hint clearly singles one out.
                d = [np.linalg.norm(np.array(occ.edge_geometry(e)["midpoint"]) - np.array(ref.hint)) for e, _ in hits]
                best = int(np.argmin(d))
                if d[best] < 1e-3:
                    hits = [hits[best]]
            return [(e, label(r)) for e, r in hits]
        if ref.hint is not None:
            e, r, dist = _nearest(allrefs, ref.hint)
            if e is not None and dist < 1e-3 * max(1.0, _diag(body)):
                warnings.append(f"Edge between {a} and {b} resolved by geometric fallback near {list(ref.hint)}")
                return [(e, label(r))]
        raise FeatureError(
            f"Edge between faces {a!r} and {b!r} could not be found on body {body.name!r}. "
            "The upstream geometry changed topology; re-select the edge.",
            "unresolved_reference",
            {"faces": [a, b]},
        )
    if ref.kind == "nearest" and ref.hint is not None:
        e, r, dist = _nearest(allrefs, ref.hint)
        if e is None:
            raise FeatureError("Body has no edges", "unresolved_reference")
        warnings.append("Edge selected by nearest-point query (fragile reference)")
        return [(e, label(r))]
    raise FeatureError("Invalid edge reference", "invalid_reference")


def _diag(body: Body) -> float:
    bb = occ.bbox(body.shape)
    if not bb:
        return 1.0
    return float(np.linalg.norm(np.array(bb[1]) - np.array(bb[0])))


def _nearest(allrefs, hint) -> tuple[Any, Any, float]:
    best, br, bd = None, None, float("inf")
    h = np.array(hint)
    for e, r in allrefs:
        d = float(np.linalg.norm(np.array(occ.edge_geometry(e)["midpoint"]) - h))
        if d < bd:
            best, br, bd = e, r, d
    return best, br, bd


def _edge_on_face(edge: Any, face: Any) -> bool:
    return any(edge.IsSame(e) for e in occ.edges(face))


def _edge_on_any(edge: Any, faces: list[Any]) -> bool:
    return any(_edge_on_face(edge, f) for f in faces)


def resolve_faces(body: Body, names: list[str]) -> list[Any]:
    out = []
    for n in names:
        fs = body.faces_named(n)
        if not fs:
            raise FeatureError(f"Face {n!r} not found on body {body.name!r}; re-select it", "unresolved_reference", {"face": n})
        out.extend(fs)
    return out
