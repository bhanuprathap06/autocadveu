import { expect, type APIRequestContext, type BrowserContext, type Page } from "@playwright/test";

export const PASSWORD = "Correct-Horse-Battery-9";

/** Call the API with the session's CSRF token (as the web client does). */
export async function api<T = any>(page: Page, method: string, path: string, data?: unknown): Promise<T> {
  const csrf = (await page.context().cookies()).find((c) => c.name === "af_csrf")?.value ?? "";
  const r = await page.request.fetch(path, { method, data, headers: { "x-csrf-token": csrf } });
  if (!r.ok()) throw new Error(`${method} ${path} -> ${r.status()} ${(await r.text()).slice(0, 300)}`);
  return r.json() as Promise<T>;
}

/** Latest email body for an address: Mailpit when configured, otherwise the development outbox endpoint. */
export async function latestEmail(request: APIRequestContext, to: string, subject: RegExp): Promise<string> {
  const mailpit = process.env.E2E_MAILPIT;
  for (let i = 0; i < 40; i++) {
    if (mailpit) {
      const list = await (await request.get(`${mailpit}/api/v1/search?query=${encodeURIComponent(`to:${to}`)}`)).json();
      const m = (list.messages ?? []).find((x: any) => subject.test(x.Subject));
      if (m) return (await (await request.get(`${mailpit}/api/v1/message/${m.ID}`)).json()).Text as string;
    } else {
      const r = await request.get(`/api/v1/dev/outbox?to=${encodeURIComponent(to)}`);
      if (r.ok()) {
        const m = ((await r.json()).items ?? []).find((x: any) => subject.test(x.subject));
        if (m) return m.body as string;
      }
    }
    await new Promise((res) => setTimeout(res, 500));
  }
  throw new Error(`No email matching ${subject} for ${to}`);
}

export function linkFrom(body: string, path: string): string {
  const m = body.match(new RegExp(`https?://[^\\s]+${path}[^\\s]*`));
  if (!m) throw new Error(`No ${path} link in email`);
  return new URL(m[0]).pathname + new URL(m[0]).search;
}

/** Register through the UI and verify the address with the emailed link. */
export async function registerAndVerify(page: Page, name: string, email: string) {
  await page.goto("/register");
  await page.getByLabel("Name", { exact: true }).fill(name);
  await page.getByLabel("Work email").fill(email);
  await page.getByLabel("Password", { exact: true }).fill(PASSWORD);
  await page.getByRole("button", { name: /create account/i }).click();
  await page.waitForURL(/\/app/);
  const body = await latestEmail(page.request, email, /verify/i);
  await page.goto(linkFrom(body, "/verify-email"));
  await expect(page.getByText(/verified/i).first()).toBeVisible();
}

export function collectErrors(page: Page): string[] {
  const errors: string[] = [];
  page.on("pageerror", (e) => errors.push(`pageerror: ${e.message}`));
  page.on("console", (m) => { if (m.type() === "error" && !/401 \(Unauthorized\)|404 \(Not Found\)|403 \(Forbidden\)/.test(m.text())) errors.push(`console: ${m.text()}`); });
  return errors;
}

export async function newUserContext(context: BrowserContext) {
  const ctx = await context.browser()!.newContext({ ignoreHTTPSErrors: true, baseURL: process.env.E2E_BASE_URL ?? "http://localhost:3000", viewport: { width: 1400, height: 900 } });
  return ctx;
}
