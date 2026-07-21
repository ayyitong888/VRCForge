import { useCallback, useEffect, useRef, useState } from "react";
import { useTranslation } from "react-i18next";
import type {
  AgentGoal,
  AgentGoalBackgroundAcknowledgement,
  AgentGoalBackgroundState,
  AgentGoalDelivery,
  AgentGoalProviderWarning,
  AgentGoalRenderedRecap,
} from "../lib/api";
import {
  acknowledgeAgentGoalBackgroundState,
  fetchAgentGoalBackgroundState,
} from "../lib/api";
import { notifyBackgroundGoal } from "../lib/background-goal-notifications";
import { useGoalWake } from "./use-goal-wake";

const BACKGROUND_STATE_POLL_MS = 30_000;

function optionalRevision(value: unknown): number | null {
  const revision = Number(value);
  return Number.isInteger(revision) && revision >= 0 ? revision : null;
}

function currentRevision(delivery: AgentGoalDelivery): number {
  return optionalRevision(delivery.revision) ?? 0;
}

function recapRevision(delivery: AgentGoalDelivery): number {
  return optionalRevision(delivery.recapRevision) ?? currentRevision(delivery);
}

function toastRevision(delivery: AgentGoalDelivery): number {
  return optionalRevision(delivery.toastRevision) ?? recapRevision(delivery);
}

function revisionKey(deliveryId: string, revision: number): string {
  return `${deliveryId}:${revision}`;
}

function recapKey(delivery: AgentGoalDelivery): string {
  return revisionKey(delivery.deliveryId, recapRevision(delivery));
}

function toastKey(delivery: AgentGoalDelivery): string {
  return revisionKey(delivery.deliveryId, toastRevision(delivery));
}

function providerWarningRevision(warning: AgentGoalProviderWarning): number {
  return optionalRevision(warning.count) ?? 0;
}

function providerWarningKey(warning: AgentGoalProviderWarning): string {
  return revisionKey(warning.warningKey, providerWarningRevision(warning));
}

function providerWarningAlreadyAcknowledged(warning: AgentGoalProviderWarning): boolean {
  const acknowledgedRevision = optionalRevision(warning.acknowledgedRevision);
  return acknowledgedRevision === null
    ? Boolean(warning.acknowledgedAt)
    : acknowledgedRevision >= providerWarningRevision(warning);
}

function recapAlreadySeen(delivery: AgentGoalDelivery): boolean {
  const seenRevision = optionalRevision(delivery.recapSeenRevision);
  return seenRevision === null
    ? Boolean(delivery.recapSeenAt)
    : seenRevision >= recapRevision(delivery);
}

function toastAlreadySent(delivery: AgentGoalDelivery): boolean {
  const sentRevision = optionalRevision(delivery.toastSentRevision);
  return sentRevision !== null && sentRevision >= toastRevision(delivery);
}

function isQuietSuccess(delivery: AgentGoalDelivery): boolean {
  const status = String(delivery.state || delivery.status || "").trim().toLowerCase();
  return status === "completed" || status === "materialized";
}

function isMaterializedSuccess(delivery: AgentGoalDelivery): boolean {
  return String(delivery.state || delivery.status || "").trim().toLowerCase() === "materialized";
}

function deliveryEventTimestamp(delivery: AgentGoalDelivery): number | null {
  for (const value of [delivery.completedAt, delivery.materializedAt, delivery.updatedAt]) {
    const timestamp = Date.parse(String(value || ""));
    if (Number.isFinite(timestamp)) {
      return timestamp;
    }
  }
  return null;
}

function findDelivery(state: AgentGoalBackgroundState, deliveryId: string): AgentGoalDelivery | undefined {
  return [...(state.unread || []), ...(state.recent || []), ...(state.deliveries || [])]
    .find((delivery) => delivery.deliveryId === deliveryId);
}

