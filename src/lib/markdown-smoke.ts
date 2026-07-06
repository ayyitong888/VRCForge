import type { AgentGoal, AgentMemory, AgentRuntimeResponse, SubAgentTask } from "./api";
import type { ChatThread } from "./chat-types";

export function isMarkdownSmokeMode(): boolean {
  try {
    return new URLSearchParams(window.location.search).get("markdownSmoke") === "1";
  } catch {
    return false;
  }
}

function markdownSmokeCase(): string {
  try {
    return new URLSearchParams(window.location.search).get("case") || "";
  } catch {
    return "";
  }
}

function isSubAgentContextSmokeMode(): boolean {
  return isMarkdownSmokeMode() && markdownSmokeCase() === "subagent-context";
}

function isContextMeterSmokeMode(): boolean {
  return isMarkdownSmokeMode() && markdownSmokeCase() === "context-meter";
}

function markdownSmokeContextPercent(): number {
  try {
    const raw = Number(new URLSearchParams(window.location.search).get("contextPct") || "1");
    if (!Number.isFinite(raw)) {
      return 1;
    }
    return Math.max(0, Math.min(100, Math.round(raw)));
  } catch {
    return 1;
  }
}

const MARKDOWN_SMOKE_TEXT = [
  "# Markdown Smoke H1",
  "## Markdown Smoke H2",
  "### Markdown Smoke H3",
  "",
  "Paragraph with **bold text**, *italic text*, ***bold italic text***, ~~deleted text~~, `inline code`, escaped \\*asterisks\\*, and a hard break  ",
  "after two trailing spaces.",
  "",
  "Autolink literal: https://example.com and explicit [safe link](https://example.com/docs).",
  "",
  "> Blockquote with **formatting**.",
  ">",
  "> - Quote list item",
  "",
  "1. Ordered item",
  "2. Nested item",
  "   - Nested bullet",
  "   - Another nested bullet with `code`",
  "",
  "- [x] Completed task",
  "- [ ] Open task",
  "",
  "| Feature | Status | Notes |",
  "| --- | :---: | ---: |",
  "| Tables | **Rendered** | 1 |",
  "| HTML | <mark>sanitized</mark> | 2 |",
  "",
  "```ts",
  "const rendered: boolean = true;",
  "console.log(rendered);",
  "```",
  "",
  "![Markdown image](data:image/png;base64,iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAYAAAAfFcSJAAAADUlEQVR42mP8z8BQDwAFgwJ/lUXYkwAAAABJRU5ErkJggg==)",
  "",
  "Footnote reference[^1].",
  "",
  "[^1]: Footnote body rendered by GFM.",
  "",
  "<details><summary>Safe HTML summary</summary><kbd>Ctrl</kbd> + <kbd>K</kbd></details>",
  "",
  "<script>window.__vrcforgeMarkdownUnsafe = true</script>",
  "<img src=x onerror=\"window.__vrcforgeMarkdownUnsafe = true\" />",
].join("\n");

export function createMarkdownSmokeChatState(): { chats: ChatThread[]; activeChatId: string } {
  if (!isMarkdownSmokeMode()) {
    return { chats: [], activeChatId: "" };
  }
  const chatId = "markdown-smoke-chat";
  const contextPercent = isContextMeterSmokeMode() ? markdownSmokeContextPercent() : 1;
  const smokeInputTokens = Math.round(1_048_576 * (contextPercent / 100));
  const response: AgentRuntimeResponse = {
    ok: true,
    session_id: "markdown-smoke-session",
    sessionId: "markdown-smoke-session",
    turn_id: "markdown-smoke-turn",
    turnId: "markdown-smoke-turn",
    observe: {},
    plan: {
      summary: MARKDOWN_SMOKE_TEXT,
      reply: MARKDOWN_SMOKE_TEXT,
      planner: "markdown-smoke",
      plannerLabel: "Markdown Smoke",
      shellNeeded: false,
      nextStep: "done",
    },
    contextUsage: {
      schema: "vrcforge.context_usage.v1",
      source: "provider_usage",
      exact: true,
      provider: "smoke",
      providerLabel: "Smoke",
      model: "CommonMark + GFM",
      inputTokens: smokeInputTokens,
      outputTokens: 321,
      totalTokens: smokeInputTokens + 321,
      requestCount: 1,
      sentHistoryEntryCount: 1,
      promptCharacterCount: 18000,
    },
  };
  return {
    activeChatId: chatId,
    chats: [
      {
        id: chatId,
        sessionId: "markdown-smoke-session",
        title: "Markdown smoke",
        projectPath: "",
        items: [
          {
            id: "markdown-smoke-user",
            type: "user",
            text: MARKDOWN_SMOKE_TEXT,
          },
          {
            id: "markdown-smoke-agent",
            type: "agent",
            response,
            providerLabel: "Smoke",
            model: "CommonMark + GFM",
          },
        ],
      },
    ],
  };
}

export function createSubAgentContextSmokeTask(): SubAgentTask | null {
  if (!isSubAgentContextSmokeMode()) {
    return null;
  }
  return {
    id: "subagent-context-smoke",
    role: "selected_context_review",
    displayName: "New session question",
    task: "Review the selected conversation excerpt in a scoped sub-agent thread.",
    parentSessionId: "markdown-smoke-session",
    projectPath: "",
    toolProfile: "read-only",
    status: "completed",
    createdAt: "2026-07-03T00:00:00Z",
    startedAt: "2026-07-03T00:00:01Z",
    stoppedAt: "2026-07-03T00:00:02Z",
    updatedAt: "2026-07-03T00:00:02Z",
    summary: "Selected context opened in a sub-agent thread: 1462 character(s).",
    result: {
      ok: true,
      schema: "vrcforge.sub_agent.selected_context_review.v1",
      role: "selected_context_review",
      readOnly: true,
      summaryText: "Selected context opened in a sub-agent thread: 1462 character(s).",
      selectedTextPreview: "Tool sync and async execution notes selected from the transcript.",
      selectedTextCharacters: 1462,
    },
    paramsSummary: {
      source: "selection-menu",
      selectedTextCharacters: 1462,
    },
    eventCount: 2,
  };
}

export function markdownSmokeAgentNotes(): string {
  if (!isSubAgentContextSmokeMode()) {
    return "";
  }
  return [
    "Project instruction notes:",
    "- Review project status before changing files.",
    "- Keep user-facing changes scoped to the requested workflow.",
    "- Preserve approval, checkpoint, validation, and rollback boundaries for writes.",
    "- Prefer existing app APIs instead of bypassing the runtime.",
    "- Keep heavyweight diagnostics outside first-screen startup.",
  ].join("\n");
}

export function markdownSmokeGoals(): AgentGoal[] {
  if (!isSubAgentContextSmokeMode()) {
    return [];
  }
  return [
    {
      goalId: "goal-context-smoke",
      title: "Route selected text into a sub-agent workspace",
      summary: "The right workspace owns the scoped follow-up thread and can close or reopen the detail panel.",
      status: "active",
      sessionId: "markdown-smoke-session",
    },
  ];
}

export function markdownSmokeMemories(): AgentMemory[] {
  if (!isSubAgentContextSmokeMode()) {
    return [];
  }
  return [
    {
      memoryId: "memory-context-smoke",
      scope: "project",
      kind: "constraint",
      text: "Context usage must include real prompt inputs: recent chat, current draft, attachments, project instructions, goals, and memories.",
      status: "active",
    },
  ];
}
