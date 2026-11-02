import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import { createRequire } from "node:module";
import ts from "typescript";

const require = createRequire(import.meta.url);
const source = readFileSync(new URL("../src/lib/runtime-authorization-status.ts", import.meta.url), "utf8");
const appSource = readFileSync(new URL("../src/App.tsx", import.meta.url), "utf8");
const providerHookSource = readFileSync(new URL("../src/hooks/use-provider-settings.ts", import.meta.url), "utf8");
const oauthSource = readFileSync(new URL("../src/features/chatgpt-oauth/settings.tsx", import.meta.url), "utf8");
const sidebarSource = readFileSync(new URL("../src/components/runtime/runtime-sidebar.tsx", import.meta.url), "utf8");
const sectionsSource = readFileSync(new URL("../src/components/runtime/project-workbench-sections.tsx", import.meta.url), "utf8");
const compiled = ts.transpileModule(source, {
  compilerOptions: { module: ts.ModuleKind.CommonJS, target: ts.ScriptTarget.ES2020 },
}).outputText;
const exports = {};
new Function("exports", "require", compiled)(exports, require);

const external = { status: "unknown", detail: { gatewayEnabled: true, connected: true } };
const waiting = { status: "unknown", detail: { gatewayEnabled: true, connected: false } };

assert.deepEqual(exports.resolveRuntimeAuthorizationStatus({ runtimeConnected: true, externalAgentComponent: external }), {
  source: "external",
  status: "ok",
  externalState: "connected",
  externalConnected: true,
  internalConfigured: false,
  internalTestPassed: false,
  internalKind: "none",
});
assert.deepEqual(exports.resolveRuntimeAuthorizationStatus({
  runtimeConnected: true,
  externalAgentComponent: { status: "unknown", detail: { gatewayEnabled: true, connected: true } },
  apiConfig: { configured: true, provider: "openai" },
  providerTestPassed: true,
}), {
  source: "external",
  status: "ok",
  externalState: "connected",
  externalConnected: true,
  internalConfigured: true,
  internalTestPassed: true,
  internalKind: "api",
});
const configuredOAuth = exports.resolveRuntimeAuthorizationStatus({
  runtimeConnected: true,
  externalAgentComponent: waiting,
  apiConfig: { configured: true, provider: "chatgpt", apiKeyPresent: true },
});
assert.equal(configuredOAuth.source, "none");
assert.equal(configuredOAuth.status, "warning");
const configuredApi = exports.resolveRuntimeAuthorizationStatus({
  runtimeConnected: true,
  apiConfig: { configured: true, provider: "openai" },
});
assert.equal(configuredApi.status, "warning", "saved configuration stays yellow until a test succeeds");
assert.equal(exports.runtimeAuthorizationLabelKey(configuredApi), "workspace.internalAgent", "an untested internal configuration is labeled as the internal Agent");
const externalWithUntestedInternal = exports.resolveRuntimeAuthorizationStatus({
  runtimeConnected: true,
  externalAgentComponent: external,
  apiConfig: { configured: true, provider: "openai" },
  providerTestPassed: false,
});
assert.equal(externalWithUntestedInternal.source, "external");
assert.equal(exports.runtimeAuthorizationLabelKey(externalWithUntestedInternal), "workspace.externalAgent", "a real external session keeps title priority while internal config is untested");
const testedApi = exports.resolveRuntimeAuthorizationStatus({
  runtimeConnected: true,
  apiConfig: { configured: true, provider: "openai" },
  providerTestPassed: true,
});
assert.equal(testedApi.status, "ok", "an HTTP-200 successful provider test is green");
assert.equal(testedApi.source, "internal");
const bothConnected = {
  runtimeConnected: true,
  externalAgentComponent: { status: "ok", detail: { connected: true, gatewayEnabled: true, lastConnectedAt: 2000 } },
  apiConfig: { configured: true, provider: "openai" },
  providerTestPassed: true,
};
assert.equal(exports.resolveRuntimeAuthorizationStatus({ ...bothConnected, providerConnectedAt: 3000 }).source, "internal", "a later successful internal connection replaces the external source");
assert.equal(exports.resolveRuntimeAuthorizationStatus({ ...bothConnected, providerConnectedAt: 1000 }).source, "external", "a later external connection replaces the internal source");
assert.equal(exports.resolveRuntimeAuthorizationStatus({ ...bothConnected, providerConnectedAt: 3000, providerTestPassed: false }).source, "external", "an invalid internal result cannot win using an old connection time");
assert.equal(exports.resolveRuntimeAuthorizationStatus({
  runtimeConnected: true,
  apiConfig: { configured: true, provider: "anthropic" },
  providerTestPassed: false,
}).status, "warning", "a changed or failed configuration test cannot remain green");
assert.equal(exports.resolveRuntimeAuthorizationStatus({
  runtimeConnected: true,
  externalAgentComponent: waiting,
  apiConfig: { configured: true, provider: "chatgpt", apiKeyPresent: true },
  providerTestPassed: true,
}).source, "internal");
assert.equal(exports.resolveRuntimeAuthorizationStatus({
  runtimeConnected: true,
  apiConfig: { configured: true, provider: "chatgpt", apiKeyPresent: true },
}).internalKind, "oauth");
assert.equal(exports.resolveRuntimeAuthorizationStatus({
  runtimeConnected: true,
  apiConfig: { configured: true, provider: "chatgpt", apiKeyPresent: false },
}).source, "none");
assert.equal(exports.resolveRuntimeAuthorizationStatus({
  runtimeConnected: true,
  apiConfig: { configured: true, provider: "openai" },
  providerTestPassed: false,
}).status, "warning");
assert.equal(exports.runtimeAuthorizationMessageKey(externalWithUntestedInternal, true), "workspace.externalAgentConnected", "an internal test failure cannot replace a connected MCP status");
assert.equal(exports.runtimeAuthorizationMessageKey(testedApi, true), "workspace.internalApiConfigured", "a completed successful test must not be called failed");
assert.equal(exports.runtimeAuthorizationMessageKey(configuredApi, true), "workspace.internalApiTestFailed");
assert.equal(exports.runtimeAuthorizationMessageKey(configuredApi), "workspace.internalApiNeedsTest");
const internalWithExternalError = exports.resolveRuntimeAuthorizationStatus({
  runtimeConnected: true,
  apiConfig: { configured: true, provider: "openai" },
  externalAgentComponent: { status: "error", detail: { connected: false } },
});
assert.equal(internalWithExternalError.status, "warning", "an unrelated external connection error cannot suppress the internal test action");
assert.equal(exports.runtimeAuthorizationLabelKey(internalWithExternalError), "workspace.internalAgent");
assert.equal(exports.runtimeAuthorizationMessageKey(configuredOAuth), "workspace.internalOAuthNeedsTest");
assert.equal(exports.runtimeAuthorizationLabelKey(configuredOAuth), "workspace.internalAgent");
assert.equal(exports.resolveRuntimeAuthorizationStatus({ runtimeConnected: true, externalAgentComponent: waiting }).source, "none");
assert.equal(exports.resolveRuntimeAuthorizationStatus({ runtimeConnected: true }).status, "warning");
assert.equal(exports.resolveRuntimeAuthorizationStatus({
  runtimeConnected: false,
  externalAgentComponent: external,
  apiConfig: { configured: true, provider: "openai" },
}).source, "none");
assert.equal(exports.resolveRuntimeAuthorizationStatus({
  runtimeConnected: true,
  externalAgentComponent: { status: "error", detail: { gatewayEnabled: true, connected: false } },
}).status, "unknown");
assert.equal(exports.runtimeAuthorizationMessageKey(exports.resolveRuntimeAuthorizationStatus({
  runtimeConnected: true,
  externalAgentComponent: waiting,
})), "workspace.externalAgentWaiting");
assert.equal(exports.runtimeAuthorizationMessageKey(exports.resolveRuntimeAuthorizationStatus({
  runtimeConnected: true,
  apiConfig: { configured: true, provider: "openai" },
  providerTestPassed: true,
})), "workspace.internalApiConfigured");

