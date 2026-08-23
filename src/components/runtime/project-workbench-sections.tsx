import type { ReactNode } from "react";
import { useEffect, useMemo, useState } from "react";
import { useTranslation } from "react-i18next";

import { formatAttachmentSize } from "../../lib/chat-format";
import { formatCount } from "../../lib/utils";
import type { AgentDesktopAction, AgentProgress, WorkspaceDiffSummary } from "../../lib/api";
import type { ChatAttachment, ProjectType } from "../../lib/chat-types";
import { AgentTodoPanelEmbedded } from "./agent-todo-panel";
import { RuntimeDiffFileRow, RuntimeInfoRow, RuntimeSection, StatusDot } from "./runtime-sidebar-ui";
import {
  Box,
  Bot,
  Database,
  FileText,
  Folder,
  ListChecks,
  Monitor,
  PlugZap,
  Server,
} from "lucide-react";

type ComponentStatus = { status: string; message?: string; detail?: unknown } | null | undefined;

export type UserAttachmentSource = {
  id: string;
  name: string;
  type: string;
  size: number;
  messageId?: string;
  attachment?: ChatAttachment;
};

type ProjectSectionState = Record<"todo" | "subAgents" | "environment" | "attachments", boolean>;

const PROJECT_WORKBENCH_SECTIONS_KEY_PREFIX = "vrcforge_project_workbench_sections_collapsed_v1";
const PROJECT_WORKBENCH_SECTIONS_KEY_GENERAL = `${PROJECT_WORKBENCH_SECTIONS_KEY_PREFIX}_general`;
const PROJECT_WORKBENCH_SECTIONS_KEY_UNITY = `${PROJECT_WORKBENCH_SECTIONS_KEY_PREFIX}_unity`;
const MAX_WORKSPACE_FILE_LIST = 6;
const MAX_WORKSPACE_ATTACHMENT_LIST = 6;
const INITIAL_PROJECT_SECTIONS_UNITY: ProjectSectionState = {
  todo: false,
  subAgents: false,
  environment: false,
  attachments: false,
};
const INITIAL_PROJECT_SECTIONS_GENERAL: ProjectSectionState = {
  todo: true,
  subAgents: true,
  environment: true,
  attachments: false,
};

const ACTION_STATUSES_RUNNING = new Set(["running", "claimed", "requested", "cancel_requested", "starting"]);
const ACTION_STATUS_COMPLETED = new Set(["completed", "applied", "approved"]);
const OUTPUT_FILE_STATUSES = new Set(["??", "A"]);

function normalizedStatus(component: ComponentStatus): string {
  return component?.status || "unknown";
}

function isRunningDesktopAction(action: AgentDesktopAction): boolean {
  const status = (action.status || "").trim().toLowerCase();
  return ACTION_STATUSES_RUNNING.has(status);
}

function workspaceSectionsKey(isUnityProject: boolean) {
  return isUnityProject ? PROJECT_WORKBENCH_SECTIONS_KEY_UNITY : PROJECT_WORKBENCH_SECTIONS_KEY_GENERAL;
}

function splitWorkspaceFiles(files: WorkspaceDiffSummary["files"]) {
  const outputs: WorkspaceDiffSummary["files"] = [];
  const changed: WorkspaceDiffSummary["files"] = [];
  for (const file of files) {
    const status = (file.status || "").trim().toUpperCase();
    if (OUTPUT_FILE_STATUSES.has(status)) {
      outputs.push(file);
    } else {
      changed.push(file);
    }
  }
  return { outputs, changed };
}

function desktopActionStatusLabel(action: AgentDesktopAction, t: (key: string) => string) {
  const status = (action.status || "").trim().toLowerCase();
  if (status === "running" || status === "starting") {
    return t("workspace.runStatusRunning");
  }
  if (ACTION_STATUS_COMPLETED.has(status)) {
    return t("workspace.runStatusCompleted");
  }
  if (status === "failed" || status === "error") {
    return t("workspace.runStatusFailed");
  }
  if (status === "queued" || status === "cancelling" || status === "cancel_requested" || status === "requested") {
    return t("workspace.runStatusPending");
  }
  return t("workspace.runStatusUnknown");
}

