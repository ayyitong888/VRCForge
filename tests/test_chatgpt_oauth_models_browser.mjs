// Real React/browser regression for per-account ChatGPT model catalogs.
// All account IDs, models, and responses are synthetic; no OAuth/provider call
// is made by this fixture.
import assert from "node:assert/strict";
import { createServer } from "node:http";
import { createRequire } from "node:module";
import { build } from "esbuild";

const require = createRequire(import.meta.url);
const { chromium } = require(process.env.VRCFORGE_PLAYWRIGHT_PATH || "playwright");
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
      apiProvider="chatgpt"
      apiModel="gpt-6.1-sol"
      savingApiConfig={false}
      testingProvider=""
      providerTestMessage=""
      providerTestPassed={true}
      developerOptionsEnabled={false}
      onTestProvider={() => {}}
      onProviderConfigChanged={() => {}}
    />;
  }
  createRoot(document.getElementById("root")).render(<Fixture />);
`;
const bundle = await build({
  stdin: { resolveDir: process.cwd(), loader: "tsx", contents: fixtureSource },
  bundle: true,
  write: false,
  format: "iife",
  jsx: "automatic",
  define: { "process.env.NODE_ENV": '"test"' },
});

const modelRequests = [];
const activations = [];
let activeModel = "o3";
let activeThinkingLevel = "medium";
const server = createServer((req, res) => {
  if (req.url === "/api/chatgpt/status" && req.method === "GET") {
    res.setHeader("Content-Type", "application/json");
    res.end(JSON.stringify({
      accounts: [
        { id: "account-a", label: "Account A", email: "", connected: true },
        { id: "account-b", label: "Account B", email: "", connected: true },
      ],
      pending: false,
      error: "",
      activeAccountId: "account-a",
      activeModel,
      activeThinkingLevel,
    }));
    return;
  }
  if (req.url === "/api/chatgpt/activate" && req.method === "POST") {
    let body = "";
    req.on("data", (chunk) => { body += chunk; });
    req.on("end", () => {
      const activation = JSON.parse(body);
      activations.push(activation);
      activeModel = activation.model;
      activeThinkingLevel = activation.thinking_level;
      res.setHeader("Content-Type", "application/json");
      res.end(JSON.stringify({ ok: true }));
    });
    return;
  }
  if (req.url === "/api/chatgpt/models" && req.method === "POST") {
    let body = "";
    req.on("data", (chunk) => { body += chunk; });
    req.on("end", () => { modelRequests.push({ accountId: JSON.parse(body).account_id, res }); });
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

function replyNext(accountId, payload, status = 200) {
  const index = modelRequests.findIndex((request) => request.accountId === accountId);
  assert.notEqual(index, -1, `no pending model request for ${accountId}`);
  const [{ res }] = modelRequests.splice(index, 1);
  res.statusCode = status;
  res.setHeader("Content-Type", "application/json");
  res.end(JSON.stringify(payload));
}

const firstCatalog = { models: [
  { id: "o3", name: "o3", supportedReasoningLevels: ["low", "medium", "high"].map((effort) => ({ effort })), defaultReasoningLevel: "medium" },
  { id: "gpt-4o", name: "GPT-4o" },
  { id: "gpt-5.6-sol", name: "GPT-5.6 Sol", supportedReasoningLevels: ["none", "low", "medium", "high", "xhigh", "max"].map((effort) => ({ effort })), defaultReasoningLevel: "low" },
  { id: "gpt-6.1-sol", name: "GPT-6.1 Sol", supportedReasoningLevels: ["low", "medium", "high", "xhigh", "max", "ultra"].map((effort) => ({ effort })), defaultReasoningLevel: "low" },
] };

const wait = async (predicate, label) => {
  for (let i = 0; i < 300; i += 1) {
    if (await predicate()) return;
    await new Promise((resolveWait) => setTimeout(resolveWait, 10));
  }
  assert.fail(`timed out: ${label}`);
};

let browser;
try {
  browser = await chromium.launch({ headless: true, channel: process.env.VRCFORGE_BROWSER_CHANNEL || "msedge" });
  const page = await browser.newPage({ viewport: { width: 520, height: 800 } });
  await page.goto(`http://127.0.0.1:${server.address().port}/`);
  await wait(async () => (await page.locator("select").first().inputValue()) === "account-a", "saved active account selection");
  await wait(() => modelRequests.length === 1, "automatic catalog load");
  assert.equal(modelRequests[0].accountId, "account-a");
  replyNext("account-a", firstCatalog);
  await wait(async () => (await page.locator("[data-chatgpt-model-select]").inputValue()) === "o3", "active model selection");
  await wait(async () => (await page.locator("[data-chatgpt-reasoning-select]").count()) === 1, "model-specific reasoning choices");
  assert.equal(await page.locator("[data-chatgpt-reasoning-select]").inputValue(), "medium", "saved effort is restored when settings open");
  assert.equal(await page.locator("[data-chatgpt-reasoning-select] option[value=default]").count(), 0, "supported model requires a concrete effort");
  await page.locator("[data-chatgpt-reasoning-select]").selectOption("high");
  await page.getByRole("button", { name: "Use this model" }).click();
  await wait(() => activations.length === 1, "OAuth activation saves selected effort");
  assert.deepEqual(activations[0], { account_id: "account-a", model: "o3", thinking_level: "high" });
  await page.reload();
  await wait(() => modelRequests.length === 1, "model catalog after reopening settings");
  replyNext("account-a", firstCatalog);
  await wait(async () => (await page.locator("[data-chatgpt-reasoning-select]").count()) === 1, "persisted reasoning selector after reopen");
  assert.equal(await page.locator("[data-chatgpt-reasoning-select]").inputValue(), "high", "saved effort survives reopening");
  await page.locator("[data-chatgpt-model-select]").selectOption("gpt-5.6-sol");
  await wait(async () => (await page.locator("[data-chatgpt-reasoning-select] option[value=max]").count()) === 1, "GPT-5.6 model-specific maximum effort");
  assert.equal(await page.locator("[data-chatgpt-reasoning-select]").inputValue(), "", "new model requires an explicit effort selection");
  assert.equal(await page.getByRole("button", { name: "Use this model" }).isDisabled(), true, "activation remains disabled until effort is selected");
  await page.locator("[data-chatgpt-reasoning-select]").selectOption("low");
  await page.locator("[data-chatgpt-model-select]").selectOption("gpt-6.1-sol");
  await wait(async () => (await page.locator("[data-chatgpt-reasoning-select] option[value=max]").count()) === 1, "GPT-6.1 Sol reasoning choices");
  assert.equal(await page.locator("[data-chatgpt-reasoning-select] option[value=ultra]").count(), 1, "model-advertised ultra level is preserved");
  assert.equal(await page.locator("[data-chatgpt-reasoning-select]").inputValue(), "", "another model also requires explicit effort selection");
  await page.locator("[data-chatgpt-reasoning-select]").selectOption("low");
  await page.locator("[data-chatgpt-model-select]").selectOption("gpt-4o");
  await wait(async () => (await page.locator("[data-chatgpt-reasoning-select]").count()) === 0, "unsupported model hides reasoning control");
  await page.locator("[data-chatgpt-model-select]").selectOption("o3");
  await wait(async () => (await page.locator("[data-chatgpt-reasoning-select]").count()) === 1, "supported model restores reasoning control");
  assert.equal(await page.locator("[data-chatgpt-reasoning-select] option[value=max]").count(), 0, "o3 choices differ from GPT-5.6 choices");
  assert.equal(await page.locator("[data-chatgpt-model-select] option[value=o3]").count(), 1);
  await new Promise((resolveWait) => setTimeout(resolveWait, 100));
  assert.equal(modelRequests.length, 0, "status refreshes do not storm model requests");

  await page.getByRole("button", { name: "Refresh model list" }).click();
  await wait(() => modelRequests.length === 1, "explicit account A refresh");
  await page.locator("select").first().selectOption("account-b");
  await wait(() => modelRequests.some((request) => request.accountId === "account-b"), "automatic account B catalog load");
  replyNext("account-b", { models: [{ id: "b-model", name: "B Model" }] });
  await wait(async () => (await page.locator("[data-chatgpt-model-select]").inputValue()) === "b-model", "account B model selection");
  assert.equal(await page.locator("[data-chatgpt-model-select] option[value=a-model]").count(), 0, "old account catalog is hidden immediately");
  replyNext("account-a", { models: [{ id: "a-late", name: "Late A Model" }] });
  await new Promise((resolveWait) => setTimeout(resolveWait, 100));
  assert.equal(await page.locator("[data-chatgpt-model-select] option[value=a-late]").count(), 0, "late account A response is rejected");

  await page.getByRole("button", { name: "Refresh model list" }).click();
  await wait(() => modelRequests.length === 1, "explicit account B refresh failure");
  replyNext("account-b", { detail: "synthetic failure" }, 500);
  await wait(async () => (await page.locator("[data-chatgpt-model-select]").inputValue()) === "b-model", "catalog retained after refresh failure");

  await page.getByRole("button", { name: "Refresh model list" }).click();
  await wait(() => modelRequests.length === 1, "explicit account B empty refresh");
  replyNext("account-b", { models: [] });
  await wait(async () => (await page.locator("[data-chatgpt-model-select]").inputValue()) === "", "empty catalog placeholder");
  assert.equal(await page.getByText("No models were returned.", { exact: true }).count(), 1);
  console.log("ChatGPT OAuth model catalog: auto-load, account isolation, late response rejection, and refresh retention passed");
} finally {
  if (browser) await browser.close();
  await new Promise((resolveServer) => server.close(resolveServer));
}
