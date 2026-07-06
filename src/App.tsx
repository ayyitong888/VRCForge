import { invoke } from "@tauri-apps/api/core";
import { listen } from "@tauri-apps/api/event";
import {
  AlertTriangle,
  Camera,
  Check,
  Copy,
  FileText,
  GitBranch,
  ListChecks,
  Loader2,
  MoreHorizontal,
  Monitor,
  Moon,
  MousePointer2,
  PanelRightClose,
  PanelRightOpen,
  Paperclip,
  RotateCcw,
  Search,
  Send,
  Square,
  Sun,
  ThumbsDown,
  ThumbsUp,
  X,
} from "lucide-react";
import { useTranslation } from "react-i18next";
import i18n, { setLocale } from "./i18n";
import {
  FormEvent,
  PointerEvent as ReactPointerEvent,
  useEffect,
  useLayoutEffect,
  useMemo,
  useRef,
  useState,
} from "react";
import { Badge } from "./components/ui/badge";
import { Button } from "./components/ui/button";
import { ConversationCard } from "./components/chat/conversation-card";
import { DoctorWorkspace } from "./components/doctor/doctor-workspace";
import { OptimizationWorkspace, buildOptimizationRequestOptions, type OptimizationActionOptions } from "./components/optimization/optimization-workspace";
import { ProtectionWorkspace, protectionPlanPayload } from "./components/protection/protection-workspace";
import { RightRuntimeSidebar } from "./components/runtime/runtime-sidebar";
import { RuntimeToolButton } from "./components/runtime/runtime-sidebar-ui";
import { AttachmentStrip, Composer } from "./components/chat/composer";
import { CheckpointWorkspace, type AdjustmentCheckpointPreview } from "./components/checkpoints/checkpoint-workspace";
import { SettingsWorkspace } from "./components/settings/settings-workspace";
import { AppSidebar } from "./components/sidebar/app-sidebar";
import { SidebarMenus } from "./components/sidebar/sidebar-menus";
import { OnboardingOverlay } from "./components/onboarding/onboarding-overlay";
import { OutfitImportPanel } from "./components/project/outfit-import-panel";
import { ProjectIndexPanel } from "./components/project/project-index-panel";
import { ProjectPickerModal } from "./components/project/project-picker-modal";
import { SkillsWorkspace } from "./components/skills/skills-workspace";
import { SubAgentPanel } from "./components/subagents/sub-agent-panel";
import { DataLine } from "./components/ui/data-line";
import { TEMP_CHATS_COLLAPSE_KEY, type ActiveView } from "./lib/app-view";
import type { AgentRuntimeDeltaEvent } from "./lib/chat-streaming";
import { formatConnectorActionMessage } from "./lib/connector-ui";
import {
  appendAttachmentSummary,
  buildChatHistory,
  buildCompactSummary,
  buildContextUsageFromRuntime,
  cloneChatAttachments,
  conversationItemText,
  findProviderModelInfo,
  formatPayload,
  isRetryableConversationItem,
  latestAgentContextUsage,
  latestConversationItemId,
  normalizeProviderForContext,
  readChatAttachment,
  selectedTextAttachment,
  serializeChatAttachments,
  textContextAttachment,
} from "./lib/conversation-utils";
import {
  defaultBaseUrlForProvider,
  defaultModelForProvider,
  providerDisplayName,
  providerNeedsApiKey,
  thinkingStatusForModelLabel,
  thinkingTraceLabel,
} from "./lib/provider-ui";
import { cacheChatTimestampsFast, formatChatSidebarTime, groupSidebarChats, isStoredChat } from "./lib/chat-thread";
import type { ApprovalActionState, ChatAttachment, ChatThread, ComposerAction, ComposerActionId, ContextUsage, ConversationItem, MessageFeedback } from "./lib/chat-types";
import { executionModeLabel, permissionVisualState } from "./lib/permission-ui";
import { normalizeProjectPathKey, projectKey, shortPath } from "./lib/project-path";
import { buildRuntimeFileReferences } from "./lib/runtime-file-references";
import { approvalIdFromResponse, asRecord, getHealthDetailNumber, isAgentShellResult } from "./lib/runtime-parsing";
import { emptySkillDraft } from "./lib/skill-draft";
import type { RuntimeFileReference, RuntimeReviewEvidence, RuntimeScheduleItem } from "./lib/runtime-ui-types";
import { displaySubAgentStatus, subAgentRoleLabel, subAgentStatusTone } from "./lib/subagent-ui";
import {
  AgentApproval,
  AdjustmentCheckpoint,
  AgentCheckpoint,
  AgentCheckpointPreview,
  AgentDesktopAction,
  AgentGoal,
  AgentMemory,
  AgentRuntimeRun,
  AgentRuntimeResponse,
  AgentReasoningTrace,
  AgentSkill,
  AgentSkillRegistry,
  AvatarListItem,
  AvatarEncryptionPlanResult,
  InterruptedApplyRecovery,
  InterruptedApplyRecoveryPreview,
  SubAgentTask,
  SubAgentTaskList,
  ApiError,
  AppBootstrap,
  DoctorReport,
  DiagnosticsStatus,
  ExternalAgentConnectorClient,
  ExternalAgentConnectorStatus,
  OutfitImportPlanResult,
  OptimizationPlannerReport,
  OptimizationProofDetail,
  OptimizationProofSummary,
  ProviderModelInfo,
  ProjectIndexScanResult,
  SkillPackageEntry,
  SkillPackagePreflight,
  WorkspaceDiffSummary,
  approveAgentApproval,
  checkSkills,
  compactAgentHistory,
  cancelSubAgent,
  createAgentGoal,
  createAgentMemory,
  createSubAgent,
  createSkill,
  deleteSkill,
  exportSupportBundle,
  exportSkillPackage,
  fetchAdjustmentCheckpoints,
  fetchCheckpoints,
  applyAdjustmentCheckpoint,
  createAdjustmentCheckpoint,
  deleteAdjustmentCheckpoint,
  fetchBootstrap,
  fetchWorkspaceDiff,
  fetchDiagnostics,
  fetchDoctor,
  fetchDesktopRuntimeSnapshot,
  fetchExternalAgentConnectors,
  fetchInterruptedApplyRecoveries,
  fetchOptimizationPlan,
  fetchOptimizationProof,
  fetchOptimizationProofs,
  fetchSkillPackages,
  fetchSkills,
  AgentSkillCheck,
  ExecutionMode,
  PermissionState,
  blockSkillPackage,
  fetchAgentNotes,
  fetchAgentDesktopActions,
  fetchAgentGoals,
  fetchAgentMemory,
  fetchAgentApprovals,
  fetchAgentRuns,
  fetchAppSession,
  fetchAppHealth,
  fetchAvatars,
  fetchChats,
  fetchProjectPrefs,
  fetchProviderModels,
  fetchSubAgent,
  fetchSubAgents,
  installExternalAgentConnector,
  importSkillPackage,
  planOutfitImport,
  planAvatarEncryption,
  preflightSkillPackage,
  ProjectPrefs,
  previewRestoreCheckpoint,
  previewInterruptedApplyRecovery,
  previewAdjustmentCheckpoint,
  rejectAgentApproval,
  recordAgentRunQueued,
  requestAgentDesktopAction,
  requestAgentRunCancel,
  requestApprovalRevision,
  requestOptimizationApply,
  requestAvatarEncryptionApply,
  requestRestoreInterruptedApplyRecovery,
  requestOutfitImport,
  requestPackageInstall,
  requestRestoreCheckpoint,
  resolveInterruptedApplyRecovery,
  refreshProjects,
  refreshUnityReadiness,
  selectAdjustmentCheckpoint,
  repairUnityMcpBridge,
  revokeSkillPackageSigner,
  retrySubAgent,
  saveChats,
  saveProjectPrefs,
  saveAgentNotes,
  scanProjectIndex,
  sendAgentMessage,
  setSkillPackageSafeMode,
  setSkillPackageEnabled,
  setAppSessionToken,
  testProviderCapability,
  trustSkillPackageSigner,
  updateAdjustmentCheckpoint,
  updateApiConfig,
  updateVisionConfig,
  updateDiagnostics,
  updateExternalAgentGateway,
  updatePermission,
  updateSkill,
  overwriteAdjustmentCheckpoint,
  exportInterruptedApplyIncidentBundle,
  uninstallExternalAgentConnector,
  uninstallSkillPackage,
} from "./lib/api";
import { cn, formatCount } from "./lib/utils";

type BackendStartResult = {
  endpoint: string;
  started: boolean;
  already_running: boolean;
  mode: string;
  message: string;
};

type BackendStartStatus = {
  ok?: boolean;
  status?: string;
  error?: string;
  logDir?: string;
};

type BackendEventMessage = {
  type?: string;
  sessionId?: string;
  turnId?: string;
  clientTurnId?: string;
  textDelta?: string;
  done?: boolean;
  payload?: unknown;
};

type QueuedTurn = {
  id: string;
  text: string;
  attachments: ChatAttachment[];
  providerLabel: string;
  model: string;
  queuedFrom?: boolean;
};

type CurrentTurn = {
  clientTurnId?: string;
  text: string;
  startedAt: number;
  providerLabel: string;
  model: string;
};

type ProviderSnapshot = {
  provider: string;
  providerLabel: string;
  model: string;
};

type ModelOptionsScope = {
  provider: string;
  baseUrl: string;
};




type RunSingleTurnOptions = {
  baseItems?: ConversationItem[];
  sessionId?: string;
};



type ProjectUiPrefs = {
  pinnedPaths: string[];
  aliases: Record<string, string>;
};

type LayoutPaneWidths = {
  left: number;
  right: number;
};

type ThemeMode = "light" | "dark";

const ONBOARDING_FLAG_KEY = "vrcforge_onboarded";
const COLLAPSED_PROJECTS_KEY = "vrcforge_collapsed_projects";
const PROJECT_UI_PREFS_KEY = "vrcforge_project_ui_prefs";
const THEME_STORAGE_KEY = "vrcforge_theme";
const LEFT_SIDEBAR_COLLAPSED_KEY = "vrcforge_left_sidebar_collapsed";
const RIGHT_SIDEBAR_COLLAPSED_KEY = "vrcforge_right_sidebar_collapsed";
const LAYOUT_PANE_WIDTHS_KEY = "vrcforge_layout_pane_widths";
const RIGHT_RUNTIME_SECTION_COLLAPSED_KEY = "vrcforge_right_runtime_sections_collapsed";
const MAX_QUEUED_TURNS = 8;
const MAX_ATTACHMENTS_PER_TURN = 8;
const STARTUP_BACKGROUND_REFRESH_DELAY_MS = 1200;
const DEFAULT_LEFT_PANE_WIDTH = 280;
const DEFAULT_RIGHT_PANE_WIDTH = 320;
const COLLAPSED_LEFT_PANE_WIDTH = 56;
const RESIZE_HANDLE_WIDTH = 6;
const MIN_LEFT_PANE_WIDTH = 220;
const MAX_LEFT_PANE_WIDTH = 440;
const MIN_RIGHT_PANE_WIDTH = 260;
const MAX_RIGHT_PANE_WIDTH = 520;
const MIN_CENTER_PANE_WIDTH = 520;
const FALLBACK_ENDPOINT = "http://127.0.0.1:8757";
const VRCHAT_AVATAR_AGENT_NAMES = [
  "Manuka",
  "Shinano",
  "Kikyo",
  "Moe",
  "Selestia",
  "Milltina",
  "Kipfel",
  "Rurune",
  "Mamehinata",
  "Usasaki",
  "Airi",
  "Maya",
  "Rindo",
  "Karin",
  "Lasyusha",
  "Lime",
  "Chiffon",
  "Chocolat",
  "Mizuki",
  "Sio",
  "Milfy",
  "Mao",
  "Lumina",
  "Leefa",
  "Lunalitt",
  "Rusk",
  "Clonka",
  "Uzuruha",
  "Mitsumame",
  "Ulthara",
  "IsanaiNuku",
  "Yilnel",
  "NoraFirika",
  "IODragonewt",
  "Ortwa",
  "Ricorine",
  "Siska",
  "NoraMiaree",
  "Clara",
  "Korone",
  "Azuki",
  "Miminoko",
  "Nemesis",
  "Elusion",
];



function isTauriRuntime() {
  return "__TAURI_INTERNALS__" in window;
}

function isAbsoluteLocalPath(path?: string): boolean {
  const value = (path || "").trim();
  return /^[a-zA-Z]:[\\/]/.test(value) || value.startsWith("\\\\") || value.startsWith("/");
}

function pickSubAgentName(): string {
  const index = Math.floor(Math.random() * VRCHAT_AVATAR_AGENT_NAMES.length);
  return VRCHAT_AVATAR_AGENT_NAMES[index] || "Manuka";
}

function updateSubAgentList(current: SubAgentTaskList | null, task: SubAgentTask): SubAgentTaskList {
  const existing = current?.tasks || [];
  const tasks = [task, ...existing.filter((item) => item.id !== task.id)];
  return {
    ok: true,
    schema: current?.schema || "vrcforge.sub_agent_tasks.v1",
    tasks,
    count: tasks.length,
    roles: current?.roles,
    maxConcurrent: current?.maxConcurrent,
    runningCount: tasks.filter((item) => ["queued", "running", "cancelling"].includes(item.status)).length,
  };
}

function loadProjectUiPrefs(): ProjectUiPrefs {
  try {
    const raw = window.localStorage.getItem(PROJECT_UI_PREFS_KEY);
    const parsed = raw ? JSON.parse(raw) : null;
    if (!parsed || typeof parsed !== "object") {
      return { pinnedPaths: [], aliases: {} };
    }
    const pinnedPaths = Array.isArray(parsed.pinnedPaths)
      ? parsed.pinnedPaths.filter((item: unknown): item is string => typeof item === "string" && item.trim().length > 0)
      : [];
    const aliases =
      parsed.aliases && typeof parsed.aliases === "object"
        ? Object.fromEntries(
            Object.entries(parsed.aliases).filter(
              (entry): entry is [string, string] => typeof entry[0] === "string" && typeof entry[1] === "string" && entry[1].trim().length > 0,
            ),
          )
        : {};
    return { pinnedPaths, aliases };
  } catch {
    return { pinnedPaths: [], aliases: {} };
  }
}

function loadThemePreference(): ThemeMode {
  try {
    const raw = window.localStorage.getItem(THEME_STORAGE_KEY);
    return raw === "dark" || raw === "light" ? raw : "light";
  } catch {
    return "light";
  }
}

function clampNumber(value: number, min: number, max: number): number {
  if (!Number.isFinite(value)) {
    return min;
  }
  return Math.min(max, Math.max(min, value));
}

function loadLayoutPaneWidths(): LayoutPaneWidths {
  try {
    const raw = window.localStorage.getItem(LAYOUT_PANE_WIDTHS_KEY);
    if (!raw) {
      return { left: DEFAULT_LEFT_PANE_WIDTH, right: DEFAULT_RIGHT_PANE_WIDTH };
    }
    const parsed = raw ? JSON.parse(raw) : {};
    return {
      left: clampNumber(Number(parsed.left || DEFAULT_LEFT_PANE_WIDTH), MIN_LEFT_PANE_WIDTH, MAX_LEFT_PANE_WIDTH),
      right: clampNumber(Number(parsed.right || DEFAULT_RIGHT_PANE_WIDTH), MIN_RIGHT_PANE_WIDTH, MAX_RIGHT_PANE_WIDTH),
    };
  } catch {
    return { left: DEFAULT_LEFT_PANE_WIDTH, right: DEFAULT_RIGHT_PANE_WIDTH };
  }
}

