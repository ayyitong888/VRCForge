import assert from "node:assert/strict";
import { readFile, mkdtemp, rm } from "node:fs/promises";
import { createServer } from "node:http";
import { tmpdir } from "node:os";
import { extname, resolve, sep } from "node:path";
import { chromium } from "playwright";
import { build } from "vite";

const scratch = await mkdtemp(resolve(tmpdir(), "vrcforge-first-project-"));
const types = { ".html": "text/html", ".js": "text/javascript", ".css": "text/css" };
const server = createServer(async (request, response) => {
  const path = new URL(request.url, "http://localhost").pathname;
  const file = resolve(scratch, `.${path === "/" ? "/index.html" : path}`);
  if (!file.startsWith(scratch + sep)) { response.writeHead(403).end(); return; }
  try {
    const contents = await readFile(file);
    response.writeHead(200, { "Content-Type": types[extname(file)] || "application/octet-stream" });
    response.end(contents);
  } catch {
    response.writeHead(404).end();
  }
});
let browser;
const pageErrors = [];
try {
  await build({ logLevel: "error", build: { outDir: scratch, minify: false } });
  await new Promise((done) => server.listen(0, "127.0.0.1", done));
  const origin = `http://127.0.0.1:${server.address().port}`;
  browser = await chromium.launch({ headless: true, channel: process.env.VRCFORGE_BROWSER_CHANNEL || "msedge" });
  const page = await browser.newPage();
  page.setDefaultTimeout(12_000);
  page.on("pageerror", (error) => pageErrors.push(error.message));
  page.on("console", (message) => {
    if (message.type() === "error" && /Error:|vrcforge:/.test(message.text())) pageErrors.push(message.text());
  });
  const messages = [];
  await page.addInitScript(() => {
    localStorage.setItem("vrcforge-locale", "en-US");
    localStorage.setItem("vrcforge_onboarding_language_gate_completed", "true");
  });
  await page.route("**/*", (route) => {
    const url = new URL(route.request().url());
    if (url.origin === origin) return route.continue();
    if (!url.pathname.startsWith("/api/")) return route.abort();
    const apiConfig = { configured: true, provider: "openai", model: "gpt-4.1", api_type: "auto", credentialFingerprint: "fixture" };
    const health = { ok: true, version: "fixture", components: {}, projects: { projects: [] }, state: {} };
    let json = { ok: true, tasks: [], actions: [], questions: [], runs: [], items: [], sources: [], settings: {}, approvals: [] };
    switch (url.pathname) {
      case "/api/app/bootstrap":
        json = { ok: true, app: { name: "VRCForge" }, health, apiConfig, permission: { pending: [] }, approvals: [] };
        break;
      case "/api/app/chats": json = { ok: true, entries: [], sources: [], chats: [] }; break;
      case "/api/chatgpt/status": json = { accounts: [], pending: false }; break;
      case "/api/app/agent/memory/review": json = { ok: true, runStatus: { state: "idle" }, candidates: [] }; break;
      case "/api/app/provider/test":
        json = { ok: true, capability: "text", status: "passed", message: "Fixture provider", credentialFingerprint: "fixture" };
        break;
      case "/api/app/agent/message": messages.push(route.request().postDataJSON()); break;
    }
    return route.fulfill({ json, headers: { "Access-Control-Allow-Origin": "*", "Access-Control-Allow-Headers": "*" } });
  });
  await page.goto(origin, { waitUntil: "domcontentloaded" });
  const overlay = page.locator("[data-vrcforge-onboarding]");
  await overlay.getByRole("button", { name: "Set up internal AI" }).click();
  // The provider spotlight is separate from the project step under test.
  await page.getByRole("button", { name: "Test connection", exact: true }).evaluate((button) => button.click());
  await overlay.getByRole("button", { name: "Next", exact: true }).click();
  const selectProject = overlay.getByRole("button", { name: "Select Project", exact: true });
  assert.ok(await selectProject.isVisible());
  assert.equal(await overlay.getByRole("button", { name: "Ask for setup help" }).count(), 0);
  await selectProject.click();
  assert.deepEqual(messages, []);
  assert.deepEqual(pageErrors, []);
  console.log("PASS onboarding project step offers project selection without a setup prompt");
} catch (error) {
  if (pageErrors.length) console.error(pageErrors);
  throw error;
} finally {
  await browser?.close();
  server.closeAllConnections();
  await new Promise((done) => server.close(done));
  await rm(scratch, { recursive: true, force: true });
}
