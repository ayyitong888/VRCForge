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
  Paperclip,
  RotateCcw,
  Search,
  Send,
  Square,
  ThumbsDown,
  ThumbsUp,
} from "lucide-react";
import { useTranslation } from "react-i18next";
import i18n, { setLocale, type LocaleCode } from "./i18n";
import {
  FormEvent,
  PointerEvent as ReactPointerEvent,
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
import { RightRuntimeSidebar } from "./components/runtime/runtime-sidebar";
import { CheckpointWorkspace } from "./components/checkpoints/checkpoint-workspace";
import { SettingsWorkspace } from "./components/settings/settings-workspace";
import { AppSidebar } from "./components/sidebar/app-sidebar";
import { SidebarMenus } from "./components/sidebar/sidebar-menus";
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
import { SkillsWorkspace } from "./components/skills/skills-workspace";
import { SubAgentPanel } from "./components/subagents/sub-agent-panel";
import { useApprovalExecution } from "./hooks/use-approval-execution";
import { useCheckpointWorkspaceController } from "./hooks/use-checkpoint-workspace-controller";
import { useChatRunController, type QueuedTurn } from "./hooks/use-chat-run-controller";
import { useChatSessions } from "./hooks/use-chat-sessions";
import { useContextCompactionController } from "./hooks/use-context-compaction-controller";
import { parseGoalWakeDirective, useGoalWake } from "./hooks/use-goal-wake";
import { useProjectManagement } from "./hooks/use-project-management";
import { useOptimizationWorkspaceController } from "./hooks/use-optimization-workspace-controller";
import { useProtectionWorkspaceController } from "./hooks/use-protection-workspace-controller";
import { useProviderSettings } from "./hooks/use-provider-settings";
import { useRuntimeWorkspace } from "./hooks/use-runtime-workspace";
import { useSettingsWorkspaceController } from "./hooks/use-settings-workspace-controller";
import { useSkillsWorkspaceController } from "./hooks/use-skills-workspace-controller";
import { TEMP_CHATS_COLLAPSE_KEY, type ActiveView, type SettingsSection } from "./lib/app-view";
import {
  COLLAPSED_LEFT_PANE_WIDTH,
  DEVELOPER_OPTIONS_ENABLED_KEY,
  LAYOUT_PANE_WIDTHS_KEY,
  LEFT_SIDEBAR_COLLAPSED_KEY,
  MAX_LEFT_PANE_WIDTH,
  MAX_RIGHT_PANE_WIDTH,
  MIN_CENTER_PANE_WIDTH,
  MIN_LEFT_PANE_WIDTH,
  MIN_RIGHT_PANE_WIDTH,
  ONBOARDING_FLAG_KEY,
  RESIZE_HANDLE_WIDTH,
  RIGHT_RUNTIME_SECTION_COLLAPSED_KEY,
  RIGHT_SIDEBAR_COLLAPSED_KEY,
  THEME_STORAGE_KEY,
  clampNumber,
  loadLayoutPaneWidths,
  loadDeveloperOptionsEnabled,
  loadThemePreference,
  type LayoutPaneWidths,
  type ThemeMode,
} from "./lib/app-preferences";
import { FALLBACK_ENDPOINT, isAbsoluteLocalPath, isRuntimeSessionVerificationError, isTauriRuntime } from "./lib/app-runtime";
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
  readChatAttachment,
  selectedTextAttachment,
} from "./lib/conversation-utils";
import { resolveContextLimit } from "./lib/context-compaction";
import { thinkingTraceLabel } from "./lib/provider-ui";
import type { ChatAttachment, ComposerAction, ComposerActionId, ContextUsage, ConversationItem, MessageFeedback } from "./lib/chat-types";
import { executionModeLabel, permissionVisualState } from "./lib/permission-ui";
import { resolveComputerUseAccentHex } from "./lib/computer-use-visuals";
import { normalizeProjectPathKey, projectKey, shortPath } from "./lib/project-path";
import { asRecord, getHealthDetailNumber } from "./lib/runtime-parsing";
import { buildRuntimeSchedule } from "./lib/runtime-schedule";
import { buildEmptyProjectState } from "./lib/sidebar-view";
import { buildRuntimeWorkspaceViewModel } from "./lib/runtime-workspace-view";
import { displaySubAgentStatus, subAgentRoleLabel, subAgentStatusTone } from "./lib/subagent-ui";
import {
  createMarkdownSmokeChatState,
  createSubAgentContextSmokeTask,
  isMarkdownSmokeMode,
} from "./lib/markdown-smoke";
import { parseDelegateCommand } from "./lib/subagent-delegate";
import { subAgentProposedNextAction } from "./lib/subagent-merge";
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
  refreshProjects,
  repairUnityMcpBridge,
  setAppSessionToken,
  retrySubAgent,
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
};

