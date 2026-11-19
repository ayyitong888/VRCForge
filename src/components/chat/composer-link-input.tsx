import { Fragment, useContext, useEffect, useLayoutEffect, useMemo, useRef, type ComponentPropsWithoutRef, type RefObject } from "react";
import { chatLinksInText, ChatLinkTitlesContext } from "../../lib/chat-deep-link";
import { ChatDeepLink } from "./chat-deep-link";

type ComposerInputProps = Omit<ComponentPropsWithoutRef<"textarea">, "value" | "onChange"> & {
  inputRef: RefObject<HTMLTextAreaElement>;
  value: string;
  ghost?: string;
  onChange: (value: string) => void;
};

function projectDraft(value: string, titles: ReadonlyMap<string, string> | null) {
  const links = titles ? chatLinksInText(value, titles) : [];
  let text = "";
  let sourceEnd = 0;
  const spans = links.map(link => {
    text += value.slice(sourceEnd, link.start);
    const start = text.length;
    text += link.title;
    sourceEnd = link.start + link.href.length;
    return { ...link, start, end: text.length };
  });
  text += value.slice(sourceEnd);
  return { text, spans };
}

function restoreLinks(text: string, spans: ReturnType<typeof projectDraft>["spans"], start: number, end: number): string {
  let result = "";
  let cursor = start;
  for (const link of spans) {
    if (link.start < start || link.end > end) continue;
    result += text.slice(cursor, link.start) + link.href;
    cursor = link.end;
  }
  return result + text.slice(cursor, end);
}

export function ComposerLinkInput({ inputRef, value, className, ghost, onChange, ...props }: ComposerInputProps) {
  const titles = useContext(ChatLinkTitlesContext);
  const { text, spans } = useMemo(() => projectDraft(value, titles), [value, titles]);
  const projected = spans.length > 0;
  const overlay = useRef<HTMLDivElement>(null);
  const pendingCaret = useRef<number | null>(null);
  const editSelection = useRef<{ start: number; end: number } | null>(null);
  useEffect(() => {
    if (!projected) return;
    const input = inputRef.current;
    if (!input) return;
    const rememberSelection = (event: InputEvent) => {
      editSelection.current = !event.isComposing && input.selectionStart !== input.selectionEnd
        ? { start: input.selectionStart, end: input.selectionEnd }
        : null;
    };
    input.addEventListener("beforeinput", rememberSelection);
    return () => input.removeEventListener("beforeinput", rememberSelection);
  }, [projected, inputRef]);
  const scrollOverlay = () => {
    const input = inputRef.current;
    if (!input || !overlay.current) return;
    overlay.current.style.right = `${input.offsetWidth - input.clientWidth}px`;
    overlay.current.scrollTop = input.scrollTop;
  };
  useLayoutEffect(() => {
    const input = inputRef.current;
    if (!input) return;
    input.style.height = "28px";
    const contentHeight = input.scrollHeight;
    input.style.height = `${Math.min(128, Math.max(28, contentHeight))}px`;
    input.style.overflowY = contentHeight > 128 ? "auto" : "hidden";
    scrollOverlay();
    if (pendingCaret.current !== null) {
      input.setSelectionRange(pendingCaret.current, pendingCaret.current);
      pendingCaret.current = null;
    }
  }, [value, text, className, inputRef]);
  return <div className={className}>
    <div className={ghost ? "relative grid" : "relative"}>
      <textarea {...props} ref={inputRef} value={text}
        className={`app-scrollbar block min-h-7 w-full resize-none whitespace-pre-wrap break-words bg-transparent px-1 py-1 outline-none [overflow-wrap:anywhere] placeholder:text-muted-foreground ${projected ? "text-transparent caret-foreground" : ""} ${ghost ? "z-10 col-start-1 row-start-1" : ""}`}
        onScroll={scrollOverlay}
        onChange={event => {
          const next = event.currentTarget.value;
          const caret = event.currentTarget.selectionEnd;
          const selection = editSelection.current;
          editSelection.current = null;
          if (!projected) {
            const nextDisplay = projectDraft(next, titles);
            if (nextDisplay.spans.length) {
              pendingCaret.current = projectDraft(next.slice(0, caret), titles).text.length;
            }
            onChange(next);
            return;
          }
          // Use the selected range for replacements whose text happens to match
          // an existing title. Otherwise compare outward from the native caret.
          let start = 0;
          const prefixEnd = selection?.start ?? caret;
          while (start < prefixEnd && start < text.length && text[start] === next[start]) start++;
          let end = text.length;
          let nextEnd = next.length;
          while (end > (selection?.end ?? start) && nextEnd > caret && text[end - 1] === next[nextEnd - 1]) {
            end--;
            nextEnd--;
          }
          const prefix = restoreLinks(text, spans, 0, start);
          const inserted = next.slice(start, nextEnd);
          const raw = prefix + inserted + restoreLinks(text, spans, end, text.length);
          const nextCaret = projectDraft(prefix + next.slice(start, caret), titles).text.length;
          if (projectDraft(raw, titles).text !== next) pendingCaret.current = nextCaret;
          onChange(raw);
        }}
        onCopy={event => {
          const { selectionStart, selectionEnd } = event.currentTarget;
          if (!spans.some(link => link.start >= selectionStart && link.end <= selectionEnd)) return;
          event.clipboardData.setData("text/plain", restoreLinks(text, spans, selectionStart, selectionEnd));
          event.preventDefault();
        }}
        onCut={event => {
          const { selectionStart, selectionEnd } = event.currentTarget;
          if (!spans.some(link => link.start >= selectionStart && link.end <= selectionEnd)) return;
          event.clipboardData.setData("text/plain", restoreLinks(text, spans, selectionStart, selectionEnd));
          event.preventDefault();
          pendingCaret.current = selectionStart;
          onChange(restoreLinks(text, spans, 0, selectionStart) + restoreLinks(text, spans, selectionEnd, text.length));
        }}
      />
      {ghost && <div aria-hidden="true" data-composer-input-suggestion className="pointer-events-none col-start-1 row-start-1 min-w-0 select-none whitespace-pre-wrap break-words px-1 py-1 text-muted-foreground [overflow-wrap:anywhere]">{ghost}</div>}
      {projected && <div ref={overlay} className="pointer-events-none absolute inset-0 overflow-hidden whitespace-pre-wrap break-words px-1 py-1 [overflow-wrap:anywhere]">
        {spans.map((link, index) => <Fragment key={link.start}>
          <span aria-hidden>{text.slice(spans[index - 1]?.end ?? 0, link.start)}</span>
          <ChatDeepLink className="pointer-events-auto" chatId={link.chatId} href={link.href}>{link.title}</ChatDeepLink>
        </Fragment>)}
        <span aria-hidden>{text.slice(spans.at(-1)?.end)}{text.endsWith("\n") && "\u200b"}</span>
      </div>}
    </div>
  </div>;
}
