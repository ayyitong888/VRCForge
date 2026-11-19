import {
  Archive,
  Bot,
  Bug,
  ChevronDown,
  ChevronRight,
  EyeOff,
  FolderOpen,
  Link,
  MessageSquare,
  Pencil,
  Pin,
  Plus,
  Trash2,
} from "lucide-react";
import { useEffect, useLayoutEffect, useRef, useState, type ReactNode, type Ref } from "react";
import { createPortal } from "react-dom";
import { useTranslation } from "react-i18next";
import type { ChatThread } from "../../lib/chat-types";
import { normalizeProjectPathKey } from "../../lib/project-path";
import { cn } from "../../lib/utils";
import { Button } from "../ui/button";
import { DeleteProjectDialog } from "./delete-project-dialog";

export type ProjectMenuState = { projectPath: string; x: number; y: number };
export type ChatMenuState = { chatId: string; x: number; y: number };
export type SelectionMenuState = { x: number; y: number; text: string };

type SidebarMenusProps = {
  projectMenu: ProjectMenuState | null;
  chatMenu: ChatMenuState | null;
  selectionMenu: SelectionMenuState | null;
  deleteTargetId: string;
  chats: ChatThread[];
  customPathSet: Set<string>;
  collapsedProjects: Record<string, boolean>;
  pinnedProjectSet: Set<string>;
  selectionMenuRef: Ref<HTMLDivElement>;
  onCloseProjectMenu: () => void;
  onTogglePinProject: (projectPath: string) => void;
  onOpenProjectFolder: (projectPath: string) => void;
  onNewConversation: (projectPath: string) => void;
  onStartRenameProject: (projectPath: string) => void;
  onToggleProjectCollapse: (projectPath: string) => void;
  onArchiveProjectChats: (projectPath: string, archive: boolean) => void;
  onHideProject: (projectPath: string) => void;
  onRemoveCustomProject: (projectPath: string) => void;
  canRecycleProject: (projectPath: string) => boolean;
  onRecycleProject: (projectPath: string, confirmation: string) => Promise<void>;
  onAskInNewSession: (text: string) => void;
  onAddSelectionToComposer: (text: string) => void;
  onCloseChatMenu: () => void;
  onTogglePinChat: (chatId: string) => void;
  onToggleTestChat: (chatId: string) => void;
  onStartRenameChat: (chat: ChatThread) => void;
  onCopyChatLink: (chatId: string) => void;
  onDeleteChat: (chatId: string) => void;
  onCancelDeleteChat: () => void;
  onConfirmDeleteChat: (chatId: string) => void;
};

