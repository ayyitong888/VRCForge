import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import { useTranslation } from "react-i18next";
import { fetchChatCatalog, fetchChatRecord, saveChatRecord, type ChatCatalogEntry, type ChatSaveReceipt, type ChatRecoveryMarker, type ChatSourceRevision, type StoredChats } from "../lib/api";
import { deleteChatPermanentlyApi } from "../lib/api/app";
import { ApiError } from "../lib/api/http";
import type { ProjectRecycleResult } from "../lib/api/project";
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
import { buildChatStoragePatches } from "../lib/chat-storage-delta";
import { chatStorageOriginId } from "../lib/chat-storage-origin";
import { acknowledgeLocalChatInputs, readLocalChatInputs, removeLocalChatInputs, restoreLocalChatInputs } from "../lib/chat-local-inputs";
import {
  loadChatSidebarCache,
  removeChatFromSidebarCache,
  saveChatSidebarCache,
  type ChatSidebarCacheEntry,
} from "../lib/chat-sidebar-cache";
import { removeChatTranscriptCache } from "../lib/chat-transcript-cache";
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
import { isExternalRuntimeStream } from "../lib/runtime-stream-owner";
import { chatSidebarActivity, type SidebarChatActivity } from "../lib/sidebar-view";
import { latestChatReplyMarker } from "../lib/chat-unread";
import { useChatHistoryPages } from "./use-chat-history-pages";

type InitialChatState = {
  chats: ChatThread[];
  activeChatId: string;
};

type ChatStorageLoadOutcome = {
  status: "ready" | "blocked" | "conflict" | "cancelled";
  chats: ChatThread[];
  shouldPersist: boolean;
};

type ChatStorageError = {
  kind: "save" | "conflict" | "blocked";
  message: string;
};

type PendingChatSave = {
  endpoint: string;
  snapshot: ChatThread[];
  chatId: string;
  deleted?: boolean;
  saveVersion: number;
  submit: () => Promise<ChatSaveReceipt>;
};

export function chatSaveErrorMessage(cause: unknown, fallback: string, translate: (key: string) => string): string {
  if (cause instanceof ApiError && cause.status === 409) {
    const detail = cause.detail;
    let code = "";
    if (detail && typeof detail === "object") {
      const record = detail as { code?: unknown; detail?: unknown };
      code = String(record.code || (record.detail && typeof record.detail === "object" ? (record.detail as { code?: unknown }).code : "") || "");
    } else if (typeof detail === "string") {
      try {
        const parsed = JSON.parse(detail) as { code?: unknown; detail?: { code?: unknown } };
        code = String(parsed.code || parsed.detail?.code || "");
      } catch {
        code = "";
      }
    }
    if (code === "chat_store_recovery_required") {
      return translate("chat.sessionSaveBlocked");
    }
    return translate("chat.sessionSaveConflict");
  }
  return cause instanceof ApiError && cause.status === 413 ? translate("chat.saveTooLarge") : fallback;
}

function projectDurableChatFingerprint(chat: ChatThread): unknown {
  return filterPersistableChats([chat])[0] ?? null;
}

function isStableChatSaveRejection(cause: unknown): cause is ApiError {
  return cause instanceof ApiError
    && cause.status >= 400 && cause.status < 500
    && ![408, 425, 429].includes(cause.status);
}

