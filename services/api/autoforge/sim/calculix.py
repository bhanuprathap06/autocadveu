"""CalculiX (ccx) adapter: input deck generation, execution, result parsing.

Unit system (consistent): mm, N, MPa, tonne/mm^3, s, W, degC.
Elements: C3D10 quadratic tetrahedra.

Distributed loads are converted to *exact* consistent nodal loads for 6-node
triangles under uniform traction (corner nodes 0, mid-side nodes A/3 each),
which avoids element-face bookkeeping for pressure and arbitrary-direction
forces. Convection uses *FILM on element faces (face numbering per the
CalculiX/Abaqus C3D10 convention).
"""

from __future__ import annotations

import os
import re
import resource
import shutil
import subprocess
import time
from dataclasses import dataclass
from typing import Any

import numpy as np

# C3D10 face definitions by corner indices (0-based): F1..F4
TET_FACES = [(0, 1, 2), (0, 3, 1), (1, 3, 2), (2, 3, 0)]


class SolverError(RuntimeError):
    def __init__(self, message: str, code: str = "solver_failed", log: str = ""):
        super().__init__(message)
        self.code = code
        self.log = log


@dataclass
class Mesh:
    nodes: np.ndarray  # (n, 3)
    node_ids: np.ndarray  # (n,)
    tets: np.ndarray  # (m, 10) node ids, CCX order
    tet_ids: np.ndarray
    boundary_tris: np.ndarray  # (k, 6) node ids
    faces: dict[str, np.ndarray]  # name -> (k, 6)

    @staticmethod
    def load(path: str, face_names: list[str]) -> "Mesh":
        z = np.load(path)
        faces = {n: z[f"face_{i}"] for i, n in enumerate(face_names)}
        return Mesh(z["nodes"], z["node_ids"], z["tets"], z["tet_ids"], z["boundary_tris"], faces)

    def coords(self) -> dict[int, np.ndarray]:
        return {int(i): p for i, p in zip(self.node_ids, self.nodes)}


def _tri_area_normal(P: np.ndarray, tri: np.ndarray) -> tuple[float, np.ndarray]:
    a, b, c = P[tri[0]], P[tri[1]], P[tri[2]]
    n = np.cross(b - a, c - a)
    area = 0.5 * float(np.linalg.norm(n))
    return area, (n / (2 * area) if area > 0 else n)


class _Owner:
    """Find the owning tet + face number + outward normal for boundary triangles."""

    def __init__(self, mesh: Mesh):
        self.map: dict[tuple[int, int, int], tuple[int, int, int]] = {}
        for eid, t in zip(mesh.tet_ids, mesh.tets):
            corners = t[:4]
            for fi, (i, j, k) in enumerate(TET_FACES):
                key = tuple(sorted((int(corners[i]), int(corners[j]), int(corners[k]))))
                opp = int(corners[({0, 1, 2, 3} - {i, j, k}).pop()])
                self.map[key] = (int(eid), fi + 1, opp)

    def lookup(self, tri: np.ndarray) -> tuple[int, int, int]:
        key = tuple(sorted(int(x) for x in tri[:3]))
        if key not in self.map:
            raise SolverError("Boundary triangle does not belong to any element (mesh inconsistency)", "mesh_error")
        return self.map[key]


def consistent_loads(mesh: Mesh, face: str, traction: np.ndarray | None, pressure: float | None,
                     owner: _Owner, P: dict[int, np.ndarray]) -> tuple[dict[int, np.ndarray], float]:
    """Nodal loads for uniform traction (vector) or pressure (normal) over a named face."""
    loads: dict[int, np.ndarray] = {}
    total_area = 0.0
    for tri in mesh.faces[face]:
        pts = {int(n): P[int(n)] for n in tri}
        a, b, c = (pts[int(tri[i])] for i in range(3))
        nrm = np.cross(b - a, c - a)
        area = 0.5 * float(np.linalg.norm(nrm))
        if area <= 0:
            continue
        nrm = nrm / (2 * area)
        _, _, opp = owner.lookup(tri)
        if np.dot(nrm, a - P[opp]) < 0:  # make it outward
            nrm = -nrm
        total_area += area
        f = traction if traction is not None else -pressure * nrm  # pressure acts against outward normal
        for mid in tri[3:6]:
            loads[int(mid)] = loads.get(int(mid), np.zeros(3)) + f * area / 3.0
    return loads, total_area