function responseConfirmsToast(
  state: AgentGoalBackgroundState,
  deliveryId: string,
  expectedToastRevision: number,
): boolean {
  const delivery = findDelivery(state, deliveryId);
  const sentRevision = optionalRevision(delivery?.toastSentRevision);
  return sentRevision !== null && sentRevision >= expectedToastRevision;
}

function mergeAcknowledgementStatePreservingDisplayed(
  current: AgentGoalBackgroundState,
  acknowledged: AgentGoalBackgroundState,
  ownerChatId: string,
): AgentGoalBackgroundState {
  return {
    ...current,
    unread: [
      ...(acknowledged.unread || []),
      ...(current.unread || []).filter((delivery) => delivery.chatId !== ownerChatId),
    ],
    unreadByChat: acknowledged.unreadByChat,
    totalUnread: acknowledged.totalUnread,
  };
}

export function useBackgroundGoalRuns({
  endpoint,
  runtimeConnected,
  chatAvailable,
  ownerChatVisible,
  activeChatId,
  refreshSignal,
  notificationsEnabled,
  onGoalDelivery,
}: {
  endpoint: string;
  runtimeConnected: boolean;
  chatAvailable: boolean;
  ownerChatVisible: boolean;
  activeChatId: string;
  refreshSignal: number;
  notificationsEnabled: boolean;
  onGoalDelivery: (
    goal: AgentGoal | null,
    delivery: AgentGoalDelivery,
  ) => "persisted" | "retry" | Promise<"persisted" | "retry">;
}): {
  state: AgentGoalBackgroundState | null;
  onCatchUpRendered: (recaps: AgentGoalRenderedRecap[]) => void;
  onProviderWarningsRendered: (warnings: AgentGoalBackgroundAcknowledgement[]) => void;
  dismissCatchUp: () => void;
} {
  const { t } = useTranslation();
  const [state, setState] = useState<AgentGoalBackgroundState | null>(null);
  const stateRef = useRef<AgentGoalBackgroundState | null>(null);
  const displayedChatId = useRef(activeChatId);
  const displayedRecaps = useRef(new Map<string, AgentGoalDelivery>());
  const displayedProviderWarnings = useRef(new Map<string, AgentGoalProviderWarning>());
  const dismissedRecapKeys = useRef(new Set<string>());
  const dismissedProviderWarningKeys = useRef(new Set<string>());
  const initiallyAttended = ownerChatVisible
    && document.visibilityState === "visible"
    && document.hasFocus();
  const ownerChatAttendedRef = useRef(initiallyAttended);
  const ownerChatAttendedSinceRef = useRef(initiallyAttended ? Date.now() : 0);
  const renderedRecaps = useRef(new Map<string, AgentGoalBackgroundAcknowledgement>());
  const acknowledgedRecapKeys = useRef(new Set<string>());
  const recapAcknowledgementInFlight = useRef(false);
  const quietSuccessAckInFlightKeys = useRef(new Set<string>());
  const renderedProviderWarnings = useRef(new Map<string, AgentGoalBackgroundAcknowledgement>());
  const acknowledgedProviderWarningKeys = useRef(new Set<string>());
  const providerAcknowledgementInFlight = useRef(false);
  const toastInFlightKeys = useRef(new Set<string>());
  const confirmedToastKeys = useRef(new Set<string>());

  const commitState = useCallback((next: AgentGoalBackgroundState | null) => {
    stateRef.current = next;
    setState(next);
  }, []);

  const acknowledgeRenderedRecaps = useCallback(async () => {
    if (
      !runtimeConnected
      || !activeChatId
      || recapAcknowledgementInFlight.current
      || document.visibilityState !== "visible"
      || !document.hasFocus()
    ) {
      return;
    }
    const mountedKeys = new Set(
      Array.from(document.querySelectorAll<HTMLElement>("[data-background-goal-recap-key]"))
        .filter((element) => element.isConnected && element.getClientRects().length > 0)
        .map((element) => String(element.dataset.backgroundGoalRecapKey || ""))
        .filter(Boolean),
    );
    const candidates = (stateRef.current?.recent || [])
      .flatMap((delivery) => {
        const key = recapKey(delivery);
        const rendered = renderedRecaps.current.get(key);
        const revision = currentRevision(delivery);
        return delivery.chatId === activeChatId
          && !recapAlreadySeen(delivery)
          && rendered?.expectedRevision === revision
          && mountedKeys.has(key)
          && !acknowledgedRecapKeys.current.has(key)
          ? [{ key, acknowledgement: rendered }]
          : [];
      });
    if (!candidates.length) {
      return;
    }

    recapAcknowledgementInFlight.current = true;
    try {
      const acknowledged = await acknowledgeAgentGoalBackgroundState(endpoint, {
        chatId: activeChatId,
        kind: "recap",
        deliveries: candidates.map(({ acknowledgement }) => acknowledgement),
      });
      const remainingKeys = new Set((acknowledged.recent || []).map(recapKey));
      for (const { key } of candidates) {
        if (!remainingKeys.has(key)) {
          acknowledgedRecapKeys.current.add(key);
        }
      }
      setState((current) => {
        if (!current) {
          return current;
        }
        const next = mergeAcknowledgementStatePreservingDisplayed(current, acknowledged, activeChatId);
        stateRef.current = next;
        return next;
      });
    } catch {
      // A later poll, focus, or visibility event retries the revision-bound ACK.
    } finally {
      recapAcknowledgementInFlight.current = false;
    }
  }, [activeChatId, endpoint, runtimeConnected]);

  const onCatchUpRendered = useCallback((recaps: AgentGoalRenderedRecap[]) => {
    for (const recap of recaps) {
      if (recap.deliveryId) {
        renderedRecaps.current.set(
          revisionKey(recap.deliveryId, recap.recapRevision),
          { deliveryId: recap.deliveryId, expectedRevision: recap.expectedRevision },
        );
      }
    }
    window.requestAnimationFrame(() => void acknowledgeRenderedRecaps());
  }, [acknowledgeRenderedRecaps]);

  const acknowledgeQuietSuccesses = useCallback((deliveries: AgentGoalDelivery[]) => {
    if (!runtimeConnected || !activeChatId) {
      return;
    }
    const candidates = deliveries.flatMap((delivery) => {
      const key = recapKey(delivery);
      return !recapAlreadySeen(delivery)
        && !quietSuccessAckInFlightKeys.current.has(key)
        && !acknowledgedRecapKeys.current.has(key)
        ? [{
            key,
            acknowledgement: {
              deliveryId: delivery.deliveryId,
              expectedRevision: currentRevision(delivery),
            },
          }]
        : [];
    });
    if (!candidates.length) {
      return;
    }
    candidates.forEach(({ key }) => quietSuccessAckInFlightKeys.current.add(key));
    void (async () => {
      try {
        const acknowledged = await acknowledgeAgentGoalBackgroundState(endpoint, {
          chatId: activeChatId,
          kind: "recap",
          deliveries: candidates.map(({ acknowledgement }) => acknowledgement),
        });
        const remainingKeys = new Set((acknowledged.recent || []).map(recapKey));
        for (const { key } of candidates) {
          if (!remainingKeys.has(key)) {
            acknowledgedRecapKeys.current.add(key);
          }
        }
        setState((current) => {
          if (!current) {
            return current;
          }
          const next = mergeAcknowledgementStatePreservingDisplayed(current, acknowledged, activeChatId);
          stateRef.current = next;
          return next;
        });
      } catch {
        // A later fine-grained poll retries a quiet-success acknowledgement.
      } finally {
        candidates.forEach(({ key }) => quietSuccessAckInFlightKeys.current.delete(key));
      }
    })();
  }, [activeChatId, endpoint, runtimeConnected]);

  const acknowledgeRenderedProviderWarnings = useCallback(async () => {
    if (
      !runtimeConnected
      || !activeChatId
      || providerAcknowledgementInFlight.current
      || document.visibilityState !== "visible"
      || !document.hasFocus()
    ) {
      return;
    }
    const mountedKeys = new Set(
      Array.from(document.querySelectorAll<HTMLElement>("[data-background-goal-provider-warning-key]"))
        .filter((element) => element.isConnected && element.getClientRects().length > 0)
        .map((element) => String(element.dataset.backgroundGoalProviderWarningKey || ""))
        .filter(Boolean),
    );
    const candidates = (stateRef.current?.providerWarnings || []).flatMap((warning) => {
      const key = providerWarningKey(warning);
      const rendered = renderedProviderWarnings.current.get(key);
      return !providerWarningAlreadyAcknowledged(warning)
        && rendered?.expectedRevision === providerWarningRevision(warning)
        && mountedKeys.has(key)
        && !acknowledgedProviderWarningKeys.current.has(key)
        ? [{ key, acknowledgement: rendered }]
        : [];
    });
    if (!candidates.length) {
      return;
    }

    providerAcknowledgementInFlight.current = true;
    try {
      const acknowledged = await acknowledgeAgentGoalBackgroundState(endpoint, {
        chatId: activeChatId,
        kind: "provider",
        deliveries: candidates.map(({ acknowledgement }) => acknowledgement),
      });
      const remainingKeys = new Set((acknowledged.providerWarnings || []).map(providerWarningKey));
      for (const { key } of candidates) {
        if (!remainingKeys.has(key)) {
          acknowledgedProviderWarningKeys.current.add(key);
        }
      }
      setState((current) => {
        if (!current) {
          return current;
        }
        const next = mergeAcknowledgementStatePreservingDisplayed(current, acknowledged, activeChatId);
        stateRef.current = next;
        return next;
      });
    } catch {
      // A later poll or attention event retries the revision-bound warning ACK.
    } finally {
      providerAcknowledgementInFlight.current = false;
    }
  }, [activeChatId, endpoint, runtimeConnected]);

  const onProviderWarningsRendered = useCallback((warnings: AgentGoalBackgroundAcknowledgement[]) => {
    for (const warning of warnings) {
      if (warning.deliveryId) {
        renderedProviderWarnings.current.set(
          revisionKey(warning.deliveryId, warning.expectedRevision),
          warning,
        );
      }
    }
    window.requestAnimationFrame(() => void acknowledgeRenderedProviderWarnings());
  }, [acknowledgeRenderedProviderWarnings]);

  const dismissCatchUp = useCallback(() => {
    const mountedRecapKeys = new Set(
      Array.from(document.querySelectorAll<HTMLElement>("[data-background-goal-recap-key]"))
        .map((element) => String(element.dataset.backgroundGoalRecapKey || ""))
        .filter(Boolean),
    );
    const mountedProviderWarningKeys = new Set(
      Array.from(document.querySelectorAll<HTMLElement>("[data-background-goal-provider-warning-key]"))
        .map((element) => String(element.dataset.backgroundGoalProviderWarningKey || ""))
        .filter(Boolean),
    );
    const dismissedDeliveries = [...displayedRecaps.current.values()].filter(
      (delivery) => delivery.chatId === activeChatId && mountedRecapKeys.has(recapKey(delivery)),
    );
    const dismissedWarnings = [...displayedProviderWarnings.current.values()].filter(
      (warning) => mountedProviderWarningKeys.has(providerWarningKey(warning)),
    );
    const recapAcknowledgements = dismissedDeliveries
      .filter((delivery) => !recapAlreadySeen(delivery))
      .map((delivery) => ({
        deliveryId: delivery.deliveryId,
        expectedRevision: currentRevision(delivery),
      }));
    const providerAcknowledgements = dismissedWarnings
      .filter((warning) => !providerWarningAlreadyAcknowledged(warning))
      .map((warning) => ({
        deliveryId: warning.warningKey,
        expectedRevision: providerWarningRevision(warning),
      }));
    for (const delivery of dismissedDeliveries) {
      const key = recapKey(delivery);
      dismissedRecapKeys.current.add(key);
      renderedRecaps.current.delete(key);
      displayedRecaps.current.delete(delivery.deliveryId);
    }
    for (const warning of dismissedWarnings) {
      const key = providerWarningKey(warning);
      dismissedProviderWarningKeys.current.add(key);
      renderedProviderWarnings.current.delete(key);
      displayedProviderWarnings.current.delete(warning.warningKey);
    }
    setState((current) => {
      if (!current) {
        return current;
      }
      const next = {
        ...current,
        recent: (current.recent || []).filter((delivery) => !mountedRecapKeys.has(recapKey(delivery))),
        deliveries: (current.deliveries || []).filter((delivery) => !mountedRecapKeys.has(recapKey(delivery))),
        providerWarnings: (current.providerWarnings || []).filter(
          (warning) => !mountedProviderWarningKeys.has(providerWarningKey(warning)),
        ),
      };
      stateRef.current = next;
      return next;
    });
    if (!runtimeConnected || !activeChatId || (!recapAcknowledgements.length && !providerAcknowledgements.length)) {
      return;
    }
    void (async () => {
      let latestAcknowledgement: AgentGoalBackgroundState | null = null;
      if (recapAcknowledgements.length) {
        try {
          latestAcknowledgement = await acknowledgeAgentGoalBackgroundState(endpoint, {
            chatId: activeChatId,
            kind: "recap",
            deliveries: recapAcknowledgements,
          });
        } catch {
          // A failed durable dismiss may reappear after restart; the current session stays dismissed.
        }
      }
      if (providerAcknowledgements.length) {
        try {
          latestAcknowledgement = await acknowledgeAgentGoalBackgroundState(endpoint, {
            chatId: activeChatId,
            kind: "provider",
            deliveries: providerAcknowledgements,
          });
        } catch {
          // A failed durable dismiss may reappear after restart; the current session stays dismissed.
        }
      }
      if (latestAcknowledgement) {
        setState((current) => {
          if (!current) {
            return current;
          }
          const next = mergeAcknowledgementStatePreservingDisplayed(
            current,
            latestAcknowledgement,
            activeChatId,
          );
          stateRef.current = next;
          return next;
        });
      }
    })();
  }, [activeChatId, endpoint, runtimeConnected]);

  const notifyUnreadDeliveries = useCallback((deliveries: AgentGoalDelivery[]) => {
    const ownerChatAttended = ownerChatVisible
      && document.visibilityState === "visible"
      && document.hasFocus();
    for (const delivery of deliveries) {
      if (!delivery.deliveryId || toastAlreadySent(delivery)) {
        continue;
      }
      if (delivery.chatId === activeChatId && ownerChatAttended) {
        continue;
      }
      const key = toastKey(delivery);
      if (toastInFlightKeys.current.has(key) || confirmedToastKeys.current.has(key)) {
        continue;
      }
      toastInFlightKeys.current.add(key);
      void (async () => {
        try {
          const sent = await notifyBackgroundGoal(delivery, notificationsEnabled, t);
          if (!sent) {
            return;
          }
          const acknowledged = await acknowledgeAgentGoalBackgroundState(endpoint, {
            chatId: delivery.chatId,
            kind: "toast",
            deliveries: [{
              deliveryId: delivery.deliveryId,
              expectedRevision: currentRevision(delivery),
            }],
          });
          if (responseConfirmsToast(acknowledged, delivery.deliveryId, toastRevision(delivery))) {
            confirmedToastKeys.current.add(key);
          }
        } catch {
          // Without a durable confirmation, a later poll may safely retry this revision.
        } finally {
          toastInFlightKeys.current.delete(key);
        }
      })();
    }
  }, [activeChatId, endpoint, notificationsEnabled, ownerChatVisible, t]);

  useGoalWake({
    endpoint,
    runtimeConnected,
    chatAvailable,
    onGoalDelivery,
  });

  useEffect(() => {
    const attended = ownerChatVisible
      && document.visibilityState === "visible"
      && document.hasFocus();
    ownerChatAttendedRef.current = attended;
    ownerChatAttendedSinceRef.current = attended ? Date.now() : 0;
  }, [activeChatId, ownerChatVisible]);

  useEffect(() => {
    if (displayedChatId.current === activeChatId) {
      return;
    }
    displayedChatId.current = activeChatId;
    displayedRecaps.current.clear();
    displayedProviderWarnings.current.clear();
    dismissedRecapKeys.current.clear();
    dismissedProviderWarningKeys.current.clear();
    setState((current) => {
      if (!current) {
        return current;
      }
      const next = {
        ...current,
        recent: [],
        deliveries: [],
        providerWarnings: [],
      };
      stateRef.current = next;
      return next;
    });
  }, [activeChatId]);

  useEffect(() => {
    if (!runtimeConnected) {
      commitState(null);
      return;
    }
    let cancelled = false;

    async function refresh() {
      try {
        const next = await fetchAgentGoalBackgroundState(endpoint);
        if (cancelled) {
          return;
        }
        const retainedRecapKeys = new Set((next.recent || []).map(recapKey));
        renderedRecaps.current = new Map(
          [...renderedRecaps.current].filter(([key]) => retainedRecapKeys.has(key)),
        );
        acknowledgedRecapKeys.current = new Set(
          [...acknowledgedRecapKeys.current].filter((key) => retainedRecapKeys.has(key)),
        );
        const retainedProviderWarningKeys = new Set((next.providerWarnings || []).map(providerWarningKey));
        renderedProviderWarnings.current = new Map(
          [...renderedProviderWarnings.current].filter(([key]) => retainedProviderWarningKeys.has(key)),
        );
        acknowledgedProviderWarningKeys.current = new Set(
          [...acknowledgedProviderWarningKeys.current].filter((key) => retainedProviderWarningKeys.has(key)),
        );
        const unreadByToastKey = new Map((next.unread || []).map((delivery) => [toastKey(delivery), delivery]));
        confirmedToastKeys.current = new Set(
          [...confirmedToastKeys.current].filter((key) => {
            const delivery = unreadByToastKey.get(key);
            return Boolean(delivery && !toastAlreadySent(delivery));
          }),
        );
        const attendedNow = ownerChatVisible
          && document.visibilityState === "visible"
          && document.hasFocus();
        if (attendedNow && !ownerChatAttendedRef.current) {
          ownerChatAttendedSinceRef.current = Date.now();
        }
        ownerChatAttendedRef.current = attendedNow;
        if (!attendedNow) {
          ownerChatAttendedSinceRef.current = 0;
        }
        const candidateActiveRecaps = (next.recent || []).filter(
          (delivery) => delivery.chatId === activeChatId
            && !dismissedRecapKeys.current.has(recapKey(delivery)),
        );
        const quietAttendedSuccesses = candidateActiveRecaps.filter((delivery) => {
          const eventTimestamp = deliveryEventTimestamp(delivery);
          return isQuietSuccess(delivery)
            && attendedNow
            && !displayedRecaps.current.has(delivery.deliveryId)
            && eventTimestamp !== null
            && eventTimestamp >= ownerChatAttendedSinceRef.current;
        });
        const quietAttendedKeys = new Set(quietAttendedSuccesses.map(recapKey));
        const nextActiveRecaps = candidateActiveRecaps.filter(
          (delivery) => !quietAttendedKeys.has(recapKey(delivery)),
        );
        acknowledgeQuietSuccesses(quietAttendedSuccesses.filter(isMaterializedSuccess));
        displayedRecaps.current = new Map([
          ...nextActiveRecaps.map((delivery) => [delivery.deliveryId, delivery] as const),
          ...[...displayedRecaps.current].filter(
            ([deliveryId]) => !nextActiveRecaps.some((delivery) => delivery.deliveryId === deliveryId),
          ),
        ]);
        const nextProviderWarnings = (next.providerWarnings || []).filter(
          (warning) => !dismissedProviderWarningKeys.current.has(providerWarningKey(warning)),
        );
        displayedProviderWarnings.current = new Map([
          ...nextProviderWarnings.map((warning) => [warning.warningKey, warning] as const),
          ...[...displayedProviderWarnings.current].filter(
            ([warningKey]) => !nextProviderWarnings.some((warning) => warning.warningKey === warningKey),
          ),
        ]);
        const displayed = {
          ...next,
          recent: [
            ...(next.recent || []).filter((delivery) => delivery.chatId !== activeChatId),
            ...displayedRecaps.current.values(),
          ],
          deliveries: [
            ...(next.deliveries || []).filter((delivery) => delivery.chatId !== activeChatId),
            ...displayedRecaps.current.values(),
          ],
          providerWarnings: [...displayedProviderWarnings.current.values()],
        };
        commitState(displayed);
        notifyUnreadDeliveries(next.unread || []);
        window.requestAnimationFrame(() => void acknowledgeRenderedRecaps());
        window.requestAnimationFrame(() => void acknowledgeRenderedProviderWarnings());
      } catch {
        // Durable backend state remains authoritative and will be retried.
      }
    }

    const initialTimer = window.setTimeout(() => void refresh(), 1_000);
    const timer = window.setInterval(() => void refresh(), BACKGROUND_STATE_POLL_MS);
    return () => {
      cancelled = true;
      window.clearTimeout(initialTimer);
      window.clearInterval(timer);
    };
  }, [
    acknowledgeRenderedRecaps,
    acknowledgeRenderedProviderWarnings,
    acknowledgeQuietSuccesses,
    commitState,
    endpoint,
    notifyUnreadDeliveries,
    ownerChatVisible,
    refreshSignal,
    runtimeConnected,
  ]);

  useEffect(() => {
    const handleAttentionChange = () => {
      const attended = ownerChatVisible
        && document.visibilityState === "visible"
        && document.hasFocus();
      if (attended && !ownerChatAttendedRef.current) {
        ownerChatAttendedSinceRef.current = Date.now();
      }
      ownerChatAttendedRef.current = attended;
      if (!attended) {
        ownerChatAttendedSinceRef.current = 0;
      }
      if (attended) {
        window.requestAnimationFrame(() => void acknowledgeRenderedRecaps());
        window.requestAnimationFrame(() => void acknowledgeRenderedProviderWarnings());
      } else {
        notifyUnreadDeliveries(stateRef.current?.unread || []);
      }
    };
    document.addEventListener("visibilitychange", handleAttentionChange);
    window.addEventListener("focus", handleAttentionChange);
    window.addEventListener("blur", handleAttentionChange);
    return () => {
      document.removeEventListener("visibilitychange", handleAttentionChange);
      window.removeEventListener("focus", handleAttentionChange);
      window.removeEventListener("blur", handleAttentionChange);
    };
  }, [acknowledgeRenderedProviderWarnings, acknowledgeRenderedRecaps, notifyUnreadDeliveries, ownerChatVisible]);

  const displayedState = displayedChatId.current === activeChatId
    ? state
    : state
      ? { ...state, recent: [], deliveries: [], providerWarnings: [] }
      : state;
  return { state: displayedState, onCatchUpRendered, onProviderWarningsRendered, dismissCatchUp };
}
