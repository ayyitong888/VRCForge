import { useLayoutEffect, useState } from "react";
import {
  DEFAULT_THEME_CUSTOMIZATION,
  THEME_CUSTOMIZATION_STORAGE_KEY,
  hexColorToHslChannels,
  loadThemeCustomization,
  normalizeThemeCustomization,
  type ThemeCustomization,
} from "../lib/theme-customization";

export function useThemeCustomization() {
  const [customization, setCustomization] = useState<ThemeCustomization>(() => loadThemeCustomization());

  useLayoutEffect(() => {
    const root = document.documentElement;
    const accent = hexColorToHslChannels(customization.accentColor);
    if (accent) {
      root.style.setProperty("--primary", accent);
      root.style.setProperty("--ring", accent);
    } else {
      root.style.removeProperty("--primary");
      root.style.removeProperty("--ring");
    }

    if (customization.backgroundImageDataUrl) {
      root.dataset.vrcforgeWallpaper = "active";
      root.style.setProperty("--vrcforge-background-image", `url("${customization.backgroundImageDataUrl}")`);
      root.style.setProperty("--vrcforge-wallpaper-scrim", String(1 - customization.backgroundOpacity));
    } else {
      delete root.dataset.vrcforgeWallpaper;
      root.style.removeProperty("--vrcforge-background-image");
      root.style.removeProperty("--vrcforge-wallpaper-scrim");
    }

    try {
      window.localStorage.setItem(THEME_CUSTOMIZATION_STORAGE_KEY, JSON.stringify(customization));
    } catch {
      // The current session still uses the selected theme when storage is blocked.
    }
  }, [customization]);

  return {
    themeCustomization: customization,
    updateThemeCustomization: (next: Partial<ThemeCustomization>) => {
      setCustomization((current) => normalizeThemeCustomization({ ...current, ...next }));
    },
    resetThemeCustomization: () => setCustomization(DEFAULT_THEME_CUSTOMIZATION),
  };
}
