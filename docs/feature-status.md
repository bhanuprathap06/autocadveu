# Feature status

_Generated from `apps/web/src/content/feature-status.json` (updated 2026-10-16). Do not edit by hand: run `python tools/gen_feature_status.py`. The same data drives the in-app /status page._

| Status | Meaning | Count |
|---|---|---|
| Implemented and verified | Works end to end and is covered by automated tests against the real engine. | 24 |
| Implemented with limitations | Works, with the stated limits. | 7 |
| Prototype | Exploratory; not for engineering decisions. | 0 |
| External integration required | Needs an external service or solver that is not bundled. Nothing is simulated in its place. | 2 |
| Planned | Not available yet. | 2 |

## CAD kernel

| Capability | Status | Evidence | Limits |
|---|---|---|---|
| Server-side OCCT B-rep kernel in isolated worker processes | Implemented and verified | Kernel pool tests: crash containment, timeout kill + recovery, structured errors, deterministic recompute. | Geometry is computed on the server; the browser receives tessellations. No offline modelling. |
| Parametric feature history with dependency graph, rollback and suppression | Implemented and verified | Forward-reference and parameter-cycle detection; downstream-only recompute; rollback/suppression tests. |  |
| Extrude, revolve, hole (simple/counterbore/countersink), fillet, chamfer, shell, booleans, patterns, mirror, primitives | Implemented and verified | Kernel tests check volumes against closed-form values, validity and persistent face names. |  |
| Sweep and loft | Implemented with limitations | Roundtrip test sweeps a profile along a sketch path and lofts two sections. | Single path wire, no guide curves, twist or tangency control. |
| Persistent topological naming (history-based face names, edge = face pair + hint) | Implemented with limitations | Fillet reference survives upstream dimension changes; unresolvable references produce a re-select error. | Faces split by later booleans can become ambiguous; the UI flags unstable names. |

## Sketching

| Capability | Status | Evidence | Limits |
|---|---|---|---|
| 2D constraint solver (22 constraint types, DOF, redundancy and conflict isolation) | Implemented and verified | Solver tests: fully/under/over-constrained, conflicts isolated, drag moves only free geometry. |  |
| Profile detection (regions, holes, islands, shared edges, open/crossing detection) | Implemented and verified | Profile tests including arc slots and nested islands. |  |
| Interactive sketch editor (lines, rectangles, circles, arcs, points, splines, constraints, dimensions) | Implemented with limitations | Uses the server solver for every edit; status, DOF and conflicts shown live. | No trim/extend/offset tools yet; ellipse drawing is API-only. |

## Data exchange

| Capability | Status | Evidence | Limits |
|---|---|---|---|
| STEP AP214, IGES, BREP export with re-import volume verification | Implemented and verified | Exports are re-read and the volume compared (rel. error < 1e-4) before download. |  |
| STL, OBJ, glTF (GLB) mesh export | Implemented and verified | Roundtrip test parses written meshes. |  |
| STEP/IGES/BREP import with healing and diagnostics | Implemented with limitations | Import tests reject garbage and mesh formats; imported solids become editable bases. | Imported solids carry no feature history; DXF, Parasolid and native CAD formats are not supported. |
| AutoForge native document (full parametric history, checksum) | Implemented and verified | Native export/import round trip with SHA-256 check. |  |

## Assemblies

| Capability | Status | Evidence | Limits |
|---|---|---|---|
| Mates (fixed, coincident, concentric, parallel, perpendicular, distance, angle, revolute, slider) with DOF reporting | Implemented and verified | Revolute leaves one DOF; conflicting and redundant mates are isolated. |  |
| Exact B-rep interference and clearance checks | Implemented and verified | Common-volume interference with bounding-box prefilter; measured with BRepExtrema. |  |
| Quasi-static kinematic sweeps with collision checks | Implemented with limitations | Sweep test checks limits and collisions per step. | Positions only; velocity/acceleration by finite differences; no dynamics, forces or contact. |
| Exploded views and bill of materials | Implemented and verified | BOM mass from CAD volume × assigned density or cited catalog data; missing masses are flagged. |  |

## Component library

