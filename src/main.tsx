import React from "react";
import { flushSync } from "react-dom";
import ReactDOM from "react-dom/client";
import { initializeI18n } from "./i18n";
import "./styles.css";

function StartupShell() {
  return (
    <main className="flex h-screen items-center justify-center bg-workspace text-foreground" data-vrcforge-startup-shell>
      <div className="rounded-2xl border border-border/70 bg-background/80 px-8 py-6 text-center shadow-panel">
        <div className="text-lg font-semibold">VRCForge</div>
        <div className="mt-2 text-sm text-muted-foreground">Local AI Workbench for VRChat Avatar Editing</div>
      </div>
    </main>
  );
}

async function main() {
  const metrics = ((window as any).__vrcforgeStartupMetrics ||= {});
  metrics.mainModuleStartedMs ??= Math.round(performance.now());
  const root = ReactDOM.createRoot(document.getElementById("root") as HTMLElement);
  flushSync(() => root.render(<StartupShell />));
  metrics.startupShellRequestedMs ??= Math.round(performance.now());
  const startupShellPainted = new Promise<void>((resolve) => {
    window.requestAnimationFrame(() => {
      window.requestAnimationFrame(() => {
        metrics.startupShellPaintedMs ??= Math.round(performance.now());
        document.documentElement.dataset.vrcforgeStartupShell = "ready";
        resolve();
      });
    });
  });

  const appModule = import("./App");
  const [, { default: App }] = await Promise.all([initializeI18n(), appModule, startupShellPainted]);
  metrics.appDependenciesReadyMs ??= Math.round(performance.now());

  root.render(
    <React.StrictMode>
      <App />
    </React.StrictMode>,
  );
  metrics.appRenderRequestedMs ??= Math.round(performance.now());
}

void main().catch(() => {
  document.documentElement.dataset.vrcforgeStartupShell = "error";
  const root = document.getElementById("root");
  if (root) {
    root.innerHTML = '<main class="flex h-screen items-center justify-center bg-workspace px-6 text-foreground"><div class="max-w-lg rounded-xl border border-destructive/40 bg-background p-6 text-center"><div class="font-semibold">VRCForge could not start</div><div class="mt-2 text-sm text-muted-foreground">Restart the app. If this continues, open the logs folder from the tray menu.</div></div></main>';
  }
});
