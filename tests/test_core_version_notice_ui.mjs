import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import { createRequire } from "node:module";
import React from "react";
import { renderToStaticMarkup } from "react-dom/server";
import ts from "typescript";

const require = createRequire(import.meta.url);
const source = readFileSync(new URL("../src/components/runtime/core-version-notice.tsx", import.meta.url), "utf8");
const compiled = ts.transpileModule(source, {
  compilerOptions: { module: ts.ModuleKind.CommonJS, jsx: ts.JsxEmit.ReactJSX, target: ts.ScriptTarget.ES2020 },
}).outputText;
for (const locale of ["zh-CN", "zh-TW", "en-US", "ja-JP"]) {
  const messages = JSON.parse(readFileSync(new URL(`../src/locales/${locale}.json`, import.meta.url), "utf8"));
  const exports = {};
  const t = (key, values = {}) => {
    const text = key.split(".").reduce((value, part) => value?.[part], messages);
    assert.equal(typeof text, "string", `missing ${locale}:${key}`);
    return text.replace(/\{\{(\w+)\}\}/g, (_, name) => String(values[name]));
  };
  const healthExports = {};
  const healthSource = readFileSync(new URL("../src/lib/runtime-workspace-view.ts", import.meta.url), "utf8");
  new Function("exports", ts.transpileModule(healthSource, {
    compilerOptions: { module: ts.ModuleKind.CommonJS },
  }).outputText)(healthExports);
  for (const [message, key] of [
    ["VRCForge Unity tools are not ready.", "workspace.toolsNotReady"],
    ["VRCForge Unity tools are ready.", "workspace.toolsReady"],
    ["Selected Unity project is not connected.", "workspace.unityProjectNotOpen"],
  ]) {
    assert.equal(healthExports.localizeRuntimeHealthMessage(t, message), t(key));
  }
  new Function("exports", "require", compiled)(exports, (id) =>
    id === "react-i18next" ? { useTranslation: () => ({ t }) } : require(id));
  const render = (detail) => renderToStaticMarkup(React.createElement(exports.CoreVersionNotice, { detail }));
  assert.equal(render(null), "");
  assert.equal(render({ readiness: { ready: true } }), "");
  assert.equal(render({ readiness: { ready: true, blockerCode: "unity_compile_errors" } }), "");
  for (const [blockerCode, key] of [
    ["unity_compile_errors", "compileErrors"],
    ["unity_core_package_incomplete", "missingCore"],
    ["unity_core_connection_failed", "connection"],
    ["unity_core_unavailable", "connection"],
    ["unity_core_timeout", "connection"],
    ["unity_core_project_busy", "busy"],
    ["unity_project_not_selected", "selectProject"],
    ["unity_editor_reload_dialog", "reloadDialog"],
    ["unity_vrchat_sdk_missing", "vrchatSdkMissing"],
    ["unity_newtonsoft_missing", "newtonsoftMissing"],
    ["future_unknown_error", "unknown"],
  ]) {
    const markup = render({ readiness: { ready: false, blockerCode } });
    assert.ok(markup.includes('role="alert"'), `${locale}:${blockerCode} needs an actionable notice`);
    const expected = renderToStaticMarkup(React.createElement("p", null, t(`coreConnection.${key}`)));
    assert.ok(markup.includes(expected.slice(3, -4)));
  }
  for (const [relation, action, key] of [
    ["core_older", "update_core", "coreOlder"],
    ["app_older", "update_app", "appOlder"],
    ["unknown", "reinstall_pair", "unknown"],
  ]) {
    const markup = render({ readiness: {
      ready: false, blockerCode: "unity_core_version_mismatch",
      versionMismatch: { appVersion: "1.8.8", coreVersion: "1.8.7", relation, action },
    } });
    assert.ok(markup.includes('role="alert"'));
    assert.ok(markup.includes("1.8.8") && markup.includes("1.8.7"));
    const expected = renderToStaticMarkup(React.createElement("p", null, t(`coreVersion.${key}`, { version: "1.8.8" })));
    assert.ok(markup.includes(expected.slice(3, -4)));
  }
}
console.log("Core mismatch notice: four locales and three action directions passed");
