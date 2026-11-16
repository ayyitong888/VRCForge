import type { ChatThread, ProjectType } from "./chat-types";

export const CHAT_SIDEBAR_CACHE_KEY = "vrcforge_chat_sidebar_cache";

export type ChatSidebarCacheEntry = Pick<
  ChatThread,
  "id" | "sessionId" | "title" | "projectPath" | "projectType" | "pinned" | "archived" | "isTestChat" | "createdAt" | "updatedAt" | "lastViewedAt"
> & { sourceProjectPath?: string };

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
    isTestChat: candidate.isTestChat === true,
    createdAt: typeof candidate.createdAt === "string" ? candidate.createdAt : "",
    updatedAt: typeof candidate.updatedAt === "string" ? candidate.updatedAt : "",
    lastViewedAt: typeof candidate.lastViewedAt === "string" ? candidate.lastViewedAt : undefined,
    sourceProjectPath: typeof candidate.sourceProjectPath === "string" ? candidate.sourceProjectPath : undefined,
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

export function saveChatSidebarCache(
  chats: ReadonlyArray<ChatThread>,
  catalog: ReadonlyArray<{ id: string; sourceProjectPath: string }>,
): void {
  try {
    const sourcePaths = new Map(catalog.map((entry) => [entry.id, entry.sourceProjectPath]));
    const entries: ChatSidebarCacheEntry[] = chats.map(({ id, sessionId, title, projectPath, projectType, pinned, archived, isTestChat, createdAt, updatedAt, lastViewedAt }) => ({
      id,
      sessionId,
      title,
      projectPath,
      projectType,
      pinned,
      archived,
      isTestChat,
      createdAt,
      updatedAt,
      lastViewedAt,
      sourceProjectPath: sourcePaths.get(id),
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
