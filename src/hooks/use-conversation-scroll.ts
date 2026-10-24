import { useCallback, useEffect, useLayoutEffect, useRef, useState, type RefObject } from "react";

type ReadingPosition = { top: number; pinned: boolean };

/** Follow a growing transcript only while its reader is at the bottom. */
export function useConversationScroll({ chatKey, endRef }: {
  chatKey: string;
  endRef: RefObject<HTMLDivElement>;
}) {
  const positions = useRef(new Map<string, ReadingPosition>());
  const active = useRef<{ key: string; node: HTMLDivElement; pinned: boolean }>();
  const observer = useRef<ResizeObserver>();
  const [pinned, setPinned] = useState(true);

  // A layout pass covers text/timeline replacement within an existing item.
  // ResizeObserver also covers images, expanded details and viewport resizing.
  useLayoutEffect(() => {
    const node = endRef.current?.closest<HTMLDivElement>("[data-chat-history-scroll]");
    if (!node) {
      observer.current?.disconnect();
      active.current = undefined;
      return;
    }
    const changed = active.current?.key !== chatKey || active.current.node !== node;
    if (changed) {
      observer.current?.disconnect();
      const saved = positions.current.get(chatKey);
      const following = saved?.pinned ?? true;
      active.current = { key: chatKey, node, pinned: following };
      node.scrollTop = following ? node.scrollHeight : saved?.top ?? 0;
      setPinned(following);
      observer.current = new ResizeObserver(() => {
        if (active.current?.node === node && active.current.pinned) node.scrollTop = node.scrollHeight;
      });
      observer.current.observe(node);
      if (endRef.current?.parentElement) observer.current.observe(endRef.current.parentElement);
    } else if (active.current?.pinned) {
      node.scrollTop = node.scrollHeight;
    }
  });

  useEffect(() => () => {
    observer.current?.disconnect();
    active.current = undefined;
  }, []);

  const onScroll = useCallback((node: HTMLDivElement) => {
    const current = active.current;
    if (!current || current.key !== chatKey || current.node !== node) return;
    current.pinned = node.scrollHeight - node.scrollTop - node.clientHeight < 24;
    positions.current.set(chatKey, { top: node.scrollTop, pinned: current.pinned });
    setPinned(current.pinned);
  }, [chatKey]);

  const scrollToBottom = useCallback(() => {
    const current = active.current;
    if (!current || current.key !== chatKey) return;
    current.pinned = true;
    current.node.scrollTop = current.node.scrollHeight;
    positions.current.set(chatKey, { top: current.node.scrollTop, pinned: true });
    setPinned(true);
  }, [chatKey]);

  return { pinned, onScroll, scrollToBottom };
}
