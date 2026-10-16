# Simulation validation

AutoForge only reports results from a real solver run (CalculiX), and validates the complete pipeline — B-rep to
Gmsh mesh to CalculiX deck to post-processing — against closed-form solutions. The current results are in
[benchmark-results.md](benchmark-results.md), regenerated with:

    cd services/api && python scripts/sim_benchmarks.py > ../../docs/validation/benchmark-results.md

The same cases run in the automated test suite (`tests/test_simulation.py`) with tolerances.

## Cases and references

| Case | Model | Reference solution |
|---|---|---|
| S1 | Steel bar 200×20×20 mm (E = 210 GPa, ν = 0.3), one end face fully fixed, 1 kN shear on the free end face | Timoshenko beam tip deflection δ = PL³/(3EI) + PL/(κGA), κ = 10(1+ν)/(12+11ν) |
| S2 | Same model | Bending stress on the top surface at mid-span σ = M·c/I = 75 MPa (far from the support singularity) |
| S3 | Same bar, 10 kN axial | δ = FL/(EA) |
| T1 | Bar 100×10×10 mm, k = 50 W/m·K, 20 °C fixed at one end, 0.01 W/mm² into the other | T(L) = T₀ + qL/k = 40 °C |
| T2 | Same bar, 100 °C fixed, convection h = 0.001 W/mm²·K to 20 °C at the other end | T(L) = 20 + (ΔT/(L/k + 1/h))/h = 46.667 °C |

## Interpretation

- Tip deflection is 0.4–0.5 % stiffer than Timoshenko and changes by < 0.1 % between the two meshes: the residual is a
  modelling difference (the fully clamped end face also restrains Poisson contraction and warping, which beam theory
  ignores), not discretisation error.
- Peak von Mises stress at the clamped edge is a **singularity**: it grows with mesh refinement and must not be used
  as a design value. The results page and report say so; the mid-span stress (S2) converges to the analytical value.
- Every static run checks global equilibrium (reaction forces vs applied loads; imbalance ~1e-11 in these cases) and
  reports mesh statistics and quality; users can request a refinement study (second solve on a 1.6× finer mesh) that
  flags > 5 % changes as unconverged.

## Scope

Validated: linear elastic small-displacement statics, steady-state conduction with fixed temperature, flux and
convection boundary conditions, C3D10 elements. Not validated and therefore not offered: contact, nonlinear material,
large deformation, modal, buckling, fatigue, transient and CFD analyses.
