"""Simulation setup schemas. Units: mm, N, MPa (N/mm^2), s, degC, W.

Every simulation records the exact setup, the model revision, the solver
version and the mesh it ran on. Results are never shown without them.
"""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator


class _M(BaseModel):
    model_config = ConfigDict(extra="forbid")


class SimMaterial(_M):
    name: str = Field(max_length=160)
    youngs_modulus_mpa: float | None = Field(default=None, gt=0, lt=1e7)
    poisson_ratio: float | None = Field(default=None, ge=0, lt=0.5)
    density_kg_m3: float | None = Field(default=None, gt=0, lt=30000)
    yield_strength_mpa: float | None = Field(default=None, gt=0)
    thermal_conductivity_w_mk: float | None = Field(default=None, gt=0, lt=5000)
    source: str = Field(default="", max_length=500)


class MeshSettings(_M):
    size_mm: float | None = Field(default=None, gt=0)  # None = automatic (bbox diagonal / 25)
    min_size_mm: float | None = Field(default=None, gt=0)
    order: Literal[2] = 2  # quadratic tetrahedra (C3D10); linear tets are too stiff for bending
    optimize: bool = True


class FixedSupport(_M):
    type: Literal["fixed"] = "fixed"
    faces: list[str] = Field(min_length=1, max_length=200)


class Pressure(_M):
    type: Literal["pressure"] = "pressure"
    faces: list[str] = Field(min_length=1, max_length=200)
    value_mpa: float  # positive pushes into the surface (along -outward normal)


class Force(_M):
    type: Literal["force"] = "force"
    faces: list[str] = Field(min_length=1, max_length=200)
    vector_n: tuple[float, float, float]  # total force, distributed as uniform traction over the faces


class Gravity(_M):
    type: Literal["gravity"] = "gravity"
    vector_mm_s2: tuple[float, float, float] = (0.0, 0.0, -9806.65)


class FixedTemperature(_M):
    type: Literal["temperature"] = "temperature"
    faces: list[str] = Field(min_length=1, max_length=200)
    value_c: float


class HeatFlux(_M):
    type: Literal["heat_flux"] = "heat_flux"
    faces: list[str] = Field(min_length=1, max_length=200)
    value_w_mm2: float  # positive into the body


class Convection(_M):
    type: Literal["convection"] = "convection"
    faces: list[str] = Field(min_length=1, max_length=200)
    h_w_mm2k: float = Field(gt=0)
    ambient_c: float


class StaticSetup(_M):
    analysis: Literal["static"] = "static"
    body: str
    material: SimMaterial
    mesh: MeshSettings = MeshSettings()
    supports: list[FixedSupport] = Field(min_length=1)
    loads: list[Pressure | Force | Gravity] = Field(min_length=1)
    convergence_study: bool = False

    @model_validator(mode="after")
    def _mat(self) -> "StaticSetup":
        m = self.material
        if m.youngs_modulus_mpa is None or m.poisson_ratio is None:
            raise ValueError("Static analysis requires Young's modulus and Poisson's ratio")
        if any(isinstance(l, Gravity) for l in self.loads) and m.density_kg_m3 is None:
            raise ValueError("Gravity load requires material density")
        return self


class ThermalSetup(_M):
    analysis: Literal["thermal"] = "thermal"
    body: str
    material: SimMaterial
    mesh: MeshSettings = MeshSettings()
    temperatures: list[FixedTemperature] = Field(min_length=1)
    fluxes: list[HeatFlux] = Field(default_factory=list)
    convection: list[Convection] = Field(default_factory=list)
    convergence_study: bool = False

    @model_validator(mode="after")
    def _mat(self) -> "ThermalSetup":
        if self.material.thermal_conductivity_w_mk is None:
            raise ValueError("Thermal analysis requires thermal conductivity")
        return self


SimSetup = StaticSetup | ThermalSetup


def parse_setup(d: dict) -> SimSetup:
    if d.get("analysis") == "thermal":
        return ThermalSetup.model_validate(d)
    return StaticSetup.model_validate(d)


def bc_faces(setup: SimSetup) -> list[str]:
    out: list[str] = []
    groups = (setup.supports + setup.loads) if isinstance(setup, StaticSetup) else (setup.temperatures + setup.fluxes + setup.convection)
    for g in groups:
        for f in getattr(g, "faces", []):
            if f not in out:
                out.append(f)
    return out
