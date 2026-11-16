import { useCallback, useEffect, useLayoutEffect, useRef, useState, type RefObject } from "react";

type ReadingPosition = { top: number; pinned: boolean };

/** Follow a growing transcript only while its reader is at the bottom. */
export function useConversationScroll({ chatKey, endRef, loadOlder }: {
  chatKey: string;
  endRef: RefObject<HTMLDivElement>;
  loadOlder?: () => Promise<boolean>;
}) {
  const positions = useRef(new Map<string, ReadingPosition>());
  const active = useRef<{ key: string; node: HTMLDivElement; pinned: boolean }>();
  const observer = useRef<ResizeObserver>();
  const [pinned, setPinned] = useState(true);
  const prepend = useRef<{ node: HTMLDivElement; anchor?: Element; top: number }>();

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
    } else if (prepend.current?.node === node && prepend.current.anchor?.isConnected) {
      const { anchor, top } = prepend.current;
      const distance = anchor.getBoundingClientRect().top - top;
      if (distance !== 0) {
        node.scrollTop += distance;
        prepend.current = undefined;
      }
    } else if (active.current?.pinned) {
      node.scrollTop = node.scrollHeight;
    }
  });

  useEffect(() => () => {
    observer.current?.disconnect();
    active.current = undefined;
  }, []);

  const onScroll = useCallback((node: HTMLDivElement, requestOlder = false) => {
    const current = active.current;
    if (!current || current.key !== chatKey || current.node !== node) return;
    const previousTop = positions.current.get(chatKey)?.top ?? node.scrollHeight;
    current.pinned = !requestOlder && node.scrollHeight - node.scrollTop - node.clientHeight < 24;
    positions.current.set(chatKey, { top: node.scrollTop, pinned: current.pinned });
    setPinned(current.pinned);
    if (!loadOlder || current.pinned || node.scrollTop > 72 || (!requestOlder && node.scrollTop >= previousTop) || prepend.current) return;
    const anchor = [...node.querySelectorAll("[data-conversation-item-id]")].find((item) =>
      item.getBoundingClientRect().bottom >= node.getBoundingClientRect().top);
    const position = { node, anchor, top: anchor?.getBoundingClientRect().top ?? 0 };
    prepend.current = position;
    void loadOlder().finally(() => {
      requestAnimationFrame(() => {
        if (prepend.current === position) prepend.current = undefined;
      });
    });
  }, [chatKey, loadOlder]);

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
