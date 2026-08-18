import { Check, ImagePlus, Loader2, RotateCcw, Trash2 } from "lucide-react";
import { useState } from "react";
import { useTranslation } from "react-i18next";
import { clearThemeBackground, pickThemeBackground } from "../../lib/theme-background";
import {
  THEME_PALETTES,
  type ThemeBackgroundScope,
  type ThemeCustomization,
  type ThemePaletteId,
} from "../../lib/theme-customization";
import { cn } from "../../lib/utils";
import { Button } from "../ui/button";
import { ThemeColorEditor } from "./theme-color-editor";

type ThemeCustomizationPanelProps = {
  value: ThemeCustomization;
  onChange: (next: Partial<ThemeCustomization>) => void;
  onReset: () => void;
};

const BACKGROUND_SCOPE_OPTIONS: readonly ThemeBackgroundScope[] = ["workspace", "app"];

export function ThemeCustomizationPanel({ value, onChange, onReset }: ThemeCustomizationPanelProps) {
  const { t } = useTranslation();
  const [message, setMessage] = useState("");
  const [backgroundBusy, setBackgroundBusy] = useState(false);

  const choosePalette = (palette: ThemePaletteId) => {
    if (palette === "custom") {
      onChange({
        palette,
        accentColor: value.accentColor || "#2563eb",
        surfaceColor: value.surfaceColor || "#f8fafc",
      });
      return;
    }
    onChange({ palette });
  };

  const commitCustomColor = (field: "accentColor" | "surfaceColor", color: string) => {
    const recentColors = [color, ...value.recentColors.filter((recent) => recent !== color)].slice(0, 3);
    onChange({ [field]: color, recentColors });
  };

  const chooseBackground = async () => {
    setBackgroundBusy(true);
    setMessage("");
    try {
      const backgroundImagePath = await pickThemeBackground();
      if (backgroundImagePath) {
        onChange({ backgroundImagePath });
        setMessage(t("settings.themeBackgroundReady"));
      }
    } catch {
      setMessage(t("settings.themeBackgroundError"));
    } finally {
      setBackgroundBusy(false);
    }
  };

  const removeBackground = async () => {
    setBackgroundBusy(true);
    setMessage("");
    try {
      await clearThemeBackground();
      onChange({ backgroundImagePath: "" });
    } catch {
      setMessage(t("settings.themeBackgroundError"));
    } finally {
      setBackgroundBusy(false);
    }
  };

  const resetTheme = async () => {
    setBackgroundBusy(true);
    setMessage("");
    try {
      await clearThemeBackground();
      onReset();
    } catch {
      setMessage(t("settings.themeBackgroundError"));
    } finally {
      setBackgroundBusy(false);
    }
  };

  return (
    <div data-vrcforge-theme-customization>
      <h2 className="text-base font-semibold">{t("settings.themeCustomization")}</h2>
      <p className="mt-1 text-sm text-muted-foreground">{t("settings.themeCustomizationDesc")}</p>
      <div className="mt-4 grid gap-5 rounded-xl border border-border bg-card p-4">
        <fieldset>
          <legend className="text-sm font-medium">{t("settings.themePalette")}</legend>
          <div className="mt-3 grid grid-cols-2 gap-2 sm:grid-cols-3 lg:grid-cols-4">
            {THEME_PALETTES.map((palette) => {
              const selected = value.palette === palette.id;
              const paletteLabel = `settings.themePalette${palette.id[0].toUpperCase()}${palette.id.slice(1)}`;
              return (
                <button
                  key={palette.id}
                  type="button"
                  aria-pressed={selected}
                  onClick={() => choosePalette(palette.id)}
                  className={cn(
                    "relative rounded-lg border px-3 py-2.5 text-left transition-colors",
                    selected ? "border-primary bg-primary/10" : "border-border bg-background hover:bg-muted",
                  )}
                >
                  <span className="flex gap-1.5" aria-hidden="true">
                    {palette.swatches.map((swatch) => (
                      <span key={swatch} className="h-4 w-4 rounded-full border border-black/10" style={{ backgroundColor: swatch }} />
                    ))}
                  </span>
                  <span className="mt-2 block text-xs font-medium">{t(paletteLabel)}</span>
                  {selected ? <Check className="absolute right-2 top-2 h-3.5 w-3.5 text-primary" /> : null}
                </button>
              );
            })}
          </div>
          {value.palette === "custom" ? (
            <div className="mt-3 grid gap-3 lg:grid-cols-2">
              <ThemeColorEditor
                label={t("settings.themeCustomAccent")}
                value={value.accentColor}
                fallbackColor="#2563eb"
                recentColors={value.recentColors}
                onCommit={(color) => commitCustomColor("accentColor", color)}
              />
              <ThemeColorEditor
                label={t("settings.themeCustomSurface")}
                value={value.surfaceColor}
                fallbackColor="#f8fafc"
                recentColors={value.recentColors}
                onCommit={(color) => commitCustomColor("surfaceColor", color)}
              />
            </div>
          ) : null}
        </fieldset>

        <div className="border-t border-border pt-4">
          <div className="text-sm font-medium">{t("settings.themeBackground")}</div>
          <div className="mt-2 flex flex-wrap gap-2">
            <Button type="button" variant="outline" disabled={backgroundBusy} onClick={() => void chooseBackground()}>
              {backgroundBusy ? <Loader2 className="mr-1 h-4 w-4 animate-spin" /> : <ImagePlus className="mr-1 h-4 w-4" />}
              {t("settings.themeChooseBackground")}
            </Button>
            {value.backgroundImagePath ? (
              <Button type="button" variant="outline" disabled={backgroundBusy} onClick={() => void removeBackground()}>
                <Trash2 className="mr-1 h-4 w-4" />
                {t("settings.themeClearBackground")}
              </Button>
            ) : null}
          </div>
          {value.backgroundImagePath ? (
            <div className="mt-3 grid gap-3">
              <fieldset>
                <legend className="text-xs text-muted-foreground">{t("settings.themeBackgroundScope")}</legend>
                <div className="mt-2 grid grid-cols-2 gap-2" role="radiogroup">
                  {BACKGROUND_SCOPE_OPTIONS.map((scope) => {
                    const selected = value.backgroundScope === scope;
                    return (
                      <button
                        key={scope}
                        type="button"
                        role="radio"
                        aria-checked={selected}
                        onClick={() => onChange({ backgroundScope: scope })}
                        className={cn(
                          "rounded-lg border px-3 py-2 text-left text-xs transition-colors",
                          selected ? "border-primary bg-primary/10 text-foreground" : "border-border bg-background text-muted-foreground hover:bg-muted",
                        )}
                      >
                        {t(scope === "workspace" ? "settings.themeBackgroundScopeWorkspace" : "settings.themeBackgroundScopeApp")}
                      </button>
                    );
                  })}
                </div>
              </fieldset>
              <label className="block text-xs text-muted-foreground">
                {t("settings.themeBackgroundOpacity")}: {Math.round(value.backgroundOpacity * 100)}%
                <input
                  type="range"
                  min="0"
                  max="1"
                  step="0.01"
                  value={value.backgroundOpacity}
                  onChange={(event) => onChange({ backgroundOpacity: Number(event.target.value) })}
                  className="mt-2 w-full accent-primary"
                />
              </label>
            </div>
          ) : null}
          {message ? <p className="mt-2 text-xs text-muted-foreground" role="status">{message}</p> : null}
        </div>
        <div>
          <Button type="button" variant="ghost" disabled={backgroundBusy} onClick={() => void resetTheme()}>
            <RotateCcw className="mr-1 h-4 w-4" />
            {t("settings.themeReset")}
          </Button>
        </div>
      </div>
    </div>
  );
}
