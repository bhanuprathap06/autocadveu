# Runbook: incidents

## Triage

1. `curl https://<host>/readyz` — which dependency is failing (database, storage, redis, kernel)?
2. `docker compose -f infra/docker-compose.yml ps` and `logs --since 30m api worker`. Logs are JSON with `request_id`;
   users see the same request ID in error messages, so correlate from a user report.
3. Admin console: jobs (stuck, failed), outbox, audit log.

## Common situations

| Symptom | Likely cause | Action |
|---|---|---|
| Kernel check failing, models do not load | Worker processes crashed repeatedly or memory limit too low | Check API logs for kernel restarts; raise `AF_KERNEL_MEMORY_MB` or reduce model complexity; restart `api`. |
| Simulations stay queued | No worker running or worker cannot reach the database | `ps worker`; `logs worker`; stale running jobs are recovered automatically after missed heartbeats. |
| Simulations fail with solver errors | Invalid setup, mesh too large, or CalculiX missing | The job error and `solver.log` artefact explain it; `/api/v1/status` shows the CalculiX version. |
| Emails not delivered | SMTP misconfigured | Admin console → outbox shows the last error; fix `SMTP_*`, restart `worker`; pending mail is retried. |
| Storage errors on save/export | Object store unreachable or full | `/readyz` storage check; free space or fix credentials. Saves fail rather than lose data. |
| Suspected account compromise | — | Disable the user (admin console revokes all sessions immediately), review their audit events, reset credentials. |
| Suspected data exposure | — | Preserve logs and audit events, rotate secrets, follow your organisation's breach procedure. |

## Post-incident

Record timeline, impact, root cause and follow-ups; add a regression test where possible.
