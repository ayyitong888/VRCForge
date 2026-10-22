import type { ChatThread, ProjectType } from "./chat-types";

export const CHAT_SIDEBAR_CACHE_KEY = "vrcforge_chat_sidebar_cache";

export type ChatSidebarCacheEntry = Pick<
  ChatThread,
  "id" | "sessionId" | "title" | "projectPath" | "projectType" | "pinned" | "archived" | "createdAt" | "updatedAt" | "lastViewedAt"
>;

function toEntry(value: unknown): ChatSidebarCacheEntry | null {
  if (!value || typeof value !== "object") return null;
  const candidate = value as Partial<ChatSidebarCacheEntry>;
  if (typeof candidate.id !== "string" || !candidate.id.trim()) return null;
  const projectType: ProjectType = candidate.projectType === "general" ? "general" : "unity";
  return {
    id: candidate.id,
    sessionId: typeof candidate.sessionId === "string" ? candidate.sessionId : "",
    title: typeof candidate.title === "string" ? candidate.title : "",
    projectPath: typeof candidate.projectPath === "string" ? candidate.projectPath : "",
    projectType,
    pinned: candidate.pinned === true,
    archived: candidate.archived === true,
    createdAt: typeof candidate.createdAt === "string" ? candidate.createdAt : "",
    updatedAt: typeof candidate.updatedAt === "string" ? candidate.updatedAt : "",
    lastViewedAt: typeof candidate.lastViewedAt === "string" ? candidate.lastViewedAt : undefined,
  };
}

export function loadChatSidebarCache(): ChatSidebarCacheEntry[] {
  try {
    const raw = window.localStorage.getItem(CHAT_SIDEBAR_CACHE_KEY);
    const parsed: unknown = raw ? JSON.parse(raw) : [];
    return Array.isArray(parsed) ? parsed.map(toEntry).filter((entry): entry is ChatSidebarCacheEntry => Boolean(entry)) : [];
  } catch {
    return [];
  }
}

export function saveChatSidebarCache(chats: ReadonlyArray<ChatThread>): void {
  try {
    const entries: ChatSidebarCacheEntry[] = chats.map(({ id, sessionId, title, projectPath, projectType, pinned, archived, createdAt, updatedAt, lastViewedAt }) => ({
      id,
      sessionId,
      title,
      projectPath,
      projectType,
      pinned,
      archived,
      createdAt,
      updatedAt,
      lastViewedAt,
    }));
    window.localStorage.setItem(CHAT_SIDEBAR_CACHE_KEY, JSON.stringify(entries));
  } catch {
    // A blocked or full preference store must not affect chat persistence.
  }
}

export function removeChatFromSidebarCache(chatId: string): void {
  const entries = loadChatSidebarCache().filter((entry) => entry.id !== chatId);
  try {
    window.localStorage.setItem(CHAT_SIDEBAR_CACHE_KEY, JSON.stringify(entries));
  } catch {
    // Best effort only; the authoritative chat save remains the source of truth.
  }
}
