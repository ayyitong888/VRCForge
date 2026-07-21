import assert from "node:assert/strict";
import fs from "node:fs";
import path from "node:path";
import { fileURLToPath } from "node:url";

const root = path.resolve(path.dirname(fileURLToPath(import.meta.url)), "..");
const read = (relative) => fs.readFileSync(path.join(root, relative), "utf8");

const hook = read("src/hooks/use-background-goal-runs.ts");
const notification = read("src/lib/background-goal-notifications.ts");
const card = read("src/components/chat/background-goal-catch-up-card.tsx");
const runController = read("src/hooks/use-chat-run-controller.ts");
const goalWake = read("src/hooks/use-goal-wake.ts");
const app = read("src/App.tsx");
const apiTypes = read("src/lib/api/types.ts");
const chatWorkspace = read("src/components/chat/chat-workspace.tsx");
const sidebar = read("src/components/sidebar/sidebar.tsx");
const settings = read("src/components/settings/background-goal-settings.tsx");
const settingsWorkspace = read("src/components/settings/settings-workspace.tsx");
const api = read("src/lib/api/agent-runtime.ts");
const rustCommands = read("src-tauri/src/commands.rs");
const rustMain = read("src-tauri/src/main.rs");
const capability = JSON.parse(read("src-tauri/capabilities/main-window.json"));
const backend = read("dashboard_server.py");

