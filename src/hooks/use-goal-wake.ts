import { useEffect, useRef } from "react";
import type { AgentGoal, AgentGoalDelivery } from "../lib/api";
import {
  deferAgentGoalDelivery,
  fetchDueAgentGoals,
  fetchRecoverableAgentGoalDeliveries,
  materializeAgentGoalDelivery,
  wakeAgentGoal,
} from "../lib/api";

const GOAL_WAKE_POLL_INTERVAL_MS = 5_000;
const GOAL_WAKE_INITIAL_DELAY_MS = 1_000;
const GOAL_WAKE_MAX_PARALLEL = 2;
const GOAL_WAKE_FETCH_LIMIT = 12;

function parseGoalWakeMinutes(raw: string, unit: string): number {
  const magnitude = Number(raw);
  const multiplier = unit.toLowerCase().startsWith("h") ? 60 : 1;
  if (!Number.isSafeInteger(magnitude) || magnitude <= 0 || magnitude > Math.floor(Number.MAX_SAFE_INTEGER / multiplier)) {
    return Number.MAX_SAFE_INTEGER;
  }
  return magnitude * multiplier;
}

export function parseGoalWakeDirective(raw: string): {
  title: string;
  wakeAt?: string;
  wakeEveryMinutes?: number;
} {
  const text = raw.trim();
  const recurring = text.match(/^(.*\S)\s+every\s+(\d+)\s*(m|min|h|hr)$/i);
  if (recurring) {
    return { title: recurring[1].trim(), wakeEveryMinutes: parseGoalWakeMinutes(recurring[2], recurring[3]) };
  }
  const oneShot = text.match(/^(.*\S)\s+\+(\d+)\s*(m|min|h|hr)$/i);
  if (oneShot) {
    const minutes = parseGoalWakeMinutes(oneShot[2], oneShot[3]);
    const wakeTimestamp = Date.now() + minutes * 60_000;
    if (!Number.isFinite(wakeTimestamp) || Math.abs(wakeTimestamp) > 8.64e15) {
      return { title: oneShot[1].trim(), wakeAt: "invalid-goal-wake-interval" };
    }
    return {
      title: oneShot[1].trim(),
      wakeAt: new Date(wakeTimestamp).toISOString(),
    };
  }
  return { title: text };
}

type UseGoalWakeParams = {
  endpoint: string;
  runtimeConnected: boolean;
  chatAvailable: boolean;
  onGoalDelivery: (
    goal: AgentGoal | null,
    delivery: AgentGoalDelivery,
  ) => "persisted" | "retry" | Promise<"persisted" | "retry">;
};

function eligibleTimestamp(item: AgentGoal | AgentGoalDelivery): number {
  const candidates = [
    item.eligibleAt,
    "retryAt" in item ? item.retryAt : undefined,
    "scheduledFor" in item ? item.scheduledFor : undefined,
    "wakeAt" in item ? item.wakeAt : undefined,
    item.createdAt,
  ];
  for (const value of candidates) {
    const timestamp = Date.parse(String(value || ""));
    if (Number.isFinite(timestamp)) {
      return timestamp;
    }
  }
  return Number.MAX_SAFE_INTEGER;
}

function compareEligible(left: AgentGoal | AgentGoalDelivery, right: AgentGoal | AgentGoalDelivery): number {
  const byTime = eligibleTimestamp(left) - eligibleTimestamp(right);
  if (byTime !== 0) {
    return byTime;
  }
  const leftId = "deliveryId" in left ? left.deliveryId : left.goalId;
  const rightId = "deliveryId" in right ? right.deliveryId : right.goalId;
  return String(leftId || "").localeCompare(String(rightId || ""));
}

export function useGoalWake({
  endpoint,
  runtimeConnected,
  chatAvailable,
  onGoalDelivery,
}: UseGoalWakeParams) {
  const schedulingRef = useRef(false);
  const activeDeliveryIdsRef = useRef(new Set<string>());
  const onGoalDeliveryRef = useRef(onGoalDelivery);
  onGoalDeliveryRef.current = onGoalDelivery;

  useEffect(() => {
    if (!runtimeConnected) {
      return;
    }
    let cancelled = false;

    async function persistAndAcknowledge(goal: AgentGoal | null, delivery: AgentGoalDelivery) {
      let result: "persisted" | "retry" = "retry";
      try {
        result = await onGoalDeliveryRef.current(goal, delivery);
      } catch {
        result = "retry";
      }
      if (result !== "persisted" || cancelled) {
        if (result === "retry") {
          await deferAgentGoalDelivery(endpoint, delivery.deliveryId, {
            expectedRevision: delivery.revision,
          });
        }
        return false;
      }
      await materializeAgentGoalDelivery(endpoint, delivery.deliveryId, {
        chatId: delivery.chatId,
      });
      return true;
    }

    function launchDelivery(goal: AgentGoal | null, delivery: AgentGoalDelivery): boolean {
      const deliveryId = String(delivery.deliveryId || "").trim();
      if (
        !deliveryId
        || activeDeliveryIdsRef.current.has(deliveryId)
        || activeDeliveryIdsRef.current.size >= GOAL_WAKE_MAX_PARALLEL
      ) {
        return false;
      }
      activeDeliveryIdsRef.current.add(deliveryId);
      void (async () => {
        try {
          await persistAndAcknowledge(goal, delivery);
        } catch {
          // Durable eligibility and delivery state are retried by a later tick.
        } finally {
          activeDeliveryIdsRef.current.delete(deliveryId);
        }
      })();
      return true;
    }

    async function tick() {
      if (cancelled || schedulingRef.current) {
        return;
      }
      let availableSlots = GOAL_WAKE_MAX_PARALLEL - activeDeliveryIdsRef.current.size;
      if (availableSlots <= 0) {
        return;
      }
      schedulingRef.current = true;
      try {
        const recoverable = await fetchRecoverableAgentGoalDeliveries(endpoint, {
          limit: GOAL_WAKE_FETCH_LIMIT,
        });
        for (const completed of [...(recoverable.deliveries || [])].sort(compareEligible)) {
          if (availableSlots <= 0) {
            break;
          }
          if (launchDelivery(null, completed)) {
            availableSlots -= 1;
          }
        }
        if (!chatAvailable || availableSlots <= 0) {
          return;
        }
        const due = await fetchDueAgentGoals(endpoint, { limit: GOAL_WAKE_FETCH_LIMIT });
        for (const goal of [...(due.goals || [])].sort(compareEligible)) {
          if (availableSlots <= 0 || cancelled) {
            break;
          }
          if (!goal?.goalId) {
            continue;
          }
          const woken = await wakeAgentGoal(endpoint, goal.goalId, {
            sessionId: goal.sessionId || undefined,
            chatId: goal.chatId || undefined,
            projectRoot: goal.projectRoot || undefined,
          });
          if (woken.delivery?.deliveryId && launchDelivery(woken.goal || goal, woken.delivery)) {
            availableSlots -= 1;
          }
        }
      } catch {
        // The next fine-grained tick retries transient gateway or claim conflicts.
      } finally {
        schedulingRef.current = false;
      }
    }

    const initialTimer = window.setTimeout(() => void tick(), GOAL_WAKE_INITIAL_DELAY_MS);
    const timer = window.setInterval(() => void tick(), GOAL_WAKE_POLL_INTERVAL_MS);
    return () => {
      cancelled = true;
      window.clearTimeout(initialTimer);
      window.clearInterval(timer);
    };
  }, [endpoint, runtimeConnected, chatAvailable]);
}
