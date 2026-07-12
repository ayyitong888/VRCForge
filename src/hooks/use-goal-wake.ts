import { useEffect, useRef } from "react";
import { AgentGoal, fetchDueAgentGoals, wakeAgentGoal } from "../lib/api";

/**
 * Goal 唤醒轮询：网关负责"哪些 goal 到点了"的持久判定，
 * 这个 hook 只负责在运行时在线时定期询问、消费一次唤醒，
 * 然后把 resumePrompt 交回 App 走既有的可见运行队列。
 * 每个周期最多唤醒一个 goal，避免重启后积压的计划一次性打爆运行队列。
 */
const GOAL_WAKE_POLL_INTERVAL_MS = 60_000;
const GOAL_WAKE_INITIAL_DELAY_MS = 5_000;

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
  const recurring = text.match(/^(.*\S)\s+every\s+(\d{1,4})\s*(m|min|h|hr)$/i);
  if (recurring) {
    const unit = recurring[3].toLowerCase().startsWith("h") ? 60 : 1;
    return { title: recurring[1].trim(), wakeEveryMinutes: Number(recurring[2]) * unit };
  }
  const oneShot = text.match(/^(.*\S)\s+\+(\d{1,4})\s*(m|min|h|hr)$/i);
  if (oneShot) {
    const unit = oneShot[3].toLowerCase().startsWith("h") ? 60 : 1;
    const minutes = Number(oneShot[2]) * unit;
    return {
      title: oneShot[1].trim(),
      wakeAt: new Date(Date.now() + minutes * 60_000).toISOString(),
    };
  }
  return { title: text };
}

type UseGoalWakeParams = {
  endpoint: string;
  runtimeConnected: boolean;
  chatAvailable: boolean;
  sending: boolean;
  sessionId: string;
  projectRoot: string;
  onGoalWoken: (goal: AgentGoal, resumePrompt: string) => void | Promise<void>;
};

export function useGoalWake({
  endpoint,
  runtimeConnected,
  chatAvailable,
  sending,
  sessionId,
  projectRoot,
  onGoalWoken,
}: UseGoalWakeParams) {
  const busyRef = useRef(false);
  const sendingRef = useRef(sending);
  const onGoalWokenRef = useRef(onGoalWoken);
  sendingRef.current = sending;
  onGoalWokenRef.current = onGoalWoken;

  useEffect(() => {
    if (!runtimeConnected || !chatAvailable) {
      return;
    }
    let cancelled = false;

    async function tick() {
      if (cancelled || busyRef.current || sendingRef.current) {
        return;
      }
      busyRef.current = true;
      try {
        const due = await fetchDueAgentGoals(endpoint, {
          limit: 3,
          sessionId: sessionId || undefined,
          projectRoot: projectRoot || undefined,
        });
        const goal = due.goals?.[0];
        if (!goal?.goalId || cancelled) {
          return;
        }
        const woken = await wakeAgentGoal(endpoint, goal.goalId, {
          sessionId: sessionId || undefined,
          projectRoot: projectRoot || undefined,
        });
        if (cancelled) {
          return;
        }
        const resumed = woken.goal || goal;
        const prompt = (woken.resumePrompt || "").trim() || `Resume goal: ${resumed.title || ""}`.trim();
        await onGoalWokenRef.current(resumed, prompt);
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
  }, [endpoint, runtimeConnected, chatAvailable, sessionId, projectRoot]);
}