| Capability | Status | Evidence | Limits |
|---|---|---|---|
| Searchable catalog with provenance, licences and completeness | Implemented and verified | Seed data is generated from standards (ISO 15, ISO 4762, ISO 4032, ISO 7089, cell formats) with manufacturer values left blank. | No manufacturer datasets are bundled; organisations import their own records with sources. |
| Side-by-side comparison with unit normalisation | Implemented and verified | Missing values are reported as missing, never inferred; the tool never declares parts interchangeable. |  |

## Simulation

| Capability | Status | Evidence | Limits |
|---|---|---|---|
| Linear static FEA (CalculiX, C3D10 tetrahedra via Gmsh) | Implemented and verified | Cantilever benchmark within 0.5 % of Timoshenko; equilibrium check on every run; mesh-convergence study. | Linear elastic, small deformation, single body; no contact, no bolt preload. |
| Steady-state thermal (CalculiX) | Implemented and verified | Flux and convection benchmarks match analytical temperatures exactly. |  |
| Durable job queue with cancellation, timeouts and crash recovery | Implemented and verified | Queue tests cover timeout, crash, failure, cancel and stale-job recovery. |  |
| CFD (external aerodynamics, coolant flow) | External integration required | No CFD solver is bundled. Results are never simulated or estimated in its place. | Requires an OpenFOAM (or similar) adapter and compute infrastructure. |
| Modal, buckling, fatigue, nonlinear and transient analyses | Planned | Not implemented. |  |

## Drawings

| Capability | Status | Evidence | Limits |
|---|---|---|---|
| Associative 2D drawings (HLR projections, auto dimensions, hole callouts, title block, revision table) | Implemented and verified | Drawing tests render views from the model, detect stale drawings and export PDF/DXF/SVG. |  |
| GD&T feature control frames and datums (ISO 1101 / ASME Y14.5 rule checks) | Implemented with limitations | Invalid frames block export; rule tests cover datum requirements and modifiers. | Rules check structure only; they do not verify that a tolerance is functionally appropriate. |

## Engineering calculators

| Capability | Status | Evidence | Limits |
|---|---|---|---|
| Shaft, gear, spring, beam, thermal, battery pack, ISO 286 fits, tolerance stack, unit conversion | Implemented and verified | Calculator tests compare with closed-form results and ISO 286 table values. |  |

## AI assistant

| Capability | Status | Evidence | Limits |
|---|---|---|---|
| Requirement extraction, concept generation and validated CAD proposals | Implemented with limitations | Schema-validated structured outputs; every claim is classified; proposals compile through the parametric builder before use. | Default provider is a deterministic rules engine; Claude is used only when configured and the user consents per request. |

## Collaboration

| Capability | Status | Evidence | Limits |
|---|---|---|---|
| Private rooms, single-use email-bound invitations, roles, comments, presence | Implemented and verified | Invitation lifecycle, comment permissions and WebSocket origin/auth tests. |  |
| Immutable revisions with optimistic concurrency, idempotent saves, diff and restore | Implemented and verified | Conflicts return 409 with the head revision; restores create new revisions. |  |
| Real-time co-editing of the same feature tree | Planned | Concurrent edits are detected and resolved explicitly (reload, overwrite or save a copy); there is no live merge. |  |

## Security

| Capability | Status | Evidence | Limits |
|---|---|---|---|
| Argon2id passwords, server sessions, CSRF, TOTP MFA, rate limits, IDOR-safe authorisation, audit log | Implemented and verified | Auth, CSRF, MFA, rate-limit and cross-project access tests. |  |
| Malware scanning of uploads | External integration required | Uploads are type-checked and content-sniffed; files are marked “not scanned” until a ClamAV integration is configured. |  |

## Platform

| Capability | Status | Evidence | Limits |
|---|---|---|---|
| Postgres persistence, S3-compatible storage, Redis events, Prometheus metrics, health/readiness | Implemented and verified | Restart-survival, readiness and metrics tests. |  |
| Container images and docker compose deployment (Caddy TLS, Postgres, Redis, S3-compatible storage, SMTP) | Implemented and verified | The Playwright acceptance suite passes against the production-mode compose stack; the backend suite passes inside the API image against Postgres and Redis containers. | Single-host topology; horizontal scaling of API and workers is supported by design but not load-tested. |
| Backup and restore (Postgres dump + checksummed object archive + integrity verification) | Implemented and verified | Drill: back up, destroy all volumes, restore into a fresh stack; state fingerprint identical, integrity check and a fresh verified export pass. |  |
