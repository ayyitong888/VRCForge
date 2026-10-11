// Real React/browser regression for removing a disconnected ChatGPT account.
// The before mode recreates the old connected-only button in the in-memory
// bundle; it never edits product source and never contacts the OAuth provider.
import assert from "node:assert/strict";
import { createServer } from "node:http";
import { createRequire } from "node:module";
import { readFileSync } from "node:fs";
import { resolve } from "node:path";
import { build } from "esbuild";

const require = createRequire(import.meta.url);
const { chromium } = require(process.env.VRCFORGE_PLAYWRIGHT_PATH || "playwright");
const beforeFix = process.env.VRCFORGE_OAUTH_TEST_BEFORE === "1";
const sourcePath = resolve("src/features/chatgpt-oauth/settings.tsx");
const fixtureSource = `
  import React from "react";
  import { createRoot } from "react-dom/client";
  import i18n from "i18next";
  import { initReactI18next } from "react-i18next";
  import en from "./src/locales/en-US.json";
  import { ChatGptOAuthSettings } from "./src/features/chatgpt-oauth/settings";
  i18n.use(initReactI18next).init({ lng: "en-US", resources: { "en-US": { translation: en } } });
  function Fixture() {
    return <ChatGptOAuthSettings
      endpoint={location.origin}
      runtimeConnected={true}
      apiProvider="custom"
      apiModel=""
      savingApiConfig={false}
      testingProvider=""
      providerTestMessage=""
      onTestProvider={() => {}}
      onProviderConfigChanged={() => {}}
    />;
  }
  createRoot(document.getElementById("root")).render(<Fixture />);
`;

const plugins = [{
  name: "oauth-logout-pre-fix-source",
  setup(buildApi) {
    buildApi.onLoad({ filter: /settings\.tsx$/ }, async (args) => {
      if (resolve(args.path) !== sourcePath) return undefined;
      let source = readFileSync(args.path, "utf8").replace(/\r\n/g, "\n");
      if (beforeFix) {
        const fixed = "{selectedAccount ? (";
        const legacy = "{selectedAccount?.connected ? (";
        assert.ok(source.includes(fixed), "logout fixture could not locate the disconnected-account condition");
        source = source.replace(fixed, legacy);
      }
      return { contents: source, loader: "tsx", resolveDir: resolve(args.path, "..") };
    });
  },
}];
const bundle = await build({
  stdin: { resolveDir: process.cwd(), loader: "tsx", contents: fixtureSource },
  bundle: true,
  write: false,
  format: "iife",
  jsx: "automatic",
  define: { "process.env.NODE_ENV": '"test"' },
  plugins,
});

let removed = false;
const logoutRequests = [];
const server = createServer((req, res) => {
  if (req.url === "/api/chatgpt/status" && req.method === "GET") {
    const accounts = removed
      ? [{ id: "kept-account", label: "Kept account", email: "", connected: false }]
      : [
        { id: "removed-account", label: "Removed account", email: "", connected: false },
        { id: "kept-account", label: "Kept account", email: "", connected: false },
      ];
    res.setHeader("Content-Type", "application/json");
    res.end(JSON.stringify({
      accounts,
      pending: false,
      error: "",
      activeAccountId: removed ? "" : "removed-account",
      activeModel: "",
    }));
    return;
  }
  if (req.url === "/api/chatgpt/logout" && req.method === "POST") {
    let body = "";
    req.on("data", (chunk) => { body += chunk; });
    req.on("end", () => {
      logoutRequests.push(JSON.parse(body));
      removed = true;
      res.setHeader("Content-Type", "application/json");
      res.end(JSON.stringify({ revoked: true, localCleared: true }));
    });
    return;
  }
  if (req.url === "/fixture.js") {
    res.setHeader("Content-Type", "text/javascript");
    res.end(bundle.outputFiles[0].text);
    return;
  }
  if (req.url === "/") {
    res.setHeader("Content-Type", "text/html");
    res.end("<div id=\"root\"></div><script src=\"/fixture.js\"></script>");
    return;
  }
  res.statusCode = 404;
  res.end();
});
await new Promise((resolveServer) => server.listen(0, "127.0.0.1", resolveServer));

let browser;
try {
  browser = await chromium.launch({ headless: true, channel: process.env.VRCFORGE_BROWSER_CHANNEL || "msedge" });
  const page = await browser.newPage();
  const wait = async (predicate, label) => {
    for (let i = 0; i < 300; i += 1) {
      if (await predicate()) return;
      await new Promise((resolveWait) => setTimeout(resolveWait, 10));
    }
    assert.fail(`timed out: ${label}`);
  };
  await page.goto(`http://127.0.0.1:${server.address().port}/`);
  await wait(async () => (await page.locator("select").inputValue()) === "removed-account", "disconnected account selection");
  assert.equal(await page.getByRole("button", { name: "Remove account" }).count(), beforeFix ? 0 : 1);

  if (beforeFix) {
    await page.getByRole("button", { name: "Remove account" }).click({ timeout: 100 });
  } else {
    await page.getByRole("button", { name: "Remove account" }).click();
    await wait(() => logoutRequests.length === 1, "remove-account request");
    assert.deepEqual(logoutRequests[0], { account_id: "removed-account" });
    await wait(async () => (await page.locator("select").inputValue()) === "kept-account", "remaining account selection");
    assert.equal(await page.locator("option[value=removed-account]").count(), 0);
    assert.equal(await page.locator("option[value=kept-account]").count(), 1);
  }

  console.log(`${beforeFix ? "pre-fix expected failure fixture" : "current source"}: disconnected-account removal contract passed`);
} finally {
  if (browser) await browser.close();
  await new Promise((resolveServer) => server.close(resolveServer));
}
