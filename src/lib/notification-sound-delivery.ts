import { playAgentQuestionNotificationSound, type AgentQuestionNotificationSoundKind } from "./agent-question-notification-sound";

const STORAGE_KEY = "vrcforge.notification-sound-played.v1";
const played = new Set<string>();
const pending = new Map<string, Promise<boolean>>();

/** Sound delivery is independent of Toast delivery: a failed Toast can retry silently. */
export async function playNotificationSoundOnce(id: string, kind: AgentQuestionNotificationSoundKind): Promise<boolean> {
  try {
    const saved: unknown = JSON.parse(window.localStorage.getItem(STORAGE_KEY) || "[]");
    if (Array.isArray(saved)) for (const key of saved) if (typeof key === "string") played.add(key);
  } catch { /* Keep in-memory delivery state when storage is unavailable. */ }
  if (played.has(id)) return true;
  const existing = pending.get(id);
  if (existing) return existing;
  const attempt = playAgentQuestionNotificationSound(kind).then((success) => {
    if (success) {
      played.add(id);
      try { window.localStorage.setItem(STORAGE_KEY, JSON.stringify([...played])); } catch { /* Retain in memory. */ }
    }
    return success;
  }).finally(() => pending.delete(id));
  pending.set(id, attempt);
  return attempt;
}
