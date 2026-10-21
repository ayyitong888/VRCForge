import { ChevronDown, ChevronRight, Folder, Loader2, MessageSquare, MoreHorizontal, Pin, Trash2 } from "lucide-react";
import { MouseEvent as ReactMouseEvent, ReactNode, useId, useState } from "react";
import { useTranslation } from "react-i18next";
import i18n from "../../i18n";
import type { ChatThread } from "../../lib/chat-types";
import type { SidebarChatActivity } from "../../lib/sidebar-view";
import { normalizeProjectPathKey, projectKey } from "../../lib/project-path";
import { cn } from "../../lib/utils";

const SIDEBAR_PREVIEW_COUNT = 5;

export function SidebarChatList({
  chats,
  activeChatId,
  indent = false,
  renderChat,
}: {
  chats: ChatThread[];
  activeChatId: string;
  indent?: boolean;
  renderChat: (chat: ChatThread) => ReactNode;
}) {
  const { t } = useTranslation();
  return <SidebarOverflowList items={chats} isActive={(chat) => chat.id === activeChatId}
    indent={indent} renderItem={renderChat}
    moreLabel={(count) => t("sidebar.moreChats", { count })} fewerLabel={t("sidebar.fewerChats")} />;
}

export function SidebarProjectList<T extends { name?: string; path?: string }>({
  projects, activeProjectPath, renderProject,
}: {
  projects: T[];
  activeProjectPath: string;
  renderProject: (project: T, index: number) => ReactNode;
}) {
  const { t } = useTranslation();
  const activeKey = normalizeProjectPathKey(activeProjectPath);
  return <SidebarOverflowList items={projects}
    isActive={(project) => Boolean(activeKey) && normalizeProjectPathKey(projectKey(project)) === activeKey}
    renderItem={renderProject}
    moreLabel={(count) => t("sidebar.moreProjects", { count })} fewerLabel={t("sidebar.fewerProjects")} />;
}

function SidebarOverflowList<T>({ items, isActive, renderItem, moreLabel, fewerLabel, indent = false }: {
  items: T[];
  isActive: (item: T) => boolean;
  renderItem: (item: T, index: number) => ReactNode;
  moreLabel: (count: number) => string;
  fewerLabel: string;
  indent?: boolean;
}) {
  const [expanded, setExpanded] = useState(false);
  const listId = useId();

  if (items.length <= SIDEBAR_PREVIEW_COUNT) {
    return <>{items.map(renderItem)}</>;
  }

  const visibleItems = expanded
    ? items
    : items.filter((item, index) => index < SIDEBAR_PREVIEW_COUNT || isActive(item));
  const hiddenCount = items.length - visibleItems.length;

  return (
    <>
      <div id={`sidebar-list-${listId}`} className="space-y-1">
        {visibleItems.map(renderItem)}
      </div>
      {expanded || hiddenCount > 0 ? (
        <button
          type="button"
          aria-controls={`sidebar-list-${listId}`}
          aria-expanded={expanded}
          onClick={() => setExpanded((value) => !value)}
          className={cn(
            "flex h-8 w-full min-w-0 items-center rounded-md py-1 text-left text-xs text-muted-foreground transition-colors hover:bg-muted hover:text-foreground",
            indent ? "pl-9 pr-3" : "px-3",
          )}
        >
          {expanded ? fewerLabel : moreLabel(hiddenCount)}
        </button>
      ) : null}
    </>
  );
}

export function SidebarSection({
  title,
  children,
  collapsed = false,
  onToggleCollapse,
  action,
}: {
  title: string;
  children: ReactNode;
  collapsed?: boolean;
  onToggleCollapse?: () => void;
  action?: ReactNode;
}) {
  const { t } = useTranslation();
  return (
    <section className="mt-8 min-w-0 max-md:hidden">
      <div className="mb-3 flex min-w-0 items-center gap-1 px-2">
        {onToggleCollapse ? (
          <button
            type="button"
            onClick={onToggleCollapse}
            title={collapsed ? t("common.expand") : t("common.collapse")}
            className="group flex min-w-0 flex-1 items-center gap-1 text-left text-xs font-medium text-muted-foreground hover:text-foreground"
          >
            <span className="truncate">{title}</span>
            <span className={cn("shrink-0", collapsed ? "" : "opacity-0 group-hover:opacity-100")}>
              {collapsed ? <ChevronRight className="h-3 w-3" /> : <ChevronDown className="h-3 w-3" />}
            </span>
          </button>
        ) : (
          <div className="min-w-0 flex-1 truncate text-xs font-medium text-muted-foreground">{title}</div>
        )}
        {action ? <div className="shrink-0">{action}</div> : null}
      </div>
      {collapsed ? null : <div className="space-y-1">{children}</div>}
    </section>
  );
}

