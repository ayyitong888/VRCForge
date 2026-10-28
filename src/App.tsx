import { projectDiscoveryState } from "./lib/project-discovery-state";
import { conversationQuoteFromSelection } from "./lib/chat-selection-quote";
import { hasRecentConnectorSelfTest } from "./lib/connector-verification";
import { invoke } from "@tauri-apps/api/core";
import { listen } from "@tauri-apps/api/event";
import {
  Camera,
  Copy,
  FileText,
  GitBranch,
  ListChecks,
  MoreHorizontal,
  Monitor,
  MousePointer2,
  PanelRightOpen,
  Paperclip,
  RotateCcw,
  Search,
  Send,
  Square,
} from "lucide-react";
import { useTranslation } from "react-i18next";
import i18n, { setLocale, type LocaleCode } from "./i18n";
import {
  FormEvent,
  PointerEvent as ReactPointerEvent,
  type ReactNode,
  Suspense,
  lazy,
  useCallback,
  useEffect,
  useLayoutEffect,
  useMemo,
  useRef,
  useState,
} from "react";
import { PendingApprovalsStrip } from "./components/approvals/pending-approvals-strip";
import { ChatWorkspace } from "./components/chat/chat-workspace";
import { LayoutSplitter } from "./components/workspace/layout-splitter";
import { WorkspaceHeader } from "./components/workspace/workspace-header";
import { DoctorWorkspace } from "./components/doctor/doctor-workspace";
import { OptimizationWorkspace } from "./components/optimization/optimization-workspace";
import { ProtectionWorkspace } from "./components/protection/protection-workspace";
import { ComputerUseActivitySurface } from "./components/runtime/computer-use-activity-surface";
import { CheckpointWorkspace } from "./components/checkpoints/checkpoint-workspace";
import { SettingsWorkspace } from "./components/settings/settings-workspace";
import { CheckpointQuotaNotice } from "./components/settings/checkpoint-quota-notice";
import { SidebarMenus } from "./components/sidebar/sidebar-menus";
import { TransientFailureToast } from "./components/ui/transient-failure-toast";
import { TextEditContextMenu } from "./components/common/text-edit-context-menu";
import { OnboardingOverlay } from "./components/onboarding/onboarding-overlay";
import { ExternalSetupTour } from "./components/onboarding/external-setup-tour";
import { ProviderSetupTour } from "./components/onboarding/provider-setup-tour";
import { OnboardingLanguageGate } from "./components/onboarding/onboarding-language-gate";
import {
  persistOnboardingLanguageGateCompletion,
  readOnboardingStoredState,
  resolveOnboardingLaunchState,
} from "./components/onboarding/onboarding-language-gate-state";
import { OutfitImportPanel } from "./components/project/outfit-import-panel";
import { ProjectIndexPanel } from "./components/project/project-index-panel";
import { ProjectPickerModal } from "./components/project/project-picker-modal";
import type { ProjectType } from "./lib/chat-types";
import { SkillsWorkspace } from "./components/skills/skills-workspace";
import { VskOpenDialog } from "./components/skills/vsk-open-dialog";
import { SubAgentPanel } from "./components/subagents/sub-agent-panel";
import { groupSubAgentThreads } from "./lib/subagent-threads";
import { type UserAttachmentSource } from "./components/runtime/project-workbench-sections";
import { useApprovalExecution } from "./hooks/use-approval-execution";
import { useAppUpdate } from "./hooks/use-app-update";
import { AppUpdatePopup } from "./components/ui/app-update-popup";
import type { AppUpdatePromptState, AppUpdateResult } from "./lib/api/app-update";
import { useCheckpointWorkspaceController } from "./hooks/use-checkpoint-workspace-controller";
import { useChatRunController, type QueuedTurn } from "./hooks/use-chat-run-controller";
import { chatSaveErrorMessage, useChatSessions } from "./hooks/use-chat-sessions";
import { useConversationScroll } from "./hooks/use-conversation-scroll";
import { useContextCompactionController } from "./hooks/use-context-compaction-controller";
import { useDoctorFixController } from "./hooks/use-doctor-fix-controller";
import { useBackgroundGoalRuns } from "./hooks/use-background-goal-runs";
import { parseGoalWakeDirective } from "./hooks/use-goal-wake";
import { useProjectManagement } from "./hooks/use-project-management";
import { useOptimizationWorkspaceController } from "./hooks/use-optimization-workspace-controller";
import { useProtectionWorkspaceController } from "./hooks/use-protection-workspace-controller";
import { useProviderSettings } from "./hooks/use-provider-settings";
import { useRuntimeWorkspace } from "./hooks/use-runtime-workspace";
import { projectAgentQuestionContinuation, questionContinuationCancelTarget, questionContinuationDisplay, questionContinuationStatus, type RuntimeTurnEvent, useQuestionContinuationActivity, useRuntimeTurnContinuationDelivery } from "./hooks/use-runtime-turn-continuation";
import { useSettingsWorkspaceController } from "./hooks/use-settings-workspace-controller";
import { useSessionHandoff } from "./hooks/use-session-handoff";
import { useSkillsWorkspaceController } from "./hooks/use-skills-workspace-controller";
import { useTransientFailureNotice } from "./hooks/use-transient-failure-notice";
import { useThemeCustomization } from "./hooks/use-theme-customization";
import { TEMP_CHATS_COLLAPSE_KEY, type ActiveView, type SettingsSection } from "./lib/app-view";
import { presentApproval } from "./lib/approval-presentation";
import { approvalBelongsToRuntimeScope, filterQuestionsForRuntimeScope, questionBelongsToRuntimeScope } from "./lib/runtime-scope";
import { replyToSessionHandoff } from "./lib/api/session-handoff";
import { flushChatsBeforeQuit } from "./lib/app-quit";
import {
  DEVELOPER_OPTIONS_ENABLED_KEY,
  LAYOUT_PANE_WIDTHS_KEY,
  MAX_LEFT_PANE_WIDTH,
  MAX_RIGHT_PANE_WIDTH,
  MIN_CENTER_PANE_WIDTH,
  MIN_LEFT_PANE_WIDTH,
  MIN_RIGHT_PANE_WIDTH,
  ONBOARDING_FLAG_KEY,
  RESIZE_HANDLE_WIDTH,
  RIGHT_SIDEBAR_COLLAPSED_KEY,
  THEME_STORAGE_KEY,
  clampNumber,
  loadLayoutPaneWidths,
  loadAutomaticUpdateCheckEnabled,
  loadDeveloperOptionsEnabled,
  loadThemePreference,
  persistAutomaticUpdateCheckEnabled,
  type LayoutPaneWidths,
  type ThemeMode,
} from "./lib/app-preferences";
import { FALLBACK_ENDPOINT, isAbsoluteLocalPath, isRuntimeSessionVerificationError, isTauriRuntime } from "./lib/app-runtime";
import { useDashboardProjectSelection } from "./hooks/use-dashboard-project-selection";
import type { AgentRuntimeDeltaEvent } from "./lib/chat-streaming";
import { useRuntimeTurnActivity } from "./hooks/use-runtime-turn-activity";
import {
  buildChatHistory,
  buildContextUsageFromRuntime,
  cloneChatAttachments,
  conversationItemText,
  findProviderModelInfo,
  formatPayload,
  isRetryableConversationItem,
  latestAgentContextUsage,
  latestConversationItemId,
  normalizeProviderForContext,
  selectedTextAttachment,
} from "./lib/conversation-utils";
import { ingestChatAttachment } from "./lib/attachment-ingest";
import { serializeChatAttachments } from "./lib/conversation-utils";
import { latestContextWindowUsage, resolveContextLimit } from "./lib/context-compaction";
import { thinkingTraceLabel } from "./lib/provider-ui";
import { questionAnswerItem } from "./lib/question-answer";
import type { ChatAttachment, ComposerAction, ComposerActionId, ContextUsage, ConversationItem } from "./lib/chat-types";
import { executionModeLabel, permissionVisualState } from "./lib/permission-ui";
import { resolveComputerUseAccentHex } from "./lib/computer-use-visuals";
import { normalizeProjectPathKey, projectKey, shortPath } from "./lib/project-path";
import { sortSidebarProjects } from "./lib/sidebar-project-order";
import { asRecord, getHealthDetailNumber } from "./lib/runtime-parsing";
import { isVrcForgeUnityToolsReady } from "./lib/unity-readiness";
import { buildEmptyProjectState } from "./lib/sidebar-view";
import { localizeRuntimeHealthMessage } from "./lib/runtime-workspace-view";
import { resolveRuntimeAuthorizationStatus } from "./lib/runtime-authorization-status";
import {
  loadSubAgentReviewNotificationIds,
  parseApprovalNotificationAction,
  parseSubAgentReviewNotificationAction,
  saveSubAgentReviewNotificationIds,
  showApprovalNotification,
  showSubAgentReviewNotification,
  SUB_AGENT_REVIEW_NOTIFICATION_ACTION_EVENT,
} from "./lib/approval-notifications";
import { displaySubAgentStatus, subAgentRoleLabel, subAgentStatusTone } from "./lib/subagent-ui";
import {
  createMarkdownSmokeChatState,
  createSubAgentContextSmokeTask,
  isMarkdownSmokeMode,
} from "./lib/markdown-smoke";
import { parseDelegateCommand } from "./lib/subagent-delegate";
import { isAwaitingMergeReview, subAgentProposedNextAction } from "./lib/subagent-merge";
import {
  AGENT_QUESTION_NOTIFICATION_ACTION_EVENT,
  parseAgentQuestionNotificationAction,
} from "./lib/agent-question-notifications";
import { pickSubAgentName, reconcileSelectedSubAgent, updateSubAgentList } from "./lib/subagent-state";
import {
  AgentApproval,
  AgentQuestion,
  AgentRuntimeResponse,
  AgentReasoningTrace,
  SubAgentTask,
  SubAgentTaskList,
  ApiError,
  AppBootstrap,
  ProjectSnapshot,
  AdvancedSettingsState,
  acknowledgeSubAgentHandoff,
  DoctorReport,
  answerAgentQuestion,
  cancelSubAgent,
  createAgentGoal,
  createAgentMemory,
  createSubAgent,
  fetchBootstrap,
  fetchDoctor,
  ExecutionMode,
  PermissionState,
  fetchAgentDesktopActions,
  fetchAgentGoals,
  fetchAgentMemory,
  fetchAgentApprovals,
  fetchAgentQuestions,
  fetchAgentTurnResponse,
  fetchAppSession,
  fetchAppHealth,
  fetchSubAgent,
  fetchSubAgents,
  mergeSubAgent,
  requestAgentDesktopAction,
  requestAgentRunCancel,
  requestChatAttachmentImport,
  resumeSubAgent,
  refreshProjects,
  setAppSessionToken,
  retrySubAgent,
  updateAgentGoal,
  updatePermission,
  updateAdvancedSettings,
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
  originId?: string;
  sessionId?: string;
  turnId?: string;
  clientTurnId?: string;
  textDelta?: string;
  done?: boolean;
  payload?: unknown;
};

type EditingMessageDraft = {
  chatId: string;
  itemId: string;
  priorInput: string;
  priorAttachments: ChatAttachment[];
  draftText: string;
  draftAttachments: ChatAttachment[];
};

const MAX_ATTACHMENTS_PER_TURN = 8;
const STARTUP_BACKGROUND_REFRESH_DELAY_MS = 1200;
const PROJECT_SNAPSHOT_CACHE_KEY = "vrcforge_project_snapshot_cache";

function loadCachedProjectSnapshot(): ProjectSnapshot | null {
  try {
    const raw = window.localStorage.getItem(PROJECT_SNAPSHOT_CACHE_KEY);
    if (!raw) {
      return null;
    }
    const parsed = JSON.parse(raw) as ProjectSnapshot;
    return parsed && typeof parsed === "object" && Array.isArray(parsed.projects) ? parsed : null;
  } catch {
    return null;
  }
}

function cacheProjectSnapshot(snapshot: ProjectSnapshot): void {
  try {
    window.localStorage.setItem(PROJECT_SNAPSHOT_CACHE_KEY, JSON.stringify(snapshot));
  } catch {
    // A blocked or full preference store must not delay startup.
  }
}
const AsyncAppSidebar = lazy(() =>
  import("./components/sidebar/app-sidebar").then((module) => ({ default: module.AppSidebar })),
);
const AsyncRightRuntimeSidebar = lazy(() =>
  import("./components/runtime/runtime-sidebar").then((module) => ({ default: module.RightRuntimeSidebar })),
);
const AsyncSubAgentSidebar = lazy(() =>
  import("./components/subagents/sub-agent-sidebar").then((module) => ({
    default: module.SubAgentSidebar,
  })),
);

function SidebarPlaceholder({ side }: { side: "left" | "right" }) {
  return (
    <aside
      className={side === "left" ? "h-screen border-r border-border/70 bg-sidebar" : "h-screen border-l border-border/70 bg-sidebar"}
      data-vrcforge-sidebar-placeholder={side}
      aria-hidden="true"
    />
  );
}

function SidebarMountTracker({ side, onMounted, children }: { side: "left" | "right"; onMounted: () => void; children: ReactNode }) {
  useLayoutEffect(() => {
    const metrics = ((window as any).__vrcforgeStartupMetrics ||= {});
    metrics[side === "left" ? "leftSidebarMountedMs" : "rightSidebarMountedMs"] ??= Math.round(performance.now());
    onMounted();
  }, [onMounted, side]);
  return <>{children}</>;
}

