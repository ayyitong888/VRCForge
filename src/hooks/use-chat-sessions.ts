import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import { useTranslation } from "react-i18next";
import { fetchChats, saveChats, type ChatRecoveryMarker, type ChatSourceRevision, type StoredChats } from "../lib/api";
import { TEMP_CHATS_COLLAPSE_KEY, type ActiveView } from "../lib/app-view";
import { normalizeAttachmentPayloadVault, normalizeCompactedAttachmentReferences } from "../lib/attachment-payloads";
import {
  applyRevisionedChatUpdate,
  normalizeChatRevision,
  normalizeRestoredCompaction,
  restoredCompactionRequiresPersistence,
} from "../lib/chat-compaction-state";
import {
  preserveConflictingChatCopies,
  reconcileChatStorage,
  snapshotChatFingerprints,
  sourceRevisionsFromPayload,
} from "../lib/chat-storage-reconcile";
import {
  cacheChatContextUsageFast,
  cacheChatTimestampsFast,
  filterPersistableChats,
  isStoredChat,
  normalizeChatContextUsage,
  stripSupersededStreamingItems,
  stripTransientConversationItems,
} from "../lib/chat-thread";
import type { ChatThread, ConversationItem } from "../lib/chat-types";
import { normalizeProjectPathKey } from "../lib/project-path";
import { buildChatSidebarView } from "../lib/sidebar-view";

type InitialChatState = {
  chats: ChatThread[];
  activeChatId: string;
};

type ChatStorageLoadOutcome = {
  status: "ready" | "blocked" | "conflict" | "cancelled";
  chats: ChatThread[];
  shouldPersist: boolean;
};