export function SidebarMenus({
  projectMenu,
  chatMenu,
  selectionMenu,
  deleteTargetId,
  chats,
  customPathSet,
  collapsedProjects,
  pinnedProjectSet,
  selectionMenuRef,
  onCloseProjectMenu,
  onTogglePinProject,
  onOpenProjectFolder,
  onNewConversation,
  onStartRenameProject,
  onToggleProjectCollapse,
  onArchiveProjectChats,
  onHideProject,
  onRemoveCustomProject,
  canRecycleProject,
  onRecycleProject,
  onAskInNewSession,
  onAddSelectionToComposer,
  onCloseChatMenu,
  onTogglePinChat,
  onToggleTestChat,
  onStartRenameChat,
  onCopyChatLink,
  onDeleteChat,
  onCancelDeleteChat,
  onConfirmDeleteChat,
}: SidebarMenusProps) {
  const { t } = useTranslation();
  const menuChat = chatMenu ? chats.find((chat) => chat.id === chatMenu.chatId) : null;
  const deleteTarget = deleteTargetId ? chats.find((chat) => chat.id === deleteTargetId) : null;
  const [projectDeletePath, setProjectDeletePath] = useState("");

  return (
    <>
      {projectMenu ? createPortal(
        <ProjectContextMenu
          menu={projectMenu}
          chats={chats}
          customPathSet={customPathSet}
          collapsedProjects={collapsedProjects}
          pinnedProjectSet={pinnedProjectSet}
          onClose={onCloseProjectMenu}
          onTogglePinProject={onTogglePinProject}
          onOpenProjectFolder={onOpenProjectFolder}
          onNewConversation={onNewConversation}
          onStartRenameProject={onStartRenameProject}
          onToggleProjectCollapse={onToggleProjectCollapse}
          onArchiveProjectChats={onArchiveProjectChats}
          onHideProject={onHideProject}
          onRemoveCustomProject={onRemoveCustomProject}
          onDeleteProject={canRecycleProject(projectMenu.projectPath) ? setProjectDeletePath : undefined}
        />,
        document.body,
      ) : null}

      {selectionMenu ? (
        <div
          ref={selectionMenuRef}
          className="fixed z-50 flex w-max max-w-[calc(100vw-1rem)] flex-wrap items-center gap-0.5 rounded-lg border border-border bg-card p-1 shadow-panel"
          style={{ left: 0, top: 0 }}
          onMouseUp={(event) => event.stopPropagation()}
        >
          <button
            type="button"
            className="flex shrink-0 items-center gap-1.5 whitespace-nowrap rounded-md px-2.5 py-1.5 text-xs transition-colors hover:bg-muted"
            onClick={() => onAddSelectionToComposer(selectionMenu.text)}
          >
            <MessageSquare className="h-3.5 w-3.5 shrink-0" />
            {t("contextMenu.addToChat")}
          </button>
          <button
            type="button"
            className="flex shrink-0 items-center gap-1.5 whitespace-nowrap rounded-md px-2.5 py-1.5 text-xs transition-colors hover:bg-muted"
            onClick={() => onAskInNewSession(selectionMenu.text)}
          >
            <Bot className="h-3.5 w-3.5 shrink-0" />
            {t("contextMenu.askInNewSession")}
          </button>
        </div>
      ) : null}

      {chatMenu && menuChat ? (
        <ChatContextMenu
          menu={chatMenu}
          chat={menuChat}
          onClose={onCloseChatMenu}
          onTogglePinChat={onTogglePinChat}
          onToggleTestChat={onToggleTestChat}
          onStartRenameChat={onStartRenameChat}
          onCopyChatLink={onCopyChatLink}
          onDeleteChat={onDeleteChat}
        />
      ) : null}

      {deleteTargetId ? (
        <DeleteChatDialog
          title={deleteTarget?.title || t("sidebar.newChat")}
          chatId={deleteTargetId}
          onCancel={onCancelDeleteChat}
          onConfirm={onConfirmDeleteChat}
        />
      ) : null}
      {projectDeletePath ? (
        <DeleteProjectDialog
          path={projectDeletePath}
          onCancel={() => setProjectDeletePath("")}
          onConfirm={async (confirmation) => {
            await onRecycleProject(projectDeletePath, confirmation);
            setProjectDeletePath("");
          }}
        />
      ) : null}
    </>
  );
}