export default function App() {
  const { t } = useTranslation();
  const initialChatState = useMemo(() => createMarkdownSmokeChatState(), []);
  const smokeMode = isMarkdownSmokeMode();
  const initialOnboardingState = useMemo(
    () => resolveOnboardingLaunchState(readOnboardingStoredState(), smokeMode),
    [smokeMode],
  );
  const initialSubAgentTask = useMemo(() => createSubAgentContextSmokeTask(), []);
  const [endpoint, setEndpoint] = useState(FALLBACK_ENDPOINT);
  const [bootstrap, setBootstrap] = useState<AppBootstrap | null>(null);
  const [cachedProjectSnapshot, setCachedProjectSnapshot] = useState<ProjectSnapshot | null>(() => loadCachedProjectSnapshot());
  const [agentApprovals, setAgentApprovals] = useState<AgentApproval[] | null>(null);
  const [backendMessage, setBackendMessage] = useState("starting");
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState("");
  const {
    notice: transientFailure,
    showTransientNotice,
    showTransientFailure,
    dismissTransientFailure,
  } = useTransientFailureNotice();
  const [theme, setTheme] = useState<ThemeMode>(() => loadThemePreference());
  const { themeCustomization, backgroundError: themeBackgroundError, updateThemeCustomization, resetThemeCustomization } = useThemeCustomization();
  const [input, setInput] = useState("");
  const [activeProjectPath, setActiveProjectPath] = useState("");
  const [activeProjectType, setActiveProjectType] = useState<ProjectType>("general");
  const [activeView, setActiveView] = useState<ActiveView>("chat");
  const [pendingApprovalPanelOpen, setPendingApprovalPanelOpen] = useState(false);
  const [activeSettingsSection, setActiveSettingsSection] = useState<SettingsSection>("general");
  const [developerOptionsEnabled, setDeveloperOptionsEnabled] = useState(() => loadDeveloperOptionsEnabled());
  const [developerOptionsEverEnabled, setDeveloperOptionsEverEnabled] = useState(false);
  const [computerUseEnabled, setComputerUseEnabled] = useState(false);
  const [computerUseEverEnabled, setComputerUseEverEnabled] = useState(false);
  const [backgroundGoalNotificationsEnabled, setBackgroundGoalNotificationsEnabled] = useState(true);
  const [backgroundGoalRefreshSignal, setBackgroundGoalRefreshSignal] = useState(0);
  const [memoryReviewRefreshSignal, setMemoryReviewRefreshSignal] = useState(0);
  const initialGlobalQuestionFetchRef = useRef(false);
  const [savingAdvancedSettings, setSavingAdvancedSettings] = useState(false);
  const [handoffSendOpen, setHandoffSendOpen] = useState(false);
  const [appUpdatePrompt, setAppUpdatePrompt] = useState<AppUpdatePromptState | null>(null);
  const [backgroundRuntimeReady, setBackgroundRuntimeReady] = useState(false);
  const [automaticUpdateCheckEnabled, setAutomaticUpdateCheckEnabled] = useState(
    () => loadAutomaticUpdateCheckEnabled(),
  );
  const [rightSidebarCollapsed, setRightSidebarCollapsed] = useState(() => {
    try {
      return window.localStorage.getItem(RIGHT_SIDEBAR_COLLAPSED_KEY) === "true";
    } catch {
      return false;
    }
  });
  const [layoutPaneWidths, setLayoutPaneWidths] = useState<LayoutPaneWidths>(() => loadLayoutPaneWidths());
  const [sidebarsVisible, setSidebarsVisible] = useState(false);
  const [leftSidebarMounted, setLeftSidebarMounted] = useState(false);
  const [rightSidebarMounted, setRightSidebarMounted] = useState(false);
  const [showOnboarding, setShowOnboarding] = useState(initialOnboardingState.showOnboarding);
  const [showOnboardingLanguageGate, setShowOnboardingLanguageGate] = useState(
    initialOnboardingState.showLanguageGate,
  );
  const [onboardingStep, setOnboardingStep] = useState(0);
  const [onboardingMinimized, setOnboardingMinimized] = useState(false);
  const [providerSetupGuide, setProviderSetupGuide] = useState(false);
  const [externalSetupGuide, setExternalSetupGuide] = useState(false);
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
  const [subAgentSidebarOpen, setSubAgentSidebarOpen] = useState(() => Boolean(initialSubAgentTask));
  const [subAgentPaneWidth, setSubAgentPaneWidth] = useState(() => Math.min(1000, Math.max(520, window.innerWidth * 0.6)));
  const [subAgentActionBusyTaskIds, setSubAgentActionBusyTaskIds] = useState<Set<string>>(() => new Set());
  const [attachments, setAttachments] = useState<ChatAttachment[]>([]);
  const [pendingReplyQuestionId, setPendingReplyQuestionId] = useState("");
  const [attachmentsLoading, setAttachmentsLoading] = useState(false);
  const attachmentLoadsRef = useRef(0);
  const replySubmitInFlightRef = useRef(false);
  const [editingMessage, setEditingMessage] = useState<EditingMessageDraft | null>(null);
  const [savingMessageEdit, setSavingMessageEdit] = useState(false);
  const messageEditSaveInFlightRef = useRef(false);
  const messageEditSnapshotRef = useRef<{ draft: EditingMessageDraft | null; chatId: string; busy: boolean }>({ draft: null, chatId: "", busy: false });
  const [selectionMenu, setSelectionMenu] = useState<{ x: number; y: number; text: string } | null>(null);
  const [doctorReport, setDoctorReport] = useState<DoctorReport | null>(null);
  const [loadingDoctor, setLoadingDoctor] = useState(false);
  const [doctorMessage, setDoctorMessage] = useState("");
  const [doctorMessageTone, setDoctorMessageTone] = useState<"ok" | "warn" | "danger">("ok");
  const [startupIssue, setStartupIssue] = useState("");
  const [dismissedDoctorPromptSignature, setDismissedDoctorPromptSignature] = useState("");
  const conversationEndRef = useRef<HTMLDivElement | null>(null);
  const persistChatsNowRef = useRef<() => Promise<void>>(async () => undefined);
  const quitRequestedRef = useRef(false);
  const projectInitRef = useRef(false);
  const refreshRuntimeRunsRef = useRef<(includeEvents?: boolean, target?: string) => Promise<void>>(async () => undefined);
  const pendingApprovalsRef = useRef<AgentApproval[]>([]);
  const knownApprovalNotificationIdsRef = useRef<Set<string>>(new Set());
  const exhaustedApprovalNotificationIdsRef = useRef<Set<string>>(new Set());
  const deliveredSubAgentReviewNotificationIdsRef = useRef<Set<string>>(loadSubAgentReviewNotificationIds());
  const knownSubAgentReviewNotificationIdsRef = useRef<Set<string>>(new Set(deliveredSubAgentReviewNotificationIdsRef.current));
  const inFlightSubAgentReviewNotificationIdsRef = useRef<Set<string>>(new Set());
  const handledSubAgentReviewNotificationIdsRef = useRef<Set<string>>(new Set());
  const exhaustedSubAgentReviewNotificationIdsRef = useRef<Set<string>>(new Set());
  const subAgentReviewNotificationActionHandlerRef = useRef<(payload: unknown) => void>(() => undefined);
  const subAgentReviewRuntimeConnectedRef = useRef(false);
  const subAgentTasksRef = useRef<SubAgentTask[]>(initialSubAgentTask ? [initialSubAgentTask] : []);
  const toReviewNotificationId = useCallback((task: SubAgentTask): string => {
    const revision = typeof task.revision === "number" ? String(task.revision) : "0";
    return `${task.id}:${revision}:${task.parentChatId || ""}`;
  }, []);
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
  const projectRefreshInFlightRef = useRef(false);
  const projectRefreshTimerRef = useRef<number | null>(null);
  const bootstrapRequestSequenceRef = useRef(0);
  const bootstrapForegroundRequestRef = useRef(0);
  const bootstrapSilentRefreshPromiseRef = useRef<Promise<void> | null>(null);
  const bootstrapSilentPendingTargetRef = useRef<string | null>(null);
  const bootstrapSilentActiveTargetRef = useRef<string | null>(null);
  const desktopEventBootstrapTimerRef = useRef<number | null>(null);
  const desktopEventRuntimeTimerRef = useRef<number | null>(null);
  const desktopEventSubAgentTimerRef = useRef<number | null>(null);
  const subAgentHandoffBusyRef = useRef(new Set<string>());
  const subAgentActionBusyRef = useRef(new Set<string>());
  const subAgentInspectRequestRef = useRef(0);
  const subAgentSelectionIntentRef = useRef(initialSubAgentTask?.id || "");
  const selectionMenuRef = useRef<HTMLDivElement | null>(null);
  const chatSessionActionsRef = useRef<{
    selectProject: (projectPath: string, projectType?: ProjectType) => void;
    newConversation: (projectPath?: string, projectType?: ProjectType) => void;
  } | null>(null);

  const permission = bootstrap?.permission;
  const currentPermissionVisual = permissionVisualState(permission);
  const apiConfig = bootstrap?.apiConfig;
  const visionConfig = bootstrap?.visionConfig;
  const healthComponents = bootstrap?.health.components ?? {};
  const healthErrors = Object.values(healthComponents).filter((item) => item.status === "error").length;
  const healthWarnings = Object.values(healthComponents).filter((item) => item.status === "warning").length;
  const runtimeConnected = Boolean(bootstrap?.ok);
  const showStartupUpdate = useCallback((result: AppUpdateResult) => {
    setAppUpdatePrompt({ source: "startup", result });
  }, []);
  const checkForAppUpdateNow = useAppUpdate(
    endpoint,
    backgroundRuntimeReady,
    automaticUpdateCheckEnabled,
    showStartupUpdate,
  );
  const authoritativeSelectedProjectPath = (
    bootstrap?.health.state?.selectedProjectPath
    || bootstrap?.health.projects?.selectedProjectPath
    || ""
  ).trim();
  const {
    apiProvider,
    apiKey,
    setApiKey,
    apiBaseUrl,
    setApiBaseUrl,
    apiModel,
    setApiModel,
    apiType,
    setApiType,
    apiContextWindow,
    setApiContextWindow,
    selectedModelCapabilities,
    selectedModelCapabilitySource,
    apiThinkingLevel,
    setApiThinkingLevel,
    reasoningVariants,
    apiKeySaved,
    savingApiConfig,
    modelOptions,
    modelOptionsScope,
    loadingModels,
    modelsError,
    testingProvider,
    providerTestMessage,
    providerTestPassed,
    providerTestPassedForPersistedConfig,
    providerReadyForOnboarding,
    visionProvider,
    visionApiKey,
    setVisionApiKey,
    visionBaseUrl,
    setVisionBaseUrl,
    visionModel,
    setVisionModel,
    visionEnabled,
    setVisionEnabled,
    savingVisionConfig,
    savedBaseUrl,
    providerConfigured,
    providerSnapshot,
    saveApiProvider,
    handleProviderChange,
    handleProtocolAutoNegotiationChange,
    handleVisionProviderChange,
    saveVisionProfile,
    clearVisionProfile,
    loadModels,
    runProviderTest,
  } = useProviderSettings({
    endpoint,
    runtimeConnected,
    apiConfig,
    visionConfig,
    startRuntime,
    refresh,
    setError,
  });
  const {
    diagnosticsStatus,
    loadingDiagnostics,
    exportingSupportBundle,
    diagnosticsMessage,
    agentNotes,
    agentNotesPath,
    agentNotesLoaded,
    savingNotes,
    notesMessage,
    connectorStatus,
    loadingConnectors,
    connectorMessage,
    checkpointArchiveLimitInput,
    openSettings,
    loadDiagnostics,
    setLogLevel,
    createSupportBundle,
    loadConnectors,
    updateGatewaySettings,
    saveCheckpointArchiveLimit,
    openCheckpointArchiveFolder,
    openLogsFolder,
    pickCheckpointArchiveDirectory,
    deleteCheckpointArchives,
    relocateCheckpointArchives,
    runConnectorAction,
    saveNotes,
    setCheckpointArchiveLimitInput,
    updateAgentNotes,
    copyConnectorText,
  } = useSettingsWorkspaceController({
    endpoint,
    runtimeConnected,
    activeProjectPath,
    setActiveView,
    startRuntime,
    refresh,
    setError,
    setDoctorMessage,
  });
  const {
    skills,
    skillCount,
    skillCheck,
    selectedSkillName,
    skillDraft,
    savingSkill,
    skillPackages,
    skillPackageStore,
    skillPackageGovernance,
    skillPackageAudit,
    loadingSkillPackages,
    skillPackageMessage,
    skillPackageError,
    pathToSkillDraftSeed,
    openSkills,
    openSkillsWithCapturedPath,
    loadSkillPackages,
    preflightVskPackage,
    importVskPackage,
    exportVskPackage,
    previewCapturedPath,
    writeCapturedPath,
    setVskPackageEnabled,
    uninstallVskPackage,
    setVskPackageSafeMode,
    trustVskPackageSigner,
    revokeVskPackageSigner,
    blockVskPackage,
    selectSkill,
    newSkill,
    runSkillCheck,
    saveSkill,
    removeSelectedSkill,
    setSkillDraft,
  } = useSkillsWorkspaceController({
    endpoint,
    runtimeConnected,
    bootstrapSkills: bootstrap?.agentManifest?.skills ?? [],
    activeView,
    setActiveView,
    startRuntime,
    refresh,
    setError,
    t,
  });
  const vrcForgeToolsCount = getHealthDetailNumber(healthComponents.vrcForgeUnityTools?.detail, "vrcForgeToolsCount");
  const vrcForgeToolsReady = isVrcForgeUnityToolsReady(
    runtimeConnected,
    healthComponents.vrcForgeUnityTools,
  );
  const {
    optimizationReport,
    optimizationTargetProfile,
    optimizationAvatarPath,
    optimizationAvatars,
    loadingOptimizationAvatars,
    optimizationAvatarMessage,
    loadingOptimization,
    optimizationMessage,
    requestingOptimizationAction,
    requestingOptimizationDependency,
    optimizationActionOptions,
    optimizationProofs,
    selectedOptimizationProof,
    loadingOptimizationProofs,
    optimizationProofMessage,
    openOptimization,
    loadOptimizationPlan,
    loadOptimizationProofs,
    selectOptimizationProof,
    loadOptimizationAvatars,
    setOptimizationAvatarPath,
    setOptimizationTargetProfile,
    updateOptimizationActionOption,
    requestOptimizationAction,
    requestOptimizationDependencyInstall,
  } = useOptimizationWorkspaceController({
    endpoint,
    runtimeConnected,
    unityToolsReady: vrcForgeToolsReady,
    activeView,
    activeProjectPath,
    setActiveView,
    startRuntime,
    refreshSilently,
    setError,
  });
  const {
    protectionPlan,
    protectionProfile,
    protectionAvatarPath,
    protectionAvatars,
    protectionOwnsAssets,
    loadingProtection,
    loadingProtectionAvatars,
    protectionMessage,
    protectionAvatarMessage,
    requestingProtectionFamily,
    openProtection,
    loadProtectionPlan,
    loadProtectionAvatars,
    requestProtectionApply,
    setProtectionProfile,
    setProtectionAvatarPath,
    setProtectionOwnsAssets,
  } = useProtectionWorkspaceController({
    endpoint,
    runtimeConnected,
    activeView,
    activeProjectPath,
    setActiveView,
    startRuntime,
    refreshSilently,
    setError,
  });
  const hasStartupIssue = startupIssue.trim().length > 0;
  const hasEnvironmentAttention = runtimeConnected && (healthErrors > 0 || healthWarnings > 0);
  const doctorPromptSignature = hasStartupIssue
    ? `startup:${startupIssue.trim()}`
    : `health:${Object.entries(healthComponents)
        .map(([id, component]) => `${id}:${component.status}:${component.message}`)
        .join("|")}`;
  const showDoctorStartupPrompt =
    activeView !== "doctor" && dismissedDoctorPromptSignature !== doctorPromptSignature && (hasStartupIssue || hasEnvironmentAttention);
  const slashCommands = useMemo(() => {
    const list: Array<{ name: string; title: string }> = [
      { name: "compact", title: t("chat.slashCompact") },
      { name: "goal", title: t("chat.slashGoal") },
      { name: "memory", title: t("chat.slashMemory") },
      { name: "delegate", title: t("chat.slashDelegate") },
    ];
    list.push({ name: "handoff", title: t("chat.slashHandoff") });
    if (developerOptionsEnabled && computerUseEnabled) {
      list.push({ name: "desktop", title: t("composerAction.desktop") });
    }
    return list;
  }, [computerUseEnabled, developerOptionsEnabled, t]);
  // Keep the last known project list visible while backend startup/refresh is still pending.
  const projects = bootstrap?.health.projects?.projects ?? cachedProjectSnapshot?.projects ?? [];
  const projectScanState = projectDiscoveryState(bootstrap?.health.projects ?? cachedProjectSnapshot ?? undefined);
  const externalAgentGatewayEnabled = Boolean(connectorStatus?.gateway?.enabled);
  const externalAgentVerified = hasRecentConnectorSelfTest(connectorStatus, runtimeConnected, activeProjectPath);
  const chatAvailable = providerConfigured || externalAgentGatewayEnabled;
  const chatDisabledReason = !runtimeConnected
    ? t("agent.modeLabel.notConnected")
    : !chatAvailable
      ? t("chat.providerNotConfigured")
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
      if (developerOptionsEnabled && computerUseEnabled) {
        actions.push({
          id: "desktop",
          label: t("composerAction.desktop"),
          description: t("composerAction.desktopDesc"),
        });
      }
      return actions;
    },
    [computerUseEnabled, developerOptionsEnabled, t, vrcForgeToolsReady],
  );
  const {
    showProjectModal,
    setShowProjectModal,
    newProjectPath,
    setNewProjectPath,
    newProjectType,
    setNewProjectType,
    savingProjectPrefs,
    projectModalError,
    setProjectModalError,
    projectPrefs,
    projectPrefsReady,
    loadingProjects,
    setLoadingProjects,
    projectMenu,
    setProjectMenu,
    renamingProjectPath,
    projectRenameDraft,
    setProjectRenameDraft,
    projectIndex,
    projectIndexProject,
    loadingProjectIndex,
    projectIndexError,
    outfitPackagePath,
    setOutfitPackagePath,
    outfitImportPlan,
    outfitImportStatus,
    loadingOutfitImportPlan,
    requestingOutfitImport,
    collapsedProjects,
    customPathSet,
    pinnedProjectSet,
    projectOrder,
    reorderProjects,
    resetProjectOrder,
    projectItems,
    hiddenProjects,
    addProjectPath,
    removeCustomProject,
    hideProject,
    unhideProject,
    projectDisplayName,
    projectDisplayLabel,
    togglePinProject,
    startRenameProject,
    commitRenameProject,
    openProjectFolder,
    scanActiveProjectIndex,
    planActiveOutfitImport,
    requestActiveOutfitImport,
    editingProjectPath,
    editProjectPathDraft,
    setEditProjectPathDraft,
    startEditProject,
    cancelEditProject,
    saveEditProject,
    projectListError,
    refreshProjectList: refreshManagedProjectList,
    toggleProjectCollapse,
    expandProjectGroup,
  } = useProjectManagement({
    endpoint,
    runtimeConnected,
    activeProjectPath,
    activeProjectType,
    projects,
    refresh,
    refreshProjects: (target?: string) => refreshProjectList(target, { throwOnError: true, allowDuringStartup: true }),
    startRuntime,
    setError,
    onProjectAdded: (projectPath, projectType) => {
      chatSessionActionsRef.current?.selectProject(projectPath, projectType);
    },
    onActiveProjectHidden: () => {
      chatSessionActionsRef.current?.newConversation("");
    },
  });
  const chatSessionProjectPaths = useMemo(() => projectItems.map((project) => projectKey(project)).filter(Boolean), [projectItems]);
  const {
    chats,
    chatStorageReady,
    activeChat,
    displayChat,
    activeChatPending,
    activeChatId,
    setActiveChatId,
    chatMenu,
    setChatMenu,
    renamingChatId,
    renameDraft,
    setRenameDraft,
    deleteTargetId,
    setDeleteTargetId,
    chatSidebar,
    chatRecoveries,
    chatPersistenceBlocked,
    resolvingChatStorageConflict,
    resolveChatStorageConflict,
    reloadChatStorageState,
    notifyChatStorageChanged,
    touchChat,
    updateChat,
    updateChatIfRevision,
    appendToChat,
    ensureActiveChat,
    persistChatsNow,
    getChatById,
    newConversation,
    togglePinChat,
    toggleTestChat,
    startRenameChat,
    commitRenameChat,
    deleteChatPermanently,
    bindProject,
    newTemporaryChat,
    archiveProjectChats,
    openChat,
    selectProject,
  } = useChatSessions({
    activeView,
    endpoint,
    runtimeConnected,
    projectPrefsReady,
    projectPaths: chatSessionProjectPaths,
    customProjectPaths: projectPrefs.customPaths,
    activeProjectPath,
    activeProjectType,
    setActiveProjectType,
    setActiveProjectPath,
    setActiveView,
    setError,
    expandProjectGroup,
    initialChatState,
  });
  persistChatsNowRef.current = persistChatsNow;
  const sessionHandoff = useSessionHandoff(endpoint, activeChatId);
  const handoffTargetChats = useMemo(() => {
    const sourceScope = normalizeProjectPathKey(activeChat?.projectPath || "");
    return chats
      .filter((chat) => chat.id !== activeChatId && normalizeProjectPathKey(chat.projectPath || "") === sourceScope)
      .slice(0, 32)
      .map((chat) => ({ id: chat.id, title: chat.title || t("header.currentSession") }));
  }, [activeChat?.projectPath, activeChatId, chats, t]);
  const updateHandoffCardStatus = (handoffId: string, status: string) => {
    if (!activeChatId) return;
    updateChat(activeChatId, (chat) => ({
      ...chat,
      items: chat.items.map((item) => item.type === "handoff_card" && item.handoffId === handoffId ? { ...item, status } : item),
    }));
  };
  const acceptSessionHandoff = (handoffId: string) => { void sessionHandoff.accept(handoffId).then(() => updateHandoffCardStatus(handoffId, "materialized")); };
  const dismissSessionHandoff = (handoffId: string) => { void sessionHandoff.dismiss(handoffId).then(() => updateHandoffCardStatus(handoffId, "dismissed")); };
  const pauseSessionHandoff = (handoffId: string) => { void sessionHandoff.pause(handoffId).then(() => updateHandoffCardStatus(handoffId, "paused")); };
  const resumeSessionHandoff = (handoffId: string) => { void sessionHandoff.resume(handoffId).then(() => updateHandoffCardStatus(handoffId, "pending_review")); };
  const replySessionHandoff = (handoffId: string, text: string) => {
    const card = activeChat?.items.find((item) => item.type === "handoff_card" && item.handoffId === handoffId);
    if (!card || card.type !== "handoff_card" || !card.sourceChatId || !card.targetChatId) return;
    void replyToSessionHandoff(endpoint, card.targetChatId, card.sourceChatId, handoffId, text);
  };
  const sidebarProjectItems = useMemo(
    () => sortSidebarProjects(projectItems, chats, pinnedProjectSet, projectOrder),
    [chats, pinnedProjectSet, projectItems, projectOrder],
  );
  chatSessionActionsRef.current = { selectProject, newConversation };
  useDashboardProjectSelection({
    endpoint,
    runtimeConnected,
    projectPath: activeProjectPath,
    projectType: activeProjectType,
    activeChatId,
    confirmedProjectPath: authoritativeSelectedProjectPath,
    onConfirmedProjectPath: (projectPath) => chatSessionActionsRef.current?.selectProject(projectPath, "unity"),
    setBootstrap,
    setError,
  });
  const projectTypeForPath = (projectPath: string): ProjectType => {
    const normalized = normalizeProjectPathKey(projectPath);
    const project = projectItems.find((candidate) => normalizeProjectPathKey(projectKey(candidate)) === normalized);
    return project?.projectType === "general" ? "general" : "unity";
  };
  const selectProjectByPath = (projectPath: string) => selectProject(projectPath, projectTypeForPath(projectPath));
  const onboardingSelectedProjectReady = Boolean(
    activeProjectPath && projectItems.some((project) => normalizeProjectPathKey(projectKey(project)) === normalizeProjectPathKey(activeProjectPath)),
  );
  const onboardingProjectMatchesBackend = !authoritativeSelectedProjectPath || normalizeProjectPathKey(authoritativeSelectedProjectPath) === normalizeProjectPathKey(activeProjectPath);
  const onboardingUnityToolsReady = onboardingSelectedProjectReady && onboardingProjectMatchesBackend && vrcForgeToolsReady;
  const newConversationForProject = (projectPath?: string) => newConversation(
    projectPath,
    projectPath ? projectTypeForPath(projectPath) : "general",
  );
  const settledQuestionContinuationIdsRef = useRef(new Set<string>());
  const deliverRuntimeTurnContinuation = useRuntimeTurnContinuationDelivery({
    chats,
    appendToChat,
    updateChat,
    onContinuation: (event: RuntimeTurnEvent) => {
      if (event.continuationSource !== "question_answered") return;
      const questionId = event.clientTurnId?.split(":question:")[1]?.trim();
      if (!questionId) return;
      const clientTurnId = event.clientTurnId?.trim();
      if (clientTurnId) {
        settledQuestionContinuationIdsRef.current.add(clientTurnId);
        const ownerChat = chats.find((chat) => chat.sessionId === event.sessionId
          && chat.items.some((item) => item.type === "streaming" && item.id === `question-continuation:${clientTurnId}`));
        if (ownerChat) {
          updateChat(ownerChat.id, (current) => ({
            ...current,
            items: current.items.filter((item) => item.id !== `question-continuation:${clientTurnId}`),
          }));
        }
      }
      const nextStep = String(event.plan?.nextStep || "").toLowerCase();
      const status = questionContinuationStatus("delivered", nextStep);
      updateAgentQuestionContinuation(questionId, {
        status,
        turnId: event.turnId,
        clientTurnId: event.clientTurnId,
        error: status === "failed" ? event.plan?.reply : undefined,
      });
    },
  });
  useEffect(() => {
    for (const continuation of bootstrap?.runtimeContinuations ?? []) {
      deliverRuntimeTurnContinuation(continuation);
    }
  }, [bootstrap?.runtimeContinuations, chats, deliverRuntimeTurnContinuation]);

  function openTemporaryChat() {
    projectInitRef.current = true;
    newTemporaryChat();
  }

  const conversation = displayChat?.items ?? [];
  const conversationScroll = useConversationScroll({
    chatKey: `${endpoint}\n${activeChatId || ""}`,
    endRef: conversationEndRef,
  });
  const sessionId = activeChat?.sessionId ?? "";
  const activeRuntimeProjectPath = activeChat?.projectPath || activeProjectPath;
  const hasAgentRuntimeScope = Boolean(sessionId || activeRuntimeProjectPath);
  const latestEditableUserItemId = latestConversationItemId(conversation, (item) => item.type === "user");
  const latestRetryableItemId = latestConversationItemId(conversation, isRetryableConversationItem);
  const runtimeScope = { sessionId, projectRoot: activeRuntimeProjectPath };
  const projectPendingApprovalItems = (agentApprovals ?? []).filter(
    (item) => item.status === "pending" && approvalBelongsToRuntimeScope(item, runtimeScope),
  );
  const pendingApprovalItems = projectPendingApprovalItems.filter(
    (item) => approvalBelongsToRuntimeScope(item, runtimeScope, { requireSession: true }),
  );
  const pendingApprovals = projectPendingApprovalItems.length;
  const hasUnresolvedRuntimeState = (chatId: string, targetSessionId: string): boolean => {
    const asRecord = (value: unknown): Record<string, unknown> | undefined => (
      typeof value === "object" && value !== null ? value as Record<string, unknown> : undefined
    );
    const owns = (item: unknown) => {
      const record = asRecord(item);
      const taskContext = asRecord(record?.taskContext ?? record?.task_context);
      const scopedValues = [
        record?.sessionId,
        record?.session_id,
        record?.chatId,
        record?.chat_id,
        taskContext?.sessionId,
        taskContext?.session_id,
        taskContext?.chatId,
        taskContext?.chat_id,
      ].filter((value): value is string => typeof value === "string");
      return scopedValues.includes(chatId) || (Boolean(targetSessionId) && scopedValues.includes(targetSessionId));
    };
    if ((agentApprovals ?? []).some((item) => ["pending", "approved", "applying"].includes(item.status) && owns(item))) return true;
    if (agentQuestions.some((item) => item.status === "pending"
      && questionBelongsToRuntimeScope(item, runtimeScope, { requireSession: true })
      && owns(item))) return true;
    if (runtimeRuns.some((item) => ["running", "queued", "cancelling", "waiting_for_model", "waiting_for_tool"].includes(String(item.status || "")) && owns(item))) return true;
    if (activeDesktopActions.some((item) => owns(item))) return true;
    return subAgentTasksRef.current.some((item) => ["queued", "running", "cancelling"].includes(item.status)
      && (item.parentChatId === chatId || item.parentSessionId === targetSessionId));
  };
  const {
    compacting,
    compactChat: runContextCompaction,
    prepareTurnContext,
    cancelCompaction,
  } = useContextCompactionController({
    getChatById,
    updateChat,
    updateChatIfRevision,
    persistChatsNow,
    setError,
    hasUnresolvedRuntimeState,
  });
  const {
    sending: chatRunSending,
    queued,
    currentTurn,
    stopRequested,
    isRunning: isChatRunActive,
    submitTurn,
    editQueuedTurn,
    removeQueuedTurn,
    steerQueuedTurn,
    runTurnNow,
    runBackgroundTurn,
    stopCurrentRun,
    resumeQueuedTurns,
    cancelQueuedTurns,
    applyRuntimeDelta,
  } = useChatRunController({
    endpoint,
    runtimeConnected,
    sessionId,
    activeRuntimeProjectPath,
    getChatById,
    ensureActiveChat,
    updateChat,
    appendToChat,
    touchChat,
    startRuntime,
    refresh,
    refreshRuntimeRuns: (includeEvents, target) => refreshRuntimeRunsRef.current(includeEvents, target),
    refreshBackgroundGoals: () => setBackgroundGoalRefreshSignal((current) => current + 1),
    handleRuntimeSessionFailure,
    setError,
    notifyFailure: showTransientFailure,
    prepareTurnContext,
    persistChatsNow,
    traceSteerDelivery: developerOptionsEnabled
      ? (event) => console.debug("[vrcforge:steer-delivery]", event)
      : undefined,
    chats,
    chatStorageReady,
  });
  const sending = chatRunSending || compacting;
  const visibleQueued = useMemo(
    () => queued.filter((turn) => turn.chatId === activeChat?.id),
    [activeChat?.id, queued],
  );
  const {
    workspaceDiff,
    loadingWorkspaceDiff,
    workspaceDiffError,
    workspaceDiffReviewOpen,
    loadingWorkspaceDiffPatch,
    loadingUnityStatus,
    runtimeRuns,
    runtimeAttention,
    desktopActions,
    activeDesktopActions,
    desktopBridge,
    cancellingDesktopActionIds,
    agentGoals,
    agentProgress,
    agentQuestions,
    notifyCallUserQuestions,
    agentMemory,
    memoryReviewUnreadCount,
    memoryReviewNeedsAttention,
    workspaceStateError,
    runtimeNotice,
    setRuntimeNotice,
    refreshUnityStatus,
    refreshWorkspaceDiff,
    refreshRuntimeRuns,
    toggleWorkspaceDiffReview,
    prependDesktopAction,
    cancelDesktopAction,
    upsertAgentGoal,
    upsertAgentQuestion,
    updateAgentQuestionContinuation,
    upsertAgentMemory,
  } = useRuntimeWorkspace({
    endpoint,
    runtimeConnected,
    sessionId,
    activeRuntimeProjectPath,
    activeProjectPath,
    sending,
    pendingApprovals,
    setBootstrap,
    setAgentApprovals,
    setError,
    chatIdForSessionId: (ownerSessionId) =>
      chats.find((chat) => chat.sessionId === ownerSessionId)?.id || "",
  });
  const applyQuestionRuntimeDelta = useQuestionContinuationActivity({
    chats,
    questions: agentQuestions,
    runs: runtimeRuns,
    updateChat,
    settledClientTurnIds: settledQuestionContinuationIdsRef.current,
  });
  const applyExternalRuntimeDelta = useRuntimeTurnActivity({
    endpoint, runtimeConnected, chats, runs: runtimeRuns, updateChat,
    deliver: deliverRuntimeTurnContinuation, recoverySignal: bootstrap,
  });
  const queuedQuestionSessionIds = useMemo(() => new Set(queued.map((turn) => (
    turn.sessionId || chats.find((chat) => chat.id === turn.chatId)?.sessionId || ""
  )).filter(Boolean)), [chats, queued]);
  const pendingQuestionSessionIds = useMemo(() => new Set(agentQuestions.flatMap((question) => {
    const projected = projectAgentQuestionContinuation(question, runtimeRuns);
    const status = questionContinuationDisplay(projected);
    const questionSessionId = projected.runtimeContinuation?.sessionId || projected.sessionId || "";
    return questionSessionId && status !== "settled" ? [questionSessionId] : [];
  })), [agentQuestions, runtimeRuns]);
  const previousPendingQuestionSessionIdsRef = useRef(new Set<string>());
  const resumeQueuedTurnsRef = useRef(resumeQueuedTurns);
  resumeQueuedTurnsRef.current = resumeQueuedTurns;
  const queuedQuestionSessionKey = [...queuedQuestionSessionIds].sort().join("|");
  const pendingQuestionSessionKey = [...pendingQuestionSessionIds].sort().join("|");
  useEffect(() => {
    const wasPending = previousPendingQuestionSessionIdsRef.current;
    const settledQueuedQuestion = [...wasPending].some((targetSessionId) => (
      queuedQuestionSessionIds.has(targetSessionId)
      && !pendingQuestionSessionIds.has(targetSessionId)
    ));
    previousPendingQuestionSessionIdsRef.current = new Set(pendingQuestionSessionIds);
    if (settledQueuedQuestion) void resumeQueuedTurnsRef.current();
  }, [pendingQuestionSessionKey, queuedQuestionSessionKey]);
  const activeAgentGoal = useMemo(() => {
    const currentSessionId = activeChat?.sessionId || sessionId;
    return agentGoals.find((goal) => {
      const status = String(goal.status || "").toLowerCase();
      if (status !== "active" && status !== "paused" && status !== "blocked") return false;
      if (goal.chatId) return goal.chatId === activeChatId;
      return Boolean(currentSessionId && goal.sessionId === currentSessionId);
    }) || null;
  }, [activeChat?.sessionId, activeChatId, agentGoals, sessionId]);
  refreshRuntimeRunsRef.current = refreshRuntimeRuns;
  const {
    state: backgroundGoalState,
    onCatchUpRendered: onBackgroundGoalCatchUpRendered,
    onProviderWarningsRendered: onBackgroundGoalProviderWarningsRendered,
    dismissCatchUp: dismissBackgroundGoalCatchUp,
  } = useBackgroundGoalRuns({
    endpoint,
    runtimeConnected,
    chatAvailable,
    ownerChatVisible:
      activeView === "chat"
      && Boolean(activeChatId)
      && (!showOnboarding || onboardingMinimized),
    activeChatId,
    refreshSignal: backgroundGoalRefreshSignal,
    notificationsEnabled: backgroundGoalNotificationsEnabled,
    onGoalDelivery: async (goal, delivery) => {
      const targetChat = chats.find((chat) => chat.id === delivery.chatId);
      if (!targetChat || compacting) {
        return "retry";
      }
      // 唤醒后的续跑走原聊天的可见运行队列，不能误投到当前打开的聊天。
      if (goal) {
        upsertAgentGoal(goal);
      }
      const resumePrompt = (delivery.resumePrompt || "").trim();
      if (delivery.response) {
        const response = delivery.response;
        const completedAt = delivery.completedAt || response.completedAt;
        updateChat(targetChat.id, (chat) => ({
          ...touchChat(chat, completedAt),
          sessionId: response.sessionId || response.session_id || chat.sessionId,
          title: chat.title || resumePrompt,
          items: [
            ...chat.items.filter(
              (item) => item.id !== delivery.userItemId && item.id !== delivery.agentItemId && item.type !== "streaming",
            ),
            {
              id: delivery.userItemId,
              type: "user",
              text: resumePrompt,
              attachments: [],
              ...(delivery.createdAt ? { createdAt: delivery.createdAt } : {}),
            },
            {
              id: delivery.agentItemId,
              type: "agent",
              response,
              elapsedSeconds: 1,
              providerLabel: delivery.providerLabel || providerSnapshot.providerLabel,
              model: delivery.model || providerSnapshot.model,
              ...(completedAt ? { completedAt } : {}),
            },
          ],
        }));
        try {
          await persistChatsNow();
          return "persisted";
        } catch {
          return "retry";
        }
      }
      const turn: QueuedTurn = {
        id: delivery.clientTurnId,
        text: resumePrompt,
        attachments: [],
        providerLabel: providerSnapshot.providerLabel,
        provider: providerSnapshot.provider,
        model: providerSnapshot.model,
        chatId: delivery.chatId,
        sessionId: delivery.sessionId || targetChat.sessionId || undefined,
        projectPath: delivery.projectRoot || targetChat.projectPath || undefined,
        goalDelivery: {
          deliveryId: delivery.deliveryId,
          userItemId: delivery.userItemId,
          agentItemId: delivery.agentItemId,
        },
      };
      const succeeded = await runBackgroundTurn(targetChat.id, turn);
      if (!succeeded) {
        return "retry";
      }
      try {
        await persistChatsNow();
      } catch {
        return "retry";
      }
      setRuntimeNotice(t("goal.woken", { title: goal?.title || delivery.goalId || "" }));
      return "persisted";
    },
  });
  useEffect(() => {
    if (!editingMessage || editingMessage.chatId === activeChatId) {
      return;
    }
    setInput(editingMessage.priorInput);
    setAttachments(cloneChatAttachments(editingMessage.priorAttachments));
    setEditingMessage(null);
    setRuntimeNotice("");
  }, [activeChatId, editingMessage, setRuntimeNotice]);
  const {
    checkpoints,
    interruptedRecoveries,
    adjustmentCheckpoints,
    checkpointPreview,
    recoveryPreview,
    adjustmentPreview,
    loadingCheckpoints,
    restoringCheckpointId,
    recoveryBusyId,
    adjustmentBusyId,
    checkpointMessage,
    recoveryMessage,
    adjustmentMessage,
    openCheckpoints,
    loadCheckpoints,
    previewCheckpoint,
    restoreCheckpoint,
    previewRecovery,
    restoreRecovery,
    exportRecoveryBundle,
    resolveRecovery,
    createAdjustment,
    renameAdjustment,
    previewAdjustment,
    selectAdjustment,
    applyAdjustment,
    overwriteAdjustment,
    removeAdjustment,
  } = useCheckpointWorkspaceController({
    endpoint,
    runtimeConnected,
    activeView,
    activeProjectPath,
    setActiveView,
    startRuntime,
    refresh,
    setError,
  });
  const {
    approvalActions,
    pendingApprovalForResponse,
    modifyApprovalInComposer,
    approveShell,
    rejectShell,
  } = useApprovalExecution({
    endpoint,
    activeRuntimeProjectPath,
    activeChatId,
    activeView,
    pendingApprovalItems: projectPendingApprovalItems,
    setRuntimeNotice,
    setError,
    appendToChat,
    chatIdForSessionId: (ownerSessionId) =>
      chats.find((chat) => chat.sessionId === ownerSessionId)?.id || "",
    refresh,
    refreshRuntimeRuns,
    loadCheckpoints,
    reloadChatStorageState,
  });
  pendingApprovalsRef.current = projectPendingApprovalItems;

  useEffect(() => {
    if (agentApprovals === null) {
      return;
    }
    const pendingIds = new Set(pendingApprovalItems.map((approval) => approval.id));
    for (const approvalId of knownApprovalNotificationIdsRef.current) {
      if (!pendingIds.has(approvalId)) {
        knownApprovalNotificationIdsRef.current.delete(approvalId);
      }
    }
    for (const approvalId of exhaustedApprovalNotificationIdsRef.current) {
      if (!pendingIds.has(approvalId)) {
        exhaustedApprovalNotificationIdsRef.current.delete(approvalId);
      }
    }
    for (const approval of pendingApprovalItems) {
      if (
        knownApprovalNotificationIdsRef.current.has(approval.id) ||
        exhaustedApprovalNotificationIdsRef.current.has(approval.id) ||
        !isTauriRuntime()
      ) {
        continue;
      }
      knownApprovalNotificationIdsRef.current.add(approval.id);
      const presentation = presentApproval(approval, t);
      const notify = () =>
        showApprovalNotification(
          approval,
          t("approval.notificationTitle"),
          t("approval.notificationBody", { summary: presentation.notificationSummary }),
        );
      void notify().catch(() =>
        new Promise<void>((resolve) => window.setTimeout(resolve, 1_500))
          .then(() => {
            if (!pendingApprovalsRef.current.some((item) => item.id === approval.id)) {
              knownApprovalNotificationIdsRef.current.delete(approval.id);
              return;
            }
            return notify();
          })
          .catch((cause) => {
            knownApprovalNotificationIdsRef.current.delete(approval.id);
            exhaustedApprovalNotificationIdsRef.current.add(approval.id);
            setError(cause instanceof Error ? cause.message : String(cause));
          }),
        );
    }
  }, [agentApprovals, pendingApprovalItems, t]);

  useEffect(() => {
    if (!isTauriRuntime() || !runtimeConnected) {
      return;
    }
    // Wait for the first task snapshot; an empty reconnect snapshot must not
    // erase persisted delivery markers and cause a duplicate toast.
    if (!subAgentList) {
      return;
    }
    const taskSnapshot = subAgentList.tasks || [];
    // An empty reconnect response is temporarily unavailable state. Do not
    // interpret it as evidence that persisted notifications were resolved.
    if (!taskSnapshot.length) {
      return;
    }
    const allReviewTasks = taskSnapshot.filter((task) => {
      return (
        isAwaitingMergeReview(task)
        && typeof task.revision === "number"
        && Number.isInteger(task.revision)
        && task.revision > 0
        && Boolean(task.parentChatId)
      );
    });
    const reviewTasks = allReviewTasks.filter((task) => {
      return (
        isAwaitingMergeReview(task)
        && typeof task.revision === "number"
        && Number.isInteger(task.revision)
        && task.revision > 0
        && Boolean(task.parentChatId)
        && getChatById(task.parentChatId || "")
      );
    });
    const pendingReviewNotificationIds = new Set(allReviewTasks.map((task) => toReviewNotificationId(task)));
    const snapshotTaskIds = new Set(taskSnapshot.map((task) => task.id));
    for (const notificationId of knownSubAgentReviewNotificationIdsRef.current) {
      const taskId = notificationId.split(":", 1)[0];
      if (snapshotTaskIds.has(taskId) && !pendingReviewNotificationIds.has(notificationId)) {
        knownSubAgentReviewNotificationIdsRef.current.delete(notificationId);
        inFlightSubAgentReviewNotificationIdsRef.current.delete(notificationId);
        deliveredSubAgentReviewNotificationIdsRef.current.delete(notificationId);
      }
    }
    for (const notificationId of handledSubAgentReviewNotificationIdsRef.current) {
      if (!pendingReviewNotificationIds.has(notificationId)) {
        handledSubAgentReviewNotificationIdsRef.current.delete(notificationId);
      }
    }
    for (const notificationId of exhaustedSubAgentReviewNotificationIdsRef.current) {
      if (!pendingReviewNotificationIds.has(notificationId)) {
        exhaustedSubAgentReviewNotificationIdsRef.current.delete(notificationId);
      }
    }
    saveSubAgentReviewNotificationIds(deliveredSubAgentReviewNotificationIdsRef.current);

    for (const task of reviewTasks) {
      const reviewNotificationId = toReviewNotificationId(task);
      if (
        knownSubAgentReviewNotificationIdsRef.current.has(reviewNotificationId)
        || exhaustedSubAgentReviewNotificationIdsRef.current.has(reviewNotificationId)
      ) {
        continue;
      }
      knownSubAgentReviewNotificationIdsRef.current.add(reviewNotificationId);
      inFlightSubAgentReviewNotificationIdsRef.current.add(reviewNotificationId);
      const notify = () =>
        showSubAgentReviewNotification({
          taskId: task.id,
          revision: Number(task.revision),
          parentChatId: task.parentChatId || "",
          title: `${t("subagent.review")} · ${subAgentRoleLabel(task.role)}`,
          body: `${task.displayName || t("subagent.taskLabel")} · ${t("subagent.awaitingReview")} (${task.revision})`,
          openLabel: t("subagent.inspect"),
        });
      const deliver = () => notify().then(() => {
        inFlightSubAgentReviewNotificationIdsRef.current.delete(reviewNotificationId);
        deliveredSubAgentReviewNotificationIdsRef.current.add(reviewNotificationId);
        saveSubAgentReviewNotificationIds(deliveredSubAgentReviewNotificationIdsRef.current);
      });
      void deliver().catch(() =>
        new Promise<void>((resolve) => window.setTimeout(resolve, 1_500))
          .then(() => {
            const currentTask = subAgentTasksRef.current.find((current) => current.id === task.id);
            if (
              !subAgentReviewRuntimeConnectedRef.current
              || !currentTask
              || currentTask.revision !== task.revision
              || currentTask.parentChatId !== task.parentChatId
              || !isAwaitingMergeReview(currentTask)
              || !getChatById(currentTask.parentChatId || "")
            ) {
              knownSubAgentReviewNotificationIdsRef.current.delete(reviewNotificationId);
              inFlightSubAgentReviewNotificationIdsRef.current.delete(reviewNotificationId);
              return;
            }
            return deliver();
          })
          .catch((cause) => {
            knownSubAgentReviewNotificationIdsRef.current.delete(reviewNotificationId);
            inFlightSubAgentReviewNotificationIdsRef.current.delete(reviewNotificationId);
            exhaustedSubAgentReviewNotificationIdsRef.current.add(reviewNotificationId);
            setError(cause instanceof Error ? cause.message : String(cause));
          }),
      );
    }
  }, [getChatById, runtimeConnected, subAgentList, t, toReviewNotificationId]);

  useLayoutEffect(() => {
    subAgentReviewRuntimeConnectedRef.current = runtimeConnected;
    subAgentReviewNotificationActionHandlerRef.current = (value: unknown) => {
      if (!runtimeConnected || !subAgentReviewRuntimeConnectedRef.current) {
        return;
      }
      const action = parseSubAgentReviewNotificationAction(value);
      if (!action) {
        return;
      }
      const reviewNotificationId = `${action.taskId}:${action.revision}:${action.parentChatId}`;
      if (
        handledSubAgentReviewNotificationIdsRef.current.has(reviewNotificationId)
        || exhaustedSubAgentReviewNotificationIdsRef.current.has(reviewNotificationId)
      ) {
        return;
      }
      handledSubAgentReviewNotificationIdsRef.current.add(reviewNotificationId);
      void (async () => {
        try {
          const payload = await fetchSubAgent(endpoint, action.taskId);
          const task = payload.task;
          if (!subAgentReviewRuntimeConnectedRef.current) {
            handledSubAgentReviewNotificationIdsRef.current.delete(reviewNotificationId);
            return;
          }
          if (
            task.id !== action.taskId
            || task.parentChatId !== action.parentChatId
            || task.revision !== action.revision
            || !isAwaitingMergeReview(task)
            || !getChatById(task.parentChatId)
          ) {
            handledSubAgentReviewNotificationIdsRef.current.delete(reviewNotificationId);
            exhaustedSubAgentReviewNotificationIdsRef.current.add(reviewNotificationId);
            setError(t("approval.notificationExpired"));
            return;
          }
          const chat = getChatById(task.parentChatId);
          if (!chat) {
            handledSubAgentReviewNotificationIdsRef.current.delete(reviewNotificationId);
            exhaustedSubAgentReviewNotificationIdsRef.current.add(reviewNotificationId);
            setError(t("approval.notificationExpired"));
            return;
          }
          openChat(chat);
          setActiveView("chat");
          subAgentSelectionIntentRef.current = task.id;
          setSelectedSubAgent(task);
          setSubAgentList((current) => updateSubAgentList(current, task));
          setSelectedSubAgentPanelOpen(true);
        } catch (cause) {
          handledSubAgentReviewNotificationIdsRef.current.delete(reviewNotificationId);
          setError(cause instanceof Error ? cause.message : String(cause));
        }
      })();
    };
  }, [endpoint, getChatById, openChat, runtimeConnected, t]);

  useEffect(() => {
    if (runtimeConnected) {
      return;
    }
    handledSubAgentReviewNotificationIdsRef.current.clear();
    exhaustedSubAgentReviewNotificationIdsRef.current.clear();
  }, [runtimeConnected]);

  useEffect(() => {
    if (!isTauriRuntime()) {
      return;
    }
    let active = true;
    let unlistenQuestionAction: (() => void) | null = null;
    void listen<unknown>(AGENT_QUESTION_NOTIFICATION_ACTION_EVENT, (event) => {
      const action = parseAgentQuestionNotificationAction(event.payload);
      if (!action || !active) return;
      let chat = chats.find((item) => item.id === action.chatId);
      void (async () => {
        let question = agentQuestions.find((item) => item.questionId === action.questionId);
        if (!question || !chat) {
          const result = await fetchAgentQuestions(endpoint, {
            sessionId: chat?.sessionId,
            projectRoot: chat?.projectPath || undefined,
            includeAnswered: true,
            limit: 1000,
          });
          question = result.questions.find((item) => item.questionId === action.questionId);
          if (!chat && question?.sessionId) {
            chat = chats.find((item) => item.sessionId === question?.sessionId);
          }
        }
        if (!active || !question) return;
        upsertAgentQuestion(question, true);
        const questionSessionId = String(question.sessionId || "").trim();
        const chatSessionId = String(chat?.sessionId || "").trim();
        if (!questionSessionId || !chat || !chatSessionId || questionSessionId !== chatSessionId) {
          if (!chat) setActiveView("chat");
          return;
        }
        setActiveChatId(chat.id);
        setActiveView("chat");
      })().catch((cause) => active && setError(cause instanceof Error ? cause.message : String(cause)));
    })
      .then((unlisten) => {
        if (active) {
          unlistenQuestionAction = unlisten;
        } else {
          unlisten();
        }
      })
      .catch((cause) => active && setError(cause instanceof Error ? cause.message : String(cause)));
    return () => {
      active = false;
      unlistenQuestionAction?.();
    };
  }, [agentQuestions, chats, endpoint, setActiveChatId, setActiveView, setError]);

  useEffect(() => {
    if (!isTauriRuntime()) {
      return;
    }
    let active = true;
    let unlistenSubAgentAction: (() => void) | null = null;
    void listen<unknown>(SUB_AGENT_REVIEW_NOTIFICATION_ACTION_EVENT, (event) =>
      subAgentReviewNotificationActionHandlerRef.current(event.payload)
    )
      .then((unlisten) => {
        if (active) {
          unlistenSubAgentAction = unlisten;
        } else {
          unlisten();
        }
      })
      .catch((cause) => active && setError(cause instanceof Error ? cause.message : String(cause)));

    return () => {
      active = false;
      subAgentReviewRuntimeConnectedRef.current = false;
      unlistenSubAgentAction?.();
    };
  }, []);

  useEffect(() => {
    if (!isTauriRuntime()) {
      return;
    }
    let active = true;
    let unlistenAction: (() => void) | null = null;
    void listen<unknown>("vrcforge-approval-notification-action", (event) => {
      const action = parseApprovalNotificationAction(event.payload);
      if (!action || !active) {
        return;
      }
      void (async () => {
        // A native toast is only a wake/deep-link hint. Re-read the backend
        // authority before exposing the card so a stale local event can never
        // resurrect a decision that completed in another window/process.
        const latest = await fetchAgentApprovals(endpoint);
        if (!active) {
          return;
        }
        setAgentApprovals(latest.approvals);
        const approval = latest.approvals.find(
          (item) => item.id === action.approvalId && String(item.status || "").trim().toLowerCase() === "pending",
        );
        if (!approval) {
          setError(t("approval.notificationExpired"));
          return;
        }
        const approvalSessionId = String(approval.taskContext?.sessionId || "").trim();
        const approvalTurnId = String(approval.taskContext?.turnId || "").trim();
        const approvalClientTurnId = String(approval.taskContext?.clientTurnId || "").trim();
        const ownerChat = chats.find((chat) => {
          if (approvalSessionId && chat.sessionId !== approvalSessionId) {
            return false;
          }
          return chat.items.some((item) => {
            if (item.type !== "agent") {
              return false;
            }
            const response = item.response;
            const responseApprovalId = String(
              response.approvalId
              || response.approval_id
              || response.shell?.approvalId
              || response.shell?.approval_id
              || response.shell?.approval?.id
              || response.write?.approvalId
              || response.write?.approval_id
              || "",
            );
            return responseApprovalId === approval.id
              || (approvalTurnId && (response.turnId === approvalTurnId || response.turn_id === approvalTurnId))
              || (approvalClientTurnId && response.clientTurnId === approvalClientTurnId);
          });
        });
        if (ownerChat) {
          openChat(ownerChat);
        }
        setActiveView("chat");
        setSelectedSubAgentPanelOpen(false);
        window.setTimeout(() => {
          document.querySelector<HTMLElement>(`[data-approval-id="${approval.id.replace(/["\\]/g, "\\$&")}"]`)
            ?.scrollIntoView({ block: "center", behavior: "smooth" });
        }, 0);
      })().catch((cause) => {
        if (active) {
          setError(cause instanceof Error ? cause.message : t("approval.notificationFailed"));
        }
      });
    })
      .then((unlisten) => {
        if (active) {
          unlistenAction = unlisten;
        } else {
          unlisten();
        }
      })
      .catch((cause) => active && setError(cause instanceof Error ? cause.message : String(cause)));
    return () => {
      active = false;
      unlistenAction?.();
    };
  }, [chats, endpoint, openChat, t]);
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
  const contextUsage = useMemo(() => {
    if (!apiConfig && !smokeMode) {
      return undefined;
    }
    const nextUsage = buildContextUsageFromRuntime(
      latestContextUsage,
      providerSnapshot.provider,
      providerSnapshot.model,
      currentModelInfo,
      t,
      apiConfig?.contextWindow,
    );
    if (nextUsage?.source === "provider_usage") {
      return nextUsage;
    }
    const cachedUsage = buildContextUsageFromRuntime(
      conversation.some((item) => item.type === "compact")
        ? latestContextWindowUsage(conversation, true) : activeChat?.contextUsageCache,
      providerSnapshot.provider,
      providerSnapshot.model,
      currentModelInfo,
      t,
      apiConfig?.contextWindow,
    );
    if (cachedUsage?.source === "provider_usage") {
      return { ...cachedUsage, cached: true };
    }
    return nextUsage;
  }, [activeChat?.contextUsageCache, conversation, apiConfig, currentModelInfo, latestContextUsage, providerSnapshot.model, providerSnapshot.provider, smokeMode, t]);
  const compactDebugEntries = useMemo(
    () =>
      (activeChat?.items || [])
        .filter((item): item is Extract<ConversationItem, { type: "compact" }> => item.type === "compact" && Boolean(item.detail))
        .map((item) => ({
          id: item.id,
          text: item.detail || "",
          entryCount: item.entryCount,
          createdAt: item.createdAt,
        })),
    [activeChat?.items],
  );
  const userAttachmentSources = useMemo<UserAttachmentSource[]>(() => {
    const seen = new Set<string>();
    const sources: UserAttachmentSource[] = [];
    const register = (source: UserAttachmentSource | undefined | null) => {
      if (!source || !source.id || !source.name) {
        return;
      }
      const normalizedSize = Number.isSafeInteger(source.size) && source.size >= 0 ? source.size : 0;
      const dedupeKey = `${source.id}\u0000${source.name}\u0000${source.type}\u0000${normalizedSize}`;
      if (seen.has(dedupeKey)) {
        return;
      }
      seen.add(dedupeKey);
      sources.push({ ...source, size: normalizedSize, type: source.type || "file" });
    };
    for (const item of activeChat?.items || []) {
      if (item.type !== "user" || !item.attachments?.length) {
        continue;
      }
      for (const attachment of item.attachments) {
        register({
          id: attachment.id,
          name: attachment.name,
          type: attachment.type || "file",
          size: attachment.size,
          messageId: item.id,
          attachment,
        });
      }
    }
    for (const attachment of activeChat?.compactedAttachmentRefs || []) {
      register({
        id: attachment.id,
        name: attachment.name,
        type: attachment.type || "file",
        size: attachment.size,
        attachment: {
          id: attachment.id,
          name: attachment.name,
          type: attachment.type || "file",
          size: attachment.size,
          payloadKind: attachment.payloadKind,
          payloadHash: attachment.payloadHash,
          vaultPayloadHash: attachment.vaultPayloadHash,
          vaultKind: attachment.vaultKind,
          truncated: attachment.truncated,
        },
      });
    }
    return sources;
  }, [activeChat?.id, activeChat?.items, activeChat?.compactedAttachmentRefs]);

  function locateUserAttachmentSource(source: UserAttachmentSource) {
    setActiveView("chat");
    setSelectedSubAgentPanelOpen(false);
    if (!source.messageId) {
      return;
    }
    window.setTimeout(() => {
      document.querySelector<HTMLElement>(`[data-conversation-item-id="${source.messageId?.replace(/["\\]/g, "\\$&")}"]`)
        ?.scrollIntoView({ block: "center", behavior: "smooth" });
    }, 0);
  }

  function openUserAttachmentSource(source: UserAttachmentSource) {
    locateUserAttachmentSource(source);
    if (!source.messageId || !source.attachment?.dataUrl || !source.attachment.type.startsWith("image/")) {
      return;
    }
    window.setTimeout(() => {
      window.dispatchEvent(new CustomEvent("vrcforge-open-chat-attachment", {
        detail: { messageId: source.messageId, attachmentId: source.attachment?.id },
      }));
    }, 0);
  }
  const subAgentTasks = subAgentList?.tasks ?? [];
  subAgentTasksRef.current = subAgentTasks;
  const activeSubAgentTasks = useMemo(() => {
    const parentChatId = activeChat?.id || "";
    return parentChatId ? subAgentTasks.filter((task) => task.parentChatId === parentChatId) : [];
  }, [activeChat?.id, subAgentTasks]);
  const activeSubAgentThreads = useMemo(() => groupSubAgentThreads(activeSubAgentTasks), [activeSubAgentTasks]);
  const runningSubAgentTaskCount = useMemo(
    () => activeSubAgentThreads.filter(({ task }) => ["queued", "running", "cancelling"].includes(task.status)).length,
    [activeSubAgentThreads],
  );
  const completedSubAgentTaskCount = useMemo(
    () => activeSubAgentThreads.filter(({ task }) => !["queued", "running", "cancelling"].includes(task.status)).length,
    [activeSubAgentThreads],
  );
  useEffect(() => {
    subAgentSelectionIntentRef.current = selectedSubAgent?.id || "";
  }, [selectedSubAgent?.id]);
  const hasRunningSubAgents = subAgentTasks.some((task) => ["queued", "running", "cancelling"].includes(task.status));
  const activeProjectName =
    projectDisplayLabel(projectItems.find((project) => normalizeProjectPathKey(projectKey(project)) === normalizeProjectPathKey(activeProjectPath))) ||
    (activeProjectPath ? shortPath(activeProjectPath) : "");
  const effectiveLeftPaneWidth = layoutPaneWidths.left;
  const subAgentSidebarActive = subAgentSidebarOpen || selectedSubAgentPanelOpen;
  const centerPaneMinimum = subAgentSidebarActive ? 360 : MIN_CENTER_PANE_WIDTH;
  const rightPaneMaximum = subAgentSidebarActive
    ? Math.max(MIN_RIGHT_PANE_WIDTH, window.innerWidth - effectiveLeftPaneWidth - RESIZE_HANDLE_WIDTH * 2 - centerPaneMinimum)
    : MAX_RIGHT_PANE_WIDTH;
  const effectiveRightPaneWidth = rightSidebarCollapsed ? 44 : subAgentSidebarActive
    ? clampNumber(subAgentPaneWidth, MIN_RIGHT_PANE_WIDTH, rightPaneMaximum) : layoutPaneWidths.right;
  useEffect(() => {
    if (subAgentSidebarActive) setRightSidebarCollapsed(false);
  }, [subAgentSidebarOpen, selectedSubAgentPanelOpen, selectedSubAgent?.id]);
  useEffect(() => {
    if (!subAgentSidebarActive) return;
    const fitPane = () => setSubAgentPaneWidth((width) => Math.min(width,
      Math.max(MIN_RIGHT_PANE_WIDTH, window.innerWidth - effectiveLeftPaneWidth - RESIZE_HANDLE_WIDTH * 2 - 360)));
    window.addEventListener("resize", fitPane);
    return () => window.removeEventListener("resize", fitPane);
  }, [subAgentSidebarActive, effectiveLeftPaneWidth]);
  const rightSidebarVisible = sidebarsVisible && activeView === "chat";
  const workspaceGridColumnsWithRightSidebar = `${effectiveLeftPaneWidth}px ${RESIZE_HANDLE_WIDTH}px minmax(0,1fr) ${RESIZE_HANDLE_WIDTH}px ${effectiveRightPaneWidth}px`;
  const workspaceGridColumnsWithoutRightSidebar = `${effectiveLeftPaneWidth}px ${RESIZE_HANDLE_WIDTH}px minmax(0,1fr)`;
  const workspaceGridColumns = rightSidebarVisible ? workspaceGridColumnsWithRightSidebar : workspaceGridColumnsWithoutRightSidebar;
  const startLayoutResize = (side: "left" | "right", event: ReactPointerEvent<HTMLDivElement>) => {
    event.preventDefault();
    const startX = event.clientX;
    const startLeftWidth = effectiveLeftPaneWidth;
    const startRightWidth = effectiveRightPaneWidth;
    const rightCollapseThreshold = MIN_RIGHT_PANE_WIDTH * 0.65;
    const previousCursor = document.body.style.cursor;
    const previousUserSelect = document.body.style.userSelect;
    document.body.style.cursor = "col-resize";
    document.body.style.userSelect = "none";

    const maxLeftWidth = () => {
      const available = window.innerWidth - RESIZE_HANDLE_WIDTH * 2 - centerPaneMinimum - effectiveRightPaneWidth;
      return Math.max(MIN_LEFT_PANE_WIDTH, Math.min(MAX_LEFT_PANE_WIDTH, available));
    };
    const maxRightWidth = () => {
      const available = window.innerWidth - RESIZE_HANDLE_WIDTH * 2 - centerPaneMinimum - layoutPaneWidths.left;
      return Math.max(MIN_RIGHT_PANE_WIDTH, Math.min(rightPaneMaximum, available));
    };

    const onPointerMove = (moveEvent: PointerEvent) => {
      const delta = moveEvent.clientX - startX;
      if (side === "left") {
        const proposed = startLeftWidth + delta;
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
      if (subAgentSidebarActive) {
        setSubAgentPaneWidth(clampNumber(proposed, MIN_RIGHT_PANE_WIDTH, maxRightWidth()));
      } else {
        setLayoutPaneWidths((current) => ({ ...current, right: clampNumber(proposed, MIN_RIGHT_PANE_WIDTH, maxRightWidth()) }));
      }
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
  const workspaceProjectPath = activeChat?.projectPath || activeProjectPath;
  const workspaceProjectType: ProjectType = activeChat?.projectType || activeProjectType;
  const selectedProjectComponent = workspaceProjectType === "general"
    ? { status: workspaceProjectPath ? "ok" : "unknown" }
    : healthComponents.selectedUnityProject;
  const backendComponent = healthComponents.backend;
  const mcpPackageComponent = healthComponents.mcpPackageConfigured;
  const unityBridgeComponent = healthComponents.unityMcpBridgeReachable;
  const unityInstanceComponent = healthComponents.unityMcpInstance;
  const unityToolsComponent = healthComponents.vrcForgeUnityTools;
  const externalAgentComponent = healthComponents.externalAgentConnection;
  const authorizationStatus = resolveRuntimeAuthorizationStatus({
    runtimeConnected,
    externalAgentComponent,
    apiConfig,
    providerTestPassed: providerTestPassedForPersistedConfig,
  });
  const localizeHealthMessage = useCallback(
    (message?: string) => localizeRuntimeHealthMessage(t, message),
    [t],
  );
  const authoritativeProjectName = projectDisplayLabel(
    projectItems.find(
      (project) => normalizeProjectPathKey(projectKey(project)) === normalizeProjectPathKey(authoritativeSelectedProjectPath),
    ),
  );
  const workspaceProjectLabel = workspaceProjectPath
    ? projectDisplayLabel(projectItems.find(
        (project) => normalizeProjectPathKey(projectKey(project)) === normalizeProjectPathKey(workspaceProjectPath),
      )) || shortPath(workspaceProjectPath)
    : t("workspace.noProjectSelected");
  const persistQuestionAnswer = (chatId: string, answerItem: Extract<ConversationItem, { type: "question_answer" }>) => {
    updateChat(chatId, (chat) => {
      const index = chat.items.findIndex((item) => item.type === "question_answer" && item.id === answerItem.id);
      if (index < 0) return { ...chat, items: [...chat.items, answerItem] };
      const items = [...chat.items];
      items[index] = answerItem;
      return { ...chat, items };
    });
    void persistChatsNow().catch((cause) => {
      setError(chatSaveErrorMessage(cause, t("chat.sessionSaveBlocked"), t("chat.sessionSaveConflict")));
    });
  };
  const answerRuntimeQuestion = async (questionId: string, optionId: string, value: string) => {
    setActiveView("chat");
    const answeredQuestion = agentQuestions.find((item) => item.questionId === questionId);
    try {
      const payload = await answerAgentQuestion(endpoint, questionId, {
        answer: value,
        selectedOptionId: optionId,
        sessionId: answeredQuestion?.sessionId || sessionId,
        projectRoot: answeredQuestion?.projectRoot || activeRuntimeProjectPath || undefined,
      });
      upsertAgentQuestion({
        ...payload.question,
        runtimeContinuation: payload.runtimeContinuation
          ? { ...payload.runtimeContinuation, sessionId: payload.runtimeContinuation.sessionId || sessionId }
          : agentQuestions.find((item) => item.questionId === questionId)?.runtimeContinuation,
      });
      const answerChat = chats.find((chat) => chat.sessionId === answeredQuestion?.sessionId);
      const answerChatId = answerChat?.id;
      const answerItem = questionAnswerItem(payload.question, payload.question.answer || value);
      if (answerChat && answerChatId) {
        persistQuestionAnswer(answerChatId, answerItem);
      }
      void refreshRuntimeRuns(false);
    } catch (cause) {
      setError(cause instanceof Error ? cause.message : String(cause));
      throw cause;
    }
  };
  const replyToAgentQuestionInComposer = (questionId: string) => {
    if (!agentQuestions.some((question) => question.questionId === questionId && question.status === "pending")) return;
    setPendingReplyQuestionId(questionId);
    setActiveView("chat");
  };
  const stopRuntimeQuestionContinuation = async (question: AgentQuestion) => {
    const continuation = question.runtimeContinuation;
    if (!continuation || !["queued", "running"].includes(String(continuation.status || "").toLowerCase())) return;
    const target = questionContinuationCancelTarget(question);
    if (!target) return;
    await requestAgentRunCancel(endpoint, {
      ...target,
      reason: "user_stop_question_continuation",
    });
    updateAgentQuestionContinuation(question.questionId, { status: "cancelling" });
    void refreshRuntimeRuns(false);
  };
  const projectPromptTitle = activeProjectPath && activeProjectName ? t("chat.promptTitle", { name: activeProjectName }) : t("chat.promptTitleDefault");
  const emptyProjectState = useMemo(
    () =>
      buildEmptyProjectState({
        t,
        projectCount: projectItems.length,
        loading,
        error,
        hasStartupIssue,
        runtimeConnected,
      }),
    [error, hasStartupIssue, loading, projectItems.length, runtimeConnected, t],
  );
  const markLeftSidebarMounted = useCallback(() => setLeftSidebarMounted(true), []);
  const markRightSidebarMounted = useCallback(() => setRightSidebarMounted(true), []);

  useLayoutEffect(() => {
    const metrics = ((window as any).__vrcforgeStartupMetrics ||= {});
    metrics.shellCommittedMs ??= Math.round(performance.now());
    document.documentElement.dataset.vrcforgeShell = "ready";
    let paintedFrame = 0;
    const committedFrame = window.requestAnimationFrame(() => {
      paintedFrame = window.requestAnimationFrame(() => {
        metrics.shellPaintedMs ??= Math.round(performance.now());
        if (document.querySelector("[data-chat-composer-dock], [data-empty-chat-content]")) {
          metrics.centerUsableMs ??= Math.round(performance.now());
          document.documentElement.dataset.vrcforgeCenter = "ready";
        }
        metrics.sidebarsRequestedMs ??= Math.round(performance.now());
        setSidebarsVisible(true);
      });
    });
    return () => {
      window.cancelAnimationFrame(committedFrame);
      if (paintedFrame) {
        window.cancelAnimationFrame(paintedFrame);
      }
    };
  }, []);

  useEffect(() => {
    if (!sidebarsVisible || !leftSidebarMounted || (rightSidebarVisible && !rightSidebarCollapsed && !rightSidebarMounted)) {
      return;
    }
    const metrics = ((window as any).__vrcforgeStartupMetrics ||= {});
    metrics.sidebarsMountedMs ??= Math.round(performance.now());
    document.documentElement.dataset.vrcforgeSidebars = "mounted";
  }, [leftSidebarMounted, rightSidebarCollapsed, rightSidebarMounted, rightSidebarVisible, sidebarsVisible]);

  useEffect(() => {
    if (
      !bootstrap
      || !projectPrefsReady
      || !sidebarsVisible
      || !leftSidebarMounted
      || (rightSidebarVisible && !rightSidebarCollapsed && !rightSidebarMounted)
    ) {
      return;
    }
    const metrics = ((window as any).__vrcforgeStartupMetrics ||= {});
    metrics.sidebarsHydratedMs ??= Math.round(performance.now());
    document.documentElement.dataset.vrcforgeSidebars = "ready";
  }, [bootstrap, leftSidebarMounted, projectPrefsReady, rightSidebarCollapsed, rightSidebarMounted, rightSidebarVisible, sidebarsVisible]);

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

  useEffect(() => {
    setSelectionMenu(null);
    window.getSelection()?.removeAllRanges();
  }, [activeView, activeChat?.id, activeProjectPath, selectedSubAgentPanelOpen, showProjectModal]);

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
      window.localStorage.setItem(DEVELOPER_OPTIONS_ENABLED_KEY, String(developerOptionsEnabled));
    } catch {
      // The backend is authoritative; this only avoids a startup flash before bootstrap.
    }
  }, [developerOptionsEnabled]);

  useEffect(() => {
    const settings = bootstrap?.advancedSettings;
    if (!settings) {
      return;
    }
    setDeveloperOptionsEnabled(settings.developerOptionsEnabled);
    setDeveloperOptionsEverEnabled(settings.developerOptionsEverEnabled);
    setComputerUseEnabled(settings.computerUseEnabled);
    setComputerUseEverEnabled(settings.computerUseEverEnabled);
    setBackgroundGoalNotificationsEnabled(settings.backgroundGoalNotificationsEnabled !== false);
  }, [bootstrap?.advancedSettings]);

  useEffect(() => {
    setAgentApprovals(
      runtimeConnected && bootstrap && bootstrap.approvalsState?.ok !== false
        ? bootstrap.approvals ?? []
        : null,
    );
  }, [bootstrap?.approvals, bootstrap?.approvalsState?.ok, runtimeConnected]);

  useEffect(() => {
    if (initialOnboardingState.migrateLanguageGateCompletion) {
      persistOnboardingLanguageGateCompletion();
    }
  }, [initialOnboardingState.migrateLanguageGateCompletion]);

  useEffect(() => {
    if (showOnboarding && onboardingMinimized && providerSetupGuide && providerReadyForOnboarding) {
      setActiveView("chat");
      setProviderSetupGuide(false);
      setOnboardingMinimized(false);
    }
  }, [showOnboarding, onboardingMinimized, providerSetupGuide, providerReadyForOnboarding]);

  useEffect(() => {
    if (showOnboarding && onboardingMinimized && externalSetupGuide && externalAgentVerified) {
      setActiveView("chat");
      setExternalSetupGuide(false);
      setOnboardingMinimized(false);
    }
  }, [showOnboarding, onboardingMinimized, externalSetupGuide, externalAgentVerified]);

  useEffect(() => {
    if (!showOnboarding || !onboardingMinimized || providerSetupGuide || activeView === "settings" || showProjectModal) {
      return;
    }
    const stepStates = activeProjectType === "unity"
      ? [providerReadyForOnboarding || externalAgentVerified, onboardingSelectedProjectReady, onboardingUnityToolsReady]
      : [providerReadyForOnboarding || externalAgentVerified, onboardingSelectedProjectReady];
    if (stepStates[Math.min(onboardingStep, stepStates.length - 1)]) {
      setOnboardingMinimized(false);
    }
  }, [showOnboarding, onboardingMinimized, onboardingStep, onboardingSelectedProjectReady, onboardingUnityToolsReady, activeProjectType, providerReadyForOnboarding, externalAgentVerified, providerSetupGuide, activeView, showProjectModal]);

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
    if (projectInitRef.current || activeProjectPath || !authoritativeSelectedProjectPath) {
      return;
    }
    projectInitRef.current = true;
    setActiveProjectPath(authoritativeSelectedProjectPath);
    setActiveProjectType("unity");
  }, [activeProjectPath, authoritativeSelectedProjectPath]);

  useEffect(() => {
    const intervalMs = isTauriRuntime() ? 30000 : 5000;
    const timer = window.setInterval(() => {
      void refreshSilently();
    }, intervalMs);
    return () => window.clearInterval(timer);
  }, [endpoint]);

  useEffect(() => {
    // Refresh the authoritative config lease at request/terminal boundaries,
    // including a foreground reply that becomes an approval/question wait.
    if (runtimeConnected) void refreshSilently();
  }, [chatRunSending, runtimeConnected, endpoint]);

  useEffect(() => {
    if (!runtimeConnected || !isTauriRuntime() || initialGlobalQuestionFetchRef.current) {
      return;
    }
    initialGlobalQuestionFetchRef.current = true;
    void fetchAgentQuestions(endpoint, { limit: 1000 }).then((result) => {
      notifyCallUserQuestions(result.questions);
      result.questions.forEach((question) => upsertAgentQuestion(question, true));
    }).catch(() => {
      // The regular runtime snapshot/event path remains authoritative after startup.
    });
  }, [endpoint, notifyCallUserQuestions, runtimeConnected, upsertAgentQuestion]);

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
      "agentGoalBackground",
      "agentGoals",
      "agentMemory",
      "agentMemoryReview",
      "agentProgress",
      "agentQuestions",
      "agentPermission",
      "agentRuntimeCancel",
      "agentRuntimeQueue",
      "agentRuntimeRuns",
      "agentRuntimeTurn",
    ]);
    const bootstrapEvents = new Set(["advancedSettings", "agentApprovals", "agentPermission", "agentQuestions", "agentRuntimeCancel", "agentRuntimeTurn", "config", "hello", "projects", "unity_status"]);
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
        const delta = event.payload as AgentRuntimeDeltaEvent;
        applyRuntimeDelta(delta);
        applyQuestionRuntimeDelta(delta);
        applyExternalRuntimeDelta(delta);
        return;
      }
      if (eventType === "agentRuntimeTurn") {
        deliverRuntimeTurnContinuation(event.payload?.payload);
      }
      if (eventType === "chatStorageChanged") {
        notifyChatStorageChanged(event.payload?.originId);
      }
      if (eventType === "agentQuestions") {
        const payload = event.payload as { question?: AgentQuestion; questions?: AgentQuestion[] } | undefined;
        const questions = Array.isArray(payload?.questions) ? payload.questions : payload?.question ? [payload.question] : [];
        notifyCallUserQuestions(questions);
        questions.forEach((question) => upsertAgentQuestion(question, true));
      }
      if (bootstrapEvents.has(eventType)) {
        scheduleBootstrapRefresh();
      }
      if (runtimeEvents.has(eventType)) {
        scheduleRuntimeRefresh();
      }
      if (eventType === "agentGoalBackground") {
        setBackgroundGoalRefreshSignal((current) => current + 1);
      }
      if (eventType === "agentMemoryReview") {
        setMemoryReviewRefreshSignal((current) => current + 1);
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
  }, [runtimeConnected, endpoint, sessionId, activeRuntimeProjectPath, activeProjectPath, workspaceDiffReviewOpen, deliverRuntimeTurnContinuation, applyQuestionRuntimeDelta, applyExternalRuntimeDelta]);

  useEffect(() => {
    if (!runtimeConnected || bootstrap?.health.deferredDiagnostics !== true) {
      return;
    }
    const timer = window.setTimeout(() => {
      void refreshFullHealth(endpoint);
    }, STARTUP_BACKGROUND_REFRESH_DELAY_MS);
    return () => window.clearTimeout(timer);
  }, [runtimeConnected, endpoint, activeProjectPath, bootstrap?.health.deferredDiagnostics]);

  useEffect(() => {
    return () => {
      if (projectRefreshTimerRef.current !== null) {
        window.clearTimeout(projectRefreshTimerRef.current);
        projectRefreshTimerRef.current = null;
      }
    };
  }, []);

  useEffect(() => {
    if (!isTauriRuntime()) {
      return;
    }
    let active = true;
    let unlistenTrayOpenChat: (() => void) | undefined;
    let unlistenTrayCheckUpdate: (() => void) | undefined;
    let unlistenTrayQuit: (() => void) | undefined;
    void listen("vrcforge-tray-open-chat", () => {
      setActiveView("chat");
      setError("");
      if (!activeChatId) {
        openTemporaryChat();
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
    void listen("vrcforge-tray-check-update", () => {
      void (async () => {
        try {
          const result = await checkForAppUpdateNow();
          if (result.shouldNotify) {
            setAppUpdatePrompt({ source: "tray", result });
          }
        } catch {
          // Manual checks share the same quiet background policy as startup.
        }
      })();
    })
      .then((unlisten) => {
        if (active) {
          unlistenTrayCheckUpdate = unlisten;
        } else {
          unlisten();
        }
      })
      .catch(() => undefined);
    void listen("vrcforge-app-quit-requested", () => {
      if (quitRequestedRef.current) return;
      quitRequestedRef.current = true;
      void flushChatsBeforeQuit(
        persistChatsNowRef.current,
        () => invoke("confirm_app_quit").then(() => undefined),
      ).then((result) => {
        if (result === "persistence_failed") {
          quitRequestedRef.current = false;
          setError(t("chat.sessionSaveFailed"));
        }
      }).catch(() => {
        quitRequestedRef.current = false;
        setError(t("chat.sessionSaveFailed"));
      });
    })
      .then((unlisten) => {
        if (active) {
          unlistenTrayQuit = unlisten;
        } else {
          unlisten();
        }
      })
      .catch(() => undefined);
    return () => {
      active = false;
      unlistenTrayOpenChat?.();
      unlistenTrayCheckUpdate?.();
      unlistenTrayQuit?.();
    };
  }, [activeChatId, chats, checkForAppUpdateNow]);

  useEffect(() => {
    if (!runtimeConnected) {
      setSubAgentList(null);
      return;
    }
    void loadSubAgents(false);
  }, [runtimeConnected, endpoint]);

  useEffect(() => {
    if (smokeMode || !selectedSubAgent) {
      return;
    }
    if (!activeChat?.id || selectedSubAgent.parentChatId !== activeChat.id) {
      setSelectedSubAgent(null);
      setSelectedSubAgentPanelOpen(false);
    }
  }, [activeChat?.id, selectedSubAgent?.id, selectedSubAgent?.parentChatId, smokeMode]);

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
    if (!runtimeConnected || !subAgentList?.tasks.length || chats.length === 0) {
      return;
    }
    void reconcileSubAgentHandoffs(subAgentList.tasks);
  }, [runtimeConnected, endpoint, subAgentList, chats.length]);

  useEffect(() => {
    if (activeView === "doctor" && runtimeConnected) {
      void loadDoctor();
    }
  }, [activeView, runtimeConnected, endpoint, activeProjectPath]);

  function refreshStartupInBackground(target: string, options: { refreshProjects?: boolean } = {}) {
    void refreshStartupWithMetrics(target, options).catch((cause) => {
      const message = cause instanceof Error ? cause.message : String(cause);
      setError(message);
      setStartupIssue(message);
    });
  }

  function scheduleProjectRefresh(target: string) {
    if (projectRefreshTimerRef.current !== null) {
      window.clearTimeout(projectRefreshTimerRef.current);
    }
    projectRefreshTimerRef.current = window.setTimeout(() => {
      projectRefreshTimerRef.current = null;
      void refreshProjectList(target, { allowDuringStartup: true });
    }, 0);
  }

  async function refreshStartupWithMetrics(target: string, options: { refreshProjects?: boolean } = {}) {
    if (options.refreshProjects) {
      // Project discovery is an independent background request. Launch it now
      // so it can overlap bootstrap hydration and the automatic update check.
      scheduleProjectRefresh(target);
    }
    const startedAt = performance.now();
    try {
      await refreshWithRetry(target);
    } finally {
      const metrics = ((window as any).__vrcforgeStartupMetrics ||= {});
      metrics.bootstrapRefreshMs ??= Math.round(performance.now() - startedAt);
    }
  }

  function resolveBackendReady(target: string, status?: string) {
    backendReadyStatusRef.current = "ready";
    backendReadyEndpointRef.current = target;
    setBackgroundRuntimeReady(true);
    const startedAt = startupLaunchStartedAtRef.current;
    const metrics = ((window as any).__vrcforgeStartupMetrics ||= {});
    metrics.backendReadyEventMs = startedAt === null ? null : Math.round(performance.now() - startedAt);
    metrics.backendReadyMode = status || "ready";
    const waiters = backendReadyWaitersRef.current.splice(0);
    waiters.forEach((waiter) => waiter.resolve(target));
  }

  function rejectBackendReady(message: string) {
    backendReadyStatusRef.current = "error";
    setBackgroundRuntimeReady(false);
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
    bootstrapRequestSequenceRef.current += 1;
    bootstrapForegroundRequestRef.current = 0;
    bootstrapSilentPendingTargetRef.current = null;
    bootstrapSilentActiveTargetRef.current = null;
    setBackgroundRuntimeReady(false);
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
          await refreshStartupWithMetrics(readyEndpoint, { refreshProjects: true });
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
            await refreshStartupWithMetrics(targetEndpoint, { refreshProjects: true });
          }
        } else {
          resolveBackendReady(targetEndpoint, result.mode);
          await refreshStartupWithMetrics(targetEndpoint, { refreshProjects: true });
        }
      } else {
        setBackendMessage("dev");
        try {
          const session = await fetchAppSession(targetEndpoint);
          setAppSessionToken(session.appSessionToken || session.app_session_token || "");
        } catch {
          setAppSessionToken("");
        }
        await refreshStartupWithMetrics(targetEndpoint, { refreshProjects: true });
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
    const sequence = ++bootstrapRequestSequenceRef.current;
    bootstrapForegroundRequestRef.current = sequence;
    bootstrapSilentPendingTargetRef.current = null;
    try {
      const payload = await fetchBootstrap(target, { ...options, deferAgentCatalog: true });
      if (sequence !== bootstrapRequestSequenceRef.current) {
        return;
      }
      setBootstrap(payload);
      if (payload.health.projects) {
        setCachedProjectSnapshot(payload.health.projects);
        cacheProjectSnapshot(payload.health.projects);
      }
      setStartupIssue("");
    } catch (cause) {
      if (sequence !== bootstrapRequestSequenceRef.current) {
        return;
      }
      throw cause;
    } finally {
      if (bootstrapForegroundRequestRef.current === sequence) {
        bootstrapForegroundRequestRef.current = 0;
        const pendingTarget = bootstrapSilentPendingTargetRef.current;
        bootstrapSilentPendingTargetRef.current = null;
        if (pendingTarget !== null) {
          void refreshSilently(pendingTarget);
        }
      }
    }
  }

  async function refreshSilently(target = endpoint) {
    if (bootstrapForegroundRequestRef.current !== 0) {
      bootstrapSilentPendingTargetRef.current = target;
      return bootstrapSilentRefreshPromiseRef.current ?? undefined;
    }
    const existing = bootstrapSilentRefreshPromiseRef.current;
    if (existing) {
      bootstrapSilentPendingTargetRef.current = target;
      // Repeated same-endpoint polls can arrive faster than bootstrap fetches.
      // Preserve the active response so a steady polling stream still makes
      // visible progress; only an endpoint switch makes it stale.
      if (target !== bootstrapSilentActiveTargetRef.current) {
        bootstrapRequestSequenceRef.current += 1;
      }
      return existing;
    }

    const runRefreshes = async () => {
      let nextTarget: string | null = target;
      while (nextTarget !== null) {
        if (bootstrapForegroundRequestRef.current !== 0) {
          bootstrapSilentPendingTargetRef.current = nextTarget;
          return;
        }
        const activeTarget = nextTarget;
        nextTarget = null;
        bootstrapSilentActiveTargetRef.current = activeTarget;
        const sequence = ++bootstrapRequestSequenceRef.current;
        try {
          const payload = await fetchBootstrap(activeTarget, { deferAgentCatalog: true });
          if (sequence === bootstrapRequestSequenceRef.current) {
            setBootstrap(payload);
            if (payload.health.projects) {
              setCachedProjectSnapshot(payload.health.projects);
              cacheProjectSnapshot(payload.health.projects);
            }
            setStartupIssue("");
            setError((current) => (current.toLowerCase().includes("fetch") ? "" : current));
          }
        } catch (cause) {
          if (sequence === bootstrapRequestSequenceRef.current) {
            const message = cause instanceof Error ? cause.message : String(cause);
            if (isRuntimeSessionVerificationError(message)) {
              handleRuntimeSessionFailure(message);
            }
            // Keep the current UI usable; explicit retry remains available.
          }
        }
        bootstrapSilentActiveTargetRef.current = null;

        const pendingTarget = bootstrapSilentPendingTargetRef.current;
        bootstrapSilentPendingTargetRef.current = null;
        if (bootstrapForegroundRequestRef.current !== 0) {
          if (pendingTarget !== null) {
            bootstrapSilentPendingTargetRef.current = pendingTarget;
          }
          return;
        }
        nextTarget = pendingTarget;
      }
    };

    const promise = runRefreshes();
    bootstrapSilentRefreshPromiseRef.current = promise;
    try {
      await promise;
    } finally {
      if (bootstrapSilentRefreshPromiseRef.current === promise) {
        bootstrapSilentRefreshPromiseRef.current = null;
        if (bootstrapForegroundRequestRef.current === 0) {
          const pendingTarget = bootstrapSilentPendingTargetRef.current;
          bootstrapSilentPendingTargetRef.current = null;
          if (pendingTarget !== null) {
            void refreshSilently(pendingTarget);
          }
        }
      }
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

  async function refreshProjectList(
    target = endpoint,
    options: { allowDuringStartup?: boolean; throwOnError?: boolean } = {},
  ) {
    if ((!runtimeConnected && !options.allowDuringStartup) || projectRefreshInFlightRef.current) {
      return;
    }
    projectRefreshInFlightRef.current = true;
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
      setCachedProjectSnapshot(projectsPayload);
      cacheProjectSnapshot(projectsPayload);
      setError((current) => (current.toLowerCase().includes("project") ? "" : current));
    } catch (cause) {
      setError(cause instanceof Error ? cause.message : String(cause));
      if (options.throwOnError) {
        throw cause;
      }
    } finally {
      projectRefreshInFlightRef.current = false;
      setLoadingProjects(false);
    }
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

  async function saveAdvancedSettings(
    next: Partial<
      Pick<
        AdvancedSettingsState,
        "developerOptionsEnabled" | "computerUseEnabled" | "backgroundGoalNotificationsEnabled"
      >
    > & {
      developerChallengeId?: string;
    },
  ) {
    const nextDeveloperOptionsEnabled = next.developerOptionsEnabled ?? developerOptionsEnabled;
    const nextComputerUseEnabled = nextDeveloperOptionsEnabled && (next.computerUseEnabled ?? computerUseEnabled);
    const nextBackgroundGoalNotificationsEnabled =
      next.backgroundGoalNotificationsEnabled ?? backgroundGoalNotificationsEnabled;
    setSavingAdvancedSettings(true);
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
      const payload = await updateAdvancedSettings(targetEndpoint, {
        developerOptionsEnabled: nextDeveloperOptionsEnabled,
        computerUseEnabled: nextComputerUseEnabled,
        backgroundGoalNotificationsEnabled: nextBackgroundGoalNotificationsEnabled,
        developerChallengeId: next.developerChallengeId,
      });
      const settings = payload.settings;
      setDeveloperOptionsEnabled(settings.developerOptionsEnabled);
      setDeveloperOptionsEverEnabled(settings.developerOptionsEverEnabled);
      setComputerUseEnabled(settings.computerUseEnabled);
      setComputerUseEverEnabled(settings.computerUseEverEnabled);
      setBackgroundGoalNotificationsEnabled(settings.backgroundGoalNotificationsEnabled !== false);
      setBootstrap((current) => (current ? { ...current, advancedSettings: settings } : current));
    } catch (cause) {
      setError(cause instanceof Error ? cause.message : String(cause));
    } finally {
      setSavingAdvancedSettings(false);
    }
  }

  function copyConversationItem(item: ConversationItem) {
    const text = conversationItemText(item, t);
    const trimmed = text.trim();
    if (!trimmed) {
      showTransientNotice("error", "copy", t("chat.copyFailed", { reason: t("chat.copyNoText") }));
      return;
    }
    if (!navigator.clipboard?.writeText) {
      showTransientNotice("error", "copy", t("chat.copyFailed", { reason: t("chat.copyClipboardUnavailable") }));
      return;
    }
    void navigator.clipboard.writeText(trimmed).then(
      () => {
        showTransientNotice("success", "copy", t("chat.copiedMessage"));
      },
      (cause) => {
        const reason = cause instanceof Error ? cause.message : String(cause || "");
        showTransientNotice("error", "copy", t("chat.copyFailed", { reason: (reason || t("chat.copyUnknownReason")).slice(0, 500) }));
      },
    );
  }

  messageEditSnapshotRef.current = {
    draft: editingMessage,
    chatId: activeChatId,
    busy: isChatRunActive() || compacting || visibleQueued.length > 0
      || Boolean(editingMessage && hasUnresolvedRuntimeState(editingMessage.chatId, getChatById(editingMessage.chatId)?.sessionId || "")),
  };

  function editConversationMessage(itemId: string) {
    const chat = getChatById(activeChatId);
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
    setEditingMessage({
      chatId: chat.id,
      itemId,
      priorInput: input,
      priorAttachments: cloneChatAttachments(attachments),
      draftText: item.text,
      draftAttachments: cloneChatAttachments(item.attachments || []),
    });
    setRuntimeNotice(t("chat.editingMessage"));
  }

  function cancelMessageEdit() {
    if (!editingMessage) {
      return;
    }
    setInput(editingMessage.priorInput);
    setAttachments(cloneChatAttachments(editingMessage.priorAttachments));
    setEditingMessage(null);
    setRuntimeNotice("");
  }

  function discardMessageEdit() {
    if (!editingMessage) {
      return;
    }
    setInput(editingMessage.priorInput);
    setAttachments(cloneChatAttachments(editingMessage.priorAttachments));
    setEditingMessage(null);
    setRuntimeNotice("");
  }

  function updateEditingMessageText(nextText: string) {
    setEditingMessage((current) => {
      if (!current) {
        return current;
      }
      return { ...current, draftText: nextText };
    });
  }

  function removeEditingMessageAttachment(attachmentId: string) {
    setEditingMessage((current) => {
      if (!current) {
        return current;
      }
      return {
        ...current,
        draftAttachments: current.draftAttachments.filter((attachment) => attachment.id !== attachmentId),
      };
    });
  }

  async function saveMessageEdit() {
    const draft = editingMessage;
    if (!draft || messageEditSaveInFlightRef.current) return false;
    if (isChatRunActive() || compacting || visibleQueued.length > 0
      || hasUnresolvedRuntimeState(draft.chatId, getChatById(draft.chatId)?.sessionId || "")) {
      setError(t("chat.cannotActionWhileRunning"));
      return true;
    }
    const originalChat = getChatById(draft.chatId);
    if (!originalChat || originalChat.id !== activeChatId) {
      discardMessageEdit();
      return true;
    }
    const nextText = draft.draftText.trim();
    const nextAttachments = cloneChatAttachments(draft.draftAttachments);
    if (!nextText && nextAttachments.length === 0) {
      discardMessageEdit();
      return true;
    }
    messageEditSaveInFlightRef.current = true;
    setSavingMessageEdit(true);
    try {
      // Read the exact turn's live owner, not the ledger's historical tool and
      // approval events. "missing" also means no active/finalizing turn after
      // restart or recovery-cache expiry; it does not mean execution is pending.
      const originalItem = originalChat.items.find((item) => item.id === draft.itemId);
      const clientTurnId = originalItem?.type === "user" ? originalItem.clientTurnId : undefined;
      if (originalChat.sessionId && clientTurnId) {
        const state = await fetchAgentTurnResponse(endpoint, originalChat.sessionId, clientTurnId);
        if (!state.ok || state.sessionId !== originalChat.sessionId || state.clientTurnId !== clientTurnId
          || !["completed", "failed", "missing"].includes(state.status)) {
          setError(t("chat.cannotActionWhileRunning"));
          return true;
        }
      }
      const current = messageEditSnapshotRef.current;
      if (current.draft !== draft || current.chatId !== draft.chatId || current.busy || isChatRunActive()) return true;
      const chat = getChatById(draft.chatId);
      if (!chat || chat.sessionId !== originalChat.sessionId) return true;
      const index = chat.items.findIndex((item) => item.id === draft.itemId);
      const item = index >= 0 ? chat.items[index] : null;
      if (!item || item.type !== "user") return true;
      if (latestConversationItemId(chat.items, (entry) => entry.type === "user") !== draft.itemId) {
        setError(t("chat.latestMessageActionOnly", { defaultValue: "Only the latest message can be changed." }));
        return true;
      }
      const turnContextLimit = resolveContextLimit(providerSnapshot.provider, providerSnapshot.model, currentModelInfo, apiConfig?.contextWindow);
      const turn: QueuedTurn = {
        id: `edit-${Date.now()}-${Math.random().toString(16).slice(2)}`,
        text: nextText,
        planMode: item.planMode === true,
        attachments: nextAttachments,
        providerLabel: providerSnapshot.providerLabel,
        provider: providerSnapshot.provider,
        model: providerSnapshot.model,
        contextLimit: turnContextLimit.known ? turnContextLimit.limit : undefined,
      };
      setEditingMessage(null);
      setInput(draft.priorInput);
      setAttachments(cloneChatAttachments(draft.priorAttachments));
      await runTurnNow(chat.id, turn, {
        baseItems: chat.items.slice(0, index),
        sessionId: "",
        restoreOnFailure: {
          items: chat.items,
          sessionId: chat.sessionId,
          title: chat.title,
          updatedAt: chat.updatedAt,
        },
      });
    } catch (cause) {
      setError(cause instanceof Error ? cause.message : String(cause));
    } finally {
      messageEditSaveInFlightRef.current = false;
      setSavingMessageEdit(false);
    }
    return true;
  }

  function retryConversationItem(itemId: string) {
    if (isChatRunActive() || compacting) {
      setError(t("chat.cannotActionWhileRunning"));
      return;
    }
    const chat = getChatById(activeChatId);
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
    const turnContextLimit = resolveContextLimit(providerSnapshot.provider, providerSnapshot.model, currentModelInfo, apiConfig?.contextWindow);
    const turn: QueuedTurn = {
      id: `retry-${Date.now()}-${Math.random().toString(16).slice(2)}`,
      text: userItem.text,
      planMode: userItem.planMode === true,
      attachments: cloneChatAttachments(userItem.attachments || []),
      providerLabel: providerSnapshot.providerLabel,
      provider: providerSnapshot.provider,
      model: providerSnapshot.model,
      contextLimit: turnContextLimit.known ? turnContextLimit.limit : undefined,
    };
    void runTurnNow(chat.id, turn, {
      baseItems: chat.items.slice(0, userIndex),
      sessionId: "",
    });
  }

  async function runExplicitWorkspaceAction(actionId: ComposerActionId) {
    const action = composerActions.find((item) => item.id === actionId);
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
    if (actionId === "desktop") {
      if (!developerOptionsEnabled || !computerUseEnabled) {
        setError(t("computerUse.disabled"));
        return;
      }
      setError("");
      setInput((current) => {
        if (/^\/desktop(?:\s|$)/i.test(current.trimStart())) {
          return current;
        }
        return current.trim() ? `/desktop ${current.trimStart()}` : "/desktop ";
      });
      return;
    }
    setRightSidebarCollapsed(false);
    const desktopAction =
      actionId === "screenshot" || actionId === "annotation" || actionId === "browser" ? actionId : "";
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
          prependDesktopAction(payload.event);
        }
        void refreshRuntimeRuns(false);
      } catch (cause) {
        const message = cause instanceof Error ? cause.message : String(cause);
        setRuntimeNotice(message);
        setError(message);
      }
    }
  }

  async function compactChat() {
    if (isChatRunActive() || visibleQueued.length > 0 || compacting) {
      setError(t("compact.busy"));
      return;
    }
    if (!activeChat || activeChat.items.length === 0) {
      setError(t("compact.noContent"));
      return;
    }
    let targetEndpoint = endpoint;
    if (!runtimeConnected) {
      const readyEndpoint = await startRuntime();
      if (!readyEndpoint) {
        return;
      }
      targetEndpoint = readyEndpoint;
    }
    const limit = resolveContextLimit(providerSnapshot.provider, providerSnapshot.model, currentModelInfo, apiConfig?.contextWindow);
    await runContextCompaction({
      chatId: activeChat.id,
      endpoint: targetEndpoint,
      trigger: "manual",
      phase: "standalone",
      provider: providerSnapshot.provider,
      model: providerSnapshot.model,
      contextLimit: limit.known ? limit.limit : undefined,
    });
  }

  function openSettingsSection(section: SettingsSection = "general") {
    setActiveSettingsSection(section);
    if (activeView !== "settings") {
      void openSettings();
    }
  }

  async function createGoalFromSlash(raw: string) {
    const body = raw.replace(/^\/goal\s*/i, "").trim();
    if (!body) {
      setError(t("goal.empty"));
      return;
    }
    // 支持尾部唤醒指令："… +30m/+2h" 一次性、"… every 30m/2h" 周期；间隔越界由网关报错。
    const directive = parseGoalWakeDirective(body);
    if (!directive.title) {
      setError(t("goal.empty"));
      return;
    }
    try {
      const ownerChatId = ensureActiveChat();
      updateChat(ownerChatId, (chat) => touchChat({
        ...chat,
        title: chat.title || directive.title,
      }));
      await persistChatsNow();
      const ownerChat = getChatById(ownerChatId);
      const payload = await createAgentGoal(endpoint, {
        title: directive.title,
        wakeAt: directive.wakeAt,
        wakeEveryMinutes: directive.wakeEveryMinutes,
        sessionId: ownerChat?.sessionId || sessionId || undefined,
        chatId: ownerChatId,
        projectPath: ownerChat?.projectPath || activeRuntimeProjectPath || undefined,
        projectRoot: ownerChat?.projectPath || activeRuntimeProjectPath || undefined,
      });
      upsertAgentGoal(payload.goal);
      setRuntimeNotice(t("goal.created"));
      setInput("");
    } catch (cause) {
      const message = cause instanceof Error ? cause.message : String(cause);
      setError(message);
    }
  }

  async function handleGoalSlashCommand(raw: string) {
    const argument = raw.replace(/^\/goal\s*/i, "").trim();
    const command = argument.toLowerCase();
    if (!argument) {
      setRuntimeNotice(
        activeAgentGoal
          ? t("goal.current", {
            title: activeAgentGoal.title || activeAgentGoal.summary || activeAgentGoal.goalId,
            status: t(
              activeAgentGoal.status === "blocked"
                ? "goal.statusBlocked"
                : activeAgentGoal.status === "paused"
                  ? "goal.statusPaused"
                  : "goal.statusActive",
            ),
          })
          : t("goal.noInProgress"),
      );
      setInput("");
      return;
    }
    if (command === "pause" || command === "resume" || command === "clear") {
      if (!activeAgentGoal) {
        setRuntimeNotice(t("goal.noInProgress"));
        setInput("");
        return;
      }
      const status = command === "pause" ? "paused" : command === "resume" ? "active" : "cancelled";
      try {
        const payload = await updateAgentGoal(endpoint, activeAgentGoal.goalId, {
          status,
          sessionId: activeAgentGoal.sessionId,
          chatId: activeAgentGoal.chatId,
          projectRoot: activeAgentGoal.projectRoot,
        });
        upsertAgentGoal(payload.goal);
        setRuntimeNotice(t(command === "pause" ? "goal.paused" : command === "resume" ? "goal.resumed" : "goal.cleared"));
        setInput("");
      } catch (cause) {
        setError(cause instanceof Error ? cause.message : String(cause));
      }
      return;
    }
    if (activeAgentGoal) {
      setError(t("goal.alreadyActive"));
      return;
    }
    await createGoalFromSlash(raw);
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
      upsertAgentMemory(payload.memory);
      setRuntimeNotice(t("memory.created"));
      setInput("");
    } catch (cause) {
      const message = cause instanceof Error ? cause.message : String(cause);
      setError(message);
    }
  }

  async function submitMessage(event?: FormEvent, deliveryMode: "steer" | "queue" = "steer") {
    event?.preventDefault();
    if (activeChatPending) {
      setRuntimeNotice(t("common.loading"));
      return;
    }
    if (attachmentLoadsRef.current > 0) return;
    const composerMessage = input.trim();
    let message = composerMessage;
    let computerUseRequested = false;
    if (!composerMessage && attachments.length === 0) {
      return;
    }
    setError("");
    if (pendingReplyQuestionId) {
      if (replySubmitInFlightRef.current) return;
      const question = agentQuestions.find((item) => item.questionId === pendingReplyQuestionId);
      if (!question) {
        setPendingReplyQuestionId("");
        return;
      }
      replySubmitInFlightRef.current = true;
      try {
        const payload = await answerAgentQuestion(endpoint, pendingReplyQuestionId, {
          answer: composerMessage,
          selectedOptionId: "custom",
          attachments: serializeChatAttachments(attachments),
          sessionId: question.sessionId || sessionId,
          projectRoot: question.projectRoot || activeRuntimeProjectPath || undefined,
        });
        upsertAgentQuestion(payload.question);
        const answerChat = activeChat && (!question.sessionId || activeChat.sessionId === question.sessionId)
          ? activeChat
          : undefined;
        if (answerChat) {
          const answerItem = questionAnswerItem(payload.question, payload.question.answer || composerMessage, attachments);
          persistQuestionAnswer(answerChat.id, answerItem);
        }
        setPendingReplyQuestionId("");
        setInput("");
        setAttachments([]);
        void refreshRuntimeRuns(false);
      } catch (cause) {
        setError(cause instanceof Error ? cause.message : String(cause));
      } finally {
        replySubmitInFlightRef.current = false;
      }
      return;
    }
    if (!chatAvailable) {
      const message = chatDisabledReason || t("chat.connectProviderBeforeSend");
      setError(message);
      showTransientFailure("send", message);
      return;
    }
    if (deliveryMode === "steer" && (message === "/compact" || message.startsWith("/compact "))) {
      void compactChat();
      setInput("");
      return;
    }
    if (deliveryMode === "steer" && (message === "/goal" || message.startsWith("/goal "))) {
      void handleGoalSlashCommand(message);
      return;
    }
    if (deliveryMode === "steer" && (message === "/memory" || message.startsWith("/memory "))) {
      void createMemoryFromSlash(message);
      return;
    }
    if (deliveryMode === "steer" && (message === "/delegate" || message.startsWith("/delegate "))) {
      const command = parseDelegateCommand(
        message,
        skills.map((skill) => skill.name),
      );
      void startSubAgentTask(
        command.toolName ? "skill_delegate" : undefined,
        command.task || undefined,
        command.toolName,
        command.targetKind === "skill" ? command.task : undefined,
      );
      setInput("");
      return;
    }
    if (deliveryMode === "steer" && (message === "/handoff" || message.startsWith("/handoff "))) {
      setHandoffSendOpen((current) => !current);
      setInput("");
      return;
    }
    if (deliveryMode === "steer" && (message === "/desktop" || message.startsWith("/desktop "))) {
      if (!developerOptionsEnabled || !computerUseEnabled) {
        setError(t("computerUse.disabled"));
        return;
      }
      const task = message.replace(/^\/desktop\s*/i, "").trim();
      if (!task) {
        setError(t("computerUse.taskRequired"));
        return;
      }
      message = task;
      computerUseRequested = true;
    }
    const nextClientTurnId = `turn-${Date.now()}-${Math.random().toString(16).slice(2)}`;
    try {
      const handoff = await sessionHandoff.consume(nextClientTurnId);
      const payload = handoff.context?.payload;
      if (payload && typeof payload === "object") {
        const bounded = JSON.stringify({
          goal: payload.goal,
          decisions: payload.decisions,
          blockers: payload.blockers,
          nextAction: payload.nextAction,
          question: payload.question,
        }).slice(0, 3000);
        if (bounded !== "{}") message = `[Session handoff context]\n${bounded}\n\n${message}`;
      }
    } catch {
      // A failed consume leaves the durable queue untouched for a later turn.
    }
    const turnContextLimit = resolveContextLimit(providerSnapshot.provider, providerSnapshot.model, currentModelInfo, apiConfig?.contextWindow);
    const turn: QueuedTurn = {
      id: nextClientTurnId,
      planMode: activeChat?.planMode === true,
      text: message,
      attachments,
      providerLabel: providerSnapshot.providerLabel,
      provider: providerSnapshot.provider,
      model: providerSnapshot.model,
      contextLimit: turnContextLimit.known ? turnContextLimit.limit : undefined,
      computerUseRequested,
      computerUseVisualTheme: computerUseRequested ? theme : undefined,
      computerUseVisualAccent: computerUseRequested ? resolveComputerUseAccentHex() || undefined : undefined,
    };
    setInput("");
    setAttachments([]);
    const result = await submitTurn(turn, { deliveryMode });
    if (result === "not_accepted") {
      setInput(composerMessage);
      setAttachments(turn.attachments);
    }
  }

  function stopInteractiveActivity(actionId?: string) {
    if (currentTurn?.clientTurnId || isChatRunActive()) {
      stopCurrentRun();
      return;
    }
    if (compacting && activeChatId) {
      cancelCompaction(activeChatId);
      return;
    }
    if (actionId) {
      void cancelDesktopAction(actionId);
    }
  }

  async function addComposerFiles(files: FileList | File[] | null) {
    if (!files || files.length === 0) {
      return;
    }
    const remaining = Math.max(0, MAX_ATTACHMENTS_PER_TURN - attachments.length);
    if (remaining === 0) {
      const message = t("attachments.limitReached", { max: MAX_ATTACHMENTS_PER_TURN });
      setError(message);
      showTransientFailure("upload", message);
      return;
    }
    const selected = Array.from(files).slice(0, remaining);
    const replyQuestion = pendingReplyQuestionId
      ? agentQuestions.find((question) => question.questionId === pendingReplyQuestionId)
      : undefined;
    const chatId = replyQuestion?.sessionId || ensureActiveChat();
    const nextAttachments: ChatAttachment[] = [];
    attachmentLoadsRef.current += 1;
    setAttachmentsLoading(true);
    try {
      for (const file of selected) {
        try {
          const attachment = await ingestChatAttachment(file, { endpoint, chatId }, t);
          nextAttachments.push(attachment);
          if (attachment.error) {
            showTransientFailure("upload", attachment.error);
          }
        } catch (cause) {
          const message = cause instanceof Error ? cause.message : String(cause);
          setError(message);
          showTransientFailure("upload", message);
        }
      }
      setAttachments((current) => [...current, ...nextAttachments].slice(0, MAX_ATTACHMENTS_PER_TURN));
    } finally {
      attachmentLoadsRef.current -= 1;
      setAttachmentsLoading(attachmentLoadsRef.current > 0);
    }
    if (files.length > remaining) {
      const message = t("attachments.limitOneTurn", { max: MAX_ATTACHMENTS_PER_TURN });
      setError(message);
      showTransientFailure("upload", message);
    }
  }

  function removeAttachment(id: string) {
    setAttachments((current) => current.filter((attachment) => attachment.id !== id));
  }

  async function importVaultAttachment(attachment: ChatAttachment) {
    const payloadHash = attachment.payloadKind === "vault_file" ? attachment.payloadHash : attachment.vaultPayloadHash;
    if (!payloadHash) {
      return;
    }
    setError("");
    try {
      const response = await requestChatAttachmentImport(endpoint, {
        payloadHash,
        projectPath: activeChat?.projectPath || activeProjectPath || undefined,
      });
      const approval = response?.approval as AgentApproval | undefined;
      if (approval && typeof approval.id === "string" && approval.id) {
        // Surface the pending import approval immediately; runtime polling keeps it in sync.
        setAgentApprovals((current) => {
          const existing = current ?? [];
          return existing.some((item) => item.id === approval.id) ? existing : [approval, ...existing];
        });
      }
    } catch (cause) {
      setError(cause instanceof Error ? cause.message : String(cause));
    }
  }

  async function reconcileSubAgentHandoffs(tasks: SubAgentTask[]) {
    for (const task of tasks) {
      if (!task.parentChatId) {
        continue;
      }
      const parentChat = getChatById(task.parentChatId);
      if (!parentChat || subAgentHandoffBusyRef.current.has(task.id)) {
        continue;
      }
      const publicEvents = (task.events || [])
        .filter((event) => ["created", "started", "completed", "failed", "cancel_requested", "cancelled", "interrupted"].includes(String(event.event || "")))
        .sort((left, right) => Number(left.revision || 0) - Number(right.revision || 0));
      if (!publicEvents.length && Number(task.revision || 0) > 0) {
        publicEvents.push({
          timestamp: task.updatedAt || task.createdAt,
          taskId: task.id,
          event: task.status === "queued" ? "created" : task.status,
          revision: task.revision,
          data: {},
        });
      }
      const eventItems: ConversationItem[] = publicEvents.map((event) => {
        const eventName = String(event.event || task.status || "started");
        const revision = Number(event.revision || task.revision || 0);
        const timestamp = String(event.timestamp || task.updatedAt || task.createdAt || new Date().toISOString());
        const eventData = event.data && typeof event.data === "object" ? event.data : {};
        const summary = String(
          eventData.summary
          || eventData.error
          || (eventName === "created" ? task.task : "")
          || (eventName === "completed" ? task.summary : "")
          || (["failed", "cancelled", "interrupted"].includes(eventName) ? task.error : "")
          || "",
        );
        const subagentStatus = eventName === "created"
          ? "created"
          : eventName === "started" || eventName === "cancel_requested"
            ? "started"
            : eventName === "completed"
              ? "completed"
              : "failed";
        return {
          id: `subagent-event-${task.id}-${revision}`,
          type: "timeline_event",
          createdAt: timestamp,
          event: {
            id: `subagent-event-${task.id}-${revision}`,
            sequence: revision,
            timestamp,
            kind: "subagent",
            sessionId: String(task.parentSessionId || "").slice(0, 180) || undefined,
            turnId: String(task.parentTurnId || "").slice(0, 180) || undefined,
            clientTurnId: String(task.parentClientTurnId || "").slice(0, 240) || undefined,
            payload: {
              label: task.displayName || t("agent.subagentTask"),
              summary: summary.slice(0, 1000),
              status: eventName,
              tool: task.role,
              subagentStatus,
            },
          },
        };
      });
      const knownIds = new Set(parentChat.items.map((item) => item.id));
      const newEventItems = eventItems.filter((item) => !knownIds.has(item.id));
      const needsTimelineUpdate = newEventItems.length > 0
        || parentChat.items.some((item) => item.id === `subagent-${task.id}`);
      if (!needsTimelineUpdate && task.handoffStatus !== "handoff_pending") {
        continue;
      }
      subAgentHandoffBusyRef.current.add(task.id);
      try {
        if (needsTimelineUpdate) {
          updateChat(parentChat.id, (chat) => {
            const items = chat.items
              .filter((item) => item.id !== `subagent-${task.id}`)
              .concat(newEventItems)
              .sort((left, right) => {
                const leftCreatedAt = "createdAt" in left ? left.createdAt : left.type === "subagent" ? left.task.updatedAt : "";
                const rightCreatedAt = "createdAt" in right ? right.createdAt : right.type === "subagent" ? right.task.updatedAt : "";
                const leftTime = Date.parse(leftCreatedAt || "");
                const rightTime = Date.parse(rightCreatedAt || "");
                if (Number.isFinite(leftTime) && Number.isFinite(rightTime) && leftTime !== rightTime) {
                  return leftTime - rightTime;
                }
                return 0;
              });
            return touchChat({ ...chat, items }, task.updatedAt || new Date().toISOString());
          });
        }
        if (needsTimelineUpdate || task.handoffStatus === "handoff_pending") {
          await persistChatsNow();
        }
        if (task.handoffStatus === "handoff_pending") {
          const acknowledged = await acknowledgeSubAgentHandoff(endpoint, task.id, task.revision);
          setSubAgentList((current) => updateSubAgentList(current, acknowledged.task));
          setSelectedSubAgent((current) => (current?.id === acknowledged.task.id ? acknowledged.task : current));
        }
      } catch {
        // Leave handoff_pending durable; the next event/poll retries the exact
        // same stable card id without duplicating it.
      } finally {
        subAgentHandoffBusyRef.current.delete(task.id);
      }
    }
  }

  async function loadSubAgents(includeEvents = false) {
    if (!runtimeConnected && !includeEvents) {
      return;
    }
    setLoadingSubAgents(true);
    try {
      const payload = await fetchSubAgents(endpoint, true);
      setSubAgentList(payload);
      setSelectedSubAgent((current) => reconcileSelectedSubAgent(current, payload.tasks));
      setSubAgentError("");
      await reconcileSubAgentHandoffs(payload.tasks);
    } catch (cause) {
      setSubAgentError(cause instanceof Error ? cause.message : String(cause));
    } finally {
      setLoadingSubAgents(false);
    }
  }

  async function startSubAgentTask(
    roleOverride?: string,
    taskOverride?: string,
    toolName?: string,
    skillArguments?: string,
  ) {
    const agentName = pickSubAgentName();
    const projectPath = activeChat?.projectPath || activeProjectPath;
    const hasPackage = outfitPackagePath.trim().length > 0;
    const role = roleOverride || "llm_readonly";
    const defaultTask =
      role === "skill_delegate"
        ? `Run the delegated skill ${toolName || ""} and report its output.`.trim()
        : role === "outfit_import_plan_review"
          ? "Inspect the selected outfit package and return a supervised import plan summary."
          : role === "validation_triage"
            ? "Run read-only validation triage and summarize findings."
            : hasPackage
            ? `Inspect the outfit package ${outfitPackagePath.trim()} and return a supervised import plan summary.`
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
      const parentChatId = ensureActiveChat();
      updateChat(parentChatId, (chat) => touchChat({ ...chat, title: chat.title || task }));
      await persistChatsNow();
      const parentChat = getChatById(parentChatId);
      const payload = await createSubAgent(targetEndpoint, {
        role,
        task,
        displayName: agentName,
        parentChatId,
        parentSessionId: parentChat?.sessionId || "",
        projectPath: parentChat?.projectPath || projectPath,
        params: {
          projectPath,
          packagePath: outfitPackagePath.trim(),
          ...(toolName ? { toolName } : {}),
          ...(skillArguments?.trim() ? { skillArguments: skillArguments.trim() } : {}),
        },
      });
      setSelectedSubAgent(payload.task);
      setSelectedSubAgentPanelOpen(true);
      setSubAgentList((current) => updateSubAgentList(current, payload.task));
      await reconcileSubAgentHandoffs([payload.task]);
      void loadSubAgents(false);
    } catch (cause) {
      setSubAgentError(cause instanceof Error ? cause.message : String(cause));
    }
  }

  function beginSubAgentAction(taskId: string): boolean {
    if (subAgentActionBusyRef.current.has(taskId)) {
      return false;
    }
    subAgentActionBusyRef.current.add(taskId);
    setSubAgentActionBusyTaskIds(new Set(subAgentActionBusyRef.current));
    return true;
  }

  function endSubAgentAction(taskId: string) {
    subAgentActionBusyRef.current.delete(taskId);
    setSubAgentActionBusyTaskIds(new Set(subAgentActionBusyRef.current));
  }

  async function cancelSubAgentTask(taskId: string) {
    if (!beginSubAgentAction(taskId)) {
      return;
    }
    try {
      const payload = await cancelSubAgent(endpoint, taskId);
      setSubAgentList((current) => updateSubAgentList(current, payload.task));
      setSelectedSubAgent((current) => (current?.id === payload.task.id ? payload.task : current));
      await refreshSelectedSubAgentTask(taskId, "if-current");
    } catch (cause) {
      setSubAgentError(cause instanceof Error ? cause.message : String(cause));
    } finally {
      endSubAgentAction(taskId);
    }
  }

  async function retrySubAgentTask(taskId: string) {
    if (!beginSubAgentAction(taskId)) {
      return;
    }
    const selectionIntentAtStart = subAgentSelectionIntentRef.current;
    try {
      const payload = await retrySubAgent(endpoint, taskId);
      setSubAgentList((current) => updateSubAgentList(current, payload.task));
      if (
        selectionIntentAtStart !== taskId
        || subAgentSelectionIntentRef.current !== selectionIntentAtStart
      ) {
        return;
      }
      setSelectedSubAgent(payload.task);
      subAgentSelectionIntentRef.current = payload.task.id;
      await refreshSelectedSubAgentTask(payload.task.id, "if-current");
    } catch (cause) {
      setSubAgentError(cause instanceof Error ? cause.message : String(cause));
    } finally {
      endSubAgentAction(taskId);
    }
  }

  async function resumeSubAgentTask(taskId: string, question: string): Promise<boolean> {
    if (!beginSubAgentAction(taskId)) {
      return false;
    }
    const selectionIntentAtStart = subAgentSelectionIntentRef.current;
    try {
      const payload = await resumeSubAgent(endpoint, taskId, { task: question });
      setSubAgentList((current) => updateSubAgentList(current, payload.task));
      if (
        selectionIntentAtStart !== taskId
        || subAgentSelectionIntentRef.current !== selectionIntentAtStart
      ) {
        return true;
      }
      setSelectedSubAgent(payload.task);
      subAgentSelectionIntentRef.current = payload.task.id;
      await refreshSelectedSubAgentTask(payload.task.id, "if-current");
      return true;
    } catch (cause) {
      setSubAgentError(cause instanceof Error ? cause.message : String(cause));
      return false;
    } finally {
      endSubAgentAction(taskId);
    }
  }

  async function refreshSelectedSubAgentTask(taskId: string, mode: "select" | "if-current" = "select") {
    const requestId = mode === "select" ? ++subAgentInspectRequestRef.current : subAgentInspectRequestRef.current;
    if (mode === "select") {
      subAgentSelectionIntentRef.current = taskId;
    }
    const payload = await fetchSubAgent(endpoint, taskId);
    setSubAgentList((current) => updateSubAgentList(current, payload.task));
    const selectionIsCurrent = subAgentSelectionIntentRef.current === taskId;
    if ((mode === "select" && requestId !== subAgentInspectRequestRef.current) || !selectionIsCurrent) {
      return payload.task;
    }
    setSelectedSubAgent(payload.task);
    return payload.task;
  }

  async function inspectSubAgentTask(taskId: string) {
    const task = subAgentTasksRef.current.find((item) => item.id === taskId);
    if (task) setSelectedSubAgent(task);
    setSubAgentError("");
    setSelectedSubAgentPanelOpen(true);
    try {
      await refreshSelectedSubAgentTask(taskId);
    } catch (cause) {
      setSubAgentError(cause instanceof Error ? cause.message : String(cause));
    }
  }

  async function openSubAgentWorkspace() {
    setSubAgentSidebarOpen(true);
    setSelectedSubAgentPanelOpen(false);
    setRightSidebarCollapsed(false);
  }

  async function mergeSubAgentTask(task: SubAgentTask, decision: "adopted" | "dismissed") {
    if (!beginSubAgentAction(task.id)) {
      return;
    }
    try {
      if (!task.parentChatId || !getChatById(task.parentChatId)) {
        throw new Error("Sub-agent parent chat is unavailable.");
      }
      const latest = await fetchSubAgent(endpoint, task.id);
      const payload = await mergeSubAgent(endpoint, task.id, {
        decision,
        chatId: latest.task.parentChatId || task.parentChatId,
        expectedRevision: latest.task.revision,
      });
      setSubAgentList((current) => updateSubAgentList(current, payload.task));
      setSelectedSubAgent((current) => (current && current.id === payload.task.id ? payload.task : current));
      await loadSubAgents(true);
      await refreshSelectedSubAgentTask(payload.task.id, "if-current");
      setActiveView("chat");
    } catch (cause) {
      setSubAgentError(cause instanceof Error ? cause.message : String(cause));
      void loadSubAgents(false);
    } finally {
      endSubAgentAction(task.id);
    }
  }

  function ignoreSubAgentReviewNotification(task: SubAgentTask) {
    if (!task.parentChatId || typeof task.revision !== "number" || !Number.isInteger(task.revision) || task.revision <= 0) {
      return;
    }
    const notificationId = toReviewNotificationId(task);
    knownSubAgentReviewNotificationIdsRef.current.add(notificationId);
    inFlightSubAgentReviewNotificationIdsRef.current.delete(notificationId);
    deliveredSubAgentReviewNotificationIdsRef.current.add(notificationId);
    saveSubAgentReviewNotificationIds(deliveredSubAgentReviewNotificationIdsRef.current);
    setSelectedSubAgent(null);
    setSelectedSubAgentPanelOpen(false);
    setSubAgentError("");
  }

  function adoptSubAgentNextAction(task: SubAgentTask) {
    const nextAction = subAgentProposedNextAction(task);
    if (!nextAction) {
      return;
    }
    setActiveView("chat");
    setInput(nextAction);
  }

  function handleConversationMouseUp() {
    window.setTimeout(() => {
      const quote = conversationQuoteFromSelection(window.getSelection());
      if (!quote) {
        setSelectionMenu(null);
        return;
      }
      setSelectionMenu(quote);
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
    setSelectedSubAgentPanelOpen(true);
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
      const parentChatId = ensureActiveChat();
      updateChat(parentChatId, (chat) => touchChat({
        ...chat,
        title: chat.title || selectedText.slice(0, 80),
      }));
      await persistChatsNow();
      const parentChat = getChatById(parentChatId);
      const payload = await createSubAgent(targetEndpoint, {
        role: "llm_readonly",
        task: "Review the selected conversation excerpt in a scoped sub-agent thread.",
        selectedMaterials: [{ content: { text: selectedText } }],
        displayName: agentName,
        parentChatId,
        parentSessionId: parentChat?.sessionId || "",
        projectPath: parentChat?.projectPath || projectPath,
        params: {
          projectPath,
          source: "selection-menu",
        },
      });
      setSelectedSubAgent(payload.task);
      setSubAgentList((current) => ({
        ok: true,
        schema: current?.schema || "vrcforge.sub_agent_tasks.v2",
        tasks: [payload.task, ...(current?.tasks || []).filter((taskItem) => taskItem.id !== payload.task.id)],
        count: (current?.count || 0) + 1,
        roles: current?.roles,
        maxConcurrent: current?.maxConcurrent,
        runningCount: (current?.runningCount || 0) + 1,
      }));
      await reconcileSubAgentHandoffs([payload.task]);
      void loadSubAgents(false);
    } catch (cause) {
      setSubAgentError(cause instanceof Error ? cause.message : String(cause));
    }
  }

  function askInNewSession(text: string) {
    void openSelectionInSubAgent(text);
  }

  function finishOnboarding() {
    try {
      window.localStorage.setItem(ONBOARDING_FLAG_KEY, "true");
    } catch {
      // Keep onboarding close usable even if local storage is blocked.
    }
    setShowOnboarding(false);
    setShowOnboardingLanguageGate(false);
    setOnboardingMinimized(false);
    setProviderSetupGuide(false);
    setExternalSetupGuide(false);
  }

  function returnFromOnboardingSettings() {
    setActiveView("chat");
    setProviderSetupGuide(false);
    setExternalSetupGuide(false);
    if (showOnboarding) setOnboardingMinimized(false);
  }

  function restartOnboarding() {
    try {
      window.localStorage.removeItem(ONBOARDING_FLAG_KEY);
    } catch {
      // Ignore blocked local storage.
    }
    setActiveView("chat");
    setOnboardingStep(0);
    setOnboardingMinimized(false);
    setProviderSetupGuide(false);
    setExternalSetupGuide(false);
    setShowOnboardingLanguageGate(false);
    setShowOnboarding(true);
  }

  async function completeOnboardingLanguageGate(locale: LocaleCode) {
    try {
      await setLocale(locale);
    } catch {
      return;
    }
    persistOnboardingLanguageGateCompletion();
    setShowOnboardingLanguageGate(false);
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
    setDoctorMessageTone("ok");
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

  async function refreshDoctorAndChatRecovery() {
    if (chatPersistenceBlocked || chatRecoveries.length > 0) {
      try {
        await reloadChatStorageState();
      } catch (cause) {
        setError(cause instanceof Error ? cause.message : String(cause));
      }
    }
    await loadDoctor();
  }

  const { fixingCheckId, lastFixResult, fixCheck } = useDoctorFixController({
    endpoint,
    projectPath: activeProjectPath,
    onRefresh: async (result) => {
      if (result.checkId === "session.storage") {
        await reloadChatStorageState();
      }
      await loadDoctor();
      await refreshWithRetry(endpoint);
    },
    onMessage: (message, tone) => {
      setDoctorMessage(message);
      setDoctorMessageTone(tone);
    },
    onError: setError,
  });

  const projectChatWorkspace = activeView === "chat" && Boolean(activeChat?.projectPath);
  const subAgentActivityPanel = (
    <SubAgentPanel
      tasks={activeSubAgentTasks}
      loading={loadingSubAgents}
      error={subAgentError}
      roles={subAgentList?.roles || []}
      onOpen={(taskId) => void (taskId ? inspectSubAgentTask(taskId) : openSubAgentWorkspace())}
    />
  );
  const subAgentWorkspaceSurface = subAgentSidebarActive ? (
    <div className="h-full min-h-0 overflow-hidden bg-workspace">
      <Suspense fallback={<div className="p-4 text-xs text-muted-foreground">{t("common.loading")}</div>}>
        <AsyncSubAgentSidebar
          tasks={activeSubAgentTasks}
          openThread={selectedSubAgentPanelOpen}
          loading={loadingSubAgents}
          error={subAgentError}
          onBack={() => { setSubAgentSidebarOpen(true); setSelectedSubAgentPanelOpen(false); }}
          endpoint={endpoint}
          developerOptionsEnabled={developerOptionsEnabled}
          roles={subAgentList?.roles || []}
          selected={activeSubAgentTasks.some((task) => task.id === selectedSubAgent?.id) ? selectedSubAgent : null}
          onSelect={(taskId) => void inspectSubAgentTask(taskId)}
          onCancel={(taskId) => void cancelSubAgentTask(taskId)}
          onRetry={(taskId) => void retrySubAgentTask(taskId)}
          onResume={resumeSubAgentTask}
          onMerge={(task, decision) => void mergeSubAgentTask(task, decision)}
          onIgnoreNotification={ignoreSubAgentReviewNotification}
          onAdoptNextAction={adoptSubAgentNextAction}
          busyTaskIds={subAgentActionBusyTaskIds}
          onClose={() => { setSubAgentSidebarOpen(false); setSelectedSubAgentPanelOpen(false); }}
        />
      </Suspense>
    </div>
  ) : null;

  return (
    <main className="h-screen overflow-hidden bg-background text-foreground">
      <div className="grid h-screen" style={{ gridTemplateColumns: workspaceGridColumns }}>
        {sidebarsVisible ? (
          <Suspense fallback={<SidebarPlaceholder side="left" />}>
            <SidebarMountTracker side="left" onMounted={markLeftSidebarMounted}>
              <AsyncAppSidebar
          collapsed={false}
          activeView={activeView}
          activeSettingsSection={activeSettingsSection}
          developerOptionsEnabled={developerOptionsEnabled}
          temporaryChatActive={activeView === "chat" && !activeProjectPath && !activeChat}
          activeProjectPath={activeProjectPath}
          activeChatId={activeChatId}
          runtimeConnected={runtimeConnected}
          loadingProjects={loadingProjects}
          projectItems={sidebarProjectItems}
          chatSidebar={chatSidebar}
          backgroundGoalUnreadByChat={backgroundGoalState?.unreadByChat || {}}
          emptyProjectState={emptyProjectState}
          collapsedProjects={collapsedProjects}
          temporaryChatsCollapsed={Boolean(collapsedProjects[TEMP_CHATS_COLLAPSE_KEY])}
          pinnedProjectSet={pinnedProjectSet}
          renamingProjectPath={renamingProjectPath}
          projectRenameDraft={projectRenameDraft}
          renamingChatId={renamingChatId}
          renameDraft={renameDraft}
          projectDisplayName={projectDisplayLabel}
          onNewTemporaryChat={openTemporaryChat}
          onOpenProjectPicker={() => {
            setProjectModalError("");
            setShowProjectModal(true);
          }}
          onOpenDoctor={() => void openDoctor()}
          onOpenOptimization={() => void openOptimization()}
          onOpenProtection={() => void openProtection()}
          onOpenSkills={() => void openSkills()}
          onOpenCheckpoints={() => void openCheckpoints()}
          onOpenSettings={() => openSettingsSection("general")}
          onOpenSettingsSection={openSettingsSection}
          onBackFromSettings={returnFromOnboardingSettings}
          onRefreshProjects={() => void refreshProjectList()}
          onSelectProject={newConversationForProject}
          onReorderProjects={reorderProjects}
          onResetProjectOrder={resetProjectOrder}
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
            </SidebarMountTracker>
          </Suspense>
        ) : (
          <SidebarPlaceholder side="left" />
        )}

        <LayoutSplitter
          side="left"
          value={effectiveLeftPaneWidth}
          min={MIN_LEFT_PANE_WIDTH}
          max={MAX_LEFT_PANE_WIDTH}
          title={t("workspace.resizeLeftPane")}
          onPointerDown={(event) => startLayoutResize("left", event)}
        />

        <section className="relative flex h-screen min-w-0 flex-col overflow-hidden bg-workspace">
          <WorkspaceHeader
            activeProjectLabel={activeProjectPath ? activeProjectName : t("sidebar.tempChat")}
            activeView={activeView}
            activeChatTitle={displayChat ? displayChat.title || t("header.currentSession") : activeChatPending ? t("header.currentSession") : ""}
            theme={theme}
            showDoctorStartupPrompt={showDoctorStartupPrompt}
            hasStartupIssue={hasStartupIssue}
            healthErrors={healthErrors}
            healthWarnings={healthWarnings}
            startupIssue={startupIssue}
            loadingDoctor={loadingDoctor}
            loading={loading}
            error={error}
            onToggleTheme={() => setTheme(theme === "dark" ? "light" : "dark")}
            onOpenDoctor={() => void openDoctor()}
            onRetryStartupOrHealth={() => void retryStartupOrHealth()}
            onDismissDoctorPrompt={() => setDismissedDoctorPromptSignature(doctorPromptSignature)}
            onStartRuntime={() => void startRuntime()}
          />

          {activeView === "doctor" ? (
            <DoctorWorkspace
              report={doctorReport}
              loading={loadingDoctor}
              message={doctorMessage}
              messageTone={doctorMessageTone}
              fixingCheckId={fixingCheckId}
              lastFixResult={lastFixResult}
              chatRecoveries={chatRecoveries}
              chatPersistenceBlocked={chatPersistenceBlocked}
              resolvingChatStorageConflict={resolvingChatStorageConflict}
              exportingSupportBundle={exportingSupportBundle}
              onRefresh={() => void refreshDoctorAndChatRecovery()}
              onFix={(checkId, mode) => void fixCheck(checkId, mode)}
              onResolveChatConflict={() => {
                void resolveChatStorageConflict().catch((cause) => {
                  setError(cause instanceof Error ? cause.message : String(cause));
                });
              }}
              onOpenSettings={() => openSettingsSection("general")}
              onExportSupportBundle={() => void createSupportBundle()}
              onCopy={() => {
                if (!doctorReport) {
                  return;
                }
                void navigator.clipboard
                  .writeText(JSON.stringify(doctorReport, null, 2))
                  .then(() => {
                    setDoctorMessage(t("doctor.copiedSummary"));
                    setDoctorMessageTone("ok");
                  })
                  .catch((cause) => setError(cause instanceof Error ? cause.message : String(cause)));
              }}
              formatPayload={formatPayload}
            />
          ) : activeView === "skills" ? (
            <SkillsWorkspace
              endpoint={endpoint}
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
              pathToSkillDraftSeed={pathToSkillDraftSeed}
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
              onPreviewPathToSkill={previewCapturedPath}
              onWritePathToSkill={writeCapturedPath}
              selectedUnityProjectPath={activeProjectType === "unity" ? activeProjectPath : ""}
              onRefreshApprovals={() => void refreshSilently()}
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
              onRefresh={() => void loadCheckpoints(endpoint, { force: true })}
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
              onRefresh={() => void loadProtectionPlan(endpoint, protectionProfile, true)}
              onRefreshAvatars={() => void loadProtectionAvatars(endpoint, true)}
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
              activeSection={activeSettingsSection}
              endpoint={endpoint}
              developerOptionsEnabled={developerOptionsEnabled}
              developerOptionsEverEnabled={developerOptionsEverEnabled}
              computerUseEnabled={computerUseEnabled}
              computerUseEverEnabled={computerUseEverEnabled}
              backgroundGoalNotificationsEnabled={backgroundGoalNotificationsEnabled}
              savingAdvancedSettings={savingAdvancedSettings}
              permission={permission ?? null}
              loading={loading}
              runtimeConnected={runtimeConnected}
              currentLanguage={i18n.language}
              themeCustomization={themeCustomization}
              themeBackgroundError={themeBackgroundError}
              apiProvider={apiProvider}
              apiKey={apiKey}
              apiBaseUrl={apiBaseUrl}
              apiModel={apiModel}
              apiType={apiType}
              apiContextWindow={apiContextWindow}
              selectedModelCapabilities={selectedModelCapabilities}
              selectedModelCapabilitySource={selectedModelCapabilitySource}
              apiThinkingLevel={apiThinkingLevel}
              reasoningVariants={reasoningVariants}
              apiKeySaved={apiKeySaved}
              savingApiConfig={savingApiConfig}
              providerConfigurationLocked={chatRunSending || Boolean(apiConfig?.runtimeLocked)}
              modelOptions={modelOptions}
              loadingModels={loadingModels}
              modelsError={modelsError}
              testingProvider={testingProvider}
              providerTestMessage={providerTestMessage}
              providerTestPassed={providerTestPassed}
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
              memoryReviewRefreshSignal={memoryReviewRefreshSignal}
              isDesktop={isTauriRuntime()}
              checkpointArchiveLimitInput={checkpointArchiveLimitInput}
              agentNotes={agentNotes}
              agentNotesLoaded={agentNotesLoaded}
              agentNotesPath={agentNotesPath}
              notesMessage={notesMessage}
              savingNotes={savingNotes}
              compactDebugEntries={compactDebugEntries}
              onSectionChange={setActiveSettingsSection}
              onDeveloperOptionsChange={(enabled, developerChallengeId) =>
                saveAdvancedSettings({
                  developerOptionsEnabled: enabled,
                  computerUseEnabled: enabled ? computerUseEnabled : false,
                  developerChallengeId,
                })
              }
              onComputerUseChange={(enabled) => void saveAdvancedSettings({ computerUseEnabled: enabled })}
              onBackgroundGoalNotificationsChange={(enabled) =>
                void saveAdvancedSettings({ backgroundGoalNotificationsEnabled: enabled })
              }
              onSwitchMode={(mode) => void switchMode(mode)}
              onRestartOnboarding={restartOnboarding}
              onLocaleChange={(code) => void setLocale(code)}
              onThemeCustomizationChange={updateThemeCustomization}
              onResetThemeCustomization={resetThemeCustomization}
              onLoadModels={() => void loadModels()}
              onProviderTest={(capability) => void runProviderTest(capability)}
              onProviderConfigChanged={() => refresh(endpoint)}
              onProviderChange={handleProviderChange}
              onApiKeyChange={setApiKey}
              onApiBaseUrlChange={setApiBaseUrl}
              onApiModelChange={setApiModel}
              onProtocolAutoNegotiationChange={handleProtocolAutoNegotiationChange}
              onApiTypeChange={setApiType}
              onApiContextWindowChange={setApiContextWindow}
              onApiThinkingLevelChange={setApiThinkingLevel}
              onSaveApiProvider={saveApiProvider}
              onVisionProviderChange={handleVisionProviderChange}
              onVisionApiKeyChange={setVisionApiKey}
              onVisionBaseUrlChange={setVisionBaseUrl}
              onVisionModelChange={setVisionModel}
              onVisionEnabledChange={setVisionEnabled}
              onSaveVisionProfile={saveVisionProfile}
              onClearVisionProfile={() => void clearVisionProfile()}
              onSetLogLevel={(level) => void setLogLevel(level)}
              onOpenLogsFolder={() => void openLogsFolder()}
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
              onCopyConnectorText={copyConnectorText}
              onAgentNotesChange={updateAgentNotes}
              onSaveNotes={saveNotes}
            />
          ) : (
            <ChatWorkspace
              projectPromptTitle={projectPromptTitle}
              conversationPending={activeChatPending}
              conversationPendingMessage={chatStorageReady ? t("chat.sessionRestoreFailed") : t("common.loading")}
              input={input}
              setInput={setInput}
              sending={sending}
              queueAllowed={(chatRunSending || pendingQuestionSessionIds.has(activeChat?.sessionId || "")) && !stopRequested}
              permission={permission}
              onSubmit={submitMessage}
              onQueue={() => void submitMessage(undefined, "queue")}
              onSteerNow={() => void submitMessage(undefined, "steer")}
              onStop={stopInteractiveActivity}
              onResumeQueue={() => void resumeQueuedTurns(true)}
              onCancelQueue={() => void cancelQueuedTurns()}
              onEditQueuedTurn={async (turnId) => {
                const turn = await editQueuedTurn(turnId);
                if (!turn) return;
                setInput(current => current ? `${current}\n\n${turn.text}` : turn.text);
                setAttachments(current => [...current, ...turn.attachments.filter(item => !current.some(existing => existing.id === item.id))]);
              }}
              onRemoveQueuedTurn={(turnId) => void removeQueuedTurn(turnId)}
              onSteerQueuedTurn={(turnId) => void steerQueuedTurn(turnId)}
              onSwitchMode={switchMode}
              commands={slashCommands}
              planMode={activeChat?.planMode === true}
              onPlanModeChange={(planMode) => {
                const chatId = ensureActiveChat();
                updateChat(chatId, (chat) => ({ ...chat, planMode }));
              }}
              actions={composerActions}
              onAction={runExplicitWorkspaceAction}
              disabledReason={activeChatPending || attachmentsLoading ? t("common.loading") : pendingReplyQuestionId ? "" : chatDisabledReason}
              attachments={attachments}
              onAttachFiles={(files) => void addComposerFiles(files)}
              onRemoveAttachment={removeAttachment}
              developerOptionsEnabled={developerOptionsEnabled}
              contextUsage={contextUsage}
              compaction={activeChat?.compaction}
              onCancelCompaction={activeChatId ? () => cancelCompaction(activeChatId) : undefined}
              providerLabel={providerSnapshot.providerLabel}
              model={providerSnapshot.model}
              providerReasoningEndpoint={providerSnapshot.provider === "chatgpt" ? endpoint : undefined}
              providerReasoningLevel={apiConfig?.thinking_level || ""}
              onProviderReasoningActivated={() => refresh(endpoint)}
              activeGoal={activeAgentGoal}
              onGoalChanged={upsertAgentGoal}
              goalEndpoint={endpoint}
              resourceImageEndpoint={endpoint}
              resourceChatId={activeChatId || undefined}
              runtimeSessionId={sessionId || undefined}
              resourceProjectPath={activeChat?.projectPath || activeProjectPath || undefined}
              projects={projectItems.map((project) => ({
                key: projectKey(project),
                name: project.name || shortPath(project.path || ""),
              }))}
              onBindProject={bindProject}
              conversation={conversation}
              attachmentPayloads={displayChat?.attachmentPayloads}
              queued={visibleQueued}
              agentQuestions={hasAgentRuntimeScope ? filterQuestionsForRuntimeScope(agentQuestions, runtimeScope) : []}
              backgroundGoalDeliveries={
                activeChatId
                  ? (backgroundGoalState?.recent || []).filter((delivery) => delivery.chatId === activeChatId)
                  : []
              }
              backgroundGoalProviderWarnings={backgroundGoalState?.providerWarnings || []}
              onBackgroundGoalCatchUpRendered={onBackgroundGoalCatchUpRendered}
              onBackgroundGoalProviderWarningsRendered={onBackgroundGoalProviderWarningsRendered}
              onBackgroundGoalCatchUpDismiss={dismissBackgroundGoalCatchUp}
              onAnswerQuestion={answerRuntimeQuestion}
              onReplyInChat={replyToAgentQuestionInComposer}
              focusInput={Boolean(pendingReplyQuestionId)}
              pendingReplyQuestionId={pendingReplyQuestionId}
              onCancelReply={() => {
                setPendingReplyQuestionId("");
              }}
              onStopQuestionContinuation={stopRuntimeQuestionContinuation}
              conversationEndRef={conversationEndRef}
              onConversationMouseUp={handleConversationMouseUp}
              onConversationScroll={(scrollElement) => {
                conversationScroll.onScroll(scrollElement);
                if (selectionMenu) {
                  setSelectionMenu(null);
                }
              }}
              showScrollToBottom={!conversationScroll.pinned}
              onScrollToBottom={conversationScroll.scrollToBottom}
              pendingApprovalForResponse={pendingApprovalForResponse}
              scopedPendingApprovals={pendingApprovalItems}
              approvalActions={approvalActions}
              editingItemId={editingMessage?.chatId === activeChatId ? editingMessage.itemId : ""}
              editingText={editingMessage?.draftText || ""}
              editSaveDisabledReason={editingMessage && (savingMessageEdit || sending || compacting || visibleQueued.length > 0 || hasUnresolvedRuntimeState(editingMessage.chatId, getChatById(editingMessage.chatId)?.sessionId || "")) ? t("chat.cannotActionWhileRunning") : ""}
              editingAttachments={editingMessage?.draftAttachments || []}
              onEditItemChangeText={updateEditingMessageText}
              onEditItemRemoveAttachment={removeEditingMessageAttachment}
              latestRetryableItemId={latestRetryableItemId}
              latestEditableUserItemId={latestEditableUserItemId}
              onCopyItem={copyConversationItem}
              onRetryItem={retryConversationItem}
              onEditItem={editConversationMessage}
              onEditItemSave={saveMessageEdit}
              onEditItemCancel={cancelMessageEdit}
              onApprove={approveShell}
              onReject={rejectShell}
              onModifyApproval={modifyApprovalInComposer}
              onImportAttachment={(attachment) => void importVaultAttachment(attachment)}
              onOpenDoctor={() => void openDoctor()}
              runtimeRuns={runtimeRuns}
              onSaveOperationAsSkill={(summary) => void openSkillsWithCapturedPath(summary)}
              onAcceptHandoff={acceptSessionHandoff}
              onDismissHandoff={dismissSessionHandoff}
              onPauseHandoff={pauseSessionHandoff}
              onResumeHandoff={resumeSessionHandoff}
              onReplyHandoff={replySessionHandoff}
              handoffBusyId={sessionHandoff.busyId}
              sessionHandoffEndpoint={endpoint}
              sessionHandoffSourceChatId={activeChatId}
              sessionHandoffTargetChats={handoffTargetChats}
              handoffSendOpen={handoffSendOpen}
              onHandoffSendOpenChange={setHandoffSendOpen}
            />
          )}
          {activeView !== "chat" || pendingApprovalPanelOpen ? (
            <PendingApprovalsStrip
              approvals={projectPendingApprovalItems}
              actions={approvalActions}
              loading={loading}
              onApprove={approveShell}
              onReject={rejectShell}
              onClose={activeView === "chat" ? () => setPendingApprovalPanelOpen(false) : undefined}
            />
          ) : null}

        </section>
        {rightSidebarVisible ? (
          <>
            <LayoutSplitter
              side="right"
              value={effectiveRightPaneWidth}
              min={0}
              max={rightPaneMaximum}
              title={t("workspace.resizeRightPane")}
              onPointerDown={(event) => startLayoutResize("right", event)}
            />
            {rightSidebarCollapsed ? (
          <aside className="flex h-screen items-start justify-center border-l border-border/80 bg-sidebar pt-2">
            <button
              type="button"
              className="flex h-8 w-8 items-center justify-center rounded-md text-muted-foreground transition-colors hover:bg-muted hover:text-foreground"
              onClick={() => setRightSidebarCollapsed(false)}
              title={t("workspace.showSidebar")}
              aria-label={t("workspace.showSidebar")}
              data-vrcforge-right-sidebar-restore
            >
              <PanelRightOpen className="h-4 w-4" />
            </button>
          </aside>
            ) : (
          <Suspense fallback={<SidebarPlaceholder side="right" />}>
            <SidebarMountTracker side="right" onMounted={markRightSidebarMounted}>
              {subAgentSidebarActive ? subAgentWorkspaceSurface : <AsyncRightRuntimeSidebar
              runtimeConnected={runtimeConnected}
              loadingUnityStatus={loadingUnityStatus}
              workspaceProjectLabel={workspaceProjectLabel}
              workspaceProjectType={workspaceProjectType}
              selectedProjectComponent={selectedProjectComponent}
              backendComponent={backendComponent}
              mcpPackageComponent={mcpPackageComponent}
              unityBridgeComponent={unityBridgeComponent}
              unityInstanceComponent={unityInstanceComponent}
              unityToolsComponent={unityToolsComponent}
              externalAgentComponent={externalAgentComponent}
              authorizationStatus={authorizationStatus}
              agentProgress={agentProgress}
              projectWorkspace={projectChatWorkspace}
              subAgentPanel={activeView === "chat" ? subAgentActivityPanel : undefined}
              subAgentTaskCount={activeSubAgentThreads.length}
              subAgentRunningTaskCount={runningSubAgentTaskCount}
              subAgentCompletedTaskCount={completedSubAgentTaskCount}
              userAttachmentSources={userAttachmentSources}
              onLocateUserAttachmentSource={locateUserAttachmentSource}
              onOpenUserAttachmentSource={openUserAttachmentSource}
              approvalsLoaded={agentApprovals !== null}
              pendingApprovals={pendingApprovals}
              runtimeReviewCount={runtimeAttention.length}
              workspaceSummary={workspaceDiff}
              activeDesktopActions={activeDesktopActions}
              refreshUnityStatus={refreshUnityStatus}
              onTestAgentConnection={() => void runProviderTest("text")}
              providerTestAttempted={Boolean(providerTestMessage.trim())}
              onHideSidebar={() => setRightSidebarCollapsed(true)}
              onOpenPendingApprovals={() => {
                setPendingApprovalPanelOpen(true);
              }}
              localizeHealthMessage={localizeHealthMessage}
              />}
            </SidebarMountTracker>
          </Suspense>
            )}
          </>
        ) : null}
      </div>

      <ComputerUseActivitySurface
        actions={activeDesktopActions}
        cancellingActionIds={cancellingDesktopActionIds}
        theme={theme}
        onCancel={stopInteractiveActivity}
      />

      <OnboardingLanguageGate
        open={showOnboarding && showOnboardingLanguageGate}
        currentLanguage={i18n.language}
        onContinue={(locale) => void completeOnboardingLanguageGate(locale)}
      />

      <OnboardingOverlay
        open={showOnboarding && !showOnboardingLanguageGate}
        minimized={onboardingMinimized}
        stepIndex={onboardingStep}
        runtimeConnected={runtimeConnected}
        selectedProjectReady={onboardingSelectedProjectReady}
        projectType={activeProjectType}
        unityToolsReady={onboardingUnityToolsReady}
        unityToolsCount={vrcForgeToolsCount}
        providerVerified={providerReadyForOnboarding}
        providerConfigured={providerConfigured}
        externalAgentReady={externalAgentVerified}
        loadingRuntime={loading}
        currentLanguage={i18n.language}
        onRetryRuntime={() => void startRuntime()}
        onOpenSettings={() => {
          setOnboardingMinimized(true);
          setProviderSetupGuide(true);
          setExternalSetupGuide(false);
          openSettingsSection("models");
        }}
        onOpenExternalSettings={() => {
          setOnboardingMinimized(true);
          setExternalSetupGuide(true);
          setProviderSetupGuide(false);
          openSettingsSection("connectors");
        }}
        onOpenProjectPicker={() => {
          setOnboardingMinimized(true);
          setProjectModalError("");
          setShowProjectModal(true);
        }}
        onResume={returnFromOnboardingSettings}
        onFinish={finishOnboarding}
        onPreviousStep={() => setOnboardingStep((value) => Math.max(0, value - 1))}
        onNextStep={() => setOnboardingStep((value) => value + 1)}
        onLocaleChange={(locale) => void setLocale(locale)}
      />

      <ExternalSetupTour
        open={showOnboarding && onboardingMinimized && externalSetupGuide && activeView === "settings" && activeSettingsSection === "connectors"}
        gatewayEnabled={Boolean(connectorStatus?.gateway?.enabled)}
        onReturn={returnFromOnboardingSettings}
      />

      <ProviderSetupTour
        open={showOnboarding && onboardingMinimized && providerSetupGuide && activeView === "settings" && activeSettingsSection === "models"}
        onReturn={returnFromOnboardingSettings}
      />

      <ProjectPickerModal
        open={showProjectModal}
        projects={projectItems}
        hiddenProjects={hiddenProjects}
        customPathSet={customPathSet}
        saving={savingProjectPrefs}
        newProjectPath={newProjectPath}
        projectType={newProjectType}
        error={projectModalError}
        onClose={() => {
          setShowProjectModal(false);
          setProjectModalError("");
          if (showOnboarding) setOnboardingMinimized(false);
        }}
        onSelectProject={(key) => {
          selectProjectByPath(key);
          setShowProjectModal(false);
          setProjectModalError("");
          if (showOnboarding) setOnboardingMinimized(false);
        }}
        onRemoveCustomProject={removeCustomProject}
        onRestoreProject={unhideProject}
        onNewProjectPathChange={setNewProjectPath}
        onProjectTypeChange={setNewProjectType}
        onClearError={() => setProjectModalError("")}
        onAddProjectPath={() => void addProjectPath()}
        onRefreshProjects={() => void refreshManagedProjectList()}
        loadingProjects={loadingProjects || projectScanState.scanning}
        discoveryPending={projectScanState.notChecked}
        projectListError={projectListError || projectScanState.error || (projectScanState.sourceErrors.length ? t("project.sourceScanFailed", { sources: projectScanState.sourceErrors.join(", ") }) : "")}
        editingProjectPath={editingProjectPath}
        editProjectPathDraft={editProjectPathDraft}
        onEditProjectPathChange={setEditProjectPathDraft}
        onStartEditProject={startEditProject}
        onCancelEditProject={cancelEditProject}
        onSaveEditProject={() => void saveEditProject()}
      />

      <SidebarMenus
        projectMenu={projectMenu}
        chatMenu={chatMenu}
        selectionMenu={activeView === "chat" && !selectedSubAgentPanelOpen && !showProjectModal ? selectionMenu : null}
        deleteTargetId={deleteTargetId}
        chats={chats}
        customPathSet={customPathSet}
        collapsedProjects={collapsedProjects}
        pinnedProjectSet={pinnedProjectSet}
        selectionMenuRef={selectionMenuRef}
        onCloseProjectMenu={() => setProjectMenu(null)}
        onTogglePinProject={togglePinProject}
        onOpenProjectFolder={(projectPath) => void openProjectFolder(projectPath)}
        onNewConversation={newConversationForProject}
        onStartRenameProject={startRenameProject}
        onToggleProjectCollapse={toggleProjectCollapse}
        onArchiveProjectChats={archiveProjectChats}
        onHideProject={hideProject}
        onRemoveCustomProject={removeCustomProject}
        onAskInNewSession={askInNewSession}
        onAddSelectionToComposer={addSelectionToComposer}
        onCloseChatMenu={() => setChatMenu(null)}
        onTogglePinChat={togglePinChat}
        onToggleTestChat={toggleTestChat}
        onStartRenameChat={startRenameChat}
        onDeleteChat={setDeleteTargetId}
        onCancelDeleteChat={() => setDeleteTargetId("")}
        onConfirmDeleteChat={deleteChatPermanently}
      />

      <TextEditContextMenu />

      <CheckpointQuotaNotice
        endpoint={endpoint}
        connected={runtimeConnected}
        refreshTrigger={connectorStatus?.gateway?.checkpointArchiveUsage}
        onManage={() => openSettingsSection("storage")}
      />

      {transientFailure ? (
        <TransientFailureToast
          title={
            transientFailure.tone === "success" && transientFailure.kind === "copy"
              ? t("notifications.copySuccess")
              : t(`notifications.${transientFailure.kind}Failed`)
          }
          tone={transientFailure.tone}
          message={transientFailure.message}
          dismissLabel={t("notifications.dismiss")}
          onDismiss={dismissTransientFailure}
        />
      ) : null}

      <AppUpdatePopup
        prompt={appUpdatePrompt}
        automaticCheckEnabled={automaticUpdateCheckEnabled}
        onAutomaticCheckEnabledChange={(enabled) => {
          setAutomaticUpdateCheckEnabled(enabled);
          persistAutomaticUpdateCheckEnabled(enabled);
        }}
        onDismiss={() => setAppUpdatePrompt(null)}
      />

      <VskOpenDialog ready={runtimeConnected} onPreflight={preflightVskPackage} onImport={importVskPackage} />

    </main>
  );
}
