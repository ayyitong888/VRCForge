import assert from "node:assert/strict";
import { readFile } from "node:fs/promises";

const desktopCommands = await readFile(
  new URL("../src-tauri/src/commands.rs", import.meta.url),
  "utf8",
);
for (const command of [
  "fetch_avatars",
  "plan_avatar_encryption",
  "request_avatar_encryption_apply",
]) {
  const signature = new RegExp(`pub async fn ${command}\\s*\\(`);
  assert.match(desktopCommands, signature, `${command} must not run synchronously on the desktop thread`);
  const start = desktopCommands.search(signature);
  const nextCommand = desktopCommands.indexOf("#[tauri::command]", start);
  const body = desktopCommands.slice(start, nextCommand < 0 ? undefined : nextCommand);
  assert.match(body, /blocking_backend_json_request\(move \|\|/, `${command} must reuse the bounded backend worker`);
  assert.match(body, /\.await/, `${command} must await its worker without blocking the desktop thread`);
}

console.log("preserved protection commands use the bounded backend worker: ok");
