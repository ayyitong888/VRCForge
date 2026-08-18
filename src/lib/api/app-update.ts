import { hasTauriInternals, invokeTauriWithAbort, requestJson } from "./http";

export type AppUpdateStatus = "update_available" | "up_to_date" | "unavailable" | "cancelled";

export type AppUpdateResult = {
  ok: boolean;
  schema: "vrcforge.app_update.v1";
  status: AppUpdateStatus;
  currentVersion: string;
  latestVersion: string;
  releaseUrl: string;
  shouldNotify: boolean;
};

export type AppUpdatePromptState = {
  source: "startup" | "tray";
  result: AppUpdateResult | null;
};

export async function checkAppUpdate(
  endpoint: string,
  signal?: AbortSignal,
  refresh = false,
): Promise<AppUpdateResult> {
  if (hasTauriInternals()) {
    return invokeTauriWithAbort<AppUpdateResult>("check_app_update", {
      request: { timeoutMs: 4000, refresh },
    }, signal);
  }
  const suffix = refresh ? "?refresh=true" : "";
  return requestJson<AppUpdateResult>(`${endpoint}/api/app/update${suffix}`, { timeoutMs: 4000, signal });
}

export async function openAppReleaseUrl(releaseUrl: string): Promise<void> {
  if (hasTauriInternals()) {
    await invokeTauriWithAbort<void>("open_app_release_url", {
      request: { releaseUrl },
    });
    return;
  }
  const opened = window.open(releaseUrl, "_blank", "noopener,noreferrer");
  if (!opened) {
    throw new Error("The browser blocked the GitHub Releases window.");
  }
}
