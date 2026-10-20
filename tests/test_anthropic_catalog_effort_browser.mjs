import assert from "node:assert/strict";
import { createServer } from "node:http";
import { createRequire } from "node:module";
import { readFileSync } from "node:fs";
import { dirname } from "node:path";
import { build } from "esbuild";

const require = createRequire(import.meta.url);
const { chromium } = require(process.env.VRCFORGE_PLAYWRIGHT_PATH || "playwright");
const bundle = await build({
  plugins: process.env.VRCFORGE_PROVIDER_SETTINGS_BASELINE ? [{
    name: "provider-settings-baseline",
    setup(builder) {
      builder.onLoad({ filter: /provider-settings\.tsx$/ }, ({ path }) => ({
        contents: readFileSync(process.env.VRCFORGE_PROVIDER_SETTINGS_BASELINE, "utf8"),
        loader: "tsx", resolveDir: dirname(path),
      }));
    },
  }] : [],
  stdin: {
    resolveDir: process.cwd(),
    loader: "tsx",
    contents: `
      import React from "react";
      import {createRoot} from "react-dom/client";
      import i18n from "i18next";
      import {initReactI18next} from "react-i18next";
      import en from "./src/locales/en-US.json";
      import {ProviderSetup} from "./src/components/settings/provider-settings";
      i18n.use(initReactI18next).init({lng:"en-US",resources:{"en-US":{translation:en}}});
      createRoot(document.getElementById("root")).render(<ProviderSetup
        provider={(window as any).fixture.provider} apiKey="" baseUrl="" model="claude-fixture" apiType="messages" contextWindow=""
        thinkingLevel={(window as any).fixture.selected} reasoningVariants={{schema:"v1",provider:"anthropic",model:"claude-fixture",transport:"anthropic_messages",defaultKey:"default",variants:[{key:"low",level:"low",displayKey:"provider.reasoning_low",requestMode:"manual_thinking_effort"}]}}
        saving={false} keySaved={true} models={[{id:"claude-fixture",label:"fixture",...(window as any).fixture.metadata}]}
        loadingModels={false} modelsError="" testingProvider="" providerTestMessage="" runtimeConnected={true}
        onLoadModels={()=>{}} onTestProvider={()=>{}} onProviderChange={()=>{}} onApiKeyChange={()=>{}} onBaseUrlChange={()=>{}}
        onModelChange={()=>{}} onProtocolAutoNegotiationChange={()=>{}} onApiTypeChange={()=>{}} onContextWindowChange={()=>{}}
        onThinkingLevelChange={()=>{}} onSubmit={()=>{}} />);`,
  },
  bundle: true,
  write: false,
  format: "iife",
  jsx: "automatic",
  define: { "process.env.NODE_ENV": '"test"' },
});
const server = createServer((req, res) => {
  if (req.url === "/fixture.js") {
    res.setHeader("Content-Type", "text/javascript");
    res.end(bundle.outputFiles[0].text);
  } else if (req.url === "/") {
    res.setHeader("Content-Type", "text/html");
    res.end('<div id="root"></div><script src="/fixture.js"></script>');
  } else {
    res.statusCode = 404;
    res.end();
  }
});
await new Promise((resolve) => server.listen(0, "127.0.0.1", resolve));
let browser;
try {
  browser = await chromium.launch({ headless: true, channel: process.env.VRCFORGE_BROWSER_CHANNEL || "msedge" });
  const page = await browser.newPage();
  for (const fixture of [
    {provider:"anthropic", selected:"xhigh", metadata:{reasoningEfforts:["low","xhigh"],thinkingTypes:["adaptive"]}, options:["default","low","xhigh"], invalid:false},
    {provider:"deepseek", selected:"default", metadata:{reasoningEfforts:["low","high","max"],reasoningDisabledSupported:true}, options:["default","none","low","high","max"], invalid:false},
    {provider:"openrouter", selected:"max", metadata:{reasoningEfforts:["max","high","low","none"]}, options:["default","max","high","low","none"], invalid:false},
    {provider:"openrouter", selected:"none", metadata:{reasoningEfforts:["high","low"]}, options:["default","none","high","low"], invalid:true},
    {provider:"openrouter", selected:"high", metadata:{reasoningEfforts:[]}, options:["default","high"], invalid:true},
    {provider:"deepseek", selected:"low", metadata:{}, options:["default","low"], invalid:false},
  ]) {
  await page.addInitScript((fixture) => { window.fixture=fixture; }, fixture);
  await page.goto(`http://127.0.0.1:${server.address().port}/`);
  const options = await page.locator("select").last().locator("option").evaluateAll((items) => items.map((item) => item.value));
  assert.deepEqual(options, fixture.options, `${fixture.provider}: catalog replaces stale variants, preserving provider order`);
  assert.equal(await page.locator("select").last().inputValue(), fixture.selected, "metadata never auto-selects server default");
  assert.equal(await page.locator('button[type="submit"]').isDisabled(), fixture.invalid, "invalid stored choice requires explicit selection");
  if(fixture.invalid) assert.equal(await page.locator('select').last().locator(`option[value="${fixture.selected}"]`).evaluate((option)=>option.disabled),true, JSON.stringify(fixture));
  }
  console.log("Provider catalog effort selectors (6 cases): ok");
} finally {
  if (browser) await browser.close();
  await new Promise((resolve) => server.close(resolve));
}

