import { useEffect, useLayoutEffect, useState } from "react";
import { migrateLegacyThemeBackground, themeBackgroundAssetUrl } from "../lib/theme-background";
import { readableForegroundForHsl, themeColorToHsl } from "../lib/theme-color";
import {
  DEFAULT_THEME_CUSTOMIZATION,
  THEME_CUSTOMIZATION_STORAGE_KEY,
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

    root.style.removeProperty("--primary");
    root.style.removeProperty("--ring");
    const customAccent = customization.palette === "custom"
      ? themeColorToHsl(customization.accentColor || "#2563eb")
      : null;
    if (customAccent) {
      const accentSaturation = customAccent.s < 5 ? 0 : Math.min(95, Math.max(35, customAccent.s));
      const accentSoftSaturation = accentSaturation === 0 ? 0 : Math.max(10, accentSaturation * 0.45);
      const lightLightness = Math.min(56, Math.max(38, customAccent.l));
      const darkLightness = Math.min(72, Math.max(58, customAccent.l));
      root.style.setProperty("--vrcforge-custom-accent-h", String(Math.round(customAccent.h * 10) / 10));
      root.style.setProperty("--vrcforge-custom-accent-s", `${Math.round(accentSaturation * 10) / 10}%`);
      root.style.setProperty("--vrcforge-custom-accent-soft-s", `${Math.round(accentSoftSaturation * 10) / 10}%`);
      root.style.setProperty("--vrcforge-custom-accent-light-l", `${Math.round(lightLightness * 10) / 10}%`);
      root.style.setProperty("--vrcforge-custom-accent-dark-l", `${Math.round(darkLightness * 10) / 10}%`);
      root.style.setProperty(
        "--vrcforge-custom-accent-light-foreground",
        readableForegroundForHsl({ ...customAccent, s: accentSaturation, l: lightLightness }),
      );
      root.style.setProperty(
        "--vrcforge-custom-accent-dark-foreground",
        readableForegroundForHsl({ ...customAccent, s: accentSaturation, l: darkLightness }),
      );
    } else {
      for (const property of [
        "--vrcforge-custom-accent-h",
        "--vrcforge-custom-accent-s",
        "--vrcforge-custom-accent-soft-s",
        "--vrcforge-custom-accent-light-l",
        "--vrcforge-custom-accent-dark-l",
        "--vrcforge-custom-accent-light-foreground",
        "--vrcforge-custom-accent-dark-foreground",
      ]) root.style.removeProperty(property);
    }

    const customSurface = customization.palette === "custom" && customization.surfaceColor
      ? themeColorToHsl(customization.surfaceColor)
      : null;
    if (customSurface) {
      const surfaceSaturation = customSurface.s < 3 ? 0 : Math.min(50, Math.max(10, customSurface.s));
      const mutedSaturation = surfaceSaturation === 0 ? 0 : Math.max(8, surfaceSaturation * 0.55);
      root.dataset.vrcforgeCustomSurfaces = "active";
      root.style.setProperty("--vrcforge-custom-surface-h", String(Math.round(customSurface.h * 10) / 10));
      root.style.setProperty("--vrcforge-custom-surface-s", `${Math.round(surfaceSaturation * 10) / 10}%`);
      root.style.setProperty("--vrcforge-custom-surface-muted-s", `${Math.round(mutedSaturation * 10) / 10}%`);
    } else {
      delete root.dataset.vrcforgeCustomSurfaces;
      root.style.removeProperty("--vrcforge-custom-surface-h");
      root.style.removeProperty("--vrcforge-custom-surface-s");
      root.style.removeProperty("--vrcforge-custom-surface-muted-s");
    }

    const backgroundAssetUrl = themeBackgroundAssetUrl(customization.backgroundImagePath);
    if (backgroundAssetUrl) {
      root.dataset.vrcforgeWallpaper = "active";
      root.dataset.vrcforgeWallpaperScope = customization.backgroundScope;
      root.style.setProperty("--vrcforge-background-image", `url("${backgroundAssetUrl}")`);
      root.style.setProperty("--vrcforge-wallpaper-scrim", String(1 - customization.backgroundOpacity));
    } else {
      delete root.dataset.vrcforgeWallpaper;
      delete root.dataset.vrcforgeWallpaperScope;
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
    resetThemeCustomization: () => setCustomization((current) => ({
      ...DEFAULT_THEME_CUSTOMIZATION,
      recentColors: current.recentColors,
    })),
  };
}
