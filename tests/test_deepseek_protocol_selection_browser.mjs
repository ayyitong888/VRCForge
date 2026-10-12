// Real hook regression: protocol negotiation must never choose another model.
import assert from "node:assert/strict";
import { createServer } from "node:http";
import { createRequire } from "node:module";
import { build } from "esbuild";

const require = createRequire(import.meta.url);
const { chromium } = require(process.env.VRCFORGE_PLAYWRIGHT_PATH || "playwright");
const bundle = await build({
  stdin: { resolveDir: process.cwd(), loader: "tsx", contents: `
    import React from "react";
    import {createRoot} from "react-dom/client";
    import i18n from "i18next";
    import {initReactI18next} from "react-i18next";
    import en from "./src/locales/en-US.json";
    import {useProviderSettings} from "./src/hooks/use-provider-settings";
    i18n.use(initReactI18next).init({lng:"en-US",resources:{"en-US":{translation:en}}});
    function Fixture() {
      const query = new URLSearchParams(location.search);
      const hook = useProviderSettings({endpoint:location.origin,runtimeConnected:true,
        apiConfig:{provider:query.get("provider") || "deepseek",model:query.get("model") || "deepseek-v4-pro",
          api_type:query.get("protocol") || "auto",configured:true,apiKeyRequired:true,apiKeyPresent:true},
        startRuntime:async()=>location.origin,refresh:async()=>{},setError:()=>{}});
      return <main>
        <input data-testid="model" value={hook.apiModel} onChange={e=>hook.setApiModel(e.target.value)}/>
        <div data-testid="protocol">{hook.apiType}</div>
        <div data-testid="error">{hook.modelsError}</div>
        <div data-testid="loading">{String(hook.loadingModels)}</div>
        <button data-testid="auto-on" onClick={()=>hook.handleProtocolAutoNegotiationChange(true)}>on</button>
        <button data-testid="auto-off" onClick={()=>hook.handleProtocolAutoNegotiationChange(false)}>off</button>
        <button data-testid="provider-change" onClick={()=>hook.handleProviderChange("gemini")}>provider</button>
        <button data-testid="refresh" onClick={()=>void hook.loadModels()}>refresh</button>
        <button data-testid="test" onClick={()=>void hook.runProviderTest("text")}>test</button>
        <button data-testid="save" onClick={()=>void hook.saveApiProvider()}>save</button>
      </main>;
    }
    createRoot(document.getElementById("root")).render(<Fixture/>);
  ` }, bundle: true, write: false, format: "iife", jsx: "automatic",
  define: { "process.env.NODE_ENV": '"test"' },
});
// Loopback-only synthetic fixture. No credentials/provider traffic; owned by
// this test and closed in finally. It exposes no real application operations.
const requests = [];
const server = createServer((req, res) => {
  if (req.url === "/fixture.js") {
    res.setHeader("Content-Type", "text/javascript");
    res.end(bundle.outputFiles[0].text);
    return;
  }
  if (req.url?.startsWith("/api/")) {
    let body = "";
    req.on("data", chunk => body += chunk);
    req.on("end", () => {
      const request = JSON.parse(body || "{}");
      requests.push({ path: req.url, body: request });
      res.setHeader("Content-Type", "application/json");
      res.end(JSON.stringify(req.url === "/api/models"
        ? { provider: request.provider, selectedModel: "catalog-first", models: [{ id: "catalog-first" }] }
        : { ok: true, capability: "text", status: "ok", message: "fixture", variants: [] }));
    });
    return;
  }
  res.setHeader("Content-Type", "text/html");
  res.end('<div id="root"></div><script src="/fixture.js"></script>');
});
await new Promise(resolve => server.listen(0, "127.0.0.1", resolve));
let browser;
try {
  browser = await chromium.launch({ headless: true, channel: process.env.VRCFORGE_BROWSER_CHANNEL || "msedge" });
  const page = await browser.newPage();
  const base = `http://127.0.0.1:${server.address().port}/`;
  const matrix = [
    ["gemini", "selected-gemini", "generate_content"],
    ["vertexai", "selected-vertex", "generate_content"],
    ["anthropic", "selected-claude", "messages"],
    ["openai", "selected-openai", "responses"],
    ["deepseek", "deepseek-v4-pro", "responses"],
    ["deepseek", "deepseek-v4-flash", "responses"],
    ["deepseek", "deepseek-flash", "responses"],
    ["deepseek", "future-model", "chat_completions"],
    ["custom", "selected-custom", "responses"],
    ["openrouter", "selected-router", "chat_completions"],
    ["ollama", "selected-local", "chat_completions"],
  ];
  for (const [provider, model, defaultProtocol] of matrix) {
    await page.goto(`${base}?provider=${provider}&model=${model}`);
    await page.getByTestId("auto-on").click();
    assert.equal(await page.getByTestId("model").inputValue(), model, "enabling auto must preserve the selected model");
    assert.equal(await page.getByTestId("protocol").innerText(), "auto");
    await page.getByTestId("auto-off").click();
    assert.equal(await page.getByTestId("model").inputValue(), model, "disabling auto must preserve the selected model");
    assert.equal(await page.getByTestId("protocol").innerText(), defaultProtocol, `${provider} must choose its supported default`);
    await Promise.all([
      page.waitForResponse(response => response.url().endsWith("/api/models")),
      page.getByTestId("refresh").click(),
    ]);
    await page.waitForFunction(() => document.querySelector('[data-testid="loading"]').textContent === "false");
    assert.equal(await page.getByTestId("model").inputValue(), model, "catalog refresh must not select its first or recommended model");
  }
  for (const [provider, model, protocol] of [
    ["openai", "selected-openai", "chat_completions"],
    ["deepseek", "deepseek-v4-pro", "messages"],
    ["custom", "selected-custom", "generate_content"],
  ]) {
    await page.goto(`${base}?provider=${provider}&model=${model}&protocol=${protocol}`);
    await page.getByTestId("auto-on").click();
    await page.getByTestId("auto-on").click();
    await page.getByTestId("auto-off").click();
    assert.equal(await page.getByTestId("protocol").innerText(), protocol, "auto off restores the previous valid explicit choice");
    assert.equal(await page.getByTestId("model").inputValue(), model);
  }
  await page.goto(`${base}?provider=deepseek&model=deepseek-v4-pro&protocol=messages`);
  await page.getByTestId("auto-on").click();
  await page.getByTestId("model").fill("future-model");
  await page.getByTestId("auto-off").click();
  assert.equal(await page.getByTestId("protocol").innerText(), "chat_completions", "an invalid previous protocol is never restored for a changed model");
  assert.equal(await page.getByTestId("model").inputValue(), "future-model");
  await page.getByTestId("provider-change").click();
  await page.getByTestId("auto-off").click();
  assert.equal(await page.getByTestId("protocol").innerText(), "generate_content", "provider changes discard previous protocol selection");
  await page.goto(`${base}?model=deepseek-auto`);
  await page.getByTestId("auto-on").click();
  assert.equal(await page.getByTestId("model").inputValue(), "deepseek-auto", "legacy sentinel requires an explicit user selection");
  const before = requests.filter(item => ["/api/config", "/api/app/provider/test"].includes(item.path)).length;
  await page.getByTestId("test").click();
  assert.ok(await page.getByTestId("error").innerText(), "sentinel test must explain model selection is required");
  await page.getByTestId("save").click();
  assert.equal(requests.filter(item => ["/api/config", "/api/app/provider/test"].includes(item.path)).length, before);
  await page.getByTestId("model").fill("deepseek-v4-pro");
  await Promise.all([
    page.waitForResponse(response => response.url().endsWith("/api/app/provider/test")),
    page.getByTestId("test").click(),
  ]);
  await page.waitForFunction(() => document.querySelector('[data-testid="error"]').textContent === "");
  const testRequest = requests.filter(item => item.path === "/api/app/provider/test").at(-1);
  assert.equal(testRequest?.body.model, "deepseek-v4-pro");
  assert.equal(testRequest?.body.api_type, "auto");
  console.log("All-provider protocol-only model selection and explicit protocol restoration: passed");
} finally {
  if (browser) await browser.close();
  await new Promise(resolve => server.close(resolve));
}
