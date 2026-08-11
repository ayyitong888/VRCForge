import { Box, Boxes, FileText, Folder, GitBranch, ListChecks, Monitor, PanelRightClose, PlugZap, RefreshCw, Server, Wrench } from "lucide-react";
import type { ReactNode } from "react";
import { useEffect, useState } from "react";
import { useTranslation } from "react-i18next";

import type { AgentGoal, AgentMemory, AgentProgress, AgentSkill, WorkspaceDiffSummary } from "../../lib/api";
import { cn, formatCount } from "../../lib/utils";
import { AgentTodoPanel } from "./agent-todo-panel";
import { RuntimeInfoRow, RuntimeSection, StatusDot } from "./runtime-sidebar-ui";

type ComponentStatus = { status: string; message?: string } | null | undefined;
const PROJECT_WORKBENCH_SECTIONS_KEY = "vrcforge_project_workbench_sections_collapsed_v1";

function normalizedStatus(component: ComponentStatus): string {
  return component?.status || "unknown";
}

export function RightRuntimeSidebar({
  runtimeConnected,
  loadingUnityStatus,
  hasEnvironmentAttention,
  hasStartupIssue,
  workspaceProjectLabel,
  projectWorkspaceLabel,
  selectedProjectComponent,
  backendComponent,
  mcpPackageComponent,
  unityBridgeComponent,
  unityInstanceComponent,
  unityToolsComponent,
  agentProgress,
  projectWorkspace,
  activityPanel,
  subAgentPanel,
  runtimeActivityCount,
  subAgentCount,
  workspaceDiff,
  agentGoals,
  agentMemory,
  skills,
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
  projectWorkspaceLabel: string;
  selectedProjectComponent: ComponentStatus;
  backendComponent: ComponentStatus;
  mcpPackageComponent: ComponentStatus;
  unityBridgeComponent: ComponentStatus;
  unityInstanceComponent: ComponentStatus;
  unityToolsComponent: ComponentStatus;
  agentProgress: AgentProgress[];
  projectWorkspace: boolean;
  activityPanel?: ReactNode;
  subAgentPanel?: ReactNode;
  runtimeActivityCount: number;
  subAgentCount: number;
  workspaceDiff: WorkspaceDiffSummary | null;
  agentGoals: AgentGoal[];
  agentMemory: AgentMemory[];
  skills: AgentSkill[];
  approvalsLoaded: boolean;
  pendingApprovals: number;
  refreshUnityStatus: () => void | Promise<void>;
  onHideSidebar: () => void;
  openDoctor: () => void | Promise<void>;
  localizeHealthMessage: (message?: string) => string;
}) {
  const { t } = useTranslation();
  const [collapsedSections, setCollapsedSections] = useState<Record<string, boolean>>(() => {
    try {
      const stored = window.localStorage.getItem(PROJECT_WORKBENCH_SECTIONS_KEY);
      return stored ? JSON.parse(stored) as Record<string, boolean> : { context: true };
    } catch {
      return { context: true };
    }
  });
  useEffect(() => {
    window.localStorage.setItem(PROJECT_WORKBENCH_SECTIONS_KEY, JSON.stringify(collapsedSections));
  }, [collapsedSections]);
  const toggleSection = (section: string) => {
    setCollapsedSections((current) => ({ ...current, [section]: !current[section] }));
  };
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
  const progressCount = agentProgress.length + runtimeActivityCount + subAgentCount + agentGoals.length;
  const contextCount = agentMemory.length + skills.length;

  return (
    <aside
      className="flex h-screen min-w-0 flex-col overflow-hidden border-l border-border/80 bg-sidebar"
      data-vrcforge-environment-status
      data-vrcforge-project-workbench={projectWorkspace || undefined}
    >
      <div className="flex h-12 shrink-0 items-center gap-2 border-b border-border/80 px-3">
        <div className="min-w-0 flex-1 truncate text-sm font-semibold">
          {t(projectWorkspace ? "workspace.title" : "workspace.environment")}
        </div>
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
        {projectWorkspace ? (
          <RuntimeSection
            title={t("workspace.progress")}
            collapsed={Boolean(collapsedSections.progress)}
            onToggle={() => toggleSection("progress")}
            count={<span className="text-xs tabular-nums text-muted-foreground">{formatCount(progressCount)}</span>}
          >
            <div data-vrcforge-project-workbench-activity>
              {progressCount ? (
                <>
                  <AgentTodoPanel progress={agentProgress} />
                  {activityPanel}
                  {subAgentPanel}
                  {agentGoals.slice(0, 4).map((goal) => (
                    <div key={goal.goalId} className="mb-1 flex min-w-0 items-center gap-2 rounded-md px-2 py-1.5 text-xs hover:bg-muted/60">
                      <span className={cn("h-2 w-2 shrink-0 rounded-full", goal.status === "active" ? "bg-primary" : "bg-muted-foreground/40")} />
                      <span className="min-w-0 flex-1 truncate font-medium">{goal.title || goal.goalId}</span>
                      <span className="shrink-0 text-muted-foreground">{goal.status}</span>
                    </div>
                  ))}
                </>
              ) : (
                <div className="rounded-md border border-dashed border-border px-2 py-2 text-xs text-muted-foreground">
                  {t("workspace.noProgress")}
                </div>
              )}
            </div>
          </RuntimeSection>
        ) : (
          <AgentTodoPanel progress={agentProgress} />
        )}

        {projectWorkspace ? (
          <RuntimeSection
            title={projectWorkspaceLabel}
            collapsed={Boolean(collapsedSections.project)}
            onToggle={() => toggleSection("project")}
            count={workspaceDiff?.status === "changed" ? (
              <span className="text-xs tabular-nums text-muted-foreground">{formatCount(workspaceDiff.fileCount)}</span>
            ) : undefined}
          >
            <div className="rounded-xl border border-border/80 bg-background/65 px-3 py-2 shadow-sm">
              {workspaceDiff?.branch ? (
                <RuntimeInfoRow
                  icon={<GitBranch className="h-4 w-4" />}
                  label={workspaceDiff.branch}
                  value={workspaceDiff.gitRoot || ""}
                />
              ) : null}
              {workspaceDiff?.status === "changed" ? (
                <RuntimeInfoRow
                  icon={<FileText className="h-4 w-4" />}
                  label={t("workspace.changes")}
                  value={t("workspace.changedFiles", { count: workspaceDiff.fileCount })}
                  suffix={
                    <span className="font-mono">
                      <span className="text-emerald-600">+{formatCount(workspaceDiff.additions)}</span>{" "}
                      <span className="text-destructive">-{formatCount(workspaceDiff.deletions)}</span>
                    </span>
                  }
                />
              ) : null}
              <div data-vrcforge-project-environment>
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
            </div>
          </RuntimeSection>
        ) : null}

        {projectWorkspace ? null : <div className="rounded-xl border border-border/80 bg-background/65 px-3 py-2 shadow-sm">
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
        </div>}

        {projectWorkspace ? (
          <RuntimeSection
            title={t("workspace.context")}
            collapsed={Boolean(collapsedSections.context)}
            onToggle={() => toggleSection("context")}
            count={<span className="text-xs tabular-nums text-muted-foreground">{formatCount(contextCount)}</span>}
          >
            <div data-vrcforge-project-context className="space-y-3 px-1 text-xs">
              {agentMemory.length ? (
                <div>
                  <div className="mb-1 font-medium text-muted-foreground">{t("workspace.memory")}</div>
                  {agentMemory.slice(0, 4).map((memory) => (
                    <div key={memory.memoryId} className="line-clamp-2 rounded-md px-1 py-1 text-foreground">
                      {memory.text || memory.kind || memory.memoryId}
                    </div>
                  ))}
                </div>
              ) : null}
              {skills.length ? (
                <div>
                  <div className="mb-1 font-medium text-muted-foreground">{t("sidebar.skills")}</div>
                  <div className="flex flex-wrap gap-1">
                    {skills.slice(0, 8).map((skill) => (
                      <span key={skill.name} className="max-w-full truncate rounded-md bg-muted px-2 py-1" title={skill.title || skill.name}>
                        {skill.title || skill.name}
                      </span>
                    ))}
                  </div>
                </div>
              ) : null}
              {!contextCount ? <div className="text-muted-foreground">{t("workspace.notLoaded")}</div> : null}
            </div>
          </RuntimeSection>
        ) : null}

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