def face_nodes(mesh: Mesh, faces: list[str]) -> list[int]:
    ids: set[int] = set()
    for f in faces:
        ids.update(int(x) for x in mesh.faces[f].ravel())
    return sorted(ids)


def _fmt_set(name: str, ids: list[int]) -> list[str]:
    out = [f"*NSET, NSET={name}"]
    for i in range(0, len(ids), 12):
        out.append(", ".join(str(x) for x in ids[i:i + 12]))
    return out


def write_static_deck(mesh: Mesh, setup: Any) -> tuple[str, dict[str, Any]]:
    P = mesh.coords()
    owner = _Owner(mesh)
    L = ["** AutoForge Engineering Studio - linear static analysis", "** Units: mm, N, MPa, tonne/mm^3, s", "*NODE, NSET=NALL"]
    L += [f"{int(i)}, {p[0]:.9g}, {p[1]:.9g}, {p[2]:.9g}" for i, p in zip(mesh.node_ids, mesh.nodes)]
    L.append("*ELEMENT, TYPE=C3D10, ELSET=EALL")
    L += [f"{int(e)}, " + ", ".join(str(int(n)) for n in t) for e, t in zip(mesh.tet_ids, mesh.tets)]
    fixed = face_nodes(mesh, [f for s in setup.supports for f in s.faces])
    L += _fmt_set("NFIX", fixed)
    m = setup.material
    L += ["*MATERIAL, NAME=MAT", "*ELASTIC", f"{m.youngs_modulus_mpa:.9g}, {m.poisson_ratio:.9g}"]
    if m.density_kg_m3:
        L += ["*DENSITY", f"{m.density_kg_m3 * 1e-12:.9g}"]
    L += ["*SOLID SECTION, ELSET=EALL, MATERIAL=MAT", "*BOUNDARY", "NFIX, 1, 3, 0.0",
          "*STEP", "*STATIC"]
    applied = np.zeros(3)
    cload: dict[int, np.ndarray] = {}
    info: dict[str, Any] = {"fixed_nodes": len(fixed), "loads": []}
    for ld in setup.loads:
        if ld.type in ("pressure", "force"):
            area_total = sum(_tri_area_normal(np.array([P[int(n)] for n in tri]), np.arange(3))[0] for f in ld.faces
                             for tri in mesh.faces[f])
            if area_total <= 0:
                raise SolverError("Loaded face has zero area", "invalid_setup")
            for f in ld.faces:
                if ld.type == "force":
                    nl, _ = consistent_loads(mesh, f, np.array(ld.vector_n) / area_total, None, owner, P)
                else:
                    nl, _ = consistent_loads(mesh, f, None, ld.value_mpa, owner, P)
                for k, v in nl.items():
                    cload[k] = cload.get(k, np.zeros(3)) + v
            info["loads"].append({"type": ld.type, "faces": ld.faces, "area_mm2": area_total})
        elif ld.type == "gravity":
            g = np.array(ld.vector_mm_s2, float)
            gm = float(np.linalg.norm(g))
            L += ["*DLOAD", f"EALL, GRAV, {gm:.9g}, {g[0] / gm:.9g}, {g[1] / gm:.9g}, {g[2] / gm:.9g}"]
            info["loads"].append({"type": "gravity", "g_mm_s2": g.tolist()})
    if cload:
        L.append("*CLOAD")
        for n, v in sorted(cload.items()):
            for d in range(3):
                if abs(v[d]) > 0:
                    L.append(f"{n}, {d + 1}, {v[d]:.12g}")
            applied += v
    info["applied_nodal_force_n"] = applied.tolist()
    L += ["*NODE FILE", "U", "*EL FILE", "S, E", "*NODE PRINT, NSET=NFIX, TOTALS=ONLY", "RF", "*END STEP"]
    return "\n".join(L) + "\n", info


