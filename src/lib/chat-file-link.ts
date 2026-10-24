import { invoke } from "@tauri-apps/api/core";
import type { ChatResourceImageContext } from "./api/chat-resource-image";

const PREFIX = "vrcforge-file:";
export function localChatFilePath(value: string): string | null {
  let path = value.trim();
  if (path.startsWith(PREFIX)) { try { path = decodeURIComponent(path.slice(PREFIX.length)); } catch { return null; } }
  else if (/^file:\/\/\//i.test(path)) { try { path = decodeURIComponent(path.slice(8)); } catch { return null; } }
  else { try { path = decodeURIComponent(path); } catch { return null; } }
  path = path.replace(/^\/([a-z]:[\\/])/i, "$1").replace(/:\d+(?::\d+)?$/, "");
  if (!/^[a-z]:[\\/]/i.test(path) || /[\u0000-\u001f]/.test(path) || path.slice(2).includes(":")) return null;
  return path;
}
export function chatFileHref(value: string): string | null {
  const path = localChatFilePath(value);
  return path ? PREFIX + encodeURIComponent(path) : null;
}
export async function openChatFile(context: ChatResourceImageContext, path: string): Promise<void> {
  await invoke("open_chat_file", { request: { path, chatId: context.chatId, projectPath: context.projectPath, sessionId: context.sessionId, clientTurnId: context.clientTurnId } });
}
