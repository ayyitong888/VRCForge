import type { ChatThread } from "./chat-types";
import type { ProjectType } from "./chat-types";
import { normalizeProjectPathKey, projectKey } from "./project-path";

type SidebarProjectItem = {
  name?: string;
  path?: string;
  projectType?: ProjectType;
};

export type SidebarProjectGroups<TProject extends SidebarProjectItem> = {
  general: TProject[];
  unity: TProject[];
};

/** Splits the sidebar list without changing incoming order within either type. */
export function groupSidebarProjects<TProject extends SidebarProjectItem>(projects: readonly TProject[]): SidebarProjectGroups<TProject> {
  const groups: SidebarProjectGroups<TProject> = { general: [], unity: [] };
  for (const project of projects) {
    // Legacy catalogue entries have no type and retain the historical Unity binding.
    (project.projectType === "general" ? groups.general : groups.unity).push(project);
  }
  return groups;
}

type IndexedProject<TProject extends SidebarProjectItem> = {
  project: TProject;
  index: number;
  pinned: boolean;
  latestChatTime: number | null;
};

/**
 * Orders the sidebar-only project view without mutating project state.
 *
 * Pinned projects form the first group. Within each group, the most recently
 * updated project conversation wins; a missing updatedAt falls back to
 * createdAt. Projects without a usable conversation timestamp retain their
 * incoming order.
 */
export function sortSidebarProjects<TProject extends SidebarProjectItem>(
  projects: readonly TProject[],
  chats: readonly ChatThread[],
  pinnedProjectSet: ReadonlySet<string>,
): TProject[] {
  const latestChatTimeByProject = latestChatTimeByProjectPath(chats);
  const pinnedProjectKeys = new Set(Array.from(pinnedProjectSet, normalizeProjectPathKey));
  return projects
    .map((project, index): IndexedProject<TProject> => {
      const key = normalizeProjectPathKey(projectKey(project));
      return {
        project,
        index,
        pinned: pinnedProjectKeys.has(key),
        latestChatTime: key ? latestChatTimeByProject.get(key) ?? null : null,
      };
    })
    .sort(compareSidebarProjects)
    .map(({ project }) => project);
}

function latestChatTimeByProjectPath(chats: readonly ChatThread[]): Map<string, number> {
  const latestByProject = new Map<string, number>();
  for (const chat of chats) {
    const key = normalizeProjectPathKey(chat.projectPath);
    const timestamp = chatSidebarTimestamp(chat);
    if (!key || timestamp === null || timestamp <= (latestByProject.get(key) ?? Number.NEGATIVE_INFINITY)) {
      continue;
    }
    latestByProject.set(key, timestamp);
  }
  return latestByProject;
}

function chatSidebarTimestamp(chat: Pick<ChatThread, "updatedAt" | "createdAt">): number | null {
  for (const value of [chat.updatedAt, chat.createdAt]) {
    const timestamp = Date.parse(value || "");
    if (Number.isFinite(timestamp)) {
      return timestamp;
    }
  }
  return null;
}

function compareSidebarProjects<TProject extends SidebarProjectItem>(
  left: IndexedProject<TProject>,
  right: IndexedProject<TProject>,
): number {
  const pinDelta = Number(right.pinned) - Number(left.pinned);
  if (pinDelta !== 0) {
    return pinDelta;
  }
  if (left.latestChatTime !== null && right.latestChatTime !== null && left.latestChatTime !== right.latestChatTime) {
    return right.latestChatTime - left.latestChatTime;
  }
  if (left.latestChatTime !== null && right.latestChatTime === null) {
    return -1;
  }
  if (left.latestChatTime === null && right.latestChatTime !== null) {
    return 1;
  }
  return left.index - right.index;
}
