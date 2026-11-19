import { ChevronDown, Loader2, Pipette } from "lucide-react";
import { useEffect, useId, useRef, useState } from "react";
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
const RGB_CHANNELS = ["R", "G", "B"] as const;

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
  const hex = formatThemeColor(resolvedColor, "hex");
  const channels = [1, 3, 5].map((offset) => Number.parseInt(resolvedColor.slice(offset, offset + 2), 16));
  const pickerId = useId();
  const pickerButton = useRef<HTMLButtonElement>(null);
  const [pickerOpen, setPickerOpen] = useState(false);
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

  const updateChannel = (index: number, value: number) => {
    const rgb = channels.map((channel, position) => position === index ? value : channel);
    const parsed = parseThemeColor(`rgb(${rgb.join(", ")})`);
    if (parsed) onCommit(parsed);
  };

  return (
    <fieldset className="rounded-lg border border-border bg-background p-3">
      <legend className="px-1 text-sm font-medium">{label}</legend>
      <div className="mt-1 flex items-center gap-2">
        <button
          ref={pickerButton}
          type="button"
          aria-expanded={pickerOpen}
          aria-controls={pickerId}
          aria-label={`${label} ${hex}`}
          onClick={() => setPickerOpen((open) => !open)}
          className="flex h-10 min-w-0 flex-1 items-center gap-2 rounded-md border border-input bg-card px-2 text-foreground hover:bg-muted focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring"
        >
          <span className="h-6 w-8 shrink-0 rounded-sm border border-border" style={{ backgroundColor: resolvedColor }} aria-hidden="true" />
          <span className="min-w-0 flex-1 truncate text-left font-mono text-xs">{hex}</span>
          <ChevronDown className={pickerOpen ? "h-4 w-4 shrink-0 rotate-180" : "h-4 w-4 shrink-0"} aria-hidden="true" />
        </button>
        {eyeDropper ? (
          <Button
            type="button"
            variant="outline"
            className="h-10 w-10 px-0"
            disabled={picking}
            onClick={() => void pickScreenColor()}
            aria-label={t("settings.themePickScreenColor")}
            data-app-tooltip={t("settings.themePickScreenColor")}
          >
            {picking ? <Loader2 className="h-4 w-4 animate-spin" /> : <Pipette className="h-4 w-4" />}
          </Button>
        ) : null}
      </div>
      <div id={pickerId} hidden={!pickerOpen} onKeyDown={(event) => {
        if (event.key !== "Escape") return;
        event.stopPropagation();
        setPickerOpen(false);
        pickerButton.current?.focus();
      }}>
        <div role="group" aria-label={`${label} RGB`} className="mt-2 grid gap-2 rounded-md border border-border bg-card p-2">
          {RGB_CHANNELS.map((channel, index) => {
            const start = channels.map((value, position) => position === index ? 0 : value);
            const end = channels.map((value, position) => position === index ? 255 : value);
            return (
              <label key={channel} className="grid grid-cols-[1rem_minmax(0,1fr)_2rem] items-center gap-2 font-mono text-xs text-muted-foreground">
                <span>{channel}</span>
                <input
                  type="range"
                  min={0}
                  max={255}
                  value={channels[index]}
                  aria-label={`${label} RGB ${channel}`}
                  onChange={(event) => updateChannel(index, event.currentTarget.valueAsNumber)}
                  className="h-2 min-w-0 cursor-pointer appearance-none rounded-full focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring [&::-webkit-slider-thumb]:h-4 [&::-webkit-slider-thumb]:w-4 [&::-webkit-slider-thumb]:appearance-none [&::-webkit-slider-thumb]:rounded-full [&::-webkit-slider-thumb]:border-2 [&::-webkit-slider-thumb]:border-foreground/70 [&::-webkit-slider-thumb]:bg-background"
                  style={{ background: `linear-gradient(to right, rgb(${start.join(",")}), rgb(${end.join(",")}))` }}
                />
                <span className="text-right tabular-nums" aria-hidden="true">{channels[index]}</span>
              </label>
            );
          })}
        </div>
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
                data-app-tooltip={color.toUpperCase()}
              />
            ))}
          </div>
        </div>
      ) : null}
    </fieldset>
  );
}
