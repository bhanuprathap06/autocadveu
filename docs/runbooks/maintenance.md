# Runbook: routine maintenance

| Frequency | Task | How |
|---|---|---|
| Daily | Backups | `infra/scripts/backup.sh` (cron/systemd timer) and off-host copy |
| Daily | Retention | `docker compose … exec api python -m autoforge.cli purge` — permanently removes projects soft-deleted longer than `AF_PROJECT_RETENTION_DAYS` (default 30) including their objects, and audit events older than `AF_AUDIT_RETENTION_DAYS` (default 365) |
| Weekly | Health review | Admin console: failed jobs (24 h), unsent emails, catalog records needing review; `/readyz` |
| Monthly | Updates | Rebuild images for OS and dependency security updates; run the CI suite; deploy per [deploy.md](deploy.md) |
| Monthly | Capacity | Postgres size, object storage size (`cache/` can be deleted at any time), worker queue latency |
| Quarterly | Restore drill | Restore the latest backup into a scratch stack and run `verify_integrity.py` |
| On kernel or solver upgrade | Re-validation | `python -m autoforge.bench`, `python scripts/sim_benchmarks.py`; compare with docs/validation and update them |

Model caches are keyed by kernel version, so a kernel upgrade invalidates them automatically; old cache objects can be
deleted from `projects/*/cache/` and `catalog/cache/`.