function isMarkdownSmokeMode(): boolean {
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

function createMarkdownSmokeChatState(): { chats: ChatThread[]; activeChatId: string } {
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

function createSubAgentContextSmokeTask(): SubAgentTask | null {
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

function markdownSmokeAgentNotes(): string {
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

function markdownSmokeGoals(): AgentGoal[] {
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

function markdownSmokeMemories(): AgentMemory[] {
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

function isRuntimeSessionVerificationError(message: string): boolean {
  const normalized = message.toLowerCase();
  return (
    normalized.includes("runtime session verification failed") ||
    normalized.includes("local runtime was replaced") ||
    normalized.includes("does not accept this desktop session")
  );
}

export default function App() {
  const { t } = useTranslation();
  const initialChatState = useMemo(() => createMarkdownSmokeChatState(), []);
  const smokeMode = isMarkdownSmokeMode();
  const initialSubAgentTask = useMemo(() => createSubAgentContextSmokeTask(), []);
  const [endpoint, setEndpoint] = useState(FALLBACK_ENDPOINT);
  const [bootstrap, setBootstrap] = useState<AppBootstrap | null>(null);
  const [agentApprovals, setAgentApprovals] = useState<AgentApproval[] | null>(null);
  const [backendMessage, setBackendMessage] = useState("starting");
  const [loading, setLoading] = useState(false);
  const [sending, setSending] = useState(false);
  const [error, setError] = useState("");
  const [theme, setTheme] = useState<ThemeMode>(() => loadThemePreference());
  const [input, setInput] = useState("");
  const [chats, setChats] = useState<ChatThread[]>(() => initialChatState.chats);
  const [activeChatId, setActiveChatId] = useState(() => initialChatState.activeChatId);
  const [activeProjectPath, setActiveProjectPath] = useState("");
  const [activeView, setActiveView] = useState<ActiveView>("chat");
  const [leftSidebarCollapsed, setLeftSidebarCollapsed] = useState(() => {
    try {
      return window.localStorage.getItem(LEFT_SIDEBAR_COLLAPSED_KEY) === "true";
    } catch {
      return false;
    }
  });
  const [rightSidebarCollapsed, setRightSidebarCollapsed] = useState(() => {
    try {
      return window.localStorage.getItem(RIGHT_SIDEBAR_COLLAPSED_KEY) === "true";
    } catch {
      return false;
    }
  });
  const [layoutPaneWidths, setLayoutPaneWidths] = useState<LayoutPaneWidths>(() => loadLayoutPaneWidths());
  const [rightRuntimeSectionsCollapsed, setRightRuntimeSectionsCollapsed] = useState<Record<string, boolean>>(() => {
    try {
      const raw = window.localStorage.getItem(RIGHT_RUNTIME_SECTION_COLLAPSED_KEY);
      const parsed = raw ? JSON.parse(raw) : {};
      return parsed && typeof parsed === "object" ? (parsed as Record<string, boolean>) : {};
    } catch {
      return {};
    }
  });
  const [approvalActions, setApprovalActions] = useState<Record<string, ApprovalActionState>>({});
  const [messageFeedback, setMessageFeedback] = useState<Record<string, MessageFeedback>>({});
  const [chatMenu, setChatMenu] = useState<{ chatId: string; x: number; y: number } | null>(null);
  const [renamingChatId, setRenamingChatId] = useState("");
  const [renameDraft, setRenameDraft] = useState("");
  const [deleteTargetId, setDeleteTargetId] = useState("");
  const [showOnboarding, setShowOnboarding] = useState(() => {
    if (isMarkdownSmokeMode()) {
      return false;
    }
    try {
      return window.localStorage.getItem(ONBOARDING_FLAG_KEY) !== "true";
    } catch {
      return false;
    }
  });
  const [onboardingStep, setOnboardingStep] = useState(0);
  const [onboardingMinimized, setOnboardingMinimized] = useState(false);
  const [showProjectModal, setShowProjectModal] = useState(false);
  const [newProjectPath, setNewProjectPath] = useState("");
  const [savingProjectPrefs, setSavingProjectPrefs] = useState(false);
  const [projectModalError, setProjectModalError] = useState("");
  const [projectPrefs, setProjectPrefs] = useState<ProjectPrefs>({ customPaths: [], hiddenPaths: [] });
  const [projectPrefsReady, setProjectPrefsReady] = useState(false);
  const [loadingProjects, setLoadingProjects] = useState(false);
  const [projectMenu, setProjectMenu] = useState<{ projectPath: string; x: number; y: number } | null>(null);
  const [projectUiPrefs, setProjectUiPrefs] = useState<ProjectUiPrefs>(() => loadProjectUiPrefs());
  const [renamingProjectPath, setRenamingProjectPath] = useState("");
  const [projectRenameDraft, setProjectRenameDraft] = useState("");
  const [projectIndex, setProjectIndex] = useState<ProjectIndexScanResult | null>(null);
  const [projectIndexProject, setProjectIndexProject] = useState("");
  const [loadingProjectIndex, setLoadingProjectIndex] = useState(false);
  const [projectIndexError, setProjectIndexError] = useState("");
  const [optimizationReport, setOptimizationReport] = useState<OptimizationPlannerReport | null>(null);
  const [optimizationTargetProfile, setOptimizationTargetProfile] = useState("pc_conservative");
  const [optimizationAvatarPath, setOptimizationAvatarPath] = useState("");
  const [optimizationAvatars, setOptimizationAvatars] = useState<AvatarListItem[]>([]);
  const [loadingOptimizationAvatars, setLoadingOptimizationAvatars] = useState(false);
  const [optimizationAvatarMessage, setOptimizationAvatarMessage] = useState("");
  const [loadingOptimization, setLoadingOptimization] = useState(false);
  const [optimizationMessage, setOptimizationMessage] = useState("");
  const [requestingOptimizationAction, setRequestingOptimizationAction] = useState("");
  const [requestingOptimizationDependency, setRequestingOptimizationDependency] = useState("");
  const [optimizationActionOptions, setOptimizationActionOptions] = useState<Record<string, OptimizationActionOptions>>({});
  const [optimizationProofs, setOptimizationProofs] = useState<OptimizationProofSummary[]>([]);
  const [selectedOptimizationProof, setSelectedOptimizationProof] = useState<OptimizationProofDetail | null>(null);
  const [loadingOptimizationProofs, setLoadingOptimizationProofs] = useState(false);
  const [optimizationProofMessage, setOptimizationProofMessage] = useState("");
  const [protectionPlan, setProtectionPlan] = useState<AvatarEncryptionPlanResult | null>(null);
  const [protectionProfile, setProtectionProfile] = useState("standard");
  const [protectionAvatarPath, setProtectionAvatarPath] = useState("");
  const [protectionAvatars, setProtectionAvatars] = useState<AvatarListItem[]>([]);
  const [protectionOwnsAssets, setProtectionOwnsAssets] = useState(false);
  const [loadingProtection, setLoadingProtection] = useState(false);
  const [loadingProtectionAvatars, setLoadingProtectionAvatars] = useState(false);
  const [protectionMessage, setProtectionMessage] = useState("");
  const [protectionAvatarMessage, setProtectionAvatarMessage] = useState("");
  const [requestingProtectionFamily, setRequestingProtectionFamily] = useState("");
  const [subAgentList, setSubAgentList] = useState<SubAgentTaskList | null>(() =>
    initialSubAgentTask
      ? {
          ok: true,
          schema: "vrcforge.sub_agent_tasks.v1",
          tasks: [initialSubAgentTask],
          count: 1,
          roles: [
            {
              id: "selected_context_review",
              title: "Selected context review",
              description: "Open a scoped read-only sub-agent thread from selected chat text.",
              toolProfile: "read-only",
              readOnly: true,
            },
          ],
          maxConcurrent: 5,
          runningCount: 0,
        }
      : null,
  );
  const [loadingSubAgents, setLoadingSubAgents] = useState(false);
  const [subAgentError, setSubAgentError] = useState("");
  const [selectedSubAgent, setSelectedSubAgent] = useState<SubAgentTask | null>(() => initialSubAgentTask);
  const [selectedSubAgentPanelOpen, setSelectedSubAgentPanelOpen] = useState(() => Boolean(initialSubAgentTask));
  const [outfitPackagePath, setOutfitPackagePath] = useState("");
  const [outfitImportPlan, setOutfitImportPlan] = useState<OutfitImportPlanResult | null>(null);
  const [outfitImportStatus, setOutfitImportStatus] = useState("");
  const [loadingOutfitImportPlan, setLoadingOutfitImportPlan] = useState(false);
  const [requestingOutfitImport, setRequestingOutfitImport] = useState(false);
  const [workspaceDiff, setWorkspaceDiff] = useState<WorkspaceDiffSummary | null>(null);
  const [loadingWorkspaceDiff, setLoadingWorkspaceDiff] = useState(false);
  const [workspaceDiffError, setWorkspaceDiffError] = useState("");
  const [workspaceDiffReviewOpen, setWorkspaceDiffReviewOpen] = useState(false);
  const [loadingWorkspaceDiffPatch, setLoadingWorkspaceDiffPatch] = useState(false);
  const [loadingUnityStatus, setLoadingUnityStatus] = useState(false);
  const [runtimeRuns, setRuntimeRuns] = useState<AgentRuntimeRun[]>([]);
  const [runtimeRunsError, setRuntimeRunsError] = useState("");
  const [desktopActions, setDesktopActions] = useState<AgentDesktopAction[]>([]);
  const [agentGoals, setAgentGoals] = useState<AgentGoal[]>(() => markdownSmokeGoals());
  const [agentMemory, setAgentMemory] = useState<AgentMemory[]>(() => markdownSmokeMemories());
  const [workspaceStateError, setWorkspaceStateError] = useState("");
  const [runtimeNotice, setRuntimeNotice] = useState("");
  const [collapsedProjects, setCollapsedProjects] = useState<Record<string, boolean>>(() => {
    try {
      const raw = window.localStorage.getItem(COLLAPSED_PROJECTS_KEY);
      const parsed = raw ? JSON.parse(raw) : {};
      return parsed && typeof parsed === "object" ? (parsed as Record<string, boolean>) : {};
    } catch {
      return {};
    }
  });
  const [queued, setQueued] = useState<QueuedTurn[]>([]);
  const [currentTurn, setCurrentTurn] = useState<CurrentTurn | null>(null);
  const [stopRequested, setStopRequested] = useState(false);
  const [attachments, setAttachments] = useState<ChatAttachment[]>([]);
  const [selectionMenu, setSelectionMenu] = useState<{ x: number; y: number; text: string } | null>(null);
  const [apiProvider, setApiProvider] = useState("gemini");
  const [apiKey, setApiKey] = useState("");
  const [apiBaseUrl, setApiBaseUrl] = useState("");
  const [apiModel, setApiModel] = useState("gemini-2.5-flash");
  const [savingApiConfig, setSavingApiConfig] = useState(false);
  const [visionProvider, setVisionProvider] = useState("");
  const [visionApiKey, setVisionApiKey] = useState("");
  const [visionBaseUrl, setVisionBaseUrl] = useState("");
  const [visionModel, setVisionModel] = useState("");
  const [visionEnabled, setVisionEnabled] = useState(true);
  const [savingVisionConfig, setSavingVisionConfig] = useState(false);
  const [modelOptions, setModelOptions] = useState<ProviderModelInfo[]>([]);
  const [modelOptionsScope, setModelOptionsScope] = useState<ModelOptionsScope | null>(null);
  const [loadingModels, setLoadingModels] = useState(false);
  const [modelsError, setModelsError] = useState("");
  const [testingProvider, setTestingProvider] = useState("");
  const [providerTestMessage, setProviderTestMessage] = useState("");
  const [skillRegistry, setSkillRegistry] = useState<AgentSkillRegistry | null>(null);
  const [skillCheck, setSkillCheck] = useState<AgentSkillCheck | null>(null);
  const [selectedSkillName, setSelectedSkillName] = useState("");
  const [skillDraft, setSkillDraft] = useState<Partial<AgentSkill>>(emptySkillDraft());
  const [savingSkill, setSavingSkill] = useState(false);
  const [skillPackages, setSkillPackages] = useState<SkillPackageEntry[]>([]);
  const [skillPackageStore, setSkillPackageStore] = useState("");
  const [skillPackageGovernance, setSkillPackageGovernance] = useState<Record<string, unknown>>({});
  const [skillPackageAudit, setSkillPackageAudit] = useState<Array<Record<string, unknown>>>([]);
  const [loadingSkillPackages, setLoadingSkillPackages] = useState(false);
  const [skillPackageMessage, setSkillPackageMessage] = useState("");
  const [skillPackageError, setSkillPackageError] = useState("");
  const [doctorReport, setDoctorReport] = useState<DoctorReport | null>(null);
  const [loadingDoctor, setLoadingDoctor] = useState(false);
  const [doctorMessage, setDoctorMessage] = useState("");
  const [repairingUnityBridge, setRepairingUnityBridge] = useState(false);
  const [startupIssue, setStartupIssue] = useState("");
  const [dismissedDoctorPromptSignature, setDismissedDoctorPromptSignature] = useState("");
  const [diagnosticsStatus, setDiagnosticsStatus] = useState<DiagnosticsStatus | null>(null);
  const [loadingDiagnostics, setLoadingDiagnostics] = useState(false);
  const [exportingSupportBundle, setExportingSupportBundle] = useState(false);
  const [diagnosticsMessage, setDiagnosticsMessage] = useState("");
  const [checkpoints, setCheckpoints] = useState<AgentCheckpoint[]>([]);
  const [interruptedRecoveries, setInterruptedRecoveries] = useState<InterruptedApplyRecovery[]>([]);
  const [adjustmentCheckpoints, setAdjustmentCheckpoints] = useState<AdjustmentCheckpoint[]>([]);
  const [checkpointPreview, setCheckpointPreview] = useState<AgentCheckpointPreview | null>(null);
  const [recoveryPreview, setRecoveryPreview] = useState<InterruptedApplyRecoveryPreview | null>(null);
  const [adjustmentPreview, setAdjustmentPreview] = useState<AdjustmentCheckpointPreview | null>(null);
  const [loadingCheckpoints, setLoadingCheckpoints] = useState(false);
  const [restoringCheckpointId, setRestoringCheckpointId] = useState("");
  const [recoveryBusyId, setRecoveryBusyId] = useState("");
  const [adjustmentBusyId, setAdjustmentBusyId] = useState("");
  const [checkpointMessage, setCheckpointMessage] = useState("");
  const [recoveryMessage, setRecoveryMessage] = useState("");
  const [adjustmentMessage, setAdjustmentMessage] = useState("");
  const [agentNotes, setAgentNotes] = useState(() => markdownSmokeAgentNotes());
  const [agentNotesPath, setAgentNotesPath] = useState("");
  const [agentNotesLoaded, setAgentNotesLoaded] = useState(() => Boolean(markdownSmokeAgentNotes()));
  const [savingNotes, setSavingNotes] = useState(false);
  const [notesMessage, setNotesMessage] = useState("");
  const [connectorStatus, setConnectorStatus] = useState<ExternalAgentConnectorStatus | null>(null);
  const [loadingConnectors, setLoadingConnectors] = useState(false);
  const [connectorMessage, setConnectorMessage] = useState("");
  const [checkpointArchiveLimitInput, setCheckpointArchiveLimitInput] = useState("10240");
  const conversationEndRef = useRef<HTMLDivElement | null>(null);
  const projectInitRef = useRef(false);
  const chatsLoadedRef = useRef(false);
  const chatsDirtyRef = useRef(false);
  const chatsSaveVersionRef = useRef(0);
  const chatTimestampCacheTimerRef = useRef<number | null>(null);
  const projectPrefsLoadedRef = useRef(false);
  const chatsRef = useRef<ChatThread[]>([]);
  const queueRef = useRef<QueuedTurn[]>([]);
  const sendingRef = useRef(false);
  const stopRequestedRef = useRef(false);
  const streamingTurnChatRef = useRef(new Map<string, string>());
  const activeTurnAbortRef = useRef<AbortController | null>(null);
  const runtimeStartingRef = useRef(false);
  const startupLaunchStartedAtRef = useRef<number | null>(null);
  const backendReadyStatusRef = useRef<"idle" | "starting" | "ready" | "error">("idle");
  const backendReadyEndpointRef = useRef(endpoint);
  const backendReadyWaitersRef = useRef<
    Array<{
      resolve: (endpoint: string) => void;
      reject: (error: Error) => void;
    }>
  >([]);
  const healthRefreshInFlightRef = useRef(false);
  const runtimeRefreshSeqRef = useRef(0);
  const desktopEventBootstrapTimerRef = useRef<number | null>(null);
  const desktopEventRuntimeTimerRef = useRef<number | null>(null);
  const desktopEventSubAgentTimerRef = useRef<number | null>(null);
  const selectionMenuRef = useRef<HTMLDivElement | null>(null);

  const permission = bootstrap?.permission;
  const currentPermissionVisual = permissionVisualState(permission);
  const apiConfig = bootstrap?.apiConfig;
  const visionConfig = bootstrap?.visionConfig;
  const healthComponents = bootstrap?.health.components ?? {};
  const healthErrors = Object.values(healthComponents).filter((item) => item.status === "error").length;
  const healthWarnings = Object.values(healthComponents).filter((item) => item.status === "warning").length;
  const runtimeConnected = Boolean(bootstrap?.ok);
  const hasStartupIssue = startupIssue.trim().length > 0;
  const hasEnvironmentAttention = runtimeConnected && (healthErrors > 0 || healthWarnings > 0);
  const doctorPromptSignature = hasStartupIssue
    ? `startup:${startupIssue.trim()}`
    : `health:${Object.entries(healthComponents)
        .map(([id, component]) => `${id}:${component.status}:${component.message}`)
        .join("|")}`;
  const showDoctorStartupPrompt =
    activeView !== "doctor" && dismissedDoctorPromptSignature !== doctorPromptSignature && (hasStartupIssue || hasEnvironmentAttention);
  const toolCount = bootstrap?.agentManifest.toolCount ?? 0;
  const skills = skillRegistry?.skills ?? bootstrap?.agentManifest.skills ?? [];
  const skillCount = skillRegistry?.count ?? skills.length;
  const slashCommands = useMemo(() => {
    const list: Array<{ name: string; title: string }> = [
      { name: "compact", title: t("chat.slashCompact") },
      { name: "goal", title: t("chat.slashGoal") },
      { name: "memory", title: t("chat.slashMemory") },
      { name: "delegate", title: t("chat.slashDelegate") },
      { name: "desktop", title: t("composerAction.desktop") },
    ];
    for (const skill of skills) {
      if (!skill.name || skill.enabled === false || skill.available === false || skill.userInvocable === false) {
        continue;
      }
      list.push({ name: skill.name, title: skill.title || skill.description || "" });
    }
    return list;
  }, [skills, t]);
  const projects = bootstrap?.health.projects?.projects ?? [];
  const vrcForgeToolsCount = getHealthDetailNumber(healthComponents.vrcForgeUnityTools?.detail, "vrcForgeToolsCount");
  const vrcForgeToolsReady = runtimeConnected && healthComponents.vrcForgeUnityTools?.status === "ok" && vrcForgeToolsCount > 0;
  const agentModeLabel = runtimeConnected ? t("agent.modeLabel.basicMode") : t("agent.modeLabel.notConnected");
  const apiKeySaved = Boolean(apiConfig?.apiKeyPresent && (apiConfig?.provider || "") === apiProvider);
  const savedProvider = apiConfig?.provider || apiProvider;
  const savedProviderLabel = apiConfig?.providerLabel || providerDisplayName(savedProvider);
  const savedModel = apiConfig?.model || apiModel || defaultModelForProvider(savedProvider);
  const savedBaseUrl = apiConfig?.base_url || apiBaseUrl;
  const providerConfigured = runtimeConnected && Boolean(apiConfig) && (!apiConfig?.apiKeyRequired || Boolean(apiConfig?.apiKeyPresent));
  const externalAgentConnected = Boolean(connectorStatus?.gateway?.enabled);
  const chatAvailable = providerConfigured || externalAgentConnected;
  const chatDisabledReason = !runtimeConnected
    ? t("agent.modeLabel.notConnected")
    : !chatAvailable
      ? t("chat.providerNotConfigured", { provider: savedProviderLabel })
      : "";

  useEffect(() => {
    backendReadyEndpointRef.current = endpoint;
  }, [endpoint]);
  const composerActions = useMemo<ComposerAction[]>(
    () => {
      const actions: ComposerAction[] = [{ id: "attach", label: t("composerAction.attach"), description: t("composerAction.attachDesc") }];
      if (vrcForgeToolsReady) {
        actions.push({
          id: "screenshot",
          label: t("composerAction.screenshot"),
          description: t("composerAction.screenshotDesc"),
        });
      }
      actions.push({
        id: "desktop",
        label: t("composerAction.desktop"),
        description: t("composerAction.desktopDesc"),
      });
      return actions;
    },
    [t, vrcForgeToolsReady],
  );
  const providerSnapshot: ProviderSnapshot = {
    provider: savedProvider,
    providerLabel: savedProviderLabel,
    model: savedModel,
  };

  const hiddenPathSet = useMemo(
    () => new Set(projectPrefs.hiddenPaths.map(normalizeProjectPathKey)),
    [projectPrefs.hiddenPaths],
  );
  const customPathSet = useMemo(
    () => new Set(projectPrefs.customPaths.map(normalizeProjectPathKey)),
    [projectPrefs.customPaths],
  );
  const pinnedProjectSet = useMemo(
    () => new Set(projectUiPrefs.pinnedPaths.map(normalizeProjectPathKey)),
    [projectUiPrefs.pinnedPaths],
  );
  const projectItems = useMemo(
    () =>
      projects
        .filter((project) => !hiddenPathSet.has(normalizeProjectPathKey(project.path || "")))
        .sort((a, b) => Number(pinnedProjectSet.has(normalizeProjectPathKey(projectKey(b)))) - Number(pinnedProjectSet.has(normalizeProjectPathKey(projectKey(a))))),
    [projects, hiddenPathSet, pinnedProjectSet],
  );
  const hiddenProjects = useMemo(
    () => projects.filter((project) => hiddenPathSet.has(normalizeProjectPathKey(project.path || ""))),
    [projects, hiddenPathSet],
  );
  const activeChat = chats.find((chat) => chat.id === activeChatId) || null;
  const conversation = activeChat?.items ?? [];
  const sessionId = activeChat?.sessionId ?? "";
  const activeRuntimeProjectPath = activeChat?.projectPath || activeProjectPath;
  const latestEditableUserItemId = latestConversationItemId(conversation, (item) => item.type === "user");
  const latestRetryableItemId = latestConversationItemId(conversation, isRetryableConversationItem);
  const pendingApprovalItems = (agentApprovals ?? []).filter((item) => item.status === "pending");
  const pendingApprovals = pendingApprovalItems.length;
  const currentModelInfo = useMemo(
    () => {
      const modelScopeMatches =
        modelOptionsScope &&
        normalizeProviderForContext(modelOptionsScope.provider) === normalizeProviderForContext(providerSnapshot.provider) &&
        modelOptionsScope.baseUrl.trim() === savedBaseUrl.trim();
      return modelScopeMatches ? findProviderModelInfo(modelOptions, providerSnapshot.model) : undefined;
    },
    [modelOptions, modelOptionsScope, providerSnapshot.model, providerSnapshot.provider, savedBaseUrl],
  );
  const latestContextUsage = useMemo(() => latestAgentContextUsage(conversation), [conversation]);
  const contextUsage = useMemo(
    () =>
      apiConfig || smokeMode
        ? buildContextUsageFromRuntime(latestContextUsage, providerSnapshot.provider, providerSnapshot.model, currentModelInfo, t)
        : undefined,
    [apiConfig, currentModelInfo, latestContextUsage, providerSnapshot.model, providerSnapshot.provider, smokeMode, t],
  );
  const subAgentTasks = subAgentList?.tasks ?? [];
  const activeSubAgentTasks = useMemo(() => {
    const parentSession = activeChat?.sessionId || "";
    const projectKeyValue = normalizeProjectPathKey(activeRuntimeProjectPath);
    return subAgentTasks.filter((task) => {
      const sameSession = parentSession && task.parentSessionId === parentSession;
      const sameProject = projectKeyValue && normalizeProjectPathKey(task.projectPath || "") === projectKeyValue;
      return sameSession || sameProject || (!parentSession && !projectKeyValue);
    });
  }, [activeChat?.sessionId, activeRuntimeProjectPath, subAgentTasks]);
  const visibleSubAgentTasks = useMemo(() => {
    if (!selectedSubAgent || activeSubAgentTasks.some((task) => task.id === selectedSubAgent.id)) {
      return activeSubAgentTasks;
    }
    return [selectedSubAgent, ...activeSubAgentTasks];
  }, [activeSubAgentTasks, selectedSubAgent]);
  const runtimeSchedule = useMemo<RuntimeScheduleItem[]>(() => {
    const items: RuntimeScheduleItem[] = [];
    if (currentTurn) {
      items.push({
        id: "current-turn",
        status: stopRequested ? "cancelling" : "running",
        title: thinkingStatusForModelLabel(currentTurn.providerLabel, currentTurn.model),
        meta: `${currentTurn.providerLabel} / ${currentTurn.model}`,
      });
    }
    queued.forEach((turn, index) => {
      items.push({
        id: `queued-${turn.id}`,
        status: "queued",
        title: turn.text || i18n.t("attachments.fallbackTitle"),
        meta: i18n.t("workspace.queueMeta", { index: index + 1, provider: turn.providerLabel, model: turn.model }),
      });
    });
    activeSubAgentTasks
      .filter((task) => ["queued", "running", "cancelling"].includes(task.status))
      .forEach((task) => {
        items.push({
          id: `subagent-${task.id}`,
          status: task.status === "cancelling" ? "cancelling" : task.status === "queued" ? "queued" : "running",
          title: task.displayName || subAgentRoleLabel(task.role),
          meta: task.task || task.status,
        });
    });
    return items;
  }, [activeSubAgentTasks, currentTurn, queued, stopRequested]);
  const hasRunningSubAgents = subAgentTasks.some((task) => ["queued", "running", "cancelling"].includes(task.status));
  const activeProjectName =
    projectDisplayName(projectItems.find((project) => normalizeProjectPathKey(projectKey(project)) === normalizeProjectPathKey(activeProjectPath))) ||
    (activeProjectPath ? shortPath(activeProjectPath) : "");
  const effectiveLeftPaneWidth = leftSidebarCollapsed ? COLLAPSED_LEFT_PANE_WIDTH : layoutPaneWidths.left;
  const effectiveRightPaneWidth = rightSidebarCollapsed ? 0 : layoutPaneWidths.right;
  const workspaceGridColumns = `${effectiveLeftPaneWidth}px ${RESIZE_HANDLE_WIDTH}px minmax(0,1fr) ${RESIZE_HANDLE_WIDTH}px ${effectiveRightPaneWidth}px`;
  const startLayoutResize = (side: "left" | "right", event: ReactPointerEvent<HTMLDivElement>) => {
    event.preventDefault();
    const startX = event.clientX;
    const startLeftWidth = effectiveLeftPaneWidth;
    const startRightWidth = effectiveRightPaneWidth;
    const leftCollapseThreshold = MIN_LEFT_PANE_WIDTH * 0.65;
    const rightCollapseThreshold = MIN_RIGHT_PANE_WIDTH * 0.65;
    const previousCursor = document.body.style.cursor;
    const previousUserSelect = document.body.style.userSelect;
    document.body.style.cursor = "col-resize";
    document.body.style.userSelect = "none";

    const maxLeftWidth = () => {
      const available = window.innerWidth - RESIZE_HANDLE_WIDTH * 2 - MIN_CENTER_PANE_WIDTH - (rightSidebarCollapsed ? 0 : layoutPaneWidths.right);
      return Math.max(MIN_LEFT_PANE_WIDTH, Math.min(MAX_LEFT_PANE_WIDTH, available));
    };
    const maxRightWidth = () => {
      const available = window.innerWidth - RESIZE_HANDLE_WIDTH * 2 - MIN_CENTER_PANE_WIDTH - (leftSidebarCollapsed ? COLLAPSED_LEFT_PANE_WIDTH : layoutPaneWidths.left);
      return Math.max(MIN_RIGHT_PANE_WIDTH, Math.min(MAX_RIGHT_PANE_WIDTH, available));
    };

    const onPointerMove = (moveEvent: PointerEvent) => {
      const delta = moveEvent.clientX - startX;
      if (side === "left") {
        const proposed = startLeftWidth + delta;
        if (proposed <= leftCollapseThreshold) {
          setLeftSidebarCollapsed(true);
          return;
        }
        setLeftSidebarCollapsed(false);
        setLayoutPaneWidths((current) => ({
          ...current,
          left: clampNumber(proposed, MIN_LEFT_PANE_WIDTH, maxLeftWidth()),
        }));
        return;
      }

      const proposed = startRightWidth - delta;
      if (proposed <= rightCollapseThreshold) {
        setRightSidebarCollapsed(true);
        return;
      }
      setRightSidebarCollapsed(false);
      setLayoutPaneWidths((current) => ({
        ...current,
        right: clampNumber(proposed, MIN_RIGHT_PANE_WIDTH, maxRightWidth()),
      }));
    };

    const onPointerUp = () => {
      document.body.style.cursor = previousCursor;
      document.body.style.userSelect = previousUserSelect;
      window.removeEventListener("pointermove", onPointerMove);
      window.removeEventListener("pointerup", onPointerUp);
      window.removeEventListener("pointercancel", onPointerUp);
    };

    window.addEventListener("pointermove", onPointerMove);
    window.addEventListener("pointerup", onPointerUp);
    window.addEventListener("pointercancel", onPointerUp);
  };
  const workspaceDiffFiles = workspaceDiff?.files ?? [];
  const runtimeFileReferences = useMemo(() => buildRuntimeFileReferences(conversation, workspaceDiffFiles), [conversation, workspaceDiffFiles]);
  const workspaceDiffChanged = workspaceDiff?.status === "changed" && workspaceDiff.fileCount > 0;
  const runtimeReviewEvidence = useMemo<RuntimeReviewEvidence[]>(() => {
    const items: RuntimeReviewEvidence[] = [];
    for (const approval of pendingApprovalItems.slice(0, 4)) {
      items.push({
        id: `approval-${approval.id}`,
        kind: "approval",
        title: approval.targetTool || approval.preview?.command || t("workspace.approvalEvidence"),
        meta: t("workspace.approvalEvidenceMeta", { status: approval.status || "pending" }),
        status: approval.status,
      });
    }
    const seenCheckpoints = new Set<string>();
    for (const run of runtimeRuns) {
      const checkpointIds = [run.checkpointId, ...(run.checkpointIds ?? [])].filter(Boolean) as string[];
      for (const checkpointId of checkpointIds) {
        if (seenCheckpoints.has(checkpointId)) {
          continue;
        }
        seenCheckpoints.add(checkpointId);
        items.push({
          id: `checkpoint-${checkpointId}`,
          kind: "checkpoint",
          title: checkpointId,
          meta: run.targetTool || run.writeTool || run.messageSummary || t("workspace.checkpointEvidence"),
          status: run.status,
          action: () => setActiveView("checkpoints"),
        });
      }
    }
    const recentRunWithApproval = runtimeRuns.find((run) => (run.approvalId || (run.approvalIds ?? []).length) && !run.checkpointId);
    if (recentRunWithApproval && items.length < 6) {
      items.push({
        id: `run-approval-${recentRunWithApproval.id || recentRunWithApproval.turnId || recentRunWithApproval.clientTurnId}`,
        kind: "run",
        title: recentRunWithApproval.targetTool || recentRunWithApproval.writeTool || t("workspace.runEvidence"),
        meta: recentRunWithApproval.status || recentRunWithApproval.lastEvent || "",
        status: recentRunWithApproval.status,
      });
    }
    if (workspaceDiffChanged) {
      items.push({
        id: "git-diff",
        kind: "diff",
        title: t("workspace.gitDiffEvidence"),
        meta: t("workspace.gitDiffEvidenceMeta", {
          count: formatCount(workspaceDiff?.fileCount || 0),
          additions: formatCount(workspaceDiff?.additions || 0),
          deletions: formatCount(workspaceDiff?.deletions || 0),
        }),
        status: workspaceDiff?.status,
        action: toggleWorkspaceDiffReview,
      });
    }
    return items.slice(0, 8);
  }, [pendingApprovalItems, runtimeRuns, t, workspaceDiff?.additions, workspaceDiff?.deletions, workspaceDiff?.fileCount, workspaceDiff?.status, workspaceDiffChanged]);
  const backendComponent = healthComponents.backend;
  const unityBridgeComponent = healthComponents.unityMcpBridgeReachable;
  const unityToolsComponent = healthComponents.vrcForgeUnityTools;
  const providerComponent = healthComponents.providerConfigPresent;
  const localizeHealthMessage = (message?: string | null) => {
    const normalized = (message || "").trim();
    if (!normalized) {
      return "";
    }
    if (normalized === "Backend process is responding.") {
      return t("workspace.backendResponding");
    }
    if (normalized === "Unity MCP bridge online" || normalized === "Unity bridge online") {
      return t("workspace.unityBridgeOnline");
    }
    if (normalized === "Unity MCP bridge is not reachable.") {
      return t("workspace.unityBridgeNotReachable");
    }
    if (normalized === "Unity MCP is connected, but VRCForge Unity tools are missing or incomplete.") {
      return t("workspace.unityToolsMissing");
    }
    return normalized;
  };
  const workspaceProjectLabel = activeProjectPath ? activeProjectName || shortPath(activeProjectPath) : t("sidebar.tempChat");
  const unityBridgeLabel = !runtimeConnected
    ? t("workspace.coreOffline")
    : unityBridgeComponent?.status === "ok"
      ? t("workspace.unityBridgeOnline")
      : localizeHealthMessage(unityBridgeComponent?.message) || t("workspace.unityNotConnected");
  const unityToolsLabel = vrcForgeToolsReady
    ? t("workspace.vrcTools", { count: formatCount(vrcForgeToolsCount) })
    : localizeHealthMessage(unityToolsComponent?.message) || (runtimeConnected ? t("workspace.avatarToolsNotReady") : t("workspace.coreOffline"));
  const providerCompactLabel = `${providerSnapshot.providerLabel}${providerSnapshot.model ? ` / ${providerSnapshot.model}` : ""}`;
  const reviewSummaryLabel = pendingApprovals
    ? t("workspace.pendingApprovals", { count: formatCount(pendingApprovals) })
    : t("workspace.noPendingApprovals");
  const changeSummaryLabel = loadingWorkspaceDiff
    ? t("workspace.refreshing")
    : workspaceDiffError
      ? t("workspace.diffUnavailable")
      : workspaceDiff
        ? workspaceDiffChanged
          ? t("workspace.changedFiles", { count: formatCount(workspaceDiff.fileCount) })
          : workspaceDiff.status === "clean"
            ? t("workspace.clean")
            : workspaceDiff.status
        : runtimeConnected
          ? t("workspace.notLoaded")
          : t("workspace.coreOffline");
  const chatSidebar = useMemo(() => {
    const now = Date.now();
    return {
      ...groupSidebarChats(chats, normalizeProjectPathKey),
      times: new Map(chats.map((chat) => [chat.id, formatChatSidebarTime(chat, now, i18n.language)])),
    };
  }, [chats, i18n.language]);
  const projectPromptTitle = activeProjectPath && activeProjectName ? t("chat.promptTitle", { name: activeProjectName }) : t("chat.promptTitleDefault");
  const emptyProjectState = useMemo(() => {
    if (projectItems.length > 0) {
      return null;
    }
    if (loading && !error) {
      return { name: t("agent.emptyProjectState.scanning"), meta: "wait" };
    }
    if (hasStartupIssue || !runtimeConnected) {
      return { name: t("agent.modeLabel.notConnected"), meta: "retry" };
    }
    if (error) {
      return { name: t("agent.emptyProjectState.refreshFailed"), meta: "retry" };
    }
    return { name: t("agent.emptyProjectState.noUnityProject"), meta: "empty" };
  }, [error, hasStartupIssue, loading, projectItems.length, runtimeConnected]);

  useLayoutEffect(() => {
    const isDark = theme === "dark";
    document.documentElement.classList.toggle("dark", isDark);
    document.documentElement.dataset.theme = theme;
    document.documentElement.style.colorScheme = theme;
    document.body.style.colorScheme = theme;
    try {
      window.localStorage.setItem(THEME_STORAGE_KEY, theme);
    } catch {
      // Ignore blocked storage; the in-memory theme still works for this run.
    }
  }, [theme]);

  useLayoutEffect(() => {
    const menu = selectionMenuRef.current;
    if (!selectionMenu || !menu) {
      return;
    }

    const positionMenu = () => {
      const margin = 8;
      const gap = 8;
      const rect = menu.getBoundingClientRect();
      const maxLeft = Math.max(margin, window.innerWidth - rect.width - margin);
      const left = Math.min(Math.max(margin, selectionMenu.x - rect.width / 2), maxLeft);
      const preferredTop = selectionMenu.y - rect.height - gap;
      const fallbackTop = selectionMenu.y + gap;
      const maxTop = Math.max(margin, window.innerHeight - rect.height - margin);
      const top = Math.min(Math.max(margin, preferredTop >= margin ? preferredTop : fallbackTop), maxTop);

      menu.style.left = `${left}px`;
      menu.style.top = `${top}px`;
    };

    positionMenu();
    window.addEventListener("resize", positionMenu);
    return () => window.removeEventListener("resize", positionMenu);
  }, [selectionMenu]);

  useEffect(() => {
    // 屏蔽 WebView 默认右键菜单（返回/刷新/另存为等）；输入框保留原生菜单以便粘贴。
    const handler = (event: MouseEvent) => {
      const target = event.target as HTMLElement | null;
      if (target && target.closest("input, textarea, [contenteditable='true']")) {
        return;
      }
      event.preventDefault();
    };
    window.addEventListener("contextmenu", handler);
    return () => window.removeEventListener("contextmenu", handler);
  }, []);

  useEffect(() => {
    chatsRef.current = chats;
  }, [chats]);

  useEffect(() => {
    return () => {
      if (chatTimestampCacheTimerRef.current) {
        window.clearTimeout(chatTimestampCacheTimerRef.current);
      }
    };
  }, []);

  useEffect(() => {
    const configuredLimit = connectorStatus?.gateway?.checkpointArchiveMaxSizeMb;
    setCheckpointArchiveLimitInput(typeof configuredLimit === "number" ? String(configuredLimit) : "10240");
  }, [connectorStatus?.gateway?.checkpointArchiveMaxSizeMb]);

  useEffect(() => {
    try {
      window.localStorage.setItem(COLLAPSED_PROJECTS_KEY, JSON.stringify(collapsedProjects));
    } catch {
      // 忽略持久化失败
    }
  }, [collapsedProjects]);

  useEffect(() => {
    try {
      window.localStorage.setItem(PROJECT_UI_PREFS_KEY, JSON.stringify(projectUiPrefs));
    } catch {
      // Project display preferences are best-effort local UI state.
    }
  }, [projectUiPrefs]);

  useEffect(() => {
    try {
      window.localStorage.setItem(LEFT_SIDEBAR_COLLAPSED_KEY, String(leftSidebarCollapsed));
    } catch {
      // Sidebar width is best-effort local UI state.
    }
  }, [leftSidebarCollapsed]);

  useEffect(() => {
    try {
      window.localStorage.setItem(RIGHT_SIDEBAR_COLLAPSED_KEY, String(rightSidebarCollapsed));
    } catch {
      // Sidebar width is best-effort local UI state.
    }
  }, [rightSidebarCollapsed]);

  useEffect(() => {
    try {
      window.localStorage.setItem(LAYOUT_PANE_WIDTHS_KEY, JSON.stringify(layoutPaneWidths));
    } catch {
      // Pane widths are best-effort local UI state.
    }
  }, [layoutPaneWidths]);

  useEffect(() => {
    try {
      window.localStorage.setItem(RIGHT_RUNTIME_SECTION_COLLAPSED_KEY, JSON.stringify(rightRuntimeSectionsCollapsed));
    } catch {
      // Runtime section layout is best-effort local UI state.
    }
  }, [rightRuntimeSectionsCollapsed]);

  useEffect(() => {
    if (!runtimeConnected || projectPrefsLoadedRef.current) {
      return;
    }
    projectPrefsLoadedRef.current = true;
    void fetchProjectPrefs(endpoint)
      .then((prefs) => {
        setProjectPrefs(prefs);
        setProjectPrefsReady(true);
      })
      .catch(() => {
        setProjectPrefsReady(true);
      });
  }, [runtimeConnected, endpoint]);

  useEffect(() => {
    // 引导最小化期间，当前步骤完成后自动弹回向导。
    if (!showOnboarding || !onboardingMinimized) {
      return;
    }
    const stepStates = [runtimeConnected, Boolean(apiConfig?.apiKeyPresent), projectItems.length > 0];
    if (stepStates[Math.min(onboardingStep, stepStates.length - 1)]) {
      setOnboardingMinimized(false);
    }
  }, [showOnboarding, onboardingMinimized, onboardingStep, runtimeConnected, apiConfig?.apiKeyPresent, projectItems.length]);

  useEffect(() => {
    if (!isTauriRuntime()) {
      void startRuntime({ waitForBootstrap: false });
      return;
    }
    let active = true;
    let unlistenStartStatus: (() => void) | null = null;
    void listen<BackendStartStatus>("vrcforge-backend-start-status", (event) => {
      if (!active) {
        return;
      }
      handleBackendStartStatus(event.payload);
    })
      .then((unlisten) => {
        if (active) {
          unlistenStartStatus = unlisten;
          void startRuntime({ waitForBootstrap: false });
        } else {
          unlisten();
        }
      })
      .catch(() => {
        void startRuntime({ waitForBootstrap: false });
      });
    return () => {
      active = false;
      unlistenStartStatus?.();
    };
  }, []);

  useEffect(() => {
    conversationEndRef.current?.scrollIntoView({ behavior: "smooth", block: "end" });
  }, [conversation.length]);

  useEffect(() => {
    if (!projectInitRef.current && projectItems.length > 0) {
      projectInitRef.current = true;
      if (!activeProjectPath) {
        setActiveProjectPath(projectKey(projectItems[0]));
      }
    }
  }, [projectItems]);

  useEffect(() => {
    if (!runtimeConnected || chatsLoadedRef.current || !projectPrefsReady) {
      return;
    }
    let restoreCancelled = false;
    let restoreCompleted = false;
    chatsLoadedRef.current = true;
    void (async () => {
      try {
        await new Promise((resolve) => window.setTimeout(resolve, isTauriRuntime() ? 1000 : 0));
        if (restoreCancelled) {
          return;
        }
        const projectPaths = Array.from(
          new Set([
            ...projectItems.map((project) => projectKey(project)).filter(Boolean),
            ...projectPrefs.customPaths.filter(Boolean),
          ]),
        );
        const payload = await fetchChats<unknown>(endpoint, projectPaths);
        let shouldCacheRestoredTimestamps = false;
        const restored = (payload.chats || []).filter(isStoredChat).map((chat) => {
          const normalized: ChatThread = {
          id: chat.id,
          sessionId: typeof chat.sessionId === "string" ? chat.sessionId : "",
          title: typeof chat.title === "string" ? chat.title : "",
          projectPath: typeof chat.projectPath === "string" ? chat.projectPath : "",
          createdAt: typeof chat.createdAt === "string" ? chat.createdAt : "",
          updatedAt: typeof chat.updatedAt === "string" ? chat.updatedAt : "",
          agentName: typeof chat.agentName === "string" ? chat.agentName : "",
          pinned: chat.pinned === true,
          archived: chat.archived === true,
          items: chat.items,
          };
          const cached = cacheChatTimestampsFast(normalized);
          shouldCacheRestoredTimestamps =
            shouldCacheRestoredTimestamps || cached.createdAt !== normalized.createdAt || cached.updatedAt !== normalized.updatedAt;
          return cached;
        });
        if (restoreCancelled) {
          return;
        }
        if (restored.length > 0) {
          const initialSaveVersion = chatsSaveVersionRef.current;
          const canRestore = chatsRef.current.length === 0;
          if (canRestore) {
            setChats(restored);
            if (shouldCacheRestoredTimestamps) {
              if (chatTimestampCacheTimerRef.current) {
                window.clearTimeout(chatTimestampCacheTimerRef.current);
              }
              chatTimestampCacheTimerRef.current = window.setTimeout(() => {
                chatTimestampCacheTimerRef.current = null;
                if (chatsSaveVersionRef.current !== initialSaveVersion || chatsRef.current.length !== restored.length) {
                  return;
                }
                void saveChats(endpoint, chatsRef.current);
              }, 3000);
            }
          }
        }
        restoreCompleted = true;
      } catch {
        // 读取失败时保持空列表，不打断使用；下次启动会重试。
        chatsLoadedRef.current = false;
      }
    })();
    return () => {
      restoreCancelled = true;
      if (!restoreCompleted) {
        chatsLoadedRef.current = false;
      }
    };
  }, [runtimeConnected, endpoint, projectItems, projectPrefs.customPaths, projectPrefsReady]);

  useEffect(() => {
    if (!chatsLoadedRef.current || !runtimeConnected || !chatsDirtyRef.current) {
      return;
    }
    const saveVersion = chatsSaveVersionRef.current;
    const timer = window.setTimeout(() => {
      void saveChats(endpoint, chats)
        .then(() => {
          if (chatsSaveVersionRef.current === saveVersion) {
            chatsDirtyRef.current = false;
          }
        })
        .catch(() => {
          chatsDirtyRef.current = true;
        });
    }, 800);
    return () => window.clearTimeout(timer);
  }, [chats, runtimeConnected, endpoint]);

  useEffect(() => {
    if (!apiConfig) {
      return;
    }
    setApiProvider(apiConfig.provider || "gemini");
    setApiBaseUrl(apiConfig.base_url || "");
    setApiModel(apiConfig.model || defaultModelForProvider(apiConfig.provider || "gemini"));
    setModelOptions([]);
    setModelOptionsScope(null);
  }, [apiConfig?.provider, apiConfig?.base_url, apiConfig?.model]);

  useEffect(() => {
    if (!visionConfig) {
      return;
    }
    setVisionProvider(visionConfig.provider || "");
    setVisionBaseUrl(visionConfig.base_url || "");
    setVisionModel(visionConfig.model || "");
    setVisionEnabled(visionConfig.enabled !== false);
  }, [visionConfig?.provider, visionConfig?.base_url, visionConfig?.model, visionConfig?.enabled]);

  useEffect(() => {
    const intervalMs = isTauriRuntime() ? 30000 : 5000;
    const timer = window.setInterval(() => {
      void refreshSilently();
    }, intervalMs);
    return () => window.clearInterval(timer);
  }, [endpoint]);

  useEffect(() => {
    if (!runtimeConnected || !isTauriRuntime()) {
      return;
    }
    let active = true;
    let unlistenBackendEvent: (() => void) | null = null;
    let unlistenBackendStatus: (() => void) | null = null;
    const runtimeEvents = new Set([
      "agentApprovals",
      "agentDesktopActions",
      "agentGoals",
      "agentMemory",
      "agentPermission",
      "agentRuntimeCancel",
      "agentRuntimeQueue",
      "agentRuntimeRuns",
      "agentRuntimeTurn",
    ]);
    const bootstrapEvents = new Set(["agentPermission", "hello", "projects", "unity_status"]);
    const scheduleBootstrapRefresh = () => {
      if (desktopEventBootstrapTimerRef.current !== null) {
        window.clearTimeout(desktopEventBootstrapTimerRef.current);
      }
      desktopEventBootstrapTimerRef.current = window.setTimeout(() => {
        desktopEventBootstrapTimerRef.current = null;
        if (active) {
          void refreshSilently();
        }
      }, 200);
    };
    const scheduleRuntimeRefresh = () => {
      if (desktopEventRuntimeTimerRef.current !== null) {
        window.clearTimeout(desktopEventRuntimeTimerRef.current);
      }
      desktopEventRuntimeTimerRef.current = window.setTimeout(() => {
        desktopEventRuntimeTimerRef.current = null;
        if (active) {
          void refreshRuntimeRuns(false);
        }
      }, 150);
    };
    const scheduleSubAgentRefresh = () => {
      if (desktopEventSubAgentTimerRef.current !== null) {
        window.clearTimeout(desktopEventSubAgentTimerRef.current);
      }
      desktopEventSubAgentTimerRef.current = window.setTimeout(() => {
        desktopEventSubAgentTimerRef.current = null;
        if (active) {
          void loadSubAgents(false);
        }
      }, 200);
    };
    void listen<BackendEventMessage>("vrcforge-backend-event", (event) => {
      const eventType = typeof event.payload?.type === "string" ? event.payload.type : "";
      if (!eventType) {
        return;
      }
      if (eventType === "agentRuntimeDelta") {
        applyRuntimeDelta(event.payload as AgentRuntimeDeltaEvent);
        return;
      }
      if (bootstrapEvents.has(eventType)) {
        scheduleBootstrapRefresh();
      }
      if (runtimeEvents.has(eventType)) {
        scheduleRuntimeRefresh();
      }
      if (eventType === "subAgentTasks") {
        scheduleSubAgentRefresh();
      }
    })
      .then((unlisten) => {
        if (active) {
          unlistenBackendEvent = unlisten;
        } else {
          unlisten();
        }
      })
      .catch(() => undefined);
    void listen("vrcforge-backend-event-status", () => {
      // Status is intentionally quiet; the normal runtime banner remains the user-facing signal.
    })
      .then((unlisten) => {
        if (active) {
          unlistenBackendStatus = unlisten;
        } else {
          unlisten();
        }
      })
      .catch(() => undefined);
    return () => {
      active = false;
      unlistenBackendEvent?.();
      unlistenBackendStatus?.();
      if (desktopEventBootstrapTimerRef.current !== null) {
        window.clearTimeout(desktopEventBootstrapTimerRef.current);
        desktopEventBootstrapTimerRef.current = null;
      }
      if (desktopEventRuntimeTimerRef.current !== null) {
        window.clearTimeout(desktopEventRuntimeTimerRef.current);
        desktopEventRuntimeTimerRef.current = null;
      }
      if (desktopEventSubAgentTimerRef.current !== null) {
        window.clearTimeout(desktopEventSubAgentTimerRef.current);
        desktopEventSubAgentTimerRef.current = null;
      }
    };
  }, [runtimeConnected, endpoint, sessionId, activeRuntimeProjectPath, activeProjectPath, workspaceDiffReviewOpen]);

  useEffect(() => {
    if (!runtimeConnected) {
      return;
    }
    const timer = window.setTimeout(() => {
      void refreshFullHealth(endpoint);
    }, STARTUP_BACKGROUND_REFRESH_DELAY_MS);
    return () => window.clearTimeout(timer);
  }, [runtimeConnected, endpoint, activeProjectPath]);

  useEffect(() => {
    if (!isTauriRuntime()) {
      return;
    }
    let active = true;
    let unlistenTrayOpenChat: (() => void) | undefined;
    void listen("vrcforge-tray-open-chat", () => {
      setActiveView("chat");
      setError("");
      if (!activeChatId) {
        newTemporaryChat();
      }
    })
      .then((unlisten) => {
        if (active) {
          unlistenTrayOpenChat = unlisten;
        } else {
          unlisten();
        }
      })
      .catch(() => undefined);
    return () => {
      active = false;
      unlistenTrayOpenChat?.();
    };
  }, [activeChatId, chats]);

  useEffect(() => {
    if (!runtimeConnected) {
      setSubAgentList(null);
      return;
    }
    void loadSubAgents(false);
  }, [runtimeConnected, endpoint]);

  useEffect(() => {
    if (!runtimeConnected || !hasRunningSubAgents) {
      return;
    }
    const timer = window.setInterval(() => {
      void loadSubAgents(false);
    }, 1500);
    return () => window.clearInterval(timer);
  }, [runtimeConnected, endpoint, hasRunningSubAgents]);

  useEffect(() => {
    if (activeView === "checkpoints" && runtimeConnected) {
      void loadCheckpoints();
    }
  }, [activeView, runtimeConnected, endpoint, activeProjectPath]);

  useEffect(() => {
    if (activeView === "doctor" && runtimeConnected) {
      void loadDoctor();
    }
  }, [activeView, runtimeConnected, endpoint, activeProjectPath]);

  useEffect(() => {
    if (activeView === "optimization" && runtimeConnected) {
      void loadOptimizationPlan();
    }
  }, [activeView, runtimeConnected, endpoint, activeProjectPath, optimizationTargetProfile]);

  useEffect(() => {
    if (activeView === "optimization" && runtimeConnected) {
      void loadOptimizationAvatars();
    }
  }, [activeView, runtimeConnected, endpoint, activeProjectPath]);

  useEffect(() => {
    if (activeView === "protection" && runtimeConnected) {
      void loadProtectionPlan();
    }
  }, [activeView, runtimeConnected, endpoint, activeProjectPath, protectionProfile, protectionOwnsAssets]);

  useEffect(() => {
    if (activeView === "protection" && runtimeConnected) {
      void loadProtectionAvatars();
    }
  }, [activeView, runtimeConnected, endpoint, activeProjectPath]);

  useEffect(() => {
    if (!runtimeConnected || !activeProjectPath) {
      setProjectIndex(null);
      setProjectIndexProject("");
      setProjectIndexError("");
      return;
    }
    const timer = window.setTimeout(() => {
      void scanActiveProjectIndex(activeProjectPath, true);
    }, STARTUP_BACKGROUND_REFRESH_DELAY_MS);
    return () => window.clearTimeout(timer);
  }, [runtimeConnected, endpoint, activeProjectPath]);

  useEffect(() => {
    if (!runtimeConnected) {
      setWorkspaceDiff(null);
      setWorkspaceDiffError("");
    }
  }, [runtimeConnected, endpoint, activeProjectPath]);

  useEffect(() => {
    setAgentApprovals(null);
  }, [activeRuntimeProjectPath]);

  useEffect(() => {
    if (!runtimeConnected) {
      setRuntimeRuns([]);
      setRuntimeRunsError("");
      return;
    }
    void refreshRuntimeRuns(false);
  }, [runtimeConnected, endpoint, sessionId, activeRuntimeProjectPath]);

  useEffect(() => {
    if (!runtimeConnected || rightSidebarCollapsed) {
      return;
    }
    const intervalMs = isTauriRuntime() ? (sending || pendingApprovals > 0 ? 5000 : 15000) : sending || pendingApprovals > 0 ? 2500 : 5000;
    const timer = window.setInterval(() => {
      void refreshRuntimeRuns(false);
    }, intervalMs);
    return () => window.clearInterval(timer);
  }, [runtimeConnected, endpoint, sessionId, activeRuntimeProjectPath, rightSidebarCollapsed, sending, pendingApprovals]);

  function refreshStartupInBackground(target: string, options: { refreshProjects?: boolean } = {}) {
    const startedAt = performance.now();
    void refreshWithRetry(target, options)
      .then(() => {
        const metrics = ((window as any).__vrcforgeStartupMetrics ||= {});
        metrics.bootstrapRefreshMs = Math.round(performance.now() - startedAt);
      })
      .catch((cause) => {
        const message = cause instanceof Error ? cause.message : String(cause);
        setError(message);
        setStartupIssue(message);
      });
  }

  function resolveBackendReady(target: string, status?: string) {
    backendReadyStatusRef.current = "ready";
    backendReadyEndpointRef.current = target;
    const startedAt = startupLaunchStartedAtRef.current;
    const metrics = ((window as any).__vrcforgeStartupMetrics ||= {});
    metrics.backendReadyEventMs = startedAt === null ? null : Math.round(performance.now() - startedAt);
    metrics.backendReadyMode = status || "ready";
    const waiters = backendReadyWaitersRef.current.splice(0);
    waiters.forEach((waiter) => waiter.resolve(target));
  }

  function rejectBackendReady(message: string) {
    backendReadyStatusRef.current = "error";
    const error = new Error(message);
    const waiters = backendReadyWaitersRef.current.splice(0);
    waiters.forEach((waiter) => waiter.reject(error));
  }

  function waitForBackendReady(target = backendReadyEndpointRef.current): Promise<string> {
    if (!isTauriRuntime() || backendReadyStatusRef.current === "ready") {
      return Promise.resolve(target);
    }
    return new Promise((resolve, reject) => {
      let timeoutId = 0;
      const waiter = {
        resolve: (readyEndpoint: string) => {
          window.clearTimeout(timeoutId);
          resolve(readyEndpoint || target);
        },
        reject: (error: Error) => {
          window.clearTimeout(timeoutId);
          reject(error);
        },
      };
      timeoutId = window.setTimeout(() => {
        backendReadyWaitersRef.current = backendReadyWaitersRef.current.filter((item) => item !== waiter);
        reject(new Error("VRCForge runtime startup timed out."));
      }, 20000);
      backendReadyWaitersRef.current.push(waiter);
    });
  }

  function handleBackendStartStatus(payload: BackendStartStatus | undefined, target = backendReadyEndpointRef.current) {
    if (payload?.ok) {
      resolveBackendReady(target, payload.status);
      refreshStartupInBackground(target, { refreshProjects: true });
      return;
    }
    const message =
      payload?.error ||
      (payload?.status === "timeout" ? `VRCForge runtime startup timed out. Logs: ${payload?.logDir || "unknown"}` : "");
    if (message) {
      rejectBackendReady(message);
      if (isRuntimeSessionVerificationError(message)) {
        handleRuntimeSessionFailure(message);
      } else {
        setError(message);
        setStartupIssue(message);
      }
    }
  }

  function handleRuntimeSessionFailure(message: string) {
    setAppSessionToken("");
    setBootstrap(null);
    setStartupIssue(message);
    setError(message);
    setBackendMessage("session_mismatch");
  }

  async function startRuntime(options: { waitForBootstrap?: boolean } = {}): Promise<string | null> {
    if (runtimeStartingRef.current) {
      if (options.waitForBootstrap ?? true) {
        try {
          const readyEndpoint = await waitForBackendReady();
          await refreshWithRetry(readyEndpoint, { refreshProjects: true });
          return readyEndpoint;
        } catch (cause) {
          const message = cause instanceof Error ? cause.message : String(cause);
          setError(message);
          setStartupIssue(message);
          return null;
        }
      }
      return endpoint;
    }
    const waitForBootstrap = options.waitForBootstrap ?? true;
    runtimeStartingRef.current = true;
    setLoading(true);
    setError("");
    let targetEndpoint = endpoint;
    try {
      if (isTauriRuntime()) {
        void invoke("ensure_agent_notes_file").catch(() => undefined);
        const startedAt = performance.now();
        startupLaunchStartedAtRef.current = startedAt;
        backendReadyStatusRef.current = "starting";
        const result = await invoke<BackendStartResult>("start_backend");
        const metrics = ((window as any).__vrcforgeStartupMetrics ||= {});
        metrics.startBackendInvokeMs = Math.round(performance.now() - startedAt);
        metrics.startBackendMode = result.mode;
        metrics.startBackendStarted = result.started;
        metrics.startBackendAlreadyRunning = result.already_running;
        targetEndpoint = result.endpoint;
        backendReadyEndpointRef.current = targetEndpoint;
        setAppSessionToken("");
        setEndpoint(targetEndpoint);
        setBackendMessage(result.message);
        if (result.mode === "starting") {
          setStartupIssue("");
          if (waitForBootstrap) {
            await waitForBackendReady(targetEndpoint);
            await refreshWithRetry(targetEndpoint, { refreshProjects: true });
          }
        } else {
          resolveBackendReady(targetEndpoint, result.mode);
          await refreshWithRetry(targetEndpoint, { refreshProjects: true });
        }
      } else {
        setBackendMessage("dev");
        try {
          const session = await fetchAppSession(targetEndpoint);
          setAppSessionToken(session.appSessionToken || session.app_session_token || "");
        } catch {
          setAppSessionToken("");
        }
        await refreshWithRetry(targetEndpoint, { refreshProjects: true });
      }
      return targetEndpoint;
    } catch (cause) {
      const message = cause instanceof Error ? cause.message : String(cause);
      if (isRuntimeSessionVerificationError(message)) {
        handleRuntimeSessionFailure(message);
      } else {
        setError(message);
        setStartupIssue(message);
      }
      return null;
    } finally {
      runtimeStartingRef.current = false;
      setLoading(false);
    }
  }

  async function refresh(target = endpoint, options: { refreshProjects?: boolean } = {}) {
    setError("");
    const payload = await fetchBootstrap(target, options);
    setBootstrap(payload);
    setStartupIssue("");
  }

  async function refreshSilently(target = endpoint) {
    try {
      const payload = await fetchBootstrap(target);
      setBootstrap(payload);
      setStartupIssue("");
      setError((current) => (current.toLowerCase().includes("fetch") ? "" : current));
    } catch (cause) {
      const message = cause instanceof Error ? cause.message : String(cause);
      if (isRuntimeSessionVerificationError(message)) {
        handleRuntimeSessionFailure(message);
      }
      // Keep the current UI usable; explicit retry remains available.
    }
  }

  async function refreshFullHealth(target = endpoint) {
    if (healthRefreshInFlightRef.current) {
      return;
    }
    healthRefreshInFlightRef.current = true;
    try {
      const health = await fetchAppHealth(target);
      setBootstrap((current) => (current ? { ...current, health } : current));
    } catch {
      // Full diagnostics are secondary; bootstrap keeps the chat surface usable.
    } finally {
      healthRefreshInFlightRef.current = false;
    }
  }

  async function refreshProjectList(target = endpoint) {
    if (!runtimeConnected || loadingProjects) {
      return;
    }
    setLoadingProjects(true);
    try {
      const projectsPayload = await refreshProjects(target);
      setBootstrap((current) =>
        current
          ? {
              ...current,
              health: {
                ...current.health,
                projects: projectsPayload,
              },
            }
          : current,
      );
      setError((current) => (current.toLowerCase().includes("project") ? "" : current));
    } catch (cause) {
      setError(cause instanceof Error ? cause.message : String(cause));
    } finally {
      setLoadingProjects(false);
    }
  }

  async function refreshUnityStatus(target = endpoint) {
    if (!runtimeConnected || loadingUnityStatus) {
      return;
    }
    setLoadingUnityStatus(true);
    try {
      const payload = await refreshUnityReadiness(target);
      setBootstrap((current) => (current ? { ...current, health: payload.health } : current));
      setWorkspaceStateError("");
      setError((current) => (current.toLowerCase().includes("unity") ? "" : current));
    } catch (cause) {
      const message = cause instanceof Error ? cause.message : String(cause);
      setWorkspaceStateError(message);
      setError(message);
    } finally {
      setLoadingUnityStatus(false);
    }
  }

  async function refreshWorkspaceDiff(showLoading = true, includePatch = workspaceDiffReviewOpen) {
    if (!runtimeConnected) {
      return;
    }
    if (showLoading) {
      setLoadingWorkspaceDiff(true);
    }
    if (includePatch) {
      setLoadingWorkspaceDiffPatch(true);
    }
    try {
      let payload = await fetchWorkspaceDiff(endpoint, activeProjectPath, includePatch);
      if (!payload.ok && payload.status === "not_git" && activeProjectPath) {
        payload = await fetchWorkspaceDiff(endpoint, "", includePatch);
        setRuntimeNotice(t("notice.projectNotGit"));
      }
      setWorkspaceDiff(payload);
      setWorkspaceDiffError(payload.ok ? "" : payload.error || t("workspace.diffUnavailable"));
    } catch (cause) {
      setWorkspaceDiffError(cause instanceof Error ? cause.message : String(cause));
    } finally {
      if (showLoading) {
        setLoadingWorkspaceDiff(false);
      }
      if (includePatch) {
        setLoadingWorkspaceDiffPatch(false);
      }
    }
  }

  async function refreshRuntimeRuns(showError = false, target = endpoint) {
    const refreshSeq = runtimeRefreshSeqRef.current + 1;
    runtimeRefreshSeqRef.current = refreshSeq;
    const requestSessionId = sessionId;
    const requestProjectRoot = activeRuntimeProjectPath || "";
    const isLatestRefresh = () =>
      runtimeRefreshSeqRef.current === refreshSeq &&
      requestSessionId === sessionId &&
      requestProjectRoot === (activeRuntimeProjectPath || "");
    if (!runtimeConnected) {
      setRuntimeRuns([]);
      setRuntimeRunsError("");
      setDesktopActions([]);
      setAgentGoals([]);
      setAgentMemory([]);
      setAgentApprovals(null);
      setWorkspaceStateError("");
      return;
    }
    try {
      const projectRoot = requestProjectRoot || undefined;
      const snapshot = await fetchDesktopRuntimeSnapshot(target, {
        sessionId: requestSessionId || undefined,
        projectRoot,
        globalOnly: !projectRoot,
        includePatch: workspaceDiffReviewOpen,
      });
      if (!isLatestRefresh()) {
        return;
      }
      if (snapshot.workspaceDiff) {
        setWorkspaceDiff(snapshot.workspaceDiff);
        setWorkspaceDiffError(snapshot.workspaceDiff.ok ? "" : snapshot.workspaceDiff.error || t("workspace.diffUnavailable"));
        if (snapshot.workspaceDiff.fallbackFromProjectRoot) {
          setRuntimeNotice(t("notice.projectNotGit"));
        }
      }
      setAgentApprovals(snapshot.approvals?.approvals ?? []);
      setRuntimeRuns(snapshot.runs?.runs ?? []);
      setDesktopActions(snapshot.desktopActions?.actions ?? []);
      setAgentGoals(snapshot.goals?.goals ?? []);
      setAgentMemory(snapshot.memory?.memories ?? []);
      setRuntimeRunsError("");
      setWorkspaceStateError("");
    } catch (cause) {
      if (!isLatestRefresh()) {
        return;
      }
      const message = cause instanceof Error ? cause.message : String(cause);
      setRuntimeRunsError(message);
      setWorkspaceStateError(message);
      if (showError) {
        setRuntimeNotice(message);
      }
    }
  }

  function toggleWorkspaceDiffReview() {
    setWorkspaceDiffReviewOpen((open) => {
      const next = !open;
      if (next && runtimeConnected && !workspaceDiff?.patch) {
        void refreshWorkspaceDiff(false, true);
      }
      return next;
    });
  }

  async function refreshWithRetry(target = endpoint, options: { refreshProjects?: boolean } = {}) {
    let lastError: unknown = null;
    for (let attempt = 0; attempt < 16; attempt += 1) {
      try {
        await refresh(target, options);
        return;
      } catch (cause) {
        lastError = cause;
        await new Promise((resolve) => window.setTimeout(resolve, 450));
      }
    }
    throw lastError instanceof Error ? lastError : new Error(String(lastError || "Failed to fetch runtime bootstrap."));
  }

  async function switchMode(mode: PermissionState["executionMode"], acknowledge = false) {
    if (!permission) {
      return;
    }
    setLoading(true);
    setError("");
    try {
      const payload = await updatePermission(endpoint, mode, acknowledge);
      setBootstrap((current) => (current ? { ...current, permission: payload.permission } : current));
      void refreshSilently();
    } catch (cause) {
      setError(cause instanceof Error ? cause.message : String(cause));
    } finally {
      setLoading(false);
    }
  }

  function updateChat(chatId: string, updater: (chat: ChatThread) => ChatThread) {
    markChatsDirty();
    setChats((list) => list.map((chat) => (chat.id === chatId ? updater(chat) : chat)));
  }

  function markChatsDirty() {
    chatsDirtyRef.current = true;
    chatsSaveVersionRef.current += 1;
  }

  function touchChat(chat: ChatThread, timestamp = new Date().toISOString()): ChatThread {
    return { ...chat, createdAt: chat.createdAt || timestamp, updatedAt: timestamp };
  }

  function appendToChat(chatId: string, item: ConversationItem) {
    updateChat(chatId, (chat) => touchChat({ ...chat, items: [...chat.items, item] }));
  }

  function applyRuntimeDelta(delta: AgentRuntimeDeltaEvent) {
    const clientTurnId = String(delta.clientTurnId || "").trim();
    if (!clientTurnId || !delta.textDelta) {
      return;
    }
    markChatsDirty();
    setChats((list) =>
      list.map((chat) => {
        if (streamingTurnChatRef.current.get(clientTurnId) !== chat.id) {
          return chat;
        }
        const index = chat.items.findIndex((item) => item.type === "streaming" && item.clientTurnId === clientTurnId);
        if (index < 0) {
          return chat;
        }
        const items = [...chat.items];
        const item = items[index];
        if (!item || item.type !== "streaming") {
          return chat;
        }
        items[index] = { ...item, text: `${item.text}${delta.textDelta}` };
        return { ...chat, items };
      }),
    );
  }

  function pendingApprovalForResponse(response: AgentRuntimeResponse): AgentApproval | null {
    const approvalId = approvalIdFromResponse(response);
    if (approvalId) {
      const pending = pendingApprovalItems.find((approval) => approval.id === approvalId);
      if (pending) {
        return pending;
      }
    }
    const shellApproval = response.shell?.approval;
    if (shellApproval?.status === "pending") {
      return shellApproval;
    }
    return null;
  }

  async function modifyApprovalInComposer(approval: AgentApproval) {
    const target = approval.targetTool || approval.preview?.command || t("approval.thisApproval");
    const detail = approval.paramsSummary || approval.arguments || approval.preview || {};
    const approvalContext = [
      `${t("approval.contextPending")}: ${approval.id}`,
      `${t("approval.contextTarget")}: ${target}`,
      approval.reason ? `${t("approval.contextReason")}: ${approval.reason}` : "",
      `${t("approval.contextDetails")}:\n${formatPayload(detail)}`,
    ]
      .filter(Boolean)
      .join("\n\n");
    setInput((current) => {
      const prefix = current.trim() ? `${current.trimEnd()}\n\n` : "";
      return `${prefix}${t("approval.modifyPrompt", { id: approval.id, target })}\n`;
    });
    setAttachments((current) => [...current, textContextAttachment(t("approval.pendingContextTitle"), approvalContext)].slice(0, MAX_ATTACHMENTS_PER_TURN));
    setRuntimeNotice(t("approval.modifyNotice"));
    setApprovalActions((current) => ({ ...current, [approval.id]: "modify" }));
    setError("");
    const approvalScope = { expectedProjectRoot: activeRuntimeProjectPath || undefined, globalOnly: !activeRuntimeProjectPath };
    try {
      await requestApprovalRevision(endpoint, approval.id, {
        reason: t("approval.revisionReason"),
        note: t("approval.revisionNote", { id: approval.id, target }),
        ...approvalScope,
      });
      await refresh();
      await refreshRuntimeRuns(false);
    } catch (cause) {
      setError(cause instanceof Error ? cause.message : String(cause));
    } finally {
      setApprovalActions((current) => {
        const next = { ...current };
        delete next[approval.id];
        return next;
      });
    }
  }

  function copyConversationItem(item: ConversationItem) {
    const text = conversationItemText(item, t);
    if (!text.trim()) {
      return;
    }
    void navigator.clipboard?.writeText(text).catch(() => undefined);
    setRuntimeNotice(t("chat.copiedMessage"));
  }

  function setConversationFeedback(itemId: string, value: MessageFeedback) {
    setMessageFeedback((current) => {
      const next = { ...current };
      if (next[itemId] === value) {
        delete next[itemId];
      } else {
        next[itemId] = value;
      }
      return next;
    });
  }

  function editConversationMessage(itemId: string) {
    const chat = chatsRef.current.find((item) => item.id === activeChatId);
    if (!chat) {
      return;
    }
    if (latestConversationItemId(chat.items, (item) => item.type === "user") !== itemId) {
      setError(t("chat.latestMessageActionOnly", { defaultValue: "Only the latest message can be changed." }));
      return;
    }
    const index = chat.items.findIndex((item) => item.id === itemId);
    const item = index >= 0 ? chat.items[index] : null;
    if (!item || item.type !== "user") {
      return;
    }
    setInput(item.text);
    setAttachments(cloneChatAttachments(item.attachments || []));
    updateChat(chat.id, (current) => ({
      ...touchChat(current),
      sessionId: "",
      items: current.items.slice(0, index),
    }));
    setRuntimeNotice(t("chat.editingMessage"));
  }

  function retryConversationItem(itemId: string) {
    if (sendingRef.current) {
      setError(t("chat.cannotActionWhileRunning"));
      return;
    }
    const chat = chatsRef.current.find((item) => item.id === activeChatId);
    if (!chat) {
      return;
    }
    const index = chat.items.findIndex((item) => item.id === itemId);
    if (index < 0) {
      return;
    }
    if (latestConversationItemId(chat.items, isRetryableConversationItem) !== itemId) {
      setError(t("chat.latestMessageActionOnly", { defaultValue: "Only the latest message can be changed." }));
      return;
    }
    let userIndex = chat.items[index].type === "user" ? index : -1;
    if (userIndex < 0) {
      for (let cursor = index - 1; cursor >= 0; cursor -= 1) {
        if (chat.items[cursor].type === "user") {
          userIndex = cursor;
          break;
        }
      }
    }
    const userItem = userIndex >= 0 ? chat.items[userIndex] : null;
    if (!userItem || userItem.type !== "user") {
      setError(t("chat.noPreviousUserMessage"));
      return;
    }
    const turn: QueuedTurn = {
      id: `retry-${Date.now()}-${Math.random().toString(16).slice(2)}`,
      text: userItem.text,
      attachments: cloneChatAttachments(userItem.attachments || []),
      providerLabel: providerSnapshot.providerLabel,
      model: providerSnapshot.model,
    };
    void runTurnNow(chat.id, turn, {
      baseItems: chat.items.slice(0, userIndex),
      sessionId: "",
    });
  }

  function toggleRightRuntimeSection(section: string) {
    setRightRuntimeSectionsCollapsed((current) => ({ ...current, [section]: !current[section] }));
  }

  async function runExplicitWorkspaceAction(actionId: ComposerActionId) {
    const action = composerActions.find((item) => item.id === actionId);
    setRightSidebarCollapsed(false);
    setRuntimeNotice("");
    if (!action) {
      return;
    }
    if (action.disabled) {
      const reason = action.disabledReason || t("notice.actionUnavailable", { action: action.label });
      setRuntimeNotice(reason);
      setError(reason);
      return;
    }
    const desktopAction =
      actionId === "desktop"
        ? "computer_use"
        : actionId === "screenshot" || actionId === "annotation" || actionId === "browser"
          ? actionId
          : "";
    if (desktopAction) {
      try {
        const payload = await requestAgentDesktopAction(endpoint, {
          action: desktopAction,
          prompt: input.trim(),
          sessionId: sessionId || undefined,
          clientTurnId: currentTurn?.clientTurnId,
          projectPath: activeRuntimeProjectPath || undefined,
          projectRoot: activeRuntimeProjectPath || undefined,
          params: desktopAction === "screenshot" ? { projectPath: activeRuntimeProjectPath || undefined } : {},
        });
        const message =
          payload.status === "executed"
            ? t("notice.desktopActionExecuted", { action: action.label })
            : payload.error || t("notice.desktopActionRecorded", { action: action.label, status: payload.status || "recorded" });
        setRuntimeNotice(message);
        if (payload.event) {
          setDesktopActions((items) => [payload.event as AgentDesktopAction, ...items].slice(0, 8));
        }
        void refreshRuntimeRuns(false);
      } catch (cause) {
        const message = cause instanceof Error ? cause.message : String(cause);
        setRuntimeNotice(message);
        setError(message);
      }
    }
  }

  function ensureActiveChat(): string {
    if (activeChat) {
      return activeChat.id;
    }
    const id = `chat-${Date.now()}`;
    const now = new Date().toISOString();
    markChatsDirty();
    setChats((list) => [{ id, sessionId: "", title: "", projectPath: activeProjectPath, createdAt: now, updatedAt: now, items: [] }, ...list]);
    setActiveChatId(id);
    return id;
  }

  async function compactChat() {
    if (!activeChat || activeChat.items.length === 0) {
      setError(t("compact.noContent"));
      return;
    }
    const chatId = activeChat.id;
    const items = activeChat.items;
    setSending(true);
    let summary = "";
    try {
      let targetEndpoint = endpoint;
      if (!runtimeConnected) {
        const readyEndpoint = await startRuntime();
        if (readyEndpoint) {
          targetEndpoint = readyEndpoint;
        }
      }
      const payload = await compactAgentHistory(targetEndpoint, buildChatHistory(items, t));
      summary = (payload.summary || "").trim();
      if (summary) {
        summary = `${t("compact.modelSummary", { count: payload.entryCount ?? items.length })}\n${summary}`;
      }
    } catch {
      summary = "";
    } finally {
      setSending(false);
    }
    if (!summary) {
      summary = buildCompactSummary(items, t);
    }
    updateChat(chatId, (chat) => ({
      ...touchChat(chat),
      sessionId: "",
      items: [{ id: `compact-${Date.now()}`, type: "compact", text: summary }],
    }));
  }

  async function createGoalFromSlash(raw: string) {
    const title = raw.replace(/^\/goal\s*/i, "").trim();
    if (!title) {
      setError(t("goal.empty"));
      return;
    }
    try {
      const payload = await createAgentGoal(endpoint, {
        title,
        sessionId: sessionId || undefined,
        projectPath: activeRuntimeProjectPath || undefined,
        projectRoot: activeRuntimeProjectPath || undefined,
      });
      setAgentGoals((items) => [payload.goal, ...items.filter((item) => item.goalId !== payload.goal.goalId)].slice(0, 8));
      setRuntimeNotice(t("goal.created"));
      setInput("");
    } catch (cause) {
      const message = cause instanceof Error ? cause.message : String(cause);
      setError(message);
    }
  }

  async function createMemoryFromSlash(raw: string) {
    const text = raw.replace(/^\/memory\s*/i, "").trim();
    if (!text) {
      setError(t("memory.empty"));
      return;
    }
    try {
      const payload = await createAgentMemory(endpoint, {
        text,
        scope: activeRuntimeProjectPath ? "project" : "user",
        kind: "preference",
        source: "slash",
        projectPath: activeRuntimeProjectPath || undefined,
        projectRoot: activeRuntimeProjectPath || undefined,
      });
      setAgentMemory((items) => [payload.memory, ...items.filter((item) => item.memoryId !== payload.memory.memoryId)].slice(0, 8));
      setRuntimeNotice(t("memory.created"));
      setInput("");
    } catch (cause) {
      const message = cause instanceof Error ? cause.message : String(cause);
      setError(message);
    }
  }

  async function submitMessage(event?: FormEvent) {
    event?.preventDefault();
    const message = input.trim();
    if (!message && attachments.length === 0) {
      return;
    }
    setError("");
    if (!chatAvailable) {
      setError(chatDisabledReason || t("chat.connectProviderBeforeSend"));
      return;
    }
    if (message === "/compact" || message.startsWith("/compact ")) {
      void compactChat();
      setInput("");
      return;
    }
    if (message === "/goal" || message.startsWith("/goal ")) {
      void createGoalFromSlash(message);
      return;
    }
    if (message === "/memory" || message.startsWith("/memory ")) {
      void createMemoryFromSlash(message);
      return;
    }
    if (message === "/delegate" || message.startsWith("/delegate ")) {
      const task = message.replace(/^\/delegate\s*/i, "").trim();
      void startSubAgentTask(undefined, task || undefined);
      setInput("");
      return;
    }
    if (message === "/desktop" || message.startsWith("/desktop ")) {
      await runExplicitWorkspaceAction("desktop");
      setInput("");
      return;
    }
    const turn: QueuedTurn = {
      id: `turn-${Date.now()}-${Math.random().toString(16).slice(2)}`,
      text: message,
      attachments,
      providerLabel: providerSnapshot.providerLabel,
      model: providerSnapshot.model,
    };
    setInput("");
    setAttachments([]);
    if (sendingRef.current) {
      // Running turns queue follow-up messages in FIFO order.
      if (queueRef.current.length >= MAX_QUEUED_TURNS) {
        setError(t("chat.queueFull", { max: MAX_QUEUED_TURNS }));
        setInput(message);
        setAttachments(turn.attachments);
        return;
      }
      const queuedTurn = { ...turn, queuedFrom: true };
      queueRef.current.push(queuedTurn);
      setQueued([...queueRef.current]);
      void recordAgentRunQueued(endpoint, {
        sessionId: sessionId || undefined,
        clientTurnId: turn.id,
        message: turn.text,
        attachments: serializeChatAttachments(turn.attachments),
        provider: providerSnapshot.provider,
        providerLabel: turn.providerLabel,
        model: turn.model,
        projectPath: activeRuntimeProjectPath || undefined,
        projectRoot: activeRuntimeProjectPath || undefined,
      })
        .then(() => refreshRuntimeRuns(false))
        .catch(() => undefined);
      return;
    }
    const chatId = ensureActiveChat();
    sendingRef.current = true;
    setSending(true);
    setStopRequested(false);
    stopRequestedRef.current = false;
    try {
      let next: QueuedTurn | undefined = turn;
      while (next !== undefined) {
        await runSingleTurn(chatId, next);
        if (stopRequestedRef.current) {
          queueRef.current = [];
          break;
        }
        next = queueRef.current.shift();
        setQueued([...queueRef.current]);
      }
    } finally {
      queueRef.current = [];
      setQueued([]);
      sendingRef.current = false;
      setSending(false);
      setStopRequested(false);
      stopRequestedRef.current = false;
    }
  }

  async function runTurnNow(chatId: string, turn: QueuedTurn, options?: RunSingleTurnOptions) {
    if (sendingRef.current) {
      setError(t("chat.cannotActionWhileRunning"));
      return;
    }
    sendingRef.current = true;
    setSending(true);
    setStopRequested(false);
    stopRequestedRef.current = false;
    try {
      await runSingleTurn(chatId, turn, options);
    } finally {
      queueRef.current = [];
      setQueued([]);
      sendingRef.current = false;
      setSending(false);
      setStopRequested(false);
      stopRequestedRef.current = false;
    }
  }

  async function runSingleTurn(chatId: string, turn: QueuedTurn, options?: RunSingleTurnOptions) {
    const chat = chatsRef.current.find((item) => item.id === chatId);
    const baseItems = options?.baseItems ?? chat?.items ?? [];
    const chatSessionId = options?.sessionId ?? chat?.sessionId ?? "";
    const chatAgentName = chat?.agentName || "desktop-agent";
    const history = baseItems.length > 0 ? buildChatHistory(baseItems, t) : [];
    const startedAt = Date.now();
    const messageForModel = appendAttachmentSummary(turn.text, turn.attachments, t);
    const abortController = new AbortController();
    let userItemId = "";
    activeTurnAbortRef.current = abortController;
    setCurrentTurn({ clientTurnId: turn.id, text: turn.text, startedAt, providerLabel: turn.providerLabel, model: turn.model });
    try {
      let targetEndpoint = endpoint;
      if (!runtimeConnected) {
        const readyEndpoint = await startRuntime();
        if (!readyEndpoint) {
          throw new Error(t("agent.coreDisconnectedSend"));
        }
        targetEndpoint = readyEndpoint;
      }
      const userItem: ConversationItem = { id: `user-${Date.now()}`, type: "user", text: turn.text, attachments: turn.attachments, queuedFrom: Boolean(turn.queuedFrom) };
      userItemId = userItem.id;
      const streamingItem: ConversationItem = {
        id: `stream-${turn.id}`,
        type: "streaming",
        clientTurnId: turn.id,
        text: "",
        providerLabel: turn.providerLabel,
        model: turn.model,
      };
      const message = turn.text;
      streamingTurnChatRef.current.set(turn.id, chatId);
      updateChat(chatId, (current) => ({
        ...touchChat(current),
        sessionId: options?.sessionId ?? current.sessionId,
        title: current.title || (message.length > 24 ? `${message.slice(0, 24)}...` : message),
        items: [...(options?.baseItems ?? current.items), userItem, streamingItem],
      }));
      const response = await sendAgentMessage(targetEndpoint, messageForModel, chatSessionId || undefined, history, chatAgentName, {
        signal: abortController.signal,
        attachments: serializeChatAttachments(turn.attachments),
        projectPath: chat?.projectPath || activeRuntimeProjectPath || undefined,
        provider: providerSnapshot.provider,
        providerLabel: turn.providerLabel,
        model: turn.model,
        clientTurnId: turn.id,
      });
      const elapsedSeconds = Math.max(1, Math.round((Date.now() - startedAt) / 1000));
      updateChat(chatId, (current) => ({
        ...touchChat(current),
        sessionId: response.sessionId || response.session_id || current.sessionId,
        items: [
          ...current.items.filter((item) => !(item.type === "streaming" && item.clientTurnId === turn.id)),
          { id: response.turnId || response.turn_id, type: "agent", response, elapsedSeconds, providerLabel: turn.providerLabel, model: turn.model },
        ],
      }));
      await refresh(targetEndpoint);
      await refreshRuntimeRuns(false, targetEndpoint);
    } catch (cause) {
      const text = cause instanceof Error ? cause.message : String(cause);
      if (userItemId && text.toLowerCase().includes("cancel")) {
        updateChat(chatId, (current) => ({
          ...touchChat(current),
          sessionId: "",
          items: current.items.filter((item) => item.id !== userItemId),
        }));
      }
      appendToChat(chatId, { id: `error-${Date.now()}`, type: "error", text });
      updateChat(chatId, (current) => ({
        ...touchChat(current),
        items: current.items.filter((item) => !(item.type === "streaming" && item.clientTurnId === turn.id)),
      }));
      if (isRuntimeSessionVerificationError(text)) {
        handleRuntimeSessionFailure(text);
      } else {
        setError(text);
      }
    } finally {
      if (activeTurnAbortRef.current === abortController) {
        activeTurnAbortRef.current = null;
      }
      streamingTurnChatRef.current.delete(turn.id);
      setCurrentTurn(null);
    }
  }

  function stopCurrentRun() {
    stopRequestedRef.current = true;
    setStopRequested(true);
    queueRef.current = [];
    setQueued([]);
    const current = currentTurn;
    if (current?.clientTurnId || sessionId) {
      void requestAgentRunCancel(endpoint, {
        sessionId: sessionId || undefined,
        clientTurnId: current?.clientTurnId,
        reason: "user_stop",
      })
        .then(() => refreshRuntimeRuns(false))
        .catch(() => undefined);
    }
    activeTurnAbortRef.current?.abort();
  }

  async function addComposerFiles(files: FileList | null) {
    if (!files || files.length === 0) {
      return;
    }
    const remaining = Math.max(0, MAX_ATTACHMENTS_PER_TURN - attachments.length);
    if (remaining === 0) {
      setError(t("attachments.limitReached", { max: MAX_ATTACHMENTS_PER_TURN }));
      return;
    }
    const selected = Array.from(files).slice(0, remaining);
    const nextAttachments = await Promise.all(selected.map((file) => readChatAttachment(file, t)));
    setAttachments((current) => [...current, ...nextAttachments].slice(0, MAX_ATTACHMENTS_PER_TURN));
    if (files.length > remaining) {
      setError(t("attachments.limitOneTurn", { max: MAX_ATTACHMENTS_PER_TURN }));
    }
  }

  function removeAttachment(id: string) {
    setAttachments((current) => current.filter((attachment) => attachment.id !== id));
  }

  async function approveShell(approvalId: string) {
    setApprovalActions((current) => ({ ...current, [approvalId]: "approve" }));
    setError("");
    const approvalScope = { expectedProjectRoot: activeRuntimeProjectPath || undefined, globalOnly: !activeRuntimeProjectPath };
    try {
      const payload = await approveAgentApproval(endpoint, approvalId, approvalScope);
      const executionResult = payload.execution?.result;
      const shellResult = isAgentShellResult(executionResult) ? executionResult : undefined;
      if (activeChatId && (shellResult || payload.execution?.error)) {
        appendToChat(activeChatId, {
          id: `result-${approvalId}-${Date.now()}`,
          type: "result",
          approvalId,
          result: shellResult,
          error: payload.execution?.error,
        });
      }
      await refresh();
      await refreshRuntimeRuns(false);
      const executionRecord = asRecord(payload.execution);
      const executionTargetTool = typeof executionRecord?.targetTool === "string" ? executionRecord.targetTool : "";
      const executionResultRecord = asRecord(executionResult);
      const resolvedRecoveries = executionResultRecord?.resolvedApplyRecoveries;
      const shouldRefreshCheckpoints =
        activeView === "checkpoints" ||
        executionTargetTool === "vrcforge_restore_checkpoint" ||
        executionTargetTool === "vrcforge_resolve_interrupted_apply_recovery" ||
        Array.isArray(resolvedRecoveries);
      if (shouldRefreshCheckpoints) {
        await loadCheckpoints();
      }
    } catch (cause) {
      setError(cause instanceof Error ? cause.message : String(cause));
    } finally {
      setApprovalActions((current) => {
        const next = { ...current };
        delete next[approvalId];
        return next;
      });
    }
  }

  async function rejectShell(approvalId: string) {
    setApprovalActions((current) => ({ ...current, [approvalId]: "reject" }));
    setError("");
    const approvalScope = { expectedProjectRoot: activeRuntimeProjectPath || undefined, globalOnly: !activeRuntimeProjectPath };
    try {
      await rejectAgentApproval(endpoint, approvalId, approvalScope);
      if (activeChatId) {
        appendToChat(activeChatId, {
          id: `result-${approvalId}-${Date.now()}`,
          type: "result",
          approvalId,
          error: "rejected",
        });
      }
      await refresh();
      await refreshRuntimeRuns(false);
    } catch (cause) {
      setError(cause instanceof Error ? cause.message : String(cause));
    } finally {
      setApprovalActions((current) => {
        const next = { ...current };
        delete next[approvalId];
        return next;
      });
    }
  }

  function newConversation(projectPath?: string) {
    setActiveView("chat");
    if (projectPath !== undefined) {
      setActiveProjectPath(projectPath);
    }
    setActiveChatId("");
    setError("");
  }

  function togglePinChat(chatId: string) {
    updateChat(chatId, (chat) => ({ ...chat, pinned: !chat.pinned }));
  }

  function startRenameChat(chat: ChatThread) {
    setRenamingChatId(chat.id);
    setRenameDraft(chat.title || "");
  }

  function commitRenameChat(cancel = false) {
    if (!cancel && renamingChatId) {
      const title = renameDraft.trim();
      if (title) {
        updateChat(renamingChatId, (chat) => ({ ...chat, title }));
      }
    }
    setRenamingChatId("");
    setRenameDraft("");
  }

  function deleteChatPermanently(chatId: string) {
    markChatsDirty();
    setChats((list) => list.filter((chat) => chat.id !== chatId));
    if (activeChatId === chatId) {
      setActiveChatId("");
    }
    setDeleteTargetId("");
    setChatMenu(null);
  }

  function bindProject(projectPath: string) {
    setActiveProjectPath(projectPath);
    if (activeChatId) {
      updateChat(activeChatId, (chat) => ({ ...chat, projectPath }));
    }
  }

  function newTemporaryChat() {
    setActiveView("chat");
    setActiveProjectPath("");
    setError("");
    // 折叠状态下新建临时对话自动展开，避免「点了没反应」的错觉。
    setCollapsedProjects((map) => (map[TEMP_CHATS_COLLAPSE_KEY] ? { ...map, [TEMP_CHATS_COLLAPSE_KEY]: false } : map));
    const existingEmpty = chats.find((chat) => !chat.projectPath && !chat.archived && chat.items.length === 0);
    if (existingEmpty) {
      setActiveChatId(existingEmpty.id);
      return;
    }
    const id = `chat-${Date.now()}`;
    const now = new Date().toISOString();
    markChatsDirty();
    setChats((list) => [{ id, sessionId: "", title: "", projectPath: "", createdAt: now, updatedAt: now, items: [] }, ...list]);
    setActiveChatId(id);
  }

  async function loadSubAgents(includeEvents = false) {
    if (!runtimeConnected && !includeEvents) {
      return;
    }
    setLoadingSubAgents(true);
    try {
      const payload = await fetchSubAgents(endpoint, includeEvents);
      setSubAgentList(payload);
      setSubAgentError("");
    } catch (cause) {
      setSubAgentError(cause instanceof Error ? cause.message : String(cause));
    } finally {
      setLoadingSubAgents(false);
    }
  }

  async function startSubAgentTask(roleOverride?: string, taskOverride?: string) {
    const agentName = pickSubAgentName();
    const projectPath = activeChat?.projectPath || activeProjectPath;
    const hasPackage = outfitPackagePath.trim().length > 0;
    const role = roleOverride || (hasPackage ? "outfit_import_plan_review" : "project_index_review");
    const defaultTask =
      role === "outfit_import_plan_review"
        ? "Inspect the selected outfit package and return a supervised import plan summary."
        : role === "validation_triage"
          ? "Run read-only validation triage and summarize findings."
          : "Review the local Unity project index and summarize changed scanner families.";
    const task = taskOverride?.trim() || defaultTask;
    setActiveView("chat");
    setError("");
    setSubAgentError("");
    try {
      let targetEndpoint = endpoint;
      if (!runtimeConnected) {
        const readyEndpoint = await startRuntime();
        if (!readyEndpoint) {
          setSubAgentError("Runtime is not connected.");
          return;
        }
        targetEndpoint = readyEndpoint;
      }
      const payload = await createSubAgent(targetEndpoint, {
        role,
        task,
        displayName: agentName,
        parentSessionId: activeChat?.sessionId || "",
        projectPath,
        params: {
          projectPath,
          packagePath: outfitPackagePath.trim(),
        },
      });
      setRightSidebarCollapsed(false);
      setSelectedSubAgent(payload.task);
      setSelectedSubAgentPanelOpen(true);
      setRightRuntimeSectionsCollapsed((current) => ({ ...current, subagents: false }));
      setSubAgentList((current) => updateSubAgentList(current, payload.task));
      void loadSubAgents(false);
    } catch (cause) {
      setSubAgentError(cause instanceof Error ? cause.message : String(cause));
    }
  }

  async function cancelSubAgentTask(taskId: string) {
    try {
      const payload = await cancelSubAgent(endpoint, taskId);
      setSubAgentList((current) => updateSubAgentList(current, payload.task));
    } catch (cause) {
      setSubAgentError(cause instanceof Error ? cause.message : String(cause));
    }
  }

  async function retrySubAgentTask(taskId: string) {
    try {
      const payload = await retrySubAgent(endpoint, taskId);
      setSubAgentList((current) => updateSubAgentList(current, payload.task));
    } catch (cause) {
      setSubAgentError(cause instanceof Error ? cause.message : String(cause));
    }
  }

  async function inspectSubAgentTask(taskId: string) {
    try {
      const payload = await fetchSubAgent(endpoint, taskId);
      setRightSidebarCollapsed(false);
      setSelectedSubAgentPanelOpen(true);
      setRightRuntimeSectionsCollapsed((current) => ({ ...current, subagents: false }));
      setSelectedSubAgent(payload.task);
      setSubAgentList((current) => updateSubAgentList(current, payload.task));
    } catch (cause) {
      setSubAgentError(cause instanceof Error ? cause.message : String(cause));
    }
  }

  function acceptSubAgentSummary(task: SubAgentTask) {
    const chatId = ensureActiveChat();
    setActiveView("chat");
    appendToChat(chatId, {
      id: `subagent-${task.id}-${Date.now()}`,
      type: "subagent",
      task,
    });
  }

  function handleConversationMouseUp() {
    window.setTimeout(() => {
      const selection = window.getSelection();
      const text = selection?.toString().trim() ?? "";
      if (!text || !selection || selection.rangeCount === 0) {
        setSelectionMenu(null);
        return;
      }
      const rect = selection.getRangeAt(0).getBoundingClientRect();
      setSelectionMenu({ x: rect.left + rect.width / 2, y: rect.top, text });
    }, 0);
  }

  function clearSelectionMenu() {
    setSelectionMenu(null);
    window.getSelection()?.removeAllRanges();
  }

  function addSelectionToComposer(text: string) {
    if (attachments.length >= MAX_ATTACHMENTS_PER_TURN) {
      setError(t("attachments.limitReached", { max: MAX_ATTACHMENTS_PER_TURN }));
      clearSelectionMenu();
      return;
    }
    setAttachments((current) => [...current, selectedTextAttachment(text)].slice(0, MAX_ATTACHMENTS_PER_TURN));
    clearSelectionMenu();
  }

  async function openSelectionInSubAgent(text: string) {
    const selectedText = text.trim();
    if (!selectedText) {
      clearSelectionMenu();
      return;
    }
    const projectPath = activeChat?.projectPath ?? activeProjectPath;
    const agentName = pickSubAgentName();
    setActiveView("chat");
    clearSelectionMenu();
    setRightSidebarCollapsed(false);
    setSelectedSubAgentPanelOpen(true);
    setRightRuntimeSectionsCollapsed((current) => ({ ...current, subagents: false }));
    setSubAgentError("");
    try {
      let targetEndpoint = endpoint;
      if (!runtimeConnected) {
        const readyEndpoint = await startRuntime();
        if (!readyEndpoint) {
          setSubAgentError("Runtime is not connected.");
          return;
        }
        targetEndpoint = readyEndpoint;
      }
      const payload = await createSubAgent(targetEndpoint, {
        role: "selected_context_review",
        task: "Review the selected conversation excerpt in a scoped sub-agent thread.",
        displayName: agentName,
        parentSessionId: activeChat?.sessionId || "",
        projectPath,
        params: {
          projectPath,
          selectedText,
          source: "selection-menu",
        },
      });
      setSelectedSubAgent(payload.task);
      setSubAgentList((current) => ({
        ok: true,
        schema: current?.schema || "vrcforge.sub_agent_tasks.v1",
        tasks: [payload.task, ...(current?.tasks || []).filter((taskItem) => taskItem.id !== payload.task.id)],
        count: (current?.count || 0) + 1,
        roles: current?.roles,
        maxConcurrent: current?.maxConcurrent,
        runningCount: (current?.runningCount || 0) + 1,
      }));
      void loadSubAgents(false);
    } catch (cause) {
      setSubAgentError(cause instanceof Error ? cause.message : String(cause));
    }
  }

  function askInNewSession(text: string) {
    void openSelectionInSubAgent(text);
  }

  async function persistProjectPrefs(next: ProjectPrefs): Promise<ProjectPrefs | null> {
    setSavingProjectPrefs(true);
    try {
      const saved = await saveProjectPrefs(endpoint, next);
      setProjectPrefs(saved);
      await refreshSilently();
      return saved;
    } catch (cause) {
      setError(cause instanceof Error ? cause.message : String(cause));
      return null;
    } finally {
      setSavingProjectPrefs(false);
    }
  }

  async function addProjectPath() {
    const path = newProjectPath.trim();
    if (!path || savingProjectPrefs) {
      return;
    }
    setProjectModalError("");
    const saved = await persistProjectPrefs({
      ...projectPrefs,
      customPaths: [...projectPrefs.customPaths, path],
    });
    if (!saved) {
      return;
    }
    const accepted = saved.customPaths.some((item) => item.replace(/\//g, "\\").toLowerCase() === path.replace(/\//g, "\\").toLowerCase());
    if (!accepted) {
      setProjectModalError(t("project.invalidProjectRoot"));
      return;
    }
    setNewProjectPath("");
    setShowProjectModal(false);
    try {
      await refresh();
    } catch {
      // 列表会随下一次轮询更新
    }
    selectProject(path);
  }

  function removeCustomProject(path: string) {
    void persistProjectPrefs({
      ...projectPrefs,
      customPaths: projectPrefs.customPaths.filter((item) => normalizeProjectPathKey(item) !== normalizeProjectPathKey(path)),
    });
  }

  function hideProject(path: string) {
    if (!path) {
      return;
    }
    void persistProjectPrefs({
      ...projectPrefs,
      hiddenPaths: [...projectPrefs.hiddenPaths.filter((item) => normalizeProjectPathKey(item) !== normalizeProjectPathKey(path)), path],
    });
    if (normalizeProjectPathKey(activeProjectPath) === normalizeProjectPathKey(path)) {
      newConversation("");
    }
  }

  function unhideProject(path: string) {
    void persistProjectPrefs({
      ...projectPrefs,
      hiddenPaths: projectPrefs.hiddenPaths.filter((item) => normalizeProjectPathKey(item) !== normalizeProjectPathKey(path)),
    });
  }

  function projectDisplayName(project?: { path?: string; name?: string }): string {
    if (!project) {
      return "";
    }
    const key = projectKey(project);
    return projectUiPrefs.aliases[normalizeProjectPathKey(key)] || project.name || project.path || "Unity Project";
  }

  function togglePinProject(path: string) {
    const key = normalizeProjectPathKey(path);
    if (!key) {
      return;
    }
    setProjectUiPrefs((current) => {
      const pinned = new Set(current.pinnedPaths.map(normalizeProjectPathKey));
      if (pinned.has(key)) {
        pinned.delete(key);
      } else {
        pinned.add(key);
      }
      return { ...current, pinnedPaths: Array.from(pinned) };
    });
  }

  function startRenameProject(path: string) {
    setRenamingProjectPath(path);
    const project = projectItems.find((item) => normalizeProjectPathKey(projectKey(item)) === normalizeProjectPathKey(path));
    setProjectRenameDraft(projectDisplayName(project) || shortPath(path));
  }

  function commitRenameProject(cancel = false) {
    if (!cancel && renamingProjectPath) {
      const key = normalizeProjectPathKey(renamingProjectPath);
      const title = projectRenameDraft.trim();
      setProjectUiPrefs((current) => {
        const aliases = { ...current.aliases };
        if (title) {
          aliases[key] = title;
        } else {
          delete aliases[key];
        }
        return { ...current, aliases };
      });
    }
    setRenamingProjectPath("");
    setProjectRenameDraft("");
  }

  function resolveOpenableProjectPath(path: string): string {
    const raw = path.trim();
    const normalized = normalizeProjectPathKey(raw);
    const candidates: string[] = [raw];
    const pushCandidate = (candidate?: string) => {
      const value = (candidate || "").trim();
      if (value && !candidates.some((item) => normalizeProjectPathKey(item) === normalizeProjectPathKey(value))) {
        candidates.push(value);
      }
    };
    const matchingProject = projectItems.find((project) => {
      const identifiers = [project.path, project.name, projectKey(project), projectDisplayName(project)];
      return identifiers.some((identifier) => normalizeProjectPathKey(identifier || "") === normalized);
    });
    pushCandidate(matchingProject?.path);
    if (normalized && normalized === normalizeProjectPathKey(activeProjectPath)) {
      pushCandidate(activeProjectPath);
    }
    if (!isAbsoluteLocalPath(raw)) {
      const activeMcpProjects = projectItems.filter((project) => Boolean((project as { activeMcp?: boolean }).activeMcp && project.path));
      if (activeMcpProjects.length === 1) {
        pushCandidate(activeMcpProjects[0].path);
      }
    }
    return candidates.find(isAbsoluteLocalPath) || raw;
  }

  async function openProjectFolder(path: string) {
    const targetPath = resolveOpenableProjectPath(path);
    if (!targetPath) {
      return;
    }
    if (!isAbsoluteLocalPath(targetPath)) {
      setError("Cannot open this project because it does not have an absolute Unity project path.");
      return;
    }
    try {
      if (!isTauriRuntime()) {
        throw new Error("Open folder is available in the desktop app.");
      }
      await invoke("open_folder", { path: targetPath });
    } catch (cause) {
      setError(cause instanceof Error ? cause.message : String(cause));
    }
  }

  function archiveProjectChats(path: string, archived: boolean) {
    const key = normalizeProjectPathKey(path);
    if (!key) {
      return;
    }
    markChatsDirty();
    setChats((list) => list.map((chat) => (normalizeProjectPathKey(chat.projectPath) === key ? { ...chat, archived } : chat)));
    if (archived && activeProjectPath && normalizeProjectPathKey(activeProjectPath) === key) {
      setActiveChatId("");
    }
  }

  async function scanActiveProjectIndex(projectPath = activeProjectPath, silent = false) {
    if (!projectPath) {
      return;
    }
    setLoadingProjectIndex(true);
    if (!silent) {
      setProjectIndexError("");
    }
    try {
      let targetEndpoint = endpoint;
      if (!runtimeConnected) {
        const readyEndpoint = await startRuntime();
        if (!readyEndpoint) {
          return;
        }
        targetEndpoint = readyEndpoint;
      }
      const payload = await scanProjectIndex(targetEndpoint, { projectPath });
      if (normalizeProjectPathKey(projectPath) === normalizeProjectPathKey(activeProjectPath)) {
        setProjectIndex(payload);
        setProjectIndexProject(projectPath);
        setProjectIndexError(payload.ok ? "" : payload.error || "Project index scan failed.");
      }
    } catch (cause) {
      if (normalizeProjectPathKey(projectPath) === normalizeProjectPathKey(activeProjectPath)) {
        setProjectIndexError(cause instanceof Error ? cause.message : String(cause));
      }
    } finally {
      setLoadingProjectIndex(false);
    }
  }

  async function planActiveOutfitImport() {
    const packagePath = outfitPackagePath.trim();
    if (!packagePath) {
      setOutfitImportStatus("Package path is required.");
      return;
    }
    setLoadingOutfitImportPlan(true);
    setOutfitImportStatus("");
    try {
      let targetEndpoint = endpoint;
      if (!runtimeConnected) {
        const readyEndpoint = await startRuntime();
        if (!readyEndpoint) {
          setOutfitImportStatus("VRCForge runtime is not connected.");
          return;
        }
        targetEndpoint = readyEndpoint;
      }
      const payload = await planOutfitImport(targetEndpoint, {
        packagePath,
        projectPath: activeProjectPath,
      });
      setOutfitImportPlan(payload);
      setOutfitImportStatus(payload.ok ? "Import plan ready." : payload.error || payload.plan?.error || "Import plan needs review.");
    } catch (cause) {
      setOutfitImportStatus(cause instanceof Error ? cause.message : String(cause));
    } finally {
      setLoadingOutfitImportPlan(false);
    }
  }

  async function requestActiveOutfitImport() {
    const packagePath = outfitPackagePath.trim();
    if (!packagePath) {
      setOutfitImportStatus("Package path is required.");
      return;
    }
    setRequestingOutfitImport(true);
    setOutfitImportStatus("");
    try {
      let targetEndpoint = endpoint;
      if (!runtimeConnected) {
        const readyEndpoint = await startRuntime();
        if (!readyEndpoint) {
          setOutfitImportStatus("VRCForge runtime is not connected.");
          return;
        }
        targetEndpoint = readyEndpoint;
      }
      const payload = await requestOutfitImport(targetEndpoint, {
        packagePath,
        projectPath: activeProjectPath,
      });
      setOutfitImportStatus(payload.approval ? `Approval queued: ${payload.approval.id}` : "Approval queued.");
      await refresh(targetEndpoint);
    } catch (cause) {
      setOutfitImportStatus(cause instanceof Error ? cause.message : String(cause));
    } finally {
      setRequestingOutfitImport(false);
    }
  }

  function toggleProjectCollapse(key: string) {
    setCollapsedProjects((current) => ({ ...current, [key]: !current[key] }));
  }

  function finishOnboarding() {
    try {
      window.localStorage.setItem(ONBOARDING_FLAG_KEY, "true");
    } catch {
      // 忽略持久化失败，仅本次会话关闭引导。
    }
    setShowOnboarding(false);
    setOnboardingMinimized(false);
  }

  function restartOnboarding() {
    try {
      window.localStorage.removeItem(ONBOARDING_FLAG_KEY);
    } catch {
      // 忽略
    }
    setActiveView("chat");
    setOnboardingStep(0);
    setOnboardingMinimized(false);
    setShowOnboarding(true);
  }

  function openChat(chat: ChatThread) {
    setActiveView("chat");
    setActiveChatId(chat.id);
    setActiveProjectPath(chat.projectPath);
    setError("");
  }

  function selectProject(projectPath: string) {
    setActiveView("chat");
    setActiveProjectPath(projectPath);
    const latest = chats.find((chat) => normalizeProjectPathKey(chat.projectPath) === normalizeProjectPathKey(projectPath) && !chat.archived);
    setActiveChatId(latest ? latest.id : "");
    setError("");
  }

  async function openDoctor() {
    setActiveView("doctor");
    setError("");
    await loadDoctor();
  }

  async function retryStartupOrHealth() {
    if (hasStartupIssue || !runtimeConnected) {
      await startRuntime();
      return;
    }
    try {
      await refresh();
    } catch (cause) {
      const message = cause instanceof Error ? cause.message : String(cause);
      setError(message);
      setStartupIssue(message);
    }
  }

  async function loadDoctor(target = endpoint) {
    setLoadingDoctor(true);
    setDoctorMessage("");
    try {
      let targetEndpoint = target;
      if (!runtimeConnected && target === endpoint) {
        const readyEndpoint = await startRuntime();
        if (!readyEndpoint) {
          return;
        }
        targetEndpoint = readyEndpoint;
      }
      const payload = await fetchDoctor(targetEndpoint);
      setDoctorReport(payload);
      setStartupIssue("");
    } catch (cause) {
      setError(cause instanceof Error ? cause.message : String(cause));
    } finally {
      setLoadingDoctor(false);
    }
  }

  async function openOptimization() {
    setActiveView("optimization");
    setError("");
    await loadOptimizationPlan();
    await loadOptimizationAvatars();
    await loadOptimizationProofs();
  }

  async function loadOptimizationPlan(target = endpoint, profile = optimizationTargetProfile) {
    setLoadingOptimization(true);
    setOptimizationMessage("");
    try {
      let targetEndpoint = target;
      if (!runtimeConnected && target === endpoint) {
        const readyEndpoint = await startRuntime();
        if (!readyEndpoint) {
          return;
        }
        targetEndpoint = readyEndpoint;
      }
      const payload = await fetchOptimizationPlan(targetEndpoint, {
        projectPath: activeProjectPath || undefined,
        avatarPath: optimizationAvatarPath.trim() || undefined,
        targetProfile: profile,
        includeQuest: true,
      });
      setOptimizationReport(payload);
      setOptimizationMessage(payload.ok ? "Plan refreshed" : "Planner returned warnings");
      void loadOptimizationProofs(targetEndpoint);
    } catch (cause) {
      setOptimizationMessage(cause instanceof Error ? cause.message : String(cause));
    } finally {
      setLoadingOptimization(false);
    }
  }

  async function loadOptimizationProofs(target = endpoint, runId?: string) {
    setLoadingOptimizationProofs(true);
    setOptimizationProofMessage("");
    try {
      let targetEndpoint = target;
      if (!runtimeConnected && target === endpoint) {
        const readyEndpoint = await startRuntime();
        if (!readyEndpoint) {
          return;
        }
        targetEndpoint = readyEndpoint;
      }
      const payload = await fetchOptimizationProofs(targetEndpoint, 8);
      const proofs = payload.proofs || [];
      setOptimizationProofs(proofs);
      const selectedRunId = runId || selectedOptimizationProof?.proof?.runId || proofs[0]?.runId || "";
      if (selectedRunId) {
        const detail = await fetchOptimizationProof(targetEndpoint, selectedRunId);
        setSelectedOptimizationProof(detail);
      } else {
        setSelectedOptimizationProof(null);
      }
      setOptimizationProofMessage(proofs.length ? `${proofs.length} proof run${proofs.length === 1 ? "" : "s"}` : "No optimizer proof runs");
    } catch (cause) {
      setOptimizationProofMessage(cause instanceof Error ? cause.message : String(cause));
      setSelectedOptimizationProof(null);
    } finally {
      setLoadingOptimizationProofs(false);
    }
  }

  async function selectOptimizationProof(runId: string) {
    await loadOptimizationProofs(endpoint, runId);
  }

  async function loadOptimizationAvatars(target = endpoint) {
    setLoadingOptimizationAvatars(true);
    setOptimizationAvatarMessage("");
    try {
      let targetEndpoint = target;
      if (!runtimeConnected && target === endpoint) {
        const readyEndpoint = await startRuntime();
        if (!readyEndpoint) {
          return;
        }
        targetEndpoint = readyEndpoint;
      }
      const payload = await fetchAvatars(targetEndpoint, {
        projectPath: activeProjectPath || undefined,
      });
      const avatars = (payload.avatars ?? []).filter((item) => Boolean(item.avatarPath));
      setOptimizationAvatars(avatars);
      if (!optimizationAvatarPath.trim() && avatars.length === 1 && avatars[0].avatarPath) {
        setOptimizationAvatarPath(avatars[0].avatarPath);
      }
      if (payload.ok) {
        setOptimizationAvatarMessage(
          avatars.length ? `${avatars.length} avatar${avatars.length === 1 ? "" : "s"} found` : "No scene avatars found",
        );
      } else {
        setOptimizationAvatarMessage("Avatar scan returned warnings");
      }
    } catch (cause) {
      setOptimizationAvatarMessage(cause instanceof Error ? cause.message : String(cause));
    } finally {
      setLoadingOptimizationAvatars(false);
    }
  }

  function updateOptimizationActionOption(actionId: string, key: keyof OptimizationActionOptions, value: string) {
    setOptimizationActionOptions((current) => ({
      ...current,
      [actionId]: {
        ...(current[actionId] ?? {}),
        [key]: value,
      },
    }));
  }

  async function requestOptimizationAction(card: NonNullable<OptimizationPlannerReport["actionCards"]>[number]) {
    if (!card.requestTool) {
      return;
    }
    const avatarPath = optimizationAvatarPath.trim();
    if (!avatarPath) {
      setOptimizationMessage("Set avatar path before requesting an optimizer step.");
      return;
    }
    setRequestingOptimizationAction(card.id);
    setOptimizationMessage("");
    try {
      const requestOptions = buildOptimizationRequestOptions(card, optimizationActionOptions[card.id] ?? {});
      let targetEndpoint = endpoint;
      if (!runtimeConnected) {
        const readyEndpoint = await startRuntime();
        if (!readyEndpoint) {
          return;
        }
        targetEndpoint = readyEndpoint;
      }
      const payload = await requestOptimizationApply(targetEndpoint, {
        tool: card.requestTool,
        projectPath: activeProjectPath || undefined,
        avatarPath,
        targetProfile: optimizationTargetProfile,
        options: requestOptions,
        installMissingDependencies: true,
      });
      setOptimizationMessage(payload.approval ? `Approval queued: ${payload.approval.id}` : payload.error || "Request queued.");
      await refreshSilently(targetEndpoint);
      await loadOptimizationPlan(targetEndpoint);
    } catch (cause) {
      setOptimizationMessage(cause instanceof Error ? cause.message : String(cause));
    } finally {
      setRequestingOptimizationAction("");
    }
  }

  async function requestOptimizationDependencyInstall(dependency: NonNullable<NonNullable<OptimizationPlannerReport["dependencyDoctor"]>["dependencies"]>[number]) {
    const packageId = dependency.packageIds?.find((item) => item);
    if (!packageId) {
      return;
    }
    setRequestingOptimizationDependency(dependency.id || packageId);
    setOptimizationMessage("");
    try {
      let targetEndpoint = endpoint;
      if (!runtimeConnected) {
        const readyEndpoint = await startRuntime();
        if (!readyEndpoint) {
          return;
        }
        targetEndpoint = readyEndpoint;
      }
      const payload = await requestPackageInstall(targetEndpoint, {
        projectPath: activeProjectPath || undefined,
        packageId,
        repository: dependency.installMethod?.repository || undefined,
        allowAgentManagedDownload: true,
      });
      setOptimizationMessage(payload.approval ? `Install approval queued: ${payload.approval.id}` : payload.error || "Install request queued.");
      await refreshSilently(targetEndpoint);
      await loadOptimizationPlan(targetEndpoint);
    } catch (cause) {
      setOptimizationMessage(cause instanceof Error ? cause.message : String(cause));
    } finally {
      setRequestingOptimizationDependency("");
    }
  }

  async function openProtection() {
    setActiveView("protection");
    setError("");
    await loadProtectionPlan();
    await loadProtectionAvatars();
  }

  async function loadProtectionPlan(target = endpoint, profile = protectionProfile) {
    setLoadingProtection(true);
    setProtectionMessage("");
    try {
      let targetEndpoint = target;
      if (!runtimeConnected && target === endpoint) {
        const readyEndpoint = await startRuntime();
        if (!readyEndpoint) {
          return;
        }
        targetEndpoint = readyEndpoint;
      }
      const payload = await planAvatarEncryption(targetEndpoint, {
        projectPath: activeProjectPath || undefined,
        avatarPath: protectionAvatarPath.trim() || undefined,
        profile,
        protectionProfile: profile,
        confirmCreatorOwnedAssets: protectionOwnsAssets,
      });
      setProtectionPlan(payload);
      const plan = protectionPlanPayload(payload);
      const candidateCount = Number(plan.selectedCandidateCount ?? 0);
      const connector = (plan.externalAddon || {}) as Record<string, unknown>;
      const connectorConfigured = Boolean(connector.configured);
      const writeStatus = String(plan.writeStatus || "");
      setProtectionMessage(
        payload.ok
          ? connectorConfigured && writeStatus !== "blocked"
            ? `${candidateCount} target${candidateCount === 1 ? "" : "s"} ready for private addon request`
            : `${candidateCount} target${candidateCount === 1 ? "" : "s"} found; private addon required`
          : payload.error || "Protection plan returned warnings",
      );
    } catch (cause) {
      setProtectionMessage(cause instanceof Error ? cause.message : String(cause));
    } finally {
      setLoadingProtection(false);
    }
  }

  async function loadProtectionAvatars(target = endpoint) {
    setLoadingProtectionAvatars(true);
    setProtectionAvatarMessage("");
    try {
      let targetEndpoint = target;
      if (!runtimeConnected && target === endpoint) {
        const readyEndpoint = await startRuntime();
        if (!readyEndpoint) {
          return;
        }
        targetEndpoint = readyEndpoint;
      }
      const payload = await fetchAvatars(targetEndpoint, {
        projectPath: activeProjectPath || undefined,
      });
      const avatars = (payload.avatars ?? []).filter((item) => Boolean(item.avatarPath));
      setProtectionAvatars(avatars);
      if (!protectionAvatarPath.trim() && avatars.length === 1 && avatars[0].avatarPath) {
        setProtectionAvatarPath(avatars[0].avatarPath);
      }
      setProtectionAvatarMessage(
        payload.ok
          ? avatars.length
            ? `${avatars.length} avatar${avatars.length === 1 ? "" : "s"} found`
            : "No scene avatars found"
          : "Avatar scan returned warnings",
      );
    } catch (cause) {
      setProtectionAvatarMessage(cause instanceof Error ? cause.message : String(cause));
    } finally {
      setLoadingProtectionAvatars(false);
    }
  }

  async function requestProtectionApply(targetFamily: "liltoon" | "poiyomi") {
    const avatarPath = protectionAvatarPath.trim();
    if (!avatarPath) {
      setProtectionMessage("Set avatar path before requesting protection.");
      return;
    }
    if (!protectionOwnsAssets) {
      setProtectionMessage("Confirm asset ownership before requesting protection.");
      return;
    }
    setRequestingProtectionFamily(targetFamily);
    setProtectionMessage("");
    try {
      let targetEndpoint = endpoint;
      if (!runtimeConnected) {
        const readyEndpoint = await startRuntime();
        if (!readyEndpoint) {
          return;
        }
        targetEndpoint = readyEndpoint;
      }
      const payload = await requestAvatarEncryptionApply(targetEndpoint, {
        projectPath: activeProjectPath || undefined,
        avatarPath,
        profile: protectionProfile,
        protectionProfile,
        targetShaderFamily: targetFamily,
        targetShaderFamilies: [targetFamily],
        confirmCreatorOwnedAssets: true,
      });
      setProtectionMessage(payload.approval ? `Approval queued: ${payload.approval.id}` : payload.error || "Request queued.");
      await refreshSilently(targetEndpoint);
      await loadProtectionPlan(targetEndpoint);
    } catch (cause) {
      setProtectionMessage(cause instanceof Error ? cause.message : String(cause));
    } finally {
      setRequestingProtectionFamily("");
    }
  }

  async function repairUnityBridgeFromDoctor(target = endpoint) {
    setRepairingUnityBridge(true);
    setDoctorMessage("");
    setError("");
    try {
      let targetEndpoint = target;
      if (!runtimeConnected && target === endpoint) {
        const readyEndpoint = await startRuntime();
        if (!readyEndpoint) {
          return;
        }
        targetEndpoint = readyEndpoint;
      }
      const payload = await repairUnityMcpBridge(targetEndpoint, {
        projectPath: activeProjectPath || undefined,
        allowUnityRelaunch: true,
        waitSeconds: 120,
        closeTimeoutSeconds: 60,
      });
      const failedPhase = payload.phases.find((phase) => phase.status === "error" || phase.status === "warning");
      const suffix = failedPhase && !payload.ok ? `: ${failedPhase.message}` : "";
      await loadDoctor(targetEndpoint);
      await refreshWithRetry(targetEndpoint);
      setDoctorMessage(payload.ok ? `Unity bridge ${payload.status}` : `Unity bridge needs action${suffix}`);
    } catch (cause) {
      setError(cause instanceof Error ? cause.message : String(cause));
    } finally {
      setRepairingUnityBridge(false);
    }
  }

  async function openSkills() {
    setActiveView("skills");
    setError("");
    try {
      let targetEndpoint = endpoint;
      if (!runtimeConnected) {
        const readyEndpoint = await startRuntime();
        if (!readyEndpoint) {
          return;
        }
        targetEndpoint = readyEndpoint;
      }
      const [payload] = await Promise.all([fetchSkills(targetEndpoint), loadSkillPackages(targetEndpoint)]);
      setSkillRegistry(payload);
      setSkillCheck(await checkSkills(targetEndpoint));
      if (!selectedSkillName && payload.skills.length > 0) {
        const firstUserSkill = payload.skills.find((skill) => skill.source === "user") || payload.skills[0];
        selectSkill(firstUserSkill);
      }
    } catch (cause) {
      setError(cause instanceof Error ? cause.message : String(cause));
    }
  }

  async function loadSkillPackages(target = endpoint) {
    setLoadingSkillPackages(true);
    setSkillPackageError("");
    try {
      let targetEndpoint = target;
      if (!runtimeConnected && target === endpoint) {
        const readyEndpoint = await startRuntime();
        if (!readyEndpoint) {
          return null;
        }
        targetEndpoint = readyEndpoint;
      }
      const payload = await fetchSkillPackages(targetEndpoint);
      setSkillPackages(payload.installed || []);
      setSkillPackageStore(payload.store || "");
      setSkillPackageGovernance((payload.governance || {}) as Record<string, unknown>);
      setSkillPackageAudit(payload.audit || []);
      return payload;
    } catch (cause) {
      setSkillPackageError(cause instanceof Error ? cause.message : String(cause));
      return null;
    } finally {
      setLoadingSkillPackages(false);
    }
  }

  async function preflightVskPackage(packagePath: string) {
    setSkillPackageMessage("");
    setSkillPackageError("");
    const payload = await preflightSkillPackage(endpoint, { packagePath });
    setSkillPackageMessage("Package preflight completed");
    return payload;
  }

  async function importVskPackage(packagePath: string) {
    setLoadingSkillPackages(true);
    setSkillPackageMessage("");
    setSkillPackageError("");
    try {
      const payload = await importSkillPackage(endpoint, { packagePath });
      setSkillPackageMessage(payload.changed === false ? "Package already installed" : t("package.messages.packageImported"));
      const [skillsPayload] = await Promise.all([fetchSkills(endpoint), loadSkillPackages(endpoint)]);
      setSkillRegistry(skillsPayload);
      setSkillCheck(await checkSkills(endpoint));
      await refresh(endpoint);
      return payload;
    } catch (cause) {
      const message = cause instanceof Error ? cause.message : String(cause);
      setSkillPackageError(message);
      throw cause;
    } finally {
      setLoadingSkillPackages(false);
    }
  }

  async function exportVskPackage(skillName: string, outputPath: string, release: boolean, privateKeyPath?: string) {
    setLoadingSkillPackages(true);
    setSkillPackageMessage("");
    setSkillPackageError("");
    try {
      const payload = await exportSkillPackage(endpoint, { skillName, outputPath, release, privateKeyPath: privateKeyPath || undefined });
      setSkillPackageMessage(release ? t("package.messages.releaseExported") : t("package.messages.devExported"));
      return payload;
    } catch (cause) {
      const message = cause instanceof Error ? cause.message : String(cause);
      setSkillPackageError(message);
      throw cause;
    } finally {
      setLoadingSkillPackages(false);
    }
  }

  async function setVskPackageEnabled(skillPackageId: string, enabled: boolean) {
    setLoadingSkillPackages(true);
    setSkillPackageMessage("");
    setSkillPackageError("");
    try {
      const payload = await setSkillPackageEnabled(endpoint, skillPackageId, { enabled, syncProjectedSkill: true });
      setSkillPackageMessage(enabled ? t("package.messages.packageEnabled") : t("package.messages.packageDisabled"));
      const [skillsPayload] = await Promise.all([fetchSkills(endpoint), loadSkillPackages(endpoint)]);
      setSkillRegistry(skillsPayload);
      setSkillCheck(await checkSkills(endpoint));
      await refresh(endpoint);
      return payload;
    } catch (cause) {
      const message = cause instanceof Error ? cause.message : String(cause);
      setSkillPackageError(message);
      throw cause;
    } finally {
      setLoadingSkillPackages(false);
    }
  }

  async function uninstallVskPackage(skillPackageId: string) {
    setLoadingSkillPackages(true);
    setSkillPackageMessage("");
    setSkillPackageError("");
    try {
      const payload = await uninstallSkillPackage(endpoint, skillPackageId, { removeProjectedSkill: true });
      setSkillPackageMessage(t("package.messages.packageUninstalled"));
      const [skillsPayload] = await Promise.all([fetchSkills(endpoint), loadSkillPackages(endpoint)]);
      setSkillRegistry(skillsPayload);
      setSkillCheck(await checkSkills(endpoint));
      await refresh(endpoint);
      return payload;
    } catch (cause) {
      const message = cause instanceof Error ? cause.message : String(cause);
      setSkillPackageError(message);
      throw cause;
    } finally {
      setLoadingSkillPackages(false);
    }
  }

  async function refreshSkillWorkspaceState() {
    const [skillsPayload] = await Promise.all([fetchSkills(endpoint), loadSkillPackages(endpoint)]);
    setSkillRegistry(skillsPayload);
    setSkillCheck(await checkSkills(endpoint));
    await refresh(endpoint);
  }

  async function setVskPackageSafeMode(enabled: boolean, reason?: string) {
    setLoadingSkillPackages(true);
    setSkillPackageMessage("");
    setSkillPackageError("");
    try {
      const payload = await setSkillPackageSafeMode(endpoint, { enabled, reason: reason || undefined });
      setSkillPackageMessage(enabled ? t("package.messages.safeModeEnabled") : t("package.labels.safeModeDisabled"));
      await refreshSkillWorkspaceState();
      return payload;
    } catch (cause) {
      const message = cause instanceof Error ? cause.message : String(cause);
      setSkillPackageError(message);
      throw cause;
    } finally {
      setLoadingSkillPackages(false);
    }
  }

  async function trustVskPackageSigner(signerFingerprint: string, reason?: string) {
    setLoadingSkillPackages(true);
    setSkillPackageMessage("");
    setSkillPackageError("");
    try {
      const payload = await trustSkillPackageSigner(endpoint, { signerFingerprint, reason: reason || undefined });
      setSkillPackageMessage(t("package.messages.signerTrusted"));
      await refreshSkillWorkspaceState();
      return payload;
    } catch (cause) {
      const message = cause instanceof Error ? cause.message : String(cause);
      setSkillPackageError(message);
      throw cause;
    } finally {
      setLoadingSkillPackages(false);
    }
  }

  async function revokeVskPackageSigner(signerFingerprint: string, reason?: string) {
    setLoadingSkillPackages(true);
    setSkillPackageMessage("");
    setSkillPackageError("");
    try {
      const payload = await revokeSkillPackageSigner(endpoint, { signerFingerprint, reason: reason || undefined });
      setSkillPackageMessage(t("package.messages.signerRevoked"));
      await refreshSkillWorkspaceState();
      return payload;
    } catch (cause) {
      const message = cause instanceof Error ? cause.message : String(cause);
      setSkillPackageError(message);
      throw cause;
    } finally {
      setLoadingSkillPackages(false);
    }
  }

  async function blockVskPackage(request: { packageId?: string; packageSha256?: string; lockSha256?: string; reason?: string }) {
    setLoadingSkillPackages(true);
    setSkillPackageMessage("");
    setSkillPackageError("");
    try {
      const payload = await blockSkillPackage(endpoint, request);
      setSkillPackageMessage(t("package.messages.packageBlocked"));
      await refreshSkillWorkspaceState();
      return payload;
    } catch (cause) {
      const message = cause instanceof Error ? cause.message : String(cause);
      setSkillPackageError(message);
      throw cause;
    } finally {
      setLoadingSkillPackages(false);
    }
  }

  async function openSettings() {
    setActiveView("settings");
    setError("");
    setNotesMessage("");
    setDiagnosticsMessage("");
    try {
      let targetEndpoint = endpoint;
      if (!runtimeConnected) {
        const readyEndpoint = await startRuntime();
        if (!readyEndpoint) {
          return;
        }
        targetEndpoint = readyEndpoint;
      }
      const notes = await fetchAgentNotes(targetEndpoint);
      setAgentNotes(notes.content);
      setAgentNotesPath(notes.path);
      setAgentNotesLoaded(true);
      void loadConnectors(targetEndpoint);
      void loadDiagnostics(targetEndpoint);
    } catch (cause) {
      setAgentNotesLoaded(false);
      setError(cause instanceof Error ? cause.message : String(cause));
    }
  }

  async function loadDiagnostics(target = endpoint) {
    setLoadingDiagnostics(true);
    try {
      let targetEndpoint = target;
      if (!runtimeConnected && target === endpoint) {
        const readyEndpoint = await startRuntime();
        if (!readyEndpoint) {
          return;
        }
        targetEndpoint = readyEndpoint;
      }
      setDiagnosticsStatus(await fetchDiagnostics(targetEndpoint));
    } catch (cause) {
      setError(cause instanceof Error ? cause.message : String(cause));
    } finally {
      setLoadingDiagnostics(false);
    }
  }

  async function setDebugLogging(enabled: boolean) {
    setLoadingDiagnostics(true);
    setDiagnosticsMessage("");
    setError("");
    try {
      const payload = await updateDiagnostics(endpoint, { debugLogging: enabled });
      setDiagnosticsStatus(payload);
      setDiagnosticsMessage(enabled ? "Debug logging enabled" : "Debug logging disabled");
    } catch (cause) {
      setError(cause instanceof Error ? cause.message : String(cause));
    } finally {
      setLoadingDiagnostics(false);
    }
  }

  async function createSupportBundle() {
    setExportingSupportBundle(true);
    setDoctorMessage("");
    setError("");
    try {
      let targetEndpoint = endpoint;
      if (!runtimeConnected) {
        const readyEndpoint = await startRuntime();
        if (!readyEndpoint) {
          return;
        }
        targetEndpoint = readyEndpoint;
      }
      const payload = await exportSupportBundle(targetEndpoint, { logLimit: 200 });
      setDoctorMessage(`Support bundle exported: ${payload.bundlePath}`);
      setDiagnosticsMessage(`Support bundle exported: ${payload.bundlePath}`);
      void loadDiagnostics(targetEndpoint);
    } catch (cause) {
      setError(cause instanceof Error ? cause.message : String(cause));
    } finally {
      setExportingSupportBundle(false);
    }
  }

  async function loadConnectors(target = endpoint) {
    setLoadingConnectors(true);
    setConnectorMessage("");
    try {
      let targetEndpoint = target;
      if (!runtimeConnected && target === endpoint) {
        const readyEndpoint = await startRuntime();
        if (!readyEndpoint) {
          return;
        }
        targetEndpoint = readyEndpoint;
      }
      setConnectorStatus(await fetchExternalAgentConnectors(targetEndpoint, activeProjectPath || undefined));
    } catch (cause) {
      setError(cause instanceof Error ? cause.message : String(cause));
    } finally {
      setLoadingConnectors(false);
    }
  }

  async function updateGatewaySettings(request: {
    enabled?: boolean;
    allowWriteRequests?: boolean;
    revokeToken?: boolean;
    checkpointArchiveMaxSizeMb?: number;
    deleteCheckpointArchiveIds?: string[];
    checkpointArchiveDirectory?: string;
  }) {
    setLoadingConnectors(true);
    setConnectorMessage("");
    setError("");
    try {
      const payload = await updateExternalAgentGateway(endpoint, request);
      setConnectorStatus(payload);
      const relocate = payload.gateway?.checkpointArchiveRelocate;
      const del = payload.gateway?.checkpointArchiveDelete;
      let message = "Gateway updated";
      if (request.revokeToken) {
        message = "Token revoked";
      } else if (request.checkpointArchiveDirectory !== undefined) {
        message = relocate?.ok
          ? t("settings.checkpointArchiveRelocated", { count: relocate.copiedCount ?? 0 })
          : t("settings.checkpointArchiveRelocateFailed", { reason: relocate?.error || relocate?.code || "" });
      } else if (request.deleteCheckpointArchiveIds !== undefined) {
        message = del?.ok
          ? t("settings.checkpointArchiveDeleted", { count: del.deletedCount ?? 0 })
          : t("settings.checkpointArchiveDeleteFailed", { reason: del?.error || "" });
      } else if (request.checkpointArchiveMaxSizeMb !== undefined) {
        message = t("settings.checkpointArchiveUpdated");
      }
      setConnectorMessage(message);
      await refresh();
    } catch (cause) {
      setError(cause instanceof Error ? cause.message : String(cause));
    } finally {
      setLoadingConnectors(false);
    }
  }

  async function saveCheckpointArchiveLimit() {
    const trimmed = checkpointArchiveLimitInput.trim();
    const parsed = Number(trimmed || "0");
    if (!Number.isFinite(parsed) || parsed < 0) {
      setError(t("settings.checkpointArchiveLimitInvalid"));
      return;
    }
    await updateGatewaySettings({ checkpointArchiveMaxSizeMb: Math.round(parsed) });
  }

  async function openCheckpointArchiveFolder(targetPath: string) {
    if (!targetPath) {
      return;
    }
    try {
      if (!isTauriRuntime()) {
        throw new Error("Open folder is available in the desktop app.");
      }
      await invoke("open_local_folder", { path: targetPath });
    } catch (cause) {
      setError(cause instanceof Error ? cause.message : String(cause));
    }
  }

  async function pickCheckpointArchiveDirectory(currentPath: string) {
    try {
      if (!isTauriRuntime()) {
        throw new Error("Folder picker is available in the desktop app.");
      }
      const selected = await invoke<string | null>("select_folder", {
        initialPath: currentPath || undefined,
      });
      return selected || "";
    } catch (cause) {
      setError(cause instanceof Error ? cause.message : String(cause));
      return "";
    }
  }

  async function deleteCheckpointArchives(ids: string[]) {
    if (!ids.length) {
      return;
    }
    await updateGatewaySettings({ deleteCheckpointArchiveIds: ids });
  }

  async function relocateCheckpointArchives(directory: string) {
    const trimmed = directory.trim();
    if (!trimmed) {
      setError(t("settings.checkpointArchiveDirInvalid"));
      return;
    }
    await updateGatewaySettings({ checkpointArchiveDirectory: trimmed });
  }

  async function runConnectorAction(client: ExternalAgentConnectorClient, action: "install" | "uninstall") {
    setLoadingConnectors(true);
    setConnectorMessage("");
    setError("");
    try {
      let targetEndpoint = endpoint;
      if (!runtimeConnected) {
        const readyEndpoint = await startRuntime();
        if (!readyEndpoint) {
          return;
        }
        targetEndpoint = readyEndpoint;
      }
      const request = { client, projectPath: activeProjectPath || undefined };
      const payload =
        action === "install"
          ? await installExternalAgentConnector(targetEndpoint, request)
          : await uninstallExternalAgentConnector(targetEndpoint, request);
      setConnectorStatus(payload);
      setConnectorMessage(formatConnectorActionMessage(client, payload.lastConnectorAction));
      await refresh();
    } catch (cause) {
      setError(cause instanceof Error ? cause.message : String(cause));
    } finally {
      setLoadingConnectors(false);
    }
  }

  async function saveNotes(event?: FormEvent) {
    event?.preventDefault();
    if (savingNotes) {
      return;
    }
    setSavingNotes(true);
    setNotesMessage("");
    setError("");
    try {
      const payload = await saveAgentNotes(endpoint, agentNotes);
      setAgentNotesPath(payload.path);
      setNotesMessage(t("settings.saved"));
    } catch (cause) {
      setError(cause instanceof Error ? cause.message : String(cause));
    } finally {
      setSavingNotes(false);
    }
  }

  function selectSkill(skill: AgentSkill) {
    setSelectedSkillName(skill.name);
    setSkillDraft({ ...skill });
  }

  function newSkill() {
    setSelectedSkillName("");
    setSkillDraft(emptySkillDraft());
  }

  async function runSkillCheck() {
    setError("");
    try {
      let targetEndpoint = endpoint;
      if (!runtimeConnected) {
        const readyEndpoint = await startRuntime();
        if (!readyEndpoint) {
          return;
        }
        targetEndpoint = readyEndpoint;
      }
      setSkillCheck(await checkSkills(targetEndpoint));
    } catch (cause) {
      setError(cause instanceof Error ? cause.message : String(cause));
    }
  }

  async function openCheckpoints() {
    setActiveView("checkpoints");
    await loadCheckpoints();
  }

  async function loadCheckpoints(target = endpoint) {
    setLoadingCheckpoints(true);
    try {
      let targetEndpoint = target;
      if (!runtimeConnected) {
        const readyEndpoint = await startRuntime();
        if (!readyEndpoint) {
          return;
        }
        targetEndpoint = readyEndpoint;
      }
      const [payload, recoveryPayload, adjustmentPayload] = await Promise.all([
        fetchCheckpoints(targetEndpoint, activeProjectPath || undefined),
        fetchInterruptedApplyRecoveries(targetEndpoint, { projectRoot: activeProjectPath || undefined }),
        fetchAdjustmentCheckpoints(targetEndpoint, { projectRoot: activeProjectPath || undefined }),
      ]);
      const nextCheckpoints = payload.checkpoints || [];
      const nextRecoveries = recoveryPayload.recoveries || [];
      const nextAdjustmentCheckpoints = adjustmentPayload.checkpoints || [];
      setCheckpoints(nextCheckpoints);
      setInterruptedRecoveries(nextRecoveries);
      setAdjustmentCheckpoints(nextAdjustmentCheckpoints);
      if (checkpointPreview?.checkpoint?.id && !nextCheckpoints.some((item) => item.id === checkpointPreview.checkpoint?.id)) {
        setCheckpointPreview(null);
      }
      const recoveryId = recoveryPreview?.recovery?.id;
      if (recoveryId && !nextRecoveries.some((item) => item.id === recoveryId)) {
        setRecoveryPreview(null);
      }
      const adjustmentId = adjustmentPreview?.adjustmentCheckpoint?.id;
      if (adjustmentId && !nextAdjustmentCheckpoints.some((item) => item.id === adjustmentId)) {
        setAdjustmentPreview(null);
      }
    } catch (cause) {
      setError(cause instanceof Error ? cause.message : String(cause));
    } finally {
      setLoadingCheckpoints(false);
    }
  }

  async function previewCheckpoint(checkpointId: string) {
    setLoadingCheckpoints(true);
    setCheckpointMessage("");
    try {
      const payload = await previewRestoreCheckpoint(endpoint, checkpointId);
      setCheckpointPreview(payload);
      if (!payload.ok) {
        setError(payload.error || "Checkpoint preview failed.");
      }
    } catch (cause) {
      setError(cause instanceof Error ? cause.message : String(cause));
    } finally {
      setLoadingCheckpoints(false);
    }
  }

  async function restoreCheckpoint(checkpointId: string) {
    setRestoringCheckpointId(checkpointId);
    setCheckpointMessage("");
    setError("");
    try {
      const payload = await requestRestoreCheckpoint(endpoint, checkpointId);
      if (payload.status === "pending") {
        setCheckpointMessage("Restore approval is pending.");
      } else if (payload.ok) {
        setCheckpointMessage("Checkpoint restored.");
      } else {
        setCheckpointMessage(String(payload.error || "Restore request failed."));
      }
      await refresh();
      await loadCheckpoints();
    } catch (cause) {
      setError(cause instanceof Error ? cause.message : String(cause));
    } finally {
      setRestoringCheckpointId("");
    }
  }

  async function previewRecovery(recoveryId: string) {
    setRecoveryBusyId(recoveryId);
    setRecoveryMessage("");
    setError("");
    try {
      const payload = await previewInterruptedApplyRecovery(endpoint, recoveryId);
      setRecoveryPreview(payload);
      if (!payload.ok) {
        setError(payload.error || "Recovery preview failed.");
      }
    } catch (cause) {
      setError(cause instanceof Error ? cause.message : String(cause));
    } finally {
      setRecoveryBusyId("");
    }
  }

  async function restoreRecovery(recoveryId: string) {
    setRecoveryBusyId(`restore:${recoveryId}`);
    setRecoveryMessage("");
    setError("");
    try {
      const payload = await requestRestoreInterruptedApplyRecovery(endpoint, recoveryId);
      if (payload.status === "pending") {
        setRecoveryMessage("Restore approval is pending.");
      } else if (payload.ok) {
        setRecoveryMessage("Interrupted write restored.");
      } else {
        setRecoveryMessage(String(payload.error || "Restore request failed."));
      }
      await refresh();
      await loadCheckpoints();
    } catch (cause) {
      setError(cause instanceof Error ? cause.message : String(cause));
    } finally {
      setRecoveryBusyId("");
    }
  }

  async function exportRecoveryBundle(recoveryId: string) {
    setRecoveryBusyId(`bundle:${recoveryId}`);
    setRecoveryMessage("");
    setError("");
    try {
      const payload = await exportInterruptedApplyIncidentBundle(endpoint, recoveryId);
      if (payload.ok) {
        setRecoveryMessage(`Incident bundle exported: ${payload.bundlePath || payload.path || "-"}`);
      } else {
        setRecoveryMessage(String(payload.error || "Incident bundle export failed."));
      }
    } catch (cause) {
      setError(cause instanceof Error ? cause.message : String(cause));
    } finally {
      setRecoveryBusyId("");
    }
  }

  async function resolveRecovery(recoveryId: string) {
    if (!window.confirm("Mark this interrupted write as resolved without restoring its checkpoint?")) {
      return;
    }
    setRecoveryBusyId(`resolve:${recoveryId}`);
    setRecoveryMessage("");
    setError("");
    try {
      const payload = await resolveInterruptedApplyRecovery(endpoint, recoveryId, {
        confirmResolved: true,
        note: "Resolved from the desktop Checkpoints view.",
      });
      if (payload.status === "pending") {
        setRecoveryMessage("Resolve approval is pending.");
      } else if (payload.ok) {
        setRecoveryMessage("Interrupted write resolved.");
      } else {
        setRecoveryMessage(String(payload.error || "Resolve request failed."));
      }
      await refresh();
      await loadCheckpoints();
    } catch (cause) {
      setError(cause instanceof Error ? cause.message : String(cause));
    } finally {
      setRecoveryBusyId("");
    }
  }

  async function createAdjustment(kind: "face" | "shader") {
    setAdjustmentBusyId(`create:${kind}`);
    setAdjustmentMessage("");
    setError("");
    try {
      let targetEndpoint = endpoint;
      if (!runtimeConnected) {
        const readyEndpoint = await startRuntime();
        if (!readyEndpoint) {
          return;
        }
        targetEndpoint = readyEndpoint;
      }
      const payload = await createAdjustmentCheckpoint(targetEndpoint, {
        kind,
        projectRoot: activeProjectPath || undefined,
        label: kind === "face" ? "Face adjustment" : "Shader adjustment",
      });
      setAdjustmentMessage(`${kind === "face" ? t("checkpoint.face") : t("checkpoint.shader")} checkpoint created.`);
      setAdjustmentPreview(payload.baseCheckpoint ? { ok: true, checkpoint: payload.baseCheckpoint, adjustmentCheckpoint: payload.checkpoint } : null);
      await loadCheckpoints(targetEndpoint);
    } catch (cause) {
      setError(cause instanceof Error ? cause.message : String(cause));
    } finally {
      setAdjustmentBusyId("");
    }
  }

  async function renameAdjustment(checkpoint: AdjustmentCheckpoint) {
    const currentLabel = checkpoint.label || checkpoint.id;
    const nextLabel = window.prompt("Checkpoint label", currentLabel);
    if (nextLabel === null || nextLabel.trim() === currentLabel) {
      return;
    }
    setAdjustmentBusyId(checkpoint.id);
    setAdjustmentMessage("");
    setError("");
    try {
      const payload = await updateAdjustmentCheckpoint(endpoint, checkpoint.id, { label: nextLabel.trim() });
      setAdjustmentMessage("Adjustment checkpoint updated.");
      setAdjustmentPreview((previous) =>
        previous?.adjustmentCheckpoint?.id === checkpoint.id
          ? { ...previous, adjustmentCheckpoint: payload.checkpoint }
          : previous,
      );
      await loadCheckpoints();
    } catch (cause) {
      setError(cause instanceof Error ? cause.message : String(cause));
    } finally {
      setAdjustmentBusyId("");
    }
  }

  async function previewAdjustment(checkpointId: string) {
    setAdjustmentBusyId(checkpointId);
    setAdjustmentMessage("");
    setError("");
    try {
      const payload = await previewAdjustmentCheckpoint(endpoint, checkpointId);
      setAdjustmentPreview(payload);
      if (!payload.ok) {
        setError(payload.error || "Adjustment preview failed.");
      }
    } catch (cause) {
      setError(cause instanceof Error ? cause.message : String(cause));
    } finally {
      setAdjustmentBusyId("");
    }
  }

  async function selectAdjustment(checkpointId: string, slot: "A" | "B") {
    setAdjustmentBusyId(`${checkpointId}:${slot}`);
    setAdjustmentMessage("");
    setError("");
    try {
      const payload = await selectAdjustmentCheckpoint(endpoint, checkpointId, { slot });
      setAdjustmentMessage(`Selected ${slot}.`);
      setAdjustmentPreview((previous) =>
        previous?.adjustmentCheckpoint?.id === checkpointId
          ? { ...previous, adjustmentCheckpoint: payload.checkpoint }
          : previous,
      );
      await loadCheckpoints();
    } catch (cause) {
      setError(cause instanceof Error ? cause.message : String(cause));
    } finally {
      setAdjustmentBusyId("");
    }
  }

  async function applyAdjustment(checkpointId: string) {
    setAdjustmentBusyId(`apply:${checkpointId}`);
    setAdjustmentMessage("");
    setError("");
    try {
      const payload = await applyAdjustmentCheckpoint(endpoint, checkpointId);
      if (payload.status === "pending") {
        setAdjustmentMessage("Apply approval is pending.");
      } else if (payload.ok) {
        setAdjustmentMessage("Adjustment checkpoint applied.");
      } else {
        setAdjustmentMessage(String(payload.error || "Apply request failed."));
      }
      await refresh();
      await loadCheckpoints();
    } catch (cause) {
      setError(cause instanceof Error ? cause.message : String(cause));
    } finally {
      setAdjustmentBusyId("");
    }
  }

  async function overwriteAdjustment(checkpointId: string) {
    if (!window.confirm("Overwrite this adjustment checkpoint with the current project state?")) {
      return;
    }
    setAdjustmentBusyId(`overwrite:${checkpointId}`);
    setAdjustmentMessage("");
    setError("");
    try {
      const payload = await overwriteAdjustmentCheckpoint(endpoint, checkpointId);
      setAdjustmentMessage("Adjustment checkpoint overwritten.");
      setAdjustmentPreview(payload.baseCheckpoint ? { ok: true, checkpoint: payload.baseCheckpoint, adjustmentCheckpoint: payload.checkpoint } : null);
      await loadCheckpoints();
    } catch (cause) {
      setError(cause instanceof Error ? cause.message : String(cause));
    } finally {
      setAdjustmentBusyId("");
    }
  }

  async function removeAdjustment(checkpointId: string) {
    if (!window.confirm("Delete this adjustment checkpoint?")) {
      return;
    }
    setAdjustmentBusyId(`delete:${checkpointId}`);
    setAdjustmentMessage("");
    setError("");
    try {
      await deleteAdjustmentCheckpoint(endpoint, checkpointId);
      setAdjustmentMessage("Adjustment checkpoint deleted.");
      if (adjustmentPreview?.adjustmentCheckpoint?.id === checkpointId) {
        setAdjustmentPreview(null);
      }
      await loadCheckpoints();
    } catch (cause) {
      setError(cause instanceof Error ? cause.message : String(cause));
    } finally {
      setAdjustmentBusyId("");
    }
  }

  async function saveSkill(event?: FormEvent) {
    event?.preventDefault();
    if (!skillDraft.name || savingSkill) {
      return;
    }
    setSavingSkill(true);
    setError("");
    try {
      let targetEndpoint = endpoint;
      if (!runtimeConnected) {
        const readyEndpoint = await startRuntime();
        if (!readyEndpoint) {
          return;
        }
        targetEndpoint = readyEndpoint;
      }
      const payload = selectedSkillName
        ? await updateSkill(targetEndpoint, selectedSkillName, skillDraft)
        : await createSkill(targetEndpoint, skillDraft);
      setSkillRegistry(payload);
      setSkillCheck(await checkSkills(targetEndpoint));
      setSelectedSkillName(payload.skill.name);
      setSkillDraft({ ...payload.skill });
      await refresh(targetEndpoint);
    } catch (cause) {
      setError(cause instanceof Error ? cause.message : String(cause));
    } finally {
      setSavingSkill(false);
    }
  }

  async function removeSelectedSkill() {
    if (!selectedSkillName || savingSkill) {
      return;
    }
    setSavingSkill(true);
    setError("");
    try {
      const payload = await deleteSkill(endpoint, selectedSkillName);
      setSkillRegistry(payload);
      setSkillCheck(await checkSkills(endpoint));
      setSelectedSkillName("");
      setSkillDraft(emptySkillDraft());
      await refresh();
    } catch (cause) {
      setError(cause instanceof Error ? cause.message : String(cause));
    } finally {
      setSavingSkill(false);
    }
  }

  async function saveApiProvider(event?: FormEvent) {
    event?.preventDefault();
    if (!apiProvider || !apiModel || (providerNeedsApiKey(apiProvider) && !apiKey.trim() && !apiKeySaved)) {
      return;
    }
    setSavingApiConfig(true);
    setError("");
    try {
      let targetEndpoint = endpoint;
      if (!runtimeConnected) {
        const readyEndpoint = await startRuntime();
        if (!readyEndpoint) {
          return;
        }
        targetEndpoint = readyEndpoint;
      }
      await updateApiConfig(targetEndpoint, {
        provider: apiProvider,
        api_key: apiKey.trim(),
        base_url: apiBaseUrl.trim(),
        model: apiModel.trim(),
      });
      setApiKey("");
      await refresh(targetEndpoint);
    } catch (cause) {
      setError(cause instanceof Error ? cause.message : String(cause));
    } finally {
      setSavingApiConfig(false);
    }
  }

  function handleProviderChange(provider: string) {
    setApiProvider(provider);
    setApiModel(defaultModelForProvider(provider));
    setApiBaseUrl(defaultBaseUrlForProvider(provider));
    setModelOptions([]);
    setModelOptionsScope(null);
    setModelsError("");
  }

  function handleVisionProviderChange(provider: string) {
    setVisionProvider(provider);
    setVisionModel(provider ? defaultModelForProvider(provider) : "");
    setVisionBaseUrl(provider ? defaultBaseUrlForProvider(provider) : "");
  }

  async function saveVisionProfile(event?: FormEvent) {
    event?.preventDefault();
    const visionKeySaved = Boolean(visionConfig?.apiKeyPresent && (visionConfig?.provider || "") === visionProvider);
    if (visionProvider && !visionModel.trim()) {
      return;
    }
    if (visionProvider && providerNeedsApiKey(visionProvider) && !visionApiKey.trim() && !visionKeySaved) {
      return;
    }
    setSavingVisionConfig(true);
    setError("");
    try {
      let targetEndpoint = endpoint;
      if (!runtimeConnected) {
        const readyEndpoint = await startRuntime();
        if (!readyEndpoint) {
          return;
        }
        targetEndpoint = readyEndpoint;
      }
      await updateVisionConfig(targetEndpoint, {
        provider: visionProvider,
        api_key: visionApiKey.trim(),
        base_url: visionBaseUrl.trim(),
        model: visionModel.trim(),
        enabled: visionEnabled,
      });
      setVisionApiKey("");
      await refresh(targetEndpoint);
    } catch (cause) {
      setError(cause instanceof Error ? cause.message : String(cause));
    } finally {
      setSavingVisionConfig(false);
    }
  }

  async function clearVisionProfile() {
    setSavingVisionConfig(true);
    setError("");
    try {
      await updateVisionConfig(endpoint, { provider: "", api_key: "", base_url: "", model: "", enabled: false });
      setVisionProvider("");
      setVisionApiKey("");
      setVisionBaseUrl("");
      setVisionModel("");
      setVisionEnabled(true);
      await refresh(endpoint);
    } catch (cause) {
      setError(cause instanceof Error ? cause.message : String(cause));
    } finally {
      setSavingVisionConfig(false);
    }
  }

  async function loadModels() {
    if (loadingModels) {
      return;
    }
    setLoadingModels(true);
    setModelsError("");
    try {
      let targetEndpoint = endpoint;
      if (!runtimeConnected) {
        const readyEndpoint = await startRuntime();
        if (!readyEndpoint) {
          setModelsError(t("provider.coreDisconnectedModels"));
          return;
        }
        targetEndpoint = readyEndpoint;
      }
      const payload = await fetchProviderModels(targetEndpoint, {
        provider: apiProvider,
        api_key: apiKey.trim(),
        base_url: apiBaseUrl.trim(),
        model: apiModel.trim(),
      });
      const models = payload.models || [];
      setModelOptions(models);
      setModelOptionsScope({ provider: payload.provider || apiProvider, baseUrl: payload.baseUrl || apiBaseUrl.trim() });
      if (models.length === 0) {
        setModelsError(t("provider.noModelsReturned"));
      } else if (!models.some((item) => item.id === apiModel)) {
        setApiModel(payload.selectedModel && models.some((item) => item.id === payload.selectedModel) ? payload.selectedModel : models[0].id);
      }
    } catch (cause) {
      setModelOptions([]);
      setModelOptionsScope(null);
      setModelsError(cause instanceof Error ? cause.message : String(cause));
    } finally {
      setLoadingModels(false);
    }
  }

  async function runProviderTest(capability: "text" | "structured" | "vision") {
    if (testingProvider) {
      return;
    }
    setTestingProvider(capability);
    setProviderTestMessage("");
    setModelsError("");
    try {
      let targetEndpoint = endpoint;
      if (!runtimeConnected) {
        const readyEndpoint = await startRuntime();
        if (!readyEndpoint) {
          setModelsError("Runtime is not connected.");
          return;
        }
        targetEndpoint = readyEndpoint;
      }
      const payload = await testProviderCapability(targetEndpoint, {
        provider: apiProvider,
        api_key: apiKey.trim(),
        base_url: apiBaseUrl.trim(),
        model: apiModel.trim(),
        capability,
      });
      setProviderTestMessage(`${payload.capability}: ${payload.status} - ${payload.message}`);
      if (!payload.ok && payload.status !== "skipped") {
        setModelsError(payload.message);
      }
    } catch (cause) {
      setModelsError(cause instanceof Error ? cause.message : String(cause));
    } finally {
      setTestingProvider("");
    }
  }

  return (
    <main className="h-screen overflow-hidden bg-background text-foreground">
      <div className="grid h-screen" style={{ gridTemplateColumns: workspaceGridColumns }}>
        <AppSidebar
          collapsed={leftSidebarCollapsed}
          activeView={activeView}
          temporaryChatActive={activeView === "chat" && !activeProjectPath && !activeChat}
          activeProjectPath={activeProjectPath}
          activeChatId={activeChatId}
          runtimeConnected={runtimeConnected}
          loadingProjects={loadingProjects}
          projectItems={projectItems}
          chatSidebar={chatSidebar}
          emptyProjectState={emptyProjectState}
          collapsedProjects={collapsedProjects}
          temporaryChatsCollapsed={Boolean(collapsedProjects[TEMP_CHATS_COLLAPSE_KEY])}
          pinnedProjectSet={pinnedProjectSet}
          renamingProjectPath={renamingProjectPath}
          projectRenameDraft={projectRenameDraft}
          renamingChatId={renamingChatId}
          renameDraft={renameDraft}
          projectDisplayName={projectDisplayName}
          onToggleSidebar={() => setLeftSidebarCollapsed((value) => !value)}
          onNewTemporaryChat={newTemporaryChat}
          onOpenProjectPicker={() => {
            setProjectModalError("");
            setShowProjectModal(true);
          }}
          onOpenDoctor={() => void openDoctor()}
          onOpenOptimization={() => void openOptimization()}
          onOpenProtection={() => void openProtection()}
          onOpenSkills={() => void openSkills()}
          onOpenCheckpoints={() => void openCheckpoints()}
          onOpenSettings={() => void openSettings()}
          onRefreshProjects={() => void refreshProjectList()}
          onSelectProject={selectProject}
          onToggleProjectCollapse={toggleProjectCollapse}
          onProjectMenu={(projectPath, event) => {
            event.preventDefault();
            event.stopPropagation();
            setProjectMenu({ projectPath, x: event.clientX, y: event.clientY });
          }}
          onProjectRenameChange={setProjectRenameDraft}
          onProjectRenameCommit={commitRenameProject}
          onOpenChat={openChat}
          onTogglePinChat={togglePinChat}
          onDeleteChat={setDeleteTargetId}
          onChatMenu={(chatId, event) => {
            event.preventDefault();
            setChatMenu({ chatId, x: event.clientX, y: event.clientY });
          }}
          onChatRenameChange={setRenameDraft}
          onChatRenameCommit={commitRenameChat}
        />

        <div
          role="separator"
          aria-orientation="vertical"
          aria-valuenow={Math.round(effectiveLeftPaneWidth)}
          aria-valuemin={COLLAPSED_LEFT_PANE_WIDTH}
          aria-valuemax={MAX_LEFT_PANE_WIDTH}
          data-layout-splitter="left"
          className="group relative h-screen cursor-col-resize touch-none bg-transparent"
          onPointerDown={(event) => startLayoutResize("left", event)}
          title={t("workspace.resizeLeftPane")}
        >
          <div className="absolute inset-y-0 left-1/2 w-px -translate-x-1/2 bg-border/80 transition-colors group-hover:bg-primary group-active:bg-primary" />
        </div>

        <section className="flex h-screen min-w-0 flex-col overflow-hidden bg-workspace">
          <header className="flex h-12 shrink-0 items-center justify-between border-b border-border/80 px-3 md:px-5">
            <div className="flex min-w-0 items-center gap-2 text-sm">
              <span className="truncate text-muted-foreground">{activeProjectPath ? activeProjectName : t("sidebar.tempChat")}</span>
              <span className="text-muted-foreground">/</span>
              <span className="truncate font-medium">
                {activeView === "doctor"
                  ? t("sidebar.doctor")
                  : activeView === "optimization"
                    ? t("sidebar.optimization")
                    : activeView === "protection"
                      ? t("encryption.protection")
                  : activeView === "skills"
                    ? t("sidebar.skills")
                    : activeView === "settings"
                      ? t("sidebar.settings")
                      : activeChat
                        ? activeChat.title || t("header.currentSession")
                        : t("header.newTask")}
              </span>
            </div>
            <div className="flex shrink-0 items-center gap-2">
              {permission?.roslynFullAuto ? (
                <Badge tone={currentPermissionVisual.badgeTone}>
                  <AlertTriangle className="mr-1 h-3.5 w-3.5 shrink-0" />
                  {t("header.fullPermission")}
                </Badge>
              ) : permission?.executionMode === "auto" ? (
                <Badge tone={currentPermissionVisual.badgeTone}>{t("header.autoApproval")}</Badge>
              ) : null}
              <StatusChip ok={runtimeConnected} label={runtimeConnected ? t("header.coreOnline") : t("header.coreOffline")} />
              <Badge tone={pendingApprovals > 0 ? "warn" : "muted"}>{formatCount(pendingApprovals)} {t("header.pendingApprovals")}</Badge>
              <RuntimeToolButton
                icon={rightSidebarCollapsed ? <PanelRightOpen className="h-4 w-4" /> : <PanelRightClose className="h-4 w-4" />}
                label={rightSidebarCollapsed ? t("workspace.showSidebar") : t("workspace.hideSidebar")}
                onClick={() => setRightSidebarCollapsed((value) => !value)}
              />
              <Button variant="ghost" className="h-9 w-9 px-0" onClick={() => setTheme(theme === "dark" ? "light" : "dark")}>
                {theme === "dark" ? <Sun className="h-4 w-4" /> : <Moon className="h-4 w-4" />}
              </Button>
            </div>
          </header>

          {showDoctorStartupPrompt ? (
            <div className="mx-auto mt-3 w-full max-w-4xl px-4">
                <div className="flex min-w-0 items-center gap-3 rounded-md border border-amber-200 bg-amber-50 px-3 py-2 text-xs text-amber-900 shadow-panel dark:border-amber-900/40 dark:bg-amber-950/30 dark:text-amber-100">
                <AlertTriangle className="h-4 w-4 shrink-0" />
                <div className="min-w-0 flex-1 space-y-0.5">
                  <div className="font-medium">
                    {hasStartupIssue ? t("header.startupIssueDetected") : t("header.envNeedsAttention")}
                  </div>
                  <div className="break-words text-amber-900/80 dark:text-amber-100/80">
                    {hasStartupIssue
                      ? t("header.startupIssueDesc")
                      : t("header.envNeedsAttentionDesc", { errors: healthErrors, warnings: healthWarnings })}
                  </div>
                  {hasStartupIssue ? <div className="break-words text-amber-900/70 dark:text-amber-100/70">{startupIssue}</div> : null}
                </div>
                <Button variant="outline" className="h-7 shrink-0 px-2 text-xs" onClick={() => void openDoctor()} disabled={loadingDoctor}>
                  {t("sidebar.doctor")}
                </Button>
                <Button variant="ghost" className="h-7 shrink-0 px-2 text-xs" onClick={() => void retryStartupOrHealth()} disabled={loading}>
                  {loading ? t("doctor.retrying") : t("doctor.retry")}
                </Button>
                <Button
                  variant="ghost"
                  className="h-7 shrink-0 px-2 text-xs"
                  onClick={() => setDismissedDoctorPromptSignature(doctorPromptSignature)}
                >
                  {t("common.dismiss")}
                </Button>
              </div>
            </div>
          ) : null}

          {error && !showDoctorStartupPrompt ? (
            <div className="mx-auto mt-3 w-full max-w-4xl px-4">
              <div className="flex items-center gap-3 rounded-md border border-destructive/15 bg-destructive/5 px-3 py-2 text-xs text-destructive/75">
                <span className="break-words">{error}</span>
                <Button
                  variant="ghost"
                  className="ml-auto h-7 shrink-0 px-2 text-xs text-destructive/80 hover:bg-destructive/10"
                  onClick={() => void startRuntime()}
                  disabled={loading}
                >
                  {loading ? t("header.reconnecting") : t("header.reconnect")}
                </Button>
              </div>
            </div>
          ) : null}

          {activeView === "doctor" ? (
            <DoctorWorkspace
              report={doctorReport}
              loading={loadingDoctor}
              message={doctorMessage}
              repairingUnityBridge={repairingUnityBridge}
              exportingSupportBundle={exportingSupportBundle}
              onRefresh={() => void loadDoctor()}
              onRepairUnityBridge={() => void repairUnityBridgeFromDoctor()}
              onOpenSettings={() => void openSettings()}
              onExportSupportBundle={() => void createSupportBundle()}
              onCopy={() => {
                if (!doctorReport) {
                  return;
                }
                void navigator.clipboard
                  .writeText(JSON.stringify(doctorReport, null, 2))
                  .then(() => setDoctorMessage(t("doctor.copiedSummary")))
                  .catch((cause) => setError(cause instanceof Error ? cause.message : String(cause)));
              }}
              formatPayload={formatPayload}
            />
          ) : activeView === "skills" ? (
            <SkillsWorkspace
              skills={skills}
              skillCount={skillCount}
              skillCheck={skillCheck}
              selectedSkillName={selectedSkillName}
              draft={skillDraft}
              saving={savingSkill}
              onSelect={selectSkill}
              onNew={newSkill}
              onCheck={runSkillCheck}
              onDraftChange={setSkillDraft}
              onSave={saveSkill}
              onDelete={removeSelectedSkill}
              packages={skillPackages}
              packageStore={skillPackageStore}
              packagesLoading={loadingSkillPackages}
              packageMessage={skillPackageMessage}
              packageError={skillPackageError}
              packageGovernance={skillPackageGovernance}
              packageAudit={skillPackageAudit}
              onRefreshPackages={() => void loadSkillPackages()}
              onPreflightPackage={preflightVskPackage}
              onImportPackage={importVskPackage}
              onExportPackage={exportVskPackage}
              onSetPackageEnabled={setVskPackageEnabled}
              onUninstallPackage={uninstallVskPackage}
              onSetSafeMode={setVskPackageSafeMode}
              onTrustSigner={trustVskPackageSigner}
              onRevokeSigner={revokeVskPackageSigner}
              onBlockPackage={blockVskPackage}
            />
          ) : activeView === "checkpoints" ? (
            <CheckpointWorkspace
              checkpoints={checkpoints}
              interruptedRecoveries={interruptedRecoveries}
              adjustmentCheckpoints={adjustmentCheckpoints}
              selectedProjectPath={activeProjectPath}
              preview={checkpointPreview}
              recoveryPreview={recoveryPreview}
              adjustmentPreview={adjustmentPreview}
              loading={loadingCheckpoints}
              restoringId={restoringCheckpointId}
              recoveryBusyId={recoveryBusyId}
              adjustmentBusyId={adjustmentBusyId}
              message={checkpointMessage}
              recoveryMessage={recoveryMessage}
              adjustmentMessage={adjustmentMessage}
              onRefresh={() => void loadCheckpoints()}
              onPreview={previewCheckpoint}
              onRestore={restoreCheckpoint}
              onPreviewRecovery={previewRecovery}
              onRestoreRecovery={restoreRecovery}
              onExportRecoveryBundle={exportRecoveryBundle}
              onResolveRecovery={resolveRecovery}
              onCreateAdjustment={createAdjustment}
              onPreviewAdjustment={previewAdjustment}
              onSelectAdjustment={selectAdjustment}
              onApplyAdjustment={applyAdjustment}
              onOverwriteAdjustment={overwriteAdjustment}
              onRenameAdjustment={renameAdjustment}
              onDeleteAdjustment={removeAdjustment}
            />
          ) : activeView === "protection" ? (
            <ProtectionWorkspace
              plan={protectionPlan}
              selectedProjectPath={activeProjectPath}
              avatarPath={protectionAvatarPath}
              avatars={protectionAvatars}
              profile={protectionProfile}
              ownsAssets={protectionOwnsAssets}
              loading={loadingProtection}
              loadingAvatars={loadingProtectionAvatars}
              message={protectionMessage}
              avatarMessage={protectionAvatarMessage}
              requestingFamily={requestingProtectionFamily}
              onAvatarPathChange={setProtectionAvatarPath}
              onProfileChange={setProtectionProfile}
              onOwnsAssetsChange={setProtectionOwnsAssets}
              onRefresh={() => void loadProtectionPlan()}
              onRefreshAvatars={() => void loadProtectionAvatars()}
              onRequestApply={(family) => void requestProtectionApply(family)}
            />
          ) : activeView === "optimization" ? (
            <OptimizationWorkspace
              report={optimizationReport}
              proofs={optimizationProofs}
              selectedProof={selectedOptimizationProof}
              endpoint={endpoint}
              permission={permission}
              selectedProjectPath={activeProjectPath}
              avatarPath={optimizationAvatarPath}
              avatars={optimizationAvatars}
              targetProfile={optimizationTargetProfile}
              loading={loadingOptimization}
              loadingProofs={loadingOptimizationProofs}
              loadingAvatars={loadingOptimizationAvatars}
              message={optimizationMessage}
              proofMessage={optimizationProofMessage}
              avatarMessage={optimizationAvatarMessage}
              actionOptions={optimizationActionOptions}
              requestingActionId={requestingOptimizationAction}
              requestingDependencyId={requestingOptimizationDependency}
              onAvatarPathChange={setOptimizationAvatarPath}
              onTargetProfileChange={setOptimizationTargetProfile}
              onRefresh={() => void loadOptimizationPlan()}
              onRefreshProofs={() => void loadOptimizationProofs()}
              onSelectProof={(runId) => void selectOptimizationProof(runId)}
              onRefreshAvatars={() => void loadOptimizationAvatars()}
              onActionOptionChange={updateOptimizationActionOption}
              onRequestAction={(card) => void requestOptimizationAction(card)}
              onRequestDependency={(dependency) => void requestOptimizationDependencyInstall(dependency)}
            />
          ) : activeView === "settings" ? (
            <SettingsWorkspace
              permission={permission ?? null}
              loading={loading}
              runtimeConnected={runtimeConnected}
              currentLanguage={i18n.language}
              apiProvider={apiProvider}
              apiKey={apiKey}
              apiBaseUrl={apiBaseUrl}
              apiModel={apiModel}
              apiKeySaved={apiKeySaved}
              savingApiConfig={savingApiConfig}
              modelOptions={modelOptions}
              loadingModels={loadingModels}
              modelsError={modelsError}
              testingProvider={testingProvider}
              providerTestMessage={providerTestMessage}
              visionConfig={visionConfig}
              visionProvider={visionProvider}
              visionApiKey={visionApiKey}
              visionBaseUrl={visionBaseUrl}
              visionModel={visionModel}
              visionEnabled={visionEnabled}
              savingVisionConfig={savingVisionConfig}
              diagnosticsStatus={diagnosticsStatus}
              diagnosticsMessage={diagnosticsMessage}
              loadingDiagnostics={loadingDiagnostics}
              exportingSupportBundle={exportingSupportBundle}
              connectorStatus={connectorStatus}
              loadingConnectors={loadingConnectors}
              connectorMessage={connectorMessage}
              selectedProjectPath={activeProjectPath}
              isDesktop={isTauriRuntime()}
              checkpointArchiveLimitInput={checkpointArchiveLimitInput}
              agentNotes={agentNotes}
              agentNotesLoaded={agentNotesLoaded}
              agentNotesPath={agentNotesPath}
              notesMessage={notesMessage}
              savingNotes={savingNotes}
              onSwitchMode={(mode) => void switchMode(mode)}
              onRestartOnboarding={restartOnboarding}
              onLocaleChange={(code) => void setLocale(code)}
              onLoadModels={() => void loadModels()}
              onProviderTest={(capability) => void runProviderTest(capability)}
              onProviderChange={handleProviderChange}
              onApiKeyChange={setApiKey}
              onApiBaseUrlChange={setApiBaseUrl}
              onApiModelChange={setApiModel}
              onSaveApiProvider={saveApiProvider}
              onVisionProviderChange={handleVisionProviderChange}
              onVisionApiKeyChange={setVisionApiKey}
              onVisionBaseUrlChange={setVisionBaseUrl}
              onVisionModelChange={setVisionModel}
              onVisionEnabledChange={setVisionEnabled}
              onSaveVisionProfile={saveVisionProfile}
              onClearVisionProfile={() => void clearVisionProfile()}
              onSetDebugLogging={(enabled) => void setDebugLogging(enabled)}
              onCreateSupportBundle={() => void createSupportBundle()}
              onCheckpointArchiveLimitInputChange={setCheckpointArchiveLimitInput}
              onSaveCheckpointArchiveLimit={() => void saveCheckpointArchiveLimit()}
              onOpenCheckpointArchiveFolder={(targetPath) => void openCheckpointArchiveFolder(targetPath)}
              onPickCheckpointArchiveDirectory={pickCheckpointArchiveDirectory}
              onDeleteCheckpointArchives={(ids) => void deleteCheckpointArchives(ids)}
              onRelocateCheckpointArchives={(directory) => void relocateCheckpointArchives(directory)}
              onLoadConnectors={() => void loadConnectors()}
              onUpdateGatewaySettings={(settings) => void updateGatewaySettings(settings)}
              onRunConnectorAction={(client, action) => void runConnectorAction(client, action)}
              onCopyConnectorText={(text, label) => {
                void navigator.clipboard
                  .writeText(text)
                  .then(() => setConnectorMessage(`${label} copied`))
                  .catch((cause) => setError(cause instanceof Error ? cause.message : String(cause)));
              }}
              onAgentNotesChange={(value) => {
                setAgentNotes(value);
                setNotesMessage("");
              }}
              onSaveNotes={saveNotes}
            />
          ) : conversation.length === 0 ? (
            <div className="flex min-h-0 flex-1 items-center justify-center p-5 md:p-8">
              <div className="w-full max-w-3xl">
                {projectPromptTitle ? <h1 className="mb-5 text-center text-2xl font-semibold tracking-normal">{projectPromptTitle}</h1> : null}
                <Composer
                  input={input}
                  setInput={setInput}
                  sending={sending}
                  permission={permission}
                  statusLabel={agentModeLabel}
                  projectLabel={activeProjectPath ? activeProjectName : ""}
                  onSubmit={submitMessage}
                  onStop={stopCurrentRun}
                  onSwitchMode={switchMode}
                  commands={slashCommands}
                  actions={composerActions}
                  onAction={runExplicitWorkspaceAction}
                  disabledReason={chatDisabledReason}
                  attachments={attachments}
                  onAttachFiles={(files) => void addComposerFiles(files)}
                  onRemoveAttachment={removeAttachment}
                  contextUsage={contextUsage}
                  providerLabel={providerSnapshot.providerLabel}
                  model={providerSnapshot.model}
                  projects={projectItems.map((project) => ({
                    key: projectKey(project),
                    name: project.name || shortPath(project.path || ""),
                  }))}
                  onBindProject={bindProject}
                />
              </div>
            </div>
          ) : (
            <>
              <div
                className="min-h-0 flex-1 overflow-auto px-4 py-6 md:px-6 md:py-8"
                onMouseUp={handleConversationMouseUp}
                onScroll={() => (selectionMenu ? setSelectionMenu(null) : undefined)}
              >
                <div className="mx-auto max-w-3xl space-y-7">
                  {conversation.map((item) => {
                    const approval = item.type === "agent" ? pendingApprovalForResponse(item.response) : null;
                    return (
                      <ConversationCard
                        key={item.id}
                        item={item}
                        approval={approval}
                        approvalAction={approval ? approvalActions[approval.id] : undefined}
                        feedback={messageFeedback[item.id]}
                        canRetry={!sending && item.id === latestRetryableItemId}
                        canEdit={!sending && item.id === latestEditableUserItemId}
                        onCopyItem={copyConversationItem}
                        onRetryItem={retryConversationItem}
                        onEditItem={editConversationMessage}
                        onFeedbackItem={setConversationFeedback}
                        onApprove={approveShell}
                        onReject={rejectShell}
                        onModifyApproval={modifyApprovalInComposer}
                        onOpenSettings={() => void openSettings()}
                        onOpenDoctor={() => void openDoctor()}
                      />
                    );
                  })}
                  {queued.map((turn, index) => (
                    <div key={turn.id} className="flex justify-end opacity-65">
                      <div className="max-w-[72%] rounded-2xl border border-border bg-muted/80 px-4 py-3 text-sm text-foreground">
                        <div className="mb-1 flex items-center gap-1 text-[10px] opacity-90">
                          <Loader2 className="h-3 w-3 animate-spin" />
                          {t("chat.queued")} {index + 1}
                        </div>
                        <p className="whitespace-pre-wrap break-words">{turn.text || t("attachments.fallbackTitle")}</p>
                        {turn.attachments.length ? <AttachmentStrip attachments={turn.attachments} compact /> : null}
                      </div>
                    </div>
                  ))}
                  <div ref={conversationEndRef} />
                </div>
              </div>
              <div className="shrink-0 bg-workspace/95 px-4 pb-4 pt-2 md:px-6 md:pb-5 md:pt-2">
                <div className="mx-auto max-w-3xl">
                  <Composer
                    input={input}
                    setInput={setInput}
                    sending={sending}
                    permission={permission}
                    statusLabel={agentModeLabel}
                    projectLabel={activeProjectPath ? activeProjectName : ""}
                    onSubmit={submitMessage}
                    onStop={stopCurrentRun}
                    onSwitchMode={switchMode}
                    commands={slashCommands}
                    actions={composerActions}
                    onAction={runExplicitWorkspaceAction}
                    compact
                    disabledReason={chatDisabledReason}
                    attachments={attachments}
                    onAttachFiles={(files) => void addComposerFiles(files)}
                    onRemoveAttachment={removeAttachment}
                    contextUsage={contextUsage}
                    providerLabel={providerSnapshot.providerLabel}
                    model={providerSnapshot.model}
                    projects={projectItems.map((project) => ({
                      key: projectKey(project),
                      name: project.name || shortPath(project.path || ""),
                    }))}
                    onBindProject={bindProject}
                  />
                </div>
              </div>
            </>
          )}
          {activeView !== "chat" && pendingApprovalItems.length > 0 ? (
            <div className="max-h-[40vh] shrink-0 overflow-auto border-t border-amber-500/20 bg-amber-500/5 px-6 py-3">
              <div className="mx-auto max-w-4xl space-y-3">
                {pendingApprovalItems.map((approval) => (
                  <ApprovalCard
                    key={approval.id}
                    approval={approval}
                    loading={loading}
                    onApprove={approveShell}
                    onReject={rejectShell}
                  />
                ))}
              </div>
            </div>
          ) : null}
        </section>
        <div
          role="separator"
          aria-orientation="vertical"
          aria-valuenow={Math.round(effectiveRightPaneWidth)}
          aria-valuemin={0}
          aria-valuemax={MAX_RIGHT_PANE_WIDTH}
          data-layout-splitter="right"
          className="group relative h-screen cursor-col-resize touch-none bg-transparent"
          onPointerDown={(event) => startLayoutResize("right", event)}
          title={t("workspace.resizeRightPane")}
        >
          <div className="absolute inset-y-0 left-1/2 w-px -translate-x-1/2 bg-border/80 transition-colors group-hover:bg-primary group-active:bg-primary" />
        </div>
        {rightSidebarCollapsed ? null : (
          <RightRuntimeSidebar
              runtimeConnected={runtimeConnected}
              loadingUnityStatus={loadingUnityStatus}
              hasEnvironmentAttention={hasEnvironmentAttention}
              hasStartupIssue={hasStartupIssue}
              workspaceProjectLabel={workspaceProjectLabel}
              backendComponent={backendComponent}
              unityBridgeLabel={unityBridgeLabel}
              unityBridgeComponent={unityBridgeComponent}
              unityToolsLabel={unityToolsLabel}
              unityToolsComponent={unityToolsComponent}
              providerCompactLabel={providerCompactLabel}
              providerComponent={providerComponent}
              reviewSummaryLabel={reviewSummaryLabel}
              changeSummaryLabel={changeSummaryLabel}
              workspaceDiffChanged={workspaceDiffChanged}
              workspaceDiff={workspaceDiff}
              runtimeNotice={runtimeNotice}
              pendingApprovalItems={pendingApprovalItems}
              runtimeRuns={runtimeRuns}
              runtimeRunsError={runtimeRunsError}
              rightRuntimeSectionsCollapsed={rightRuntimeSectionsCollapsed}
              agentGoals={agentGoals}
              agentMemory={agentMemory}
              desktopActions={desktopActions}
              workspaceStateError={workspaceStateError}
              runtimeReviewEvidence={runtimeReviewEvidence}
              runtimeFileReferences={runtimeFileReferences}
              workspaceDiffFiles={workspaceDiffFiles}
              workspaceDiffError={workspaceDiffError}
              loadingWorkspaceDiff={loadingWorkspaceDiff}
              workspaceDiffReviewOpen={workspaceDiffReviewOpen}
              loadingWorkspaceDiffPatch={loadingWorkspaceDiffPatch}
              runtimeSchedule={runtimeSchedule}
              visibleSubAgentTasks={visibleSubAgentTasks}
              selectedSubAgent={selectedSubAgent}
              selectedSubAgentPanelOpen={selectedSubAgentPanelOpen}
              refreshUnityStatus={refreshUnityStatus}
              onHideSidebar={() => setRightSidebarCollapsed(true)}
              openDoctor={openDoctor}
              localizeHealthMessage={localizeHealthMessage}
              toggleRightRuntimeSection={toggleRightRuntimeSection}
              refreshWorkspaceDiff={refreshWorkspaceDiff}
              toggleWorkspaceDiffReview={toggleWorkspaceDiffReview}
              inspectSubAgentTask={inspectSubAgentTask}
              onCloseSelectedSubAgentPanel={() => setSelectedSubAgentPanelOpen(false)}
              onOpenSelectedSubAgentPanel={() => setSelectedSubAgentPanelOpen(true)}
              subAgentRoleLabel={subAgentRoleLabel}
              subAgentStatusTone={subAgentStatusTone}
              displaySubAgentStatus={displaySubAgentStatus}
              formatPayload={formatPayload}
          />
        )}
      </div>

      <OnboardingOverlay
        open={showOnboarding}
        minimized={onboardingMinimized}
        stepIndex={onboardingStep}
        runtimeConnected={runtimeConnected}
        apiKeyPresent={Boolean(apiConfig?.apiKeyPresent)}
        hasProjects={projectItems.length > 0}
        loadingRuntime={loading}
        onRetryRuntime={() => void startRuntime()}
        onOpenSettings={() => {
          setOnboardingMinimized(true);
          void openSettings();
        }}
        onOpenProjectPicker={() => {
          setOnboardingMinimized(true);
          setProjectModalError("");
          setShowProjectModal(true);
        }}
        onResume={() => setOnboardingMinimized(false)}
        onFinish={finishOnboarding}
        onPreviousStep={() => setOnboardingStep((value) => Math.max(0, value - 1))}
        onNextStep={() => setOnboardingStep((value) => value + 1)}
      />

      <ProjectPickerModal
        open={showProjectModal}
        projects={projectItems}
        hiddenProjects={hiddenProjects}
        customPathSet={customPathSet}
        saving={savingProjectPrefs}
        newProjectPath={newProjectPath}
        error={projectModalError}
        onClose={() => {
          setShowProjectModal(false);
          setProjectModalError("");
        }}
        onSelectProject={(key) => {
          selectProject(key);
          setShowProjectModal(false);
          setProjectModalError("");
        }}
        onRemoveCustomProject={removeCustomProject}
        onRestoreProject={unhideProject}
        onNewProjectPathChange={setNewProjectPath}
        onClearError={() => setProjectModalError("")}
        onAddProjectPath={() => void addProjectPath()}
      />

      <SidebarMenus
        projectMenu={projectMenu}
        chatMenu={chatMenu}
        selectionMenu={selectionMenu}
        deleteTargetId={deleteTargetId}
        chats={chats}
        customPathSet={customPathSet}
        collapsedProjects={collapsedProjects}
        pinnedProjectSet={pinnedProjectSet}
        selectionMenuRef={selectionMenuRef}
        onCloseProjectMenu={() => setProjectMenu(null)}
        onTogglePinProject={togglePinProject}
        onOpenProjectFolder={(projectPath) => void openProjectFolder(projectPath)}
        onNewConversation={newConversation}
        onStartRenameProject={startRenameProject}
        onToggleProjectCollapse={toggleProjectCollapse}
        onArchiveProjectChats={archiveProjectChats}
        onHideProject={hideProject}
        onRemoveCustomProject={removeCustomProject}
        onAskInNewSession={askInNewSession}
        onAddSelectionToComposer={addSelectionToComposer}
        onCloseChatMenu={() => setChatMenu(null)}
        onTogglePinChat={togglePinChat}
        onStartRenameChat={startRenameChat}
        onDeleteChat={setDeleteTargetId}
        onCancelDeleteChat={() => setDeleteTargetId("")}
        onConfirmDeleteChat={deleteChatPermanently}
      />

    </main>
  );
}

function ApprovalCard({
  approval,
  loading,
  onApprove,
  onReject,
}: {
  approval: AgentApproval;
  loading: boolean;
  onApprove: (approvalId: string) => void;
  onReject: (approvalId: string) => void;
}) {
  const { t } = useTranslation();
  return (
    <section className="rounded-xl border border-amber-500/30 bg-amber-500/5 p-4 shadow-panel">
      <div className="flex min-w-0 items-center gap-2">
        <AlertTriangle className="h-4 w-4 shrink-0 text-amber-600" />
        <div className="truncate text-sm font-semibold">{t("header.pendingApprovals")}</div>
        <Badge tone="warn" className="ml-auto shrink-0">
          {approval.riskLevel || "high"}
        </Badge>
      </div>
      <div className="mt-4 grid gap-3">
        <DataLine label={t("approval.command")} value={approval.preview?.command || "-"} mono />
        <DataLine label={t("approval.directory")} value={approval.preview?.cwd || "-"} />
        <DataLine label={t("approval.reason")} value={approval.reason || "-"} />
      </div>
      <div className="mt-4 flex justify-end gap-2">
        <Button variant="outline" disabled={loading} onClick={() => onReject(approval.id)}>
          <X className="h-4 w-4" />
          {t("approval.reject")}
        </Button>
        <Button variant="primary" disabled={loading} onClick={() => onApprove(approval.id)}>
          <Check className="h-4 w-4" />
          {t("approval.approve")}
        </Button>
      </div>
    </section>
  );
}

function StatusChip({ ok, label }: { ok: boolean; label: string }) {
  return (
    <Badge tone={ok ? "ok" : "warn"} className="max-w-[180px]">
      <span className="truncate">{label}</span>
    </Badge>
  );
}