export function SidebarProject({
  name,
  meta,
  active = false,
  collapsed = false,
  hasChats = false,
  pinned = false,
  renaming = false,
  renameDraft = "",
  onClick,
  onToggleCollapse,
  onOpenMenu,
  onContextMenu,
  onRenameChange,
  onRenameCommit,
}: {
  name: string;
  meta?: string;
  active?: boolean;
  collapsed?: boolean;
  hasChats?: boolean;
  pinned?: boolean;
  renaming?: boolean;
  renameDraft?: string;
  onClick?: () => void;
  onToggleCollapse?: () => void;
  onOpenMenu?: (event: ReactMouseEvent) => void;
  onContextMenu?: (event: ReactMouseEvent) => void;
  onRenameChange?: (value: string) => void;
  onRenameCommit?: (cancel?: boolean) => void;
}) {
  if (renaming) {
    return (
      <div className="flex h-11 w-full min-w-0 items-center rounded-md bg-muted px-2">
        <input
          autoFocus
          value={renameDraft}
          onChange={(event) => onRenameChange?.(event.target.value)}
          onBlur={() => onRenameCommit?.()}
          onKeyDown={(event) => {
            if (event.key === "Enter" && !event.nativeEvent.isComposing) {
              event.preventDefault();
              onRenameCommit?.();
            }
            if (event.key === "Escape") {
              onRenameCommit?.(true);
            }
          }}
          className="h-7 w-full min-w-0 rounded border border-primary/40 bg-background px-2 text-sm outline-none focus:border-primary"
        />
      </div>
    );
  }
  return (
    <div
      onContextMenu={onContextMenu}
      className={cn(
        "group flex h-11 w-full min-w-0 items-center rounded-md pr-1 text-sm transition-colors",
        active ? "bg-muted text-foreground" : "text-muted-foreground",
        onClick ? "hover:bg-muted hover:text-foreground" : "cursor-default",
      )}
    >
      <button onClick={onClick} disabled={!onClick} className="flex h-full min-w-0 flex-1 items-center gap-3 px-3 text-left">
        <Folder className="h-4 w-4 shrink-0" />
        <span className="min-w-0 flex-1 truncate">{name}</span>
        {pinned ? <Pin className="h-3.5 w-3.5 shrink-0 text-primary/60" /> : null}
        {meta ? <span className="max-w-[78px] shrink-0 truncate text-xs text-muted-foreground">{meta}</span> : null}
      </button>
      {onOpenMenu ? (
        <button
          type="button"
          title={i18n.t("project.menu")}
          onClick={onOpenMenu}
          className="shrink-0 rounded p-1 text-muted-foreground opacity-0 hover:bg-background hover:text-foreground group-hover:opacity-100"
        >
          <MoreHorizontal className="h-3.5 w-3.5" />
        </button>
      ) : null}
      {onToggleCollapse && hasChats ? (
        <button
          type="button"
          title={collapsed ? i18n.t("project.expandChats") : i18n.t("project.collapseChats")}
          onClick={(event) => {
            event.stopPropagation();
            onToggleCollapse();
          }}
          className={cn(
            "shrink-0 rounded p-1 text-muted-foreground hover:bg-background hover:text-foreground",
            collapsed || hasChats ? "" : "opacity-0 group-hover:opacity-100",
          )}
        >
          {collapsed ? <ChevronRight className="h-3.5 w-3.5" /> : <ChevronDown className="h-3.5 w-3.5" />}
        </button>
      ) : null}
    </div>
  );
}

