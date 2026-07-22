import assert from "node:assert/strict";
import fs from "node:fs";
import path from "node:path";
import ts from "typescript";
import { fileURLToPath, pathToFileURL } from "node:url";

const root = path.resolve(path.dirname(fileURLToPath(import.meta.url)), "..");
const sourcePath = path.join(root, "src", "lib", "api", "http.ts");
let source = fs.readFileSync(sourcePath, "utf8");
source = source
  .replace(
    'import { invoke } from "@tauri-apps/api/core";',
    "const invoke = (...args) => globalThis.__memoryReviewInvoke(...args);",
  )
  .replace(
    'import { isDesktopLoopbackApiUrl } from "../desktop-routing";',
    "const isDesktopLoopbackApiUrl = () => false;",
  );
const compiled = ts.transpileModule(source, {
  compilerOptions: {
    module: ts.ModuleKind.ESNext,
    target: ts.ScriptTarget.ES2022,
  },
  fileName: sourcePath,
}).outputText;
const moduleUrl = `data:text/javascript;base64,${Buffer.from(compiled).toString("base64")}`;
const http = await import(moduleUrl);

const rejected = async (value, signal) => {
  globalThis.__memoryReviewInvoke = () => Promise.reject(value);
  try {
    await http.invokeTauriWithAbort("memory_review_test", {}, signal);
    assert.fail("invoke should reject");
  } catch (error) {
    return error;
  }
};

const conflict = await rejected({
  errorType: "backendJsonError",
  status: 409,
  detail: "stale revision",
});
assert.ok(conflict instanceof http.ApiError);
assert.equal(conflict.status, 409);
assert.equal(conflict.message, "stale revision");
assert.equal(conflict.detail, "stale revision");

const missing = await rejected(
  JSON.stringify({ errorType: "backendJsonError", status: 404, detail: "candidate not found" }),
  new AbortController().signal,
);
assert.ok(missing instanceof http.ApiError);
assert.equal(missing.status, 404);
assert.equal(missing.detail, "candidate not found");

const transport = await rejected({
  errorType: "backendJsonError",
  status: 0,
  detail: "runtime unavailable",
});
assert.ok(transport instanceof http.ApiError);
assert.equal(transport.status, 0);
assert.equal(transport.message, "runtime unavailable");

const legacyError = "legacy-command-error";
assert.equal(await rejected(legacyError), legacyError);

let invoked = false;
globalThis.__memoryReviewInvoke = () => {
  invoked = true;
  return Promise.resolve({});
};
const controller = new AbortController();
controller.abort();
const cancelled = await (async () => {
  try {
    await http.invokeTauriWithAbort("memory_review_test", {}, controller.signal);
    assert.fail("aborted invoke should reject");
  } catch (error) {
    return error;
  }
})();
assert.ok(cancelled instanceof http.ApiError);
assert.equal(cancelled.status, 0);
assert.equal(invoked, false);

console.log("memory review IPC error contract: ok");