assert.match(hook, /useGoalWake\(\{/);
assert.match(hook, /fetchAgentGoalBackgroundState\(endpoint\)/);
assert.match(hook, /delivery\.chatId === activeChatId/);
assert.match(hook, /acknowledgeAgentGoalBackgroundState/);
assert.match(hook, /notifyBackgroundGoal\(delivery, notificationsEnabled, t\)/);
assert.match(hook, /document\.visibilityState !== "visible"/);
assert.match(hook, /!document\.hasFocus\(\)/);
assert.match(hook, /document\.querySelectorAll<HTMLElement>\("\[data-background-goal-recap-key\]"\)/);
assert.match(hook, /revisionKey\(deliveryId: string, revision: number\)/);
assert.match(hook, /return revisionKey\(delivery\.deliveryId, recapRevision\(delivery\)\)/);
assert.match(hook, /rendered\?\.expectedRevision === revision/);
assert.match(hook, /mountedKeys\.has\(key\)/);
assert.match(hook, /kind: "recap"/);
assert.match(hook, /kind: "toast"/);
assert.match(hook, /kind: "provider"/);
assert.match(hook, /deliveries: candidates\.map/);
assert.match(hook, /toastSentRevision/);
assert.match(hook, /responseConfirmsToast/);
assert.match(hook, /if \(!sent\)/);
assert.match(hook, /delivery\.chatId === activeChatId && ownerChatAttended/);
assert.match(hook, /window\.addEventListener\("blur"/);
assert.doesNotMatch(hook, /seenNotifications/);
assert.match(hook, /document\.addEventListener\("visibilitychange"/);
assert.match(hook, /window\.addEventListener\("focus"/);
assert.match(hook, /onCatchUpRendered/);
assert.match(hook, /onProviderWarningsRendered/);
assert.match(hook, /data-background-goal-provider-warning-key/);
assert.match(hook, /displayedRecaps = useRef\(new Map<string, AgentGoalDelivery>\(\)\)/);
assert.match(hook, /displayedProviderWarnings = useRef\(new Map<string, AgentGoalProviderWarning>\(\)\)/);
assert.match(hook, /dismissedRecapKeys = useRef\(new Set<string>\(\)\)/);
assert.match(hook, /dismissedProviderWarningKeys = useRef\(new Set<string>\(\)\)/);
assert.match(hook, /displayedChatId\.current === activeChatId/);
assert.match(hook, /dismissCatchUp/);
assert.match(hook, /ownerChatVisible/);
assert.match(hook, /function isQuietSuccess/);
assert.match(hook, /function isMaterializedSuccess/);
assert.match(hook, /eventTimestamp >= ownerChatAttendedSinceRef\.current/);
assert.match(hook, /acknowledgeQuietSuccesses\(quietAttendedSuccesses\.filter\(isMaterializedSuccess\)\)/);
const acknowledgementMerge = hook.slice(
  hook.indexOf("function mergeAcknowledgementStatePreservingDisplayed"),
  hook.indexOf("export function useBackgroundGoalRuns"),
);
assert.doesNotMatch(acknowledgementMerge, /recent:|deliveries:|providerWarnings:/);

assert.match(notification, /isPermissionGranted/);
assert.match(notification, /requestPermission/);
assert.match(notification, /sendNotification\(copy\)/);
assert.match(notification, /backgroundGoalNotificationBodyKey/);
assert.match(notification, /status === "parked" && blockedKind === "question"/);
assert.doesNotMatch(notification, /if \(status === "parked"\) return/);
assert.match(notification, /translate\("goal\.backgroundNotificationTitle"\)/);
assert.match(notification, /body: translate\(bodyKey\)/);
assert.doesNotMatch(notification, /event\.(error|summary|response|blockedResponse|failureLabel|failureClass)/);
assert.doesNotMatch(notification, /JSON\.stringify\(event\)/);

assert.match(card, /data-background-goal-catch-up/);
assert.match(card, /data-background-goal-delivery-id=\{delivery\.deliveryId\}/);
assert.match(card, /data-background-goal-recap-key=\{recapKey\(delivery\)\}/);
assert.match(card, /expectedRevision: finiteRevision\(delivery\.revision\)/);
assert.match(card, /recapRevision: recapRevision\(delivery\)/);
assert.match(card, /status === "completed" \|\| status === "materialized"/);
assert.match(card, /delivery\.attempt/);
assert.match(card, /delivery\.usage\?\.totalTokens/);
assert.match(card, /delivery\.phase/);
assert.match(card, /delivery\.failureLabel/);
assert.match(card, /delivery\.usage\?\.cost/);
assert.match(card, /delivery\.usage\?\.costUnavailableReason/);
assert.match(card, /goal\.backgroundCostUnavailable/);
assert.match(card, /new Map<string, AgentGoalProviderWarning>/);
const warningProjection = card.slice(
  card.indexOf("function visibleProviderWarnings"),
  card.indexOf("function deliveryLabel"),
);
assert.doesNotMatch(warningProjection, /\.slice\(/);
assert.match(card, /data-background-goal-provider-warning-key=\{providerWarningRevisionKey\(warning\)\}/);
assert.match(card, /onProviderWarningsRendered\(warnings\.map/);
assert.match(card, /onClick=\{onDismiss\}/);
assert.match(card, /goal\.backgroundDismiss/);
assert.match(card, /expectedRevision: providerWarningRevision\(warning\)/);
assert.match(card, /providerWarning\.status \|\| warning\.warningKey|warning\.status \|\| warning\.warningKey/);
assert.doesNotMatch(card, /warning\.(baseUrl|error|detail|response|raw)/);
assert.doesNotMatch(card, /delivery\.(response|error|blockedResponse|resumePrompt)/);
assert.doesNotMatch(card, /\{delivery\.failureLabel\}/);
assert.match(chatWorkspace, /deliveries=\{backgroundGoalDeliveries\}/);
assert.match(chatWorkspace, /providerWarnings=\{backgroundGoalProviderWarnings\}/);
assert.match(chatWorkspace, /onRendered=\{onBackgroundGoalCatchUpRendered\}/);
assert.match(chatWorkspace, /onProviderWarningsRendered=\{onBackgroundGoalProviderWarningsRendered\}/);
assert.match(sidebar, /data-background-goal-unread=\{unreadCount\}/);
assert.match(sidebar, /\{unreadCount > 0 \?/);

assert.match(settings, /settings\.backgroundGoalNotifications/);
assert.match(settings, /onChange\(!enabled\)/);
assert.match(settingsWorkspace, /<BackgroundGoalSettings/);
assert.match(api, /\/api\/app\/agent\/goals\/background/);
assert.match(api, /\/api\/app\/agent\/goals\/background\/ack/);
assert.match(api, /defer_agent_goal_delivery/);
assert.match(api, /kind: "recap" \| "toast" \| "provider"/);
assert.match(api, /deliveries: AgentGoalBackgroundAcknowledgement\[\]/);
assert.match(apiTypes, /recapRevision\?: number/);
assert.match(apiTypes, /recapSeenRevision\?: number/);
assert.match(apiTypes, /toastSentRevision\?: number/);
assert.match(apiTypes, /providerWarnings\?: AgentGoalProviderWarning\[\]/);
assert.match(apiTypes, /backgroundGoalDeferred\?: boolean/);
assert.match(rustCommands, /pub async fn fetch_agent_goal_background_state/);
assert.match(rustCommands, /pub async fn acknowledge_agent_goal_background_state/);
assert.match(rustCommands, /pub async fn defer_agent_goal_delivery/);
assert.match(rustMain, /tauri_plugin_notification::init\(\)/);
assert.match(rustMain, /defer_agent_goal_delivery/);
assert.ok(capability.permissions.includes("notification:default"));
assert.match(backend, /@app\.get\("\/api\/app\/agent\/goals\/background"\)/);
assert.match(backend, /@app\.post\("\/api\/app\/agent\/goals\/background\/ack"\)/);

const skippedStart = runController.indexOf("response.backgroundGoalSkipped === true");
const skippedEnd = runController.indexOf("const elapsedSeconds", skippedStart);
assert.ok(skippedStart >= 0 && skippedEnd > skippedStart, "missing bounded background preflight skip branch");
const skippedBranch = runController.slice(skippedStart, skippedEnd);
assert.match(skippedBranch, /response\.status === "provider_unreachable"/);
assert.match(skippedBranch, /Boolean\(response\.providerWarningKey\)/);
assert.match(skippedBranch, /response\.backgroundGoalDeferred === true/);
assert.match(skippedBranch, /response\.status === "background_capacity"/);
assert.match(skippedBranch, /response\.goalDeliveryId === turn\.goalDelivery\?\.deliveryId/);
assert.match(skippedBranch, /clearTurnTransientItems\(chatId, turn\.id, userItem\.id\)/);
assert.match(skippedBranch, /refreshBackgroundGoals\(\)/);
assert.match(skippedBranch, /return false/);
assert.doesNotMatch(skippedBranch, /appendToChat|setError|type: "agent"|type: "error"/);

const clearTransientStart = runController.indexOf("function clearTurnTransientItems");
const clearTransientEnd = runController.indexOf("async function submitTurn", clearTransientStart);
const clearTransient = runController.slice(clearTransientStart, clearTransientEnd);
assert.match(clearTransient, /item\.id === userItemId/);
assert.match(clearTransient, /item\.type === "streaming"/);

const backgroundTurnStart = runController.indexOf("async function runBackgroundTurn");
const backgroundTurnEnd = runController.indexOf("async function runSingleTurn", backgroundTurnStart);
const backgroundTurn = runController.slice(backgroundTurnStart, backgroundTurnEnd);
assert.match(runController, /export const MAX_BACKGROUND_TURNS = 2/);
assert.match(backgroundTurn, /backgroundTurnAbortRefs\.current\.size >= MAX_BACKGROUND_TURNS/);
assert.match(backgroundTurn, /background: true/);
assert.doesNotMatch(backgroundTurn, /sendingRef|setSending|setCurrentTurn|activeTurnAbortRef/);
const backgroundCatchStart = runController.indexOf("if (background) {", runController.indexOf("} catch (cause)"));
const backgroundCatchEnd = runController.indexOf("if (options?.restoreOnFailure)", backgroundCatchStart);
const backgroundCatch = runController.slice(backgroundCatchStart, backgroundCatchEnd);
assert.match(backgroundCatch, /clearTurnTransientItems/);
assert.match(backgroundCatch, /return false/);
assert.doesNotMatch(backgroundCatch, /appendToChat|setError|type: "agent"|type: "error"/);
assert.match(app, /const succeeded = await runBackgroundTurn\(targetChat\.id, turn\)/);
const goalCallbackStart = app.indexOf("onGoalDelivery: async (goal, delivery)");
const goalCallbackEnd = app.indexOf("useEffect(() =>", goalCallbackStart);
assert.doesNotMatch(app.slice(goalCallbackStart, goalCallbackEnd), /chatRunSending \|\| compacting/);

assert.match(goalWake, /GOAL_WAKE_POLL_INTERVAL_MS = 5_000/);
assert.match(goalWake, /GOAL_WAKE_MAX_PARALLEL = 2/);
assert.match(goalWake, /activeDeliveryIdsRef/);
assert.match(goalWake, /sort\(compareEligible\)/);
assert.match(goalWake, /item\.eligibleAt/);
assert.match(goalWake, /deferAgentGoalDelivery\(endpoint, delivery\.deliveryId/);
assert.match(goalWake, /expectedRevision: delivery\.revision/);
assert.doesNotMatch(goalWake, /sendingRef|sending:/);

const localeNames = ["en-US", "ja-JP", "zh-CN", "zh-TW"];
const locales = localeNames.map((name) => [name, JSON.parse(read(`src/locales/${name}.json`))]);
const flatten = (value, prefix = "", rows = new Map()) => {
  for (const [key, item] of Object.entries(value)) {
    const next = prefix ? `${prefix}.${key}` : key;
    if (item && typeof item === "object" && !Array.isArray(item)) flatten(item, next, rows);
    else rows.set(next, String(item));
  }
  return rows;
};
const reference = flatten(locales[0][1]);
const placeholders = (value) => [...String(value || "").matchAll(/{{\s*([^}\s]+)\s*}}/g)]
  .map((match) => match[1])
  .sort();
for (const [name, locale] of locales) {
  const entries = flatten(locale);
  assert.deepEqual([...entries.keys()].sort(), [...reference.keys()].sort(), `${name} locale keys differ`);
  for (const [key, value] of reference) {
    assert.deepEqual(placeholders(entries.get(key)), placeholders(value), `${name}:${key} placeholders differ`);
  }
  for (const required of [
    "settings.backgroundGoalNotifications",
    "settings.backgroundGoalNotificationsDesc",
    "goal.backgroundCatchUp",
    "goal.backgroundDismiss",
    "goal.backgroundFailed",
    "goal.backgroundDenied",
    "goal.backgroundApproval",
    "goal.backgroundQuestion",
    "goal.backgroundPhaseLabel",
    "goal.backgroundPhaseWake",
    "goal.backgroundPhaseProjectLock",
    "goal.backgroundPhaseProviderCall",
    "goal.backgroundPhaseApply",
    "goal.backgroundPhaseDeliver",
    "goal.backgroundPhaseUnknown",
    "goal.backgroundFailureLabel",
    "goal.backgroundFailureAuthCredit",
    "goal.backgroundFailureSchemaPrivacy",
    "goal.backgroundFailureInvalidRequest",
    "goal.backgroundFailureNetwork",
    "goal.backgroundFailureRateLimit",
    "goal.backgroundFailureTimeout",
    "goal.backgroundFailureServer",
    "goal.backgroundFailureProviderUnavailable",
    "goal.backgroundFailurePermissionDenied",
    "goal.backgroundFailureCancelled",
    "goal.backgroundFailureLoopSuppressed",
    "goal.backgroundFailureApply",
    "goal.backgroundFailureTool",
    "goal.backgroundFailureContextCompaction",
    "goal.backgroundFailureNeedsInstruction",
    "goal.backgroundFailurePaused",
    "goal.backgroundFailureUnknown",
    "goal.backgroundCost",
    "goal.backgroundCostUnavailable",
    "goal.backgroundCostPricingNotConfigured",
    "goal.backgroundCostPricingIncomplete",
    "goal.backgroundCostPricingInvalid",
    "goal.backgroundCostUsageIncomplete",
    "goal.backgroundCostUsageBounded",
    "goal.backgroundCostUsageInconsistent",
    "goal.backgroundCostUnavailableUnknown",
    "goal.backgroundProviderWarningTitle",
    "goal.backgroundProviderWarningUnavailable",
    "goal.backgroundProviderWarningUnknown",
    "goal.backgroundProviderWarningEvidence",
    "goal.backgroundProviderWarningLastSeen",
    "goal.backgroundProviderUnknown",
    "goal.backgroundNotificationTitle",
    "goal.backgroundNotificationFailed",
    "goal.backgroundNotificationDenied",
    "goal.backgroundNotificationParked",
    "goal.backgroundNotificationApproval",
    "goal.backgroundNotificationQuestion",
  ]) {
    assert.ok(entries.get(required), `${name} missing ${required}`);
  }
}

console.log("background goal UI/backend notification contract: ok");
