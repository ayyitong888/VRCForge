import assert from "node:assert/strict";
import { createServer } from "node:http";
import { createRequire } from "node:module";
import { readFile } from "node:fs/promises";
import { build } from "esbuild";

const require = createRequire(import.meta.url);
const { chromium } = require(process.env.VRCFORGE_PLAYWRIGHT_PATH || "playwright");
const ts = require("typescript");
const appSource = await readFile("src/App.tsx", "utf8");
const sourceFile = ts.createSourceFile("App.tsx", appSource, ts.ScriptTarget.Latest, true, ts.ScriptKind.TSX);

function appFunction(name) {
  let found = null;
  const visit = (node) => {
    if (ts.isFunctionDeclaration(node) && node.name?.text === name) found = node.getText(sourceFile);
    if (!found) ts.forEachChild(node, visit);
  };
  visit(sourceFile);
  assert.ok(found, `App.tsx must define ${name}`);
  return ts.transpileModule(found, { compilerOptions: { target: ts.ScriptTarget.ES2022 } }).outputText;
}

const functions = ["refresh", "refreshWithRetry", "refreshStartupWithMetrics", "refreshProjectList"]
  .map(appFunction)
  .join("\n");
const bundle = await build({
  stdin: {
    resolveDir: process.cwd(),
    loader: "ts",
    contents: `
      import { fetchBootstrap, refreshProjects } from "./src/lib/api/app";
      const createHarness = (endpoint) => {
        let bootstrap = { health: { projects: null } };
        const bootstrapRequestSequenceRef = { current: 0 };
        const bootstrapForegroundRequestRef = { current: 0 };
        const bootstrapSilentPendingTargetRef = { current: null };
        const projectRefreshInFlightRef = { current: false };
        const errors = [];
        const cachedSnapshots = [];
        const setBootstrap = (value) => { bootstrap = typeof value === "function" ? value(bootstrap) : value; };
        const setError = (value) => { errors.push(typeof value === "function" ? value(errors.at(-1) || "") : value); };
        const setCachedProjectSnapshot = (value) => cachedSnapshots.push(value);
        const cacheProjectSnapshot = (value) => cachedSnapshots.push(value);
        const setStartupIssue = () => {};
        const setLoadingProjects = () => {};
        const runtimeConnected = true;
        const isRuntimeSessionVerificationError = () => false;
        const refresh = ${functions.match(/async function refresh\([\s\S]*?\n\}/)[0]};
        const refreshWithRetry = ${functions.match(/async function refreshWithRetry\([\s\S]*?\n\}/)[0]};
        const refreshStartupWithMetrics = ${functions.match(/async function refreshStartupWithMetrics\([\s\S]*?\n\}/)[0]};
        const refreshProjectList = ${functions.match(/async function refreshProjectList\([\s\S]*?\n\}/)[0]};
        return {
          startup: () => refreshStartupWithMetrics(endpoint, { refreshProjects: true }),
          manualRefresh: () => refreshProjectList(endpoint),
          get bootstrap() { return bootstrap; },
          get errors() { return errors; },
          get cachedSnapshots() { return cachedSnapshots; },
        };
      };
      window.createHarness = createHarness;
    `,
  },
  bundle: true,
  write: false,
  format: "esm",
  platform: "browser",
  target: "es2022",
});

let releaseManual;
const heldManual = new Promise((resolve) => { releaseManual = resolve; });
let bootstrapRequests = 0;
let manualRefreshRequests = 0;
let markManualRequested;
const manualRequested = new Promise((resolve) => { markManualRequested = resolve; });
const server = createServer(async (req, res) => {
  if (req.url === "/fixture.js") {
    res.setHeader("Content-Type", "text/javascript");
    res.end(bundle.outputFiles[0].text);
    return;
  }
  if (req.url?.startsWith("/api/app/bootstrap")) {
    bootstrapRequests += 1;
    const url = new URL(req.url, "http://127.0.0.1");
    assert.equal(url.searchParams.get("refreshProjects"), "true");
    assert.equal(url.searchParams.get("deferAgentCatalog"), "true");
    res.setHeader("Content-Type", "application/json");
    res.end(JSON.stringify({
      health: { projects: { projects: [{ name: "Disk cached project" }], scan: { status: "ready", cached: true } } },
      agentManifest: { skills: [] },
    }));
    return;
  }
  if (req.method === "POST" && req.url === "/api/projects/refresh") {
    manualRefreshRequests += 1;
    markManualRequested();
    await heldManual;
    res.setHeader("Content-Type", "application/json");
    res.end(JSON.stringify({ projects: [{ name: "Fresh manual project" }], scan: { status: "ready", cached: false } }));
    return;
  }
  res.end('<div id="root"></div><script type="module" src="/fixture.js"></script>');
});

await new Promise((resolve) => server.listen(0, "127.0.0.1", resolve));
let browser;
try {
  browser = await chromium.launch({ headless: true, channel: process.env.VRCFORGE_BROWSER_CHANNEL || "msedge" });
  const page = await browser.newPage();
  await page.goto(`http://127.0.0.1:${server.address().port}`);
  await page.evaluate(() => { window.testApi = window.createHarness(location.origin); });
  await Promise.race([
    page.evaluate(() => window.testApi.startup()),
    new Promise((_, reject) => setTimeout(() => reject(new Error("startup waited for manual project refresh")), 3000)),
  ]);
  assert.equal(bootstrapRequests, 1, "startup must fetch the cached bootstrap snapshot");
  assert.equal(manualRefreshRequests, 0, "startup must not invoke the blocking manual refresh route");
  assert.equal(await page.evaluate(() => window.testApi.bootstrap.health.projects.projects[0].name), "Disk cached project");

  await page.evaluate(() => { window.testApi.manualPromise = window.testApi.manualRefresh(); });
  await manualRequested;
  assert.equal(manualRefreshRequests, 1, "explicit refresh must still call the fresh-scan endpoint");
  assert.equal(await page.evaluate(() => window.testApi.bootstrap.health.projects.projects[0].name), "Disk cached project");
  releaseManual();
  await page.evaluate(() => window.testApi.manualPromise);
  assert.equal(await page.evaluate(() => window.testApi.bootstrap.health.projects.projects[0].name), "Fresh manual project");
  console.log("PASS real App startup uses cached bootstrap without waiting for project refresh; explicit refresh still waits for fresh results");
} finally {
  releaseManual();
  await browser?.close();
  server.closeAllConnections();
  await new Promise((resolve) => server.close(resolve));
}
