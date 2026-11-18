import { useEffect, useRef, useState } from "react";
import { fetchChatRecord } from "../lib/api";
import { ApiError } from "../lib/api/http";
import { isStoredChat } from "../lib/chat-thread";
import type { ChatThread } from "../lib/chat-types";

type HistoryPage = { key: string; chat: ChatThread; nextBefore: string | null };

export function useChatHistoryPages(
  endpoint: string,
  chatId: string,
  sourceProjectPath: string | undefined,
  complete: ChatThread | null,
  updatedAt: string | undefined,
  enabled: boolean,
  sourceConfirmed: boolean,
  reportError: (cause: unknown) => void,
) {
  const key = JSON.stringify([endpoint, chatId, sourceProjectPath]);
  const [page, setPage] = useState<HistoryPage | null>(null);
  const [failedKey, setFailedKey] = useState("");
  const [speculativeFailureKey, setSpeculativeFailureKey] = useState("");
  const [refreshRevision, setRefreshRevision] = useState(0);
  const observedSource = useRef({ key, sourceConfirmed, updatedAt });
  const request = useRef<AbortController | null>(null);
  const olderPending = useRef(false);
  const onError = useRef(reportError);
  onError.current = reportError;
  const confirmed = useRef(sourceConfirmed);
  confirmed.current = sourceConfirmed;
  const retryConfirmedSource = sourceConfirmed && speculativeFailureKey === key;
  const visible = page?.key === key ? page : null;
  const hasCompleteHistory = complete !== null;

  useEffect(() => {
    const previous = observedSource.current;
    observedSource.current = { key, sourceConfirmed, updatedAt };
    // The first catalog confirmation can reuse a page from the same source.
    // Later changes refresh the active page when no complete record is loaded.
    if (!hasCompleteHistory && sourceConfirmed && previous.sourceConfirmed
      && previous.key === key && previous.updatedAt !== updatedAt) {
      setRefreshRevision((revision) => revision + 1);
    }
  }, [hasCompleteHistory, key, sourceConfirmed, updatedAt]);

  useEffect(() => {
    if (!enabled || !chatId || sourceProjectPath === undefined) return;
    const controller = new AbortController();
    request.current = controller;
    olderPending.current = false;
    void fetchChatRecord(endpoint, chatId, sourceProjectPath, { signal: controller.signal }).then((result) => {
      if (controller.signal.aborted) return;
      const chat = result.chats[0];
      if (!isStoredChat(chat) || chat.id !== chatId) throw new Error("Chat history is unavailable.");
      setFailedKey("");
      setPage({ key, chat, nextBefore: result.nextBefore });
    }).catch((cause) => {
      if (controller.signal.aborted) return;
      if (!confirmed.current) {
        setSpeculativeFailureKey(key);
        return;
      }
      setFailedKey(key);
      onError.current(cause);
    });
    return () => controller.abort();
  }, [chatId, enabled, endpoint, key, refreshRevision, retryConfirmedSource, sourceProjectPath]);

  const firstVisibleId = visible?.chat.items[0]?.id;
  const firstCompleteIndex = firstVisibleId && complete
    ? complete.items.findIndex((item) => item.id === firstVisibleId)
    : -1;
  const chat = complete && visible
    ? { ...complete, items: complete.items.slice(firstCompleteIndex < 0 ? -visible.chat.items.length : firstCompleteIndex) }
    : visible?.chat ?? complete;

  async function loadOlder(): Promise<boolean> {
    const controller = request.current;
    if (!sourceConfirmed || !visible?.nextBefore || !controller || controller.signal.aborted || olderPending.current
      || sourceProjectPath === undefined) return false;
    olderPending.current = true;
    try {
      // If the execution path already has the complete record, reuse its items.
      if (complete && firstCompleteIndex > 0) {
        const start = Math.max(0, firstCompleteIndex - 12);
        setPage({ ...visible, chat: { ...complete, items: complete.items.slice(start) }, nextBefore: start ? visible.nextBefore : null });
        return true;
      }
      const result = await fetchChatRecord(endpoint, chatId, sourceProjectPath, {
        before: visible.nextBefore, signal: controller.signal,
      });
      const older = result.chats[0];
      if (controller.signal.aborted || !isStoredChat(older)) return false;
      setPage({
        key,
        nextBefore: result.nextBefore,
        chat: {
          ...visible.chat,
          items: [...older.items, ...visible.chat.items],
          attachmentPayloads: { ...older.attachmentPayloads, ...visible.chat.attachmentPayloads },
        },
      });
      return older.items.length > 0;
    } catch (cause) {
      if (!controller.signal.aborted && cause instanceof ApiError && cause.status === 409) {
        try {
          const result = await fetchChatRecord(endpoint, chatId, sourceProjectPath, { signal: controller.signal });
          const latest = result.chats[0];
          if (!controller.signal.aborted && isStoredChat(latest)) {
            setPage({ key, chat: latest, nextBefore: result.nextBefore });
            return false;
          }
        } catch (refreshCause) {
          if (!controller.signal.aborted) onError.current(refreshCause);
          return false;
        }
      }
      if (!controller.signal.aborted) onError.current(cause);
      return false;
    } finally {
      if (request.current === controller) olderPending.current = false;
    }
  }

  return { chat, pending: Boolean(chatId && !chat && failedKey !== key), loadOlder, hasOlder: sourceConfirmed && Boolean(visible?.nextBefore) };
}
