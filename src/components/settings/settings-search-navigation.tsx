import { Search, X } from "lucide-react";
import { useRef, useState, type ReactNode } from "react";
import { useTranslation } from "react-i18next";
import { searchSettings, SETTINGS_SECTION_LABELS, SETTINGS_SEARCH_CONTEXT_LABELS, type SettingsSearchEntry } from "../../lib/settings-search";

export function SettingsSearchNavigation({ developerOptionsEnabled, onSelect, children }: {
  developerOptionsEnabled: boolean;
  onSelect: (entry: SettingsSearchEntry) => void;
  children: ReactNode;
}) {
  const { t } = useTranslation();
  const [query, setQuery] = useState("");
  const input = useRef<HTMLInputElement>(null);
  const results = useRef<HTMLUListElement>(null);
  const matches = searchSettings(query, (key) => t(key), developerOptionsEnabled);
  const searching = Boolean(query.trim());
  return (
    <div className="mt-4 min-w-0" data-settings-search>
      <div role="search" className="flex h-10 min-w-0 items-center gap-2 rounded-full bg-muted px-3 focus-within:ring-2 focus-within:ring-ring/60">
        <Search className="h-4 w-4 shrink-0 text-muted-foreground" aria-hidden="true" />
        <input ref={input} type="search" value={query} aria-label={t("settings.searchPlaceholder")} placeholder={t("settings.searchPlaceholder")}
          className="min-w-0 flex-1 bg-transparent text-sm outline-none placeholder:text-muted-foreground [&::-webkit-search-cancel-button]:hidden"
          onChange={(event) => setQuery(event.target.value)}
          onKeyDown={(event) => {
            if (event.key === "Escape") { event.preventDefault(); setQuery(""); }
            if (event.key === "ArrowDown" && matches.length) { event.preventDefault(); results.current?.querySelector<HTMLButtonElement>("button")?.focus(); }
            if (event.key === "Enter" && matches.length) { event.preventDefault(); onSelect(matches[0]); }
          }} />
        {query ? <button type="button" aria-label={t("settings.searchClear")} className="flex h-6 w-6 shrink-0 items-center justify-center rounded-full text-muted-foreground hover:bg-background focus-visible:outline focus-visible:outline-2 focus-visible:outline-ring" onClick={() => { setQuery(""); input.current?.focus(); }}><X className="h-3.5 w-3.5" aria-hidden="true" /></button> : null}
      </div>
      {searching ? <>
        <p role="status" className="px-3 pb-1 pt-4 text-xs text-muted-foreground">{t(matches.length ? "settings.searchCount" : "settings.searchEmpty", { count: matches.length })}</p>
        <ul ref={results} className="space-y-1" aria-label={t("settings.searchResults")}
          onKeyDown={(event) => {
            if (event.key === "Escape") { event.preventDefault(); setQuery(""); input.current?.focus(); return; }
            if (!["ArrowDown", "ArrowUp"].includes(event.key)) return;
            const buttons = [...(results.current?.querySelectorAll<HTMLButtonElement>("button") || [])];
            const index = buttons.indexOf(document.activeElement as HTMLButtonElement);
            event.preventDefault();
            const next = index + (event.key === "ArrowDown" ? 1 : -1);
            if (next < 0) input.current?.focus(); else buttons[Math.min(next, buttons.length - 1)]?.focus();
          }}>
          {matches.map((entry) => <li key={entry.id}><button type="button" data-settings-search-result={entry.id} onClick={() => onSelect(entry)}
            className="w-full min-w-0 rounded-lg px-3 py-2.5 text-left hover:bg-muted focus-visible:bg-muted focus-visible:outline focus-visible:outline-2 focus-visible:outline-ring">
            <span className="block break-words text-sm leading-5">{t(entry.labelKey)}</span>
            <span className="mt-0.5 block text-xs leading-4 text-muted-foreground">{t(SETTINGS_SECTION_LABELS[entry.section])}{SETTINGS_SEARCH_CONTEXT_LABELS[entry.panel] ? ` · ${t(SETTINGS_SEARCH_CONTEXT_LABELS[entry.panel])}` : ""}</span>
          </button></li>)}
        </ul>
      </> : children}
    </div>
  );
}
