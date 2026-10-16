import { expect, test, type Page } from "@playwright/test";
import { api, collectErrors, latestEmail, linkFrom, newUserContext, registerAndVerify } from "./helpers";

/**
 * Acceptance journey against a real stack (OCCT kernel workers, CalculiX worker, Postgres, Redis, object storage):
 * register -> template project -> sketch -> parameter change -> save/reopen -> export -> assembly -> simulation ->
 * comparison -> private room invitation -> reviewer is read-only -> outsider is denied -> revision restore.
 */
test.describe.configure({ mode: "serial" });

const stamp = Date.now();
const owner = { name: "Ada Engineer", email: `owner-${stamp}@example.com` };
const reviewer = { name: "Rita Reviewer", email: `reviewer-${stamp}@example.com` };
const outsider = { name: "Oscar Outsider", email: `outsider-${stamp}@example.com` };

let page: Page;
let errors: string[];
let projectPath = "";
let docPath = "";
let originalWidth = "";

test.beforeAll(async ({ browser }) => {
  const ctx = await browser.newContext({ ignoreHTTPSErrors: true, baseURL: process.env.E2E_BASE_URL ?? "http://localhost:3000",
    viewport: { width: 1600, height: 950 }, acceptDownloads: true });
  page = await ctx.newPage();
  errors = collectErrors(page);
});

test.afterAll(() => {
  expect(errors, "browser console/page errors").toEqual([]);
});

test("register and verify email", async () => {
  await registerAndVerify(page, owner.name, owner.email);
});

