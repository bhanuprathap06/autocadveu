# Deploy on Railway

AutoForge runs on [Railway](https://railway.com) from one file, [`.railway/railway.ts`](../../.railway/railway.ts)
(Railway Infrastructure as Code). A single `railway config apply` creates:

| Resource | What it does |
|---|---|
| `postgres` | Database (Railway managed) |
| `redis` | Rate limits and realtime events (Railway managed) |
| `files` | Storage Bucket (S3-compatible) for CAD files, exports and simulation results |
| `api` | FastAPI + CAD kernel workers; private only. Runs database migrations and seeding before each deploy |
| `worker` | Simulation jobs (Gmsh + CalculiX) and email delivery |
| `web` | The website; the only public service. Forwards `/api/*` and the realtime WebSocket to `api` privately |

## Cost

Railway has no free tier that fits AutoForge. You need the **Hobby plan** (a monthly fee that includes some usage
credit; usage beyond that is billed). AutoForge keeps roughly 2–3 GB of memory in use across its services, so expect
a usage bill on top of the plan; check the current prices at https://railway.com/pricing and watch
**Usage** in the Railway dashboard during the first days. Simulations use extra CPU and memory while they run.

## One-time setup (about 20 minutes)

You need: a GitHub account with access to this repository, Node.js 22+ and git on your computer.

1. **Create a Railway account** at https://railway.com (sign in with GitHub) and choose the Hobby plan.
2. **Let Railway read the repository:** Railway dashboard → Account Settings → GitHub → configure the Railway app →
   give it access to `bhanuprathap06/autocadveu`.
3. **Install the Railway CLI and log in:**

       npm install -g @railway/cli
       railway login

4. **Get the code and the IaC package:**

       git clone https://github.com/bhanuprathap06/autocadveu.git
       cd autocadveu
       git checkout claude/nifty-volta-jeijos        # until the pull request is merged
       cd .railway && npm install && cd ..

5. **Create an empty Railway project and link this folder to it:**

       railway init --name autoforge

6. **Preview and create everything:**

       railway config plan       # shows "Create service api", "Create bucket files", ...
       railway config apply      # type y to confirm

   Railway now builds the three services from GitHub. The first build takes 10–15 minutes (the API image contains
   the CAD kernel and the CalculiX solver). Follow it in the dashboard.

7. **Give the website a public address:**

       railway domain --service web --port 3000

   This prints something like `https://web-production-1234.up.railway.app`. The API and worker read the address
   from Railway; redeploy them once so they see it:

       railway redeploy --service api
       railway redeploy --service worker

8. **Open the address, register an account.** Then verify it, see below.

## Email (account verification, invitations, password reset)

Railway blocks outgoing SMTP on the Hobby plan, so AutoForge sends email through the [Resend](https://resend.com)
HTTPS API instead.

- **Simplest (no domain needed):** create a free Resend account with the same email address you will use in
  AutoForge, create an API key, and set it on **both** `api` and `worker`: dashboard → service → Variables →
  `AF_RESEND_API_KEY`. Without a verified domain, Resend only delivers to your own Resend account address, which is
  enough for a personal deployment.
- **Email for other people:** verify a domain in Resend and change `AF_SMTP_FROM` in `.railway/railway.ts` to an
  address on that domain, then `railway config apply`.
- **No email at all:** messages stay in the outbox. Verify an account from your computer with:

      railway ssh --service api -- python -m autoforge.cli verify-user you@example.com

  To make yourself an administrator (admin console, job queue, outbox with any pending verification or invitation
  links for other users):

      railway ssh --service api -- python -m autoforge.cli create-admin you@example.com

## Everyday use

- **Updates:** every push to the branch in `BRANCH` (top of `.railway/railway.ts`) redeploys the changed services
  automatically. Switch `BRANCH` to `"main"` once the pull request is merged, then `railway config apply`.
- **Changing settings:** edit `.railway/railway.ts`, run `railway config plan`, then `railway config apply`.
  Variables you add in the dashboard for keys marked `preserve()` (Resend and Anthropic keys, AI provider) are kept.
- **Logs:** `railway logs --service api` (or `worker`, `web`), or the dashboard.
- **Optional AI assistant:** set `AF_AI_PROVIDER=anthropic` and `AF_ANTHROPIC_API_KEY` on `api` and `worker`.
- **Backups:** Railway backs up the Postgres volume according to your plan; the bucket is not versioned. For a full
  application-level backup use `scripts/objects_backup.py` and `pg_dump` as in [backup-restore.md](backup-restore.md).

## Troubleshooting

| Symptom | Check |
|---|---|
| `api` deploy fails in the pre-deploy step | Logs of that deployment: database migrations or the bucket check. The bucket variables come from the `files` bucket; make sure it exists in the same environment. |
| `api` healthcheck times out | `railway logs --service api`; `/readyz` reports which dependency (database, storage, Redis, kernel) is failing. |
| Uploads or exports fail with an S3 error | Bucket → Credentials tab: if it says path-style URLs, change `AF_S3_ADDRESSING_STYLE` to `"path"` in `.railway/railway.ts` and apply. |
| Website loads but every action says "network error" | `web` must have been built after `api` existed (it compiles the private API address). Redeploy `web`. |
| "Verify your email address to continue" | See Email above. |
| Login works but live presence avatars never appear | `AF_ALLOWED_ORIGINS` must match the public address; redeploy `api` after `railway domain`. |

## What has and has not been tested

Tested before this guide was written: the project file typechecks against the Railway SDK (`railway` 3.16.0) and
evaluates to the intended resources and variable references; the full browser acceptance suite (14 steps,
including the realtime WebSocket) passes in a local copy of this topology where only `web` is reachable and `api`
is reached through it; the dual-stack listener and the email and storage options have unit tests.

Not tested: a live `railway config apply`, because it needs your Railway account. Railway-specific behaviour
(bucket URL style, IPv6 private networking, build-time variables) follows Railway's documentation and is covered
by the troubleshooting table above.

Known limitation: rate limiting uses the first `X-Forwarded-For` address. Railway's edge sets it, but this has not
been verified against a client that sends its own header, so treat login rate limits on Railway as best effort.
