import {
  isPermissionGranted,
  requestPermission,
  sendNotification,
} from "@tauri-apps/plugin-notification";
import { hasTauriInternals } from "./api/http";

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

export async function notifyBackgroundGoal(
  event: BackgroundGoalNotificationEvent,
  enabled: boolean,
  translate: BackgroundGoalNotificationTranslator,
): Promise<boolean> {
  const copy = enabled ? backgroundGoalNotificationCopy(event, translate) : null;
  if (!copy || !hasTauriInternals()) {
    return false;
  }
  try {
    let granted = await isPermissionGranted();
    if (!granted) {
      granted = (await requestPermission()) === "granted";
    }
    if (!granted) {
      return false;
    }
    sendNotification(copy);
    return true;
  } catch {
    return false;
  }
}
