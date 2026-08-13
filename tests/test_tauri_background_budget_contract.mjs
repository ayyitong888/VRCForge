import assert from "node:assert/strict";
import fs from "node:fs";

const source = fs.readFileSync("src-tauri/src/commands.rs", "utf8");
const request = source.slice(source.indexOf("pub(crate) struct DesktopAgentMessageRequest"), source.indexOf("pub(crate) struct DesktopComputerUseTurnGrantRequest"));
const handler = source.slice(source.indexOf("pub async fn send_agent_message"), source.indexOf("pub async fn issue_computer_use_turn_grant"));

assert.match(request, /#\[serde\(alias = "max_agentic_turns"\)\]\s+max_agentic_turns: Option<u64>/);
assert.match(handler, /"maxAgenticTurns": request\.max_agentic_turns/);
assert.match(handler, /max_agentic_turns\.is_none\(\)/);
assert.match(handler, /remove\("maxAgenticTurns"\)/);

console.log("tauri background budget contract: passed");
