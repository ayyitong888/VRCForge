import { AlertTriangle, Check } from "lucide-react";
import { useTranslation } from "react-i18next";
import type { UnityToolsHealthDetail } from "../../lib/api";

const connectionMessages: Record<string, string> = {
  unity_compile_errors: "compileErrors",
  unity_core_package_incomplete: "missingCore",
  unity_core_connection_failed: "connection",
  unity_core_unavailable: "connection",
  unity_core_timeout: "connection",
  unity_core_project_busy: "busy",
  unity_project_not_selected: "selectProject",
  unity_editor_reload_dialog: "reloadDialog",
  unity_vrchat_sdk_missing: "vrchatSdkMissing",
  unity_newtonsoft_missing: "newtonsoftMissing",
};

export function CoreVersionNotice({ detail, bridgeStatus, toolsStatus }: {
  detail: unknown;
  bridgeStatus?: string;
  toolsStatus?: string;
}) {
  const { t } = useTranslation();
  const readiness = detail && typeof detail === "object" ? (detail as UnityToolsHealthDetail).readiness : undefined;
  const hasConnectionStatus = bridgeStatus !== undefined || toolsStatus !== undefined;
  if (!hasConnectionStatus && readiness?.ready !== false) return null;
  if (hasConnectionStatus && (readiness?.ready ?? toolsStatus === "ok") && bridgeStatus === "ok") {
    return <p className="mb-2 flex items-center gap-2 px-1 text-xs text-muted-foreground" data-unity-readiness="ready">
      <Check className="h-3.5 w-3.5 text-primary" aria-hidden="true" />
      {t("workspace.toolsReady")}
    </p>;
  }
  if (readiness?.ready !== false && !["ok", "warning", "error"].includes(bridgeStatus || "")) {
    return <p className="mb-2 px-1 text-xs text-muted-foreground" data-unity-readiness="checking">{t("workspace.unityConnectionChecking")}</p>;
  }
  const notice = readiness?.versionMismatch;
  const versionMismatch = readiness?.blockerCode === "unity_core_version_mismatch" && notice;
  const key = notice?.relation === "core_older" ? "coreOlder"
    : notice?.relation === "app_older" ? "appOlder" : "unknown";
  const blocker = readiness?.blockerCode || "";
  const connectionKey = Object.prototype.hasOwnProperty.call(connectionMessages, blocker)
    ? connectionMessages[blocker] : "unknown";
  const bridgeConnected = bridgeStatus === "ok";
  const bridgeUnavailable = bridgeStatus === "warning" || bridgeStatus === "error";
  const title = versionMismatch ? "coreVersion.title"
    : bridgeConnected ? "coreConnection.connectedToolsUnavailable"
    : bridgeUnavailable ? "workspace.unityBridgeNotReachable" : "coreConnection.title";
  return (
    <div role="alert" className="mb-3 min-w-0 border-l-2 border-amber-500 pl-2.5 text-sm" data-unity-readiness={bridgeConnected ? "blocked" : "unavailable"}>
      <div className="flex items-start gap-2 font-medium">
        <AlertTriangle className="mt-0.5 h-4 w-4 shrink-0 text-amber-600" aria-hidden="true" />
        <span>{t(title)}</span>
      </div>
      {versionMismatch ? <>
      <p className="mt-1 whitespace-normal break-words text-xs text-muted-foreground">
        {t("coreVersion.versions", {
          app: notice.appVersion, core: notice.coreVersion || t("coreVersion.unavailable"),
        })}
      </p>
      <p className="mt-1 whitespace-normal break-words text-xs">{t(`coreVersion.${key}`, { version: notice.appVersion })}</p>
      </> : connectionKey === "unknown" && hasConnectionStatus ? (
        <>
          <p className="mt-1 whitespace-normal break-words text-xs">{t("coreConnection.reviewEditor")}</p>
          <details className="mt-1.5 text-xs text-muted-foreground">
            <summary className="w-fit cursor-pointer">{t("coreConnection.moreGuidance")}</summary>
            <p className="mt-1 whitespace-normal break-words">{t("coreConnection.unknown")}</p>
          </details>
        </>
      ) : <p className="mt-1 whitespace-normal break-words text-xs">{t(`coreConnection.${connectionKey}`)}</p>}
    </div>
  );
}
