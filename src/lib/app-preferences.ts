export type ProjectUiPrefs = {
  pinnedPaths: string[];
  aliases: Record<string, string>;
};

export type LayoutPaneWidths = {
  left: number;
  right: number;
};

export type ThemeMode = "light" | "dark";

export const ONBOARDING_FLAG_KEY = "vrcforge_onboarded";
export const COLLAPSED_PROJECTS_KEY = "vrcforge_collapsed_projects";
export const PROJECT_UI_PREFS_KEY = "vrcforge_project_ui_prefs";
export const THEME_STORAGE_KEY = "vrcforge_theme";
export const LEFT_SIDEBAR_COLLAPSED_KEY = "vrcforge_left_sidebar_collapsed";
export const RIGHT_SIDEBAR_COLLAPSED_KEY = "vrcforge_right_sidebar_collapsed";
export const LAYOUT_PANE_WIDTHS_KEY = "vrcforge_layout_pane_widths";
export const RIGHT_RUNTIME_SECTION_COLLAPSED_KEY = "vrcforge_right_runtime_sections_collapsed";
export const DEVELOPER_OPTIONS_ENABLED_KEY = "vrcforge_developer_options_enabled";

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
    return { pinnedPaths, aliases };
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
