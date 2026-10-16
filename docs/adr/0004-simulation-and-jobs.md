# ADR-0004: Simulation with Gmsh + CalculiX in a durable job queue; CFD only through a real solver adapter

- Status: Accepted
- Date: 2026-10-16

## Context

Simulation results drive engineering decisions, so they must come from a real, validated solver, and their inputs
(geometry revision, mesh, material, boundary conditions, solver version) must be recorded with the result. Solves can
take minutes and use gigabytes of memory; they must not block the API or take down other users' work.

## Decision

- **Solver**: CalculiX (`ccx`, GPL) for linear static and steady-state thermal analysis, using quadratic tetrahedra
  (C3D10) meshed by Gmsh from the exact B-rep. Gmsh runs in its own process because it bundles a different OCCT build.
  Loads are applied as exact consistent nodal forces (corner nodes zero, mid-side nodes one third of the face share);
  convection uses `*FILM` with C3D10 face numbering.
- **Jobs**: a Postgres-backed queue (`SELECT … FOR UPDATE SKIP LOCKED`) with heartbeats, stale-job recovery,
  cancellation, timeouts and per-job memory limits. Each job runs in a child process group that is killed on timeout
  or cancel. Job submission happens in the same transaction as the simulation record (transactional outbox), and
  status changes are published to the project's realtime channel.
- **Result integrity**: every run stores the input deck, raw solver output, log and a compact surface field with
  SHA-256 checksums; reactions are checked against applied loads (equilibrium) and mesh quality is reported. An
  optional second solve on a 1.6× finer mesh reports result sensitivity. Failed runs show no results.
- **CFD**: no CFD solver is bundled. The capability is reported as `external_integration_required`; the product never
  renders CFD-like fields without a real solver run. An OpenFOAM adapter is in the backlog.

## Verification

Closed-form benchmarks are run by `services/api/scripts/sim_benchmarks.py` and included in the test suite
(cantilever deflection within 0.5 % of Timoshenko, mid-span bending stress within 0.1 %, axial extension within
0.3 %, conduction and convection cases exact). See `docs/validation/simulation-benchmarks.md`.

## Consequences

- Results are reproducible from stored artefacts with an independent CalculiX installation.
- Only linear, small-displacement, single-body analyses are offered; nonlinear, contact, modal, buckling and fatigue
  analyses are planned only after their own validation.
- Worker capacity (CPU, memory) bounds simulation throughput; scale workers horizontally.