function projectDurableChatFingerprint(chat: ChatThread): unknown {
  return filterPersistableChats([chat])[0] ?? null;
}

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
  const { i18n, t } = useTranslation();
  const [chats, setChats] = useState<ChatThread[]>(() => initialChatState.chats);
  const [activeChatId, setActiveChatId] = useState(() => initialChatState.activeChatId);
  const [chatMenu, setChatMenu] = useState<{ chatId: string; x: number; y: number } | null>(null);
  const [renamingChatId, setRenamingChatId] = useState("");
  const [renameDraft, setRenameDraft] = useState("");
  const [deleteTargetId, setDeleteTargetId] = useState("");
  const [chatRecoveries, setChatRecoveries] = useState<ChatRecoveryMarker[]>([]);
  const [chatPersistenceBlocked, setChatPersistenceBlocked] = useState(false);
  const [resolvingChatStorageConflict, setResolvingChatStorageConflict] = useState(false);

  const chatsLoadedRef = useRef(false);
  const restoredChatPathKeyRef = useRef("");
  const chatsDirtyRef = useRef(false);
  const chatsSaveVersionRef = useRef(0);
  const chatTimestampCacheTimerRef = useRef<number | null>(null);
  const chatsRef = useRef<ChatThread[]>(initialChatState.chats);
  const chatsSaveQueueRef = useRef<Promise<void>>(Promise.resolve());
  const chatRestoreGenerationRef = useRef(0);
  const chatSourceRevisionsRef = useRef<ChatSourceRevision[]>([]);
  // The initial browser cache is an unsynchronized local candidate, not a
  // server baseline. An empty baseline preserves it as a local addition during
  // the first three-way merge instead of interpreting a missing server row as
  // a confirmed deletion.
  const chatBaselineFingerprintsRef = useRef<Record<string, string>>({});
  const chatPersistenceBlockedRef = useRef(false);
  const chatStorageRecoveryBlockedRef = useRef(false);
  const chatDeletedIdsRef = useRef<Set<string>>(new Set());

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

  function collectChatStorageProjectPaths(snapshot = chatsRef.current): string[] {
    const paths = [
      ...projectPaths,
      ...customProjectPaths,
      activeProjectPath,
      ...snapshot.map((chat) => chat.projectPath),
    ];
    const selected = new Map<string, string>();
    for (const value of paths) {
      const path = String(value || "").trim();
      if (!path) {
        continue;
      }
      selected.set(normalizeProjectPathKey(path) || path.toLowerCase(), path);
    }
    return [...selected.values()];
  }

  function enqueueChatStorageOperation<T>(operation: () => Promise<T>): Promise<T> {
    const pending = chatsSaveQueueRef.current.catch(() => undefined).then(operation);
    chatsSaveQueueRef.current = pending.then(() => undefined, () => undefined);
    return pending;
  }

  function markChatStorageConflict(conflictIds: string[]) {
    const conflictMarker: ChatRecoveryMarker = {
      storeId: "chat.concurrent-conflict",
      scope: "app",
      status: "conflict",
      reason: "concurrent_update",
      requiresApproval: false,
      invalidCount: conflictIds.length,
    };
    chatPersistenceBlockedRef.current = true;
    setChatPersistenceBlocked(true);
    setChatRecoveries([conflictMarker]);
    setError(t("chat.sessionConcurrentConflict", { count: conflictIds.length }));
  }

  async function reconcileFetchedChatStorage(
    payload: StoredChats<unknown>,
    shouldCommit: () => boolean = () => true,
  ): Promise<ChatStorageLoadOutcome> {
    if (!shouldCommit()) {
      return { status: "cancelled", chats: chatsRef.current, shouldPersist: false };
    }
    const sourceRevisions = sourceRevisionsFromPayload(payload.sources);
    const localProjectKeys = new Set(
      chatsRef.current
        .map((chat) => normalizeProjectPathKey(chat.projectPath))
        .filter(Boolean),
    );
    const unavailableLocalRecoveries: ChatRecoveryMarker[] = (payload.sources || []).flatMap((source) => {
      const projectPath = typeof source.projectPath === "string" ? source.projectPath : "";
      const projectKey = normalizeProjectPathKey(projectPath);
      if (source.unavailable !== true || source.indexed === true || !projectKey || !localProjectKeys.has(projectKey)) {
        return [];
      }
      return [{
        storeId: String(source.storeId || "chat.project-unavailable"),
        scope: "project",
        status: "unsupported",
        reason: "project_unavailable",
        requiresApproval: false,
        invalidCount: 0,
      }];
    });
    const recoveries = [...(payload.recoveries || []), ...unavailableLocalRecoveries];
    const blockingRecoveries = recoveries.filter((recovery) => recovery.status !== "recovered");
    const blocked = payload.writeBlocked === true || blockingRecoveries.length > 0;
    const recoveringFromDamagedSource = chatStorageRecoveryBlockedRef.current
      || recoveries.some((recovery) => recovery.status === "recovered");
    const { chats: unfilteredRemoteChats, shouldPersist } = normalizeStoredChatSnapshot(payload.chats || []);
    const unfilteredRemoteFingerprints = snapshotChatFingerprints(unfilteredRemoteChats, projectDurableChatFingerprint);
    const remoteChats = recoveringFromDamagedSource
      ? unfilteredRemoteChats.filter((chat) => {
          if (!chatDeletedIdsRef.current.has(chat.id)) {
            return true;
          }
          const baselineFingerprint = chatBaselineFingerprintsRef.current[chat.id];
          return baselineFingerprint !== undefined && unfilteredRemoteFingerprints[chat.id] !== baselineFingerprint;
        })
      : unfilteredRemoteChats;
    if (!shouldCommit()) {
      return { status: "cancelled", chats: chatsRef.current, shouldPersist: false };
    }
    chatSourceRevisionsRef.current = sourceRevisions;
    if (blocked) {
      const current = chatsRef.current;
      const currentIds = new Set(current.map((chat) => chat.id));
      const additions = remoteChats.filter(
        (chat) => !currentIds.has(chat.id) && chatBaselineFingerprintsRef.current[chat.id] === undefined,
      );
      const safeChats = additions.length ? [...current, ...additions] : current;
      chatsRef.current = safeChats;
      setChats(safeChats);
      chatPersistenceBlockedRef.current = true;
      chatStorageRecoveryBlockedRef.current = true;
      setChatPersistenceBlocked(true);
      setChatRecoveries(recoveries);
      setError(t("chat.sessionRecoveryBlocked", { count: blockingRecoveries.length }));
      return { status: "blocked", chats: safeChats, shouldPersist: false };
    }
    const reconciliationBaseline = recoveringFromDamagedSource
      ? {}
      : chatBaselineFingerprintsRef.current;
    const reconciled = reconcileChatStorage(
      reconciliationBaseline,
      chatsRef.current,
      remoteChats,
      projectDurableChatFingerprint,
    );
    if (reconciled.status === "conflict") {
      if (recoveringFromDamagedSource) {
        chatBaselineFingerprintsRef.current = {};
      }
      chatStorageRecoveryBlockedRef.current = false;
      markChatStorageConflict(reconciled.conflictIds);
      return { status: "conflict", chats: chatsRef.current, shouldPersist: false };
    }
    const remoteFingerprints = snapshotChatFingerprints(remoteChats, projectDurableChatFingerprint);
    const mergedFingerprints = snapshotChatFingerprints(reconciled.chats, projectDurableChatFingerprint);
    const mergedDiffersFromRemote = !chatFingerprintSnapshotsEqual(mergedFingerprints, remoteFingerprints);
    chatBaselineFingerprintsRef.current = remoteFingerprints;
    chatsRef.current = reconciled.chats;
    setChats(reconciled.chats);
    chatPersistenceBlockedRef.current = false;
    chatStorageRecoveryBlockedRef.current = false;
    setChatPersistenceBlocked(false);
    setChatRecoveries(recoveries);
    if (recoveringFromDamagedSource || mergedDiffersFromRemote) {
      // Quarantine makes a damaged source disappear; that disappearance is not
      // a user-confirmed deletion. Preserve the in-memory candidate and make
      // the clean snapshot durable before clearing the recovery transition.
      markChatsDirty();
    }
    return {
      status: "ready",
      chats: reconciled.chats,
      shouldPersist: shouldPersist || recoveringFromDamagedSource || mergedDiffersFromRemote,
    };
  }

  async function saveChatSnapshotWithinStorageOperation(
    snapshot: ChatThread[],
    saveVersion: number,
    clearDirty: boolean,
  ): Promise<void> {
    if (chatPersistenceBlockedRef.current) {
      throw new Error(t("chat.sessionSaveBlocked"));
    }
    const result = await saveChats(endpoint, snapshot, chatSourceRevisionsRef.current);
    if (result.sourceRevisions) {
      chatSourceRevisionsRef.current = result.sourceRevisions;
    }
    chatBaselineFingerprintsRef.current = snapshotChatFingerprints(snapshot, projectDurableChatFingerprint);
    if (chatsSaveVersionRef.current === saveVersion) {
      if (clearDirty) {
        chatsDirtyRef.current = false;
      }
      chatDeletedIdsRef.current.clear();
    }
  }

  async function saveWithOneReconcileRetryWithinStorageOperation(
    initialSnapshot: ChatThread[],
    initialSaveVersion: number,
    clearDirty: boolean,
  ): Promise<void> {
    let snapshot = initialSnapshot;
    let saveVersion = initialSaveVersion;
    for (let attempt = 0; attempt < 2; attempt += 1) {
      try {
        await saveChatSnapshotWithinStorageOperation(snapshot, saveVersion, clearDirty);
        return;
      } catch (cause) {
        if (attempt > 0) {
          throw cause;
        }
        const payload = await fetchChats<unknown>(endpoint, collectChatStorageProjectPaths());
        const outcome = await reconcileFetchedChatStorage(payload);
        if (outcome.status !== "ready") {
          throw cause;
        }
        snapshot = filterPersistableChats(chatsRef.current);
        saveVersion = chatsSaveVersionRef.current;
      }
    }
  }

  useEffect(() => {
    if (!runtimeConnected || !projectPrefsReady) {
      return;
    }
    const restoreProjectPaths = collectChatStorageProjectPaths();
    const restorePathKey = restoreProjectPaths.map((path) => normalizeProjectPathKey(path)).sort().join("\n");
    if (chatsLoadedRef.current && restoredChatPathKeyRef.current === restorePathKey) {
      return;
    }
    let restoreCancelled = false;
    const restoreGeneration = chatRestoreGenerationRef.current + 1;
    chatRestoreGenerationRef.current = restoreGeneration;
    chatsLoadedRef.current = false;
    restoredChatPathKeyRef.current = restorePathKey;
    void (async () => {
      try {
        const outcome = await enqueueChatStorageOperation(async () => {
          if (restoreCancelled || chatRestoreGenerationRef.current !== restoreGeneration) {
            return { status: "cancelled", chats: chatsRef.current, shouldPersist: false } satisfies ChatStorageLoadOutcome;
          }
          const payload = await fetchChats<unknown>(endpoint, restoreProjectPaths);
          return reconcileFetchedChatStorage(
            payload,
            () => !restoreCancelled && chatRestoreGenerationRef.current === restoreGeneration,
          );
        });
        if (restoreCancelled || outcome.status === "cancelled") {
          return;
        }
        chatsLoadedRef.current = true;
        if (outcome.chats.length > 0) {
          const initialSaveVersion = chatsSaveVersionRef.current;
          if (!activeChatId && activeProjectPath) {
            const activeProjectKey = normalizeProjectPathKey(activeProjectPath);
            const latest = outcome.chats.find((chat) => normalizeProjectPathKey(chat.projectPath) === activeProjectKey && !chat.archived);
            if (latest) {
              setActiveChatId(latest.id);
              expandProjectGroup(latest.projectPath);
            }
          }
          if (outcome.shouldPersist && !chatPersistenceBlockedRef.current) {
            if (chatTimestampCacheTimerRef.current) {
              window.clearTimeout(chatTimestampCacheTimerRef.current);
            }
            chatTimestampCacheTimerRef.current = window.setTimeout(() => {
              chatTimestampCacheTimerRef.current = null;
              if (chatsSaveVersionRef.current !== initialSaveVersion) {
                return;
              }
              void enqueueChatSave(false)
                .catch(() => setError(t("chat.sessionSaveBlocked")));
            }, 3000);
          }
        }
      } catch {
        if (restoreCancelled) {
          return;
        }
        chatsLoadedRef.current = false;
        restoredChatPathKeyRef.current = "";
        setError(t("chat.sessionRestoreFailed"));
      }
    })();
    return () => {
      restoreCancelled = true;
    };
  }, [runtimeConnected, endpoint, projectPaths, customProjectPaths, projectPrefsReady, activeChatId, activeProjectPath, expandProjectGroup, setError, t]);

  const reloadChatStorageState = useCallback(async (): Promise<boolean> => {
    if (!runtimeConnected || !projectPrefsReady) {
      return false;
    }
    return enqueueChatStorageOperation(async () => {
      const payload = await fetchChats<unknown>(endpoint, collectChatStorageProjectPaths());
      const outcome = await reconcileFetchedChatStorage(payload);
      if (outcome.status !== "ready") {
        return false;
      }
      if (chatsDirtyRef.current) {
        await saveWithOneReconcileRetryWithinStorageOperation(
          filterPersistableChats(chatsRef.current),
          chatsSaveVersionRef.current,
          true,
        );
      }
      return true;
    });
  }, [activeProjectPath, customProjectPaths, endpoint, projectPaths, projectPrefsReady, runtimeConnected, setError, t]);

  const resolveChatStorageConflict = useCallback(async (): Promise<boolean> => {
    if (!runtimeConnected || !projectPrefsReady) {
      return false;
    }
    setResolvingChatStorageConflict(true);
    try {
      return await enqueueChatStorageOperation(async () => {
        const payload = await fetchChats<unknown>(endpoint, collectChatStorageProjectPaths());
        if (payload.writeBlocked === true || (payload.recoveries || []).length > 0) {
          await reconcileFetchedChatStorage(payload);
          return false;
        }
        const sourceRevisions = sourceRevisionsFromPayload(payload.sources);
        const { chats: remoteChats } = normalizeStoredChatSnapshot(payload.chats || []);
        const localCopyIds = new Map<string, string>();
        const resolved = preserveConflictingChatCopies(
          chatBaselineFingerprintsRef.current,
          chatsRef.current,
          remoteChats,
          (chat, index) => {
            const suffix = globalThis.crypto?.randomUUID?.() || `${Date.now()}-${index}`;
            const id = `chat-recovered-${suffix}`;
            localCopyIds.set(chat.id, id);
            const now = new Date().toISOString();
            const copyLabel = t("chat.concurrentConflictLocalCopy");
            return {
              ...chat,
              id,
              sessionId: "",
              title: chat.title ? `${chat.title} — ${copyLabel}` : copyLabel,
              pinned: false,
              archived: false,
              revision: 0,
              createdAt: now,
              updatedAt: now,
            };
          },
          projectDurableChatFingerprint,
        );
        chatSourceRevisionsRef.current = sourceRevisions;
        chatBaselineFingerprintsRef.current = snapshotChatFingerprints(remoteChats, projectDurableChatFingerprint);
        chatsRef.current = resolved.chats;
        setChats(resolved.chats);
        setActiveChatId((current) => localCopyIds.get(current) || current);
        // Keep the visible recovery marker until the conflict-preserving CAS
        // write succeeds. Internally unblock only this serialized write.
        chatPersistenceBlockedRef.current = false;
        markChatsDirty();
        try {
          await saveChatSnapshotWithinStorageOperation(
            filterPersistableChats(resolved.chats),
            chatsSaveVersionRef.current,
            true,
          );
        } catch (cause) {
          chatPersistenceBlockedRef.current = true;
          setChatPersistenceBlocked(true);
          throw cause;
        }
        chatStorageRecoveryBlockedRef.current = false;
        setChatPersistenceBlocked(false);
        setChatRecoveries([]);
        return true;
      });
    } finally {
      setResolvingChatStorageConflict(false);
    }
  }, [activeProjectPath, customProjectPaths, endpoint, projectPaths, projectPrefsReady, runtimeConnected, t]);

  useEffect(() => {
    if (!chatsLoadedRef.current || !runtimeConnected || !chatsDirtyRef.current || chatPersistenceBlockedRef.current) {
      return;
    }
    const timer = window.setTimeout(() => {
      void enqueueChatSave(true)
        .catch(() => setError(t("chat.sessionSaveBlocked")));
    }, 800);
    return () => window.clearTimeout(timer);
  }, [chats, runtimeConnected, endpoint]);

  function markChatsDirty() {
    chatsDirtyRef.current = true;
    chatsSaveVersionRef.current += 1;
  }

  /**
   * Whole-chat persistence is serialized so an older request can never finish
   * after (and overwrite) a newer snapshot. Callers that need a durable owner
   * or handoff boundary can await persistChatsNow instead of relying on the
   * normal debounce.
   */
  function enqueueChatSave(clearDirty: boolean): Promise<void> {
    // A queued snapshot must never be paired with source revisions refreshed by
    // an earlier operation. Re-sample both the chat state and its generation
    // only when this operation reaches the head of the storage queue.
    const pending = enqueueChatStorageOperation(() => (
      saveWithOneReconcileRetryWithinStorageOperation(
        filterPersistableChats(chatsRef.current),
        chatsSaveVersionRef.current,
        clearDirty,
      )
    ));
    return pending.catch((cause) => {
      chatsDirtyRef.current = true;
      throw cause;
    });
  }

  function persistChatsNow(): Promise<void> {
    return enqueueChatSave(true);
  }

  function touchChat(chat: ChatThread, timestamp = new Date().toISOString()): ChatThread {
    return { ...chat, createdAt: chat.createdAt || timestamp, updatedAt: timestamp };
  }

  function updateChat(chatId: string, updater: (chat: ChatThread) => ChatThread): boolean {
    return updateChatIfRevision(chatId, undefined, updater);
  }

  /**
   * Apply one chat mutation only when the caller still owns the observed
   * revision. Long-running work (context compaction, delivery materialization,
   * approvals) must use this instead of committing an old snapshot over a
   * newer chat.
   */
  function updateChatIfRevision(
    chatId: string,
    expectedRevision: number | undefined,
    updater: (chat: ChatThread) => ChatThread,
  ): boolean {
    let changed = false;
    const next = chatsRef.current.map((chat) => {
      if (chat.id !== chatId) {
        return chat;
      }
      const revisioned = applyRevisionedChatUpdate(chat, expectedRevision, updater);
      if (!revisioned.applied) {
        return chat;
      }
      const updated = revisioned.chat;
      const items = stripSupersededStreamingItems(updated.items);
      changed = true;
      return cacheChatContextUsageFast({
        ...(items === updated.items ? updated : { ...updated, items }),
      });
    });
    if (!changed) {
      return false;
    }
    markChatsDirty();
    chatsRef.current = next;
    setChats(next);
    return true;
  }

  function appendToChat(chatId: string, item: ConversationItem) {
    updateChat(chatId, (chat) => touchChat({ ...chat, items: [...chat.items, item] }));
  }

  function ensureActiveChat(): string {
    const existing = chatsRef.current.find((chat) => chat.id === activeChatId);
    if (existing) {
      return existing.id;
    }
    const id = `chat-${Date.now()}`;
    const now = new Date().toISOString();
    markChatsDirty();
    const created = { id, sessionId: "", title: "", projectPath: activeProjectPath, createdAt: now, updatedAt: now, revision: 0, items: [] };
    chatsRef.current = [created, ...chatsRef.current];
    setChats(chatsRef.current);
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
    chatDeletedIdsRef.current.add(chatId);
    markChatsDirty();
    const next = chatsRef.current.filter((chat) => chat.id !== chatId);
    chatsRef.current = next;
    setChats(next);
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
    const next = chatsRef.current.map((chat) => (
      normalizeProjectPathKey(chat.projectPath) === key ? { ...chat, archived } : chat
    ));
    chatsRef.current = next;
    setChats(next);
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
    chatRecoveries,
    chatPersistenceBlocked,
    resolvingChatStorageConflict,
    resolveChatStorageConflict,
    reloadChatStorageState,
    markChatsDirty,
    touchChat,
    updateChat,
    updateChatIfRevision,
    appendToChat,
    ensureActiveChat,
    persistChatsNow,
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

function chatFingerprintSnapshotsEqual(
  left: Readonly<Record<string, string>>,
  right: Readonly<Record<string, string>>,
): boolean {
  const leftKeys = Object.keys(left);
  const rightKeys = Object.keys(right);
  return leftKeys.length === rightKeys.length && leftKeys.every((key) => left[key] === right[key]);
}

function normalizeStoredChatSnapshot(values: unknown[]): { chats: ChatThread[]; shouldPersist: boolean } {
  let shouldPersist = false;
  const chats = values.filter(isStoredChat).map((chat) => {
    const restoredCompactionWasInterrupted = restoredCompactionRequiresPersistence(chat.compaction);
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
      revision: normalizeChatRevision(chat.revision),
      compaction: normalizeRestoredCompaction(chat.compaction),
      contextUsageCache: normalizeChatContextUsage(chat.contextUsageCache),
      attachmentPayloads: normalizeAttachmentPayloadVault(chat.attachmentPayloads),
      compactedAttachmentRefs: normalizeCompactedAttachmentReferences(chat.compactedAttachmentRefs),
      items: stripTransientConversationItems(chat.items),
    };
    const cached = cacheChatContextUsageFast(cacheChatTimestampsFast(normalized));
    shouldPersist = shouldPersist
      || restoredCompactionWasInterrupted
      || cached.createdAt !== normalized.createdAt
      || cached.updatedAt !== normalized.updatedAt
      || JSON.stringify(chat.attachmentPayloads) !== JSON.stringify(normalized.attachmentPayloads)
      || JSON.stringify(chat.compactedAttachmentRefs) !== JSON.stringify(normalized.compactedAttachmentRefs);
    return cached;
  });
  return { chats, shouldPersist };
}
