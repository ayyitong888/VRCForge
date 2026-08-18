import { Loader2, Pipette } from "lucide-react";
import { useEffect, useState } from "react";
import { useTranslation } from "react-i18next";
import {
  formatThemeColor,
  parseThemeColor,
  type ThemeColorFormat,
} from "../../lib/theme-color";
import { Button } from "../ui/button";

type EyeDropperApi = {
  open: () => Promise<{ sRGBHex: string }>;
};

type EyeDropperConstructor = new () => EyeDropperApi;

type ThemeColorEditorProps = {
  label: string;
  value: string;
  fallbackColor: string;
  recentColors: readonly string[];
  onCommit: (color: string) => void;
};

const FORMATS: readonly ThemeColorFormat[] = ["hex", "rgb", "hsl"];

const createDrafts = (color: string): Record<ThemeColorFormat, string> => ({
  hex: formatThemeColor(color, "hex"),
  rgb: formatThemeColor(color, "rgb"),
  hsl: formatThemeColor(color, "hsl"),
});

export function ThemeColorEditor({
  label,
  value,
  fallbackColor,
  recentColors,
  onCommit,
}: ThemeColorEditorProps) {
  const { t } = useTranslation();
  const resolvedColor = parseThemeColor(value) ?? fallbackColor;
  const [drafts, setDrafts] = useState(() => createDrafts(resolvedColor));
  const [invalidFormat, setInvalidFormat] = useState<ThemeColorFormat | null>(null);
  const [picking, setPicking] = useState(false);
  const eyeDropper = typeof window === "undefined"
    ? undefined
    : (window as Window & { EyeDropper?: EyeDropperConstructor }).EyeDropper;

  useEffect(() => {
    setDrafts(createDrafts(resolvedColor));
  }, [resolvedColor]);

  const updateDraft = (format: ThemeColorFormat, nextValue: string) => {
    setDrafts((current) => ({ ...current, [format]: nextValue }));
    setInvalidFormat(null);
  };

  const finishDraft = (format: ThemeColorFormat) => {
    const parsed = parseThemeColor(drafts[format]);
    if (parsed) {
      setInvalidFormat(null);
      onCommit(parsed);
      return;
    }
    setInvalidFormat(null);
    setDrafts(createDrafts(resolvedColor));
  };

  const pickScreenColor = async () => {
    if (!eyeDropper) return;
    setPicking(true);
    try {
      const result = await new eyeDropper().open();
      const parsed = parseThemeColor(result.sRGBHex);
      if (parsed) onCommit(parsed);
    } catch (error) {
      if (!(error instanceof DOMException) || error.name !== "AbortError") {
        setInvalidFormat("hex");
      }
    } finally {
      setPicking(false);
    }
  };

  return (
    <fieldset className="rounded-lg border border-border bg-background p-3">
      <legend className="px-1 text-sm font-medium">{label}</legend>
      <div className="mt-1 flex items-center gap-2">
        <input
          type="color"
          value={resolvedColor}
          onChange={(event) => onCommit(event.target.value)}
          className="h-10 w-14 cursor-pointer rounded-md border border-input bg-background p-1"
          aria-label={label}
        />
        {eyeDropper ? (
          <Button
            type="button"
            variant="outline"
            className="h-10 w-10 px-0"
            disabled={picking}
            onClick={() => void pickScreenColor()}
            aria-label={t("settings.themePickScreenColor")}
            title={t("settings.themePickScreenColor")}
          >
            {picking ? <Loader2 className="h-4 w-4 animate-spin" /> : <Pipette className="h-4 w-4" />}
          </Button>
        ) : null}
        <span className="h-8 flex-1 rounded-md border border-border" style={{ backgroundColor: resolvedColor }} aria-hidden="true" />
      </div>
      <div className="mt-3 grid gap-2">
        {FORMATS.map((format) => (
          <label key={format} className="grid grid-cols-[2.75rem_minmax(0,1fr)] items-center gap-2 text-xs text-muted-foreground">
            <span className="uppercase">{format}</span>
            <input
              type="text"
              value={drafts[format]}
              onChange={(event) => updateDraft(format, event.target.value)}
              onBlur={() => finishDraft(format)}
              onKeyDown={(event) => {
                if (event.nativeEvent.isComposing) return;
                if (event.key === "Enter") {
                  event.preventDefault();
                  // Blur owns the single commit so Enter cannot apply twice.
                  event.currentTarget.blur();
                }
              }}
              aria-label={`${label} ${format.toUpperCase()}`}
              aria-invalid={invalidFormat === format}
              className="min-w-0 rounded-md border border-input bg-card px-2 py-1.5 font-mono text-xs text-foreground outline-none focus:ring-2 focus:ring-ring"
            />
          </label>
        ))}
      </div>
      {invalidFormat ? <p className="mt-2 text-xs text-destructive" role="alert">{t("settings.themeColorInvalid")}</p> : null}
      {recentColors.length ? (
        <div className="mt-3">
          <div className="text-xs text-muted-foreground">{t("settings.themeRecentColors")}</div>
          <div className="mt-1.5 flex gap-2">
            {recentColors.map((color) => (
              <button
                key={color}
                type="button"
                onClick={() => onCommit(color)}
                className="h-7 w-7 rounded-full border border-border shadow-sm outline-none focus:ring-2 focus:ring-ring"
                style={{ backgroundColor: color }}
                aria-label={color.toUpperCase()}
                title={color.toUpperCase()}
              />
            ))}
          </div>
        </div>
      ) : null}
    </fieldset>
  );
}
