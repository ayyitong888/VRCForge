import { useEffect, useMemo, useRef, useState } from "react";
import { useTranslation } from "react-i18next";
import { fetchChats, saveChats } from "../lib/api";
import { TEMP_CHATS_COLLAPSE_KEY, type ActiveView } from "../lib/app-view";
import { isTauriRuntime } from "../lib/app-runtime";
import {
  cacheChatContextUsageFast,
  cacheChatTimestampsFast,
  filterPersistableChats,
  isStoredChat,
  normalizeChatContextUsage,
} from "../lib/chat-thread";
import type { ChatThread, ConversationItem } from "../lib/chat-types";
import { normalizeProjectPathKey } from "../lib/project-path";
import { buildChatSidebarView } from "../lib/sidebar-view";

type InitialChatState = {
  chats: ChatThread[];
  activeChatId: string;
};

type UseChatSessionsParams = {
  endpoint: string;
  runtimeConnected: boolean;
  projectPrefsReady: boolean;
  projectPaths: string[];
  customProjectPaths: string[];
  activeProjectPath: string;
  setActiveProjectPath: (value: string) => void;
  setActiveView: (value: ActiveView) => void;
  setError: (message: string) => void;
  expandProjectGroup: (projectPath: string) => void;
  initialChatState: InitialChatState;
};

