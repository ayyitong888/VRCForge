import type { RuntimeTurnLineage } from "./api/types";
import type { ChatThread } from "./chat-types";

export function runtimeTurnLineage(run: RuntimeTurnLineage): RuntimeTurnLineage {
  return {
    ...(run.parentClientTurnId && { parentClientTurnId: run.parentClientTurnId }),
    ...(run.parentTurnId && { parentTurnId: run.parentTurnId }),
    ...(run.continuationSource && { continuationSource: run.continuationSource }),
  };
}

/** Session aliases may share history. Route a child only to a unique recorded parent. */
export function runtimeParentOwner(chats: ChatThread[], child: RuntimeTurnLineage & { sessionId?: string }): ChatThread | undefined {
  if (!child.sessionId || !(child.parentTurnId || child.parentClientTurnId)) return undefined;
  const owners = chats.filter(chat => chat.sessionId === child.sessionId && chat.items.some(item => {
    if (item.type !== "agent" && item.type !== "streaming") return false;
    const response = item.type === "agent" ? item.response : undefined;
    const session = item.type === "streaming" ? item.sessionId : response?.sessionId || response?.session_id;
    if (session && session !== child.sessionId) return false;
    const turn = item.type === "streaming" ? item.turnId : response?.turnId || response?.turn_id;
    const client = item.type === "streaming" ? item.clientTurnId : response?.clientTurnId;
    if (child.parentClientTurnId && client && child.parentClientTurnId !== client) return false;
    return child.parentTurnId ? turn === child.parentTurnId : client === child.parentClientTurnId;
  }));
  return owners.length === 1 ? owners[0] : undefined;
}
