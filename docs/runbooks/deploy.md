# Runbook: deploy

## Single host with docker compose (reference deployment)

Requirements: Linux host with Docker Engine 24+ and the compose plugin, 4+ vCPU, 16 GB RAM (simulation jobs are
limited to 8 GB each), 50 GB disk, DNS name pointing at the host, ports 80/443 open.

1. Configure:

       cp .env.example .env
       # set PUBLIC_BASE_URL=https://cad.example.com and SITE_ADDRESS=cad.example.com
       # generate secrets:  openssl rand -hex 32  -> POSTGRES_PASSWORD, S3_ACCESS_KEY, S3_SECRET_KEY
       # set SMTP_* to your mail relay (verification and invitation emails require it in production)

2. Build and start (the `migrate` service applies database migrations, creates the bucket and loads reference data
   before the API starts):

       docker compose -f infra/docker-compose.yml --env-file .env up -d --build

   Behind a TLS-inspecting corporate proxy, build images with the proxy CA as a secret, e.g.
   `docker build --secret id=extra_ca,src=proxy-ca.crt --build-arg HTTPS_PROXY=… services/api`.

3. Verify:

       curl -fsS https://cad.example.com/readyz          # database, storage, redis, kernel workers
       curl -fsS https://cad.example.com/api/v1/status   # kernel version, CalculiX version
       docker compose -f infra/docker-compose.yml ps     # all services healthy

4. Create the first administrator: register through the UI, then

       docker compose -f infra/docker-compose.yml exec api python -m autoforge.cli create-admin you@example.com

5. Optional: enable the AI provider (`AI_PROVIDER=anthropic`, `ANTHROPIC_API_KEY`), then `up -d api`.

## Using managed services

Point `AF_DATABASE_URL` at managed Postgres 16, `AF_REDIS_URL` at managed Redis, and the `AF_S3_*` settings at AWS S3
(leave `AF_S3_ENDPOINT_URL` empty to use AWS; SSE-S3 encryption is requested on every write) or another S3 service.
Run `api` and `worker` from the same image; scale `api` horizontally behind the proxy (sessions, presence and rate
limits live in Postgres/Redis) and add `worker` replicas for simulation throughput.

## Capacity guidance

- Each kernel worker process holds up to `AF_KERNEL_MEMORY_MB` (default 4 GB address-space limit); `AF_KERNEL_WORKERS`
  per API instance (default 2). Typical part recomputes take 30–250 ms (see ADR-0001).
- Simulations: one job per worker process at a time, `AF_SIM_MAX_RUNTIME_S` (default 900 s) and `AF_SIM_MEMORY_MB`
  (default 6 GB) per job, mesh limited to `AF_SIM_MAX_MESH_NODES`.

## Upgrades

1. Back up (see [backup-restore.md](backup-restore.md)).
2. `git pull` (or set `AUTOFORGE_VERSION` to the new image tag), then
   `docker compose -f infra/docker-compose.yml --env-file .env up -d --build`. Migrations run before the new API starts.
3. Check `/readyz`, `ps`, and the admin console (jobs, outbox). If anything is wrong, follow [rollback.md](rollback.md).
