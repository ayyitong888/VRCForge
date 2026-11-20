import type { SettingsSection } from "./app-view";

export type SettingsSearchEntry = {
  id: string;
  section: SettingsSection;
  panel: string;
  labelKey: string;
  descriptionKey?: string;
  developerOnly?: boolean;
};

export type SettingsSearchSelection = { entry: SettingsSearchEntry; requestId: number };

export const SETTINGS_SECTION_LABELS: Record<SettingsSection, string> = {
  general: "settings.navGeneral", chat: "settings.navChat", notifications: "settings.navNotifications",
  about: "settings.navAbout", permissions: "settings.navPermissions", models: "settings.navModels",
  memory: "settings.navMemory", storage: "settings.navStorage", connectors: "settings.navConnectors",
  instructions: "settings.navInstructions", developer: "settings.navDeveloper",
};

export const SETTINGS_SEARCH_CONTEXT_LABELS: Record<string, string> = {
  account: "chatgptOAuth.title", api: "provider.apiProvider", vision: "settings.visionProfile", telegram: "connector.telegramRemoteAccess",
};

// Only authored labels and help text are searchable. Never index configuration
// values, account names, API keys, memory contents or diagnostic payloads.
function group(section: SettingsSection, panel: string, rows: readonly (readonly [string, string?])[], developerOnly = false): SettingsSearchEntry[] {
  return rows.map(([labelKey, descriptionKey]) => ({ id: `${panel}:${labelKey}`, section, panel, labelKey, descriptionKey, developerOnly }));
}

export const SETTINGS_SEARCH_ENTRIES: readonly SettingsSearchEntry[] = [
  ...group("general", "startup-animation", [["settings.startupAnimation", "settings.startupAnimationDesc"]]),
  ...group("about", "onboarding", [["settings.onboarding", "settings.onboardingDesc"]]),
  ...group("general", "appearance", [
    ["settings.themeCustomization", "settings.themeCustomizationDesc"], ["settings.themePalette"],
    ["settings.themeCustomAccent"], ["settings.themeCustomSurface"], ["settings.themeBackground"],
    ["settings.themeBackgroundScope"], ["settings.themeBackgroundOpacity"], ["settings.themeReset"],
  ]),
  ...group("general", "language", [["settings.language", "settings.languageDesc"]]),
  ...group("chat", "enter", [["settings.inputPrediction", "settings.inputPredictionDesc"], ["settings.runningEnterAction"], ["settings.runningEnterQueue"], ["settings.runningEnterSteer"]]),
  ...group("notifications", "notifications", [["settings.backgroundGoalNotifications", "settings.backgroundGoalNotificationsDesc"]]),
  ...group("notifications", "sounds", [
    ["settings.notificationSoundTitle", "settings.notificationSoundDesc"], ["settings.notificationSoundOrdinary"], ["settings.notificationSoundBlocking"],
  ]),
  ...group("about", "diagnostics", [["settings.diagnostics", "settings.loggingDesc"], ["settings.logLevel"], ["settings.openLogsFolder"], ["settings.exportBundle"]]),
  ...group("about", "about", [["settings.aboutTitle", "settings.aboutSafety"]]),
  ...group("about", "developer-toggle", [["settings.developerOptions", "settings.developerOptionsDesc"]]),
  ...group("permissions", "permissions", [["settings.permissionMode", "settings.permissionModeDescription"], ["executionMode.approval", "executionMode.approvalDesc"], ["header.autoApproval", "executionMode.autoDesc"], ["header.fullPermission", "executionMode.roslynFullAutoDesc"]]),
  ...group("models", "account", [["chatgptOAuth.title"], ["chatgptOAuth.model"], ["chatgptOAuth.reasoningSelect"], ["chatgptOAuth.testConnection"]]),
  ...group("models", "api", [
    ["provider.apiProvider", "settings.providerDesc"], ["provider.apiKey", "provider.savedKeyHint"], ["provider.baseUrl"],
    ["provider.model", "provider.modelPlaceholder"], ["provider.protocolAutoNegotiation", "provider.protocolAutoNegotiationHint"],
    ["provider.apiType", "provider.apiTypeHint"], ["provider.contextWindow", "provider.contextWindowHint"],
    ["provider.reasoningEffort", "provider.reasoningEffortHint"], ["provider.testConnection"], ["provider.testStructured"], ["provider.testVision"],
  ]),
  ...group("models", "vision", [["settings.visionProfile", "settings.visionProfileDesc"], ["vision.enabledLabel", "vision.disabledDescription"], ["provider.apiProvider"], ["provider.apiKey"], ["provider.baseUrl"], ["provider.model"]]),
  ...group("memory", "memory", [["settings.memoryEnabled", "settings.memoryEnabledDesc"], ["settings.crossSessionMemory", "settings.crossSessionMemoryDesc"], ["settings.memoryReviewTitle", "settings.memoryReviewDesc"]]),
  ...group("storage", "checkpoints", [
    ["settings.storage"], ["settings.checkpointArchiveCapacity"], ["settings.checkpointArchiveLimit", "settings.checkpointArchiveLimitHint"],
    ["settings.checkpointArchiveCountLimit", "settings.checkpointArchiveCountLimitHint"], ["settings.checkpointArchiveDirectory"],
    ["settings.checkpointArchiveListTitle"], ["settings.checkpointArchiveCleanSelected"], ["settings.checkpointArchiveNewDirectory", "settings.checkpointArchiveNewDirectoryHint"],
  ]),
  ...group("storage", "data-root", [["settings.dataRootTitle", "settings.dataRootDesc"]]),
  ...group("connectors", "connectors", [
    ["connector.title", "connector.configStatusHint"], ["connector.gateway", "connector.permissionModeHint"],
    ["connector.chooseHost"], ["connector.genericPathLabel", "connector.genericPathPlaceholder"], ["connector.agentSetupTitle", "connector.agentSetupHint"],
  ]),
  ...group("connectors", "telegram", [
    ["connector.telegramRemoteAccess", "settings.searchTelegramDescription"],
    ["settings.telegramBotToken"], ["settings.telegramOwnerId"], ["settings.telegramProjects"],
    ["settings.telegramConnection"], ["settings.telegramProxy"],
  ]),
  ...group("instructions", "instructions", [["settings.customInstructions", "settings.customInstructionsDesc"]]),
  ...group("developer", "computer-use", [["settings.computerUse", "settings.computerUseDesc"]], true),
  ...group("developer", "identity", [["settings.logIdentityMapping", "settings.logIdentityMappingDesc"]], true),
  ...group("developer", "compaction", [["settings.compactDebugTitle", "settings.compactDebugDesc"]], true),
];

