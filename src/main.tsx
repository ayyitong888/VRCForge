async function main() {
  const metrics = ((window as any).__vrcforgeStartupMetrics ||= {});
  metrics.mainModuleStartedMs ??= Math.round(performance.now());
  await window.__vrcforgeStartupShellPaintedPromise;
  const reactModule = import("react");
  const reactDomModule = import("react-dom/client");
  const i18nModule = import("./i18n").then(async (module) => {
    await module.initializeI18n();
    return module;
  });
  const appModule = import("./App");
  const appStyles = import.meta.glob("./styles.css", { eager: false });
  const [{ default: React }, { createRoot }, , , { default: App }] = await Promise.all([
    reactModule,
    reactDomModule,
    i18nModule,
    appStyles["./styles.css"](),
    appModule,
  ]);
  metrics.appDependenciesReadyMs ??= Math.round(performance.now());

  const root = createRoot(document.getElementById("root") as HTMLElement);
  root.render(React.createElement(React.StrictMode, null, React.createElement(App)));
  metrics.appRenderRequestedMs ??= Math.round(performance.now());
}

void main().catch(() => {
  document.documentElement.dataset.vrcforgeStartupShell = "error";
  const root = document.getElementById("root");
  if (root) {
    root.innerHTML = '<main class="flex h-screen items-center justify-center bg-workspace px-6 text-foreground"><div class="max-w-lg rounded-xl border border-destructive/40 bg-background p-6 text-center"><div class="font-semibold">VRCForge could not start</div><div class="mt-2 text-sm text-muted-foreground">Restart the app. If this continues, open the logs folder from the tray menu.</div></div></main>';
  }
});
