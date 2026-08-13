import assert from "node:assert/strict";
import fs from "node:fs";

const controller = fs.readFileSync("src/hooks/use-chat-run-controller.ts", "utf8");
const runtime = fs.readFileSync("src/lib/api/agent-runtime.ts", "utf8");

assert.match(runtime, /export const DEFAULT_BACKGROUND_MAX_AGENTIC_TURNS = 25/);
assert.match(runtime, /maxAgenticTurns\?: number/);
assert.match(runtime, /maxAgenticTurns: options\.maxAgenticTurns/);
assert.match(controller, /maxAgenticTurns: background \? DEFAULT_BACKGROUND_MAX_AGENTIC_TURNS : undefined/);
assert.match(controller, /sendAgentMessage\([\s\S]*maxAgenticTurns: background/);

const interactiveSection = controller.slice(controller.indexOf("const response = await sendAgentMessage"), controller.indexOf("const consumedSteerInputIds"));
assert.match(interactiveSection, /maxAgenticTurns: background \? DEFAULT_BACKGROUND_MAX_AGENTIC_TURNS : undefined/);

console.log("background agentic budget contract: passed");
