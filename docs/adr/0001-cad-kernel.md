# ADR-0001: Server-side OCCT B-rep kernel in isolated worker processes

- Status: Accepted (Phase 0, revisited with measurements in Phase 7)
- Date: 2026-10-16
- Deciders: AutoForge engineering

## Context

AutoForge needs exact boundary-representation (B-rep) solid modelling: parametric features, booleans, fillets,
shelling, patterns, exact mass properties, STEP/IGES exchange and hidden-line projection for drawings. Mesh-only
modelling (e.g. CSG on triangles) cannot deliver exact faces and edges, persistent naming or valid STEP output, so it
was rejected outright. The only mature open-source B-rep kernel is Open CASCADE Technology (OCCT, LGPL-2.1 with an
exception). Two deployment options exist:

1. **In the browser** via a WebAssembly build (opencascade.js), so geometry runs on the user's machine.
2. **On the server** via native OCCT (OCP Python bindings), with the browser receiving tessellations.

## Measurements

The same operations were run with both options on the same machine
([raw results](benchmarks/kernel-2026-10-16.json); harness in `tools/kernel-bench/`). Identical volumes and triangle
counts confirm both executed the same geometry.

| Operation | Browser (opencascade.js 1.1.1, WASM) | Server (OCCT 8.0 via OCP) | Ratio |
|---|---|---|---|
| Kernel download / start-up | 62.8 MB download, 847 ms compile + instantiate | 0 (warm worker processes) | — |
| Memory | 242 MB JS heap + WASM heap | 303 MB peak RSS per process | similar |
| Plate, 4 holes, 4 fillets | 666 ms (+64 ms mesh) | 27 ms (+6 ms mesh) | ~25× |
| Disc with 60-hole pattern | 1,928 ms (+274 ms mesh) | 196 ms (+99 ms mesh) | ~10× |
| STEP export (242 kB) | 311 ms | 21 ms | ~15× |
| 190-pair interference (exact common volume) | 3,270 ms | 145 ms | ~22× |

The full server parametric pipeline (feature engine, persistent naming, cache, viewer tessellation) recomputes the
small part in 59 ms and a parameter edit in 46 ms; client round trips measured in the browser walkthrough were
60–90 ms including network and JSON decoding.

The WASM build is older (OCCT 7.4-era API), which explains part of the gap; single-threaded WASM and the lack of
SIMD-optimised builds explain the rest. Even a much better WASM build would not remove the 60 MB download.

## Decision

Run OCCT **on the server** in a pool of persistent, isolated worker processes:

- Workers are spawned processes with an address-space limit (`RLIMIT_AS`), a wall-clock timeout per call, crash
  detection and automatic restart; document affinity keeps per-document caches warm.
- The browser receives compact binary tessellations (base64 positions/normals/indices) with per-face and per-edge
  metadata so picking maps triangles back to persistent face names.
- Recompute results are content-addressed and cached in object storage; repeated views of the same revision are
  cache hits.

## Consequences

Positive
- 10–25× faster operations, no multi-megabyte kernel download, consistent results for all users (same OCCT build),
  and confidential geometry stays on infrastructure the organisation controls.
- Hostile or malformed CAD files are parsed only in disposable, memory-limited processes.
- The same kernel serves exports, drawings (HLR), simulation meshing input and interference checks.

Negative
- Modelling needs a network connection; interactive feedback is bounded by round-trip latency (mitigated by
  debounced preview requests and caching). Offline modelling is not supported.
- Server CPU cost scales with active users; capacity planning is in `docs/runbooks/deploy.md`.

## Alternatives considered

- **opencascade.js in the browser**: rejected on the measurements above (download size, start-up, 10–25× slower).
  Kept as a possible future read-only viewer for offline review.
- **Commercial kernels (Parasolid, ACIS)**: licensing incompatible with a self-hostable open stack.
- **Mesh/CSG kernels (e.g. Manifold)**: excellent for meshes, but no exact B-rep, no STEP, no persistent B-rep naming.
