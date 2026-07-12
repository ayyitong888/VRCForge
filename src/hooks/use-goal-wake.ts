import { useEffect, useRef } from "react";
import { AgentGoal, fetchDueAgentGoals, updateAgentGoal, wakeAgentGoal } from "../lib/api";

/**
 * Goal 唤醒轮询：网关负责"哪些 goal 到点了"的持久判定，
 * 这个 hook 只负责在运行时在线时定期询问、消费一次唤醒，
 * 然后把 resumePrompt 交回 App 走既有的可见运行队列。
 * 每个周期最多唤醒一个 goal，避免重启后积压的计划一次性打爆运行队列。
 */
const GOAL_WAKE_POLL_INTERVAL_MS = 60_000;
const GOAL_WAKE_INITIAL_DELAY_MS = 5_000;
const GOAL_WAKE_RETRY_DELAY_MS = 60_000;

function parseGoalWakeMinutes(raw: string, unit: string): number {
  const magnitude = Number(raw);
  const multiplier = unit.toLowerCase().startsWith("h") ? 60 : 1;
  if (!Number.isSafeInteger(magnitude) || magnitude <= 0 || magnitude > Math.floor(Number.MAX_SAFE_INTEGER / multiplier)) {
    return Number.MAX_SAFE_INTEGER;
  }
  return magnitude * multiplier;
}

/**
 * 解析 /goal 标题尾部的唤醒指令：
 * - "… +30m" / "… +2h"         → 一次性 wakeAt（自现在起偏移）
 * - "… every 30m" / "every 2h" → 周期 wakeEveryMinutes
 * 未命中时原样返回标题，不带调度字段；间隔合法性由网关兜底校验。
 */
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
      // Preserve directive recognition and force a gateway 400 instead of
      // silently storing an invalid/overflowing suffix as part of the title.
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
  sending: boolean;
  onGoalWoken: (goal: AgentGoal, resumePrompt: string) => "dispatched" | "retry" | Promise<"dispatched" | "retry">;
};

export function useGoalWake({
  endpoint,
  runtimeConnected,
  chatAvailable,
  sending,
  onGoalWoken,
}: UseGoalWakeParams) {
  const busyRef = useRef(false);
  const sendingRef = useRef(sending);
  const pendingRetriesRef = useRef(new Map<string, AgentGoal>());
  const onGoalWokenRef = useRef(onGoalWoken);
  sendingRef.current = sending;
  onGoalWokenRef.current = onGoalWoken;

  useEffect(() => {
    if (!runtimeConnected || !chatAvailable) {
      return;
    }
    let cancelled = false;

    async function rearmGoal(goal: AgentGoal) {
      const lastWokenAt = Date.parse(goal.lastWokenAt || "");
      const retryBase = Number.isFinite(lastWokenAt) ? Math.max(Date.now(), lastWokenAt) : Date.now();
      await updateAgentGoal(endpoint, goal.goalId, {
        status: goal.status || "active",
        summary: goal.summary || "",
        wakeAt: new Date(retryBase + GOAL_WAKE_RETRY_DELAY_MS).toISOString(),
        sessionId: goal.sessionId || undefined,
        chatId: goal.chatId || undefined,
        projectRoot: goal.projectRoot || undefined,
      });
      pendingRetriesRef.current.delete(goal.goalId);
    }

    async function tick() {
      if (cancelled || busyRef.current || sendingRef.current) {
        return;
      }
      busyRef.current = true;
      try {
        const pendingRetry = pendingRetriesRef.current.values().next().value as AgentGoal | undefined;
        if (pendingRetry) {
          await rearmGoal(pendingRetry);
        }
        const due = await fetchDueAgentGoals(endpoint, { limit: 3 });
        const goal = due.goals?.[0];
        if (!goal?.goalId || cancelled || sendingRef.current) {
          return;
        }
        const woken = await wakeAgentGoal(endpoint, goal.goalId, {
          sessionId: goal.sessionId || undefined,
          chatId: goal.chatId || undefined,
          projectRoot: goal.projectRoot || undefined,
        });
        const resumed = woken.goal || goal;
        const prompt = (woken.resumePrompt || "").trim() || `Resume goal: ${resumed.title || ""}`.trim();
        let dispatchResult: "dispatched" | "retry" = "retry";
        try {
          dispatchResult = await onGoalWokenRef.current(resumed, prompt);
        } catch {
          dispatchResult = "retry";
        }
        if (dispatchResult === "retry") {
          pendingRetriesRef.current.set(resumed.goalId, resumed);
          try {
            await rearmGoal(resumed);
          } catch {
            // Keep the in-memory retry so the next poll can re-arm it.
          }
        }
      } catch {
        // 静默：网关不可达或并发唤醒冲突（409）都留给下一个周期重试。
      } finally {
        busyRef.current = false;
      }
    }

    const initialTimer = window.setTimeout(() => {
      void tick();
    }, GOAL_WAKE_INITIAL_DELAY_MS);
    const timer = window.setInterval(() => {
      void tick();
    }, GOAL_WAKE_POLL_INTERVAL_MS);
    return () => {
      cancelled = true;
      window.clearTimeout(initialTimer);
      window.clearInterval(timer);
    };
  }, [endpoint, runtimeConnected, chatAvailable]);
}
