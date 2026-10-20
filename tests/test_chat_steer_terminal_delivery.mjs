import assert from "node:assert/strict";
import { readFile } from "node:fs/promises";
import { resolve } from "node:path";

const controllerPath = process.env.VRCFORGE_RUN_CONTROLLER_SOURCE_PATH
  || resolve(import.meta.dirname, "../src/hooks/use-chat-run-controller.ts");
const source = await readFile(controllerPath, "utf8");
const responseHandling = source.slice(
  source.indexOf("const consumedSteerInputIds = new Set("),
  source.indexOf("if (!background) {", source.indexOf("const consumedSteerInputIds = new Set(")),
);
const responseApplication = source.slice(
  source.indexOf("function applyRuntimeResponseToChat(current: ChatThread): ChatThread {"),
  source.indexOf("let compaction = current.compaction;", source.indexOf("function applyRuntimeResponseToChat(current: ChatThread): ChatThread {")),
);

assert.match(responseHandling, /terminalUnverifiedSteerInputIds = new Set/);
assert.match(responseHandling, /!consumedSteerInputIds\.has\(inputId\)/);
assert.match(responseHandling, /!deferredSteerFollowups\.has\(inputId\)/);
assert.match(responseHandling, /!deferredSteerBackpressure\.has\(inputId\)/);
assert.match(responseHandling, /steerIntentRef\.current\.delete\(inputId\)/, "terminal ambiguity must not leave an in-memory steer intent pending forever");
assert.match(responseApplication, /terminalUnverifiedSteerInputIds\.has\(item\.clientTurnId \|\| \"\"\)[\s\S]*queueStatus: \"delivery_unverified\"/);

console.log("terminal steer delivery contract: ok");
