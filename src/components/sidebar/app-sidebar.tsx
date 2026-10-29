import {
  ArrowLeft,
  Bot,
  Boxes,
  Brain,
  Bug,
  Database,
  FolderPlus,
  Gauge,
  History,
  ListFilter,
  MessageSquare,
  Plug,
  RefreshCw,
  Settings,
  Shield,
  SlidersHorizontal,
  Wrench,
} from "lucide-react";
import type { MouseEvent as ReactMouseEvent, ReactNode } from "react";
import { useTranslation } from "react-i18next";
import { TEMP_CHATS_COLLAPSE_KEY, type ActiveView, type SettingsSection } from "../../lib/app-view";
import type { ChatThread } from "../../lib/chat-types";
import type { SidebarChatGroupsWithTimes } from "../../lib/sidebar-view";
import { normalizeProjectPathKey, projectKey } from "../../lib/project-path";
import { groupSidebarProjects } from "../../lib/sidebar-project-order";
import { cn } from "../../lib/utils";
import { useSidebarChatOrder } from "../../hooks/use-sidebar-chat-order";
import { SidebarChat, SidebarChatList, SidebarProject, SidebarProjectList, SidebarSection, type DragHandleProps } from "./sidebar";

type SidebarProjectItem = {
  name?: string;
  path?: string;
  editorVersion?: string;
  unityVersion?: string;
  sources?: string[];
  projectType?: "general" | "unity";
};

