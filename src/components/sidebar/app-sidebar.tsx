import {
  Bot,
  FolderPlus,
  Gauge,
  History,
  MessageSquare,
  PanelLeftClose,
  PanelLeftOpen,
  RefreshCw,
  Settings,
  Shield,
  Wrench,
} from "lucide-react";
import type { MouseEvent as ReactMouseEvent, ReactNode } from "react";
import { useTranslation } from "react-i18next";
import { TEMP_CHATS_COLLAPSE_KEY, type ActiveView } from "../../lib/app-view";
import type { ChatThread } from "../../lib/chat-types";
import type { ChatSidebarGroups } from "../../lib/chat-thread";
import { normalizeProjectPathKey, projectKey } from "../../lib/project-path";
import { cn } from "../../lib/utils";
import { SidebarChat, SidebarProject, SidebarSection } from "./sidebar";

type SidebarChatGroupsWithTimes = ChatSidebarGroups & {
  times: Map<string, string>;
};

type SidebarProjectItem = {
  name?: string;
  path?: string;
  editorVersion?: string;
  unityVersion?: string;
  sources?: string[];
};

type AppSidebarProps = {
  collapsed: boolean;
  activeView: ActiveView;
  temporaryChatActive: boolean;
  activeProjectPath: string;
  activeChatId: string;
  runtimeConnected: boolean;
  loadingProjects: boolean;
  projectItems: SidebarProjectItem[];
  chatSidebar: SidebarChatGroupsWithTimes;
  emptyProjectState?: { name?: string; meta?: string } | null;
  collapsedProjects: Record<string, boolean>;
  temporaryChatsCollapsed: boolean;
  pinnedProjectSet: Set<string>;
  renamingProjectPath: string;
  projectRenameDraft: string;
  renamingChatId: string;
  renameDraft: string;
  projectDisplayName: (project?: SidebarProjectItem) => string;
  onToggleSidebar: () => void;
  onNewTemporaryChat: () => void;
  onOpenProjectPicker: () => void;
  onOpenDoctor: () => void;
  onOpenOptimization: () => void;
  onOpenProtection: () => void;
  onOpenSkills: () => void;
  onOpenCheckpoints: () => void;
  onOpenSettings: () => void;
  onRefreshProjects: () => void;
  onSelectProject: (path: string) => void;
  onToggleProjectCollapse: (path: string) => void;
  onProjectMenu: (projectPath: string, event: ReactMouseEvent) => void;
  onProjectRenameChange: (value: string) => void;
  onProjectRenameCommit: (cancel?: boolean) => void;
  onOpenChat: (chat: ChatThread) => void;
  onTogglePinChat: (chatId: string) => void;
  onDeleteChat: (chatId: string) => void;
  onChatMenu: (chatId: string, event: ReactMouseEvent) => void;
  onChatRenameChange: (value: string) => void;
  onChatRenameCommit: (cancel?: boolean) => void;
};

