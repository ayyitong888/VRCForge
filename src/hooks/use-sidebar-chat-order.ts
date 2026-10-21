import { useCallback, useEffect, useMemo, useState } from "react";
import type { SidebarChatGroupsWithTimes } from "../lib/sidebar-view";
import { SIDEBAR_CHAT_ORDER_KEY, loadSidebarChatOrder, mergeSidebarChatOrder, orderSidebarChats } from "../lib/sidebar-chat-order";

export function useSidebarChatOrder(groups: SidebarChatGroupsWithTimes) {
  const [order, setOrder] = useState(loadSidebarChatOrder);
  useEffect(() => {
    try {
      window.localStorage.setItem(SIDEBAR_CHAT_ORDER_KEY, JSON.stringify(order));
    } catch {
      // Match other local display preferences: a blocked store does not stop navigation.
    }
  }, [order]);
  const orderedGroups = useMemo(() => ({
    ...groups,
    temporaryChats: orderSidebarChats(groups.temporaryChats, order),
    testChats: orderSidebarChats(groups.testChats, order),
    projectChatsByPath: new Map([...groups.projectChatsByPath].map(([path, chats]) => [path, orderSidebarChats(chats, order)])),
  }), [groups, order]);
  const reorderChats = useCallback((ids: string[]) => {
    setOrder((previous) => mergeSidebarChatOrder(previous, ids));
  }, []);
  const resetChatOrder = useCallback((ids: string[]) => {
    const removed = new Set(ids);
    setOrder((previous) => previous.filter((id) => !removed.has(id)));
  }, []);
  const manualChatIds = useMemo(() => new Set(order), [order]);
  return { orderedGroups, reorderChats, resetChatOrder, manualChatIds };
}
