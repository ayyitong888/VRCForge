import assert from "node:assert/strict";
import { readFile } from "node:fs/promises";
import ts from "typescript";

const source = await readFile(new URL("../src/lib/connector-verification.ts", import.meta.url), "utf8");
const code = ts.transpileModule(source, { compilerOptions: { module: ts.ModuleKind.ES2022 } }).outputText;
const { hasRecentConnectorSelfTest } = await import(`data:text/javascript;base64,${Buffer.from(code).toString("base64")}`);
const fresh = {
  gateway: { enabled: true }, contextProjectPath: "project-a",
  clients: { codexApp: { installed: true }, generic: { installed: true } },
  connectorActions: { codexApp: {
    ok: true, action: "install", verificationScope: "installation_self_test", verificationExpiresAt: 120,
    handshake: { ready: true, preflightOk: true, preflightRuntimeOnline: true },
  } },
};
assert.equal(hasRecentConnectorSelfTest(fresh, true, "project-a", 119000), true);
const projected = structuredClone(fresh);
projected.connectorActions.codexApp.handshake = { ready: true };
assert.equal(hasRecentConnectorSelfTest(projected, true, "project-a", 119000), true);
projected.connectorActions.codexApp.handshake.ready = false;
assert.equal(hasRecentConnectorSelfTest(projected, true, "project-a", 119000), false);
assert.equal(hasRecentConnectorSelfTest(fresh, false, "project-a", 119000), false);
assert.equal(hasRecentConnectorSelfTest(fresh, true, "project-b", 119000), false);
assert.equal(hasRecentConnectorSelfTest(fresh, true, "project-a", 120000), false);
for (const change of [
  { clients: { codexApp: { installed: false } } },
  { clients: { codexApp: { installed: true, bindingConflict: true } } },
  { connectorActions: {} },
  { gateway: { enabled: false } },
]) assert.equal(hasRecentConnectorSelfTest({ ...fresh, ...change }, true, "project-a", 119000), false);
const removed = structuredClone(fresh);
removed.connectorActions.codexApp.action = "uninstall";
assert.equal(hasRecentConnectorSelfTest(removed, true, "project-a", 119000), false);
console.log("connector self-test freshness and context: ok");
