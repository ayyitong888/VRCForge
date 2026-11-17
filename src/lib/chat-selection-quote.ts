export type ConversationQuoteSelection = {
  text: string;
  x: number;
  y: number;
};

export function dismissConversationQuoteOnOutsidePointerDown(menu: HTMLElement, dismiss: () => void): () => void {
  const handlePointerDown = (event: PointerEvent) => {
    if (event.target instanceof Node && !menu.contains(event.target)) dismiss();
  };
  const ownerDocument = menu.ownerDocument;
  ownerDocument.addEventListener("pointerdown", handlePointerDown, true);
  return () => ownerDocument.removeEventListener("pointerdown", handlePointerDown, true);
}

function quoteContentRoot(node: Node | null): Element | null {
  const element = node instanceof Element ? node : node?.parentElement;
  if (!element) return null;
  const root = element.closest("[data-chat-quote-content]");
  if (!root || element.closest([
    "[data-chat-current-activity]",
    "[data-agent-turn-metadata]",
    "[data-agent-tool-call-group]",
    "[data-agent-timeline-invocation]",
    "[data-vrcforge-terminal-status]",
    "[data-vrcforge-timeline-event]",
    "[data-chat-composer-dock]",
    "[data-chat-selection-excluded]",
    "button",
    "[role='button']",
    "[aria-hidden='true']",
  ].join(","))) return null;
  return root;
}

/** Accept a quote only when its full selection stays inside one real message body. */
export function conversationQuoteFromSelection(selection: Selection | null): ConversationQuoteSelection | null {
  if (!selection || selection.isCollapsed || selection.rangeCount !== 1) return null;
  const range = selection.getRangeAt(0);
  const root = quoteContentRoot(range.startContainer);
  if (!root || root !== quoteContentRoot(range.endContainer)) return null;
  const text = selection.toString().trim();
  if (!text) return null;
  const rect = range.getBoundingClientRect();
  return { text, x: rect.left + rect.width / 2, y: rect.top };
}
