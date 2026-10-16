# Runbook: rollback

1. **Application-only regression** (no new migration in the release): redeploy the previous image tag
   (`AUTOFORGE_VERSION=<previous> docker compose … up -d`). No data changes are needed.
2. **Release included a migration**: migrations are written to be backward compatible for one release (additive
   columns/tables first, destructive changes in a later release). Roll back the image as in (1). Only if the migration
   itself is faulty, restore the pre-upgrade backup ([backup-restore.md](backup-restore.md)) — this loses writes made
   after the backup, so announce it first.
3. **Bad document revision** (user error, not a release problem): no operator action. Users restore any earlier
   revision from History; restores create a new revision and keep the audit trail.
4. After any rollback: check `/readyz`, the admin console (failed jobs, outbox) and the audit log for the window.