type AppSidebarProps = {
  collapsed: boolean;
  activeView: ActiveView;
  activeSettingsSection: SettingsSection;
  developerOptionsEnabled: boolean;
  temporaryChatActive: boolean;
  activeProjectPath: string;
  activeChatId: string;
  runtimeConnected: boolean;
  loadingProjects: boolean;
  projectItems: SidebarProjectItem[];
  chatSidebar: SidebarChatGroupsWithTimes;
  backgroundGoalUnreadByChat: Record<string, number>;
  emptyProjectState?: { name?: string; meta?: string } | null;
  collapsedProjects: Record<string, boolean>;
  temporaryChatsCollapsed: boolean;
  pinnedProjectSet: Set<string>;
  renamingProjectPath: string;
  projectRenameDraft: string;
  renamingChatId: string;
  renameDraft: string;
  projectDisplayName: (project?: SidebarProjectItem) => string;
  onNewTemporaryChat: () => void;
  onOpenProjectPicker: () => void;
  onOpenDoctor: () => void;
  onOpenOptimization: () => void;
  onOpenProtection: () => void;
  onOpenSkills: () => void;
  onOpenCheckpoints: () => void;
  onOpenSettings: () => void;
  onOpenSettingsSection: (section: SettingsSection) => void;
  onBackFromSettings: () => void;
  onRefreshProjects: () => void;
  onReorderProjects?: (orderedPaths: string[]) => void;
  onResetProjectOrder?: (paths: string[]) => void;
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
  activeSettingsSection,
  developerOptionsEnabled,
  temporaryChatActive,
  activeProjectPath,
  activeChatId,
  runtimeConnected,
  loadingProjects,
  projectItems,
  chatSidebar: unorderedChatSidebar,
  backgroundGoalUnreadByChat,
  emptyProjectState,
  collapsedProjects,
  temporaryChatsCollapsed,
  pinnedProjectSet,
  renamingProjectPath,
  projectRenameDraft,
  renamingChatId,
  renameDraft,
  projectDisplayName,
  onNewTemporaryChat,
  onOpenProjectPicker,
  onOpenDoctor,
  onOpenOptimization,
  onOpenProtection,
  onOpenSkills,
  onOpenCheckpoints,
  onOpenSettings,
  onOpenSettingsSection,
  onBackFromSettings,
  onRefreshProjects,
  onReorderProjects,
  onResetProjectOrder,
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
  const { orderedGroups: chatSidebar, reorderChats, resetChatOrder, manualChatIds } = useSidebarChatOrder(unorderedChatSidebar);
  const projectGroups = groupSidebarProjects(projectItems);
  const settingsNavItems = ([
    { section: "general", label: t("settings.navGeneral"), icon: <SlidersHorizontal className="h-4 w-4 shrink-0" /> },
    { section: "permissions", label: t("settings.navPermissions"), icon: <Shield className="h-4 w-4 shrink-0" /> },
    { section: "models", label: t("settings.navModels"), icon: <Bot className="h-4 w-4 shrink-0" /> },
    { section: "memory", label: t("settings.navMemory"), icon: <Brain className="h-4 w-4 shrink-0" /> },
    { section: "storage", label: t("settings.navStorage"), icon: <Database className="h-4 w-4 shrink-0" /> },
    { section: "connectors", label: t("settings.navConnectors"), icon: <Plug className="h-4 w-4 shrink-0" /> },
    { section: "instructions", label: t("settings.navInstructions"), icon: <Boxes className="h-4 w-4 shrink-0" /> },
    { section: "developer", label: t("settings.navDeveloper"), icon: <Bug className="h-4 w-4 shrink-0" />, developerOnly: true },
  ] satisfies Array<{ section: SettingsSection; label: string; icon: ReactNode; developerOnly?: boolean }>).filter((item) => !item.developerOnly || developerOptionsEnabled);

  if (activeView === "settings") {
    return (
      <aside
        className={cn(
          "sidebar-scrollbar flex h-full min-w-0 flex-col overflow-y-auto border-r border-border/80 bg-sidebar px-2 py-3 transition-[width] max-md:[&_nav_button]:justify-center max-md:[&_nav_button]:px-0 max-md:[&_nav_span]:hidden",
          collapsed ? "items-stretch [&_nav_button]:justify-center [&_nav_button]:px-0 [&_nav_span]:hidden" : "md:px-3",
        )}
      >
        <div className={cn("flex h-9 items-center gap-2 px-2", collapsed ? "justify-center" : "justify-between")}>
          <Settings className="h-4 w-4 shrink-0 text-primary" />
          {collapsed ? null : <div className="hidden min-w-0 flex-1 truncate text-sm font-semibold md:block">{t("sidebar.settings")}</div>}
        </div>
        <nav className="mt-4 space-y-0.5">
          <SidebarNavButton
            icon={<ArrowLeft className="h-4 w-4 shrink-0" />}
            label={t("settings.backToApp")}
            onClick={onBackFromSettings}
          />
        </nav>
        {collapsed ? null : <div className="mt-4 px-3 text-xs font-medium uppercase tracking-wide text-muted-foreground">{t("settings.navPersonal")}</div>}
        <nav className="mt-2 space-y-0.5">
          {settingsNavItems.map((item) => (
            <SidebarNavButton
              key={item.section}
              icon={item.icon}
              label={item.label}
              active={activeSettingsSection === item.section}
              onClick={() => onOpenSettingsSection(item.section)}
            />
          ))}
        </nav>
      </aside>
    );
  }

  return (
    <aside
      className={cn(
        "sidebar-scrollbar flex h-full min-w-0 flex-col overflow-y-auto border-r border-border/80 bg-sidebar px-2 py-3 transition-[width] max-md:[&_nav_button]:justify-center max-md:[&_nav_button]:px-0 max-md:[&_nav_span]:hidden",
        collapsed ? "items-stretch [&_nav_button]:justify-center [&_nav_button]:px-0 [&_nav_span]:hidden" : "md:px-3",
      )}
    >
      <div className={cn("flex h-9 items-center gap-2 px-2", collapsed ? "justify-center" : "justify-between")}>
        <Bot className="h-4 w-4 shrink-0 text-primary" />
        {collapsed ? null : <div className="hidden min-w-0 flex-1 truncate text-sm font-semibold md:block">VRCForge</div>}
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
        <SidebarNavButton
          icon={<Wrench className="h-4 w-4 shrink-0" />}
          label={t("sidebar.skills")}
          semanticId="skills"
          active={activeView === "skills"}
          onClick={onOpenSkills}
        />
        <SidebarNavButton icon={<History className="h-4 w-4 shrink-0" />} label={t("checkpoint.checkpoints")} active={activeView === "checkpoints"} onClick={onOpenCheckpoints} />
      </nav>

      {collapsed ? null : (
        <SidebarSection title={t("project.generalProject")} action={onResetProjectOrder ? (
          <button type="button" onClick={() => onResetProjectOrder(projectGroups.general.map((project) => projectKey(project)).filter(Boolean))} title={t("sidebar.sortProjectsByConversation")} aria-label={t("sidebar.sortProjectsByConversation")} className="flex h-6 w-6 items-center justify-center rounded-md text-muted-foreground transition-colors hover:bg-muted hover:text-foreground">
            <ListFilter className="h-3.5 w-3.5" />
          </button>
        ) : undefined}>
          <SidebarProjectList projects={projectGroups.general} activeProjectPath={activeProjectPath} pinnedProjectSet={pinnedProjectSet} onReorderProjects={onReorderProjects} renderProject={(project, index, dragHandle: DragHandleProps) => {
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
                    dragHandle={dragHandle}
                  />
                  {projectCollapsed ? null : (
                    <SidebarChatList chats={projectChats} activeChatId={activeChatId} onReorderChats={reorderChats} onResetOrder={projectChats.some((chat) => manualChatIds.has(chat.id)) ? () => resetChatOrder(projectChats.map((chat) => chat.id)) : undefined} indent renderChat={(chat, dragHandle) => (
                      <SidebarChat
                        key={chat.id}
                        chatId={chat.id}
                        dragHandle={dragHandle}
                        title={chat.title || t("sidebar.newChat")}
                        meta={chatSidebar.times.get(chat.id) || ""}
                        activity={chatSidebar.activityByChat.get(chat.id)}
                        active={activeView === "chat" && chat.id === activeChatId}
                        unreadCount={backgroundGoalUnreadByChat[chat.id] || 0}
                        indent
                        pinned={chat.pinned}
                        isTestChat={chat.isTestChat}
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
                    )} />
                  )}
                </div>
              );
            }} />
        </SidebarSection>
      )}

      {collapsed ? null : (
        <SidebarSection
          title={t("project.unityProject")}
          action={onResetProjectOrder ? (
            <div className="flex items-center gap-0.5">
              <button type="button" onClick={() => onResetProjectOrder(projectGroups.unity.map((project) => projectKey(project)).filter(Boolean))} title={t("sidebar.sortProjectsByConversation")} aria-label={t("sidebar.sortProjectsByConversation")} className="flex h-6 w-6 items-center justify-center rounded-md text-muted-foreground transition-colors hover:bg-muted hover:text-foreground">
                <ListFilter className="h-3.5 w-3.5" />
              </button>
              <button type="button" onClick={onRefreshProjects} disabled={!runtimeConnected || loadingProjects} title={t("workspace.refreshStatus")} aria-label={t("workspace.refreshStatus")} className="flex h-6 w-6 items-center justify-center rounded-md text-muted-foreground transition-colors hover:bg-muted hover:text-foreground disabled:pointer-events-none disabled:opacity-50">
                <RefreshCw className={cn("h-3.5 w-3.5", loadingProjects && "animate-spin")} />
              </button>
            </div>
          ) : (
            <button type="button" onClick={onRefreshProjects} disabled={!runtimeConnected || loadingProjects} title={t("workspace.refreshStatus")} aria-label={t("workspace.refreshStatus")} className="flex h-6 w-6 items-center justify-center rounded-md text-muted-foreground transition-colors hover:bg-muted hover:text-foreground disabled:pointer-events-none disabled:opacity-50">
              <RefreshCw className={cn("h-3.5 w-3.5", loadingProjects && "animate-spin")} />
            </button>
          )}
        >
          {projectGroups.unity.length > 0 ? (
            <SidebarProjectList projects={projectGroups.unity} activeProjectPath={activeProjectPath} pinnedProjectSet={pinnedProjectSet} onReorderProjects={onReorderProjects} renderProject={(project, index, dragHandle: DragHandleProps) => {
              const key = projectKey(project) || `project-unity-${index}`;
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
                    dragHandle={dragHandle}
                  />
                  {projectCollapsed ? null : (
                    <SidebarChatList chats={projectChats} activeChatId={activeChatId} onReorderChats={reorderChats} onResetOrder={projectChats.some((chat) => manualChatIds.has(chat.id)) ? () => resetChatOrder(projectChats.map((chat) => chat.id)) : undefined} indent renderChat={(chat, dragHandle) => (
                      <SidebarChat
                        key={chat.id}
                        chatId={chat.id}
                        dragHandle={dragHandle}
                        title={chat.title || t("sidebar.newChat")}
                        meta={chatSidebar.times.get(chat.id) || ""}
                        activity={chatSidebar.activityByChat.get(chat.id)}
                        active={activeView === "chat" && chat.id === activeChatId}
                        unreadCount={backgroundGoalUnreadByChat[chat.id] || 0}
                        indent
                        pinned={chat.pinned}
                        isTestChat={chat.isTestChat}
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
                    )} />
                  )}
                </div>
              );
            }} />
          ) : projectItems.length === 0 ? (
            <SidebarProject name={emptyProjectState?.name || t("agent.emptyProjectState.noUnityProject")} meta={emptyProjectState?.meta} active />
          ) : null}
        </SidebarSection>
      )}

      {collapsed ? null : (
        <SidebarSection title={t("sidebar.chats")} collapsed={temporaryChatsCollapsed} onToggleCollapse={() => onToggleProjectCollapse(TEMP_CHATS_COLLAPSE_KEY)}>
          {chatSidebar.temporaryChats.length > 0 ? (
            <SidebarChatList chats={chatSidebar.temporaryChats} activeChatId={activeChatId} onReorderChats={reorderChats} onResetOrder={chatSidebar.temporaryChats.some((chat) => manualChatIds.has(chat.id)) ? () => resetChatOrder(chatSidebar.temporaryChats.map((chat) => chat.id)) : undefined} renderChat={(chat, dragHandle) => (
              <SidebarChat
                key={chat.id}
                chatId={chat.id}
                dragHandle={dragHandle}
                title={chat.title || t("sidebar.newChat")}
                meta={chatSidebar.times.get(chat.id) || ""}
                activity={chatSidebar.activityByChat.get(chat.id)}
                active={activeView === "chat" && chat.id === activeChatId}
                unreadCount={backgroundGoalUnreadByChat[chat.id] || 0}
                pinned={chat.pinned}
                isTestChat={chat.isTestChat}
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
            )} />
          ) : (
            <div className="px-3 py-1 text-xs text-muted-foreground/70">{t("sidebar.noTempChats")}</div>
          )}
        </SidebarSection>
      )}

      {collapsed ? null : chatSidebar.testChats.length > 0 ? (
        <SidebarSection title={t("sidebar.testChats")}>
          <SidebarChatList chats={chatSidebar.testChats} activeChatId={activeChatId} onReorderChats={reorderChats} onResetOrder={chatSidebar.testChats.some((chat) => manualChatIds.has(chat.id)) ? () => resetChatOrder(chatSidebar.testChats.map((chat) => chat.id)) : undefined} renderChat={(chat, dragHandle) => (
            <SidebarChat
              key={chat.id}
              chatId={chat.id}
              dragHandle={dragHandle}
              title={chat.title || t("sidebar.newChat")}
              meta={chatSidebar.times.get(chat.id) || ""}
              activity={chatSidebar.activityByChat.get(chat.id)}
              active={activeView === "chat" && chat.id === activeChatId}
              unreadCount={backgroundGoalUnreadByChat[chat.id] || 0}
              pinned={chat.pinned}
              isTestChat
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
          )} />
        </SidebarSection>
      ) : null}

      <div className="mt-auto">
        <button
          onClick={onOpenSettings}
          aria-label={t("sidebar.settings")}
          title={t("sidebar.settings")}
          className={cn(
            "flex h-9 w-full min-w-0 items-center justify-center gap-2.5 rounded-md px-0 text-left text-sm transition-colors md:justify-start md:px-2.5",
            "text-muted-foreground hover:bg-muted hover:text-foreground",
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
  semanticId,
  active = false,
  onClick,
}: {
  icon: ReactNode;
  label: string;
  semanticId?: string;
  active?: boolean;
  onClick: () => void;
}) {
  return (
    <button
      onClick={onClick}
      data-vrcforge-sidebar-nav={semanticId}
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
