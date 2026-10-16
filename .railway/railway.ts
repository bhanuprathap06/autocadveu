/**
 * AutoForge on Railway: the whole project in one file (Railway Infrastructure as Code).
 *
 *   cd .railway && npm install && cd ..
 *   railway login && railway init        # or `railway link` to an existing empty project
 *   railway config plan                  # preview
 *   railway config apply                 # create everything
 *   railway domain --service web         # public https://<name>.up.railway.app address
 *
 * Step-by-step guide, costs and troubleshooting: docs/runbooks/railway.md
 *
 * Layout: only `web` is public. The browser talks to `web`, which forwards /api/* (including the realtime
 * WebSocket) to `api` over Railway's private network, so cookies stay first-party. `worker` runs simulations
 * (Gmsh + CalculiX) and sends email. Files live in a Railway Storage Bucket (S3-compatible).
 */
import { bucket, defineRailway, github, group, postgres, preserve, project, redis, ref, service } from "railway/iac";

const REPO = "bhanuprathap06/autocadveu";
// Deploys follow this branch. Switch to "main" once the pull request with the Railway setup is merged.
const BRANCH = "claude/nifty-volta-jeijos";

export default defineRailway(() => {
  const db = postgres("postgres");
  const cache = redis("redis");
  const files = bucket("files");

  // Railway-provided values, resolved by Railway when each service deploys.
  const publicUrl = "https://${{web.RAILWAY_PUBLIC_DOMAIN}}";

  const backendEnv = {
    AF_ENV: "production",
    AF_DATABASE_URL: db.env.DATABASE_URL,
    AF_REDIS_URL: cache.env.REDIS_URL,
    AF_STORAGE_BACKEND: "s3",
    AF_S3_ENDPOINT_URL: ref(files, "ENDPOINT"),
    AF_S3_BUCKET: ref(files, "BUCKET"),
    AF_S3_REGION: ref(files, "REGION"),
    AF_S3_ACCESS_KEY_ID: ref(files, "ACCESS_KEY_ID"),
    AF_S3_SECRET_ACCESS_KEY: ref(files, "SECRET_ACCESS_KEY"),
    AF_S3_ADDRESSING_STYLE: "virtual",
    AF_PUBLIC_BASE_URL: publicUrl,
    AF_ALLOWED_ORIGINS: `["${publicUrl}"]`,
    AF_COOKIE_SECURE: "true",
    AF_TRUST_PROXY_HEADERS: "true",
    AF_KERNEL_WORKERS: "2",
    AF_MAX_UPLOAD_MB: "100",
    // Email: Railway's Hobby plan blocks SMTP, so mail goes through the Resend HTTPS API when a key is set
    // (Settings → Variables on both api and worker). Without it, messages wait in the admin console outbox.
    AF_RESEND_API_KEY: preserve(),
    AF_SMTP_FROM: "AutoForge <onboarding@resend.dev>",
    // Optional AI assistant: set AF_AI_PROVIDER=anthropic and AF_ANTHROPIC_API_KEY in the dashboard.
    AF_AI_PROVIDER: preserve(),
    AF_ANTHROPIC_API_KEY: preserve(),
  };

  const api = service("api", {
    source: github(REPO, { branch: BRANCH, rootDirectory: "services/api" }),
    build: { builder: "DOCKERFILE", watchPatterns: ["/services/api/**"] },
    start: "python -m autoforge.serve",
    preDeploy: "sh -c 'python -m autoforge.cli migrate && python -m autoforge.cli ensure-bucket && python -m autoforge.cli seed'",
    healthcheck: "/readyz",
    healthcheckTimeout: 300,
    env: { ...backendEnv, PORT: "8000" },
  });

  const worker = service("worker", {
    source: github(REPO, { branch: BRANCH, rootDirectory: "services/api" }),
    build: { builder: "DOCKERFILE", watchPatterns: ["/services/api/**"] },
    start: "python -m autoforge.jobs.worker",
    deploy: { restartPolicyType: "ALWAYS" },
    env: backendEnv,
  });

  const web = service("web", {
    source: github(REPO, { branch: BRANCH, rootDirectory: "apps/web" }),
    build: { builder: "DOCKERFILE", watchPatterns: ["/apps/web/**"] },
    healthcheck: "/status",
    env: {
      // Build-time (Dockerfile ARG) and runtime: where Next.js forwards /api/* on the private network.
      AF_API_URL: "http://${{api.RAILWAY_PRIVATE_DOMAIN}}:8000",
      PORT: "3000",
      HOSTNAME: "::",
    },
  });

  return project("autoforge", {
    resources: [
      group("Data", [db, cache, files]),
      group("Backend", [api, worker]),
      web,
    ],
  });
});
