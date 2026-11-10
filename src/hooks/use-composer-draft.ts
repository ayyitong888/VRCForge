import { useLayoutEffect, useReducer, useRef, type SetStateAction } from "react";
import type { ChatAttachment } from "../lib/chat-types";
import { normalizeProjectPathKey } from "../lib/project-path";

type ComposerDraft = { input: string; attachments: ChatAttachment[] };
const PREFIX = "vrcforge_composer_draft:";
const chatScope = (chatId: string) => `chat:${chatId}`;

function readDraftText(scope: string) {
  try {
    return window.localStorage.getItem(PREFIX + scope) ?? "";
  } catch {
    return "";
  }
}

function writeDraftText(scope: string, input: string) {
  try {
    if (input) window.localStorage.setItem(PREFIX + scope, input);
    else window.localStorage.removeItem(PREFIX + scope);
    return true;
  } catch {
    // Keep the input usable in this window if local storage is unavailable.
    return false;
  }
}

export function useComposerDraft(chatId: string, projectPath: string, chatStarted: boolean) {
  const scope = chatId && chatStarted ? chatScope(chatId) : `new:${normalizeProjectPathKey(projectPath)}`;
  const drafts = useRef(new Map<string, ComposerDraft>());
  const submissions = useRef(new Map<string, symbol>());
  const lastScope = useRef({ chatId, scope });
  const [, render] = useReducer((revision: number) => revision + 1, 0);

  function read(scopeKey: string): ComposerDraft {
    const existing = drafts.current.get(scopeKey);
    if (existing) return existing;
    const draft = { input: readDraftText(scopeKey), attachments: [] };
    drafts.current.set(scopeKey, draft);
    return draft;
  }

  function update(scopeKey: string, draft: ComposerDraft) {
    drafts.current.set(scopeKey, draft);
    render();
  }

  useLayoutEffect(() => {
    const previous = lastScope.current;
    lastScope.current = { chatId, scope };
    if (!chatId || previous.chatId !== chatId || previous.scope === scope
      || !previous.scope.startsWith("new:") || !chatStarted) return;
    const pending = read(previous.scope);
    const target = read(scope);
    if ((!pending.input && !pending.attachments.length) || target.input || target.attachments.length) return;
    // Move text entered while this same new chat was being started.
    if (writeDraftText(scope, pending.input) && readDraftText(previous.scope) === pending.input) {
      writeDraftText(previous.scope, "");
    }
    drafts.current.set(previous.scope, { input: "", attachments: [] });
    update(scope, pending);
  }, [chatId, chatStarted, scope]);

  function setInput(value: SetStateAction<string>) {
    const draft = read(scope);
    const input = typeof value === "function" ? value(draft.input) : value;
    if (draft.input === input) return;
    submissions.current.delete(scope);
    writeDraftText(scope, input);
    update(scope, { ...draft, input });
  }

  function setAttachments(value: SetStateAction<ChatAttachment[]>) {
    const draft = read(scope);
    const attachments = typeof value === "function" ? value(draft.attachments) : value;
    update(scope, { ...draft, attachments });
  }

  function beginSubmission() {
    const submitted = read(scope);
    const submission = Symbol();
    submissions.current.set(scope, submission);
    update(scope, { input: "", attachments: [] });

    // The input stays on disk until the runtime accepts it. The in-memory
    // placeholder leaves the composer ready for the next message.
    return (accepted: boolean, recoveredChatId?: string) => {
      if (submissions.current.get(scope) !== submission) return;
      submissions.current.delete(scope);
      if (accepted) {
        if (readDraftText(scope) === submitted.input) writeDraftText(scope, "");
        return;
      }
      const recoveryScope = recoveredChatId ? chatScope(recoveredChatId) : scope;
      const recovery = read(recoveryScope);
      if (recovery.input) return;
      if (recoveryScope !== scope) {
        if (writeDraftText(recoveryScope, submitted.input) && readDraftText(scope) === submitted.input) {
          writeDraftText(scope, "");
        }
      }
      update(recoveryScope, { ...submitted, attachments: recovery.attachments.length ? recovery.attachments : submitted.attachments });
    };
  }

  return { ...read(scope), setInput, setAttachments, beginSubmission };
}