assert.match(appSource, /resolveRuntimeAuthorizationStatus\(\{[\s\S]*apiConfig,[\s\S]*\}\)/);
assert.match(appSource, /providerTestPassed:\s*providerTestPassedForPersistedConfig/);
assert.match(providerHookSource, /providerTestPassedForPersistedConfig:/);
assert.match(providerHookSource, /providerTestEndpoint === endpoint/);
assert.match(providerHookSource, /apiConfig\?\.credentialFingerprint/);
assert.match(providerHookSource, /payload\.credentialFingerprint/);
assert.match(providerHookSource, /apiConfig\?\.credentialFingerprint !== undefined/);
assert.match(providerHookSource, /providerTestGeneration\.current \+= 1/);
assert.match(appSource, /authorizationStatus=\{authorizationStatus\}/);
assert.match(sidebarSource, /runtimeAuthorizationMessageKey\(authorizationStatus, providerTestAttempted\)/);
assert.match(sectionsSource, /runtimeAuthorizationMessageKey\(authorizationStatus, providerTestAttempted\)/);
assert.match(oauthSource, /onTestProvider\("text"\)/, "OAuth card exposes the existing provider test action");
assert.match(oauthSource, /chatgptOAuth\.testConnection/);
for (const locale of ["en-US", "zh-CN", "zh-TW", "ja-JP"]) {
  const messages = JSON.parse(readFileSync(new URL(`../src/locales/${locale}.json`, import.meta.url), "utf8"));
  for (const key of ["internalApiConfigured", "internalOAuthConnected", "internalApiNeedsTest", "internalOAuthNeedsTest", "authorizationBoth", "authorizationNotConfigured", "authorizationDisconnected", "internalAgent", "authorization", "agentConnection"]) {
    assert.equal(typeof messages.workspace[key], "string", `${locale}: missing workspace.${key}`);
  }
  assert.equal(typeof messages.chatgptOAuth.testConnection, "string", `${locale}: missing chatgptOAuth.testConnection`);
}

console.log("runtime authorization status matrix: red/green contract passed");
