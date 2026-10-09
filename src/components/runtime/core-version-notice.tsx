import { AlertTriangle } from "lucide-react";
import { useTranslation } from "react-i18next";
import type { UnityToolsHealthDetail } from "../../lib/api";

const connectionMessages: Record<string, string> = {
  unity_compile_errors: "compileErrors",
  unity_core_package_incomplete: "missingCore",
  unity_core_connection_failed: "connection",
  unity_core_timeout: "connection",
  unity_core_project_busy: "busy",
  unity_project_not_selected: "selectProject",
  unity_editor_reload_dialog: "reloadDialog",
};

export function CoreVersionNotice({ detail }: { detail: unknown }) {
  const { t } = useTranslation();
  if (!detail || typeof detail !== "object") return null;
  const readiness = (detail as UnityToolsHealthDetail).readiness;
  if (readiness?.ready !== false) return null;
  const notice = readiness?.versionMismatch;
  const versionMismatch = readiness.blockerCode === "unity_core_version_mismatch" && notice;
  const key = notice?.relation === "core_older" ? "coreOlder"
    : notice?.relation === "app_older" ? "appOlder" : "unknown";
  const blocker = readiness.blockerCode || "";
  const connectionKey = Object.prototype.hasOwnProperty.call(connectionMessages, blocker)
    ? connectionMessages[blocker] : "unknown";
  return (
    <div role="alert" className="mx-3 my-2 min-w-0 border-l-2 border-amber-500 pl-3 text-sm">
      <div className="flex items-start gap-2 font-medium">
        <AlertTriangle className="mt-0.5 h-4 w-4 shrink-0 text-amber-600" aria-hidden="true" />
        <span>{t(versionMismatch ? "coreVersion.title" : "coreConnection.title")}</span>
      </div>
      {versionMismatch ? <>
      <p className="mt-1 whitespace-normal break-words text-xs text-muted-foreground">
        {t("coreVersion.versions", {
          app: notice.appVersion, core: notice.coreVersion || t("coreVersion.unavailable"),
        })}
      </p>
      <p className="mt-1 whitespace-normal break-words text-xs">{t(`coreVersion.${key}`, { version: notice.appVersion })}</p>
      </> : <p className="mt-1 whitespace-normal break-words text-xs">{t(`coreConnection.${connectionKey}`)}</p>}
    </div>
  );
}
