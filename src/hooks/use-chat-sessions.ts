import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import { useTranslation } from "react-i18next";
import { fetchChats, saveChats, type ChatRecoveryMarker, type ChatSourceRevision, type StoredChats } from "../lib/api";
import { deleteChatPermanentlyApi } from "../lib/api/app";
import { ApiError } from "../lib/api/http";
import { TEMP_CHATS_COLLAPSE_KEY, type ActiveView } from "../lib/app-view";
import { DEVELOPER_OPTIONS_ENABLED_KEY } from "../lib/app-preferences";
import { normalizeAttachmentPayloadVault, normalizeCompactedAttachmentReferences } from "../lib/attachment-payloads";
import {
  applyRevisionedChatUpdate,
  normalizeChatRevision,
  normalizeRestoredCompaction,
  restoredCompactionRequiresPersistence,
} from "../lib/chat-compaction-state";
import {
  chatDigestsFromPayload,
  chatStorageValuesEqual,
  type ChatStorageBaseline,
  chatMetadataShapesFromPayload,
  preserveConflictingChatCopies,
  reconcileChatStorage,
  snapshotChatFingerprints,
  sourceRevisionsFromPayload,
} from "../lib/chat-storage-reconcile";
import { expandChatRefresh } from "../lib/chat-refresh-snapshot";
import { buildChatStoragePatches } from "../lib/chat-storage-delta";
import { chatStorageOriginId } from "../lib/chat-storage-origin";
import {
  loadChatSidebarCache,
  removeChatFromSidebarCache,
  saveChatSidebarCache,
  type ChatSidebarCacheEntry,
} from "../lib/chat-sidebar-cache";
import { readChatTranscriptCache, removeChatTranscriptCache, writeChatTranscriptCache } from "../lib/chat-transcript-cache";
import {
  cacheChatContextUsageFast,
  cacheChatTimestampsFast,
  filterPersistableChats,
  formatChatSidebarTime,
  groupSidebarChats,
  isStoredChat,
  normalizeChatContextUsage,
  sortChatsByPin,
  stripSupersededStreamingItems,
  stripTransientConversationItems,
} from "../lib/chat-thread";
import type { ChatThread, ConversationItem, ProjectType } from "../lib/chat-types";
import { normalizeProjectPathKey } from "../lib/project-path";
import { chatLatestAgentMarker, chatSidebarActivity, type SidebarChatActivity } from "../lib/sidebar-view";

type InitialChatState = {
  chats: ChatThread[];
  activeChatId: string;
};

type ChatStorageLoadOutcome = {
  status: "ready" | "blocked" | "conflict" | "cancelled";
  chats: ChatThread[];
  shouldPersist: boolean;
};

function chatSaveErrorMessage(cause: unknown, fallback: string): string {
  // A 409 is owned by the fetch/merge recovery path, whose marker and message
  // already describe the durable conflict. Other API failures leave the
  // in-memory dirty snapshot retryable and should retain their actual detail.
  if (cause instanceof ApiError && cause.status === 409) {
    return fallback;
  }
  if (cause instanceof Error) {
    return cause.message.trim() || fallback;
  }
  return fallback;
}

function projectDurableChatFingerprint(chat: ChatThread): unknown {
  return filterPersistableChats([chat])[0] ?? null;
}

type UseChatSessionsParams = {
  activeView: ActiveView;
  endpoint: string;
  runtimeConnected: boolean;
  projectPrefsReady: boolean;
  projectPaths: string[];
  customProjectPaths: string[];
  activeProjectPath: string;
  activeProjectType?: ProjectType;
  setActiveProjectType?: (value: ProjectType) => void;
  setActiveProjectPath: (value: string) => void;
  setActiveView: (value: ActiveView) => void;
  setError: (message: string) => void;
  expandProjectGroup: (projectPath: string) => void;
  initialChatState: InitialChatState;
};

