export type ProjectUiPrefs = {
  pinnedPaths: string[];
  aliases: Record<string, string>;
  projectOrder?: string[];
  lastOpenedAt?: Record<string, string>;
};

export type LayoutPaneWidths = {
  left: number;
  right: number;
};

export type ThemeMode = "light" | "dark";
export type RunningEnterAction = "queue" | "steer";

export const ONBOARDING_FLAG_KEY = "vrcforge_onboarded";
export const COLLAPSED_PROJECTS_KEY = "vrcforge_collapsed_projects";
export const PROJECT_UI_PREFS_KEY = "vrcforge_project_ui_prefs";
export const THEME_STORAGE_KEY = "vrcforge_theme";
export const LEFT_SIDEBAR_COLLAPSED_KEY = "vrcforge_left_sidebar_collapsed";
export const RIGHT_SIDEBAR_COLLAPSED_KEY = "vrcforge_right_sidebar_collapsed";
export const LAYOUT_PANE_WIDTHS_KEY = "vrcforge_layout_pane_widths";
export const RIGHT_RUNTIME_SECTION_COLLAPSED_KEY = "vrcforge_right_runtime_sections_collapsed";
export const DEVELOPER_OPTIONS_ENABLED_KEY = "vrcforge_developer_options_enabled";
export const AUTOMATIC_UPDATE_CHECK_DISABLED_KEY = "vrcforge_automatic_update_check_disabled";
const STARTUP_ANIMATION_KEY = "vrcforge_startup_animation";
const RUNNING_ENTER_ACTION_KEY = "vrcforge_running_enter_action";
const INPUT_PREDICTION_KEY = "vrcforge_input_prediction";
const INPUT_PREDICTION_MODELS_KEY = "vrcforge_input_prediction_models";
const INPUT_PREDICTION_REASONING_KEY = "vrcforge_input_prediction_reasoning";
const NEW_SESSION_REMINDER_KEY_PREFIX = "vrcforge_new_session_reminder:";
const dismissedNewSessionReminders = new Map<string, string>();

export function isNewSessionReminderDismissed(chatId: string, generation: string): boolean {
  if (dismissedNewSessionReminders.get(chatId) === generation) return true;
  try {
    return window.localStorage.getItem(NEW_SESSION_REMINDER_KEY_PREFIX + chatId) === generation;
  } catch {
    return false;
  }
}

export function dismissNewSessionReminder(chatId: string, generation: string): void {
  dismissedNewSessionReminders.set(chatId, generation);
  try {
    window.localStorage.setItem(NEW_SESSION_REMINDER_KEY_PREFIX + chatId, generation);
  } catch {
    // The acknowledgment still applies to the current window.
  }
}

export function loadInputPredictionEnabled(): boolean {
  try {
    return window.localStorage.getItem(INPUT_PREDICTION_KEY) === "true";
  } catch {
    return false;
  }
}

export function persistInputPredictionEnabled(enabled: boolean): void {
  try {
    window.localStorage.setItem(INPUT_PREDICTION_KEY, String(enabled));
  } catch {
    // The preference still applies to the current window.
  }
}

function loadInputPredictionChoices(key: string): Record<string, string> {
  try {
    const parsed: unknown = JSON.parse(window.localStorage.getItem(key) || "{}");
    if (!parsed || typeof parsed !== "object" || Array.isArray(parsed)) return {};
    return Object.fromEntries(Object.entries(parsed)
      .filter((entry): entry is [string, string] => typeof entry[1] === "string" && Boolean(entry[1].trim()))
      .map(([provider, model]) => [provider, model.trim()]));
  } catch {
    return {};
  }
}

function persistInputPredictionChoices(key: string, choices: Record<string, string>): void {
  try {
    window.localStorage.setItem(key, JSON.stringify(choices));
  } catch {
    // The preference still applies to the current window.
  }
}

export const loadInputPredictionModels = () => loadInputPredictionChoices(INPUT_PREDICTION_MODELS_KEY);
export const persistInputPredictionModels = (models: Record<string, string>) => persistInputPredictionChoices(INPUT_PREDICTION_MODELS_KEY, models);
export const loadInputPredictionReasoning = () => loadInputPredictionChoices(INPUT_PREDICTION_REASONING_KEY);
export const persistInputPredictionReasoning = (reasoning: Record<string, string>) => persistInputPredictionChoices(INPUT_PREDICTION_REASONING_KEY, reasoning);

export const DEFAULT_LEFT_PANE_WIDTH = 280;
export const DEFAULT_RIGHT_PANE_WIDTH = 320;
export const COLLAPSED_LEFT_PANE_WIDTH = 56;
export const RESIZE_HANDLE_WIDTH = 6;
export const MIN_LEFT_PANE_WIDTH = 220;
export const MAX_LEFT_PANE_WIDTH = 440;
export const MIN_RIGHT_PANE_WIDTH = 260;
export const MAX_RIGHT_PANE_WIDTH = 520;
export const MIN_CENTER_PANE_WIDTH = 520;

