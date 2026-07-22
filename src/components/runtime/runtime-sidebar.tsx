import { Ban, Bot, Check, CornerDownRight, FileText, Folder, ListChecks, Monitor, MousePointer2, PanelRightClose, RefreshCw, Sparkles, Wrench, X } from "lucide-react";
import { useTranslation } from "react-i18next";
import type { ReactNode } from "react";
import type { AgentDesktopAction, AgentGoal, AgentMemory, AgentProgress, AgentRuntimeRun, DesktopBridgeStatus, SubAgentTask, WorkspaceDiffSummary } from "../../lib/api";
import type { RuntimeFileReference, RuntimeReviewEvidence, RuntimeScheduleItem } from "../../lib/runtime-ui-types";
import type { PathToSkillOperationSummary } from "../../lib/path-to-skill-context";
import { isAwaitingMergeReview, subAgentProposedNextAction } from "../../lib/subagent-merge";
import { cn, formatCount } from "../../lib/utils";
import { Badge } from "../ui/badge";
import { DataLine } from "../ui/data-line";
import { OutputBlock } from "../ui/output-block";
import { RuntimeDiffFileRow, RuntimeFileReferenceRow, RuntimeInfoRow, RuntimeReviewEvidenceRow, RuntimeRunRow, RuntimeScheduleRow, RuntimeSection, StatusDot } from "./runtime-sidebar-ui";

type ComponentStatus = { status: string; message?: string } | null | undefined;
type BadgeTone = "ok" | "warn" | "danger" | "muted";