function desktopActionDot(action: AgentDesktopAction): string {
  const status = (action.status || "").trim().toLowerCase();
  if (ACTION_STATUSES_RUNNING.has(status)) {
    return "warning";
  }
  if (ACTION_STATUS_COMPLETED.has(status)) {
    return "ok";
  }
  if (status === "failed" || status === "error") {
    return "error";
  }
  return "unknown";
}

export function ProjectWorkbenchSections({
  workspaceProjectLabel,
  projectType,
  selectedProjectComponent,
  backendComponent,
  mcpPackageComponent,
  unityBridgeComponent,
  unityInstanceComponent,
  unityToolsComponent,
  externalAgentComponent,
  runtimeConnected,
  localizeHealthMessage,
  agentProgress,
  subAgentPanel,
  subAgentTaskCount,
  subAgentRunningTaskCount,
  subAgentCompletedTaskCount,
  userAttachmentSources,
  onLocateUserAttachmentSource,
  onOpenUserAttachmentSource,
  approvalsLoaded,
  pendingApprovals,
  workspaceSummary,
  activeDesktopActions,
}: {
  workspaceProjectLabel: string;
  projectType: ProjectType;
  selectedProjectComponent: ComponentStatus;
  backendComponent: ComponentStatus;
  mcpPackageComponent: ComponentStatus;
  unityBridgeComponent: ComponentStatus;
  unityInstanceComponent: ComponentStatus;
  unityToolsComponent: ComponentStatus;
  externalAgentComponent: ComponentStatus;
  runtimeConnected: boolean;
  localizeHealthMessage: (message?: string) => string;
  agentProgress: AgentProgress[];
  subAgentPanel?: ReactNode;
  subAgentTaskCount: number;
  subAgentRunningTaskCount: number;
  subAgentCompletedTaskCount: number;
  userAttachmentSources: UserAttachmentSource[];
  onLocateUserAttachmentSource?: (source: UserAttachmentSource) => void;
  onOpenUserAttachmentSource?: (source: UserAttachmentSource) => void;
  approvalsLoaded: boolean;
  pendingApprovals: number;
  workspaceSummary: WorkspaceDiffSummary | null;
  activeDesktopActions: AgentDesktopAction[];
}) {
  const { t } = useTranslation();
  const isUnityProject = projectType === "unity";
  const initialSections = isUnityProject ? INITIAL_PROJECT_SECTIONS_UNITY : INITIAL_PROJECT_SECTIONS_GENERAL;
  const sectionsStorageKey = workspaceSectionsKey(isUnityProject);
  const [collapsedSections, setCollapsedSections] = useState<ProjectSectionState>(() => {
    try {
      const stored = window.localStorage.getItem(sectionsStorageKey);
      if (!stored) {
        return initialSections;
      }
      const restored = JSON.parse(stored) as Partial<ProjectSectionState>;
      return { ...initialSections, ...restored };
    } catch {
      return initialSections;
    }
  });
  const [showAllChangedFiles, setShowAllChangedFiles] = useState(false);
  const [showAllOutputFiles, setShowAllOutputFiles] = useState(false);
  const [showAllAttachmentSources, setShowAllAttachmentSources] = useState(false);
  const attachmentCount = userAttachmentSources.length;
  const hasMoreAttachmentSources = attachmentCount > MAX_WORKSPACE_ATTACHMENT_LIST;
  const visibleAttachmentSources = hasMoreAttachmentSources && !showAllAttachmentSources
    ? userAttachmentSources.slice(0, MAX_WORKSPACE_ATTACHMENT_LIST)
    : userAttachmentSources;

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
  const repoSummary = workspaceSummary;
  const workspaceFileCount = repoSummary?.fileCount ?? 0;
  const localRoot = repoSummary?.requestedRoot || repoSummary?.gitRoot || workspaceProjectLabel;
  const branchName = repoSummary?.branch || "-";
  const repoStatus = repoSummary?.status || "clean";
  const statusForDot =
    repoStatus === "changed" ? "warning" : repoStatus === "not_git" || repoStatus === "missing" || repoStatus === "error" ? "error" : "ok";
  const runningDesktopActions = (activeDesktopActions || []).filter(isRunningDesktopAction);
  const showWorkspaceSummary = Boolean(repoSummary);
  const workspaceSectionTitle = isUnityProject ? t("workspace.environment") : (workspaceProjectLabel || t("workspace.workspace"));

  const workspaceFiles = repoSummary?.files || [];
  const { changed: workspaceChangedFiles, outputs: workspaceOutputFiles } = useMemo(() => splitWorkspaceFiles(workspaceFiles), [workspaceFiles]);
  const hasMoreWorkspaceChangedFiles = workspaceChangedFiles.length > MAX_WORKSPACE_FILE_LIST;
  const hasMoreWorkspaceOutputFiles = workspaceOutputFiles.length > MAX_WORKSPACE_FILE_LIST;
  const visibleWorkspaceChangedFiles = hasMoreWorkspaceChangedFiles && !showAllChangedFiles
    ? workspaceChangedFiles.slice(0, MAX_WORKSPACE_FILE_LIST)
    : workspaceChangedFiles;
  const visibleWorkspaceOutputFiles = hasMoreWorkspaceOutputFiles && !showAllOutputFiles
    ? workspaceOutputFiles.slice(0, MAX_WORKSPACE_FILE_LIST)
    : workspaceOutputFiles;

  const subAgentPanelHasDetails = Boolean(subAgentPanel);

  useEffect(() => {
    const key = workspaceSectionsKey(isUnityProject);
    const initial = isUnityProject ? INITIAL_PROJECT_SECTIONS_UNITY : INITIAL_PROJECT_SECTIONS_GENERAL;
    try {
      const stored = window.localStorage.getItem(key);
      if (!stored) {
        setCollapsedSections(initial);
      } else {
        const restored = JSON.parse(stored) as Partial<ProjectSectionState>;
        setCollapsedSections({ ...initial, ...restored });
      }
    } catch {
      setCollapsedSections(initial);
    }
    setShowAllChangedFiles(false);
    setShowAllOutputFiles(false);
    setShowAllAttachmentSources(false);
  }, [isUnityProject]);

  useEffect(() => {
    window.localStorage.setItem(sectionsStorageKey, JSON.stringify(collapsedSections));
  }, [collapsedSections, sectionsStorageKey]);

  const toggleSection = (section: keyof ProjectSectionState) => {
    setCollapsedSections((current) => ({ ...current, [section]: !current[section] }));
  };

  return (
    <>
      <RuntimeSection
        title={isUnityProject ? t("workspace.todo") : t("workspace.progress")}
        collapsed={Boolean(collapsedSections.todo)}
        onToggle={() => toggleSection("todo")}
        count={<span className="text-xs tabular-nums text-muted-foreground">{formatCount(agentProgress.length)}</span>}
      >
        <div data-vrcforge-project-workbench-activity>
          {agentProgress.length ? (
            <AgentTodoPanelEmbedded progress={agentProgress} />
          ) : (
            <div className="rounded-md border border-dashed border-border px-2 py-2 text-xs text-muted-foreground">
              {t("workspace.noProgress")}
            </div>
          )}
        </div>
      </RuntimeSection>

      <RuntimeSection
        title={t("workspace.subAgents")}
        collapsed={Boolean(collapsedSections.subAgents)}
        onToggle={() => toggleSection("subAgents")}
        count={<span className="text-xs tabular-nums text-muted-foreground">{formatCount(subAgentTaskCount)}</span>}
      >
        <div className="px-1" data-vrcforge-project-sub-agents>
          {subAgentPanel}
        </div>
        {isUnityProject ? null : (
          <div className="mt-2 space-y-2 px-1 text-xs">
            <div className="rounded-md border border-border bg-background/65 px-2 py-1.5">
              <div className="truncate text-xs text-muted-foreground">
                {t("workspace.desktopActions")}
              </div>
              {runningDesktopActions.length ? (
                <div className="mt-1 space-y-1">
                  {runningDesktopActions.slice(0, 4).map((action) => (
                    <div
                      key={action.actionId || action.id || `${action.action || action.provider || "desktop-action"}-${action.createdAt || ""}`}
                      className="rounded border border-border/70 px-2 py-1 text-[11px] text-muted-foreground"
                    >
                      <RuntimeInfoRow
                        icon={<Monitor className="h-3.5 w-3.5" />}
                        label={action.provider || t("workspace.desktopActionProviderUnknown")}
                        value={action.action || t("workspace.backgroundAction")}
                        suffix={<StatusDot status={desktopActionDot(action)} />}
                      />
                      <div className="mt-0.5 text-right text-[10px] text-muted-foreground">{desktopActionStatusLabel(action, t)}</div>
                    </div>
                  ))}
                </div>
              ) : (
                <div className="mt-0.5 text-[11px] text-muted-foreground">{t("workspace.noBackgroundActions")}</div>
              )}
            </div>
            {subAgentPanelHasDetails ? null : (
              <div className="rounded-md border border-border bg-background/65 px-2 py-1.5 text-[11px] text-muted-foreground">
                {`${formatCount(Math.max(0, subAgentRunningTaskCount))} ${t("workspace.runStatusRunning")} / ${formatCount(Math.max(0, subAgentCompletedTaskCount))} ${t("workspace.runStatusCompleted")}`}
              </div>
            )}
          </div>
        )}
      </RuntimeSection>

      <RuntimeSection
        title={workspaceSectionTitle}
        collapsed={Boolean(collapsedSections.environment)}
        onToggle={() => toggleSection("environment")}
      >
        <div className="rounded-xl border border-border/80 bg-background/65 px-3 py-2 shadow-sm">
          <div data-vrcforge-project-environment>
            {isUnityProject ? (
              <>
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
                <div data-vrcforge-status="external-agent">
                  <RuntimeInfoRow
                    icon={<Bot className="h-4 w-4" />}
                    label={t("workspace.externalAgent")}
                    value={componentValue(externalAgentComponent)}
                    suffix={<StatusDot status={normalizedStatus(externalAgentComponent)} />}
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
                    icon={<ListChecks className="h-4 w-4" />}
                    label={t("workspace.vrcForgeTools")}
                    value={componentValue(unityToolsComponent)}
                    suffix={<StatusDot status={normalizedStatus(unityToolsComponent)} />}
                  />
                </div>
              </>
            ) : (
              <>
                {showWorkspaceSummary ? (
                  <>
                    <div data-vrcforge-status="changes">
                      <RuntimeInfoRow
                        icon={<Database className="h-4 w-4" />}
                        label={t("workspace.changes")}
                        value={formatCount(workspaceFileCount)}
                        suffix={<StatusDot status={statusForDot} />}
                      />
                    </div>
                    <div data-vrcforge-status="local">
                      <RuntimeInfoRow
                        icon={<Folder className="h-4 w-4" />}
                        label={t("workspace.local")}
                        value={localRoot}
                      />
                    </div>
                    <div data-vrcforge-status="branch">
                      <RuntimeInfoRow
                        icon={<FileText className="h-4 w-4" />}
                        label={t("workspace.branch")}
                        value={branchName}
                      />
                    </div>
                    <div className="mt-1 space-y-1">
                      {workspaceChangedFiles.length ? (
                        <div className="rounded-md border border-border bg-background/65 px-2 py-1.5">
                          <div className="text-xs font-medium text-muted-foreground">{t("workspace.changes")}</div>
                          <div className="mt-1 space-y-1">
                            {visibleWorkspaceChangedFiles.map((file) => (
                              <RuntimeDiffFileRow key={`changed-${file.status}-${file.path}`} file={file} />
                            ))}
                          </div>
                          {hasMoreWorkspaceChangedFiles ? (
                            <button
                              type="button"
                              className="mt-1.5 w-full rounded-md border border-border/80 bg-background/85 px-2 py-1 text-left text-primary transition-colors hover:bg-background"
                              onClick={() => setShowAllChangedFiles((value) => !value)}
                            >
                              {showAllChangedFiles
                                ? t("workspace.showLess", { count: workspaceChangedFiles.length })
                                : t("workspace.viewAll", { count: workspaceChangedFiles.length - MAX_WORKSPACE_FILE_LIST })}
                            </button>
                          ) : null}
                        </div>
                      ) : (
                        <div className="rounded-md border border-dashed border-border px-2 py-2 text-xs text-muted-foreground">
                          {t("workspace.noLocalChanges")}
                        </div>
                      )}
                      {workspaceOutputFiles.length ? (
                        <div className="rounded-md border border-border bg-background/65 px-2 py-1.5">
                          <div className="text-xs font-medium text-muted-foreground">{t("workspace.outputs")}</div>
                          <div className="mt-1 space-y-1">
                            {visibleWorkspaceOutputFiles.map((file) => (
                              <RuntimeDiffFileRow key={`outputs-${file.status}-${file.path}`} file={file} />
                            ))}
                          </div>
                          {hasMoreWorkspaceOutputFiles ? (
                            <button
                              type="button"
                              className="mt-1.5 w-full rounded-md border border-border/80 bg-background/85 px-2 py-1 text-left text-primary transition-colors hover:bg-background"
                              onClick={() => setShowAllOutputFiles((value) => !value)}
                            >
                              {showAllOutputFiles
                                ? t("workspace.showLess", { count: workspaceOutputFiles.length })
                                : t("workspace.viewAll", { count: workspaceOutputFiles.length - MAX_WORKSPACE_FILE_LIST })}
                            </button>
                          ) : null}
                        </div>
                      ) : null}
                    </div>
                  </>
                ) : (
                  <div className="rounded-md border border-dashed border-border px-2 py-2 text-xs text-muted-foreground">
                    {t("workspace.noWorkspaceSummary")}
                  </div>
                )}
              </>
            )}
            {isUnityProject ? (
              <div data-vrcforge-status="approval">
                <RuntimeInfoRow
                  icon={<ListChecks className="h-4 w-4" />}
                  label={t("workspace.pendingConfirmation")}
                  value={approvalValue}
                  suffix={<StatusDot status={approvalStatus} />}
                />
              </div>
            ) : null}
          </div>
        </div>
      </RuntimeSection>

      {attachmentCount > 0 ? (
        <RuntimeSection
          title={t("workspace.sources")}
          collapsed={Boolean(collapsedSections.attachments)}
          onToggle={() => toggleSection("attachments")}
          count={<span className="text-xs tabular-nums text-muted-foreground">{formatCount(attachmentCount)}</span>}
        >
          <div className="space-y-2 px-1 text-xs">
            {userAttachmentSources.length ? (
              <>
                {visibleAttachmentSources.map((source) => (
                  <div key={source.id} className="rounded-md border border-border bg-background/65 px-2 py-1.5" data-vrcforge-user-attachment-source={source.id}>
                    <div className="break-words" title={source.name || source.type || source.id}>
                      {source.name || source.type || source.id}
                    </div>
                    <div className="mt-0.5 text-muted-foreground">
                    {formatAttachmentSize(source.size)} / {source.type || "-"}
                    </div>
                    {((onLocateUserAttachmentSource && source.messageId) || (onOpenUserAttachmentSource && source.attachment?.dataUrl && source.attachment.type.startsWith("image/"))) ? (
                      <div className="mt-1 flex gap-1">
                        {onLocateUserAttachmentSource && source.messageId ? <button type="button" className="rounded px-1.5 py-0.5 text-[11px] text-primary hover:bg-muted" onClick={() => onLocateUserAttachmentSource(source)}>{t("workspace.locateAttachment")}</button> : null}
                        {onOpenUserAttachmentSource && source.attachment?.dataUrl && source.attachment.type.startsWith("image/") ? <button type="button" className="rounded px-1.5 py-0.5 text-[11px] text-primary hover:bg-muted" onClick={() => onOpenUserAttachmentSource(source)}>{t("workspace.openAttachment")}</button> : null}
                      </div>
                    ) : null}
                  </div>
                ))}
                {hasMoreAttachmentSources ? (
                  <button
                    type="button"
                    className="w-full rounded-md border border-border/80 bg-background/85 px-2 py-1.5 text-left text-primary transition-colors hover:bg-background"
                    onClick={() => setShowAllAttachmentSources((value) => !value)}
                  >
                    {showAllAttachmentSources
                      ? t("workspace.showLess", { count: attachmentCount })
                      : t("workspace.viewAll", { count: attachmentCount - MAX_WORKSPACE_ATTACHMENT_LIST })}
                  </button>
                ) : null}
              </>
            ) : (
              <div className="rounded-md border border-dashed border-border px-2 py-2 text-xs text-muted-foreground">
                {t("workspace.noUserAttachmentSources")}
              </div>
            )}
          </div>
        </RuntimeSection>
      ) : null}
    </>
  );
}
