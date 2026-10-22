import { hasTauriInternals } from "./api/http";
import { invoke } from "@tauri-apps/api/core";
import { playNotificationSoundOnce } from "./notification-sound-delivery";

export type BackgroundGoalNotificationEvent = {
  status?: string;
  state?: string;
  blockedKind?: string;
};

export type BackgroundGoalNotificationTranslator = (key: string) => string;

type BackgroundGoalNotificationBodyKey =
  | "goal.backgroundNotificationFailed"
  | "goal.backgroundNotificationDenied"
  | "goal.backgroundNotificationParked"
  | "goal.backgroundNotificationApproval"
  | "goal.backgroundNotificationQuestion";

export function backgroundGoalNotificationBodyKey(
  event: BackgroundGoalNotificationEvent,
): BackgroundGoalNotificationBodyKey | null {
  const status = String(event.state || event.status || "").trim().toLowerCase();
  const blockedKind = String(event.blockedKind || "").trim().toLowerCase();
  if (status === "failed") return "goal.backgroundNotificationFailed";
  if (status === "denied") return "goal.backgroundNotificationDenied";
  if (status === "parked" && blockedKind === "question") return "goal.backgroundNotificationParked";
  if (status === "blocked_approval" || (status === "blocked" && blockedKind === "approval")) {
    return "goal.backgroundNotificationApproval";
  }
  if (status === "blocked_question" || (status === "blocked" && blockedKind === "question")) {
    return "goal.backgroundNotificationQuestion";
  }
  return null;
}

export function backgroundGoalNotificationCopy(
  event: BackgroundGoalNotificationEvent,
  translate: BackgroundGoalNotificationTranslator,
): { title: string; body: string } | null {
  const bodyKey = backgroundGoalNotificationBodyKey(event);
  return bodyKey
    ? { title: translate("goal.backgroundNotificationTitle"), body: translate(bodyKey) }
    : null;
}

export function backgroundGoalNotificationIsBlocking(event: BackgroundGoalNotificationEvent): boolean {
  const status = String(event.state || event.status || "").trim().toLowerCase();
  const blockedKind = String(event.blockedKind || "").trim().toLowerCase();
  return status === "blocked_question" || status === "blocked_approval"
    || (status === "blocked" && (blockedKind === "question" || blockedKind === "approval"));
}

export async function notifyBackgroundGoal(
  event: BackgroundGoalNotificationEvent,
  enabled: boolean,
  translate: BackgroundGoalNotificationTranslator,
  deliveryKey: string,
): Promise<boolean> {
  const copy = enabled ? backgroundGoalNotificationCopy(event, translate) : null;
  if (!copy || !hasTauriInternals()) {
    return false;
  }
  try {
    const silent = await playNotificationSoundOnce(`goal:${deliveryKey}`, backgroundGoalNotificationIsBlocking(event) ? "blocking" : "ordinary");
    await invoke("show_background_goal_notification", { request: { ...copy, silent } });
    return true;
  } catch {
    return false;
  }
}