def write_thermal_deck(mesh: Mesh, setup: Any) -> tuple[str, dict[str, Any]]:
    P = mesh.coords()
    owner = _Owner(mesh)
    L = ["** AutoForge Engineering Studio - steady-state heat conduction", "** Units: mm, W, degC", "*NODE, NSET=NALL"]
    L += [f"{int(i)}, {p[0]:.9g}, {p[1]:.9g}, {p[2]:.9g}" for i, p in zip(mesh.node_ids, mesh.nodes)]
    L.append("*ELEMENT, TYPE=C3D10, ELSET=EALL")
    L += [f"{int(e)}, " + ", ".join(str(int(n)) for n in t) for e, t in zip(mesh.tet_ids, mesh.tets)]
    m = setup.material
    ambient = setup.convection[0].ambient_c if setup.convection else setup.temperatures[0].value_c
    L += ["*MATERIAL, NAME=MAT", "*CONDUCTIVITY", f"{m.thermal_conductivity_w_mk * 1e-3:.9g}",
          "*SOLID SECTION, ELSET=EALL, MATERIAL=MAT",
          "*INITIAL CONDITIONS, TYPE=TEMPERATURE", f"NALL, {ambient:.9g}"]
    info: dict[str, Any] = {"bcs": []}
    bset = []
    for k, t in enumerate(setup.temperatures):
        ids = face_nodes(mesh, t.faces)
        L += _fmt_set(f"NT{k}", ids)
        bset.append((f"NT{k}", t.value_c))
        info["bcs"].append({"type": "temperature", "faces": t.faces, "value_c": t.value_c, "nodes": len(ids)})
    L += _fmt_set("NTALL", face_nodes(mesh, [f for t in setup.temperatures for f in t.faces]))
    L += ["*STEP", "*HEAT TRANSFER, STEADY STATE", "*BOUNDARY"] + [f"{n}, 11, 11, {v:.9g}" for n, v in bset]
    cflux: dict[int, float] = {}
    q_in = 0.0
    for fl in setup.fluxes:
        for f in fl.faces:
            nl, area = consistent_loads(mesh, f, np.array([fl.value_w_mm2, 0, 0]), None, owner, P)
            for k2, v in nl.items():
                cflux[k2] = cflux.get(k2, 0.0) + float(v[0])
            q_in += fl.value_w_mm2 * area
        info["bcs"].append({"type": "heat_flux", "faces": fl.faces, "value_w_mm2": fl.value_w_mm2})
    if cflux:
        L.append("*CFLUX")
        L += [f"{n}, 11, {v:.12g}" for n, v in sorted(cflux.items())]
    films = []
    for cv in setup.convection:
        for f in cv.faces:
            for tri in mesh.faces[f]:
                eid, fno, _ = owner.lookup(tri)
                films.append(f"{eid}, F{fno}, {cv.ambient_c:.9g}, {cv.h_w_mm2k:.9g}")
        info["bcs"].append({"type": "convection", "faces": cv.faces, "h_w_mm2k": cv.h_w_mm2k, "ambient_c": cv.ambient_c})
    if films:
        L.append("*FILM")
        L += films
    info["applied_heat_w"] = q_in
    L += ["*NODE FILE", "NT", "*EL FILE", "HFL", "*NODE PRINT, NSET=NTALL, TOTALS=ONLY", "RFL", "*END STEP"]
    return "\n".join(L) + "\n", info


def ccx_version(binary: str = "ccx") -> str:
    exe = shutil.which(binary)
    if not exe:
        raise SolverError("CalculiX (ccx) is not installed or not on PATH", "solver_unavailable")
    out = subprocess.run([exe, "-v"], capture_output=True, text=True, timeout=10).stdout.strip()
    m = re.search(r"Version\s+([\d.]+)", out)
    return f"CalculiX {m.group(1)}" if m else out or "CalculiX (unknown version)"


