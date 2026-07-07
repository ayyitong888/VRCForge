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
import { PendingApprovalsStrip } from "./components/approvals/pending-approvals-strip";
import { ChatWorkspace } from "./components/chat/chat-workspace";
import { LayoutSplitter } from "./components/workspace/layout-splitter";
import { WorkspaceHeader } from "./components/workspace/workspace-header";
import { DoctorWorkspace } from "./components/doctor/doctor-workspace";
import { OptimizationWorkspace, buildOptimizationRequestOptions, type OptimizationActionOptions } from "./components/optimization/optimization-workspace";
import { ProtectionWorkspace, protectionPlanPayload } from "./components/protection/protection-workspace";
import { RightRuntimeSidebar } from "./components/runtime/runtime-sidebar";
import { CheckpointWorkspace } from "./components/checkpoints/checkpoint-workspace";
import { SettingsWorkspace } from "./components/settings/settings-workspace";
import { AppSidebar } from "./components/sidebar/app-sidebar";
import { SidebarMenus } from "./components/sidebar/sidebar-menus";
import { OnboardingOverlay } from "./components/onboarding/onboarding-overlay";
import { OutfitImportPanel } from "./components/project/outfit-import-panel";
import { ProjectIndexPanel } from "./components/project/project-index-panel";
import { ProjectPickerModal } from "./components/project/project-picker-modal";
import { SkillsWorkspace } from "./components/skills/skills-workspace";
import { SubAgentPanel } from "./components/subagents/sub-agent-panel";
import { useApprovalExecution } from "./hooks/use-approval-execution";
import { useCheckpointWorkspaceController } from "./hooks/use-checkpoint-workspace-controller";
import { useChatRunController, type QueuedTurn } from "./hooks/use-chat-run-controller";
import { useChatSessions } from "./hooks/use-chat-sessions";
import { useProjectManagement } from "./hooks/use-project-management";
import { useProviderSettings } from "./hooks/use-provider-settings";
import { useRuntimeWorkspace } from "./hooks/use-runtime-workspace";
import { TEMP_CHATS_COLLAPSE_KEY, type ActiveView } from "./lib/app-view";
import {
  COLLAPSED_LEFT_PANE_WIDTH,
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
  loadThemePreference,
  type LayoutPaneWidths,
  type ThemeMode,
} from "./lib/app-preferences";
import { FALLBACK_ENDPOINT, isAbsoluteLocalPath, isRuntimeSessionVerificationError, isTauriRuntime } from "./lib/app-runtime";
import type { AgentRuntimeDeltaEvent } from "./lib/chat-streaming";
import { formatConnectorActionMessage } from "./lib/connector-ui";
import {
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
} from "./lib/conversation-utils";
import { thinkingTraceLabel } from "./lib/provider-ui";
import type { ChatAttachment, ComposerAction, ComposerActionId, ContextUsage, ConversationItem, MessageFeedback } from "./lib/chat-types";
import { executionModeLabel, permissionVisualState } from "./lib/permission-ui";
import { normalizeProjectPathKey, projectKey, shortPath } from "./lib/project-path";
import { asRecord, getHealthDetailNumber } from "./lib/runtime-parsing";
import { buildRuntimeSchedule } from "./lib/runtime-schedule";
import { emptySkillDraft } from "./lib/skill-draft";
import { buildEmptyProjectState } from "./lib/sidebar-view";
import { buildRuntimeWorkspaceViewModel } from "./lib/runtime-workspace-view";
import { displaySubAgentStatus, subAgentRoleLabel, subAgentStatusTone } from "./lib/subagent-ui";
import {
  createMarkdownSmokeChatState,
  createSubAgentContextSmokeTask,
  isMarkdownSmokeMode,
  markdownSmokeAgentNotes,
} from "./lib/markdown-smoke";
import { pickSubAgentName, updateSubAgentList } from "./lib/subagent-state";
import {
  AgentApproval,
  AgentRuntimeResponse,
  AgentReasoningTrace,
  AgentSkill,
  AgentSkillRegistry,
  AvatarListItem,
  AvatarEncryptionPlanResult,
  SubAgentTask,
  SubAgentTaskList,
  ApiError,
  AppBootstrap,
  DoctorReport,
  DiagnosticsStatus,
  ExternalAgentConnectorClient,
  ExternalAgentConnectorStatus,
  OptimizationPlannerReport,
  OptimizationProofDetail,
  OptimizationProofSummary,
  SkillPackageEntry,
  SkillPackagePreflight,
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
  fetchBootstrap,
  fetchDiagnostics,
  fetchDoctor,
  fetchExternalAgentConnectors,
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
  fetchSubAgent,
  fetchSubAgents,
  installExternalAgentConnector,
  importSkillPackage,
  planAvatarEncryption,
  preflightSkillPackage,
  requestAgentDesktopAction,
  requestOptimizationApply,
  requestAvatarEncryptionApply,
  requestPackageInstall,
  refreshProjects,
  repairUnityMcpBridge,
  revokeSkillPackageSigner,
  retrySubAgent,
  saveAgentNotes,
  setSkillPackageSafeMode,
  setSkillPackageEnabled,
  setAppSessionToken,
  trustSkillPackageSigner,
  updateDiagnostics,
  updateExternalAgentGateway,
  updatePermission,
  updateSkill,
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

const MAX_ATTACHMENTS_PER_TURN = 8;
const STARTUP_BACKGROUND_REFRESH_DELAY_MS = 1200;

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
  const [error, setError] = useState("");
  const [theme, setTheme] = useState<ThemeMode>(() => loadThemePreference());
  const [input, setInput] = useState("");
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
  const [messageFeedback, setMessageFeedback] = useState<Record<string, MessageFeedback>>({});
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
  const [compacting, setCompacting] = useState(false);
  const [attachments, setAttachments] = useState<ChatAttachment[]>([]);
  const [selectionMenu, setSelectionMenu] = useState<{ x: number; y: number; text: string } | null>(null);
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
    savedProviderLabel,
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
    appendToChat,
    ensureActiveChat,
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
  const latestEditableUserItemId = latestConversationItemId(conversation, (item) => item.type === "user");
  const latestRetryableItemId = latestConversationItemId(conversation, isRetryableConversationItem);
  const pendingApprovalItems = (agentApprovals ?? []).filter((item) => item.status === "pending");
  const pendingApprovals = pendingApprovalItems.length;
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
  });
  const sending = chatRunSending || compacting;
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
    agentGoals,
    agentMemory,
    workspaceStateError,
    runtimeNotice,
    setRuntimeNotice,
    refreshUnityStatus,
    refreshWorkspaceDiff,
    refreshRuntimeRuns,
    toggleWorkspaceDiffReview,
    prependDesktopAction,
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
    const configuredLimit = connectorStatus?.gateway?.checkpointArchiveMaxSizeMb;
    setCheckpointArchiveLimitInput(typeof configuredLimit === "number" ? String(configuredLimit) : "10240");
  }, [connectorStatus?.gateway?.checkpointArchiveMaxSizeMb]);

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
    const turn: QueuedTurn = {
      id: `retry-${Date.now()}-${Math.random().toString(16).slice(2)}`,
      text: userItem.text,
      attachments: cloneChatAttachments(userItem.attachments || []),
      providerLabel: providerSnapshot.providerLabel,
      provider: providerSnapshot.provider,
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
    if (!activeChat || activeChat.items.length === 0) {
      setError(t("compact.noContent"));
      return;
    }
    const chatId = activeChat.id;
    const items = activeChat.items;
    setCompacting(true);
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
      setCompacting(false);
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
      provider: providerSnapshot.provider,
      model: providerSnapshot.model,
    };
    setInput("");
    setAttachments([]);
    const result = await submitTurn(turn);
    if (result === "queue_full") {
      setInput(message);
      setAttachments(turn.attachments);
    }
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

  function finishOnboarding() {
    try {
      window.localStorage.setItem(ONBOARDING_FLAG_KEY, "true");
    } catch {
      // Keep onboarding close usable even if local storage is blocked.
    }
    setShowOnboarding(false);
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
    setShowOnboarding(true);
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
          ) : (
            <ChatWorkspace
              projectPromptTitle={projectPromptTitle}
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
              conversation={conversation}
              queued={queued}
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
              onOpenSettings={() => void openSettings()}
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
