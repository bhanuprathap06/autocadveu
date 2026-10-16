# Runbook: backup and restore

## What to back up

| Data | Where | Method |
|---|---|---|
| Accounts, projects, all revisions, drawings, simulations metadata, audit log | Postgres | `pg_dump -Fc` |
| Imported CAD blobs, exports, simulation artefacts, caches | Object storage | checksummed tar archive (or provider snapshots/replication) |
| Configuration | `.env` | Secret manager; never store with the backup in plain text |

Caches (`cache/` prefixes) are regenerated on demand but are included for faster recovery.

## Backup (docker compose deployment)

    infra/scripts/backup.sh .env /var/backups/autoforge

This writes `<timestamp>/db.dump`, `objects.tar` (every object with its SHA-256 in a manifest) and `SHA256SUMS`.
The database is dumped first; object keys are content-addressed and immutable, so every object referenced by the dump
already exists when the archive is taken. Encrypt and copy the directory off-host (e.g. `age`/`gpg` + object storage
with versioning). Recommended: nightly backups, 30 daily + 12 monthly retention, and a restore drill each quarter.

## Restore

    infra/scripts/restore.sh .env /var/backups/autoforge/<timestamp>

The script verifies the backup checksums, starts the data services, stops the application, restores the database
(`pg_restore --clean`), recreates the bucket if needed, imports objects (rejecting any object whose checksum does not
match its manifest), runs migrations, runs `scripts/verify_integrity.py`, and starts everything.

`verify_integrity.py` fails the restore if any revision's content hash does not match, any stored file, import blob or
simulation artefact is missing or corrupted, or any of the first 20 part documents fails to regenerate in the kernel
with errors that were not present when it was saved.

## Drill record

2026-10-16, reference compose stack: backup, `docker compose down -v` (all volumes destroyed), fresh start (empty
database confirmed), restore. Result: checksums OK, 12 objects imported, integrity check passed (5 revisions, 1 file,
5 simulation artefacts, kernel recompute), state fingerprint (row counts + hash of all revision hashes) identical to
before the backup, login and a fresh verified STEP export succeeded.