type UseChatSessionsParams = {
  activeView: ActiveView;
  chatContentVisible?: boolean;
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
  chatContentVisible = true,
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
  const [catalog, setCatalog] = useState<ChatCatalogEntry[] | null>(null);
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
  const [chatStorageError, setChatStorageError] = useState<ChatStorageError | null>(null);

  const chatsLoadedRef = useRef(false);
  const restoredChatRequestKeyRef = useRef("");
  const chatsDirtyRef = useRef(false);
  const chatsSaveVersionRef = useRef(0);
  const chatAutosaveFailureVersionRef = useRef<number | null>(null);
  const chatTimestampCacheTimerRef = useRef<number | null>(null);
  const chatsRef = useRef<ChatThread[]>(initialChatState.chats);
  const catalogRef = useRef<ChatCatalogEntry[]>([]);
  const pendingChatIdsRef = useRef<string[]>([]);
  const loadingChatsRef = useRef(new Map<string, Promise<ChatThread | undefined>>());
  const endpointRef = useRef(endpoint);
  endpointRef.current = endpoint;
  const chatsSaveQueueRef = useRef<Promise<void>>(Promise.resolve());
  const chatsAutosavePendingRef = useRef<Promise<void> | null>(null);
  const chatsAutosaveRerunRequestedRef = useRef(false);
  const chatsStorageOperationSequenceRef = useRef(0);
  const chatsStorageQueueDepthRef = useRef(0);
  const chatRestoreIntentKeyRef = useRef("");
  const chatRestoreInFlightKeysRef = useRef(new Set<string>());
  const chatRestoreMountedRef = useRef(true);
  const chatSourceRevisionsRef = useRef<ChatSourceRevision[]>([]);
  const pendingChatSaveRef = useRef<PendingChatSave | null>(null);
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
  const chatSidebarActivityCacheRef = useRef(new WeakMap<ChatThread, SidebarChatActivity | null>());

  const activeChat = chats.find((chat) => chat.id === activeChatId) || null;
  const selectedEntry = catalog?.find((entry) => entry.id === activeChatId);
  const cachedSource = catalog === null && !activeChat
    ? sidebarCacheEntries.find((entry) => entry.id === activeChatId)?.sourceProjectPath
    : undefined;
  const usingCachedSource = cachedSource !== undefined;
  const history = useChatHistoryPages(
    endpoint, activeChatId, selectedEntry?.sourceProjectPath ?? cachedSource, activeChat,
    selectedEntry?.updatedAt,
    runtimeConnected && projectPrefsReady, !usingCachedSource, () => setError(t("chat.sessionRestoreFailed")),
  );
  const displayChat = usingCachedSource ? null : history.chat;
  const previewChat = usingCachedSource ? history.chat : null;
  const activeChatPending = usingCachedSource || (history.pending && (!chatStorageLoadSucceeded || Boolean(selectedEntry)));

  const sidebarChats = useMemo<ChatThread[]>(() => {
    const loadedIds = new Set(chats.map((chat) => chat.id));
    const entries = catalog ?? sidebarCacheEntries;
    return [...chats, ...entries
      .filter((entry) => !loadedIds.has(entry.id) && !chatDeletedIdsRef.current.has(entry.id))
      .map((entry) => ({ ...entry, items: [] }))];
  }, [catalog, chats, sidebarCacheEntries]);

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
    if (activeView !== "chat" || !chatContentVisible || !activeChat) return;
    const markViewed = () => {
      if (document.visibilityState !== "visible" || !document.hasFocus()) return;
      const chat = chatsRef.current.find((candidate) => candidate.id === activeChat.id);
      const marker = chat && latestChatReplyMarker(chat);
      if (chat && marker && chat.lastViewedAt !== marker) {
        updateChat(chat.id, (current) => ({ ...current, lastViewedAt: marker }));
      }
    };
    markViewed();
    window.addEventListener("focus", markViewed);
    document.addEventListener("visibilitychange", markViewed);
    return () => {
      window.removeEventListener("focus", markViewed);
      document.removeEventListener("visibilitychange", markViewed);
    };
  }, [activeView, activeChat, chatContentVisible]);

  useEffect(() => {
    if (catalog) saveChatSidebarCache(sidebarChats, catalog);
  }, [catalog, sidebarChats]);

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
      ...catalogRef.current.map((chat) => chat.sourceProjectPath),
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

  async function fetchLoadedChatSnapshot(
    projectPaths: string[],
    isCurrent: () => boolean = () => true,
  ): Promise<StoredChats<unknown>> {
    const response = await fetchChatCatalog(endpoint, projectPaths);
    if (!isCurrent()) return { ...response, path: "", exists: false, count: 0, chats: [] };
    const entries = new Map(response.entries.map((entry) => [entry.id, entry]));
    catalogRef.current = response.entries;
    pendingChatIdsRef.current = response.pendingChatIds;
    setCatalog(response.entries);
    const localInputs = await readLocalChatInputs(endpoint).catch(() => []);
    const ids = new Set([
      ...localInputs.map((input) => input.owner.id),
      ...Object.keys(chatBaselineFingerprintsRef.current),
      ...chatsRef.current.filter((chat) => entries.has(chat.id)).map((chat) => chat.id),
    ]);
    const records = await Promise.all([...ids].map(async (id) => {
      const entry = entries.get(id);
      if (!entry) return null;
      return await fetchChatRecord(endpoint, id, entry.sourceProjectPath, { full: true });
    }));
    const bodies = records.flatMap((record) => record?.chats ?? []);
    const digests = Object.assign({}, ...records.map((record) => record?.chatDigests));
    const shapes = Object.assign({}, ...records.map((record) => record?.chatMetadataShapes));
    for (const [id, baseline] of Object.entries(chatBaselineFingerprintsRef.current)) {
      if (ids.has(id) || !baseline) continue;
      bodies.push(baseline);
      digests[id] = chatServerDigestsRef.current?.[id];
      shapes[id] = chatServerMetadataShapesRef.current?.[id];
    }
    return {
      ...response,
      path: "",
      exists: true,
      chats: bodies,
      count: bodies.length,
      chatDigests: digests,
      chatMetadataShapes: shapes,
    };
  }

  function mergeSourceRevisions(revisions: ChatSourceRevision[]) {
    const merged = new Map(chatSourceRevisionsRef.current.map((revision) => [revision.storeId, revision]));
    for (const revision of revisions) merged.set(revision.storeId, revision);
    chatSourceRevisionsRef.current = [...merged.values()];
  }

  async function loadChat(chatId: string): Promise<ChatThread | undefined> {
    const existing = getChatById(chatId);
    if (existing) return existing;
    const entry = catalogRef.current.find((row) => row.id === chatId);
    if (!entry) return undefined;
    const key = JSON.stringify([endpoint, chatId]);
    const pending = loadingChatsRef.current.get(key);
    if (pending) return pending;
    const loading = fetchChatRecord(endpoint, chatId, entry.sourceProjectPath, { full: true }).then((result) => {
      const loaded = getChatById(chatId);
      if (loaded) return loaded;
      const normalized = normalizeStoredChatSnapshot(result.chats);
      const chat = normalized.chats.find((record) => record.id === chatId);
      if (!chat || !chatRestoreMountedRef.current || endpointRef.current !== endpoint) return undefined;
      if (!catalogRef.current.some((row) => row.id === chatId)) return undefined;
      const digests = chatDigestsFromPayload(result.chatDigests, [chat]);
      const shapes = chatMetadataShapesFromPayload(result.chatMetadataShapes, [chat]);
      if (result.sourceRevision) mergeSourceRevisions([result.sourceRevision]);
      Object.assign(chatBaselineFingerprintsRef.current, snapshotChatFingerprints([chat], projectDurableChatFingerprint));
      Object.assign(chatServerDigestsRef.current ??= {}, digests);
      Object.assign(chatServerMetadataShapesRef.current ??= {}, shapes);
      chatsRef.current = [...chatsRef.current, chat];
      setChats(chatsRef.current);
      if (normalized.shouldPersist) markChatsDirty();
      return chat;
    }).finally(() => loadingChatsRef.current.delete(key));
    loadingChatsRef.current.set(key, loading);
    return loading;
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
    setChatStorageError({ kind: "conflict", message: t("chat.sessionConcurrentConflict", { count: conflictIds.length }) });
  }

  function setChatSaveError(cause: unknown, fallback: string) {
    if (chatStorageConflictRef.current) {
      return;
    }
    setChatStorageError({
      kind: "save",
      message: chatSaveErrorMessage(cause, fallback, t),
    });
  }

  function clearResolvedChatStorageError() {
    chatAutosaveFailureVersionRef.current = null;
    setChatStorageError((current) => {
      if (current?.kind === "conflict" && chatStorageConflictRef.current) return current;
      if (current?.kind === "blocked" && chatPersistenceBlockedRef.current) return current;
      return null;
    });
  }

  async function reconcileFetchedChatStorage(
    payload: StoredChats<unknown>,
    shouldCommit: () => boolean = () => true,
  ): Promise<ChatStorageLoadOutcome> {
    const localInputs = await readLocalChatInputs(endpoint).catch(() => []);
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
      payload.chats,
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
    if (localInputs.length && !blocked) void acknowledgeLocalChatInputs(endpoint, remoteChats).catch(() => undefined);
    if (blocked) {
      const current = chatsRef.current;
      const currentIds = new Set(current.map((chat) => chat.id));
      const additions = remoteChats.filter(
        (chat) => !currentIds.has(chat.id) && chatBaselineFingerprintsRef.current[chat.id] === undefined,
      );
      const safeChats = restoreLocalChatInputs(additions.length ? [...current, ...additions] : current, localInputs, chatDeletedIdsRef.current);
      chatsRef.current = safeChats;
      setChats(safeChats);
      chatPersistenceBlockedRef.current = true;
      chatStorageRecoveryBlockedRef.current = true;
      setChatPersistenceBlocked(true);
      setChatRecoveries(recoveries);
      setChatStorageError({ kind: "blocked", message: t("chat.sessionRecoveryBlocked", { count: blockingRecoveries.length }) });
      return { status: "blocked", chats: safeChats, shouldPersist: false };
    }
    const reconciliationBaseline = recoveringFromDamagedSource
      ? {}
      : chatBaselineFingerprintsRef.current;
    let localChats = chatsRef.current;
    if (recoveringFromDamagedSource && localInputs.length) {
      // A blocked read may have shown the journal over a healthy transcript.
      // Undo that exact display overlay for reconciliation and restore it below.
      const projected = new Map(restoreLocalChatInputs(remoteChats, localInputs, chatDeletedIdsRef.current)
        .map((chat) => [chat.id, chat]));
      const remoteById = new Map(remoteChats.map((chat) => [chat.id, chat]));
      localChats = localChats.map((chat) => {
        const remote = remoteById.get(chat.id);
        const recovered = projected.get(chat.id);
        return remote && recovered !== remote && chatStorageValuesEqual(chat, recovered) ? remote : chat;
      });
    }
    const reconciled = reconcileChatStorage(
      reconciliationBaseline,
      localChats,
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
    const localById = new Map(localChats.map((chat) => [chat.id, chat]));
    const withLiveStreams = reconciled.chats.map((chat) => restoreLiveChatStreams(chat, localById.get(chat.id)));
    const restoredChats = restoreLocalChatInputs(withLiveStreams, localInputs, chatDeletedIdsRef.current);
    const mergedDiffersFromRemote = restoredChats.length !== Object.keys(remoteFingerprints).length
      || restoredChats.some(chat => !chatStorageValuesEqual(projectDurableChatFingerprint(chat), remoteFingerprints[chat.id]));
    chatBaselineFingerprintsRef.current = remoteFingerprints;
    chatServerDigestsRef.current = remoteDigests;
    chatServerMetadataShapesRef.current = remoteShapes;
    chatsRef.current = restoredChats;
    setChats(restoredChats);
    chatPersistenceBlockedRef.current = false;
    chatStorageRecoveryBlockedRef.current = false;
    chatStorageConflictRef.current = false;
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
      chats: restoredChats,
      shouldPersist: shouldPersist || recoveringFromDamagedSource || mergedDiffersFromRemote,
    };
  }

  async function saveChatSnapshotWithinStorageOperation(
    snapshot: ChatThread[],
    saveVersion: number,
    clearDirty: boolean,
  ): Promise<void> {
    if (chatPersistenceBlockedRef.current) throw new Error(t("chat.sessionSaveBlocked"));
    await settlePendingChatSave();
    const baseline = chatBaselineFingerprintsRef.current;
    const digests = chatServerDigestsRef.current ??= {};
    const shapes = chatServerMetadataShapesRef.current ??= {};
    for (const chat of snapshot) {
      const previous = baseline[chat.id];
      if (chatStorageValuesEqual(chat, previous)) continue;
      const owner = catalogRef.current.find((entry) => entry.id === chat.id);
      const projectPath = owner?.sourceProjectPath ?? previous?.projectPath ?? chat.projectPath;
      const patch = buildChatStoragePatches([chat], baseline, digests, shapes)?.[chat.id];
      const request = { chatId: chat.id, projectPath, sourceRevisions: chatSourceRevisionsRef.current,
        ...(patch ? { chatPatch: patch } : { chat }) };
      const chatSaveId = globalThis.crypto.randomUUID();
      pendingChatSaveRef.current = { endpoint, snapshot: [chat], chatId: chat.id, saveVersion,
        submit: () => saveChatRecord(endpoint, request, chatSaveId) };
      await settlePendingChatSave();
    }
    for (const chatId of [...chatDeletedIdsRef.current]) {
      const owner = catalogRef.current.find((entry) => entry.id === chatId);
      const projectPath = owner?.sourceProjectPath ?? baseline[chatId]?.projectPath;
      if (projectPath !== undefined) {
        const request = { chatId, projectPath, sourceRevisions: chatSourceRevisionsRef.current, delete: true as const };
        const chatSaveId = globalThis.crypto.randomUUID();
        pendingChatSaveRef.current = { endpoint, snapshot: [], chatId, deleted: true, saveVersion,
          submit: () => saveChatRecord(endpoint, request, chatSaveId) };
        await settlePendingChatSave();
      }
    }
    if (chatsSaveVersionRef.current === saveVersion) {
      if (clearDirty) chatsDirtyRef.current = false;
      chatDeletedIdsRef.current.clear();
    }
    clearResolvedChatStorageError();
  }

  async function settlePendingChatSave(): Promise<void> {
    const pending = pendingChatSaveRef.current;
    if (!pending || pending.endpoint !== endpoint) return;
    let result: ChatSaveReceipt;
    try {
      result = await pending.submit();
    } catch (cause) {
      if (!(cause instanceof ApiError) || cause.status !== 0) pendingChatSaveRef.current = null;
      throw cause;
    }
    pendingChatSaveRef.current = null;
    const { snapshot, chatId, deleted } = pending;
    const digests = chatServerDigestsRef.current ??= {};
    const shapes = chatServerMetadataShapesRef.current ??= {};
    const baseline = chatBaselineFingerprintsRef.current;
    if (result.sourceRevisions) mergeSourceRevisions(result.sourceRevisions);
    if (deleted) {
      delete baseline[chatId];
      delete digests[chatId];
      delete shapes[chatId];
      chatDeletedIdsRef.current.delete(chatId);
      catalogRef.current = catalogRef.current.filter((entry) => entry.id !== chatId);
      setCatalog(catalogRef.current);
      return;
    }
    const savedDigests = chatDigestsFromPayload(result.chatDigests, snapshot);
    const savedShapes = chatMetadataShapesFromPayload(result.chatMetadataShapes, snapshot);
    if (savedDigests === undefined) {
      throw new Error("Chat storage acknowledgement omitted chat version metadata after it was established.");
    }
    if (savedShapes === undefined) {
      throw new Error("Chat storage acknowledgement omitted chat metadata shape after it was established.");
    }
    Object.assign(digests, savedDigests);
    Object.assign(shapes, savedShapes);
    const chat = snapshot[0];
    const owner = catalogRef.current.find((entry) => entry.id === chatId);
    Object.assign(baseline, snapshotChatFingerprints(snapshot, projectDurableChatFingerprint));
    if (owner && owner.sourceProjectPath !== chat.projectPath) {
      catalogRef.current = catalogRef.current.map((entry) => entry.id === chatId
        ? { ...entry, sourceProjectPath: chat.projectPath } : entry);
      setCatalog(catalogRef.current);
    }
    if (result.ok === true) void acknowledgeLocalChatInputs(endpoint, snapshot).catch(() => undefined);
  }

  async function settlePendingChatSaveBeforeRead(): Promise<void> {
    try {
      await settlePendingChatSave();
    } catch (cause) {
      // A definite conflict is handled by the authoritative read and merge.
      if (!(cause instanceof ApiError) || cause.status !== 409) throw cause;
    }
  }

  async function saveWithOneReconcileRetryWithinStorageOperation(
    initialSnapshot: ChatThread[],
    initialSaveVersion: number,
    clearDirty: boolean,
    onAttemptVersion?: (version: number) => void,
  ): Promise<void> {
    let snapshot = initialSnapshot;
    let saveVersion = initialSaveVersion;
    for (let attempt = 0; attempt < 2; attempt += 1) {
      try {
        onAttemptVersion?.(saveVersion);
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
        const payload = await fetchLoadedChatSnapshot(collectChatStorageProjectPaths());
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
          await settlePendingChatSaveBeforeRead();
          const payload = await fetchLoadedChatSnapshot(restoreProjectPaths, isCurrentRestore);
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
        void Promise.allSettled(pendingChatIdsRef.current.map(loadChat)).then(() => {
          if (isCurrentRestore()) setChatStorageReady(true);
        });
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
                .catch((cause) => setChatSaveError(cause, t("chat.saveUnavailable")));
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
    try {
      const reloaded = await enqueueChatStorageOperation(async () => {
        await settlePendingChatSaveBeforeRead();
        const payload = await fetchLoadedChatSnapshot(collectChatStorageProjectPaths());
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
      if (reloaded) clearResolvedChatStorageError();
      return reloaded;
    } catch (cause) {
      setChatSaveError(cause, t("chat.saveUnavailable"));
      throw cause;
    }
  }, [activeProjectPath, customProjectPaths, endpoint, projectPaths, projectPrefsReady, runtimeConnected, t]);

  const externalReloadRef = useRef(reloadChatStorageState);
  externalReloadRef.current = reloadChatStorageState;
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
      void externalReloadRef.current().catch(() => undefined).finally(() => {
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
        const payload = await fetchLoadedChatSnapshot(collectChatStorageProjectPaths());
        if (payload.writeBlocked === true || (payload.recoveries || []).length > 0) {
          await reconcileFetchedChatStorage(payload);
          return false;
        }
        const sourceRevisions = sourceRevisionsFromPayload(payload.sources);
        const { chats: remoteChats } = normalizeStoredChatSnapshot(payload.chats);
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
        setChatStorageError(null);
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
      if (!chatsLoadedRef.current || !chatsDirtyRef.current || chatPersistenceBlockedRef.current
        || chatAutosaveFailureVersionRef.current === chatsSaveVersionRef.current) return;
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
    let attemptedSaveVersion: number | undefined;
    const pending = enqueueChatStorageOperation(() => saveWithOneReconcileRetryWithinStorageOperation(
      filterPersistableChats(chatsRef.current),
      chatsSaveVersionRef.current,
      clearDirty,
      (version) => { attemptedSaveVersion = version; },
    ), "save");
    return pending.then(() => {
      chatAutosaveFailureVersionRef.current = null;
    }, (cause) => {
      if (isStableChatSaveRejection(cause) && attemptedSaveVersion === chatsSaveVersionRef.current) {
        chatAutosaveFailureVersionRef.current = attemptedSaveVersion;
      }
      chatsDirtyRef.current = true;
      throw cause;
    });
  }

  function persistChatsNow(): Promise<void> {
    return enqueueChatSave(true);
  }

  function reportChatSaveError(cause: unknown) {
    setChatSaveError(cause, t("chat.saveUnavailable"));
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
      setChatSaveError(cause, t("chat.saveUnavailable"));
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
    void loadChat(chatId).then(() => updateChat(chatId, (chat) => ({ ...chat, pinned: !chat.pinned }))).catch(reportChatSaveError);
  }

  function toggleTestChat(chatId: string) {
    void loadChat(chatId).then(() => updateChat(chatId, (chat) => ({ ...chat, isTestChat: !chat.isTestChat }))).catch(reportChatSaveError);
  }

  function startRenameChat(chat: ChatThread) {
    setRenamingChatId(chat.id);
    setRenameDraft(chat.title || "");
  }

  function commitRenameChat(cancel = false) {
    if (!cancel && renamingChatId) {
      const title = renameDraft.trim();
      if (title) {
        void loadChat(renamingChatId).then(() => updateChat(renamingChatId, (chat) => ({ ...chat, title, titleSource: "manual" }))).catch(reportChatSaveError);
      }
    }
    setRenamingChatId("");
    setRenameDraft("");
  }

  async function deleteChatPermanently(chatId: string) {
    const removedSnapshot = await loadChat(chatId).catch((cause) => {
      reportChatSaveError(cause);
      return undefined;
    });
    if (!removedSnapshot) return;
    const removedIndex = removedSnapshot ? chatsRef.current.indexOf(removedSnapshot) : -1;
    chatDeletedIdsRef.current.add(chatId);
    markChatsDirty();
    const next = chatsRef.current.filter((chat) => chat.id !== chatId);
    chatsRef.current = next;
    setChats(next);
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
    const deleteSaveVersion = chatsSaveVersionRef.current;
    let durableDeletionCommitted = false;
    try {
      // The user explicitly deleted this conversation. Discard its pending
      // local inputs first so a crash after server deletion cannot recreate it.
      await removeLocalChatInputs(endpoint, chatId).catch(() => undefined);
      try {
        await persistChatsNow();
      } catch (cause) {
        if (chatDeletedIdsRef.current.has(chatId)) throw cause;
        reportChatSaveError(cause);
      }
      durableDeletionCommitted = true;
      await removeChatTranscriptCache(endpoint, chatId, removedSnapshot.projectPath);
      if (runtimeSessionId && ![...chatsRef.current, ...catalogRef.current].some((chat) => chat.sessionId.trim() === runtimeSessionId)) {
        const result = await deleteChatPermanentlyApi(endpoint, runtimeSessionId);
        if (result.ok !== true) throw new ApiError("Runtime cleanup did not return success.", 0);
      }
    } catch (cause: unknown) {
      const rollbackUnchangedSave = chatsSaveVersionRef.current === deleteSaveVersion;
      if (!durableDeletionCommitted && removedSnapshot && removedIndex >= 0 && !chatsRef.current.some((chat) => chat.id === chatId)) {
        chatDeletedIdsRef.current.delete(chatId);
        const restored = [...chatsRef.current];
        restored.splice(Math.min(removedIndex, restored.length), 0, removedSnapshot);
        chatsRef.current = restored;
        setChats(restored);
        markChatsDirty();
        if (rollbackUnchangedSave && chatAutosaveFailureVersionRef.current === deleteSaveVersion) {
          chatAutosaveFailureVersionRef.current = chatsSaveVersionRef.current;
        }
      }
      if (durableDeletionCommitted) {
        setError(t("chat.deletePermanentlyPartialSuccess"));
      } else {
        setChatSaveError(cause, t("chat.deletePermanentlyFailed"));
      }
    }
  }

  function bindProject(projectPath: string, projectType: ProjectType = "unity") {
    setActiveProjectPath(projectPath);
    setActiveProjectType?.(projectType);
    if (activeChatId) {
      void loadChat(activeChatId).then(() => updateChat(activeChatId, (chat) => ({ ...chat, projectPath, projectType }))).catch(reportChatSaveError);
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

  async function archiveProjectChats(path: string, archived: boolean) {
    const key = normalizeProjectPathKey(path);
    if (!key) {
      return;
    }
    try {
      const affected = sidebarChats.filter((chat) => normalizeProjectPathKey(chat.projectPath) === key);
      await Promise.all(affected.map((chat) => loadChat(chat.id)));
    } catch (cause) {
      reportChatSaveError(cause);
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

  async function recycleProjectWithChats(path: string, recycle: () => Promise<ProjectRecycleResult>) {
    return enqueueChatStorageOperation(async () => {
      // Drain chat writes before removing their project directory, then discard
      // its loaded state before autosaves can resume.
      if (chatsDirtyRef.current) {
        await saveWithOneReconcileRetryWithinStorageOperation(
          filterPersistableChats(chatsRef.current), chatsSaveVersionRef.current, true,
        );
      } else {
        await settlePendingChatSave();
      }
      const result = await recycle();
      const keys = new Set([path, result.path].map(normalizeProjectPathKey));
      const removed = (entry: { projectPath?: string; sourceProjectPath?: string }) =>
        keys.has(normalizeProjectPathKey(entry.projectPath))
        || keys.has(normalizeProjectPathKey(entry.sourceProjectPath));
      const ids = new Set([
        ...chatsRef.current.filter(removed).map((chat) => chat.id),
        ...catalogRef.current.filter(removed).map((chat) => chat.id),
        ...sidebarCacheEntries.filter(removed).map((chat) => chat.id),
      ]);
      for (const [id, baseline] of Object.entries(chatBaselineFingerprintsRef.current)) {
        if (baseline && removed(baseline)) ids.add(id);
      }
      for (const id of ids) {
        delete chatBaselineFingerprintsRef.current[id];
        if (chatServerDigestsRef.current) delete chatServerDigestsRef.current[id];
        if (chatServerMetadataShapesRef.current) delete chatServerMetadataShapesRef.current[id];
        chatDeletedIdsRef.current.delete(id);
      }
      const keep = (entry: { id: string }) => !ids.has(entry.id);
      chatsRef.current = chatsRef.current.filter(keep);
      catalogRef.current = catalogRef.current.filter(keep);
      pendingChatIdsRef.current = pendingChatIdsRef.current.filter((id) => !ids.has(id));
      chatSourceRevisionsRef.current = chatSourceRevisionsRef.current.filter(
        (source) => !keys.has(normalizeProjectPathKey(source.projectPath)),
      );
      setChats(chatsRef.current);
      setCatalog((current) => current?.filter(keep) ?? null);
      setSidebarCacheEntries((current) => current.filter(keep));
      if (keys.has(normalizeProjectPathKey(activeProjectPath)) || ids.has(activeChatId)) {
        newConversation("", "general");
      }
      return result;
    }, "recycle-project");
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
    const latest = sortChatsByPin(sidebarChats.filter((chat) => (
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
    previewChat,
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
    sidebarChats,
    loadChat,
    loadOlderChatMessages: history.loadOlder,
    hasOlderChatMessages: history.hasOlder,
    chatRecoveries,
    chatPersistenceBlocked,
    chatStorageError,
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
    reportChatSaveError,
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
    recycleProjectWithChats,
    openChat,
    selectProject,
  };
}

function restoreLiveChatStreams(chat: ChatThread, local: ChatThread | undefined): ChatThread {
  if (!local || local === chat || local.sessionId !== chat.sessionId) return chat;
  const liveStreams = local.items.filter((item) => item.type === "streaming");
  if (!liveStreams.length) return chat;
  const items = [...chat.items];
  for (const stream of liveStreams) {
    if (items.some((item) => item.id === stream.id)) continue;
    // Keep a live response anchored to its preceding conversation item.
    // A foreground receipt may replace that anchor while an async run remains active.
    const anchor = local.items[local.items.indexOf(stream) - 1];
    const anchorIndex = anchor ? items.findIndex((item) => item.id === anchor.id) : -1;
    if (!anchor || anchorIndex >= 0) items.splice(anchorIndex + 1, 0, stream);
    else if (isExternalRuntimeStream(stream)) items.push(stream);
  }
  return { ...chat, items: stripSupersededStreamingItems(items) };
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
      sessionReadOnly: chat.sessionReadOnly === true ? true : undefined,
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
