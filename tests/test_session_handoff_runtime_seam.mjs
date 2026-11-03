import { readFile } from "node:fs/promises";
import assert from "node:assert/strict";
const app = await readFile(new URL("../src/App.tsx", import.meta.url), "utf8");
const slash = app.indexOf('message === "/compact"');
const consume = app.indexOf("sessionHandoff.consume");
assert.ok(slash >= 0 && consume > slash, "slash commands must not consume handoff context");
// The backend owns the handoff framing: the app must prepend the harness
// promptPrefix verbatim instead of building its own payload block.
assert.match(app, /context\?\.promptPrefix/);
assert.doesNotMatch(app, /\[Session handoff context\]/, "no client-side framing literal");
assert.match(app, /nextClientTurnId/);
assert.match(app, /id: nextClientTurnId/);
assert.doesNotMatch(app.slice(consume, consume + 700), /runTurnNow|createGoalFromSlash|startSubAgentTask|approveShell/);
console.log("session handoff runtime seam contract passed");
