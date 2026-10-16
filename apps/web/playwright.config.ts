import { defineConfig } from "@playwright/test";

/**
 * End-to-end tests against a running stack.
 *   E2E_BASE_URL   web origin (default http://localhost:3000; the docker compose stack is https://localhost)
 *   E2E_MAILPIT    Mailpit API origin when email goes through SMTP (production-mode stacks); otherwise the dev outbox
 *                  endpoint of a development API is used to read verification and invitation emails.
 */
export default defineConfig({
  testDir: "./e2e",
  timeout: 240_000,
  expect: { timeout: 30_000 },
  fullyParallel: false,
  workers: 1,
  retries: 0,
  reporter: [["list"], ["html", { open: "never" }]],
  use: {
    baseURL: process.env.E2E_BASE_URL ?? "http://localhost:3000",
    ignoreHTTPSErrors: true,
    viewport: { width: 1600, height: 950 },
    acceptDownloads: true,
    trace: "retain-on-failure",
    screenshot: "only-on-failure",
    launchOptions: { args: ["--use-gl=angle", "--use-angle=swiftshader", "--enable-unsafe-swiftshader"] },
  },
});
