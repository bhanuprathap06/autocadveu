# Architecture

AutoForge is a browser application backed by a Python API that owns all engineering computation. Geometry, solving
and persistence happen on infrastructure the organisation controls; the browser renders tessellations and edits
documents.

## System context and containers

```mermaid
flowchart LR
  user([Engineer's browser]) -- HTTPS / WSS --> proxy[Caddy<br/>TLS, routing]
  proxy -- "/" --> web[web<br/>Next.js 16, React 19, R3F]
  proxy -- "/api/*" --> api[api<br/>FastAPI]
  api --> kpool[[Kernel worker pool<br/>OCCT 8 via OCP<br/>isolated processes]]
  api --> pg[(Postgres 16<br/>documents, revisions,<br/>jobs, audit)]
  api --> redis[(Redis<br/>pub/sub, presence,<br/>rate limits)]
  api --> s3[(S3-compatible storage<br/>blobs, exports,<br/>caches, results)]
  worker[worker<br/>job runner] --> pg
  worker --> s3
  worker --> redis
  worker --> sim[[Gmsh + CalculiX<br/>per-job process group]]
  worker -- SMTP --> mail[(Mail relay)]
  api -. opt-in per request .-> claude[(Claude API)]
```

- **web**: Next.js app (pages, workbenches, sketch editor, viewers). Talks only to the same-origin `/api/v1`.
  OpenAPI-generated TypeScript types make backend route or schema changes compile errors in the web app.
- **api**: FastAPI with synchronous endpoints, SQLAlchemy 2, Alembic migrations, session cookies + CSRF, authorisation
  on every object (404 for objects the caller cannot see), audit logging, Prometheus metrics, health/readiness.
- **Kernel worker pool**: persistent processes with memory and time limits that run feature recompute, sketch
  solving, tessellation, exports, imports, measurement, interference, drawing projections. Crashes are contained and
  the worker restarted; results for unchanged content are served from a content-addressed cache.
- **worker**: claims jobs from Postgres (`SKIP LOCKED`), runs simulations in child process groups, streams progress,
  stores artefacts with checksums, recovers stale jobs, delivers the email outbox.

## Document model

```mermaid
flowchart TB
  P[Project] --> D[CAD document<br/>part or assembly]
  D --> R1[Revision 1]
  D --> R2[Revision 2]
  D --> Rn[Revision n = head]
  Rn --> C[content JSON<br/>parameters, features,<br/>sketches, materials]
  Rn --> H[content SHA-256,<br/>kernel version,<br/>recompute summary]
  C -. import feature .-> B[(content-addressed blob)]
  P --> S[Simulations] --> A[(artefacts: deck, results,<br/>log, field, sha256)]
  P --> DR[Drawings<br/>pinned or following head]
  P --> RQ[Problems and requirements]
```

Part documents are an ordered feature history (sketch, extrude, revolve, sweep, loft, hole, fillet, chamfer, shell,
boolean, patterns, mirror, datums, primitives, transform, import) with named parameters and expressions. Assemblies
hold instances (part revision pinned or following head, or catalog items), groups, mates and motion studies.

## Edit, preview and save

```mermaid
sequenceDiagram
  participant B as Browser
  participant A as API
  participant K as Kernel worker
  participant DB as Postgres
  B->>A: POST /documents/{id}/recompute (unsaved content, debounced)
  A->>K: recompute + tessellate (cache by content hash)
  K-->>A: feature status, sketch solves, meshes with face/edge names
  A-->>B: model (preview)
  B->>A: POST /documents/{id}/revisions {base_revision, op_id, content} (autosave)
  A->>DB: lock document row, check head == base_revision
  A->>K: validate + recompute summary
  A->>DB: insert immutable revision, audit event
  A-->>B: revision n+1 (or 409 with head metadata)
  A--)B: WebSocket: revision.created (other clients refresh)
```

## Simulation

```mermaid
sequenceDiagram
  participant B as Browser
  participant A as API
  participant DB as Postgres
  participant W as Worker
  participant C as Gmsh/CalculiX
  B->>A: POST /projects/{id}/simulations (setup, idempotency key)
  A->>A: validate setup, faces exist on that revision, model has no errors
  A->>DB: simulation + job rows (one transaction)
  W->>DB: claim job (SKIP LOCKED), heartbeat
  W->>C: mesh C3D10, write deck, solve (time + memory limits)
  C-->>W: .frd/.dat
  W->>W: equilibrium check, mesh quality, optional refinement study
  W->>DB: results summary + artefact checksums
  W--)B: job.status via Redis -> WebSocket
```

## Security boundaries

See [security.md](security.md). In short: every request is authenticated by an HttpOnly session cookie and, for
state changes, a session-bound CSRF token; object access is checked per request and denied as "not found"; uploads
are size-limited, type-checked and parsed only in kernel workers; external AI is opt-in per request and never receives
CAD files.

## Key decisions

- [ADR-0001](adr/0001-cad-kernel.md) Server-side OCCT kernel (with browser vs server benchmarks)
- [ADR-0002](adr/0002-topological-naming.md) Persistent naming
- [ADR-0003](adr/0003-revisions-and-concurrency.md) Immutable revisions and concurrency
- [ADR-0004](adr/0004-simulation-and-jobs.md) Simulation and job queue
- [ADR-0005](adr/0005-ai-assistant-boundaries.md) AI assistant boundaries
