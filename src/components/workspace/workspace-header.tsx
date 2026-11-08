import { AlertTriangle, Moon, Sun } from "lucide-react";
import { useTranslation } from "react-i18next";
import type { ActiveView } from "../../lib/app-view";
import type { ThemeMode } from "../../lib/app-preferences";
import { Button } from "../ui/button";

export function WorkspaceHeader({
  hideWindowHeader = false,
  activeProjectLabel,
  activeView,
  activeChatTitle,
  theme,
  showDoctorStartupPrompt,
  hasStartupIssue,
  healthErrors,
  healthWarnings,
  startupIssue,
  loadingDoctor,
  loading,
  runtimeStarting,
  error,
  chatStorageError,
  chatStorageNeedsDoctor,
  onToggleTheme,
  onOpenDoctor,
  onRetryStartupOrHealth,
  onDismissDoctorPrompt,
  onDismissError,
  onRetryChatStorage,
}: {
  hideWindowHeader?: boolean;
  activeProjectLabel: string;
  activeView: ActiveView;
  activeChatTitle: string;
  theme: ThemeMode;
  showDoctorStartupPrompt: boolean;
  hasStartupIssue: boolean;
  healthErrors: number;
  healthWarnings: number;
  startupIssue: string;
  loadingDoctor: boolean;
  loading: boolean;
  runtimeStarting?: boolean;
  error: string;
  chatStorageError?: string;
  chatStorageNeedsDoctor?: boolean;
  onToggleTheme: () => void;
  onOpenDoctor: () => void;
  onRetryStartupOrHealth: () => void;
  onDismissDoctorPrompt: () => void;
  onDismissError?: () => void;
  onRetryChatStorage?: () => void;
}) {
  const { t } = useTranslation();
  const showErrorBanner = Boolean(error.trim()) && error.trim() !== startupIssue.trim();
  const showChatStorageError = Boolean(chatStorageError?.trim());
  const activeTitle =
    activeView === "doctor"
      ? t("sidebar.doctor")
      : activeView === "protection"
          ? t("encryption.protection")
          : activeView === "skills"
            ? t("sidebar.skills")
            : activeView === "settings"
              ? t("sidebar.settings")
              : activeChatTitle || t("header.newTask");

  return (
    <>
      {!hideWindowHeader && <header className="flex h-12 shrink-0 items-center justify-between border-b border-border/80 px-3 md:px-5">
        <div className="flex min-w-0 items-center gap-2 text-sm">
          {activeView !== "settings" && <>
            <span className="truncate text-muted-foreground">{activeProjectLabel}</span>
            <span className="text-muted-foreground">/</span>
          </>}
          <span className="truncate font-medium">{activeTitle}</span>
        </div>
        <div className="flex shrink-0 items-center gap-2">
          <Button variant="ghost" className="h-9 w-9 px-0" onClick={onToggleTheme}>
            {theme === "dark" ? <Sun className="h-4 w-4" /> : <Moon className="h-4 w-4" />}
          </Button>
        </div>
      </header>}

      {runtimeStarting && !hasStartupIssue ? (
        <div role="status" className="px-4 py-1 text-xs text-muted-foreground">{t("header.reconnecting")}</div>
      ) : null}

      {hasStartupIssue || showDoctorStartupPrompt ? (
        <div className="mx-auto mt-3 w-full max-w-4xl px-4">
          <div
            role={hasStartupIssue ? "alert" : "status"}
            className={hasStartupIssue
              ? "flex min-w-0 items-center gap-3 rounded-md border border-destructive/15 bg-destructive/5 px-3 py-2 text-xs text-destructive"
              : "flex min-w-0 items-center gap-3 rounded-md border border-amber-200 bg-amber-50 px-3 py-2 text-xs text-amber-900 dark:border-amber-900/40 dark:bg-amber-950/30 dark:text-amber-100"}
          >
            <AlertTriangle className="h-4 w-4 shrink-0" />
            <div className="min-w-0 flex-1 space-y-0.5">
              <div className="font-medium">{hasStartupIssue ? t("header.startupIssueDetected") : t("header.envNeedsAttention")}</div>
              <div className="break-words">
                {hasStartupIssue ? t("header.startupIssueDesc") : t("header.envNeedsAttentionDesc", { errors: healthErrors, warnings: healthWarnings })}
              </div>
              {hasStartupIssue ? <div className="break-words">{startupIssue}</div> : null}
            </div>
            {activeView !== "doctor" && <Button variant="outline" className="h-7 shrink-0 px-2 text-xs" onClick={onOpenDoctor} disabled={loadingDoctor}>
              {t("sidebar.doctor")}
            </Button>}
            <Button variant="ghost" className="h-7 shrink-0 px-2 text-xs" onClick={onRetryStartupOrHealth} disabled={loading}>
              {loading ? t("doctor.retrying") : t("doctor.retry")}
            </Button>
            {!hasStartupIssue && <Button variant="ghost" className="h-7 shrink-0 px-2 text-xs" onClick={onDismissDoctorPrompt}>
              {t("common.dismiss")}
            </Button>}
          </div>
        </div>
      ) : null}

      {showErrorBanner ? (
        <div className="mx-auto mt-3 w-full max-w-4xl px-4">
          <div className="flex items-center gap-3 px-3 py-1 text-xs text-muted-foreground" role="alert" data-workspace-error>
            <AlertTriangle className="h-3.5 w-3.5 shrink-0" aria-hidden="true" />
            <span className="break-words">{error}</span>
            {onDismissError && <Button variant="ghost" className="ml-auto h-7 shrink-0 px-2 text-xs" onClick={onDismissError}>
              {t("common.dismiss")}
            </Button>}
          </div>
        </div>
      ) : null}

      {showChatStorageError ? (
        <div className="mx-auto mt-3 w-full max-w-4xl px-4">
          <div className="flex items-center gap-3 rounded-md border border-destructive/15 bg-destructive/5 px-3 py-2 text-xs text-destructive/75" role="alert" data-workspace-chat-storage-error>
            <span className="break-words">{chatStorageError}</span>
            {(chatStorageNeedsDoctor ? activeView !== "doctor" : onRetryChatStorage) ? (
              <Button
                variant="ghost"
                className="ml-auto h-7 shrink-0 px-2 text-xs text-destructive/80 hover:bg-destructive/10"
                onClick={chatStorageNeedsDoctor ? onOpenDoctor : onRetryChatStorage}
              >
                {t(chatStorageNeedsDoctor ? "sidebar.doctor" : "doctor.retry")}
              </Button>
            ) : null}
          </div>
        </div>
      ) : null}
    </>
  );
}