test("create a project from a parametric template; kernel builds the model", async () => {
  await page.goto("/app");
  await page.getByRole("button", { name: "New project" }).first().click();
  await page.getByLabel("Project name").fill("Bracket study");
  await page.getByRole("dialog").getByRole("button", { name: /L mounting bracket/ }).click();
  await page.getByRole("button", { name: "Create project" }).click();
  await page.waitForURL(/\/app\/workbench\//);
  docPath = new URL(page.url()).pathname;
  await expect(page.getByText(/Model OK/)).toBeVisible({ timeout: 90_000 });
  await expect(page.getByRole("tree", { name: "Feature history" })).toContainText("Bracket body");
});

test("realtime channel connects through the public web origin", async () => {
  // Hosted layouts (e.g. Railway) expose only the web server, which must forward the WebSocket upgrade to the API.
  const doc = await (await page.request.get(`/api/v1/documents/${docPath.split("/").pop()}`)).json();
  const hello = await page.evaluate(async (projectId) => {
    const url = `${location.protocol === "https:" ? "wss" : "ws"}://${location.host}/api/v1/ws?project_id=${projectId}`;
    return await new Promise<Record<string, unknown>>((resolve, reject) => {
      const ws = new WebSocket(url);
      const timer = setTimeout(() => reject(new Error("no hello within 10 s")), 10_000);
      ws.onmessage = (ev) => { clearTimeout(timer); ws.close(); resolve(JSON.parse(ev.data)); };
      ws.onerror = () => { clearTimeout(timer); reject(new Error("websocket error")); };
    });
  }, doc.document.project_id);
  expect(hello.type).toBe("hello");
  expect((hello.you as { name: string }).name).toBe(owner.name);
});

test("pick a face and measure it with the exact B-rep", async () => {
  const canvas = page.locator("section[aria-label='3D viewport'] canvas");
  const box = (await canvas.boundingBox())!;
  await page.mouse.click(box.x + box.width * 0.55, box.y + box.height * 0.62);
  await expect(page.getByText("Persistent name")).toBeVisible();
  await page.getByRole("button", { name: /^Measure/ }).click();
  await expect(page.getByText("Measurement (exact B-rep)")).toBeVisible();
});

test("sketch editor solves constraints and reports degrees of freedom", async () => {
  await page.getByRole("tree", { name: "Feature history" }).getByText("L profile").dblclick();
  const editor = page.getByRole("application", { name: /Sketch editor/ });
  await expect(editor).toBeVisible();
  await expect(page.getByText("Fully constrained").first()).toBeVisible();
  await page.getByRole("button", { name: /^Circle/ }).click();
  const b = (await editor.boundingBox())!;
  await page.mouse.click(b.x + b.width * 0.7, b.y + b.height * 0.3);
  await page.mouse.click(b.x + b.width * 0.72, b.y + b.height * 0.3);
  await expect(page.getByText("Under-constrained").first()).toBeVisible();
  await page.keyboard.press("Control+z");
  await expect(page.getByText("Fully constrained").first()).toBeVisible();
  await page.getByRole("button", { name: "Finish sketch" }).click();
});

test("parameter change regenerates, autosaves and survives a reload", async () => {
  await page.getByRole("tab", { name: /Parameters/ }).click();
  const width = page.getByLabel(/Expression for /).first();
  originalWidth = await width.inputValue();
  await width.fill("135");
  await width.press("Enter");
  await expect(page.getByText(/Saved revision \d+/).first()).toBeVisible({ timeout: 60_000 });
  await expect(page.getByText("All changes saved")).toBeVisible({ timeout: 30_000 });
  await page.reload();
  await expect(page.getByText(/Model OK/)).toBeVisible({ timeout: 90_000 });
  await page.getByRole("tab", { name: /Parameters/ }).click();
  await expect(page.getByLabel(/Expression for /).first()).toHaveValue("135");
});

test("export STEP is verified by re-import", async () => {
  const [dl] = await Promise.all([
    page.waitForEvent("download", { timeout: 90_000 }),
    (async () => { await page.getByRole("button", { name: /Export/ }).click(); await page.getByRole("menuitem", { name: /STEP/ }).click(); })(),
  ]);
  expect(dl.suggestedFilename()).toMatch(/\.step$/);
  await expect(page.getByText(/re-import valid/)).toBeVisible();
});

test("assembly: insert the part and a catalog component, offset without overlap", async () => {
  await page.getByRole("link", { name: "Back to project" }).click();
  await page.waitForURL(/\/app\/projects\//);
  projectPath = new URL(page.url()).pathname;
  await page.getByRole("button", { name: "New assembly" }).click();
  await page.getByRole("dialog").getByRole("button", { name: "Create" }).click();
  await page.waitForURL(/\/app\/workbench\//);
  await page.getByRole("button", { name: /Insert component/ }).first().click();
  await page.getByRole("dialog").getByRole("button", { name: "Insert" }).first().click();
  await expect(page.getByRole("dialog")).toBeHidden();
  await page.waitForTimeout(1500);
  await page.getByRole("button", { name: /Insert component/ }).first().click();
  await page.getByRole("tab", { name: "Component library" }).click();
  await page.getByRole("dialog").getByRole("button", { name: "Insert" }).first().click();
  await expect(page.getByText("Components (2)")).toBeVisible();
  await expect(page.getByText(/All changes saved/)).toBeVisible({ timeout: 30_000 });
  const asm = await api(page, "GET", `/api/v1/documents/${page.url().split("/").pop()}`);
  const xs = asm.revision.content.instances.map((i: any) => i.placement.position[0]);
  expect(xs[1]).toBeGreaterThan(xs[0]);
});

test("static simulation runs in CalculiX and reports balanced reactions", async () => {
  const docId = docPath.split("/").pop()!;
  const pid = projectPath.split("/").pop()!;
  const doc = await api(page, "GET", `/api/v1/documents/${docId}`);
  const model = await api(page, "POST", `/api/v1/documents/${docId}/recompute`, { revision: doc.revision.number });
  const ext = doc.revision.content.features.find((f: any) => f.type === "extrude");
  const body = model.bodies[0];
  const r = await api(page, "POST", `/api/v1/projects/${pid}/simulations`, {
    document_id: docId, name: "Bracket static", idempotency_key: `e2e-${stamp}`,
    setup: { analysis: "static", body: body.name,
      material: { name: "Steel (generic)", youngs_modulus_mpa: 200000, poisson_ratio: 0.3, density_kg_m3: 7850, yield_strength_mpa: 250, source: "generic typical values for test" },
      supports: [{ type: "fixed", faces: [`${ext.id}:start`] }], loads: [{ type: "force", faces: [`${ext.id}:end`], vector_n: [0, 0, -500] }] },
  });
  await page.goto(`/app/simulations/${r.simulation.id}`);
  await expect(page.getByText("Max von Mises")).toBeVisible({ timeout: 300_000 });
  await expect(page.getByText(/balanced/)).toBeVisible();
  await expect(page.getByText(/C3D10/)).toBeVisible();
});

test("component comparison flags missing data instead of inventing it", async () => {
  await page.goto("/app/catalog");
  const boxes = page.getByRole("checkbox", { name: /Select .* for comparison/ });
  await boxes.nth(0).check();
  await boxes.nth(1).check();
  await page.getByRole("button", { name: /Compare 2/ }).click();
  await expect(page.getByText("Pairwise assessment")).toBeVisible();
  await expect(page.getByText(/missing/).first()).toBeVisible();
});

let inviteLink = "";
test("owner shares the project in a private room and invites a reviewer", async () => {
  await page.goto("/app/rooms");
  await page.getByRole("button", { name: "New room" }).click();
  await page.getByLabel("Room name").fill("Supplier review");
  await page.getByRole("button", { name: "Create room" }).click();
  await page.getByText("Supplier review").click();
  await page.getByLabel("Project to share").selectOption({ label: "Bracket study" });
  await page.getByRole("button", { name: "Share" }).click();
  await page.getByRole("tab", { name: "Invitations" }).click();
  await page.getByLabel("Email").fill(reviewer.email);
  await page.getByLabel("Role").selectOption("reviewer");
  await page.getByRole("button", { name: /Send invitation/ }).click();
  await expect(page.getByRole("cell", { name: reviewer.email })).toBeVisible();
  inviteLink = linkFrom(await latestEmail(page.request, reviewer.email, /invited you/), "/invite/");
});

test("reviewer accepts, sees the model read-only and cannot save", async () => {
  const ctx = await newUserContext(page.context());
  const p2 = await ctx.newPage();
  const errs = collectErrors(p2);
  await registerAndVerify(p2, reviewer.name, reviewer.email);
  await p2.goto(inviteLink);
  await p2.getByRole("button", { name: "Accept invitation" }).click();
  await p2.waitForURL(/\/app\/rooms\//);
  await p2.getByRole("link", { name: "Bracket study" }).click();
  await expect(p2.getByText("your role:")).toContainText("reviewer");
  await p2.goto(docPath);
  await expect(p2.getByText("Read-only")).toBeVisible({ timeout: 60_000 });
  const csrf = (await ctx.cookies()).find((c) => c.name === "af_csrf")?.value ?? "";
  const res = await p2.request.post(`/api/v1/documents/${docPath.split("/").pop()}/revisions`, {
    data: { base_revision: 1, content: {}, op_id: `deny-${stamp}` }, headers: { "x-csrf-token": csrf } });
  expect([403, 404]).toContain(res.status());
  expect(errs).toEqual([]);
  await ctx.close();
});

test("an outsider cannot see the project or its documents", async () => {
  const ctx = await newUserContext(page.context());
  const p3 = await ctx.newPage();
  await registerAndVerify(p3, outsider.name, outsider.email);
  for (const path of [`/api/v1/projects/${projectPath.split("/").pop()}`, `/api/v1/documents/${docPath.split("/").pop()}`]) {
    const r = await p3.request.get(path);
    expect(r.status(), path).toBe(404);
  }
  await p3.goto(projectPath);
  await expect(p3.getByRole("alert").filter({ hasText: /not found/i })).toBeVisible();
  await ctx.close();
});

test("restore an earlier revision as a new revision", async () => {
  await page.goto(docPath);
  await expect(page.getByText(/Model OK/)).toBeVisible({ timeout: 90_000 });
  await page.getByRole("button", { name: "History" }).click();
  await page.getByRole("button", { name: "Restore revision 1" }).click();
  await expect(page.getByText(/Restored revision 1/)).toBeVisible();
  await page.getByRole("tab", { name: /Parameters/ }).click();
  await expect(page.getByLabel(/Expression for /).first()).toHaveValue(originalWidth);
  const revs = await api(page, "GET", `/api/v1/documents/${docPath.split("/").pop()}/revisions`);
  expect(revs.items[0].restored_from).toBe(1);
});