export function AppSidebar({
  collapsed,
  activeView,
  temporaryChatActive,
  activeProjectPath,
  activeChatId,
  runtimeConnected,
  loadingProjects,
  projectItems,
  chatSidebar,
  emptyProjectState,
  collapsedProjects,
  temporaryChatsCollapsed,
  pinnedProjectSet,
  renamingProjectPath,
  projectRenameDraft,
  renamingChatId,
  renameDraft,
  projectDisplayName,
  onToggleSidebar,
  onNewTemporaryChat,
  onOpenProjectPicker,
  onOpenDoctor,
  onOpenOptimization,
  onOpenProtection,
  onOpenSkills,
  onOpenCheckpoints,
  onOpenSettings,
  onRefreshProjects,
  onSelectProject,
  onToggleProjectCollapse,
  onProjectMenu,
  onProjectRenameChange,
  onProjectRenameCommit,
  onOpenChat,
  onTogglePinChat,
  onDeleteChat,
  onChatMenu,
  onChatRenameChange,
  onChatRenameCommit,
}: AppSidebarProps) {
  const { t } = useTranslation();

  return (
    <aside
      className={cn(
        "sidebar-scrollbar flex h-screen min-w-0 flex-col overflow-y-auto border-r border-border/80 bg-sidebar px-2 py-3 transition-[width] max-md:[&_nav_button]:justify-center max-md:[&_nav_button]:px-0 max-md:[&_nav_span]:hidden",
        collapsed ? "items-stretch [&_nav_button]:justify-center [&_nav_button]:px-0 [&_nav_span]:hidden" : "md:px-3",
      )}
    >
      <div className={cn("flex h-9 items-center gap-2 px-2", collapsed ? "justify-center" : "justify-between")}>
        <Bot className="h-4 w-4 shrink-0 text-primary" />
        {collapsed ? null : <div className="hidden min-w-0 flex-1 truncate text-sm font-semibold md:block">VRCForge</div>}
        <button
          type="button"
          className="flex h-7 w-7 shrink-0 items-center justify-center rounded-md text-muted-foreground transition-colors hover:bg-muted hover:text-foreground"
          onClick={onToggleSidebar}
          title={collapsed ? t("sidebar.expandSidebar") : t("sidebar.collapseSidebar")}
        >
          {collapsed ? <PanelLeftOpen className="h-4 w-4" /> : <PanelLeftClose className="h-4 w-4" />}
        </button>
      </div>

      <nav className="mt-4 space-y-0.5">
        <SidebarNavButton
          icon={<MessageSquare className="h-4 w-4 shrink-0" />}
          label={t("sidebar.tempChat")}
          active={temporaryChatActive}
          onClick={onNewTemporaryChat}
        />
        <SidebarNavButton icon={<FolderPlus className="h-4 w-4 shrink-0" />} label={t("sidebar.newProject")} onClick={onOpenProjectPicker} />
        <SidebarNavButton icon={<Shield className="h-4 w-4 shrink-0" />} label={t("sidebar.doctor")} active={activeView === "doctor"} onClick={onOpenDoctor} />
        <SidebarNavButton icon={<Gauge className="h-4 w-4 shrink-0" />} label={t("sidebar.optimization")} active={activeView === "optimization"} onClick={onOpenOptimization} />
        <SidebarNavButton icon={<Shield className="h-4 w-4 shrink-0" />} label={t("encryption.protection")} active={activeView === "protection"} onClick={onOpenProtection} />
        <SidebarNavButton icon={<Wrench className="h-4 w-4 shrink-0" />} label={t("sidebar.skills")} active={activeView === "skills"} onClick={onOpenSkills} />
        <SidebarNavButton icon={<History className="h-4 w-4 shrink-0" />} label={t("checkpoint.checkpoints")} active={activeView === "checkpoints"} onClick={onOpenCheckpoints} />
      </nav>

      {collapsed ? null : (
        <SidebarSection
          title={t("sidebar.projects")}
          action={
            <button
              type="button"
              onClick={onRefreshProjects}
              disabled={!runtimeConnected || loadingProjects}
              title={t("workspace.refreshStatus")}
              aria-label={t("workspace.refreshStatus")}
              className="flex h-6 w-6 items-center justify-center rounded-md text-muted-foreground transition-colors hover:bg-muted hover:text-foreground disabled:pointer-events-none disabled:opacity-50"
            >
              <RefreshCw className={cn("h-3.5 w-3.5", loadingProjects && "animate-spin")} />
            </button>
          }
        >
          {projectItems.length > 0 ? (
            projectItems.map((project, index) => {
              const key = projectKey(project) || `project-${index}`;
              const projectChats = chatSidebar.projectChatsByPath.get(normalizeProjectPathKey(key)) || [];
              const projectCollapsed = Boolean(collapsedProjects[key]);
              return (
                <div key={key} className="min-w-0">
                  <SidebarProject
                    name={projectDisplayName(project)}
                    meta={project.editorVersion || project.unityVersion || (project.sources ?? []).join("+")}
                    active={activeView === "chat" && normalizeProjectPathKey(key) === normalizeProjectPathKey(activeProjectPath)}
                    collapsed={projectCollapsed}
                    hasChats={projectChats.length > 0}
                    pinned={pinnedProjectSet.has(normalizeProjectPathKey(key))}
                    renaming={renamingProjectPath === key}
                    renameDraft={projectRenameDraft}
                    onRenameChange={onProjectRenameChange}
                    onRenameCommit={onProjectRenameCommit}
                    onToggleCollapse={() => onToggleProjectCollapse(key)}
                    onClick={() => onSelectProject(key)}
                    onOpenMenu={(event) => onProjectMenu(key, event)}
                    onContextMenu={(event) => {
                      event.preventDefault();
                      onProjectMenu(key, event);
                    }}
                  />
                  {projectCollapsed
                    ? null
                    : projectChats.map((chat) => (
                        <SidebarChat
                          key={chat.id}
                          title={chat.title || t("sidebar.newChat")}
                          meta={chatSidebar.times.get(chat.id) || ""}
                          active={activeView === "chat" && chat.id === activeChatId}
                          indent
                          pinned={chat.pinned}
                          renaming={renamingChatId === chat.id}
                          renameDraft={renameDraft}
                          onRenameChange={onChatRenameChange}
                          onRenameCommit={onChatRenameCommit}
                          onClick={() => onOpenChat(chat)}
                          onTogglePin={() => onTogglePinChat(chat.id)}
                          onDelete={() => onDeleteChat(chat.id)}
                          onContextMenu={(event) => {
                            event.preventDefault();
                            onChatMenu(chat.id, event);
                          }}
                        />
                      ))}
                </div>
              );
            })
          ) : (
            <SidebarProject name={emptyProjectState?.name || t("agent.emptyProjectState.noUnityProject")} meta={emptyProjectState?.meta} active />
          )}
        </SidebarSection>
      )}

      {collapsed ? null : (
        <SidebarSection title={t("sidebar.chats")} collapsed={temporaryChatsCollapsed} onToggleCollapse={() => onToggleProjectCollapse(TEMP_CHATS_COLLAPSE_KEY)}>
          {chatSidebar.temporaryChats.length > 0 ? (
            chatSidebar.temporaryChats.map((chat) => (
              <SidebarChat
                key={chat.id}
                title={chat.title || t("sidebar.newChat")}
                meta={chatSidebar.times.get(chat.id) || ""}
                active={activeView === "chat" && chat.id === activeChatId}
                pinned={chat.pinned}
                renaming={renamingChatId === chat.id}
                renameDraft={renameDraft}
                onRenameChange={onChatRenameChange}
                onRenameCommit={onChatRenameCommit}
                onClick={() => onOpenChat(chat)}
                onTogglePin={() => onTogglePinChat(chat.id)}
                onDelete={() => onDeleteChat(chat.id)}
                onContextMenu={(event) => {
                  event.preventDefault();
                  onChatMenu(chat.id, event);
                }}
              />
            ))
          ) : (
            <div className="px-3 py-1 text-xs text-muted-foreground/70">{t("sidebar.noTempChats")}</div>
          )}
        </SidebarSection>
      )}

      <div className="mt-auto">
        <button
          onClick={onOpenSettings}
          aria-label={t("sidebar.settings")}
          title={t("sidebar.settings")}
          className={cn(
            "flex h-9 w-full min-w-0 items-center justify-center gap-2.5 rounded-md px-0 text-left text-sm transition-colors md:justify-start md:px-2.5",
            activeView === "settings" ? "bg-muted text-foreground" : "text-muted-foreground hover:bg-muted hover:text-foreground",
          )}
        >
          <Settings className="h-4 w-4 shrink-0" />
          {collapsed ? null : <span className="hidden truncate md:inline">{t("sidebar.settings")}</span>}
        </button>
      </div>
    </aside>
  );
}

function SidebarNavButton({
  icon,
  label,
  active = false,
  onClick,
}: {
  icon: ReactNode;
  label: string;
  active?: boolean;
  onClick: () => void;
}) {
  return (
    <button
      onClick={onClick}
      aria-label={label}
      title={label}
      className={cn(
        "flex h-9 w-full min-w-0 items-center gap-2.5 rounded-md px-2.5 text-left text-sm transition-colors",
        active ? "bg-muted text-foreground" : "text-muted-foreground hover:bg-muted hover:text-foreground",
      )}
    >
      {icon}
      <span className="truncate">{label}</span>
    </button>
  );
}
