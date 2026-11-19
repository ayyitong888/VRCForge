import assert from "node:assert/strict";
import { readFile } from "node:fs/promises";
import path from "node:path";
import test from "node:test";
import { fileURLToPath } from "node:url";

const root = path.resolve(path.dirname(fileURLToPath(import.meta.url)), "..");

// Opening threads and the follow-up selection race run through the real App in
// test_subagent_app_layout_browser.mjs and test_subagent_surface_recovery_browser.mjs.
test("cancel and merge refresh task details through the selection guard", async () => {
  const app = await readFile(path.join(root, "src/App.tsx"), "utf8");
  const operation = (name) => app.match(new RegExp(`  async function ${name}\\([\\s\\S]*?\n  }`))?.[0] ?? "";
  assert.match(operation("refreshSelectedSubAgentTask"), /if \(!isCurrent\(\)\) return;/);
  assert.match(operation("cancelSubAgentTask"), /await refreshSelectedSubAgentTask\(taskId, "if-current"\)/);
  assert.match(operation("mergeSubAgentTask"), /await refreshSelectedSubAgentTask\(payload\.task\.id, "if-current"\)/);
});

test("sub-agent reviews have no desktop notification command", async () => {
  const commands = await readFile(path.join(root, "src-tauri/src/main.rs"), "utf8");
  assert.doesNotMatch(commands, /show_sub_agent_review_notification/);
  assert.match(commands, /show_approval_notification/);
});