export function loadProjectUiPrefs(): ProjectUiPrefs {
  try {
    const raw = window.localStorage.getItem(PROJECT_UI_PREFS_KEY);
    const parsed = raw ? JSON.parse(raw) : null;
    if (!parsed || typeof parsed !== "object") {
      return { pinnedPaths: [], aliases: {} };
    }
    const pinnedPaths = Array.isArray(parsed.pinnedPaths)
      ? parsed.pinnedPaths.filter((item: unknown): item is string => typeof item === "string" && item.trim().length > 0)
      : [];
    const aliases =
      parsed.aliases && typeof parsed.aliases === "object"
        ? Object.fromEntries(
            Object.entries(parsed.aliases).filter(
              (entry): entry is [string, string] => typeof entry[0] === "string" && typeof entry[1] === "string" && entry[1].trim().length > 0,
            ),
          )
        : {};
    const projectOrder = Array.isArray(parsed.projectOrder)
      ? parsed.projectOrder.filter((item: unknown): item is string => typeof item === "string" && item.trim().length > 0)
      : [];
    const lastOpenedAt = parsed.lastOpenedAt && typeof parsed.lastOpenedAt === "object"
      ? Object.fromEntries(Object.entries(parsed.lastOpenedAt).filter(
          (entry): entry is [string, string] => typeof entry[1] === "string" && Number.isFinite(Date.parse(entry[1])),
        ))
      : {};
    return { pinnedPaths, aliases, projectOrder, lastOpenedAt };
  } catch {
    return { pinnedPaths: [], aliases: {} };
  }
}

export function loadThemePreference(): ThemeMode {
  try {
    const raw = window.localStorage.getItem(THEME_STORAGE_KEY);
    return raw === "dark" || raw === "light" ? raw : "light";
  } catch {
    return "light";
  }
}

export function loadDeveloperOptionsEnabled(): boolean {
  try {
    return window.localStorage.getItem(DEVELOPER_OPTIONS_ENABLED_KEY) === "true";
  } catch {
    return false;
  }
}

export function loadAutomaticUpdateCheckEnabled(): boolean {
  try {
    return window.localStorage.getItem(AUTOMATIC_UPDATE_CHECK_DISABLED_KEY) !== "true";
  } catch {
    return true;
  }
}

export function persistAutomaticUpdateCheckEnabled(enabled: boolean): void {
  try {
    if (enabled) {
      window.localStorage.removeItem(AUTOMATIC_UPDATE_CHECK_DISABLED_KEY);
    } else {
      window.localStorage.setItem(AUTOMATIC_UPDATE_CHECK_DISABLED_KEY, "true");
    }
  } catch {
    // A blocked preference store must not prevent update checks or dismissal.
  }
}

export function loadStartupAnimationEnabled(): boolean {
  try {
    return window.localStorage.getItem(STARTUP_ANIMATION_KEY) !== "false";
  } catch {
    return true;
  }
}

export function persistStartupAnimationEnabled(enabled: boolean): void {
  try {
    window.localStorage.setItem(STARTUP_ANIMATION_KEY, String(enabled));
  } catch {
    // A blocked preference store must not prevent using Settings.
  }
}

export function loadRunningEnterAction(): RunningEnterAction {
  try {
    return window.localStorage.getItem(RUNNING_ENTER_ACTION_KEY) === "steer" ? "steer" : "queue";
  } catch {
    return "queue";
  }
}

export function persistRunningEnterAction(action: RunningEnterAction): void {
  try {
    window.localStorage.setItem(RUNNING_ENTER_ACTION_KEY, action);
  } catch {
    // The current window can still use the setting.
  }
}

export function clampNumber(value: number, min: number, max: number): number {
  if (!Number.isFinite(value)) {
    return min;
  }
  return Math.min(max, Math.max(min, value));
}

export function loadLayoutPaneWidths(): LayoutPaneWidths {
  try {
    const raw = window.localStorage.getItem(LAYOUT_PANE_WIDTHS_KEY);
    if (!raw) {
      return { left: DEFAULT_LEFT_PANE_WIDTH, right: DEFAULT_RIGHT_PANE_WIDTH };
    }
    const parsed = raw ? JSON.parse(raw) : {};
    return {
      left: clampNumber(Number(parsed.left || DEFAULT_LEFT_PANE_WIDTH), MIN_LEFT_PANE_WIDTH, MAX_LEFT_PANE_WIDTH),
      right: clampNumber(Number(parsed.right || DEFAULT_RIGHT_PANE_WIDTH), MIN_RIGHT_PANE_WIDTH, MAX_RIGHT_PANE_WIDTH),
    };
  } catch {
    return { left: DEFAULT_LEFT_PANE_WIDTH, right: DEFAULT_RIGHT_PANE_WIDTH };
  }
}
