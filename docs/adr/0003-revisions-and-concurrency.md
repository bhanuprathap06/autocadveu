# ADR-0003: Immutable revisions, optimistic concurrency and explicit conflict resolution

- Status: Accepted
- Date: 2026-10-16

## Context

Engineering data must be auditable: every saved state of a model needs to be reproducible, attributable and
restorable. Several people may edit the same project, and browsers lose connectivity.

## Decision

- Each save creates an **immutable revision** (`cad_revisions`) holding the complete validated document, its canonical
  SHA-256, schema and kernel versions, author, message and the kernel recompute summary. History is never rewritten;
  "restore" creates a new revision whose content equals the old one (`restored_from` records the source).
- **Optimistic concurrency**: a save names its `base_revision`; the server accepts it only if that is still the head
  (row lock on the document). Otherwise it returns `409 revision_conflict` with the head's metadata.
- **Idempotency**: each save carries an `op_id`; retrying the same request returns the original revision, a reused
  `op_id` with different content is rejected (`idempotency_mismatch`). The client retries network failures with the
  same `op_id`, so no duplicate revisions are created.
- **No silent merges of CAD history.** On conflict the user chooses: discard local changes and load the head, save
  local changes as a new document, or save on top (the other person's revision stays in history).
- The browser autosaves after a short idle period and keeps a **local recovery draft** until the server confirms the
  save; on reopen, unsaved drafts are offered for recovery, including drafts based on an older head.
- Realtime events (WebSocket) only invalidate caches and show presence; durable state is always read through the
  REST API. A client with no unsaved changes follows other users' saves automatically.

## Consequences

- Full audit trail and exact reproducibility (content hash + kernel version per revision).
- Concurrent editing of the same document is serialised rather than merged; live co-editing of one feature tree is
  out of scope until a merge model for parametric history is designed (see `docs/backlog.md`).
- Storage grows with revisions; documents are JSON (typically kilobytes), large geometry lives in content-addressed
  blobs, so growth is modest.
