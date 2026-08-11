import { Box, Boxes, Folder, ListChecks, Monitor, PanelRightClose, PlugZap, RefreshCw, Server, Wrench } from "lucide-react";
import { useTranslation } from "react-i18next";

import type { AgentProgress } from "../../lib/api";
import { cn } from "../../lib/utils";
import { AgentTodoPanel } from "./agent-todo-panel";
import { RuntimeInfoRow, StatusDot } from "./runtime-sidebar-ui";

type ComponentStatus = { status: string; message?: string } | null | undefined;

function normalizedStatus(component: ComponentStatus): string {
  return component?.status || "unknown";
}

export function RightRuntimeSidebar({
  runtimeConnected,
  loadingUnityStatus,
  hasEnvironmentAttention,
  hasStartupIssue,
  workspaceProjectLabel,
  selectedProjectComponent,
  backendComponent,
  mcpPackageComponent,
  unityBridgeComponent,
  unityInstanceComponent,
  unityToolsComponent,
  agentProgress,
  approvalsLoaded,
  pendingApprovals,
  refreshUnityStatus,
  onHideSidebar,
  openDoctor,
  localizeHealthMessage,
}: {
  runtimeConnected: boolean;
  loadingUnityStatus: boolean;
  hasEnvironmentAttention: boolean;
  hasStartupIssue: boolean;
  workspaceProjectLabel: string;
  selectedProjectComponent: ComponentStatus;
  backendComponent: ComponentStatus;
  mcpPackageComponent: ComponentStatus;
  unityBridgeComponent: ComponentStatus;
  unityInstanceComponent: ComponentStatus;
  unityToolsComponent: ComponentStatus;
  agentProgress: AgentProgress[];
  approvalsLoaded: boolean;
  pendingApprovals: number;
  refreshUnityStatus: () => void | Promise<void>;
  onHideSidebar: () => void;
  openDoctor: () => void | Promise<void>;
  localizeHealthMessage: (message?: string) => string;
}) {
  const { t } = useTranslation();
  const componentValue = (component: ComponentStatus) =>
    localizeHealthMessage(component?.message) || t("workspace.runStatusUnknown");
  const backendStatus = backendComponent?.status || "unknown";
  const backendValue = localizeHealthMessage(backendComponent?.message)
    || (runtimeConnected ? t("workspace.online") : t("workspace.notLoaded"));
  const approvalStatus = !approvalsLoaded ? "unknown" : pendingApprovals > 0 ? "warning" : "ok";
  const approvalValue = !approvalsLoaded
    ? t("workspace.notLoaded")
    : pendingApprovals > 0
      ? t("workspace.pendingApprovals", { count: pendingApprovals })
      : t("workspace.noPendingApprovals");

  return (
    <aside
      className="flex h-screen min-w-0 flex-col overflow-hidden border-l border-border/80 bg-sidebar"
      data-vrcforge-environment-status
    >
      <div className="flex h-12 shrink-0 items-center gap-2 border-b border-border/80 px-3">
        <div className="min-w-0 flex-1 truncate text-sm font-semibold">{t("workspace.environment")}</div>
        <button
          type="button"
          className="flex h-8 w-8 shrink-0 items-center justify-center rounded-md text-muted-foreground transition-colors hover:bg-muted hover:text-foreground disabled:opacity-50"
          onClick={() => void refreshUnityStatus()}
          title={t("workspace.refreshStatus")}
          disabled={!runtimeConnected || loadingUnityStatus}
        >
          <RefreshCw className={cn("h-4 w-4", loadingUnityStatus && "animate-spin")} />
        </button>
        <button
          type="button"
          className="flex h-8 w-8 shrink-0 items-center justify-center rounded-md text-muted-foreground transition-colors hover:bg-muted hover:text-foreground"
          onClick={onHideSidebar}
          title={t("workspace.hideSidebar")}
        >
          <PanelRightClose className="h-4 w-4" />
        </button>
      </div>

      <div className="app-scrollbar min-h-0 flex-1 overflow-y-auto px-3 py-3">
        <AgentTodoPanel progress={agentProgress} />

        <div className="rounded-xl border border-border/80 bg-background/65 px-3 py-2 shadow-sm">
          <div data-vrcforge-status="project">
            <RuntimeInfoRow
              icon={<Folder className="h-4 w-4" />}
              label={t("workspace.project")}
              value={workspaceProjectLabel}
              suffix={<StatusDot status={normalizedStatus(selectedProjectComponent)} />}
            />
          </div>
          <div data-vrcforge-status="core">
            <RuntimeInfoRow
              icon={<Server className="h-4 w-4" />}
              label={t("workspace.core")}
              value={backendValue}
              suffix={<StatusDot status={backendStatus} />}
            />
          </div>
          <div data-vrcforge-status="mcp-core">
            <RuntimeInfoRow
              icon={<Box className="h-4 w-4" />}
              label={t("workspace.mcpCore")}
              value={componentValue(mcpPackageComponent)}
              suffix={<StatusDot status={normalizedStatus(mcpPackageComponent)} />}
            />
          </div>
          <div data-vrcforge-status="mcp-bridge">
            <RuntimeInfoRow
              icon={<PlugZap className="h-4 w-4" />}
              label={t("workspace.mcpBridge")}
              value={componentValue(unityBridgeComponent)}
              suffix={<StatusDot status={normalizedStatus(unityBridgeComponent)} />}
            />
          </div>
          <div data-vrcforge-status="unity">
            <RuntimeInfoRow
              icon={<Monitor className="h-4 w-4" />}
              label={t("workspace.unityEditor")}
              value={componentValue(unityInstanceComponent)}
              suffix={<StatusDot status={normalizedStatus(unityInstanceComponent)} />}
            />
          </div>
          <div data-vrcforge-status="tools">
            <RuntimeInfoRow
              icon={<Wrench className="h-4 w-4" />}
              label={t("workspace.vrcForgeTools")}
              value={componentValue(unityToolsComponent)}
              suffix={<StatusDot status={normalizedStatus(unityToolsComponent)} />}
            />
          </div>
          <div data-vrcforge-status="approval">
            <RuntimeInfoRow
              icon={<ListChecks className="h-4 w-4" />}
              label={t("workspace.pendingConfirmation")}
              value={approvalValue}
              suffix={<StatusDot status={approvalStatus} />}
            />
          </div>
        </div>

        {hasEnvironmentAttention || hasStartupIssue ? (
          <button
            type="button"
            className="mt-3 flex w-full items-center gap-2 rounded-lg border border-amber-300/70 bg-amber-50/70 px-3 py-2 text-left text-xs text-amber-800 transition-colors hover:bg-amber-100 dark:border-amber-900/50 dark:bg-amber-950/20 dark:text-amber-300 dark:hover:bg-amber-950/35"
            onClick={() => void openDoctor()}
          >
            <Boxes className="h-3.5 w-3.5" />
            {t("sidebar.doctor")}
          </button>
        ) : null}
      </div>
    </aside>
  );
}
