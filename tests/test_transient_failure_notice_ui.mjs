import assert from "node:assert/strict";
import { readFile } from "node:fs/promises";
import { resolve } from "node:path";

const root = resolve(import.meta.dirname, "..");
const [app, controller, hook, toast, types] = await Promise.all([
  readFile(resolve(root, "src", "App.tsx"), "utf8"),
  readFile(resolve(root, "src", "hooks", "use-chat-run-controller.ts"), "utf8"),
  readFile(resolve(root, "src", "hooks", "use-transient-failure-notice.ts"), "utf8"),
  readFile(resolve(root, "src", "components", "ui", "transient-failure-toast.tsx"), "utf8"),
  readFile(resolve(root, "src", "lib", "api", "types.ts"), "utf8"),
]);

assert.match(hook, /TRANSIENT_FAILURE_NOTICE_MS\s*=\s*3_000/);
assert.match(hook, /window\.setTimeout/);
assert.match(toast, /role="alert"/);
assert.match(toast, /onDismiss/);
assert.match(toast, /<X\b/);
assert.match(toast, /bottom-8/);
assert.match(toast, /left-1\/2/);
assert.match(toast, /transient-failure-toast/);
assert.match(app, /<TransientFailureToast/);
assert.match(app, /attachment\.error/);
assert.match(app, /showTransientFailure\("upload"/);
assert.match(controller, /notifyFailure\?\.\("send"/);
assert.match(controller, /response\.vision\?\.status\s*===\s*"error"/);
assert.match(controller, /notifyFailure\?\.\(\s*"vision"/);
assert.match(controller, /vrcforge_vision_audit_multi/);
assert.match(controller, /providerError/);
assert.match(controller, /managedVisualFailure/);
assert.match(types, /errorType\?: string/);
assert.match(types, /retryable\?: boolean/);
assert.match(types, /retainImages\?: boolean/);

console.log("transient failure notice UI contract: ok");
