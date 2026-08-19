import { AlertTriangle, Moon, Sun } from "lucide-react";
import { useTranslation } from "react-i18next";
import type { ActiveView } from "../../lib/app-view";
import type { ThemeMode } from "../../lib/app-preferences";
import { Button } from "../ui/button";

export function WorkspaceHeader({
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
  error,
  onToggleTheme,
  onOpenDoctor,
  onRetryStartupOrHealth,
  onDismissDoctorPrompt,
  onStartRuntime,
}: {
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
  error: string;
  onToggleTheme: () => void;
  onOpenDoctor: () => void;
  onRetryStartupOrHealth: () => void;
  onDismissDoctorPrompt: () => void;
  onStartRuntime: () => void;
}) {
  const { t } = useTranslation();
  const activeTitle =
    activeView === "doctor"
      ? t("sidebar.doctor")
      : activeView === "optimization"
        ? t("sidebar.optimization")
        : activeView === "protection"
          ? t("encryption.protection")
          : activeView === "skills"
            ? t("sidebar.skills")
            : activeView === "settings"
              ? t("sidebar.settings")
              : activeChatTitle || t("header.newTask");

  return (
    <>
      <header className="flex h-12 shrink-0 items-center justify-between border-b border-border/80 px-3 md:px-5">
        <div className="flex min-w-0 items-center gap-2 text-sm">
          <span className="truncate text-muted-foreground">{activeProjectLabel}</span>
          <span className="text-muted-foreground">/</span>
          <span className="truncate font-medium">{activeTitle}</span>
        </div>
        <div className="flex shrink-0 items-center gap-2">
          <Button variant="ghost" className="h-9 w-9 px-0" onClick={onToggleTheme}>
            {theme === "dark" ? <Sun className="h-4 w-4" /> : <Moon className="h-4 w-4" />}
          </Button>
        </div>
      </header>

      {showDoctorStartupPrompt ? (
        <div className="mx-auto mt-3 w-full max-w-4xl px-4">
          <div className="flex min-w-0 items-center gap-3 rounded-md border border-amber-200 bg-amber-50 px-3 py-2 text-xs text-amber-900 shadow-panel dark:border-amber-900/40 dark:bg-amber-950/30 dark:text-amber-100">
            <AlertTriangle className="h-4 w-4 shrink-0" />
            <div className="min-w-0 flex-1 space-y-0.5">
              <div className="font-medium">{hasStartupIssue ? t("header.startupIssueDetected") : t("header.envNeedsAttention")}</div>
              <div className="break-words text-amber-900/80 dark:text-amber-100/80">
                {hasStartupIssue ? t("header.startupIssueDesc") : t("header.envNeedsAttentionDesc", { errors: healthErrors, warnings: healthWarnings })}
              </div>
              {hasStartupIssue ? <div className="break-words text-amber-900/70 dark:text-amber-100/70">{startupIssue}</div> : null}
            </div>
            <Button variant="outline" className="h-7 shrink-0 px-2 text-xs" onClick={onOpenDoctor} disabled={loadingDoctor}>
              {t("sidebar.doctor")}
            </Button>
            <Button variant="ghost" className="h-7 shrink-0 px-2 text-xs" onClick={onRetryStartupOrHealth} disabled={loading}>
              {loading ? t("doctor.retrying") : t("doctor.retry")}
            </Button>
            <Button variant="ghost" className="h-7 shrink-0 px-2 text-xs" onClick={onDismissDoctorPrompt}>
              {t("common.dismiss")}
            </Button>
          </div>
        </div>
      ) : null}

      {error && !showDoctorStartupPrompt ? (
        <div className="mx-auto mt-3 w-full max-w-4xl px-4">
          <div className="flex items-center gap-3 rounded-md border border-destructive/15 bg-destructive/5 px-3 py-2 text-xs text-destructive/75">
            <span className="break-words">{error}</span>
            <Button
              variant="ghost"
              className="ml-auto h-7 shrink-0 px-2 text-xs text-destructive/80 hover:bg-destructive/10"
              onClick={onStartRuntime}
              disabled={loading}
            >
              {loading ? t("header.reconnecting") : t("header.reconnect")}
            </Button>
          </div>
        </div>
      ) : null}
    </>
  );
}