const MAX_ATTACHMENTS_PER_TURN = 8;
const STARTUP_BACKGROUND_REFRESH_DELAY_MS = 1200;

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
  const [theme, setTheme] = useState<ThemeMode>(() => loadThemePreference());
  const [input, setInput] = useState("");
  const [activeProjectPath, setActiveProjectPath] = useState("");
  const [activeView, setActiveView] = useState<ActiveView>("chat");
  const [activeSettingsSection, setActiveSettingsSection] = useState<SettingsSection>("general");
  const [developerOptionsEnabled, setDeveloperOptionsEnabled] = useState(() => loadDeveloperOptionsEnabled());
  const [developerOptionsEverEnabled, setDeveloperOptionsEverEnabled] = useState(false);
  const [computerUseEnabled, setComputerUseEnabled] = useState(false);
  const [computerUseEverEnabled, setComputerUseEverEnabled] = useState(false);
  const [savingAdvancedSettings, setSavingAdvancedSettings] = useState(false);
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
  const [messageFeedback, setMessageFeedback] = useState<Record<string, MessageFeedback>>({});
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
  const [attachments, setAttachments] = useState<ChatAttachment[]>([]);
  const [editingMessage, setEditingMessage] = useState<EditingMessageDraft | null>(null);
  const [selectionMenu, setSelectionMenu] = useState<{ x: number; y: number; text: string } | null>(null);
  const [doctorReport, setDoctorReport] = useState<DoctorReport | null>(null);
  const [loadingDoctor, setLoadingDoctor] = useState(false);
  const [doctorMessage, setDoctorMessage] = useState("");
  const [repairingUnityBridge, setRepairingUnityBridge] = useState(false);
  const [startupIssue, setStartupIssue] = useState("");
  const [dismissedDoctorPromptSignature, setDismissedDoctorPromptSignature] = useState("");
  const conversationEndRef = useRef<HTMLDivElement | null>(null);
  const projectInitRef = useRef(false);
  const refreshRuntimeRunsRef = useRef<(includeEvents?: boolean, target?: string) => Promise<void>>(async () => undefined);
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
  const desktopEventBootstrapTimerRef = useRef<number | null>(null);
  const desktopEventRuntimeTimerRef = useRef<number | null>(null);
  const desktopEventSubAgentTimerRef = useRef<number | null>(null);
  const subAgentHandoffBusyRef = useRef(new Set<string>());
  const selectionMenuRef = useRef<HTMLDivElement | null>(null);
  const chatSessionActionsRef = useRef<{
    selectProject: (projectPath: string) => void;
    newConversation: (projectPath?: string) => void;
  } | null>(null);

  const permission = bootstrap?.permission;
  const currentPermissionVisual = permissionVisualState(permission);
  const apiConfig = bootstrap?.apiConfig;
  const visionConfig = bootstrap?.visionConfig;
  const healthComponents = bootstrap?.health.components ?? {};
  const healthErrors = Object.values(healthComponents).filter((item) => item.status === "error").length;
  const healthWarnings = Object.values(healthComponents).filter((item) => item.status === "warning").length;
  const runtimeConnected = Boolean(bootstrap?.ok);
  const {
    apiProvider,
    apiKey,
    setApiKey,
    apiBaseUrl,
    setApiBaseUrl,
    apiModel,
    setApiModel,
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
    bootstrapSkills: bootstrap?.agentManifest.skills ?? [],
    activeView,
    setActiveView,
    startRuntime,
    refresh,
    setError,
    t,
  });
  const vrcForgeToolsCount = getHealthDetailNumber(healthComponents.vrcForgeUnityTools?.detail, "vrcForgeToolsCount");
  const vrcForgeToolsReady = runtimeConnected && healthComponents.vrcForgeUnityTools?.status === "ok" && vrcForgeToolsCount > 0;
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
  const toolCount = bootstrap?.agentManifest.toolCount ?? 0;
  const slashCommands = useMemo(() => {
    const list: Array<{ name: string; title: string }> = [
      { name: "compact", title: t("chat.slashCompact") },
      { name: "goal", title: t("chat.slashGoal") },
      { name: "memory", title: t("chat.slashMemory") },
      { name: "delegate", title: t("chat.slashDelegate") },
    ];
    if (developerOptionsEnabled && computerUseEnabled) {
      list.push({ name: "desktop", title: t("composerAction.desktop") });
    }
    for (const skill of skills) {
      if (!skill.name || skill.enabled === false || skill.available === false || skill.userInvocable === false) {
        continue;
      }
      list.push({ name: skill.name, title: skill.title || skill.description || "" });
    }
    return list;
  }, [computerUseEnabled, developerOptionsEnabled, skills, t]);
  const projects = bootstrap?.health.projects?.projects ?? [];
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
    projects,
    refresh,
    refreshSilently,
    startRuntime,
    setError,
    onProjectAdded: (projectPath) => {
      chatSessionActionsRef.current?.selectProject(projectPath);
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
    setActiveProjectPath,
    setActiveView,
    setError,
    expandProjectGroup,
    initialChatState,
  });
  chatSessionActionsRef.current = { selectProject, newConversation };
  const conversation = activeChat?.items ?? [];
  const sessionId = activeChat?.sessionId ?? "";
  const activeRuntimeProjectPath = activeChat?.projectPath || activeProjectPath;
  const hasAgentRuntimeScope = Boolean(sessionId || activeRuntimeProjectPath);
  const latestEditableUserItemId = latestConversationItemId(conversation, (item) => item.type === "user");
  const latestRetryableItemId = latestConversationItemId(conversation, isRetryableConversationItem);
  const pendingApprovalItems = (agentApprovals ?? []).filter((item) => item.status === "pending");
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
    stopCurrentRun,
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
    handleRuntimeSessionFailure,
    setError,
    prepareTurnContext,
    persistChatsNow,
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
    runtimeRunsError,
    desktopActions,
    activeDesktopActions,
    desktopBridge,
    cancellingDesktopActionIds,
    agentGoals,
    agentProgress,
    agentQuestions,
    agentMemory,
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
    rightSidebarCollapsed,
    sending,
    pendingApprovals,
    setBootstrap,
    setAgentApprovals,
    setError,
  });
  refreshRuntimeRunsRef.current = refreshRuntimeRuns;
  useGoalWake({
    endpoint,
    runtimeConnected,
    chatAvailable,
    sending,
    onGoalDelivery: async (goal, delivery) => {
      const targetChat = chats.find((chat) => chat.id === delivery.chatId);
      if (!targetChat || chatRunSending || compacting) {
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
      const result = await submitTurn(turn);
      if (result === "queue_full" || result === "failed") {
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
    setEditingMessage(null);
    setInput("");
    setAttachments([]);
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
    maxAttachmentsPerTurn: MAX_ATTACHMENTS_PER_TURN,
    setInput,
    setAttachments,
    setRuntimeNotice,
    setError,
    formatPayload,
    appendToChat,
    refresh,
    refreshRuntimeRuns,
    loadCheckpoints,
  });
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
    const nextUsage = buildContextUsageFromRuntime(latestContextUsage, providerSnapshot.provider, providerSnapshot.model, currentModelInfo, t);
    if (nextUsage?.source === "provider_usage") {
      return nextUsage;
    }
    const cachedUsage = buildContextUsageFromRuntime(activeChat?.contextUsageCache, providerSnapshot.provider, providerSnapshot.model, currentModelInfo, t);
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
  const subAgentTasks = subAgentList?.tasks ?? [];
  const activeSubAgentTasks = useMemo(() => {
    const parentChatId = activeChat?.id || "";
    return parentChatId ? subAgentTasks.filter((task) => task.parentChatId === parentChatId) : [];
  }, [activeChat?.id, subAgentTasks]);
  const visibleSubAgentTasks = activeSubAgentTasks;
  const runtimeSchedule = useMemo(
    () => buildRuntimeSchedule({ currentTurn, stopRequested, queued, activeSubAgentTasks }),
    [activeSubAgentTasks, currentTurn, i18n.language, queued, stopRequested],
  );
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
  const backendComponent = healthComponents.backend;
  const unityBridgeComponent = healthComponents.unityMcpBridgeReachable;
  const unityToolsComponent = healthComponents.vrcForgeUnityTools;
  const providerComponent = healthComponents.providerConfigPresent;
  const runtimeWorkspaceView = useMemo(
    () =>
      buildRuntimeWorkspaceViewModel({
        t,
        conversation,
        workspaceDiff,
        pendingApprovalItems,
        runtimeRuns,
        workspaceProjectLabel: activeProjectPath ? activeProjectName || shortPath(activeProjectPath) : t("sidebar.tempChat"),
        runtimeConnected,
        unityBridgeComponent,
        unityToolsComponent,
        vrcForgeToolsReady,
        vrcForgeToolsCount,
        providerLabel: providerSnapshot.providerLabel,
        model: providerSnapshot.model,
        pendingApprovals,
        loadingWorkspaceDiff,
        workspaceDiffError,
        onOpenCheckpoints: () => setActiveView("checkpoints"),
        onToggleWorkspaceDiffReview: toggleWorkspaceDiffReview,
      }),
    [
      activeProjectName,
      activeProjectPath,
      conversation,
      loadingWorkspaceDiff,
      pendingApprovalItems,
      pendingApprovals,
      providerSnapshot.model,
      providerSnapshot.providerLabel,
      runtimeConnected,
      runtimeRuns,
      t,
      toggleWorkspaceDiffReview,
      unityBridgeComponent,
      unityToolsComponent,
      vrcForgeToolsCount,
      vrcForgeToolsReady,
      workspaceDiff,
      workspaceDiffError,
    ],
  );
  const {
    workspaceDiffFiles,
    workspaceDiffChanged,
    runtimeFileReferences,
    runtimeReviewEvidence,
    localizeHealthMessage,
    workspaceProjectLabel,
    unityBridgeLabel,
    unityToolsLabel,
    providerCompactLabel,
    reviewSummaryLabel,
    changeSummaryLabel,
  } = runtimeWorkspaceView;
  const hasRightSidebarProjectContext = Boolean(activeRuntimeProjectPath);
  const showRightSidebarStatusSummary = !hasRightSidebarProjectContext && !activeChat;
  const showRightSidebarWorkspaceArtifacts = hasRightSidebarProjectContext;
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
  }, [bootstrap?.advancedSettings]);

  useEffect(() => {
    if (initialOnboardingState.migrateLanguageGateCompletion) {
      persistOnboardingLanguageGateCompletion();
    }
  }, [initialOnboardingState.migrateLanguageGateCompletion]);

  useEffect(() => {
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
      "agentProgress",
      "agentQuestions",
      "agentPermission",
      "agentRuntimeCancel",
      "agentRuntimeQueue",
      "agentRuntimeRuns",
      "agentRuntimeTurn",
    ]);
    const bootstrapEvents = new Set(["advancedSettings", "agentPermission", "hello", "projects", "unity_status"]);
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

  useEffect(() => {
    setAgentApprovals(null);
  }, [activeRuntimeProjectPath]);

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
    next: Partial<Pick<AdvancedSettingsState, "developerOptionsEnabled" | "computerUseEnabled">> & {
      developerChallengeId?: string;
    },
  ) {
    const nextDeveloperOptionsEnabled = next.developerOptionsEnabled ?? developerOptionsEnabled;
    const nextComputerUseEnabled = nextDeveloperOptionsEnabled && (next.computerUseEnabled ?? computerUseEnabled);
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
        developerChallengeId: next.developerChallengeId,
      });
      const settings = payload.settings;
      setDeveloperOptionsEnabled(settings.developerOptionsEnabled);
      setDeveloperOptionsEverEnabled(settings.developerOptionsEverEnabled);
      setComputerUseEnabled(settings.computerUseEnabled);
      setComputerUseEverEnabled(settings.computerUseEverEnabled);
      setBootstrap((current) => (current ? { ...current, advancedSettings: settings } : current));
    } catch (cause) {
      setError(cause instanceof Error ? cause.message : String(cause));
    } finally {
      setSavingAdvancedSettings(false);
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
    });
    setInput(item.text);
    setAttachments(cloneChatAttachments(item.attachments || []));
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
    setEditingMessage(null);
    setRuntimeNotice("");
  }

  async function saveMessageEdit(message: string) {
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
    const turnContextLimit = resolveContextLimit(providerSnapshot.provider, providerSnapshot.model, currentModelInfo);
    const turn: QueuedTurn = {
      id: `edit-${Date.now()}-${Math.random().toString(16).slice(2)}`,
      text: message,
      attachments: cloneChatAttachments(attachments),
      providerLabel: providerSnapshot.providerLabel,
      provider: providerSnapshot.provider,
      model: providerSnapshot.model,
      contextLimit: turnContextLimit.known ? turnContextLimit.limit : undefined,
    };
    setEditingMessage(null);
    setInput("");
    setAttachments([]);
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
    const turnContextLimit = resolveContextLimit(providerSnapshot.provider, providerSnapshot.model, currentModelInfo);
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

  function toggleRightRuntimeSection(section: string) {
    setRightRuntimeSectionsCollapsed((current) => ({ ...current, [section]: !current[section] }));
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
    const limit = resolveContextLimit(providerSnapshot.provider, providerSnapshot.model, currentModelInfo);
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
      setError(chatDisabledReason || t("chat.connectProviderBeforeSend"));
      return;
    }
    if (editingMessage) {
      await saveMessageEdit(message);
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
    const turnContextLimit = resolveContextLimit(providerSnapshot.provider, providerSnapshot.model, currentModelInfo);
    const turn: QueuedTurn = {
      id: `turn-${Date.now()}-${Math.random().toString(16).slice(2)}`,
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
    if (result === "queue_full") {
      setInput(computerUseRequested ? `/desktop ${message}` : message);
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

  async function reconcileSubAgentHandoffs(tasks: SubAgentTask[]) {
    for (const task of tasks) {
      if (!task.parentChatId || !["completed", "failed"].includes(task.status)) {
        continue;
      }
      const parentChat = getChatById(task.parentChatId);
      if (!parentChat || subAgentHandoffBusyRef.current.has(task.id)) {
        continue;
      }
      const cardId = `subagent-${task.id}`;
      const existingCard = parentChat.items.find(
        (item): item is Extract<ConversationItem, { type: "subagent" }> => item.id === cardId && item.type === "subagent",
      );
      const needsCardUpdate = !existingCard || existingCard.task.revision !== task.revision;
      if (!needsCardUpdate && task.handoffStatus !== "handoff_pending") {
        continue;
      }
      subAgentHandoffBusyRef.current.add(task.id);
      try {
        if (needsCardUpdate) {
          updateChat(parentChat.id, (chat) => {
            const nextItem: ConversationItem = { id: cardId, type: "subagent", task };
            const index = chat.items.findIndex((item) => item.id === cardId);
            const items = [...chat.items];
            if (index >= 0) {
              items[index] = nextItem;
            } else {
              items.push(nextItem);
            }
            return touchChat({ ...chat, items }, task.updatedAt || new Date().toISOString());
          });
        }
        if (needsCardUpdate || task.handoffStatus === "handoff_pending") {
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
      const payload = await fetchSubAgents(endpoint, includeEvents);
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

  async function mergeSubAgentTask(task: SubAgentTask, decision: "adopted" | "dismissed") {
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
      // The stable card is updated in place. Replayed merge requests cannot
      // append a second copy because the backend decision and card id are durable.
      updateChat(task.parentChatId, (chat) => {
        const cardId = `subagent-${payload.task.id}`;
        const nextItem: ConversationItem = { id: cardId, type: "subagent", task: payload.task };
        const index = chat.items.findIndex((item) => item.id === cardId);
        const items = [...chat.items];
        if (index >= 0) {
          items[index] = nextItem;
        } else {
          items.push(nextItem);
        }
        return touchChat({ ...chat, items });
      });
      await persistChatsNow();
      setActiveView("chat");
    } catch (cause) {
      setSubAgentError(cause instanceof Error ? cause.message : String(cause));
      void loadSubAgents(false);
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

  return (
    <main className="h-screen overflow-hidden bg-background text-foreground">
      <div className="grid h-screen" style={{ gridTemplateColumns: workspaceGridColumns }}>
        <AppSidebar
          collapsed={leftSidebarCollapsed}
          activeView={activeView}
          activeSettingsSection={activeSettingsSection}
          developerOptionsEnabled={developerOptionsEnabled}
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
          onOpenSettings={() => openSettingsSection("general")}
          onOpenSettingsSection={openSettingsSection}
          onBackFromSettings={() => setActiveView("chat")}
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

        <LayoutSplitter
          side="left"
          value={effectiveLeftPaneWidth}
          min={COLLAPSED_LEFT_PANE_WIDTH}
          max={MAX_LEFT_PANE_WIDTH}
          title={t("workspace.resizeLeftPane")}
          onPointerDown={(event) => startLayoutResize("left", event)}
        />

        <section className="flex h-screen min-w-0 flex-col overflow-hidden bg-workspace">
          <WorkspaceHeader
            activeProjectLabel={activeProjectPath ? activeProjectName : t("sidebar.tempChat")}
            activeView={activeView}
            activeChatTitle={activeChat ? activeChat.title || t("header.currentSession") : ""}
            permissionFullAuto={Boolean(permission?.roslynFullAuto)}
            permissionAuto={permission?.executionMode === "auto"}
            permissionBadgeTone={currentPermissionVisual.badgeTone}
            runtimeConnected={runtimeConnected}
            pendingApprovals={pendingApprovals}
            rightSidebarCollapsed={rightSidebarCollapsed}
            theme={theme}
            showDoctorStartupPrompt={showDoctorStartupPrompt}
            hasStartupIssue={hasStartupIssue}
            healthErrors={healthErrors}
            healthWarnings={healthWarnings}
            startupIssue={startupIssue}
            loadingDoctor={loadingDoctor}
            loading={loading}
            error={error}
            onToggleRightSidebar={() => setRightSidebarCollapsed((value) => !value)}
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
              repairingUnityBridge={repairingUnityBridge}
              exportingSupportBundle={exportingSupportBundle}
              onRefresh={() => void loadDoctor()}
              onRepairUnityBridge={() => void repairUnityBridgeFromDoctor()}
              onOpenSettings={() => openSettingsSection("general")}
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
              savingAdvancedSettings={savingAdvancedSettings}
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
              permission={permission}
              onSubmit={submitMessage}
              onStop={stopInteractiveActivity}
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
              editing={Boolean(editingMessage && editingMessage.chatId === activeChatId)}
              onCancelEdit={cancelMessageEdit}
              projects={projectItems.map((project) => ({
                key: projectKey(project),
                name: project.name || shortPath(project.path || ""),
              }))}
              onBindProject={bindProject}
              conversation={conversation}
              queued={visibleQueued}
              agentQuestions={hasAgentRuntimeScope ? agentQuestions : []}
              onAnswerQuestion={answerRuntimeQuestion}
              conversationEndRef={conversationEndRef}
              onConversationMouseUp={handleConversationMouseUp}
              onConversationScroll={() => (selectionMenu ? setSelectionMenu(null) : undefined)}
              pendingApprovalForResponse={pendingApprovalForResponse}
              approvalActions={approvalActions}
              messageFeedback={messageFeedback}
              latestRetryableItemId={latestRetryableItemId}
              latestEditableUserItemId={latestEditableUserItemId}
              onCopyItem={copyConversationItem}
              onRetryItem={retryConversationItem}
              onEditItem={editConversationMessage}
              onFeedbackItem={setConversationFeedback}
              onApprove={approveShell}
              onReject={rejectShell}
              onModifyApproval={modifyApprovalInComposer}
              onOpenSettings={() => openSettingsSection("models")}
              onOpenDoctor={() => void openDoctor()}
            />
          )}
          {activeView !== "chat" ? (
            <PendingApprovalsStrip
              approvals={pendingApprovalItems}
              loading={loading}
              onApprove={approveShell}
              onReject={rejectShell}
            />
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
              showStatusSummary={showRightSidebarStatusSummary}
              showWorkspaceArtifacts={showRightSidebarWorkspaceArtifacts}
              workspaceDiffChanged={workspaceDiffChanged}
              workspaceDiff={workspaceDiff}
              runtimeNotice={runtimeNotice}
              pendingApprovalItems={pendingApprovalItems}
              runtimeRuns={runtimeRuns}
              runtimeRunsError={runtimeRunsError}
              rightRuntimeSectionsCollapsed={rightRuntimeSectionsCollapsed}
              agentGoals={agentGoals}
              agentProgress={hasAgentRuntimeScope ? agentProgress : []}
              agentMemory={agentMemory}
              desktopActions={desktopActions}
              desktopBridge={desktopBridge}
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
              onSaveOperationAsSkill={(summary) => void openSkillsWithCapturedPath(summary)}
              inspectSubAgentTask={inspectSubAgentTask}
              onCloseSelectedSubAgentPanel={() => setSelectedSubAgentPanelOpen(false)}
              onOpenSelectedSubAgentPanel={() => setSelectedSubAgentPanelOpen(true)}
              onMergeSubAgent={mergeSubAgentTask}
              onAdoptSubAgentNextAction={adoptSubAgentNextAction}
              subAgentRoleLabel={subAgentRoleLabel}
              subAgentStatusTone={subAgentStatusTone}
              displaySubAgentStatus={displaySubAgentStatus}
              formatPayload={formatPayload}
          />
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
        apiKeyPresent={Boolean(apiConfig?.apiKeyPresent)}
        hasProjects={projectItems.length > 0}
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