function ProjectContextMenu({
  menu,
  chats,
  customPathSet,
  collapsedProjects,
  pinnedProjectSet,
  onClose,
  onTogglePinProject,
  onOpenProjectFolder,
  onNewConversation,
  onStartRenameProject,
  onToggleProjectCollapse,
  onArchiveProjectChats,
  onHideProject,
  onRemoveCustomProject,
  onDeleteProject,
}: {
  menu: ProjectMenuState;
  chats: ChatThread[];
  customPathSet: Set<string>;
  collapsedProjects: Record<string, boolean>;
  pinnedProjectSet: Set<string>;
  onClose: () => void;
  onTogglePinProject: (projectPath: string) => void;
  onOpenProjectFolder: (projectPath: string) => void;
  onNewConversation: (projectPath: string) => void;
  onStartRenameProject: (projectPath: string) => void;
  onToggleProjectCollapse: (projectPath: string) => void;
  onArchiveProjectChats: (projectPath: string, archive: boolean) => void;
  onHideProject: (projectPath: string) => void;
  onRemoveCustomProject: (projectPath: string) => void;
  onDeleteProject?: (projectPath: string) => void;
}) {
  const { t } = useTranslation();
  const menuRef = useRef<HTMLDivElement>(null);
  const menuPath = menu.projectPath;
  const menuKey = normalizeProjectPathKey(menuPath);
  const isCustom = customPathSet.has(menuKey);
  const collapsed = Boolean(collapsedProjects[menuPath]);
  const pinned = pinnedProjectSet.has(menuKey);
  const projectChatCount = chats.filter((chat) => normalizeProjectPathKey(chat.projectPath) === menuKey && !chat.archived).length;
  const archivedChatCount = chats.filter((chat) => normalizeProjectPathKey(chat.projectPath) === menuKey && chat.archived).length;

  useLayoutEffect(() => {
    const element = menuRef.current;
    if (element) element.style.top = `${Math.max(8, Math.min(menu.y, window.innerHeight - element.offsetHeight - 8))}px`;
  }, [menu.y]);

  useEffect(() => {
    menuRef.current?.querySelector<HTMLButtonElement>('button[role="menuitem"]')?.focus();
  }, []);

  return (
    <>
      <MenuScrim onClose={onClose} />
      <div
        ref={menuRef}
        role="menu"
        aria-label={t("project.menu")}
        onKeyDown={(event) => {
          if (event.key === "Escape") {
            event.preventDefault();
            onClose();
          }
        }}
        className="fixed z-50 max-h-[calc(100vh-1rem)] w-56 overflow-y-auto rounded-lg border border-border bg-card p-1.5 shadow-panel"
        style={{
          left: Math.max(8, Math.min(menu.x, window.innerWidth - 240)),
        }}
      >
        <MenuButton
          icon={<Pin className={cn("h-4 w-4 shrink-0", pinned ? "text-primary" : "")} />}
          label={pinned ? t("project.unpinProject") : t("project.pinProject")}
          onClick={() => {
            onTogglePinProject(menuPath);
            onClose();
          }}
        />
        <MenuButton
          icon={<FolderOpen className="h-4 w-4 shrink-0" />}
          label={t("project.openInExplorer")}
          onClick={() => {
            onOpenProjectFolder(menuPath);
            onClose();
          }}
        />
        <MenuButton
          icon={<Plus className="h-4 w-4 shrink-0" />}
          label={t("project.newChatInProject")}
          onClick={() => {
            onNewConversation(menuPath);
            onClose();
          }}
        />
        <MenuButton
          icon={<Pencil className="h-4 w-4 shrink-0" />}
          label={t("project.renameProject")}
          onClick={() => {
            onStartRenameProject(menuPath);
            onClose();
          }}
        />
        <MenuButton
          icon={collapsed ? <ChevronDown className="h-4 w-4 shrink-0" /> : <ChevronRight className="h-4 w-4 shrink-0" />}
          label={collapsed ? t("project.expandChats") : t("project.collapseChats")}
          onClick={() => {
            onToggleProjectCollapse(menuPath);
            onClose();
          }}
        />
        {projectChatCount > 0 || archivedChatCount > 0 ? (
          <MenuButton
            icon={<Archive className="h-4 w-4 shrink-0" />}
            label={projectChatCount > 0 ? t("project.archiveChats") : t("project.restoreArchived")}
            onClick={() => {
              onArchiveProjectChats(menuPath, projectChatCount > 0);
              onClose();
            }}
          />
        ) : null}
        <MenuButton
          icon={<EyeOff className="h-4 w-4 shrink-0" />}
          label={t("project.hideProject")}
          onClick={() => {
            onHideProject(menuPath);
            onClose();
          }}
        />
        {isCustom ? (
          <MenuButton
            destructive
            icon={<Trash2 className="h-4 w-4 shrink-0" />}
            label={t("project.removeProject")}
            onClick={() => {
              onRemoveCustomProject(menuPath);
              onClose();
            }}
          />
        ) : null}
        {onDeleteProject ? (
          <MenuButton
            destructive
            icon={<Trash2 className="h-4 w-4 shrink-0" />}
            label={t("project.deleteProject")}
            onClick={() => {
              onDeleteProject(menuPath);
              onClose();
            }}
          />
        ) : null}
      </div>
    </>
  );
}