export function SidebarChat({
  chatId,
  title,
  meta,
  activity,
  active = false,
  unreadCount = 0,
  indent = false,
  pinned = false,
  isTestChat = false,
  renaming = false,
  renameDraft = "",
  onClick,
  onTogglePin,
  onDelete,
  onContextMenu,
  onRenameChange,
  onRenameCommit,
}: {
  chatId?: string;
  title: string;
  meta?: string;
  activity?: SidebarChatActivity;
  active?: boolean;
  unreadCount?: number;
  indent?: boolean;
  pinned?: boolean;
  isTestChat?: boolean;
  renaming?: boolean;
  renameDraft?: string;
  onClick: () => void;
  onTogglePin?: () => void;
  onDelete?: () => void;
  onContextMenu?: (event: ReactMouseEvent) => void;
  onRenameChange?: (value: string) => void;
  onRenameCommit?: (cancel?: boolean) => void;
}) {
  const { t } = useTranslation();
  if (renaming) {
    return (
      <div className={cn("flex h-9 w-full min-w-0 items-center rounded-md bg-muted px-2", indent ? "pl-9" : "")}>
        <input
          autoFocus
          value={renameDraft}
          onChange={(event) => onRenameChange?.(event.target.value)}
          onBlur={() => onRenameCommit?.()}
          onKeyDown={(event) => {
            if (event.key === "Enter" && !event.nativeEvent.isComposing) {
              event.preventDefault();
              onRenameCommit?.();
            }
            if (event.key === "Escape") {
              onRenameCommit?.(true);
            }
          }}
          className="h-7 w-full min-w-0 rounded border border-primary/40 bg-background px-2 text-sm outline-none focus:border-primary"
        />
      </div>
    );
  }
  return (
    <div
      data-vrcforge-chat-id={chatId || undefined}
      data-vrcforge-chat-active={active ? "true" : "false"}
      onContextMenu={onContextMenu}
      className={cn(
        "group flex h-9 w-full min-w-0 items-center rounded-md text-sm transition-colors",
        indent ? "pl-6" : "",
        active ? "bg-muted text-foreground" : "text-muted-foreground hover:bg-muted hover:text-foreground",
      )}
    >
      <button onClick={onClick} className="flex h-full min-w-0 flex-1 items-center gap-2 px-3 text-left">
        {indent ? null : <MessageSquare className="h-4 w-4 shrink-0" />}
        <span className="min-w-0 flex-1 truncate">{title}</span>
        {isTestChat ? (
          <span
            className="shrink-0 rounded border border-primary/40 px-1 text-[9px] font-semibold uppercase tracking-wide text-primary"
            title={t("contextMenu.testChat")}
            aria-label={t("contextMenu.testChat")}
          >
            {t("contextMenu.testChat")}
          </span>
        ) : null}
        {unreadCount > 0 ? (
          <span
            className="inline-flex min-w-5 shrink-0 items-center justify-center rounded-full bg-primary px-1.5 text-[10px] font-semibold text-primary-foreground"
            data-background-goal-unread={unreadCount}
            aria-label={t("goal.backgroundUnread", { count: unreadCount })}
          >
            {unreadCount > 99 ? "99+" : unreadCount}
          </span>
        ) : null}
        {meta ? <span className="shrink-0 text-xs text-muted-foreground/75 group-hover:hidden">{meta}</span> : null}
        {activity === "running" ? <Loader2 className="h-3.5 w-3.5 shrink-0 animate-spin text-primary" aria-label={i18n.t("workspace.runStatusRunning")} /> : null}
        {activity === "completed" ? <span className="h-2 w-2 shrink-0 rounded-full bg-primary" aria-label={i18n.t("workspace.runStatusCompleted")} /> : null}
      </button>
      {pinned ? <Pin className="h-3.5 w-3.5 shrink-0 text-primary/60 group-hover:hidden" /> : null}
      <div className="hidden shrink-0 items-center gap-0.5 pr-1 group-hover:flex">
        <button
          type="button"
          title={pinned ? t("contextMenu.unpinChat") : t("contextMenu.pinChat")}
          onClick={(event) => {
            event.stopPropagation();
            onTogglePin?.();
          }}
          className="rounded p-1 text-muted-foreground hover:bg-background hover:text-foreground"
        >
          <Pin className={cn("h-3.5 w-3.5", pinned ? "text-primary" : "")} />
        </button>
        <button
          type="button"
          title={t("contextMenu.permanentDelete")}
          onClick={(event) => {
            event.stopPropagation();
            onDelete?.();
          }}
          className="rounded p-1 text-destructive/60 hover:bg-destructive/10 hover:text-destructive"
        >
          <Trash2 className="h-3.5 w-3.5" />
        </button>
      </div>
    </div>
  );
}
