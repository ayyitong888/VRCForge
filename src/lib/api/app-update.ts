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

export async function checkAppUpdate(
  endpoint: string,
  signal?: AbortSignal,
): Promise<AppUpdateResult> {
  if (hasTauriInternals()) {
    return invokeTauriWithAbort<AppUpdateResult>("check_app_update", {
      request: { timeoutMs: 4000 },
    }, signal);
  }
  return requestJson<AppUpdateResult>(`${endpoint}/api/app/update`, { timeoutMs: 4000, signal });
}
