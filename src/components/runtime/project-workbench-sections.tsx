import type { ReactNode } from "react";
import { useEffect, useState } from "react";
import { useTranslation } from "react-i18next";

import { formatAttachmentSize } from "../../lib/chat-format";
import { formatCount } from "../../lib/utils";
import type { AgentProgress } from "../../lib/api";
import type { ChatAttachment, ProjectType } from "../../lib/chat-types";
import { AgentTodoPanelEmbedded } from "./agent-todo-panel";
import { RuntimeInfoRow, RuntimeSection, StatusDot } from "./runtime-sidebar-ui";
import { Folder, ListChecks, Monitor, PlugZap, Server, Wrench, Box } from "lucide-react";

type ComponentStatus = { status: string; message?: string } | null | undefined;

export type UserAttachmentSource = {
  id: string;
  name: string;
  type: string;
  size: number;
  messageId?: string;
  attachment?: ChatAttachment;
};

const PROJECT_WORKBENCH_SECTIONS_KEY = "vrcforge_project_workbench_sections_collapsed_v1";
const INITIAL_PROJECT_SECTIONS: Record<string, boolean> = {
  todo: false,
  subAgents: false,
  environment: false,
  attachments: false,
};

function normalizedStatus(component: ComponentStatus): string {
  return component?.status || "unknown";
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
  runtimeConnected,
  localizeHealthMessage,
  agentProgress,
  subAgentPanel,
  subAgentTaskCount,
  userAttachmentSources,
  onLocateUserAttachmentSource,
  onOpenUserAttachmentSource,
  approvalsLoaded,
  pendingApprovals,
}: {
  workspaceProjectLabel: string;
  projectType: ProjectType;
  selectedProjectComponent: ComponentStatus;
  backendComponent: ComponentStatus;
  mcpPackageComponent: ComponentStatus;
  unityBridgeComponent: ComponentStatus;
  unityInstanceComponent: ComponentStatus;
  unityToolsComponent: ComponentStatus;
  runtimeConnected: boolean;
  localizeHealthMessage: (message?: string) => string;
  agentProgress: AgentProgress[];
  subAgentPanel?: ReactNode;
  subAgentTaskCount: number;
  userAttachmentSources: UserAttachmentSource[];
  onLocateUserAttachmentSource?: (source: UserAttachmentSource) => void;
  onOpenUserAttachmentSource?: (source: UserAttachmentSource) => void;
  approvalsLoaded: boolean;
  pendingApprovals: number;
}) {
  const { t } = useTranslation();
  const [collapsedSections, setCollapsedSections] = useState<Record<string, boolean>>(() => {
    try {
      const stored = window.localStorage.getItem(PROJECT_WORKBENCH_SECTIONS_KEY);
      if (!stored) {
        return INITIAL_PROJECT_SECTIONS;
      }
      const restored = JSON.parse(stored) as Record<string, boolean>;
      return { ...INITIAL_PROJECT_SECTIONS, ...restored };
    } catch {
      return INITIAL_PROJECT_SECTIONS;
    }
  });
  const [showAllAttachmentSources, setShowAllAttachmentSources] = useState(false);
  const attachmentCount = userAttachmentSources.length;
  const maxAttachmentSources = 6;
  const hasMoreAttachmentSources = userAttachmentSources.length > maxAttachmentSources;
  const visibleAttachmentSources = hasMoreAttachmentSources && !showAllAttachmentSources
    ? userAttachmentSources.slice(0, maxAttachmentSources)
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
  const isUnityProject = projectType === "unity";

  useEffect(() => {
    window.localStorage.setItem(PROJECT_WORKBENCH_SECTIONS_KEY, JSON.stringify(collapsedSections));
  }, [collapsedSections]);

  const toggleSection = (section: string) => {
    setCollapsedSections((current) => ({ ...current, [section]: !current[section] }));
  };

  return (
    <>
      <RuntimeSection
        title={t("workspace.todo")}
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
      >
        <div className="px-1" data-vrcforge-project-sub-agents>
          {subAgentPanel}
        </div>
      </RuntimeSection>

      <RuntimeSection
        title={t("workspace.environment")}
        collapsed={Boolean(collapsedSections.environment)}
        onToggle={() => toggleSection("environment")}
      >
        <div className="rounded-xl border border-border/80 bg-background/65 px-3 py-2 shadow-sm">
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
            {isUnityProject ? (
              <>
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
              </>
            ) : null}
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

      <RuntimeSection
        title={t("workspace.userAttachmentSources")}
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
                    {formatAttachmentSize(source.size)} · {source.type || "-"}
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
                    ? t("workspace.showLess", { count: userAttachmentSources.length })
                    : t("workspace.viewAll", { count: userAttachmentSources.length - maxAttachmentSources })}
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
    </>
  );
}
