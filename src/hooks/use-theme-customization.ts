import { useEffect, useLayoutEffect, useState } from "react";
import { migrateLegacyThemeBackground, themeBackgroundAssetUrl } from "../lib/theme-background";
import {
  DEFAULT_THEME_CUSTOMIZATION,
  THEME_CUSTOMIZATION_STORAGE_KEY,
  hexColorToHslChannels,
  loadLegacyThemeBackgroundDataUrl,
  loadThemeCustomization,
  normalizeThemeCustomization,
  type ThemeCustomization,
} from "../lib/theme-customization";

export function useThemeCustomization() {
  const [customization, setCustomization] = useState<ThemeCustomization>(() => loadThemeCustomization());
  const [legacyBackground] = useState(() => loadLegacyThemeBackgroundDataUrl());

  useEffect(() => {
    if (!legacyBackground || customization.backgroundImagePath) return;
    let active = true;
    void migrateLegacyThemeBackground(legacyBackground)
      .then((backgroundImagePath) => {
        if (active) {
          setCustomization((current) => current.backgroundImagePath
            ? current
            : normalizeThemeCustomization({ ...current, backgroundImagePath }));
        }
      })
      .catch(() => undefined);
    return () => {
      active = false;
    };
  }, [customization.backgroundImagePath, legacyBackground]);

  useLayoutEffect(() => {
    const root = document.documentElement;
    if (customization.palette === "default") {
      delete root.dataset.vrcforgePalette;
    } else {
      root.dataset.vrcforgePalette = customization.palette;
    }

    const customAccent = customization.palette === "custom"
      ? hexColorToHslChannels(customization.accentColor || "#2563eb")
      : null;
    if (customAccent) {
      root.style.setProperty("--primary", customAccent);
      root.style.setProperty("--ring", customAccent);
    } else {
      root.style.removeProperty("--primary");
      root.style.removeProperty("--ring");
    }

    const backgroundAssetUrl = themeBackgroundAssetUrl(customization.backgroundImagePath);
    if (backgroundAssetUrl) {
      root.dataset.vrcforgeWallpaper = "active";
      root.style.setProperty("--vrcforge-background-image", `url("${backgroundAssetUrl}")`);
      root.style.setProperty("--vrcforge-wallpaper-scrim", String(1 - customization.backgroundOpacity));
    } else {
      delete root.dataset.vrcforgeWallpaper;
      root.style.removeProperty("--vrcforge-background-image");
      root.style.removeProperty("--vrcforge-wallpaper-scrim");
    }

    // Keep the legacy Base64 record intact until its one-time file migration
    // succeeds. All new writes contain only palette settings and a managed path.
    if (!legacyBackground || customization.backgroundImagePath) {
      try {
        window.localStorage.setItem(THEME_CUSTOMIZATION_STORAGE_KEY, JSON.stringify(customization));
      } catch {
        // The current session still uses the selected theme when storage is blocked.
      }
    }
  }, [customization, legacyBackground]);

  return {
    themeCustomization: customization,
    updateThemeCustomization: (next: Partial<ThemeCustomization>) => {
      setCustomization((current) => normalizeThemeCustomization({ ...current, ...next }));
    },
    resetThemeCustomization: () => setCustomization(DEFAULT_THEME_CUSTOMIZATION),
  };
}