type Translate = (key: string) => string;
const normalize = (value: string) => value.normalize("NFKC").toLocaleLowerCase().trim();

export function searchSettings(query: string, translate: Translate, developerOptionsEnabled: boolean): SettingsSearchEntry[] {
  const words = normalize(query).split(/\s+/).filter(Boolean);
  if (!words.length) return [];
  return SETTINGS_SEARCH_ENTRIES.filter((entry) => {
    if (entry.developerOnly && !developerOptionsEnabled) return false;
    const contextKey = SETTINGS_SEARCH_CONTEXT_LABELS[entry.panel];
    const text = normalize([translate(entry.labelKey), entry.descriptionKey ? translate(entry.descriptionKey) : "", translate(SETTINGS_SECTION_LABELS[entry.section]), contextKey ? translate(contextKey) : ""].join(" "));
    return words.every((word) => text.includes(word));
  }).sort((left, right) => Number(normalize(translate(right.labelKey)).includes(normalize(query))) - Number(normalize(translate(left.labelKey)).includes(normalize(query))));
}

export function focusSettingsSearchResult(root: HTMLElement, entry: SettingsSearchEntry, label: string): void {
  const panel = root.querySelector<HTMLElement>(`[data-settings-panel="${entry.panel}"]`);
  if (!panel || panel.closest("[hidden]") || !panel.getClientRects().length) return;
  // Open disclosure UI only; search never changes the setting itself.
  if (panel instanceof HTMLDetailsElement) panel.open = true;
  const match = [...panel.querySelectorAll<HTMLElement>("label, legend, h2, h3, p, span, div, button, summary")]
    .find((element) => {
      if (element.textContent?.trim() !== label || [...element.children].some((child) => child.textContent?.trim() === label) || element.closest("[hidden]")) return false;
      let ancestor: HTMLElement | null = element;
      while (ancestor && panel.contains(ancestor)) {
        if (ancestor instanceof HTMLDetailsElement) ancestor.open = true;
        ancestor = ancestor.parentElement;
      }
      return element.getClientRects().length > 0 && getComputedStyle(element).visibility !== "hidden";
    });
  const target = match && !match.matches(":disabled") ? match : panel;
  root.querySelectorAll<HTMLElement>("[data-settings-search-match]").forEach((element) => delete element.dataset.settingsSearchMatch);
  target.dataset.settingsSearchMatch = entry.id;
  const temporaryTabIndex = !target.hasAttribute("tabindex") && !target.matches("button, input, select, textarea, a[href], summary");
  if (temporaryTabIndex) target.tabIndex = -1;
  target.addEventListener("blur", () => {
    delete target.dataset.settingsSearchMatch;
    if (temporaryTabIndex) target.removeAttribute("tabindex");
  }, { once: true });
  target.focus({ preventScroll: true });
  target.scrollIntoView({ block: "center", behavior: "auto" });
}
