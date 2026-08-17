import { ImagePlus, RotateCcw, Trash2 } from "lucide-react";
import { useRef, useState, type ChangeEvent } from "react";
import { useTranslation } from "react-i18next";
import { MAX_THEME_BACKGROUND_BYTES, type ThemeCustomization } from "../../lib/theme-customization";
import { Button } from "../ui/button";

type ThemeCustomizationPanelProps = {
  value: ThemeCustomization;
  onChange: (next: Partial<ThemeCustomization>) => void;
  onReset: () => void;
};

export function ThemeCustomizationPanel({ value, onChange, onReset }: ThemeCustomizationPanelProps) {
  const { t } = useTranslation();
  const pickerRef = useRef<HTMLInputElement>(null);
  const [message, setMessage] = useState("");

  const pickBackground = (event: ChangeEvent<HTMLInputElement>) => {
    const file = event.target.files?.[0];
    event.target.value = "";
    if (!file) return;
    if (!file.type.startsWith("image/") || file.size > MAX_THEME_BACKGROUND_BYTES) {
      setMessage(t("settings.themeBackgroundError"));
      return;
    }
    const reader = new FileReader();
    reader.onload = () => {
      const result = typeof reader.result === "string" ? reader.result : "";
      onChange({ backgroundImageDataUrl: result });
      setMessage(t("settings.themeBackgroundReady"));
    };
    reader.onerror = () => setMessage(t("settings.themeBackgroundError"));
    reader.readAsDataURL(file);
  };

  return (
    <div data-vrcforge-theme-customization>
      <h2 className="text-base font-semibold">{t("settings.themeCustomization")}</h2>
      <p className="mt-1 text-sm text-muted-foreground">{t("settings.themeCustomizationDesc")}</p>
      <div className="mt-4 grid gap-4 rounded-xl border border-border bg-card p-4">
        <label className="flex items-center justify-between gap-4 text-sm font-medium">
          {t("settings.themeAccent")}
          <input
            type="color"
            value={value.accentColor || "#2563eb"}
            onChange={(event) => onChange({ accentColor: event.target.value })}
            className="h-9 w-14 cursor-pointer rounded-md border border-input bg-background p-1"
            aria-label={t("settings.themeAccent")}
          />
        </label>
        <div>
          <div className="text-sm font-medium">{t("settings.themeBackground")}</div>
          <div className="mt-2 flex flex-wrap gap-2">
            <input ref={pickerRef} type="file" accept="image/png,image/jpeg,image/webp,image/gif" className="hidden" onChange={pickBackground} />
            <Button type="button" variant="outline" onClick={() => pickerRef.current?.click()}>
              <ImagePlus className="mr-1 h-4 w-4" />
              {t("settings.themeChooseBackground")}
            </Button>
            {value.backgroundImageDataUrl ? (
              <Button type="button" variant="outline" onClick={() => onChange({ backgroundImageDataUrl: "" })}>
                <Trash2 className="mr-1 h-4 w-4" />
                {t("settings.themeClearBackground")}
              </Button>
            ) : null}
          </div>
          {value.backgroundImageDataUrl ? (
            <label className="mt-3 block text-xs text-muted-foreground">
              {t("settings.themeBackgroundOpacity")}: {Math.round(value.backgroundOpacity * 100)}%
              <input
                type="range"
                min="0.06"
                max="0.5"
                step="0.01"
                value={value.backgroundOpacity}
                onChange={(event) => onChange({ backgroundOpacity: Number(event.target.value) })}
                className="mt-2 w-full accent-primary"
              />
            </label>
          ) : null}
          {message ? <p className="mt-2 text-xs text-muted-foreground" role="status">{message}</p> : null}
        </div>
        <div>
          <Button type="button" variant="ghost" onClick={onReset}>
            <RotateCcw className="mr-1 h-4 w-4" />
            {t("settings.themeReset")}
          </Button>
        </div>
      </div>
    </div>
  );
}
