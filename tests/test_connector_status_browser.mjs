import assert from "node:assert/strict";
import { createServer } from "node:http";
import { build } from "esbuild";
import { chromium } from "playwright";

const fixture = [
  'import React, { useState } from "react";',
  'import { createRoot } from "react-dom/client";',
  'import { initializeI18n } from "./src/i18n";',
  'import { ExternalAgentConnectorsPanel } from "./src/components/settings/external-agent-connectors-panel";',
  'window.copies = [];',
  'function Fixture() {',
  '  const [status, setStatus] = useState(null);',
  '  window.setConnectorStatus = setStatus;',
  '  return <ExternalAgentConnectorsPanel status={status} loading={false} runtimeConnected={true}',
  '    message="" selectedProjectPath="project-a" onCopy={(text) => window.copies.push(text)} />;',
  '}',
  'initializeI18n().then(() => createRoot(document.getElementById("root")).render(<Fixture />));',
].join("\n");
const bundle = await build({
  stdin: { resolveDir: process.cwd(), loader: "tsx", contents: fixture },
  bundle: true, write: false, format: "iife", jsx: "automatic",
});
const server = createServer((request, response) => {
  if (request.url === "/fixture.js") {
    response.setHeader("Content-Type", "text/javascript");
    return response.end(bundle.outputFiles[0].text);
  }
  response.setHeader("Content-Type", "text/html");
  response.end('<div id="root"></div><script src="/fixture.js"></script>');
});
await new Promise((resolve) => server.listen(0, "127.0.0.1", resolve));
let browser;
try {
  browser = await chromium.launch({ headless: true, channel: "msedge" });
  const page = await browser.newPage();
  await page.goto("http://127.0.0.1:" + server.address().port);
  await page.waitForFunction(() => Boolean(window.setConnectorStatus));
  const now = Date.now() / 1000;
  const config = '{"mcpServers":{"fixture":{}}}';
  const setup = {
    ok: true, contextProjectPath: "project-a",
    gateway: { enabled: false, allowWriteRequests: true },
    clients: { deepseekHarness: { installed: true } },
    clientConfigs: { generic: { text: config } },
    connectorActions: { deepseekHarness: {
      action: "install", ok: true, verificationScope: "installation_self_test",
      verificationExpiresAt: now + 60, handshake: { ready: true },
    } },
  };
  const show = (next) => page.evaluate((value) => window.setConnectorStatus(value), next);
  const summary = page.locator("[data-connector-gateway]");
  const verification = page.locator("[data-connector-verification]");
  await show(setup);
  assert.equal(await summary.getAttribute("data-connector-gateway", { timeout: 1500 }), "disabled");
  assert.equal(await summary.getAttribute("data-connector-config"), "installed");
  assert.equal(await verification.getAttribute("data-connector-verification"), "disabled");
  const details = page.locator("[data-connector-agent-instructions] details");
  assert.equal(await details.getAttribute("open"), null);
  await page.getByRole("button", { name: "Copy general connection instructions" }).click();
  assert.ok((await page.evaluate(() => window.copies[0])).endsWith(config));
  await details.locator("summary").click();
  assert.equal(await details.locator("pre").textContent(), config);

  await show({ ...setup, gateway: { ...setup.gateway, enabled: true, pendingApprovalCount: 2 } });
  assert.equal(await summary.getAttribute("data-connector-gateway"), "enabled");
  assert.equal(await verification.getAttribute("data-connector-verification"), "bridge-checked");
  assert.match(await summary.innerText(), /2 operations need confirmation/);
  await show({ ...setup, gateway: { enabled: true }, connectorActions: {} });
  assert.equal(await verification.getAttribute("data-connector-verification"), "unchecked");
  await show({ ...setup, gateway: { enabled: true }, connectorActions: { deepseekHarness: {
    ...setup.connectorActions.deepseekHarness, verificationExpiresAt: now - 1,
  } } });
  assert.equal(await verification.getAttribute("data-connector-verification"), "expired");
  console.log("PASS: gateway, configuration, verification, pending approvals and expandable copyable config.");
} finally {
  await browser?.close();
  server.closeAllConnections();
  await new Promise((resolve) => server.close(resolve));
}
