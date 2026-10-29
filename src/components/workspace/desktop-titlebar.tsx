import { useEffect, useState } from "react";
import { getVersion } from "@tauri-apps/api/app";
import { isTauri } from "@tauri-apps/api/core";
import { getCurrentWindow } from "@tauri-apps/api/window";
import { Copy, Minus, Moon, Square, Sun, X } from "lucide-react";
import { useTranslation } from "react-i18next";
import type { ThemeMode } from "../../lib/app-preferences";

export function DesktopTitlebar({ theme, onToggleTheme }: {
  theme: ThemeMode;
  onToggleTheme: () => void;
}) {
  const { t } = useTranslation();
  const desktop = isTauri();
  const [version, setVersion] = useState("");
  const [maximized, setMaximized] = useState(false);
  const [error, setError] = useState("");

  useEffect(() => {
    if (!desktop) return;
    const appWindow = getCurrentWindow();
    let disposed = false;
    let unlisten: (() => void) | undefined;
    const report = (cause: unknown) => {
      if (!disposed) setError(cause instanceof Error ? cause.message : String(cause));
    };
    const updateMaximized = () => appWindow.isMaximized().then((value) => {
      if (!disposed) setMaximized(value);
    });
    void getVersion().then((value) => { if (!disposed) setVersion(value); }).catch(report);
    void updateMaximized().catch(report);
    void appWindow.onResized(() => { void updateMaximized().catch(report); }).then((stop) => {
      if (disposed) stop(); else unlisten = stop;
    }).catch(report);
    return () => { disposed = true; unlisten?.(); };
  }, [desktop]);

  if (!desktop) return null;

  async function control(action: () => Promise<void>) {
    try { await action(); setError(""); }
    catch (cause) { setError(cause instanceof Error ? cause.message : String(cause)); }
  }

  const appWindow = getCurrentWindow();
  return (
    <header className="desktop-titlebar" data-desktop-titlebar data-tauri-drag-region>
      <div className="desktop-titlebar-brand" data-tauri-drag-region>
        <span data-tauri-drag-region>VRCForge</span>
        <span className="desktop-titlebar-version" data-app-version data-tauri-drag-region>{version}</span>
      </div>
      <span className="desktop-titlebar-space" data-tauri-drag-region />
      {error ? <span className="desktop-titlebar-error" role="alert">{error}</span> : null}
      <button type="button" className="desktop-titlebar-theme" aria-label={t("window.toggleTheme")}
        title={t("window.toggleTheme")} onClick={onToggleTheme}>
        {theme === "dark" ? <Sun size={15} /> : <Moon size={15} />}
      </button>
      <button type="button" aria-label={t("window.minimize")} title={t("window.minimize")}
        onClick={() => void control(() => appWindow.minimize())}><Minus size={16} /></button>
      <button type="button" aria-label={t(maximized ? "window.restore" : "window.maximize")}
        title={t(maximized ? "window.restore" : "window.maximize")}
        onClick={() => void control(() => appWindow.toggleMaximize())}>
        {maximized ? <Copy size={13} /> : <Square size={13} />}
      </button>
      <button type="button" className="desktop-titlebar-close" aria-label={t("common.close")}
        title={t("common.close")} onClick={() => void control(() => appWindow.close())}><X size={17} /></button>
    </header>
  );
}