def _limits(mem_mb: int, cpu_s: int):
    def fn() -> None:
        # Stay in the job's process group so cancelling the job also kills the solver.
        resource.setrlimit(resource.RLIMIT_AS, (mem_mb * 1024 * 1024, mem_mb * 1024 * 1024))
        resource.setrlimit(resource.RLIMIT_CPU, (cpu_s, cpu_s + 5))

    return fn


def run_ccx(workdir: str, job: str, binary: str, timeout_s: int, mem_mb: int, cancel_check=None) -> dict[str, Any]:
    exe = shutil.which(binary)
    if not exe:
        raise SolverError("CalculiX (ccx) is not installed or not on PATH", "solver_unavailable")
    env = {"PATH": os.environ.get("PATH", ""), "OMP_NUM_THREADS": "1", "CCX_NPROC_EQUATION_SOLVER": "1"}
    t0 = time.time()
    proc = subprocess.Popen([exe, "-i", job], cwd=workdir, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, env=env,
                            preexec_fn=_limits(mem_mb, timeout_s), text=True)
    while True:
        try:
            out, _ = proc.communicate(timeout=1.0)
            break
        except subprocess.TimeoutExpired:
            if time.time() - t0 > timeout_s:
                proc.kill()
                proc.communicate()
                raise SolverError(f"Solver exceeded {timeout_s} s", "timed_out")
            if cancel_check and cancel_check():
                proc.kill()
                proc.communicate()
                raise SolverError("Cancelled by user", "cancelled")
    wall = time.time() - t0
    errors = [ln.strip() for ln in out.splitlines() if "*ERROR" in ln]
    if errors or not os.path.exists(os.path.join(workdir, job + ".frd")) or "Job finished" not in out:
        raise SolverError("CalculiX reported errors: " + ("; ".join(errors[:5]) or f"exit code {proc.returncode}"),
                          "solver_failed", out[-20000:])
    warnings = [ln.strip() for ln in out.splitlines() if "*WARNING" in ln]
    return {"wall_s": wall, "returncode": proc.returncode, "log": out, "warnings": warnings[:50]}


def parse_frd(path: str) -> dict[str, dict[int, np.ndarray]]:
    """Parse nodal result blocks from an ASCII .frd file (last step wins)."""
    out: dict[str, dict[int, np.ndarray]] = {}
    name = None
    ncomp = 0
    cur: dict[int, list[float]] = {}
    with open(path, errors="replace") as fh:
        for line in fh:
            if line.startswith(" -4"):
                name = line[5:13].strip()
                ncomp = int(line[13:18])
                cur = {}
            elif line.startswith(" -1") and name is not None:
                nid = int(line[3:13])
                vals = [float(line[13 + 12 * k:25 + 12 * k]) for k in range(min(ncomp, 6)) if len(line) >= 25 + 12 * k]
                cur[nid] = vals
            elif line.startswith(" -3") and name is not None:
                out[name] = {k: np.array(v) for k, v in cur.items()}
                name = None
    return out


def parse_dat_totals(path: str) -> dict[str, list[float]]:
    totals: dict[str, list[float]] = {}
    if not os.path.exists(path):
        return totals
    with open(path, errors="replace") as fh:
        lines = fh.readlines()
    for i, ln in enumerate(lines):
        if "total" in ln and "for set" in ln:
            key = ln.strip().split(" for set")[0]
            for nxt in lines[i + 1:i + 4]:
                nums = re.findall(r"[-+]?\d+\.\d+E[-+]\d+", nxt)
                if nums:
                    totals[key] = [float(x) for x in nums]
                    break
    return totals


def von_mises(s: np.ndarray) -> np.ndarray:
    sxx, syy, szz, sxy, syz, szx = (s[:, i] for i in range(6))
    return np.sqrt(0.5 * ((sxx - syy) ** 2 + (syy - szz) ** 2 + (szz - sxx) ** 2) + 3 * (sxy ** 2 + syz ** 2 + szx ** 2))
