import assert from "node:assert/strict";
import { readFileSync } from "node:fs";

const header = readFileSync(new URL("../src/components/workspace/workspace-header.tsx", import.meta.url), "utf8");

// A storage conflict is delivered through the shared error channel while a
// startup/health Doctor prompt may also be visible. The error must remain
// visible unless it is exactly the startup text already shown above.
assert.match(
  header,
  /const showErrorBanner\s*=\s*Boolean\(error\.trim\(\)\)\s*&&\s*\(!showDoctorStartupPrompt\s*\|\|\s*error\.trim\(\)\s*!==\s*startupIssue\.trim\(\)\)/,
  "workspace header must preserve a distinct runtime/storage error beside Doctor prompt",
);
assert.match(header, /\{showErrorBanner \? \(/);
assert.match(header, /data-workspace-error/);
assert.match(header, /role="alert"/);

console.log("workspace header concurrent-error visibility contract: ok");
