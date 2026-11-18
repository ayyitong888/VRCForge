import { useContext, type ComponentPropsWithoutRef, type MouseEvent } from "react";
import { CHAT_LINK_CLASS, ChatLinkTitlesContext, OpenChatLinkContext } from "../../lib/chat-deep-link";
import { cn } from "../../lib/utils";

export function ChatDeepLink({ chatId, children, className, href, title }: ComponentPropsWithoutRef<"a"> & { chatId: string }) {
  const open = useContext(OpenChatLinkContext);
  const label = useContext(ChatLinkTitlesContext)?.get(chatId) || children;
  if (!open) return <span>{label}</span>;
  const openLink = (event: MouseEvent<HTMLAnchorElement>) => {
    event.preventDefault();
    const selection = window.getSelection();
    if (selection && !selection.isCollapsed && selection.containsNode(event.currentTarget, true)) return;
    open(chatId);
  };
  return <a className={cn(CHAT_LINK_CLASS, className)} href={href} title={title} onClick={openLink}
    onAuxClick={event => { if (event.button === 1) openLink(event); }}>{label}</a>;
}
