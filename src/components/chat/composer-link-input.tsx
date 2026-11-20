import { Fragment, useContext, useEffect, useLayoutEffect, useMemo, useRef, useState, type ComponentPropsWithoutRef, type RefObject } from "react";
import { chatLinksInText, ChatLinkTitlesContext } from "../../lib/chat-deep-link";
import { ChatDeepLink } from "./chat-deep-link";

type ComposerInputProps = Omit<ComponentPropsWithoutRef<"textarea">, "value" | "onChange"> & {
  inputRef: RefObject<HTMLTextAreaElement>;
  value: string;
  suggestion?: string;
  onDismissSuggestion?: () => void;
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

const textLayout = "whitespace-pre-wrap break-words px-1 py-1 [overflow-wrap:anywhere]";

function caretAtEnd(input: HTMLTextAreaElement) {
  return input.selectionStart === input.value.length && input.selectionEnd === input.value.length;
}

export function ComposerLinkInput({ inputRef, value, className, suggestion, onDismissSuggestion, onChange, ...props }: ComposerInputProps) {
  const titles = useContext(ChatLinkTitlesContext);
  const { text, spans } = useMemo(() => projectDraft(value, titles), [value, titles]);
  const projected = spans.length > 0;
  const overlay = useRef<HTMLDivElement>(null);
  const suggestionOverlay = useRef<HTMLDivElement>(null);
  const [caretAtDraftEnd, setCaretAtDraftEnd] = useState(false);
  const [composing, setComposing] = useState(false);
  const composingRef = useRef(false);
  const suffix = !composing && (!text || caretAtDraftEnd) && suggestion?.startsWith(value)
    ? suggestion.slice(value.length)
    : "";
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
  const scrollOverlays = () => {
    const input = inputRef.current;
    if (!input) return;
    for (const layer of [overlay.current, suggestionOverlay.current]) {
      if (!layer) continue;
      layer.style.right = `${input.offsetWidth - input.clientWidth}px`;
      layer.scrollTop = input.scrollTop;
    }
  };
  useLayoutEffect(() => {
    const input = inputRef.current;
    if (!input) return;
    input.style.height = "28px";
    const contentHeight = input.scrollHeight;
    input.style.overflowY = contentHeight > 128 ? "auto" : "hidden";
    scrollOverlays();
    const previewHeight = suggestionOverlay.current?.scrollHeight ?? 0;
    input.style.height = `${Math.min(128, Math.max(28, contentHeight, previewHeight))}px`;
    if (pendingCaret.current !== null) {
      input.setSelectionRange(pendingCaret.current, pendingCaret.current);
      pendingCaret.current = null;
    }
    setCaretAtDraftEnd(input === document.activeElement && caretAtEnd(input));
    scrollOverlays();
  }, [value, text, suffix, className, inputRef]);
  return <div className={className}>
    <div className="relative">
      <textarea {...props} ref={inputRef} value={text}
        className={`app-scrollbar block min-h-7 w-full resize-none bg-transparent outline-none placeholder:text-muted-foreground ${textLayout} ${projected ? "text-transparent caret-foreground" : ""}`}
        placeholder={suffix ? undefined : props.placeholder}
        aria-label={props["aria-label"] ?? (suffix ? props.placeholder : undefined)}
        onScroll={scrollOverlays}
        onFocus={event => {
          setCaretAtDraftEnd(caretAtEnd(event.currentTarget));
          props.onFocus?.(event);
        }}
        onBlur={event => {
          setCaretAtDraftEnd(false);
          props.onBlur?.(event);
        }}
        onSelect={event => {
          setCaretAtDraftEnd(caretAtEnd(event.currentTarget));
          props.onSelect?.(event);
        }}
        onCompositionStart={event => {
          composingRef.current = true;
          setComposing(true);
          props.onCompositionStart?.(event);
        }}
        onCompositionEnd={event => {
          composingRef.current = false;
          setComposing(false);
          props.onCompositionEnd?.(event);
        }}
        onKeyDown={event => {
          if (composingRef.current || event.nativeEvent.isComposing || event.keyCode === 229) return;
          if (suffix && caretAtEnd(event.currentTarget)) {
            if (event.key === "Escape") {
              onDismissSuggestion?.();
              event.preventDefault();
              event.stopPropagation();
              return;
            }
            if (suggestion && event.key === "Tab" && !event.shiftKey && !event.ctrlKey && !event.metaKey && !event.altKey) {
              event.preventDefault();
              pendingCaret.current = projectDraft(suggestion, titles).text.length;
              onChange(suggestion);
              onDismissSuggestion?.();
              return;
            }
          }
          props.onKeyDown?.(event);
        }}
        onChange={event => {
          const next = event.currentTarget.value;
          const caret = event.currentTarget.selectionEnd;
          setCaretAtDraftEnd(caretAtEnd(event.currentTarget));
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
      {suffix && <div ref={suggestionOverlay} aria-hidden="true" className={`pointer-events-none absolute inset-0 select-none overflow-hidden ${textLayout}`}>
        <span className="invisible">{text}</span><wbr /><span data-composer-input-suggestion className="text-muted-foreground">{suffix}</span>
      </div>}
      {projected && <div ref={overlay} className={`pointer-events-none absolute inset-0 overflow-hidden ${textLayout}`}>
        {spans.map((link, index) => <Fragment key={link.start}>
          <span aria-hidden>{text.slice(spans[index - 1]?.end ?? 0, link.start)}</span>
          <ChatDeepLink className="pointer-events-auto" chatId={link.chatId} href={link.href}>{link.title}</ChatDeepLink>
        </Fragment>)}
        <span aria-hidden>{text.slice(spans.at(-1)?.end)}{text.endsWith("\n") && "\u200b"}</span>
      </div>}
    </div>
  </div>;
}