function ChatContextMenu({
  menu,
  chat,
  onClose,
  onTogglePinChat,
  onToggleTestChat,
  onStartRenameChat,
  onCopyChatLink,
  onDeleteChat,
}: {
  menu: ChatMenuState;
  chat: ChatThread;
  onClose: () => void;
  onTogglePinChat: (chatId: string) => void;
  onToggleTestChat: (chatId: string) => void;
  onStartRenameChat: (chat: ChatThread) => void;
  onCopyChatLink: (chatId: string) => void;
  onDeleteChat: (chatId: string) => void;
}) {
  const { t } = useTranslation();
  return (
    <>
      <MenuScrim onClose={onClose} />
      <div
        className="fixed z-50 w-48 rounded-lg border border-border bg-card p-1.5 shadow-panel"
        style={{
          left: Math.min(menu.x, window.innerWidth - 210),
          top: Math.min(menu.y, window.innerHeight - 190),
        }}
      >
        <MenuButton
          icon={<Pin className="h-4 w-4 shrink-0" />}
          label={chat.pinned ? t("contextMenu.unpinChat") : t("contextMenu.pinChat")}
          onClick={() => {
            onTogglePinChat(chat.id);
            onClose();
          }}
        />
        <MenuButton
          icon={<Pencil className="h-4 w-4 shrink-0" />}
          label={t("contextMenu.renameChat")}
          onClick={() => {
            onStartRenameChat(chat);
            onClose();
          }}
        />
        <MenuButton
          icon={<Link className="h-4 w-4 shrink-0" />}
          label={t("contextMenu.copyChatLink")}
          onClick={() => {
            onCopyChatLink(chat.id);
            onClose();
          }}
        />
        <MenuButton
          icon={<Bug className={cn("h-4 w-4 shrink-0", chat.isTestChat ? "text-primary" : "")} />}
          label={chat.isTestChat
            ? t("contextMenu.unmarkTestChat")
            : t("contextMenu.markTestChat")}
          onClick={() => {
            onToggleTestChat(chat.id);
            onClose();
          }}
        />
        <MenuButton
          destructive
          icon={<Trash2 className="h-4 w-4 shrink-0" />}
          label={t("contextMenu.permanentDelete")}
          onClick={() => {
            onDeleteChat(chat.id);
            onClose();
          }}
        />
      </div>
    </>
  );
}

function DeleteChatDialog({
  title,
  chatId,
  onCancel,
  onConfirm,
}: {
  title: string;
  chatId: string;
  onCancel: () => void;
  onConfirm: (chatId: string) => void;
}) {
  const { t } = useTranslation();
  const dialogRef = useRef<HTMLDialogElement>(null);
  useLayoutEffect(() => {
    const dialog = dialogRef.current!;
    dialog.showModal();
    return () => {
      dialog.close();
      // Right-click menu items are gone by the time the dialog opens.
      queueMicrotask(() => {
        if (document.activeElement === document.body) {
          document.querySelector<HTMLButtonElement>(`[data-vrcforge-chat-id="${CSS.escape(chatId)}"] button`)?.focus();
        }
      });
    };
  }, [chatId]);
  return (
    <dialog ref={dialogRef} aria-labelledby="delete-chat-title" aria-describedby="delete-chat-description"
      onCancel={(event) => { event.preventDefault(); onCancel(); }}
      className="m-auto w-[min(24rem,calc(100vw-3rem))] rounded-lg border border-border bg-card p-5 text-foreground shadow-panel backdrop:bg-black/35">
        <div className="flex min-w-0 items-center gap-2 text-destructive">
          <Trash2 className="h-4 w-4 shrink-0" />
          <h2 id="delete-chat-title" className="truncate text-base font-semibold">{t("deleteModal.title")}</h2>
        </div>
        <p id="delete-chat-description" className="mt-3 break-words text-sm text-muted-foreground">「{title}」将被永久删除，本地记录一并清除，无法恢复。</p>
        <div className="mt-5 flex justify-end gap-3">
          <Button type="button" variant="outline" onClick={onCancel}>
            {t("deleteModal.cancel")}
          </Button>
          <Button type="button" variant="danger" onClick={() => onConfirm(chatId)}>
            {t("contextMenu.permanentDelete")}
          </Button>
        </div>
    </dialog>
  );
}

function MenuScrim({ onClose }: { onClose: () => void }) {
  return (
    <div
      className="fixed inset-0 z-40"
      onClick={onClose}
      onContextMenu={(event) => {
        event.preventDefault();
        onClose();
      }}
    />
  );
}

function MenuButton({
  icon,
  label,
  destructive = false,
  onClick,
}: {
  icon: ReactNode;
  label: string;
  destructive?: boolean;
  onClick: () => void;
}) {
  return (
    <button
      type="button"
      role="menuitem"
      className={cn(
        "flex w-full items-center gap-2 rounded-md px-2.5 py-2 text-left text-sm transition-colors",
        destructive ? "text-destructive hover:bg-destructive/10" : "hover:bg-muted",
      )}
      onClick={onClick}
    >
      {icon}
      {label}
    </button>
  );
}