export function RightRuntimeSidebar({
  runtimeConnected,
  loadingUnityStatus,
  hasEnvironmentAttention,
  hasStartupIssue,
  workspaceProjectLabel,
  backendComponent,
  unityBridgeLabel,
  unityBridgeComponent,
  unityToolsLabel,
  unityToolsComponent,
  providerCompactLabel,
  providerComponent,
  reviewSummaryLabel,
  changeSummaryLabel,
  showStatusSummary,
  showWorkspaceArtifacts,
  workspaceDiffChanged,
  workspaceDiff,
  runtimeNotice,
  pendingApprovalItems,
  runtimeRuns,
  runtimeRunsError,
  rightRuntimeSectionsCollapsed,
  agentGoals,
  agentProgress,
  agentMemory,
  memoryReviewUnreadCount,
  memoryReviewNeedsAttention,
  desktopActions,
  desktopBridge,
  workspaceStateError,
  runtimeReviewEvidence,
  runtimeFileReferences,
  workspaceDiffFiles,
  workspaceDiffError,
  loadingWorkspaceDiff,
  workspaceDiffReviewOpen,
  loadingWorkspaceDiffPatch,
  runtimeSchedule,
  visibleSubAgentTasks,
  selectedSubAgent,
  selectedSubAgentPanelOpen,
  refreshUnityStatus,
  onHideSidebar,
  openDoctor,
  localizeHealthMessage,
  toggleRightRuntimeSection,
  refreshWorkspaceDiff,
  toggleWorkspaceDiffReview,
  onSaveOperationAsSkill,
  inspectSubAgentTask,
  onCloseSelectedSubAgentPanel,
  onOpenSelectedSubAgentPanel,
  onMergeSubAgent,
  onAdoptSubAgentNextAction,
  subAgentRoleLabel,
  subAgentStatusTone,
  displaySubAgentStatus,
  formatPayload,
}: {
  runtimeConnected: boolean;
  loadingUnityStatus: boolean;
  hasEnvironmentAttention: boolean;
  hasStartupIssue: boolean;
  workspaceProjectLabel: string;
  backendComponent: ComponentStatus;
  unityBridgeLabel: string;
  unityBridgeComponent: ComponentStatus;
  unityToolsLabel: string;
  unityToolsComponent: ComponentStatus;
  providerCompactLabel: string;
  providerComponent: ComponentStatus;
  reviewSummaryLabel: string;
  changeSummaryLabel: string;
  showStatusSummary: boolean;
  showWorkspaceArtifacts: boolean;
  workspaceDiffChanged: boolean;
  workspaceDiff: WorkspaceDiffSummary | null;
  runtimeNotice: string;
  pendingApprovalItems: unknown[];
  runtimeRuns: AgentRuntimeRun[];
  runtimeRunsError: string;
  rightRuntimeSectionsCollapsed: Record<string, boolean>;
  agentGoals: AgentGoal[];
  agentProgress: AgentProgress[];
  agentMemory: AgentMemory[];
  memoryReviewUnreadCount: number;
  memoryReviewNeedsAttention: boolean;
  desktopActions: AgentDesktopAction[];
  desktopBridge?: DesktopBridgeStatus | null;
  workspaceStateError: string;
  runtimeReviewEvidence: RuntimeReviewEvidence[];
  runtimeFileReferences: RuntimeFileReference[];
  workspaceDiffFiles: WorkspaceDiffSummary["files"];
  workspaceDiffError: string;
  loadingWorkspaceDiff: boolean;
  workspaceDiffReviewOpen: boolean;
  loadingWorkspaceDiffPatch: boolean;
  runtimeSchedule: RuntimeScheduleItem[];
  visibleSubAgentTasks: SubAgentTask[];
  selectedSubAgent: SubAgentTask | null;
  selectedSubAgentPanelOpen: boolean;
  refreshUnityStatus: () => void | Promise<void>;
  onHideSidebar: () => void;
  openDoctor: () => void | Promise<void>;
  localizeHealthMessage: (message?: string) => string;
  toggleRightRuntimeSection: (section: string) => void;
  refreshWorkspaceDiff: () => void | Promise<void>;
  toggleWorkspaceDiffReview: () => void;
  onSaveOperationAsSkill: (summary: PathToSkillOperationSummary) => void;
  inspectSubAgentTask: (taskId: string) => void | Promise<void>;
  onCloseSelectedSubAgentPanel: () => void;
  onOpenSelectedSubAgentPanel: () => void;
  onMergeSubAgent: (task: SubAgentTask, decision: "adopted" | "dismissed") => void | Promise<void>;
  onAdoptSubAgentNextAction: (task: SubAgentTask) => void;
  subAgentRoleLabel: (role: string) => string;
  subAgentStatusTone: (status: string) => BadgeTone;
  displaySubAgentStatus: (status: string) => string;
  formatPayload: (value: unknown) => string;
}) {
  const { t } = useTranslation();
  const progressItems = agentProgress.map((item) => ({
    id: item.progressId,
    title: item.title || item.progressId,
    meta: item.summary || item.owner || "",
    status: item.status || "pending",
  }));
  const showProgressSection = !showStatusSummary || progressItems.length > 0;
  return (
    <aside className="flex h-screen min-w-0 flex-col overflow-hidden border-l border-border/80 bg-sidebar">
      <div className="flex h-12 shrink-0 items-center gap-2 border-b border-border/80 px-3">
        <div className="min-w-0 flex-1 truncate text-sm font-semibold">{t("workspace.title")}</div>
        <button
          type="button"
          className="flex h-8 w-8 shrink-0 items-center justify-center rounded-md text-muted-foreground transition-colors hover:bg-muted hover:text-foreground"
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
        {showProgressSection ? (
          <RuntimeSection
            title={t("workspace.progress")}
            collapsed={rightRuntimeSectionsCollapsed.progress}
            onToggle={() => toggleRightRuntimeSection("progress")}
            count={progressItems.length ? <Badge tone="muted">{formatCount(progressItems.length)}</Badge> : null}
          >
            {progressItems.length ? (
              <div className="space-y-1">
                {progressItems.slice(0, 8).map((item, index) => {
                  const status = item.status || "pending";
                  const completed = isProgressDone(status);
                  return (
                  <div key={item.id} className="rounded-md px-1 py-1.5 text-xs transition-colors hover:bg-muted/60">
                    <div className="grid min-w-0 grid-cols-[24px_minmax(0,1fr)_auto] items-center gap-2">
                      <span className={cn("flex h-6 w-6 items-center justify-center rounded-full border text-xs font-semibold", progressStatusClass(status))}>
                        {formatCount(index + 1)}
                      </span>
                      <span className="min-w-0">
                        <span className={cn("block truncate font-medium text-foreground", completed && "text-muted-foreground line-through")} title={item.title}>
                          {item.title}
                        </span>
                        {item.meta ? <span className={cn("block truncate text-muted-foreground", completed && "line-through")}>{item.meta}</span> : null}
                      </span>
                      <span className="shrink-0 text-muted-foreground">{progressStatusLabel(status, t)}</span>
                    </div>
                  </div>
                  );
                })}
              </div>
            ) : (
              <div className="rounded-md border border-dashed border-border px-2 py-2 text-xs text-muted-foreground">{t("workspace.noProgress")}</div>
            )}
          </RuntimeSection>
        ) : null}

        {showStatusSummary ? (
        <RuntimeSection
          title={t("workspace.projectStatus")}
          collapsed={rightRuntimeSectionsCollapsed.status}
          onToggle={() => toggleRightRuntimeSection("status")}
          action={
            hasEnvironmentAttention || hasStartupIssue ? (
              <button
                type="button"
                className="shrink-0 rounded-md border border-amber-300/70 px-2 py-1 text-xs text-amber-700 transition-colors hover:bg-amber-50 dark:border-amber-900/50 dark:text-amber-300 dark:hover:bg-amber-950/30"
                onClick={(event) => {
                  event.stopPropagation();
                  void openDoctor();
                }}
              >
                {t("sidebar.doctor")}
              </button>
            ) : null
          }
        >
          <div className="space-y-1">
            <RuntimeInfoRow
              icon={<Folder className="h-4 w-4" />}
              label={t("workspace.project")}
              value={workspaceProjectLabel}
            />
            <RuntimeInfoRow
              icon={<Bot className="h-4 w-4" />}
              label={t("workspace.core")}
              value={runtimeConnected ? localizeHealthMessage(backendComponent?.message) || t("workspace.online") : t("workspace.offline")}
              suffix={backendComponent ? <StatusDot status={backendComponent.status} /> : null}
            />
            <RuntimeInfoRow
              icon={<Monitor className="h-4 w-4" />}
              label={t("workspace.unity")}
              value={unityBridgeLabel}
              suffix={unityBridgeComponent ? <StatusDot status={unityBridgeComponent.status} /> : null}
            />
            <RuntimeInfoRow
              icon={<Wrench className="h-4 w-4" />}
              label={t("workspace.avatarTools")}
              value={unityToolsLabel}
              suffix={unityToolsComponent ? <StatusDot status={unityToolsComponent.status} /> : null}
            />
            <RuntimeInfoRow
              icon={<Sparkles className="h-4 w-4" />}
              label={t("workspace.agent")}
              value={providerCompactLabel}
              suffix={providerComponent ? <StatusDot status={providerComponent.status} /> : null}
            />
            {showWorkspaceArtifacts ? (
              <>
                <RuntimeInfoRow
                  icon={<ListChecks className="h-4 w-4" />}
                  label={t("workspace.review")}
                  value={reviewSummaryLabel}
                />
                <RuntimeInfoRow
                  icon={<FileText className="h-4 w-4" />}
                  label={t("workspace.changes")}
                  value={changeSummaryLabel}
                  suffix={
                    workspaceDiffChanged ? (
                      <span className="font-mono">
                        <span className="text-emerald-600">+{formatCount(workspaceDiff?.additions || 0)}</span>{" "}
                        <span className="text-destructive">-{formatCount(workspaceDiff?.deletions || 0)}</span>
                      </span>
                    ) : null
                  }
                />
              </>
            ) : null}
          </div>
          {runtimeNotice ? (
            <div className="mt-3 rounded-md border border-border bg-muted/50 px-2 py-2 text-xs text-muted-foreground">
              {runtimeNotice}
            </div>
          ) : null}
        {pendingApprovalItems.length ? (
            <div className="mt-3 rounded-md border border-amber-500/20 bg-amber-500/5 px-2 py-2 text-xs text-amber-700">
              {t("workspace.inlineApprovalHint")}
            </div>
          ) : null}
        </RuntimeSection>
        ) : null}

        {runtimeRuns.length || runtimeRunsError ? (
          <RuntimeSection
            title={t("workspace.runLedger")}
            collapsed={rightRuntimeSectionsCollapsed.runs}
            onToggle={() => toggleRightRuntimeSection("runs")}
            count={<Badge tone={runtimeRunsError ? "warn" : "muted"}>{runtimeRunsError ? "!" : formatCount(runtimeRuns.length)}</Badge>}
          >
            {runtimeRunsError ? (
              <div className="text-xs text-muted-foreground">{runtimeRunsError}</div>
            ) : (
              <div className="space-y-0.5">
                {runtimeRuns.slice(0, 3).map((run, index) => (
                  <RuntimeRunRow
                    key={run.id || run.turnId || run.clientTurnId || index}
                    run={run}
                    onSaveAsSkill={onSaveOperationAsSkill}
                  />
                ))}
                {runtimeRuns.length > 3 ? (
                  <div className="px-1 pt-1 text-xs text-muted-foreground">{t("workspace.more", { count: formatCount(runtimeRuns.length - 3) })}</div>
                ) : null}
              </div>
            )}
          </RuntimeSection>
        ) : null}

        {agentGoals.length ? (
          <RuntimeSection
            title={t("workspace.goals")}
            collapsed={rightRuntimeSectionsCollapsed.goals}
            onToggle={() => toggleRightRuntimeSection("goals")}
            count={<Badge tone="muted">{formatCount(agentGoals.length)}</Badge>}
          >
            <div className="space-y-0.5">
              {agentGoals.slice(0, 5).map((goal) => (
                <div key={goal.goalId} className="rounded-md px-1 py-1.5 text-xs">
                  <div className="flex min-w-0 items-center gap-2">
                    <span className={cn("h-2 w-2 shrink-0 rounded-full", goal.status === "active" ? "bg-primary" : goal.status === "paused" ? "bg-amber-500" : "bg-muted-foreground/40")} />
                    <span className="min-w-0 flex-1 truncate font-medium">{goal.title || goal.goalId}</span>
                    <span className="shrink-0 text-muted-foreground">{goal.status}</span>
                  </div>
                  {goal.summary ? <div className="mt-0.5 line-clamp-2 pl-4 text-muted-foreground">{goal.summary}</div> : null}
                </div>
              ))}
            </div>
          </RuntimeSection>
        ) : null}

        {agentMemory.length || memoryReviewUnreadCount || memoryReviewNeedsAttention ? (
          <RuntimeSection
            title={t("workspace.memory")}
            collapsed={rightRuntimeSectionsCollapsed.memory}
            onToggle={() => toggleRightRuntimeSection("memory")}
            count={(
              <div className="flex items-center gap-1">
                <Badge tone="muted">{formatCount(agentMemory.length)}</Badge>
                {memoryReviewUnreadCount ? (
                  <Badge tone="warn" title={t("settings.memoryReviewUnreadCount", { count: memoryReviewUnreadCount })}>
                    {formatCount(memoryReviewUnreadCount)}
                  </Badge>
                ) : null}
                {memoryReviewNeedsAttention ? (
                  <Badge tone="danger" title={t("settings.memoryReviewRunFailed")}>!</Badge>
                ) : null}
              </div>
            )}
          >
            <div className="space-y-0.5">
              {agentMemory.slice(0, 6).map((memory) => (
                <div key={memory.memoryId} className="rounded-md px-1 py-1.5 text-xs">
                  <div className="flex min-w-0 items-center gap-2">
                    <span className="shrink-0 text-muted-foreground">{memory.scope || "project"}</span>
                    <span className="min-w-0 flex-1 truncate font-medium">{memory.kind || "memory"}</span>
                  </div>
                  <div className="mt-0.5 line-clamp-2 text-muted-foreground">{memory.text}</div>
                </div>
              ))}
            </div>
          </RuntimeSection>
        ) : null}

        {desktopActions.length || desktopBridge?.connected ? (
          <RuntimeSection
            title={t("workspace.desktopActions")}
            collapsed={rightRuntimeSectionsCollapsed.desktopActions}
            onToggle={() => toggleRightRuntimeSection("desktopActions")}
            count={<Badge tone="muted">{formatCount(desktopActions.length)}</Badge>}
          >
            <div className="space-y-0.5">
              {desktopBridge?.connected ? (
                <div className="flex min-w-0 items-center gap-2 rounded-md px-1 py-1.5 text-xs text-muted-foreground">
                  <Monitor className="h-3.5 w-3.5 shrink-0" />
                  <span className="min-w-0 flex-1 truncate">
                    {t("workspace.desktopBridgeConnected", {
                      names: (desktopBridge.bridges ?? [])
                        .map((bridge) => bridge.name || bridge.provider || bridge.bridgeId || "")
                        .filter(Boolean)
                        .join(", "),
                    })}
                  </span>
                </div>
              ) : null}
              {desktopActions.slice(0, 5).map((action) => {
                const operation = typeof action.paramsSummary?.operation === "string" ? action.paramsSummary.operation : "";
                return (
                  <div key={action.id || `${action.action}-${action.createdAt}`} className="rounded-md px-1 py-1.5 text-xs">
                    <div className="flex min-w-0 items-center gap-2">
                      <MousePointer2 className="h-3.5 w-3.5 shrink-0 text-muted-foreground" />
                      <span className="min-w-0 flex-1 truncate font-medium">{action.action}{operation ? ` · ${operation}` : ""}</span>
                      <span className="shrink-0 text-muted-foreground">{action.status}</span>
                    </div>
                    {action.error || action.promptSummary ? <div className="mt-0.5 line-clamp-2 pl-5 text-muted-foreground">{action.error || action.promptSummary}</div> : null}
                  </div>
                );
              })}
            </div>
          </RuntimeSection>
        ) : null}

        {workspaceStateError ? <div className="px-1 py-2 text-xs text-muted-foreground">{workspaceStateError}</div> : null}

        {showWorkspaceArtifacts && runtimeReviewEvidence.length ? (
          <RuntimeSection
            title={t("workspace.reviewEvidence")}
            collapsed={rightRuntimeSectionsCollapsed.reviewEvidence}
            onToggle={() => toggleRightRuntimeSection("reviewEvidence")}
            count={<Badge tone="muted">{formatCount(runtimeReviewEvidence.length)}</Badge>}
          >
            <div className="space-y-0.5">
              {runtimeReviewEvidence.slice(0, 3).map((item) => (
                <RuntimeReviewEvidenceRow key={item.id} item={item} />
              ))}
              {runtimeReviewEvidence.length > 3 ? (
                <div className="px-1 pt-1 text-xs text-muted-foreground">{t("workspace.more", { count: formatCount(runtimeReviewEvidence.length - 3) })}</div>
              ) : null}
            </div>
          </RuntimeSection>
        ) : null}

        {showWorkspaceArtifacts && runtimeFileReferences.length ? (
          <RuntimeSection
            title={t("workspace.filesSeen")}
            collapsed={rightRuntimeSectionsCollapsed.files}
            onToggle={() => toggleRightRuntimeSection("files")}
            count={<Badge tone="muted">{formatCount(runtimeFileReferences.length)}</Badge>}
          >
            <div className="space-y-0.5">
              {runtimeFileReferences.map((file) => (
                <RuntimeFileReferenceRow key={`${file.source}-${file.path}`} file={file} />
              ))}
            </div>
          </RuntimeSection>
        ) : null}

        {showWorkspaceArtifacts && (workspaceDiffFiles.length || workspaceDiffError || workspaceDiff) ? (
          <RuntimeSection
            title={t("workspace.changes")}
            collapsed={rightRuntimeSectionsCollapsed.diff}
            onToggle={() => toggleRightRuntimeSection("diff")}
            action={
              <button
                type="button"
                className="flex h-7 w-7 shrink-0 items-center justify-center rounded-md text-muted-foreground transition-colors hover:bg-muted hover:text-foreground disabled:opacity-60"
                onClick={(event) => {
                  event.stopPropagation();
                  void refreshWorkspaceDiff();
                }}
                title={t("workspace.refreshChanges")}
                aria-label={t("workspace.refreshChanges")}
                disabled={!runtimeConnected || loadingWorkspaceDiff}
              >
                <RefreshCw className={cn("h-3.5 w-3.5", loadingWorkspaceDiff && "animate-spin")} />
              </button>
            }
            count={
              <Badge tone={workspaceDiffChanged ? "warn" : "muted"}>
                {workspaceDiffChanged ? formatCount(workspaceDiff?.fileCount || 0) : workspaceDiff?.status || "idle"}
              </Badge>
            }
          >
          {workspaceDiffFiles.length ? (
            <div className="space-y-2">
              <button
                type="button"
                className="flex w-full items-center justify-between gap-2 rounded-md border border-border bg-background px-2 py-1.5 text-left text-xs transition-colors hover:bg-muted"
                onClick={toggleWorkspaceDiffReview}
              >
                <span className="truncate">{t("workspace.changeReview")}</span>
                <span className="shrink-0 text-muted-foreground">
                  {loadingWorkspaceDiffPatch ? t("common.loadingShort") : workspaceDiffReviewOpen ? t("common.hide") : t("common.open")}
                </span>
              </button>
              <div className="space-y-0.5">
                {workspaceDiffFiles.slice(0, 6).map((file) => (
                  <RuntimeDiffFileRow key={`${file.status}-${file.path}`} file={file} />
                ))}
                {workspaceDiffFiles.length > 6 ? (
                  <div className="pt-1 text-xs text-muted-foreground">{t("workspace.more", { count: formatCount(workspaceDiffFiles.length - 6) })}</div>
                ) : null}
              </div>
              {workspaceDiffReviewOpen ? (
                <div className="rounded-md border border-border bg-background/80 p-2">
                  <div className="mb-1 text-xs font-medium text-muted-foreground">{t("workspace.gitPatchPreview")}</div>
                  {workspaceDiff?.patch ? (
                    <pre className="app-scrollbar max-h-72 overflow-auto whitespace-pre-wrap break-words font-mono text-[11px] leading-relaxed">
                      {workspaceDiff.patch}
                    </pre>
                  ) : (
                    <div className="text-xs text-muted-foreground">
                      {loadingWorkspaceDiffPatch ? t("workspace.loadingPatch") : t("workspace.noTrackedPatch")}
                    </div>
                  )}
                  {workspaceDiff?.patchTruncated ? <div className="mt-1 text-xs text-amber-700">{t("workspace.patchTruncated")}</div> : null}
                </div>
              ) : null}
            </div>
          ) : (
            <div className="text-xs text-muted-foreground">
              {workspaceDiffError || (runtimeConnected ? t("workspace.noLocalChanges") : t("workspace.coreOffline"))}
            </div>
          )}
          </RuntimeSection>
        ) : null}

        {runtimeSchedule.length ? (
          <RuntimeSection
            title={t("workspace.queue")}
            collapsed={rightRuntimeSectionsCollapsed.schedule}
            onToggle={() => toggleRightRuntimeSection("schedule")}
            count={<Badge tone="warn">{formatCount(runtimeSchedule.length)}</Badge>}
          >
            <div className="space-y-0.5">
              {runtimeSchedule.slice(0, 8).map((item) => (
                <RuntimeScheduleRow key={item.id} item={item} />
              ))}
            </div>
          </RuntimeSection>
        ) : null}

        {visibleSubAgentTasks.length ? (
          <RuntimeSection
            title={t("workspace.subAgents")}
            collapsed={rightRuntimeSectionsCollapsed.subagents}
            onToggle={() => toggleRightRuntimeSection("subagents")}
            count={<Badge tone="warn">{formatCount(visibleSubAgentTasks.length)}</Badge>}
          >
            <div className="space-y-1">
              {visibleSubAgentTasks.slice(0, rightRuntimeSectionsCollapsed.subagents ? 0 : 6).map((task) => {
                const runningTask = ["queued", "running", "cancelling"].includes(task.status);
                return (
                  <button
                    key={task.id}
                    type="button"
                    className="grid w-full min-w-0 grid-cols-[14px_minmax(0,1fr)_auto] items-center gap-2 rounded-md px-1 py-1.5 text-left text-xs transition-colors hover:bg-muted"
                    onClick={() => void inspectSubAgentTask(task.id)}
                  >
                    <span className={cn("block h-3 w-3 rounded-sm", runningTask ? "bg-primary" : task.status === "failed" ? "bg-destructive" : "bg-muted-foreground/50")} />
                    <span className="min-w-0">
                      <span className="block truncate font-medium">{task.displayName || subAgentRoleLabel(task.role)}</span>
                      <span className="block truncate text-muted-foreground">{task.task || task.status}</span>
                    </span>
                    <span className="shrink-0 text-muted-foreground">{task.status}</span>
                  </button>
                );
              })}
              {visibleSubAgentTasks.length > 6 ? (
                <div className="px-1 pt-1 text-xs text-muted-foreground">{t("workspace.more", { count: formatCount(visibleSubAgentTasks.length - 6) })}</div>
              ) : null}
              {selectedSubAgent && selectedSubAgentPanelOpen ? (
                <div
                  className="mt-2 rounded-md border border-border bg-background/80 p-2 text-xs"
                  data-vrcforge-sub-agent-panel="true"
                >
                  <div className="mb-2 flex min-w-0 items-center gap-2">
                    <Bot className="h-3.5 w-3.5 shrink-0 text-primary" />
                    <span className="min-w-0 flex-1 truncate font-medium">{selectedSubAgent.displayName || subAgentRoleLabel(selectedSubAgent.role)}</span>
                    {isAwaitingMergeReview(selectedSubAgent) ? (
                      <Badge tone="warn" className="shrink-0">
                        {t("subagent.awaitingReview")}
                      </Badge>
                    ) : null}
                    {selectedSubAgent.mergeDecision ? (
                      <Badge tone={selectedSubAgent.mergeDecision === "adopted" ? "ok" : "muted"} className="shrink-0">
                        {selectedSubAgent.mergeDecision === "adopted" ? t("subagent.mergedBadge") : t("subagent.dismissedBadge")}
                      </Badge>
                    ) : null}
                    <Badge tone={subAgentStatusTone(selectedSubAgent.status)} className="shrink-0">
                      {displaySubAgentStatus(selectedSubAgent.status)}
                    </Badge>
                    <button
                      type="button"
                      className="flex h-6 w-6 shrink-0 items-center justify-center rounded-md text-muted-foreground hover:bg-muted hover:text-foreground"
                            onClick={onCloseSelectedSubAgentPanel}
                      title={t("common.hide")}
                      aria-label={t("common.hide")}
                    >
                      <X className="h-3.5 w-3.5" />
                    </button>
                  </div>
                  <div className="grid gap-2">
                    <DataLine label="Role" value={subAgentRoleLabel(selectedSubAgent.role)} />
                    <OutputBlock label="Task" value={selectedSubAgent.task || selectedSubAgent.id} />
                    {selectedSubAgent.mergeDecision ? (
                      <DataLine
                        label={t("subagent.review")}
                        value={`${selectedSubAgent.mergeDecision === "adopted" ? t("subagent.mergedBadge") : t("subagent.dismissedBadge")}${selectedSubAgent.mergedAt ? ` · ${selectedSubAgent.mergedAt}` : ""}`}
                      />
                    ) : null}
                    {selectedSubAgent.summary ? <OutputBlock label="Summary" value={selectedSubAgent.summary} /> : null}
                    {selectedSubAgent.error ? <OutputBlock label={t("doctor.error")} value={selectedSubAgent.error} danger /> : null}
                    {subAgentProposedNextAction(selectedSubAgent) ? (
                      <div className="rounded-md border border-dashed border-border px-2 py-1.5">
                        <div className="text-[11px] font-medium text-muted-foreground">{t("subagent.nextAction")}</div>
                        <div className="mt-0.5 break-words">{subAgentProposedNextAction(selectedSubAgent)}</div>
                        <div className="mt-1 flex justify-end">
                          <button
                            type="button"
                            className="flex items-center gap-1 rounded-md px-1.5 py-1 text-[11px] text-muted-foreground transition-colors hover:bg-muted hover:text-foreground"
                            onClick={() => onAdoptSubAgentNextAction(selectedSubAgent)}
                          >
                            <CornerDownRight className="h-3 w-3" />
                            {t("subagent.adoptNextAction")}
                          </button>
                        </div>
                      </div>
                    ) : null}
                    {selectedSubAgent.result !== undefined ? <OutputBlock label={t("subagent.result")} value={formatPayload(selectedSubAgent.result)} /> : null}
                    {isAwaitingMergeReview(selectedSubAgent) ? (
                      <div className="flex flex-wrap justify-end gap-1.5">
                        <button
                          type="button"
                          className="flex items-center gap-1 rounded-md border border-border px-2 py-1 text-[11px] transition-colors hover:bg-muted"
                          onClick={() => void onMergeSubAgent(selectedSubAgent, "adopted")}
                        >
                          <Check className="h-3 w-3" />
                          {t("subagent.mergeAdopt")}
                        </button>
                        <button
                          type="button"
                          className="flex items-center gap-1 rounded-md border border-border px-2 py-1 text-[11px] text-muted-foreground transition-colors hover:bg-muted"
                          onClick={() => void onMergeSubAgent(selectedSubAgent, "dismissed")}
                        >
                          <Ban className="h-3 w-3" />
                          {t("subagent.mergeDismiss")}
                        </button>
                      </div>
                    ) : null}
                  </div>
                </div>
              ) : selectedSubAgent ? (
                <button
                  type="button"
                  className="mt-2 flex w-full items-center justify-between gap-2 rounded-md border border-border bg-background px-2 py-1.5 text-left text-xs transition-colors hover:bg-muted"
                        onClick={onOpenSelectedSubAgentPanel}
                >
                  <span className="truncate">{selectedSubAgent.displayName || subAgentRoleLabel(selectedSubAgent.role)}</span>
                  <span className="shrink-0 text-muted-foreground">{t("common.open")}</span>
                </button>
              ) : null}
            </div>
          </RuntimeSection>
        ) : null}

      </div>
    </aside>
  );
}

function progressStatusLabel(status: string, t: (key: string) => string) {
  const normalized = status.trim().toLowerCase();
  const labels: Record<string, string> = {
    pending: t("workspace.progressPending"),
    in_progress: t("workspace.progressInProgress"),
    running: t("workspace.progressInProgress"),
    completed: t("workspace.progressCompleted"),
    cancelled: t("workspace.progressCancelled"),
    blocked: t("workspace.progressBlocked"),
    question: t("workspace.progressQuestion"),
  };
  return labels[normalized] || status || t("workspace.runStatusUnknown");
}

function progressStatusClass(status: string) {
  const normalized = status.trim().toLowerCase();
  if (["in_progress", "running", "question"].includes(normalized)) {
    return "border-primary/25 bg-primary/5 text-foreground";
  }
  if (normalized === "completed") {
    return "border-border bg-muted/50 text-muted-foreground";
  }
  if (normalized === "blocked") {
    return "border-amber-400/60 bg-amber-50 text-amber-700 dark:bg-amber-950/30 dark:text-amber-300";
  }
  return "border-border bg-transparent text-muted-foreground";
}

function isProgressDone(status: string) {
  return ["completed", "cancelled", "deleted"].includes(status.trim().toLowerCase());
}
