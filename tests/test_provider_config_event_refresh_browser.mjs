import assert from "node:assert/strict";
import { readFile } from "node:fs/promises";
import { createRequire } from "node:module";
import { createServer } from "node:http";
import { build } from "esbuild";

const require = createRequire(import.meta.url);
const { chromium } = require(process.env.VRCFORGE_PLAYWRIGHT_PATH || "playwright");
const app = await readFile("src/App.tsx", "utf8");
const bridge = await readFile("src-tauri/src/event_bridge.rs", "utf8");
let effect = app.match(/  useEffect\(\(\) => \{\n    if \(!runtimeConnected \|\| !isTauriRuntime\(\)\) \{\n      return;\n    \}\n    let active = true;[\s\S]*?\n  \}, \[runtimeConnected, endpoint, sessionId, activeRuntimeProjectPath, activeProjectPath, workspaceDiffReviewOpen, deliverRuntimeTurnContinuation, applyQuestionRuntimeDelta\]\);/)?.[0];
assert.ok(effect, "exercise the actual desktop event subscription effect");
const allowBody = bridge.match(/pub\(crate\) fn desktop_backend_event_allowed[\s\S]*?matches!\([\s\S]*?event_type,([\s\S]*?)\n    \)/)?.[1];
assert.ok(allowBody, "exercise the actual desktop bridge event allowlist");
let allowed = [...allowBody.matchAll(/"([A-Za-z]+)"/g)].map((match) => match[1]);
if (process.env.VRCFORGE_TEST_LEGACY_CONFIG_SIGNAL === "1") {
  allowed = allowed.filter((name) => name !== "config");
  effect = effect.replace(', "config"', "");
}
const facts = process.env.VRCFORGE_PROVIDER_MISMATCH_FACTS
  ? JSON.parse(await readFile(process.env.VRCFORGE_PROVIDER_MISMATCH_FACTS, "utf8"))
  : { configured: { provider: "deepseek", model: "deepseek-flash", api_type: "responses", thinking_level: "low" } };
const configured = { ...facts.configured, configured: true, apiKeyPresent: true, apiKeyRequired: true };
const original = facts.actualProviderRequests?.[0] || { provider: "chatgpt", model: "gpt-6.1-sol" };
const initial = { provider: original.provider, model: original.model, api_type: "responses", configured: true, apiKeyPresent: true, apiKeyRequired: false };
const bundle = await build({ stdin: { resolveDir: process.cwd(), loader: "tsx", contents: `
import React, { useEffect, useRef, useState } from "react";
import { createRoot } from "react-dom/client";
import i18n from "i18next";
import { initReactI18next } from "react-i18next";
import { useProviderSettings } from "./src/hooks/use-provider-settings";
i18n.use(initReactI18next).init({ lng: "en", resources: { en: { translation: {} } } });
const noop=()=>{}; const listeners=new Map(); const allowed=new Set(${JSON.stringify(allowed)});
const listen=async(name,fn)=>{listeners.set(name,fn);return()=>listeners.delete(name)};
window.deliverBackendEvent=(event)=>{if(allowed.has(event.type))listeners.get("vrcforge-backend-event")?.({payload:{type:event.type}})};
function Fixture(){
 const [apiConfig,setApiConfig]=useState(${JSON.stringify(initial)});
 const runtimeConnected=true, endpoint=location.origin, sessionId="", activeRuntimeProjectPath="",activeProjectPath="",workspaceDiffReviewOpen=false;
 const isTauriRuntime=()=>true;
 const desktopEventBootstrapTimerRef=useRef(null),desktopEventRuntimeTimerRef=useRef(null),desktopEventSubAgentTimerRef=useRef(null);
 const refreshSilently=async()=>{const result=await fetch('/api/app/bootstrap').then(r=>r.json());setApiConfig(result.apiConfig)};
 const refreshRuntimeRuns=noop,loadSubAgents=noop,applyRuntimeDelta=noop,applyQuestionRuntimeDelta=noop,deliverRuntimeTurnContinuation=noop,notifyChatStorageChanged=noop,upsertAgentQuestion=noop,setBackgroundGoalRefreshSignal=noop,setMemoryReviewRefreshSignal=noop;
 ${effect}
 const settings=useProviderSettings({endpoint,runtimeConnected,apiConfig,startRuntime:async()=>endpoint,refresh:refreshSilently,setError:noop});
 window.submit=()=>fetch('/api/app/agent/message',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify(settings.providerSnapshot)}).then(r=>r.json());
 return <output id="provider">{JSON.stringify(settings.providerSnapshot)}</output>;
}
createRoot(document.getElementById("root")).render(<Fixture/>);
` }, bundle: true, write: false, format: "iife", jsx: "automatic", define: { "process.env.NODE_ENV": '"test"' } });
let saved = initial;
let bootstrapReads = 0;
const submissions = [];
const server = createServer(async (req, res) => {
  res.setHeader("Content-Type", "application/json");
  if (req.url === "/api/config" && req.method === "POST") {
    let body = ""; for await (const chunk of req) body += chunk;
    saved = JSON.parse(body); res.end(JSON.stringify({ apiConfig: saved })); return;
  }
  if (req.url === "/api/app/bootstrap") { bootstrapReads++; res.end(JSON.stringify({ ok: true, apiConfig: saved })); return; }
  if (req.url === "/api/models") { res.end(JSON.stringify({ models: [] })); return; }
  if (req.url === "/api/app/agent/message") {
    let body = ""; for await (const chunk of req) body += chunk;
    submissions.push(JSON.parse(body)); res.end(JSON.stringify({ ok: true })); return;
  }
  res.setHeader("Content-Type", "text/html");
  res.end(`<div id="root"></div><script>${bundle.outputFiles[0].text.replaceAll("</script", "<\\/script")}</script>`);
});
await new Promise((resolve) => server.listen(0, "127.0.0.1", resolve));
let browser;
try {
  browser = await chromium.launch({ headless: true, channel: process.env.VRCFORGE_BROWSER_CHANNEL || "msedge" });
  const page = await browser.newPage();
  const origin = `http://127.0.0.1:${server.address().port}`;
  await page.route("**/*", (route) => route.request().url().startsWith(origin) ? route.continue() : route.abort());
  await page.goto(origin);
  await page.waitForFunction(() => document.querySelector("#provider")?.textContent.includes("chatgpt"));
  await page.request.post(`${origin}/api/config`, { data: configured });
  await page.evaluate(() => window.deliverBackendEvent({ type: "config" }));
  await page.waitForTimeout(350);
  await page.evaluate(() => window.submit());
  assert.equal(submissions.at(-1).provider, configured.provider, "a saved config notification must update the provider used for the next normal submission");
  assert.equal(submissions.at(-1).model, configured.model);
  assert.equal(bootstrapReads, 1, "config change refetches the safe bootstrap exactly once");
  assert.equal(saved.api_key, undefined, "the replay uses no credential payload");
  console.log(JSON.stringify({ status: "passed", initialProvider: initial.provider, configuredProvider: configured.provider, submittedProvider: submissions.at(-1).provider, submittedModel: submissions.at(-1).model, bootstrapReads, originalFactsReplayed: Boolean(process.env.VRCFORGE_PROVIDER_MISMATCH_FACTS), providerGeneration: false }));
} finally {
  await browser?.close();
  server.closeAllConnections();
  await new Promise((resolve) => server.close(resolve));
}
