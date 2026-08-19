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
import { SidebarMenus } from "./components/sidebar/sidebar-menus";
import { TransientFailureToast } from "./components/ui/transient-failure-toast";
import { OnboardingOverlay } from "./components/onboarding/onboarding-overlay";
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
import { SubAgentPanel } from "./components/subagents/sub-agent-panel";
import { type UserAttachmentSource } from "./components/runtime/project-workbench-sections";
import { useApprovalExecution } from "./hooks/use-approval-execution";
import { useAppUpdate } from "./hooks/use-app-update";
import { AppUpdatePopup } from "./components/ui/app-update-popup";
import type { AppUpdatePromptState, AppUpdateResult } from "./lib/api/app-update";
import { useCheckpointWorkspaceController } from "./hooks/use-checkpoint-workspace-controller";
import { useChatRunController, type QueuedTurn } from "./hooks/use-chat-run-controller";
import { useChatSessions } from "./hooks/use-chat-sessions";
import { useContextCompactionController } from "./hooks/use-context-compaction-controller";
import { useDoctorFixController } from "./hooks/use-doctor-fix-controller";
import { useBackgroundGoalRuns } from "./hooks/use-background-goal-runs";
import { parseGoalWakeDirective } from "./hooks/use-goal-wake";
import { useProjectManagement } from "./hooks/use-project-management";
import { useOptimizationWorkspaceController } from "./hooks/use-optimization-workspace-controller";
import { useProtectionWorkspaceController } from "./hooks/use-protection-workspace-controller";
import { useProviderSettings } from "./hooks/use-provider-settings";
import { useRuntimeWorkspace } from "./hooks/use-runtime-workspace";
import { useRuntimeTurnContinuationDelivery } from "./hooks/use-runtime-turn-continuation";
import { useSettingsWorkspaceController } from "./hooks/use-settings-workspace-controller";
import { useSessionHandoff } from "./hooks/use-session-handoff";
import { useSkillsWorkspaceController } from "./hooks/use-skills-workspace-controller";
import { useTransientFailureNotice } from "./hooks/use-transient-failure-notice";
import { useThemeCustomization } from "./hooks/use-theme-customization";
import { TEMP_CHATS_COLLAPSE_KEY, type ActiveView, type SettingsSection } from "./lib/app-view";
import { presentApproval } from "./lib/approval-presentation";
import { replyToSessionHandoff } from "./lib/api/session-handoff";
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
import { resolveContextLimit } from "./lib/context-compaction";
import { thinkingTraceLabel } from "./lib/provider-ui";
import type { ChatAttachment, ComposerAction, ComposerActionId, ContextUsage, ConversationItem } from "./lib/chat-types";
import { executionModeLabel, permissionVisualState } from "./lib/permission-ui";
import { resolveComputerUseAccentHex } from "./lib/computer-use-visuals";
import { normalizeProjectPathKey, projectKey, shortPath } from "./lib/project-path";
import { sortSidebarProjects } from "./lib/sidebar-project-order";
import { asRecord, getHealthDetailNumber } from "./lib/runtime-parsing";
import { buildEmptyProjectState } from "./lib/sidebar-view";
import { localizeRuntimeHealthMessage } from "./lib/runtime-workspace-view";
import {
  parseApprovalNotificationAction,
  parseSubAgentReviewNotificationAction,
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
import { pickSubAgentName, reconcileSelectedSubAgent, updateSubAgentList } from "./lib/subagent-state";
import {
  AgentApproval,
  AgentRuntimeResponse,
  AgentReasoningTrace,
  SubAgentTask,
  SubAgentTaskList,
  ApiError,
  AppBootstrap,
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
  fetchAgentRuns,
  fetchAppSession,
  fetchAppHealth,
  fetchSubAgent,
  fetchSubAgents,
  mergeSubAgent,
  requestAgentDesktopAction,
  requestChatAttachmentImport,
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
const AsyncAppSidebar = lazy(() =>
  import("./components/sidebar/app-sidebar").then((module) => ({ default: module.AppSidebar })),
);
const AsyncRightRuntimeSidebar = lazy(() =>
  import("./components/runtime/runtime-sidebar").then((module) => ({ default: module.RightRuntimeSidebar })),
);
const AsyncSubAgentWorkspaceSurface = lazy(() =>
  import("./components/subagents/sub-agent-workspace-surface").then((module) => ({
    default: module.SubAgentWorkspaceSurface,
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
  const { themeCustomization, updateThemeCustomization, resetThemeCustomization } = useThemeCustomization();
  const [input, setInput] = useState("");
  const [activeProjectPath, setActiveProjectPath] = useState("");
  const [activeProjectType, setActiveProjectType] = useState<ProjectType>("general");
  const [activeView, setActiveView] = useState<ActiveView>("chat");
  const [activeSettingsSection, setActiveSettingsSection] = useState<SettingsSection>("general");
  const [developerOptionsEnabled, setDeveloperOptionsEnabled] = useState(() => loadDeveloperOptionsEnabled());
  const [developerOptionsEverEnabled, setDeveloperOptionsEverEnabled] = useState(false);
  const [computerUseEnabled, setComputerUseEnabled] = useState(false);
  const [computerUseEverEnabled, setComputerUseEverEnabled] = useState(false);
  const [backgroundGoalNotificationsEnabled, setBackgroundGoalNotificationsEnabled] = useState(true);
  const [backgroundGoalRefreshSignal, setBackgroundGoalRefreshSignal] = useState(0);
  const [memoryReviewRefreshSignal, setMemoryReviewRefreshSignal] = useState(0);
  const [savingAdvancedSettings, setSavingAdvancedSettings] = useState(false);
  const [handoffSendOpen, setHandoffSendOpen] = useState(false);
  const [appUpdatePrompt, setAppUpdatePrompt] = useState<AppUpdatePromptState | null>(null);
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
  const [subAgentActionBusyTaskIds, setSubAgentActionBusyTaskIds] = useState<Set<string>>(() => new Set());
  const [attachments, setAttachments] = useState<ChatAttachment[]>([]);
  const [editingMessage, setEditingMessage] = useState<EditingMessageDraft | null>(null);
  const [selectionMenu, setSelectionMenu] = useState<{ x: number; y: number; text: string } | null>(null);
  const [doctorReport, setDoctorReport] = useState<DoctorReport | null>(null);
  const [loadingDoctor, setLoadingDoctor] = useState(false);
  const [doctorMessage, setDoctorMessage] = useState("");
  const [doctorMessageTone, setDoctorMessageTone] = useState<"ok" | "warn" | "danger">("ok");
  const [startupIssue, setStartupIssue] = useState("");
  const [dismissedDoctorPromptSignature, setDismissedDoctorPromptSignature] = useState("");
  const conversationEndRef = useRef<HTMLDivElement | null>(null);
  const conversationPinnedRef = useRef(true);
  const [pinnedToConversationBottom, setPinnedToConversationBottom] = useState(true);
  const updateConversationPinned = useCallback((pinned: boolean) => {
    conversationPinnedRef.current = pinned;
    setPinnedToConversationBottom(pinned);
  }, []);
  const projectInitRef = useRef(false);
  const refreshRuntimeRunsRef = useRef<(includeEvents?: boolean, target?: string) => Promise<void>>(async () => undefined);
  const pendingApprovalsRef = useRef<AgentApproval[]>([]);
  const knownApprovalNotificationIdsRef = useRef<Set<string>>(new Set());
  const exhaustedApprovalNotificationIdsRef = useRef<Set<string>>(new Set());
  const knownSubAgentReviewNotificationIdsRef = useRef<Set<string>>(new Set());
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
  const bootstrapRequestSequenceRef = useRef(0);
  const bootstrapForegroundRequestRef = useRef(0);
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
    runtimeConnected,
    automaticUpdateCheckEnabled,
    showStartupUpdate,
  );
  const authoritativeSelectedProjectPath = (
    bootstrap?.health.state?.selectedProjectPath
    || bootstrap?.health.projects?.selectedProjectPath
    || ""
  ).trim();
  useDashboardProjectSelection({
    endpoint,
    runtimeConnected,
    projectPath: activeProjectPath,
    projectType: activeProjectType,
    confirmedProjectPath: authoritativeSelectedProjectPath,
    setBootstrap,
    setError,
  });
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
    handleDeepSeekAutoNegotiationChange,
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
  const vrcForgeToolsReady = runtimeConnected && healthComponents.vrcForgeUnityTools?.status === "ok" && vrcForgeToolsCount === 64;
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
  const projects = bootstrap?.health.projects?.projects ?? [];
  const onboardingSelectedProjectReady = Boolean(
    activeProjectPath
    && projects.some(
      (project) => normalizeProjectPathKey(projectKey(project)) === normalizeProjectPathKey(activeProjectPath),
    )
  );
  const onboardingProjectMatchesBackend = !authoritativeSelectedProjectPath || (
    normalizeProjectPathKey(authoritativeSelectedProjectPath) === normalizeProjectPathKey(activeProjectPath)
  );
  const onboardingUnityToolsReady = onboardingSelectedProjectReady && onboardingProjectMatchesBackend && vrcForgeToolsReady;
  const externalAgentConnected = Boolean(connectorStatus?.gateway?.enabled);
  const chatAvailable = providerConfigured || externalAgentConnected;
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
    projectItems,
    hiddenProjects,
    addProjectPath,
    removeCustomProject,
    hideProject,
    unhideProject,
    projectDisplayName,
    togglePinProject,
    startRenameProject,
    commitRenameProject,
    openProjectFolder,
    scanActiveProjectIndex,
    planActiveOutfitImport,
    requestActiveOutfitImport,
    toggleProjectCollapse,
    expandProjectGroup,
  } = useProjectManagement({
    endpoint,
    runtimeConnected,
    activeProjectPath,
    activeProjectType,
    projects,
    refresh,
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
    activeChat,
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
    touchChat,
    updateChat,
    updateChatIfRevision,
    appendToChat,
    ensureActiveChat,
    persistChatsNow,
    getChatById,
    newConversation,
    togglePinChat,
    startRenameChat,
    commitRenameChat,
    deleteChatPermanently,
    bindProject,
    newTemporaryChat,
    archiveProjectChats,
    openChat,
    selectProject,
  } = useChatSessions({
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
    () => sortSidebarProjects(projectItems, chats, pinnedProjectSet),
    [chats, pinnedProjectSet, projectItems],
  );
  chatSessionActionsRef.current = { selectProject, newConversation };
  const projectTypeForPath = (projectPath: string): ProjectType => {
    const normalized = normalizeProjectPathKey(projectPath);
    const project = projectItems.find((candidate) => normalizeProjectPathKey(projectKey(candidate)) === normalized);
    return project?.projectType === "general" ? "general" : "unity";
  };
  const selectProjectByPath = (projectPath: string) => selectProject(projectPath, projectTypeForPath(projectPath));
  const newConversationForProject = (projectPath?: string) => newConversation(
    projectPath,
    projectPath ? projectTypeForPath(projectPath) : "general",
  );
  const deliverRuntimeTurnContinuation = useRuntimeTurnContinuationDelivery({
    chats,
    appendToChat,
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

  const conversation = activeChat?.items ?? [];
  const sessionId = activeChat?.sessionId ?? "";
  const activeRuntimeProjectPath = activeChat?.projectPath || activeProjectPath;
  const hasAgentRuntimeScope = Boolean(sessionId || activeRuntimeProjectPath);
  const latestEditableUserItemId = latestConversationItemId(conversation, (item) => item.type === "user");
  const latestRetryableItemId = latestConversationItemId(conversation, isRetryableConversationItem);
  const pendingApprovalItems = (agentApprovals ?? []).filter(
    (item) => item.status === "pending",
  );
  const pendingApprovals = pendingApprovalItems.length;
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
  });
  const {
    sending: chatRunSending,
    queued,
    currentTurn,
    stopRequested,
    isRunning: isChatRunActive,
    submitTurn,
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
    chats,
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
    desktopActions,
    activeDesktopActions,
    desktopBridge,
    cancellingDesktopActionIds,
    agentGoals,
    agentProgress,
    agentQuestions,
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
  });
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
        const completedAt = delivery.completedAt || delivery.updatedAt || new Date().toISOString();
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
              createdAt: delivery.createdAt || completedAt,
            },
            {
              id: delivery.agentItemId,
              type: "agent",
              response,
              elapsedSeconds: 1,
              providerLabel: delivery.providerLabel || providerSnapshot.providerLabel,
              model: delivery.model || providerSnapshot.model,
              createdAt: completedAt,
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
    pendingApprovalItems,
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
  pendingApprovalsRef.current = pendingApprovalItems;

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
    const reviewTasks = (subAgentList?.tasks || []).filter((task) => {
      return (
        isAwaitingMergeReview(task)
        && typeof task.revision === "number"
        && Number.isInteger(task.revision)
        && task.revision > 0
        && Boolean(task.parentChatId)
        && getChatById(task.parentChatId || "")
      );
    });
    const pendingReviewNotificationIds = new Set(reviewTasks.map((task) => toReviewNotificationId(task)));
    for (const notificationId of knownSubAgentReviewNotificationIdsRef.current) {
      if (!pendingReviewNotificationIds.has(notificationId)) {
        knownSubAgentReviewNotificationIdsRef.current.delete(notificationId);
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

    for (const task of reviewTasks) {
      const reviewNotificationId = toReviewNotificationId(task);
      if (
        knownSubAgentReviewNotificationIdsRef.current.has(reviewNotificationId)
        || exhaustedSubAgentReviewNotificationIdsRef.current.has(reviewNotificationId)
      ) {
        continue;
      }
      knownSubAgentReviewNotificationIdsRef.current.add(reviewNotificationId);
      const notify = () =>
        showSubAgentReviewNotification({
          taskId: task.id,
          revision: Number(task.revision),
          parentChatId: task.parentChatId || "",
          title: `${t("subagent.review")} · ${subAgentRoleLabel(task.role)}`,
          body: `${task.displayName || t("subagent.taskLabel")} · ${t("subagent.awaitingReview")} (${task.revision})`,
          openLabel: t("subagent.inspect"),
        });
      void notify().catch(() =>
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
              return;
            }
            return notify();
          })
          .catch((cause) => {
            knownSubAgentReviewNotificationIdsRef.current.delete(reviewNotificationId);
            exhaustedSubAgentReviewNotificationIdsRef.current.add(reviewNotificationId);
            setError(cause instanceof Error ? cause.message : String(cause));
          }),
      );
    }
  }, [getChatById, runtimeConnected, subAgentList?.tasks, t, toReviewNotificationId]);

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
    knownSubAgentReviewNotificationIdsRef.current.clear();
    handledSubAgentReviewNotificationIdsRef.current.clear();
    exhaustedSubAgentReviewNotificationIdsRef.current.clear();
  }, [runtimeConnected]);

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
      activeChat?.contextUsageCache,
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
  }, [activeChat?.contextUsageCache, apiConfig, currentModelInfo, latestContextUsage, providerSnapshot.model, providerSnapshot.provider, smokeMode, t]);
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
  const runningSubAgentTaskCount = useMemo(
    () => activeSubAgentTasks.filter((task) => ["queued", "running", "cancelling"].includes(task.status)).length,
    [activeSubAgentTasks],
  );
  const completedSubAgentTaskCount = useMemo(
    () => activeSubAgentTasks.filter((task) => task.status === "completed").length,
    [activeSubAgentTasks],
  );
  useEffect(() => {
    subAgentSelectionIntentRef.current = selectedSubAgent?.id || "";
  }, [selectedSubAgent?.id]);
  const hasRunningSubAgents = subAgentTasks.some((task) => ["queued", "running", "cancelling"].includes(task.status));
  const activeProjectName =
    projectDisplayName(projectItems.find((project) => normalizeProjectPathKey(projectKey(project)) === normalizeProjectPathKey(activeProjectPath))) ||
    (activeProjectPath ? shortPath(activeProjectPath) : "");
  const effectiveLeftPaneWidth = layoutPaneWidths.left;
  const effectiveRightPaneWidth = rightSidebarCollapsed ? 44 : layoutPaneWidths.right;
  const workspaceGridColumns = `${effectiveLeftPaneWidth}px ${RESIZE_HANDLE_WIDTH}px minmax(0,1fr) ${RESIZE_HANDLE_WIDTH}px ${effectiveRightPaneWidth}px`;
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
      const available = window.innerWidth - RESIZE_HANDLE_WIDTH * 2 - MIN_CENTER_PANE_WIDTH - effectiveRightPaneWidth;
      return Math.max(MIN_LEFT_PANE_WIDTH, Math.min(MAX_LEFT_PANE_WIDTH, available));
    };
    const maxRightWidth = () => {
      const available = window.innerWidth - RESIZE_HANDLE_WIDTH * 2 - MIN_CENTER_PANE_WIDTH - layoutPaneWidths.left;
      return Math.max(MIN_RIGHT_PANE_WIDTH, Math.min(MAX_RIGHT_PANE_WIDTH, available));
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
  const localizeHealthMessage = useCallback(
    (message?: string) => localizeRuntimeHealthMessage(t, message),
    [t],
  );
  const authoritativeProjectName = projectDisplayName(
    projectItems.find(
      (project) => normalizeProjectPathKey(projectKey(project)) === normalizeProjectPathKey(authoritativeSelectedProjectPath),
    ),
  );
  const workspaceProjectLabel = workspaceProjectPath
    ? projectDisplayName(projectItems.find(
        (project) => normalizeProjectPathKey(projectKey(project)) === normalizeProjectPathKey(workspaceProjectPath),
      )) || shortPath(workspaceProjectPath)
    : t("workspace.noProjectSelected");
  const answerRuntimeQuestion = async (questionId: string, optionId: string, value: string) => {
    setActiveView("chat");
    try {
      await answerAgentQuestion(endpoint, questionId, {
        answer: value,
        selectedOptionId: optionId,
        sessionId,
        projectRoot: activeRuntimeProjectPath || undefined,
      });
      void refreshRuntimeRuns(false);
    } catch (cause) {
      setError(cause instanceof Error ? cause.message : String(cause));
    }
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
    if (!sidebarsVisible || !leftSidebarMounted || (!rightSidebarCollapsed && !rightSidebarMounted)) {
      return;
    }
    const metrics = ((window as any).__vrcforgeStartupMetrics ||= {});
    metrics.sidebarsMountedMs ??= Math.round(performance.now());
    document.documentElement.dataset.vrcforgeSidebars = "mounted";
  }, [leftSidebarMounted, rightSidebarCollapsed, rightSidebarMounted, sidebarsVisible]);

  useEffect(() => {
    if (
      !bootstrap
      || !projectPrefsReady
      || !sidebarsVisible
      || !leftSidebarMounted
      || (!rightSidebarCollapsed && !rightSidebarMounted)
    ) {
      return;
    }
    const metrics = ((window as any).__vrcforgeStartupMetrics ||= {});
    metrics.sidebarsHydratedMs ??= Math.round(performance.now());
    document.documentElement.dataset.vrcforgeSidebars = "ready";
  }, [bootstrap, leftSidebarMounted, projectPrefsReady, rightSidebarCollapsed, rightSidebarMounted, sidebarsVisible]);

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
    if (!showOnboarding || !onboardingMinimized) {
      return;
    }
    const stepStates = activeProjectType === "unity"
      ? [onboardingSelectedProjectReady, onboardingUnityToolsReady, Boolean(apiConfig?.apiKeyPresent)]
      : [onboardingSelectedProjectReady, Boolean(apiConfig?.apiKeyPresent)];
    if (stepStates[Math.min(onboardingStep, stepStates.length - 1)]) {
      setOnboardingMinimized(false);
    }
  }, [showOnboarding, onboardingMinimized, onboardingStep, onboardingSelectedProjectReady, onboardingUnityToolsReady, activeProjectType, apiConfig?.apiKeyPresent]);

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
    updateConversationPinned(true);
  }, [activeChatId, updateConversationPinned]);

  useEffect(() => {
    if (!conversationPinnedRef.current) {
      return;
    }
    conversationEndRef.current?.scrollIntoView({ behavior: "auto", block: "end" });
  }, [activeChatId, conversation.length]);

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
    const bootstrapEvents = new Set(["advancedSettings", "agentApprovals", "agentPermission", "hello", "projects", "unity_status"]);
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
      if (eventType === "agentRuntimeTurn") {
        deliverRuntimeTurnContinuation(event.payload?.payload);
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
  }, [runtimeConnected, endpoint, sessionId, activeRuntimeProjectPath, activeProjectPath, workspaceDiffReviewOpen, deliverRuntimeTurnContinuation]);

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
    let unlistenTrayCheckUpdate: (() => void) | undefined;
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
          setAppUpdatePrompt({ source: "tray", result });
        } catch {
          setAppUpdatePrompt({ source: "tray", result: null });
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
    return () => {
      active = false;
      unlistenTrayOpenChat?.();
      unlistenTrayCheckUpdate?.();
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

  async function refreshStartupWithMetrics(target: string, options: { refreshProjects?: boolean } = {}) {
    const startedAt = performance.now();
    try {
      await refreshWithRetry(target);
    } finally {
      const metrics = ((window as any).__vrcforgeStartupMetrics ||= {});
      metrics.bootstrapRefreshMs ??= Math.round(performance.now() - startedAt);
    }
    if (options.refreshProjects) {
      void refreshProjectList(target, { allowDuringStartup: true });
    }
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
    bootstrapRequestSequenceRef.current += 1;
    bootstrapForegroundRequestRef.current = 0;
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
    try {
      const payload = await fetchBootstrap(target, { ...options, deferAgentCatalog: true });
      if (sequence !== bootstrapRequestSequenceRef.current) {
        return;
      }
      setBootstrap(payload);
      setStartupIssue("");
    } catch (cause) {
      if (sequence !== bootstrapRequestSequenceRef.current) {
        return;
      }
      throw cause;
    } finally {
      if (bootstrapForegroundRequestRef.current === sequence) {
        bootstrapForegroundRequestRef.current = 0;
      }
    }
  }

  async function refreshSilently(target = endpoint) {
    if (bootstrapForegroundRequestRef.current !== 0) {
      return;
    }
    const sequence = ++bootstrapRequestSequenceRef.current;
    try {
      const payload = await fetchBootstrap(target, { deferAgentCatalog: true });
      if (sequence !== bootstrapRequestSequenceRef.current) {
        return;
      }
      setBootstrap(payload);
      setStartupIssue("");
      setError((current) => (current.toLowerCase().includes("fetch") ? "" : current));
    } catch (cause) {
      if (sequence !== bootstrapRequestSequenceRef.current) {
        return;
      }
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

  async function refreshProjectList(
    target = endpoint,
    options: { allowDuringStartup?: boolean } = {},
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
      setError((current) => (current.toLowerCase().includes("project") ? "" : current));
    } catch (cause) {
      setError(cause instanceof Error ? cause.message : String(cause));
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

  function editConversationMessage(itemId: string) {
    if (isChatRunActive() || compacting || visibleQueued.length > 0) {
      setError(t("chat.cannotActionWhileRunning"));
      return;
    }
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
    if (!editingMessage) {
      return false;
    }
    if (isChatRunActive() || compacting || visibleQueued.length > 0) {
      setError(t("chat.cannotActionWhileRunning"));
      return true;
    }
    const chat = getChatById(editingMessage.chatId);
    if (!chat || chat.id !== activeChatId) {
      discardMessageEdit();
      return true;
    }
    const index = chat.items.findIndex((item) => item.id === editingMessage.itemId);
    const item = index >= 0 ? chat.items[index] : null;
    if (!item || item.type !== "user") {
      discardMessageEdit();
      return true;
    }
    if (latestConversationItemId(chat.items, (entry) => entry.type === "user") !== editingMessage.itemId) {
      setError(t("chat.latestMessageActionOnly", { defaultValue: "Only the latest message can be changed." }));
      return true;
    }
    const nextText = editingMessage.draftText.trim();
    const nextAttachments = cloneChatAttachments(editingMessage.draftAttachments);
    if (!nextText && nextAttachments.length === 0) {
      discardMessageEdit();
      return true;
    }
    const turnContextLimit = resolveContextLimit(providerSnapshot.provider, providerSnapshot.model, currentModelInfo, apiConfig?.contextWindow);
    const turn: QueuedTurn = {
      id: `edit-${Date.now()}-${Math.random().toString(16).slice(2)}`,
      text: nextText,
      attachments: nextAttachments,
      providerLabel: providerSnapshot.providerLabel,
      provider: providerSnapshot.provider,
      model: providerSnapshot.model,
      contextLimit: turnContextLimit.known ? turnContextLimit.limit : undefined,
    };
    setEditingMessage(null);
    setInput(editingMessage.priorInput);
    setAttachments(cloneChatAttachments(editingMessage.priorAttachments));
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
    void openSettings();
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

  async function submitMessage(event?: FormEvent) {
    event?.preventDefault();
    const composerMessage = input.trim();
    let message = composerMessage;
    let computerUseRequested = false;
    if (!composerMessage && attachments.length === 0) {
      return;
    }
    setError("");
    if (!chatAvailable) {
      const message = chatDisabledReason || t("chat.connectProviderBeforeSend");
      setError(message);
      showTransientFailure("send", message);
      return;
    }
    if (message === "/compact" || message.startsWith("/compact ")) {
      void compactChat();
      setInput("");
      return;
    }
    if (message === "/goal" || message.startsWith("/goal ")) {
      void handleGoalSlashCommand(message);
      return;
    }
    if (message === "/memory" || message.startsWith("/memory ")) {
      void createMemoryFromSlash(message);
      return;
    }
    if (message === "/delegate" || message.startsWith("/delegate ")) {
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
    if (message === "/handoff" || message.startsWith("/handoff ")) {
      setHandoffSendOpen((current) => !current);
      setInput("");
      return;
    }
    if (message === "/desktop" || message.startsWith("/desktop ")) {
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
    const result = await submitTurn(turn);
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
    const chatId = ensureActiveChat();
    const nextAttachments: ChatAttachment[] = [];
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
    const role = roleOverride || (hasPackage ? "outfit_import_plan_review" : "project_index_review");
    const defaultTask =
      role === "skill_delegate"
        ? `Run the delegated skill ${toolName || ""} and report its output.`.trim()
        : role === "outfit_import_plan_review"
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
    setSelectedSubAgentPanelOpen(true);
    try {
      await refreshSelectedSubAgentTask(taskId);
    } catch (cause) {
      setSubAgentError(cause instanceof Error ? cause.message : String(cause));
    }
  }

  async function openSubAgentWorkspace() {
    const task = activeSubAgentTasks.find((item) => item.id === selectedSubAgent?.id) || activeSubAgentTasks[0];
    if (!task) {
      setSelectedSubAgentPanelOpen(true);
      return;
    }
    await inspectSubAgentTask(task.id);
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
        role: "selected_context_review",
        task: "Review the selected conversation excerpt in a scoped sub-agent thread.",
        displayName: agentName,
        parentChatId,
        parentSessionId: parentChat?.sessionId || "",
        projectPath: parentChat?.projectPath || projectPath,
        params: {
          projectPath,
          selectedText,
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
      onOpen={() => void openSubAgentWorkspace()}
    />
  );
  const subAgentWorkspaceSurface = selectedSubAgentPanelOpen ? (
    <div className="min-h-0 flex-1 overflow-hidden px-3 pb-3">
      <Suspense fallback={null}>
        <AsyncSubAgentWorkspaceSurface
          tasks={activeSubAgentTasks}
          selected={activeSubAgentTasks.some((task) => task.id === selectedSubAgent?.id) ? selectedSubAgent : null}
          onSelect={(taskId) => void inspectSubAgentTask(taskId)}
          onCancel={(taskId) => void cancelSubAgentTask(taskId)}
          onRetry={(taskId) => void retrySubAgentTask(taskId)}
          onMerge={(task, decision) => void mergeSubAgentTask(task, decision)}
          onAdoptNextAction={adoptSubAgentNextAction}
          busyTaskIds={subAgentActionBusyTaskIds}
          onClose={() => setSelectedSubAgentPanelOpen(false)}
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
          projectDisplayName={projectDisplayName}
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
          onBackFromSettings={() => setActiveView("chat")}
          onRefreshProjects={() => void refreshProjectList()}
          onSelectProject={selectProjectByPath}
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
            activeChatTitle={activeChat ? activeChat.title || t("header.currentSession") : ""}
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
              onProviderChange={handleProviderChange}
              onApiKeyChange={setApiKey}
              onApiBaseUrlChange={setApiBaseUrl}
              onApiModelChange={setApiModel}
              onDeepSeekAutoNegotiationChange={handleDeepSeekAutoNegotiationChange}
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
              input={input}
              setInput={setInput}
              sending={sending}
              queueAllowed={chatRunSending && !stopRequested}
              permission={permission}
              onSubmit={submitMessage}
              onStop={stopInteractiveActivity}
              onResumeQueue={() => void resumeQueuedTurns()}
              onCancelQueue={() => void cancelQueuedTurns()}
              onSwitchMode={switchMode}
              commands={slashCommands}
              actions={composerActions}
              onAction={runExplicitWorkspaceAction}
              disabledReason={chatDisabledReason}
              attachments={attachments}
              onAttachFiles={(files) => void addComposerFiles(files)}
              onRemoveAttachment={removeAttachment}
              contextUsage={contextUsage}
              compaction={activeChat?.compaction}
              onCancelCompaction={activeChatId ? () => cancelCompaction(activeChatId) : undefined}
              providerLabel={providerSnapshot.providerLabel}
              model={providerSnapshot.model}
              activeGoal={activeAgentGoal}
              onGoalChanged={upsertAgentGoal}
              goalEndpoint={endpoint}
              projects={projectItems.map((project) => ({
                key: projectKey(project),
                name: project.name || shortPath(project.path || ""),
              }))}
              onBindProject={bindProject}
              conversation={conversation}
              queued={visibleQueued}
              agentQuestions={hasAgentRuntimeScope ? agentQuestions : []}
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
              conversationEndRef={conversationEndRef}
              onConversationMouseUp={handleConversationMouseUp}
              onConversationScroll={(scrollElement) => {
                const nearBottom = scrollElement.scrollHeight - scrollElement.scrollTop - scrollElement.clientHeight < 24;
                updateConversationPinned(nearBottom);
                if (selectionMenu) {
                  setSelectionMenu(null);
                }
              }}
              showScrollToBottom={!pinnedToConversationBottom}
              onScrollToBottom={() => {
                updateConversationPinned(true);
                conversationEndRef.current?.scrollIntoView({ behavior: "smooth", block: "end" });
              }}
              pendingApprovalForResponse={pendingApprovalForResponse}
              scopedPendingApprovals={pendingApprovalItems}
              approvalActions={approvalActions}
              editingItemId={editingMessage?.chatId === activeChatId ? editingMessage.itemId : ""}
              editingText={editingMessage?.draftText || ""}
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
          {activeView !== "chat" ? (
            <PendingApprovalsStrip
              approvals={pendingApprovalItems}
              actions={approvalActions}
              loading={loading}
              onApprove={approveShell}
              onReject={rejectShell}
            />
          ) : null}
          {selectedSubAgentPanelOpen ? (
            <div className="absolute inset-x-0 bottom-0 top-14 z-20 flex min-h-0 flex-col bg-workspace">
              {subAgentWorkspaceSurface}
            </div>
          ) : null}
        </section>
        <LayoutSplitter
          side="right"
          value={effectiveRightPaneWidth}
          min={0}
          max={MAX_RIGHT_PANE_WIDTH}
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
        ) : sidebarsVisible ? (
          <Suspense fallback={<SidebarPlaceholder side="right" />}>
            <SidebarMountTracker side="right" onMounted={markRightSidebarMounted}>
              <AsyncRightRuntimeSidebar
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
              agentProgress={agentProgress}
              projectWorkspace={projectChatWorkspace}
              subAgentPanel={projectChatWorkspace ? subAgentActivityPanel : undefined}
              subAgentTaskCount={activeSubAgentTasks.length}
              subAgentRunningTaskCount={runningSubAgentTaskCount}
              subAgentCompletedTaskCount={completedSubAgentTaskCount}
              userAttachmentSources={userAttachmentSources}
              onLocateUserAttachmentSource={locateUserAttachmentSource}
              onOpenUserAttachmentSource={openUserAttachmentSource}
              approvalsLoaded={agentApprovals !== null}
              pendingApprovals={pendingApprovals}
              workspaceSummary={workspaceDiff}
              activeDesktopActions={activeDesktopActions}
              refreshUnityStatus={refreshUnityStatus}
              onHideSidebar={() => setRightSidebarCollapsed(true)}
              localizeHealthMessage={localizeHealthMessage}
              />
            </SidebarMountTracker>
          </Suspense>
        ) : (
          <SidebarPlaceholder side="right" />
        )}
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
        apiKeyPresent={Boolean(apiConfig?.apiKeyPresent)}
        loadingRuntime={loading}
        currentLanguage={i18n.language}
        onRetryRuntime={() => void startRuntime()}
        onOpenSettings={() => {
          setOnboardingMinimized(true);
          openSettingsSection("models");
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
        onLocaleChange={(locale) => void setLocale(locale)}
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
        }}
        onSelectProject={(key) => {
          selectProjectByPath(key);
          setShowProjectModal(false);
          setProjectModalError("");
        }}
        onRemoveCustomProject={removeCustomProject}
        onRestoreProject={unhideProject}
        onNewProjectPathChange={setNewProjectPath}
        onProjectTypeChange={setNewProjectType}
        onClearError={() => setProjectModalError("")}
        onAddProjectPath={() => void addProjectPath()}
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
        onStartRenameChat={startRenameChat}
        onDeleteChat={setDeleteTargetId}
        onCancelDeleteChat={() => setDeleteTargetId("")}
        onConfirmDeleteChat={deleteChatPermanently}
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

    </main>
  );
}