export function useChatSessions({
  endpoint,
  runtimeConnected,
  projectPrefsReady,
  projectPaths,
  customProjectPaths,
  activeProjectPath,
  setActiveProjectPath,
  setActiveView,
  setError,
  expandProjectGroup,
  initialChatState,
}: UseChatSessionsParams) {
  const { i18n } = useTranslation();
  const [chats, setChats] = useState<ChatThread[]>(() => initialChatState.chats);
  const [activeChatId, setActiveChatId] = useState(() => initialChatState.activeChatId);
  const [chatMenu, setChatMenu] = useState<{ chatId: string; x: number; y: number } | null>(null);
  const [renamingChatId, setRenamingChatId] = useState("");
  const [renameDraft, setRenameDraft] = useState("");
  const [deleteTargetId, setDeleteTargetId] = useState("");

  const chatsLoadedRef = useRef(false);
  const chatsDirtyRef = useRef(false);
  const chatsSaveVersionRef = useRef(0);
  const chatTimestampCacheTimerRef = useRef<number | null>(null);
  const chatsRef = useRef<ChatThread[]>([]);

  const activeChat = chats.find((chat) => chat.id === activeChatId) || null;
  const chatSidebar = useMemo(
    () => buildChatSidebarView(chats, i18n.language, normalizeProjectPathKey),
    [chats, i18n.language],
  );

  useEffect(() => {
    chatsRef.current = chats;
  }, [chats]);

  useEffect(
    () => () => {
      if (chatTimestampCacheTimerRef.current) {
        window.clearTimeout(chatTimestampCacheTimerRef.current);
      }
    },
    [],
  );

  useEffect(() => {
    if (!runtimeConnected || chatsLoadedRef.current || !projectPrefsReady) {
      return;
    }
    let restoreCancelled = false;
    let restoreCompleted = false;
    chatsLoadedRef.current = true;
    void (async () => {
      try {
        await new Promise((resolve) => window.setTimeout(resolve, isTauriRuntime() ? 1000 : 0));
        if (restoreCancelled) {
          return;
        }
        const restoreProjectPaths = Array.from(
          new Set([...projectPaths.filter(Boolean), ...customProjectPaths.filter(Boolean)]),
        );
        const payload = await fetchChats<unknown>(endpoint, restoreProjectPaths);
        let shouldCacheRestoredTimestamps = false;
        const restored = (payload.chats || []).filter(isStoredChat).map((chat) => {
          const normalized: ChatThread = {
            id: chat.id,
            sessionId: typeof chat.sessionId === "string" ? chat.sessionId : "",
            title: typeof chat.title === "string" ? chat.title : "",
            projectPath: typeof chat.projectPath === "string" ? chat.projectPath : "",
            createdAt: typeof chat.createdAt === "string" ? chat.createdAt : "",
            updatedAt: typeof chat.updatedAt === "string" ? chat.updatedAt : "",
            agentName: typeof chat.agentName === "string" ? chat.agentName : "",
            pinned: chat.pinned === true,
            archived: chat.archived === true,
            contextUsageCache: normalizeChatContextUsage(chat.contextUsageCache),
            items: chat.items,
          };
          const cached = cacheChatContextUsageFast(cacheChatTimestampsFast(normalized));
          shouldCacheRestoredTimestamps =
            shouldCacheRestoredTimestamps || cached.createdAt !== normalized.createdAt || cached.updatedAt !== normalized.updatedAt;
          return cached;
        });
        if (restoreCancelled) {
          return;
        }
        if (restored.length > 0) {
          const initialSaveVersion = chatsSaveVersionRef.current;
          const canRestore = chatsRef.current.length === 0;
          if (canRestore) {
            setChats(restored);
            if (shouldCacheRestoredTimestamps) {
              if (chatTimestampCacheTimerRef.current) {
                window.clearTimeout(chatTimestampCacheTimerRef.current);
              }
              chatTimestampCacheTimerRef.current = window.setTimeout(() => {
                chatTimestampCacheTimerRef.current = null;
                if (chatsSaveVersionRef.current !== initialSaveVersion || chatsRef.current.length !== restored.length) {
                  return;
                }
                void saveChats(endpoint, filterPersistableChats(chatsRef.current));
              }, 3000);
            }
          }
        }
        restoreCompleted = true;
      } catch {
        chatsLoadedRef.current = false;
      }
    })();
    return () => {
      restoreCancelled = true;
      if (!restoreCompleted) {
        chatsLoadedRef.current = false;
      }
    };
  }, [runtimeConnected, endpoint, projectPaths, customProjectPaths, projectPrefsReady]);

  useEffect(() => {
    if (!chatsLoadedRef.current || !runtimeConnected || !chatsDirtyRef.current) {
      return;
    }
    const saveVersion = chatsSaveVersionRef.current;
    const timer = window.setTimeout(() => {
      void saveChats(endpoint, filterPersistableChats(chats))
        .then(() => {
          if (chatsSaveVersionRef.current === saveVersion) {
            chatsDirtyRef.current = false;
          }
        })
        .catch(() => {
          chatsDirtyRef.current = true;
        });
    }, 800);
    return () => window.clearTimeout(timer);
  }, [chats, runtimeConnected, endpoint]);

  function markChatsDirty() {
    chatsDirtyRef.current = true;
    chatsSaveVersionRef.current += 1;
  }

  function touchChat(chat: ChatThread, timestamp = new Date().toISOString()): ChatThread {
    return { ...chat, createdAt: chat.createdAt || timestamp, updatedAt: timestamp };
  }

  function updateChat(chatId: string, updater: (chat: ChatThread) => ChatThread) {
    markChatsDirty();
    setChats((list) => list.map((chat) => (chat.id === chatId ? cacheChatContextUsageFast(updater(chat)) : chat)));
  }

  function appendToChat(chatId: string, item: ConversationItem) {
    updateChat(chatId, (chat) => touchChat({ ...chat, items: [...chat.items, item] }));
  }

  function ensureActiveChat(): string {
    if (activeChat) {
      return activeChat.id;
    }
    const id = `chat-${Date.now()}`;
    const now = new Date().toISOString();
    markChatsDirty();
    setChats((list) => [{ id, sessionId: "", title: "", projectPath: activeProjectPath, createdAt: now, updatedAt: now, items: [] }, ...list]);
    setActiveChatId(id);
    return id;
  }

  function getChatById(chatId: string) {
    return chatsRef.current.find((chat) => chat.id === chatId);
  }

  function newConversation(projectPath?: string) {
    setActiveView("chat");
    if (projectPath !== undefined) {
      setActiveProjectPath(projectPath);
    }
    setActiveChatId("");
    setError("");
  }

  function togglePinChat(chatId: string) {
    updateChat(chatId, (chat) => ({ ...chat, pinned: !chat.pinned }));
  }

  function startRenameChat(chat: ChatThread) {
    setRenamingChatId(chat.id);
    setRenameDraft(chat.title || "");
  }

  function commitRenameChat(cancel = false) {
    if (!cancel && renamingChatId) {
      const title = renameDraft.trim();
      if (title) {
        updateChat(renamingChatId, (chat) => ({ ...chat, title }));
      }
    }
    setRenamingChatId("");
    setRenameDraft("");
  }

  function deleteChatPermanently(chatId: string) {
    markChatsDirty();
    setChats((list) => list.filter((chat) => chat.id !== chatId));
    if (activeChatId === chatId) {
      setActiveChatId("");
    }
    setDeleteTargetId("");
    setChatMenu(null);
  }

  function bindProject(projectPath: string) {
    setActiveProjectPath(projectPath);
    if (activeChatId) {
      updateChat(activeChatId, (chat) => ({ ...chat, projectPath }));
    }
  }

  function newTemporaryChat() {
    setActiveView("chat");
    setActiveProjectPath("");
    setError("");
    expandProjectGroup(TEMP_CHATS_COLLAPSE_KEY);
    setActiveChatId("");
  }

  function archiveProjectChats(path: string, archived: boolean) {
    const key = normalizeProjectPathKey(path);
    if (!key) {
      return;
    }
    markChatsDirty();
    setChats((list) => list.map((chat) => (normalizeProjectPathKey(chat.projectPath) === key ? { ...chat, archived } : chat)));
    if (archived && activeProjectPath && normalizeProjectPathKey(activeProjectPath) === key) {
      setActiveChatId("");
    }
  }

  function openChat(chat: ChatThread) {
    setActiveView("chat");
    setActiveChatId(chat.id);
    setActiveProjectPath(chat.projectPath);
    setChatMenu(null);
  }

  function selectProject(projectPath: string) {
    setActiveView("chat");
    setActiveProjectPath(projectPath);
    const latest = chats.find((chat) => normalizeProjectPathKey(chat.projectPath) === normalizeProjectPathKey(projectPath) && !chat.archived);
    setActiveChatId(latest ? latest.id : "");
  }

  return {
    chats,
    setChats,
    activeChat,
    activeChatId,
    setActiveChatId,
    chatMenu,
    setChatMenu,
    renamingChatId,
    renameDraft,
    setRenameDraft,
    deleteTargetId,
    setDeleteTargetId,
    chatSidebar,
    markChatsDirty,
    touchChat,
    updateChat,
    appendToChat,
    ensureActiveChat,
    getChatById,
    newConversation,
    togglePinChat,
    startRenameChat,
    commitRenameChat,
    deleteChatPermanently,
    bindProject,
    newTemporaryChat,
    archiveProjectChats,
    openChat,
    selectProject,
  };
}