export function useChatSessions({
  activeView,
  endpoint,
  runtimeConnected,
  projectPrefsReady,
  projectPaths,
  customProjectPaths,
  activeProjectPath,
  activeProjectType = "general",
  setActiveProjectType,
  setActiveProjectPath,
  setActiveView,
  setError,
  expandProjectGroup,
  initialChatState,
}: UseChatSessionsParams) {
  const { i18n, t } = useTranslation();
  const [chats, setChats] = useState<ChatThread[]>(() => initialChatState.chats);
  const [sidebarCacheEntries, setSidebarCacheEntries] = useState<ChatSidebarCacheEntry[]>(() => loadChatSidebarCache());
  const [cachedDisplayChat, setCachedDisplayChat] = useState<{ endpoint: string; chat: ChatThread } | null>(null);
  const [chatStorageLoadSucceeded, setChatStorageLoadSucceeded] = useState(false);
  const [chatStorageReady, setChatStorageReady] = useState(false);
  const [activeChatId, setActiveChatId] = useState(() => initialChatState.activeChatId);
  const [chatMenu, setChatMenu] = useState<{ chatId: string; x: number; y: number } | null>(null);
  const [renamingChatId, setRenamingChatId] = useState("");
  const [renameDraft, setRenameDraft] = useState("");
  const [deleteTargetId, setDeleteTargetId] = useState("");
  const [chatRecoveries, setChatRecoveries] = useState<ChatRecoveryMarker[]>([]);
  const [chatPersistenceBlocked, setChatPersistenceBlocked] = useState(false);
  const [resolvingChatStorageConflict, setResolvingChatStorageConflict] = useState(false);

  const chatsLoadedRef = useRef(false);
  const restoredChatRequestKeyRef = useRef("");
  const chatsDirtyRef = useRef(false);
  const chatsSaveVersionRef = useRef(0);
  const chatTimestampCacheTimerRef = useRef<number | null>(null);
  const chatsRef = useRef<ChatThread[]>(initialChatState.chats);
  const chatsSaveQueueRef = useRef<Promise<void>>(Promise.resolve());
  const chatsAutosavePendingRef = useRef<Promise<void> | null>(null);
  const chatsAutosaveRerunRequestedRef = useRef(false);
  const chatsStorageOperationSequenceRef = useRef(0);
  const chatsStorageQueueDepthRef = useRef(0);
  const chatRestoreIntentKeyRef = useRef("");
  const chatRestoreInFlightKeysRef = useRef(new Set<string>());
  const chatRestoreMountedRef = useRef(true);
  const chatSourceRevisionsRef = useRef<ChatSourceRevision[]>([]);
  // The initial browser cache is an unsynchronized local candidate, not a
  // server baseline. An empty baseline preserves it as a local addition during
  // the first three-way merge instead of interpreting a missing server row as
  // a confirmed deletion.
  const chatBaselineFingerprintsRef = useRef<ChatStorageBaseline>({});
  const chatServerDigestsRef = useRef<Record<string, string> | undefined>(undefined);
  const chatServerMetadataShapesRef = useRef<Record<string, Record<string, unknown>> | undefined>(undefined);
  const chatPersistenceBlockedRef = useRef(false);
  // A concurrent-edit marker owns the visible error until the user resolves
  // the conflict. A queued debounce can still reject after the marker is set;
  // that generic rejection must not hide the reason the save was blocked.
  const chatStorageConflictRef = useRef(false);
  const chatStorageRecoveryBlockedRef = useRef(false);
  const chatDeletedIdsRef = useRef<Set<string>>(new Set());
  const pendingCachedDeletesRef = useRef(new Set<string>());
  const chatSidebarActivityCacheRef = useRef(new WeakMap<ChatThread, SidebarChatActivity | null>());

  const activeChat = chats.find((chat) => chat.id === activeChatId) || null;
  const selectedSidebarEntry = sidebarCacheEntries.find((entry) => entry.id === activeChatId);
  const selectedProjectPath = selectedSidebarEntry?.projectPath ?? activeProjectPath;
  // This provisional snapshot is display-only. It must never enter chatsRef,
  // reconciliation, an outgoing model request, or the authoritative save queue.
  const displayChat = useMemo<ChatThread | null>(() => {
    if (activeChat || chatStorageLoadSucceeded) return activeChat;
    if (cachedDisplayChat?.endpoint === endpoint
      && cachedDisplayChat.chat.id === activeChatId
      && normalizeProjectPathKey(cachedDisplayChat.chat.projectPath) === normalizeProjectPathKey(selectedProjectPath)) {
      return cachedDisplayChat.chat;
    }
    return selectedSidebarEntry ? { ...selectedSidebarEntry, items: [] } : null;
  }, [activeChat, activeChatId, cachedDisplayChat, chatStorageLoadSucceeded, endpoint, selectedProjectPath, selectedSidebarEntry]);
  const activeChatPending = Boolean(activeChatId && !activeChat && !chatStorageLoadSucceeded);

  useEffect(() => {
    if (!activeChatPending) return;
    let cancelled = false;
    void readChatTranscriptCache(endpoint, activeChatId, selectedProjectPath).then((chat) => {
      if (!cancelled && chat) setCachedDisplayChat({ endpoint, chat });
    });
    return () => { cancelled = true; };
  }, [activeChatId, activeChatPending, endpoint, selectedProjectPath]);

  useEffect(() => {
    if (!activeChat || !chatStorageLoadSucceeded || activeChat.items.some((item) => item.type === "streaming")) return;
    void writeChatTranscriptCache(endpoint, activeChat);
  }, [activeChat, chatStorageLoadSucceeded, endpoint]);
  const sidebarChats = useMemo<ChatThread[]>(() => {
    if (chatStorageLoadSucceeded || sidebarCacheEntries.length === 0) return chats;
    const authoritativeIds = new Set(chats.map((chat) => chat.id));
    const cached = sidebarCacheEntries
      .filter((entry) => !authoritativeIds.has(entry.id))
      .map((entry) => ({ ...entry, items: [] }));
    return [...chats, ...cached];
  }, [chatStorageLoadSucceeded, chats, sidebarCacheEntries]);

  const chatSidebarBase = useMemo(() => {
    const activityByChat = new Map<string, SidebarChatActivity>();
    for (const chat of sidebarChats) {
      if (!chatSidebarActivityCacheRef.current.has(chat)) {
        chatSidebarActivityCacheRef.current.set(chat, chatSidebarActivity(chat) || null);
      }
      const activity = chatSidebarActivityCacheRef.current.get(chat);
      if (activity) {
        activityByChat.set(chat.id, activity);
      }
    }
    return {
      ...groupSidebarChats(sidebarChats, normalizeProjectPathKey),
      activityByChat,
    };
  }, [i18n.language, sidebarChats]);
  const chatSidebarTimes = useMemo(() => {
    const nowMs = Date.now();
    return new Map(sidebarChats.map((chat) => [chat.id, formatChatSidebarTime(chat, nowMs, i18n.language)] as const));
  }, [activeChatId, activeView, i18n.language, sidebarChats]);
  const chatSidebar = useMemo(() => {
    const visibleChatId = activeView === "chat" ? activeChatId : "";
    let activityByChat = chatSidebarBase.activityByChat;
    if (visibleChatId && activityByChat.get(visibleChatId) === "completed") {
      activityByChat = new Map(activityByChat);
      activityByChat.delete(visibleChatId);
    }
    return { ...chatSidebarBase, times: chatSidebarTimes, activityByChat };
  }, [activeChatId, activeView, chatSidebarBase, chatSidebarTimes]);

  useEffect(() => {
    if (activeView !== "chat" || !activeChatId) return;
    const chat = chatsRef.current.find((candidate) => candidate.id === activeChatId);
    const marker = chat ? chatLatestAgentMarker(chat) : undefined;
    if (!chat || !marker || chat.lastViewedAt === marker) return;
    updateChat(activeChatId, (current) => ({ ...current, lastViewedAt: marker }));
  }, [activeChatId, activeView, chats]);

  useEffect(() => {
    if (chatStorageLoadSucceeded) {
      saveChatSidebarCache(chats);
      setSidebarCacheEntries(chats.map(({ id, sessionId, title, projectPath, projectType, pinned, archived, createdAt, updatedAt, lastViewedAt }) => ({
        id, sessionId, title, projectPath, projectType, pinned, archived, createdAt, updatedAt, lastViewedAt,
      })));
      return;
    }
    if (chats.length > 0) {
      const byId = new Map(sidebarCacheEntries.map((entry) => [entry.id, entry]));
      for (const chat of chats) {
        const { id, sessionId, title, projectPath, projectType, pinned, archived, createdAt, updatedAt, lastViewedAt } = chat;
        byId.set(id, { id, sessionId, title, projectPath, projectType, pinned, archived, createdAt, updatedAt, lastViewedAt });
      }
      const merged = [...byId.values()];
      saveChatSidebarCache(merged.map((entry) => ({ ...entry, items: [] } as ChatThread)));
      setSidebarCacheEntries(merged);
    }
  }, [chatStorageLoadSucceeded, chats]);

  useEffect(() => {
    chatRestoreMountedRef.current = true;
    return () => {
      chatRestoreMountedRef.current = false;
      if (chatTimestampCacheTimerRef.current) {
        window.clearTimeout(chatTimestampCacheTimerRef.current);
      }
    };
  }, []);

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

  function traceChatStorage(stage: "enqueued" | "started" | "resolved" | "failed", details: {
    operationId: number;
    kind: string;
    queueDepth: number;
    queueWaitMs?: number;
    durationMs?: number;
    errorType?: string;
  }) {
    try {
      if (window.localStorage.getItem(DEVELOPER_OPTIONS_ENABLED_KEY) !== "true") return;
      console.debug("[vrcforge:chat-storage]", {
        stage,
        timestamp: new Date().toISOString(),
        ...details,
      });
    } catch {
      // Storage diagnostics must never affect serialized chat persistence.
    }
  }

  function enqueueChatStorageOperation<T>(operation: () => Promise<T>, kind = "storage"): Promise<T> {
    const operationId = ++chatsStorageOperationSequenceRef.current;
    const enqueuedAt = performance.now();
    chatsStorageQueueDepthRef.current += 1;
    traceChatStorage("enqueued", { operationId, kind, queueDepth: chatsStorageQueueDepthRef.current });
    const pending = chatsSaveQueueRef.current.catch(() => undefined).then(async () => {
      chatsStorageQueueDepthRef.current = Math.max(0, chatsStorageQueueDepthRef.current - 1);
      const startedAt = performance.now();
      traceChatStorage("started", {
        operationId,
        kind,
        queueDepth: chatsStorageQueueDepthRef.current,
        queueWaitMs: startedAt - enqueuedAt,
      });
      try {
        const result = await operation();
        traceChatStorage("resolved", {
          operationId,
          kind,
          queueDepth: chatsStorageQueueDepthRef.current,
          durationMs: performance.now() - startedAt,
        });
        return result;
      } catch (cause) {
        traceChatStorage("failed", {
          operationId,
          kind,
          queueDepth: chatsStorageQueueDepthRef.current,
          durationMs: performance.now() - startedAt,
          errorType: cause instanceof Error ? cause.name : typeof cause,
        });
        throw cause;
      }
    });
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
    chatStorageConflictRef.current = true;
    setChatPersistenceBlocked(true);
    setChatRecoveries([conflictMarker]);
    setError(t("chat.sessionConcurrentConflict", { count: conflictIds.length }));
  }

  function setChatSaveError(cause: unknown, fallback: string) {
    if (chatStorageConflictRef.current) {
      return;
    }
    setError(chatSaveErrorMessage(cause, fallback));
  }

  async function reconcileFetchedChatStorage(
    payload: StoredChats<unknown>,
    shouldCommit: () => boolean = () => true,
  ): Promise<ChatStorageLoadOutcome> {
    if (!shouldCommit()) {
      return { status: "cancelled", chats: chatsRef.current, shouldPersist: false };
    }
    const sourceRevisions = sourceRevisionsFromPayload(payload.sources);
    // The backend deliberately keeps ad-hoc, unindexed project paths out of
    // `recoveries` and `writeBlocked`: a historical chat can retain a path
    // whose project is no longer mounted without making the whole chat store
    // read-only. Treat the backend recovery list as authoritative here;
    // synthesizing a marker from `sources` re-blocks a healthy store whenever
    // a refresh includes such a historical path.
    const recoveries = payload.recoveries || [];
    const blockingRecoveries = recoveries.filter((recovery) => recovery.status !== "recovered");
    const blocked = payload.writeBlocked === true || blockingRecoveries.length > 0;
    const recoveringFromDamagedSource = chatStorageRecoveryBlockedRef.current
      || recoveries.some((recovery) => recovery.status === "recovered");
    const { chats: unfilteredRemoteChats, shouldPersist } = normalizeStoredChatSnapshot(
      expandChatRefresh(payload, chatBaselineFingerprintsRef.current, chatServerDigestsRef.current),
      chatsLoadedRef.current ? chatsRef.current : [],
    );
    const unfilteredRemoteFingerprints = recoveringFromDamagedSource
      ? snapshotChatFingerprints(unfilteredRemoteChats, projectDurableChatFingerprint) : {};
    const remoteChats = recoveringFromDamagedSource
      ? unfilteredRemoteChats.filter((chat) => {
          if (!chatDeletedIdsRef.current.has(chat.id)) {
            return true;
          }
          const baselineFingerprint = chatBaselineFingerprintsRef.current[chat.id];
          return baselineFingerprint !== undefined && !chatStorageValuesEqual(unfilteredRemoteFingerprints[chat.id], baselineFingerprint);
        })
      : unfilteredRemoteChats;
    if (!shouldCommit()) {
      return { status: "cancelled", chats: chatsRef.current, shouldPersist: false };
    }
    const remoteDigests = chatDigestsFromPayload(payload.chatDigests, remoteChats);
    const remoteShapes = chatMetadataShapesFromPayload(payload.chatMetadataShapes, remoteChats);
    if (chatServerDigestsRef.current !== undefined && remoteDigests === undefined) {
      throw new Error("Chat storage response omitted chat version metadata after it was established.");
    }
    if ((chatServerDigestsRef.current !== undefined || remoteDigests !== undefined) && remoteShapes === undefined) {
      throw new Error("Chat storage response omitted chat metadata shape after it was established.");
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
      !recoveringFromDamagedSource && remoteDigests && chatServerDigestsRef.current
        ? { baseline: chatServerDigestsRef.current, remote: remoteDigests }
        : undefined,
    );
    if (reconciled.status === "conflict") {
      if (recoveringFromDamagedSource) {
        chatBaselineFingerprintsRef.current = {};
        chatServerDigestsRef.current = undefined;
        chatServerMetadataShapesRef.current = undefined;
      }
      chatStorageRecoveryBlockedRef.current = false;
      markChatStorageConflict(reconciled.conflictIds);
      return { status: "conflict", chats: chatsRef.current, shouldPersist: false };
    }
    const remoteFingerprints = reconciled.baselineFingerprints;
    const mergedDiffersFromRemote = reconciled.chats.length !== Object.keys(remoteFingerprints).length
      || reconciled.chats.some(chat => !chatStorageValuesEqual(projectDurableChatFingerprint(chat), remoteFingerprints[chat.id]));
    chatBaselineFingerprintsRef.current = remoteFingerprints;
    chatServerDigestsRef.current = remoteDigests;
    chatServerMetadataShapesRef.current = remoteShapes;
    chatsRef.current = reconciled.chats;
    setChats(reconciled.chats);
    for (const chatId of pendingCachedDeletesRef.current) {
      if (reconciled.chats.some((chat) => chat.id === chatId)) {
        pendingCachedDeletesRef.current.delete(chatId);
        void deleteChatPermanently(chatId);
      }
    }
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
    const chatPatches = buildChatStoragePatches(
      snapshot,
      chatBaselineFingerprintsRef.current,
      chatServerDigestsRef.current,
      chatServerMetadataShapesRef.current,
    );
    const result = await saveChats(endpoint, snapshot, chatSourceRevisionsRef.current, chatPatches);
    const nextServerDigests = chatDigestsFromPayload(result.chatDigests, snapshot);
    const nextServerShapes = chatMetadataShapesFromPayload(result.chatMetadataShapes, snapshot);
    if (chatServerDigestsRef.current !== undefined && nextServerDigests === undefined) {
      throw new Error("Chat storage acknowledgement omitted chat version metadata after it was established.");
    }
    if (nextServerDigests !== undefined && nextServerShapes === undefined) {
      throw new Error("Chat storage acknowledgement omitted chat metadata shape after it was established.");
    }
    if (result.sourceRevisions) {
      chatSourceRevisionsRef.current = result.sourceRevisions;
    }
    chatBaselineFingerprintsRef.current = snapshotChatFingerprints(snapshot, projectDurableChatFingerprint);
    chatServerDigestsRef.current = nextServerDigests;
    chatServerMetadataShapesRef.current = nextServerShapes;
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
        // Only a storage conflict can be resolved by fetching and merging.
        // Reconciliation calls setChats; doing that for a stable rejection
        // restarts the autosave debounce indefinitely. Keep the dirty local
        // snapshot so a new edit or explicit retry can save it later.
        if (attempt > 0 || !(cause instanceof ApiError) || cause.status !== 409) {
          throw cause;
        }
        const payload = await fetchChats<unknown>(endpoint, collectChatStorageProjectPaths(), chatServerDigestsRef.current);
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
    const restoreRequestKey = `${endpoint}\n${restorePathKey}`;
    if (chatsLoadedRef.current && restoredChatRequestKeyRef.current === restoreRequestKey) {
      return;
    }
    if (chatRestoreInFlightKeysRef.current.has(restoreRequestKey)) {
      chatRestoreIntentKeyRef.current = restoreRequestKey;
      restoredChatRequestKeyRef.current = restoreRequestKey;
      return;
    }
    chatRestoreIntentKeyRef.current = restoreRequestKey;
    chatRestoreInFlightKeysRef.current.add(restoreRequestKey);
    chatsLoadedRef.current = false;
    setChatStorageLoadSucceeded(false);
    setChatStorageReady(false);
    restoredChatRequestKeyRef.current = restoreRequestKey;
    const isCurrentRestore = () => (
      chatRestoreMountedRef.current && chatRestoreIntentKeyRef.current === restoreRequestKey
    );
    void (async () => {
      try {
        const outcome = await enqueueChatStorageOperation(async () => {
          if (!isCurrentRestore()) {
            return { status: "cancelled", chats: chatsRef.current, shouldPersist: false } satisfies ChatStorageLoadOutcome;
          }
          const payload = await fetchChats<unknown>(endpoint, restoreProjectPaths);
          return reconcileFetchedChatStorage(
            payload,
            () => isCurrentRestore(),
          );
        }, "restore");
        if (!isCurrentRestore() || outcome.status === "cancelled") {
          return;
        }
        chatsLoadedRef.current = true;
        setChatStorageLoadSucceeded(true);
        setChatStorageReady(true);
        if (outcome.chats.length > 0) {
          const initialSaveVersion = chatsSaveVersionRef.current;
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
                .catch((cause) => setChatSaveError(cause, t("chat.sessionSaveBlocked")));
            }, 3000);
          }
        }
      } catch {
        if (!isCurrentRestore()) {
          return;
        }
        chatsLoadedRef.current = false;
        restoredChatRequestKeyRef.current = "";
        setError(t("chat.sessionRestoreFailed"));
        // Durable follow-up recovery is independent from transcript fetch and
        // must still run when the chat snapshot endpoint is unavailable.
        setChatStorageReady(true);
      } finally {
        chatRestoreInFlightKeysRef.current.delete(restoreRequestKey);
      }
    })();
  }, [runtimeConnected, endpoint, projectPaths, customProjectPaths, projectPrefsReady, activeProjectPath, setError, t]);

  const reloadChatStorageState = useCallback(async (): Promise<boolean> => {
    if (!runtimeConnected || !projectPrefsReady) {
      return false;
    }
    return enqueueChatStorageOperation(async () => {
      const payload = await fetchChats<unknown>(endpoint, collectChatStorageProjectPaths(), chatServerDigestsRef.current);
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
    }, "reload");
  }, [activeProjectPath, customProjectPaths, endpoint, projectPaths, projectPrefsReady, runtimeConnected, setError, t]);

  const externalReloadRef = useRef(reloadChatStorageState);
  externalReloadRef.current = reloadChatStorageState;
  const externalReloadErrorRef = useRef(setError);
  externalReloadErrorRef.current = setError;
  const externalReloadTimerRef = useRef<number | null>(null);
  const externalReloadActiveRef = useRef(false);
  const externalReloadInFlightRef = useRef(false);
  const externalReloadPendingRef = useRef(false);
  useEffect(() => {
    externalReloadActiveRef.current = runtimeConnected && projectPrefsReady;
    return () => {
      externalReloadActiveRef.current = false;
      if (externalReloadTimerRef.current !== null) {
        window.clearTimeout(externalReloadTimerRef.current);
        externalReloadTimerRef.current = null;
      }
      externalReloadPendingRef.current = false;
    };
  }, [endpoint, runtimeConnected, projectPrefsReady]);

  const notifyChatStorageChanged = useCallback((originId?: string) => {
    if (originId && originId === chatStorageOriginId()) return;
    if (!externalReloadActiveRef.current) return;
    if (externalReloadTimerRef.current !== null) window.clearTimeout(externalReloadTimerRef.current);
    const reload = () => {
      if (!externalReloadActiveRef.current) return;
      if (externalReloadInFlightRef.current) {
        externalReloadPendingRef.current = true;
        return;
      }
      externalReloadPendingRef.current = false;
      externalReloadInFlightRef.current = true;
      void externalReloadRef.current().catch((cause) => {
        if (externalReloadActiveRef.current && !chatStorageConflictRef.current) {
          externalReloadErrorRef.current(cause instanceof Error ? cause.message : String(cause));
        }
      }).finally(() => {
        externalReloadInFlightRef.current = false;
        if (!externalReloadActiveRef.current || !externalReloadPendingRef.current || externalReloadTimerRef.current !== null) {
          return;
        }
        externalReloadPendingRef.current = false;
        externalReloadTimerRef.current = window.setTimeout(() => {
          externalReloadTimerRef.current = null;
          reload();
        }, 150);
      });
    };
    externalReloadTimerRef.current = window.setTimeout(() => {
      externalReloadTimerRef.current = null;
      reload();
    }, 150);
  }, []);

  const resolveChatStorageConflict = useCallback(async (): Promise<boolean> => {
    if (!runtimeConnected || !projectPrefsReady) {
      return false;
    }
    setResolvingChatStorageConflict(true);
    try {
      return await enqueueChatStorageOperation(async () => {
        const payload = await fetchChats<unknown>(endpoint, collectChatStorageProjectPaths(), chatServerDigestsRef.current);
        if (payload.writeBlocked === true || (payload.recoveries || []).length > 0) {
          await reconcileFetchedChatStorage(payload);
          return false;
        }
        const sourceRevisions = sourceRevisionsFromPayload(payload.sources);
        const { chats: remoteChats } = normalizeStoredChatSnapshot(expandChatRefresh(payload, chatBaselineFingerprintsRef.current, chatServerDigestsRef.current));
        const remoteDigests = chatDigestsFromPayload(payload.chatDigests, remoteChats);
        const remoteShapes = chatMetadataShapesFromPayload(payload.chatMetadataShapes, remoteChats);
        if (chatServerDigestsRef.current !== undefined && remoteDigests === undefined) {
          throw new Error("Chat storage response omitted chat version metadata after it was established.");
        }
        if ((chatServerDigestsRef.current !== undefined || remoteDigests !== undefined) && remoteShapes === undefined) {
          throw new Error("Chat storage response omitted chat metadata shape after it was established.");
        }
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
          chatServerDigestsRef.current && remoteDigests
            ? { baseline: chatServerDigestsRef.current, remote: remoteDigests }
            : undefined,
        );
        chatSourceRevisionsRef.current = sourceRevisions;
        chatBaselineFingerprintsRef.current = snapshotChatFingerprints(remoteChats, projectDurableChatFingerprint);
        chatServerDigestsRef.current = remoteDigests;
        chatServerMetadataShapesRef.current = remoteShapes;
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
        chatStorageConflictRef.current = false;
        return true;
      }, "conflict-resolution");
    } finally {
      setResolvingChatStorageConflict(false);
    }
  }, [activeProjectPath, customProjectPaths, endpoint, projectPaths, projectPrefsReady, runtimeConnected, t]);

  useEffect(() => {
    if (!runtimeConnected) return;
    // A stream of renders must not postpone saving until the chat becomes idle.
    const timer = window.setInterval(() => {
      if (!chatsLoadedRef.current || !chatsDirtyRef.current || chatPersistenceBlockedRef.current) return;
      enqueueDebouncedChatSave();
    }, 800);
    return () => window.clearInterval(timer);
  }, [runtimeConnected, endpoint]);

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
    ), "save");
    return pending.catch((cause) => {
      chatsDirtyRef.current = true;
      throw cause;
    });
  }

  function persistChatsNow(): Promise<void> {
    return enqueueChatSave(true);
  }

  function enqueueDebouncedChatSave(): void {
    if (chatsAutosavePendingRef.current) {
      chatsAutosaveRerunRequestedRef.current = true;
      return;
    }
    chatsAutosaveRerunRequestedRef.current = false;
    const pending = enqueueChatSave(true);
    chatsAutosavePendingRef.current = pending;
    void pending.then(() => {
      finishAutosave(pending, true);
    }, (cause) => {
      setChatSaveError(cause, t("chat.sessionSaveBlocked"));
      finishAutosave(pending, false);
    });
  }

  function finishAutosave(pending: Promise<void>, succeeded: boolean): void {
    if (chatsAutosavePendingRef.current !== pending) return;
    chatsAutosavePendingRef.current = null;
    const rerun = chatsAutosaveRerunRequestedRef.current;
    chatsAutosaveRerunRequestedRef.current = false;
    if (!succeeded || !rerun || !chatsDirtyRef.current || !runtimeConnected || chatPersistenceBlockedRef.current) return;
    // Changes received during a slow save need one latest snapshot. Debounced
    // autosaves never accumulate one stale FIFO operation per render.
    queueMicrotask(() => enqueueDebouncedChatSave());
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
    const created: ChatThread = { id, sessionId: "", title: "", projectPath: activeProjectPath, projectType: activeProjectType || (activeProjectPath ? "unity" : "general"), createdAt: now, updatedAt: now, revision: 0, items: [] };
    chatsRef.current = [created, ...chatsRef.current];
    setChats(chatsRef.current);
    setActiveChatId(id);
    return id;
  }

  function getChatById(chatId: string) {
    return chatsRef.current.find((chat) => chat.id === chatId);
  }

  function newConversation(projectPath?: string, projectType: ProjectType = projectPath ? "unity" : activeProjectType) {
    setActiveView("chat");
    if (projectPath !== undefined) {
      setActiveProjectPath(projectPath);
    }
    setActiveProjectType?.(projectType);
    setActiveChatId("");
    setError("");
  }

  function togglePinChat(chatId: string) {
    updateChat(chatId, (chat) => ({ ...chat, pinned: !chat.pinned }));
  }

  function toggleTestChat(chatId: string) {
    updateChat(chatId, (chat) => ({ ...chat, isTestChat: !chat.isTestChat }));
  }

  function startRenameChat(chat: ChatThread) {
    setRenamingChatId(chat.id);
    setRenameDraft(chat.title || "");
  }

  function commitRenameChat(cancel = false) {
    if (!cancel && renamingChatId) {
      const title = renameDraft.trim();
      if (title) {
        updateChat(renamingChatId, (chat) => ({ ...chat, title, titleSource: "manual" }));
      }
    }
    setRenamingChatId("");
    setRenameDraft("");
  }

  async function deleteChatPermanently(chatId: string) {
    const removedSnapshot = chatsRef.current.find((chat) => chat.id === chatId) || null;
    if (!removedSnapshot) {
      // A cached row has no authoritative transcript yet. Keep it visible and
      // replay the normal delete only after the restore supplies the real chat.
      pendingCachedDeletesRef.current.add(chatId);
      return;
    }
    const removedIndex = removedSnapshot ? chatsRef.current.indexOf(removedSnapshot) : -1;
    chatDeletedIdsRef.current.add(chatId);
    markChatsDirty();
    const next = chatsRef.current.filter((chat) => chat.id !== chatId);
    chatsRef.current = next;
    setChats(next);
    setSidebarCacheEntries((current) => current.filter((entry) => entry.id !== chatId));
    removeChatFromSidebarCache(chatId);
    if (activeChatId === chatId) {
      setActiveChatId("");
    }
    setDeleteTargetId("");
    setChatMenu(null);
    // The backend's runtime sessions are keyed by the session id echoed from
    // the agent run (chat.sessionId), not by the client chat id. A chat that
    // never ran has no runtime session and no native history to discard.
    const runtimeSessionId = String(removedSnapshot?.sessionId || "").trim();
    try {
      if (runtimeSessionId) {
        const result = await deleteChatPermanentlyApi(endpoint, runtimeSessionId);
        if (result.ok !== true) throw new ApiError(t("chat.deletePermanentlyFailed"), 0);
      }
      await persistChatsNow();
      await removeChatTranscriptCache(endpoint, chatId, removedSnapshot.projectPath);
    } catch (cause: unknown) {
      if (removedSnapshot && removedIndex >= 0 && !chatsRef.current.some((chat) => chat.id === chatId)) {
        chatDeletedIdsRef.current.delete(chatId);
        const restored = [...chatsRef.current];
        restored.splice(Math.min(removedIndex, restored.length), 0, removedSnapshot);
        chatsRef.current = restored;
        setChats(restored);
        saveChatSidebarCache(restored);
        setSidebarCacheEntries(restored.map(({ id, sessionId, title, projectPath, projectType, pinned, archived, createdAt, updatedAt, lastViewedAt }) => ({
          id, sessionId, title, projectPath, projectType, pinned, archived, createdAt, updatedAt, lastViewedAt,
        })));
        markChatsDirty();
      }
      setError(chatSaveErrorMessage(cause, t("chat.deletePermanentlyFailed")));
    }
  }

  function bindProject(projectPath: string, projectType: ProjectType = "unity") {
    setActiveProjectPath(projectPath);
    setActiveProjectType?.(projectType);
    if (activeChatId) {
      updateChat(activeChatId, (chat) => ({ ...chat, projectPath, projectType }));
    }
  }

  function newTemporaryChat() {
    setActiveView("chat");
    setActiveProjectPath("");
    setActiveProjectType?.("general");
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
    setActiveProjectType?.(chat.projectType || (chat.projectPath ? "unity" : "general"));
    setChatMenu(null);
  }

  function selectProject(projectPath: string, projectType: ProjectType = "unity") {
    setActiveView("chat");
    setActiveProjectPath(projectPath);
    setActiveProjectType?.(projectType);
    const latest = sortChatsByPin(chats.filter((chat) => (
      normalizeProjectPathKey(chat.projectPath) === normalizeProjectPathKey(projectPath) && !chat.archived
    )))[0];
    setActiveChatId(latest ? latest.id : "");
  }

  return {
    chats,
    chatStorageReady,
    setChats,
    activeChat,
    displayChat,
    activeChatPending,
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
    notifyChatStorageChanged,
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
    toggleTestChat,
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

function normalizeStoredChatSnapshot(values: unknown[], liveChats: ChatThread[] = []): { chats: ChatThread[]; shouldPersist: boolean } {
  let shouldPersist = false;
  const liveCompactions = new Map(liveChats.map((chat) => [chat.id, chat.compaction]));
  const chats = values.filter(isStoredChat).map((chat) => {
    // Storage notifications also read snapshots written by this live window.
    // Only cold restoration may classify its own active generation as abandoned;
    // the normal three-way merge still checks remote edits and revisions.
    const liveCompaction = liveCompactions.get(chat.id);
    const preserveActive = Boolean(liveCompaction?.generation
      && liveCompaction.generation === chat.compaction?.generation
      && liveCompaction.status === chat.compaction?.status
      && restoredCompactionRequiresPersistence(liveCompaction));
    const restoredCompactionWasInterrupted = !preserveActive && restoredCompactionRequiresPersistence(chat.compaction);
    const normalized: ChatThread = {
      id: chat.id,
      sessionId: typeof chat.sessionId === "string" ? chat.sessionId : "",
      title: typeof chat.title === "string" ? chat.title : "",
      titleSource: chat.titleSource === "auto" || chat.titleSource === "manual" ? chat.titleSource : undefined,
      isTestChat: chat.isTestChat === true ? true : undefined,
      projectPath: typeof chat.projectPath === "string" ? chat.projectPath : "",
      projectType: chat.projectType === "general" || chat.projectType === "unity"
        ? chat.projectType
        : (typeof chat.projectPath === "string" && chat.projectPath.trim() ? "unity" : "general"),
      createdAt: typeof chat.createdAt === "string" ? chat.createdAt : "",
      updatedAt: typeof chat.updatedAt === "string" ? chat.updatedAt : "",
      agentName: typeof chat.agentName === "string" ? chat.agentName : "",
      planMode: chat.planMode === true,
      pinned: chat.pinned === true,
      archived: chat.archived === true,
      revision: normalizeChatRevision(chat.revision),
      lastViewedAt: typeof chat.lastViewedAt === "string" ? chat.lastViewedAt : undefined,
      compaction: normalizeRestoredCompaction(chat.compaction, undefined, preserveActive),
      contextUsageCache: normalizeChatContextUsage(chat.contextUsageCache),
      attachmentPayloads: normalizeAttachmentPayloadVault(chat.attachmentPayloads),
      compactedAttachmentRefs: normalizeCompactedAttachmentReferences(chat.compactedAttachmentRefs),
      items: stripTransientConversationItems(chat.items),
    };
    const cached = cacheChatContextUsageFast(cacheChatTimestampsFast(normalized));
    shouldPersist = shouldPersist
      || restoredCompactionWasInterrupted
      || cached.createdAt !== normalized.createdAt
      || cached.projectType !== normalized.projectType
      || cached.updatedAt !== normalized.updatedAt
      || JSON.stringify(chat.attachmentPayloads) !== JSON.stringify(normalized.attachmentPayloads)
      || JSON.stringify(chat.compactedAttachmentRefs) !== JSON.stringify(normalized.compactedAttachmentRefs);
    return cached;
  });
  return { chats, shouldPersist };
}
