import assert from "node:assert/strict";
import { readFile } from "node:fs/promises";
import ts from "typescript";

const source = await readFile(new URL("../src/components/skills/vsk-open-dialog.tsx", import.meta.url), "utf8");
const ast = ts.createSourceFile("vsk-open-dialog.tsx", source, ts.ScriptTarget.Latest, true, ts.ScriptKind.TSX);
const importCalls = [];
function visit(node, functions = []) {
  const nested = ts.isFunctionDeclaration(node) ? [...functions, node.name?.text] : functions;
  if (ts.isCallExpression(node) && node.expression.getText(ast).endsWith(".onImport")) {
    importCalls.push(nested);
  }
  ts.forEachChild(node, (child) => visit(child, nested));
}
visit(ast);
assert.equal(importCalls.length, 1, "one and only one import call site");
assert.equal(importCalls[0].at(-1), "confirmImport", "opening and preflight cannot install packages");
assert.match(source, /callbacks\.current\.onPreflight\(path\)/);
assert.match(source, /result\.ok !== true/);
assert.match(source, /entry\.errors\?\.length/);
assert.match(source, /if \(!path \|\| verifiedPath\.current !== path \|\| !preview \|\| error \|\| imported \|\| busyRef\.current\) return/);
assert.match(source, /busyRef\.current = true;[\s\S]*await callbacks\.current\.onImport\(path\)/);
assert.match(source, /onClick=\{\(\) => \{ void confirmImport\(\); \}\}/);
assert.match(source, /onCancel=\{\(event\) => \{ event\.preventDefault\(\); dismiss\(\); \}\}/);
assert.match(source, /querySelector<HTMLButtonElement>\("\[data-vsk-cancel\]"\)\?\.focus/);
assert.match(source, /listen\("vrcforge:vsk-open"/);
assert.match(source, /invoke<string\[\]>\("take_pending_vsk_paths"\)/);
assert.match(source, /if \(!active\) \{ cleanup\(\); return; \}/);
assert.match(source, /if \(!ready \|\| !hasTauriInternals\(\)\) return/);
assert.match(source, /\.slice\(0, 32\)/);
assert.doesNotMatch(source, /dangerouslySetInnerHTML|eval\(|devMode:\s*true|allowDowngrade:\s*true/);
assert.match(source, /vskPreviewIdentity\(preview\)\.name/);
assert.match(source, /vskPreviewIdentity\(preview\)\.version/);
assert.match(source, /manifest\.author/);
assert.match(source, /preview\?\.signer_fingerprint/);
assert.match(source, /governance\.signerTrustStatus/);
assert.match(source, /<details className="mt-3 text-xs">[\s\S]*?<summary[\s\S]*?displayVskPath\(path\)[\s\S]*?signerFingerprint/);
assert.doesNotMatch(source, /<details[^>]*\bopen(?:\s|=|>)/);
assert.match(source, /max-h-\[85vh\][^\n]*overflow-y-auto/);
assert.match(source, /vskOpen\.notProvided/);
assert.doesNotMatch(source, /<dt[^>]*>\{t\("vskOpen\.signerFingerprint"\)\}/);
assert.match(source, /governance\.official === true/);
assert.match(source, /governance\.officialPublisher/);
assert.match(source, /preview\?\.official === true/);
assert.match(source, /preview\.officialPublisher/);
assert.doesNotMatch(source, /manifest\.(?:official|officialPublisher)|ayyitong888/);
assert.match(source, /signatureStatuses\.has\(signatureStatus\.toLowerCase\(\)\)[\s\S]*?vskOpen\.signatureStatus\./);
assert.match(source, /vskOpen\.permission\.\$\{rawPermission\}/);
assert.match(source, /unknownPermission", \{ permission: rawPermission \}/);
const app = await readFile(new URL("../src/App.tsx", import.meta.url), "utf8");
assert.match(app, /<VskOpenDialog ready=\{runtimeConnected\} onPreflight=\{preflightVskPackage\} onImport=\{importVskPackage\}/);
for (const language of ["zh-CN", "zh-TW", "en-US", "ja-JP"]) {
  const locale = JSON.parse(await readFile(new URL(`../src/locales/${language}.json`, import.meta.url), "utf8"));
  for (const key of ["title", "description", "name", "version", "author", "path", "details", "notProvided", "signature", "signerTrust", "signerTrustUnknown", "signerFingerprint", "permissions", "unknownPermission", "checking", "invalid", "confirm", "importing", "imported"]) {
    assert.ok(locale.vskOpen[key], `${language}.${key}`);
  }
  assert.equal(locale.vskOpen.signatureStatus.signed, language === "en-US" ? "Signed" : language === "ja-JP" ? "署名済み" : language === "zh-CN" ? "已签名" : "已簽章");
  for (const status of ["trusted", "untrusted", "revoked", "unsigned_dev"]) assert.ok(locale.vskOpen.signerTrustStatus[status], `${language}.signerTrustStatus.${status}`);
  for (const permission of ["read_project", "read_assets", "read_package", "analyze_logs", "build_index", "unity_scan_scene", "unity_modify_materials", "unity_modify_prefab", "unity_modify_components", "unity_run_validation", "write_project_files", "delete_files", "execute_shell", "run_editor_script", "network_access", "read_env", "write_outside_project"]) {
    assert.ok(locale.vskOpen.permission[permission], `${language}.permission.${permission}`);
  }
  assert.match(locale.vskOpen.unknownPermission, /\{\{permission\}\}/);
}
console.log("VSK file-open confirmation, no-auto-import, failed-check, duplicate-click and lifecycle contracts passed");
