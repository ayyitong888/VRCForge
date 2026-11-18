import { initializeStartupSplash, showWorkspaceLoadFailure } from "./lib/startup-splash";
import { installDesktopDropGuard } from "./lib/desktop-drop-guard";

initializeStartupSplash();
installDesktopDropGuard();

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
  const boundaryModule = import("./error-boundary");
  const appStyles = import.meta.glob("./styles.css", { eager: false });
  const [{ default: React }, { createRoot }, , , { default: App }, { RootErrorBoundary }] = await Promise.all([
    reactModule,
    reactDomModule,
    i18nModule,
    appStyles["./styles.css"](),
    appModule,
    boundaryModule,
  ]);
  metrics.appDependenciesReadyMs ??= Math.round(performance.now());

  const root = createRoot(document.getElementById("root") as HTMLElement);
  root.render(React.createElement(React.StrictMode, null, React.createElement(RootErrorBoundary, null, React.createElement(App))));
  metrics.appRenderRequestedMs ??= Math.round(performance.now());
}

void main().catch(error => {
  console.error("[vrcforge:startup]", error);
  showWorkspaceLoadFailure();
});
