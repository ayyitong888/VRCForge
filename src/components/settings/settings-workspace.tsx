import { version as appVersion } from "../../../package.json";
import { Check, ChevronDown, Eye, Globe, Loader2, RefreshCw } from "lucide-react";
import type { FormEvent } from "react";
import { useEffect, useState } from "react";
import { useTranslation } from "react-i18next";
import { SUPPORTED_LOCALES } from "../../i18n";
import type { SettingsSection } from "../../lib/app-view";
import { loadStartupAnimationEnabled, persistStartupAnimationEnabled, type RunningEnterAction } from "../../lib/app-preferences";
import type {
  DiagnosticsStatus,
  DiagnosticLogLevel,
  ExecutionMode,
  ExternalAgentConnectorClient,
  ExternalAgentConnectorStatus,
  PermissionState,
  ProviderModelInfo,
  ProviderApiType,
  ProviderReasoningVariants,
  VisionConfig,
} from "../../lib/api";
import { EXECUTION_MODES, executionModeLabel, permissionVisualState } from "../../lib/permission-ui";
import { hasSavedProviderKey } from "../../lib/provider-key-state";
import { cn } from "../../lib/utils";
import { Badge } from "../ui/badge";
import { Button } from "../ui/button";
import { BackgroundGoalSettings } from "./background-goal-settings";
import { CheckpointStoragePanel } from "./checkpoint-storage-panel";
import { DeveloperOptionsControl } from "./developer-options-control";
import { DiagnosticIdentityMap, DiagnosticsSettingsPanel } from "./diagnostics-settings-panel";
import { ExternalAgentConnectorsPanel } from "./external-agent-connectors-panel";
import { MemoryReviewSettings } from "./memory-review-settings";
import { ProviderSetup, VisionProfileSetup } from "./provider-settings";
import { ThemeCustomizationPanel } from "./theme-customization-panel";
import type { ThemeCustomization } from "../../lib/theme-customization";
import { DataRootSettingsPanel } from "./data-root-settings-panel";
import { ChatGptOAuthSettings } from "../../features/chatgpt-oauth/settings";
import { TelegramSettingsPanel } from "./telegram-settings-panel";
import { AgentQuestionNotificationSoundSettings } from "./agent-question-notification-sound";

type SettingsWorkspaceProps = {
  activeSection: SettingsSection;
  endpoint: string;
  developerOptionsEnabled: boolean;
  developerOptionsEverEnabled: boolean;
  computerUseEnabled: boolean;
  computerUseEverEnabled: boolean;
  backgroundGoalNotificationsEnabled: boolean;
  savingAdvancedSettings: boolean;
  permission: PermissionState | null;
  loading: boolean;
  runtimeConnected: boolean;
  providerConfigurationLocked?: boolean;
  currentLanguage: string;
  runningEnterAction: RunningEnterAction;
  onRunningEnterActionChange: (action: RunningEnterAction) => void;
  themeCustomization: ThemeCustomization;
  themeBackgroundError?: string;
  apiProvider: string;
  apiKey: string;
  apiBaseUrl: string;
  apiModel: string;
  apiType: ProviderApiType;
  apiContextWindow: string;
  selectedModelCapabilities?: readonly string[];
  selectedModelCapabilitySource?: string;
  apiThinkingLevel: string;
  reasoningVariants: ProviderReasoningVariants | null;
  apiKeySaved: boolean;
  savingApiConfig: boolean;
  modelOptions: ProviderModelInfo[];
  loadingModels: boolean;
  modelsError: string;
  testingProvider: string;
  providerTestMessage: string;
  providerTestPassed: boolean;
  visionConfig?: VisionConfig;
  visionProvider: string;
  visionApiKey: string;
  visionBaseUrl: string;
  visionModel: string;
  visionEnabled: boolean;
  savingVisionConfig: boolean;
  diagnosticsStatus: DiagnosticsStatus | null;
  diagnosticsMessage: string;
  loadingDiagnostics: boolean;
  exportingSupportBundle: boolean;
  connectorStatus: ExternalAgentConnectorStatus | null;
  loadingConnectors: boolean;
  connectorMessage: string;
  selectedProjectPath: string;
  memoryReviewRefreshSignal?: number;
  isDesktop: boolean;
  checkpointArchiveLimitInput: string;
  checkpointArchiveCountInput: string;
  agentNotes: string;
  agentNotesLoaded: boolean;
  agentNotesPath: string;
  notesMessage: string;
  savingNotes: boolean;
  compactDebugEntries: Array<{ id: string; text: string; entryCount?: number; createdAt?: string }>;
  onSectionChange: (section: SettingsSection) => void;
  onDeveloperOptionsChange: (enabled: boolean, developerChallengeId?: string) => Promise<void> | void;
  onComputerUseChange: (enabled: boolean) => void;
  onBackgroundGoalNotificationsChange: (enabled: boolean) => void;
  onSwitchMode: (mode: ExecutionMode) => void;
  onRestartOnboarding: () => void;
  onLocaleChange: (code: string) => void;
  onThemeCustomizationChange: (next: Partial<ThemeCustomization>) => void;
  onResetThemeCustomization: () => void;
  onLoadModels: () => void;
  onProviderTest: (capability: "text" | "structured" | "vision") => void;
  onProviderConfigChanged: () => Promise<void> | void;
  onProviderChange: (value: string) => void;
  onApiKeyChange: (value: string) => void;
  onApiBaseUrlChange: (value: string) => void;
  onApiModelChange: (value: string) => void;
  onProtocolAutoNegotiationChange: (enabled: boolean) => void;
  onApiTypeChange: (value: ProviderApiType) => void;
  onApiContextWindowChange: (value: string) => void;
  onApiThinkingLevelChange: (value: string) => void;
  onSaveApiProvider: (event?: FormEvent) => void;
  onVisionProviderChange: (value: string) => void;
  onVisionApiKeyChange: (value: string) => void;
  onVisionBaseUrlChange: (value: string) => void;
  onVisionModelChange: (value: string) => void;
  onVisionEnabledChange: (value: boolean) => void;
  onSaveVisionProfile: (event?: FormEvent) => void;
  onClearVisionProfile: () => void;
  onSetLogLevel: (level: DiagnosticLogLevel) => void;
  onOpenLogsFolder: () => void;
  onCreateSupportBundle: () => void;
  onCheckpointArchiveLimitInputChange: (value: string) => void;
  onCheckpointArchiveCountInputChange: (value: string) => void;
  onSaveCheckpointArchiveLimit: () => void;
  onOpenCheckpointArchiveFolder: (targetPath: string) => void;
  onPickCheckpointArchiveDirectory: (currentPath: string) => Promise<string>;
  onDeleteCheckpointArchives: (ids: string[]) => void;
  onRelocateCheckpointArchives: (directory: string) => void;
  onLoadConnectors: () => void;
  onUpdateGatewaySettings: (settings: { enabled?: boolean; allowWriteRequests?: boolean; revokeToken?: boolean }) => void;
  onRunConnectorAction: (client: ExternalAgentConnectorClient, action: "install" | "uninstall", configPath?: string) => void;
  onCopyConnectorText: (text: string, label: string) => void;
  onAgentNotesChange: (value: string) => void;
  onSaveNotes: (event: FormEvent) => void;
};

export function SettingsWorkspace({
  activeSection,
  endpoint,
  developerOptionsEnabled,
  developerOptionsEverEnabled,
  computerUseEnabled,
  computerUseEverEnabled,
  backgroundGoalNotificationsEnabled,
  savingAdvancedSettings,
  permission,
  loading,
  runtimeConnected,
  providerConfigurationLocked = false,
  currentLanguage,
  runningEnterAction,
  onRunningEnterActionChange,
  themeCustomization,
  themeBackgroundError = "",
  apiProvider,
  apiKey,
  apiBaseUrl,
  apiModel,
  apiType,
  apiContextWindow,
  selectedModelCapabilities,
  selectedModelCapabilitySource,
  apiThinkingLevel,
  reasoningVariants,
  apiKeySaved,
  savingApiConfig,
  modelOptions,
  loadingModels,
  modelsError,
  testingProvider,
  providerTestMessage,
  providerTestPassed,
  visionConfig,
  visionProvider,
  visionApiKey,
  visionBaseUrl,
  visionModel,
  visionEnabled,
  savingVisionConfig,
  diagnosticsStatus,
  diagnosticsMessage,
  loadingDiagnostics,
  exportingSupportBundle,
  connectorStatus,
  loadingConnectors,
  connectorMessage,
  selectedProjectPath,
  memoryReviewRefreshSignal = 0,
  isDesktop,
  checkpointArchiveLimitInput,
  checkpointArchiveCountInput,
  agentNotes,
  agentNotesLoaded,
  agentNotesPath,
  notesMessage,
  savingNotes,
  compactDebugEntries,
  onSectionChange,
  onDeveloperOptionsChange,
  onComputerUseChange,
  onBackgroundGoalNotificationsChange,
  onSwitchMode,
  onRestartOnboarding,
  onLocaleChange,
  onThemeCustomizationChange,
  onResetThemeCustomization,
  onLoadModels,
  onProviderTest,
  onProviderConfigChanged,
  onProviderChange,
  onApiKeyChange,
  onApiBaseUrlChange,
  onApiModelChange,
  onProtocolAutoNegotiationChange,
  onApiTypeChange,
  onApiContextWindowChange,
  onApiThinkingLevelChange,
  onSaveApiProvider,
  onVisionProviderChange,
  onVisionApiKeyChange,
  onVisionBaseUrlChange,
  onVisionModelChange,
  onVisionEnabledChange,
  onSaveVisionProfile,
  onClearVisionProfile,
  onSetLogLevel,
  onOpenLogsFolder,
  onCreateSupportBundle,
  onCheckpointArchiveLimitInputChange,
  onCheckpointArchiveCountInputChange,
  onSaveCheckpointArchiveLimit,
  onOpenCheckpointArchiveFolder,
  onPickCheckpointArchiveDirectory,
  onDeleteCheckpointArchives,
  onRelocateCheckpointArchives,
  onLoadConnectors,
  onUpdateGatewaySettings,
  onRunConnectorAction,
  onCopyConnectorText,
  onAgentNotesChange,
  onSaveNotes,
}: SettingsWorkspaceProps) {
  const { t } = useTranslation();
  const [startupAnimationEnabled, setStartupAnimationEnabled] = useState(loadStartupAnimationEnabled);
  const [providerPanel, setProviderPanel] = useState<"subscription" | "api" | null>(null);
  const visibleProvider = providerPanel ?? (apiProvider === "chatgpt" ? "subscription" : "api");
  useEffect(() => setProviderPanel(null), [apiProvider]);
  const currentPermissionVisual = permissionVisualState(permission);
  const visionKeySaved = hasSavedProviderKey(visionProvider, visionConfig);
  const visibleSection: SettingsSection = activeSection === "developer" && !developerOptionsEnabled ? "general" : activeSection;
  const updateDeveloperOptions = async (enabled: boolean, developerChallengeId?: string) => {
    await onDeveloperOptionsChange(enabled, developerChallengeId);
    if (!enabled && visibleSection === "developer") {
      onSectionChange("general");
    }
  };

  return (
    <div className="app-scrollbar min-h-0 flex-1 overflow-y-auto px-6 py-10" data-vrcforge-settings-scroll>
      <div className="mx-auto w-full max-w-3xl">
        {visibleSection === "general" ? (
          <>
            <h1 className="text-xl font-semibold text-foreground">{t("settings.navGeneral")}</h1>

            <section className="mt-6">
              <label className="flex items-center justify-between gap-5">
                <span className="min-w-0">
                  <span id="settings-startup-animation-label" className="block text-base font-semibold">
                    {t("settings.startupAnimation")}
                  </span>
                  <span id="settings-startup-animation-description" className="mt-1 block text-sm text-muted-foreground">
                    {t("settings.startupAnimationDesc")}
                  </span>
                </span>
                <input
                  type="checkbox"
                  role="switch"
                  aria-labelledby="settings-startup-animation-label"
                  aria-describedby="settings-startup-animation-description"
                  checked={startupAnimationEnabled}
                  onChange={(event) => {
                    const enabled = event.currentTarget.checked;
                    setStartupAnimationEnabled(enabled);
                    persistStartupAnimationEnabled(enabled);
                  }}
                  className="peer sr-only"
                />
                <span
                  aria-hidden="true"
                  className="relative h-7 w-12 shrink-0 rounded-full border border-border bg-muted after:absolute after:left-0.5 after:top-0.5 after:h-5 after:w-5 after:rounded-full after:bg-white after:shadow-sm peer-checked:border-primary peer-checked:bg-primary peer-checked:after:translate-x-5 peer-focus-visible:ring-2 peer-focus-visible:ring-ring peer-focus-visible:ring-offset-2"
                />
              </label>
            </section>

            <section className="mt-6">
              <ThemeCustomizationPanel
                value={themeCustomization}
                backgroundError={themeBackgroundError}
                onChange={onThemeCustomizationChange}
                onReset={onResetThemeCustomization}
              />
            </section>

            <section className="mt-6">
              <h2 className="text-base font-semibold">
                <Globe className="mr-1.5 inline-block h-4 w-4 align-text-bottom" />
                {t("settings.language")}
              </h2>
              <p className="mt-1 text-sm text-muted-foreground">{t("settings.languageDesc")}</p>
              <div className="mt-4 flex flex-wrap gap-2">
                {SUPPORTED_LOCALES.map((loc) => (
                  <button
                    key={loc.code}
                    type="button"
                    aria-pressed={currentLanguage === loc.code}
                    onClick={() => onLocaleChange(loc.code)}
                    className={cn(
                      "rounded-md border px-3 py-1.5 text-sm font-medium transition-colors",
                      currentLanguage === loc.code
                        ? "border-primary bg-primary/10 text-primary"
                        : "border-border bg-card text-foreground hover:bg-accent",
                    )}
                  >
                    {loc.label}
                  </button>
                ))}
              </div>
            </section>

            <section className="mt-6">
              <label className="block text-sm font-semibold" htmlFor="settings-running-enter-action">
                {t("settings.runningEnterAction")}
              </label>
              <select
                id="settings-running-enter-action"
                className="mt-3 max-w-full rounded-md border border-border bg-card px-3 py-2 text-sm text-foreground focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring"
                value={runningEnterAction}
                onChange={(event) => onRunningEnterActionChange(event.target.value === "steer" ? "steer" : "queue")}
              >
                <option value="queue">{t("settings.runningEnterQueue")}</option>
                <option value="steer">{t("settings.runningEnterSteer")}</option>
              </select>
            </section>

            <section className="mt-6">
              <BackgroundGoalSettings
                enabled={backgroundGoalNotificationsEnabled}
                saving={savingAdvancedSettings}
                runtimeConnected={runtimeConnected}
                onChange={onBackgroundGoalNotificationsChange}
              />
            </section>

            <section className="mt-6">
              <AgentQuestionNotificationSoundSettings />
            </section>

            <section className="mt-6">
                <DiagnosticsSettingsPanel
                endpoint={endpoint}
                developerOptionsEnabled={developerOptionsEnabled}
                status={diagnosticsStatus}
                message={diagnosticsMessage}
                loading={loadingDiagnostics}
                exportingSupportBundle={exportingSupportBundle}
                onLogLevelChange={onSetLogLevel}
                onOpenLogsFolder={onOpenLogsFolder}
                onCreateSupportBundle={onCreateSupportBundle}
              />
            </section>

            <section className="mt-6" data-vrcforge-about>
              <h2 className="text-base font-semibold">{t("settings.aboutTitle")}</h2>
              <div className="mt-4 rounded-xl border border-border bg-card p-4">
                <div className="text-sm font-semibold">{t("settings.aboutProduct", { version: appVersion })}</div>
                <div className="mt-2 text-sm text-muted-foreground">{t("settings.aboutMcp")}</div>
                <div className="mt-2 text-sm text-muted-foreground">{t("settings.aboutSafety")}</div>
                <div className="mt-2 text-xs text-muted-foreground">{t("settings.aboutLicense")}</div>
                <div className="mt-4 border-t border-border pt-4">
                  <h3 className="text-sm font-semibold">{t("settings.onboarding")}</h3>
                  <p className="mt-1 text-sm text-muted-foreground">{t("settings.onboardingDesc")}</p>
                  <div className="mt-4">
                    <Button type="button" variant="outline" onClick={onRestartOnboarding}>
                      <RefreshCw className="mr-1 h-4 w-4" />
                      {t("settings.restartOnboarding")}
                    </Button>
                  </div>
                </div>
              </div>
            </section>

            <section className="mt-6 pb-6">
              <DeveloperOptionsControl
                endpoint={endpoint}
                enabled={developerOptionsEnabled}
                everEnabled={developerOptionsEverEnabled}
                saving={savingAdvancedSettings}
                onChange={updateDeveloperOptions}
              />
            </section>
          </>
        ) : null}

        {visibleSection === "permissions" ? (
        <section className="pb-6">
          <div className="flex min-w-0 items-center gap-2">
            <h2 className="truncate text-base font-semibold">{t("settings.permissionMode")}</h2>
            <Badge tone={currentPermissionVisual.badgeTone} className="shrink-0">
              {t("settings.currentMode", { mode: executionModeLabel(permission?.executionMode) })}
            </Badge>
          </div>
          <p className="mt-1 text-sm text-muted-foreground">{t("settings.permissionModeDescription")}</p>
          <div className="mt-4 grid gap-3">
            {EXECUTION_MODES.map((mode) => {
              const modeVisual = permissionVisualState(undefined, mode.value);
              const selected = permission?.executionMode === mode.value;
              return (
                <button
                  key={mode.value}
                  type="button"
                  disabled={loading || !runtimeConnected}
                  onClick={() => onSwitchMode(mode.value)}
                  className={cn(
                    "grid min-w-0 gap-1 rounded-xl border px-4 py-3 text-left transition-colors disabled:opacity-60",
                    selected ? modeVisual.selectedClass : cn("border-border", modeVisual.hoverClass),
                  )}
                >
                  <div className="flex min-w-0 items-center gap-2">
                    <span className={cn("truncate text-sm font-medium", modeVisual.textClass)}>{mode.label}</span>
                    {mode.value === "roslyn_full_auto" ? (
                      <Badge tone={modeVisual.badgeTone} className="shrink-0">
                        {t("settings.highRisk")}
                      </Badge>
                    ) : null}
                    {mode.value === "roslyn_full_auto" && !selected && (permission?.roslynFullAutoEverEnabled || permission?.roslynRiskAcknowledged) ? (
                      <Badge tone="muted" className="shrink-0">
                        {t("settings.everEnabled")}
                      </Badge>
                    ) : null}
                    {selected ? <Check className={cn("ml-auto h-4 w-4 shrink-0", modeVisual.textClass)} /> : null}
                  </div>
                  <div className="text-xs text-muted-foreground">{mode.description}</div>
                </button>
              );
            })}
          </div>
        </section>
        ) : null}

        {visibleSection === "models" ? (
        <section className="pb-6">
          <h1 className="text-xl font-semibold text-foreground">{t("settings.modelProvider")}</h1>
          <p className="mt-1 text-sm text-muted-foreground">{t("settings.providerDesc")}</p>
          {providerConfigurationLocked ? <p role="status" className="mt-2 text-sm text-muted-foreground">{t("provider.runtimeConfigLocked")}</p> : null}
          <div className="mt-4 flex w-fit max-w-full flex-wrap gap-1 rounded-lg border border-border bg-muted/40 p-1" role="group" aria-label={t("settings.modelProvider")}>
            {(["subscription", "api"] as const).map((panel) => (
              <Button
                key={panel}
                type="button"
                variant="ghost"
                aria-pressed={visibleProvider === panel}
                aria-controls={panel === "subscription" ? "subscription-model-settings" : "api-model-settings"}
                onClick={() => setProviderPanel(panel)}
                className={cn("h-auto min-h-9 min-w-0 whitespace-normal px-3 py-1.5 text-left focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring", visibleProvider === panel && "bg-card text-primary shadow-sm hover:bg-card")}
              >
                {t(panel === "subscription" ? "chatgptOAuth.title" : "provider.apiProvider")}
              </Button>
            ))}
          </div>
          <fieldset disabled={providerConfigurationLocked} className="mt-4 min-w-0 disabled:opacity-60">
            <div id="subscription-model-settings" hidden={visibleProvider !== "subscription"}
              data-onboarding-provider={visibleProvider === "subscription" && apiProvider === "chatgpt" ? "connection" : undefined}>
              <ChatGptOAuthSettings
                endpoint={endpoint}
                runtimeConnected={runtimeConnected}
                apiProvider={apiProvider}
                apiModel={apiModel}
                savingApiConfig={savingApiConfig}
                testingProvider={testingProvider}
                providerTestMessage={providerTestMessage}
                providerTestPassed={providerTestPassed}
                developerOptionsEnabled={developerOptionsEnabled}
                onTestProvider={onProviderTest}
                onProviderConfigChanged={onProviderConfigChanged}
              />
            </div>
            <div id="api-model-settings" hidden={visibleProvider !== "api"}>
              {visibleProvider === "api" ? (
                <ProviderSetup
                  provider={apiProvider}
                  apiKey={apiKey}
                  baseUrl={apiBaseUrl}
                  model={apiModel}
                  apiType={apiType}
                  contextWindow={apiContextWindow}
                  modelCapabilities={selectedModelCapabilities}
                  capabilitySource={selectedModelCapabilitySource}
                  thinkingLevel={apiThinkingLevel}
                  reasoningVariants={reasoningVariants}
                  saving={savingApiConfig}
                  models={modelOptions}
                  loadingModels={loadingModels}
                  modelsError={modelsError}
                  testingProvider={testingProvider}
                  providerTestMessage={providerTestMessage}
                  runtimeConnected={runtimeConnected}
                  keySaved={apiKeySaved}
                  providerPickerOnly={apiProvider === "chatgpt"}
                  onLoadModels={onLoadModels}
                  onTestProvider={onProviderTest}
                  onProviderChange={onProviderChange}
                  onApiKeyChange={onApiKeyChange}
                  onBaseUrlChange={onApiBaseUrlChange}
                  onModelChange={onApiModelChange}
                  onProtocolAutoNegotiationChange={onProtocolAutoNegotiationChange}
                  onApiTypeChange={onApiTypeChange}
                  onContextWindowChange={onApiContextWindowChange}
                  onThinkingLevelChange={onApiThinkingLevelChange}
                  onSubmit={onSaveApiProvider}
                />
              ) : null}
            </div>
          </fieldset>
          <details className="group mt-6 rounded-xl border border-border bg-card px-4">
            <summary className="flex min-w-0 cursor-pointer list-none flex-wrap items-center gap-2 rounded-md py-3 text-sm font-semibold focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring [&::-webkit-details-marker]:hidden">
              <Eye className="h-4 w-4 shrink-0 text-muted-foreground" aria-hidden="true" />
              <span>{t("settings.visionProfile")}</span>
              <Badge tone={visionConfig?.configured && visionConfig.enabled ? "ok" : "muted"} className="h-6 shrink-0">
                {t(visionConfig?.configured && visionConfig.enabled ? "vision.statusActive" : "settings.visionUsesChatModel")}
              </Badge>
              <ChevronDown className="ml-auto h-4 w-4 shrink-0 text-muted-foreground group-open:rotate-180" aria-hidden="true" />
            </summary>
            <div className="border-t border-border py-4">
              <p className="mb-4 text-sm text-muted-foreground">{t("settings.visionProfileDesc")}</p>
              <VisionProfileSetup
                provider={visionProvider}
                apiKey={visionApiKey}
                baseUrl={visionBaseUrl}
                model={visionModel}
                enabled={visionEnabled}
                saving={savingVisionConfig}
                runtimeConnected={runtimeConnected}
                keySaved={visionKeySaved}
                configured={Boolean(visionConfig?.configured)}
                onProviderChange={onVisionProviderChange}
                onApiKeyChange={onVisionApiKeyChange}
                onBaseUrlChange={onVisionBaseUrlChange}
                onModelChange={onVisionModelChange}
                onEnabledChange={onVisionEnabledChange}
                onSubmit={onSaveVisionProfile}
                onClear={onClearVisionProfile}
              />
            </div>
          </details>
        </section>
        ) : null}

        {visibleSection === "developer" && developerOptionsEnabled ? (
        <section className="pb-6">
          <div className="flex min-w-0 items-center gap-2">
            <h2 className="truncate text-base font-semibold">{t("settings.computerUse")}</h2>
            <Badge tone={computerUseEnabled ? "warn" : "muted"} className="shrink-0">
              {computerUseEnabled ? t("settings.enabled") : t("connector.off")}
            </Badge>
            {!computerUseEnabled && computerUseEverEnabled ? (
              <Badge tone="muted" className="shrink-0">
                {t("settings.everEnabled")}
              </Badge>
            ) : null}
          </div>
          <p className="mt-1 text-sm text-muted-foreground">{t("settings.computerUseDesc")}</p>
          <div className="mt-4 rounded-lg border border-border bg-card p-4">
            <div className="flex min-w-0 flex-wrap items-center gap-3">
              <div className="min-w-0 flex-1">
                <div className="text-sm font-medium">{t("settings.computerUseExplicitOnly")}</div>
                <div className="mt-1 text-xs text-muted-foreground">{t("settings.computerUseRisk")}</div>
              </div>
              <Button
                type="button"
                variant={computerUseEnabled ? "outline" : "primary"}
                disabled={savingAdvancedSettings || !runtimeConnected}
                onClick={() => onComputerUseChange(!computerUseEnabled)}
              >
                {savingAdvancedSettings ? <Loader2 className="h-4 w-4 animate-spin" /> : null}
                {computerUseEnabled ? t("settings.turnOff") : t("settings.turnOn")}
              </Button>
            </div>
          </div>
          <div className="mt-6">
            <DiagnosticIdentityMap status={diagnosticsStatus} />
          </div>
          <div className="mt-6 rounded-lg border border-border bg-card p-4">
            <div className="text-sm font-medium">{t("settings.compactDebugTitle")}</div>
            <div className="mt-1 text-xs text-muted-foreground">{t("settings.compactDebugDesc")}</div>
            <div className="mt-3 space-y-2">
              {compactDebugEntries.length ? (
                compactDebugEntries.map((entry) => (
                  <details key={entry.id} className="rounded-md border border-border bg-background p-3 text-xs">
                    <summary className="cursor-pointer font-medium">
                      {t("settings.compactDebugEntry", { count: entry.entryCount ?? 0, time: entry.createdAt || "-" })}
                    </summary>
                    <pre className="app-scrollbar mt-2 max-h-64 overflow-auto whitespace-pre-wrap break-words text-muted-foreground">
                      {entry.text}
                    </pre>
                  </details>
                ))
              ) : (
                <div className="text-xs text-muted-foreground">{t("settings.compactDebugEmpty")}</div>
              )}
            </div>
          </div>
        </section>
        ) : null}

        <section className="pb-6" hidden={visibleSection !== "memory"}>
          <MemoryReviewSettings
            endpoint={endpoint}
            runtimeConnected={runtimeConnected}
            selectedProjectPath={selectedProjectPath}
            refreshSignal={memoryReviewRefreshSignal}
          />
        </section>

        {visibleSection === "storage" ? (
        <section className="pb-6">
          <CheckpointStoragePanel
            status={connectorStatus}
            loading={loadingConnectors}
            isDesktop={isDesktop}
            limitInput={checkpointArchiveLimitInput}
            countInput={checkpointArchiveCountInput}
            onLimitInputChange={onCheckpointArchiveLimitInputChange}
            onCountInputChange={onCheckpointArchiveCountInputChange}
            onSaveLimit={onSaveCheckpointArchiveLimit}
            onOpenFolder={onOpenCheckpointArchiveFolder}
            onPickDirectory={onPickCheckpointArchiveDirectory}
            onDeleteSelected={onDeleteCheckpointArchives}
            onRelocate={onRelocateCheckpointArchives}
          />
        </section>
        ) : null}

        {visibleSection === "storage" ? (
        <section className="pb-6">
          <DataRootSettingsPanel endpoint={endpoint} />
        </section>
        ) : null}

        {visibleSection === "connectors" ? (
        <section className="grid gap-6 pb-6">
          <ExternalAgentConnectorsPanel
            status={connectorStatus}
            loading={loadingConnectors}
            runtimeConnected={runtimeConnected}
            message={connectorMessage}
            selectedProjectPath={selectedProjectPath}
            onRefresh={onLoadConnectors}
            onToggleGateway={(enabled) => onUpdateGatewaySettings({ enabled })}
            onToggleWriteRequests={(allowWriteRequests) => onUpdateGatewaySettings({ allowWriteRequests })}
            onRevoke={() => onUpdateGatewaySettings({ revokeToken: true })}
            onInstall={(client, configPath) => onRunConnectorAction(client, "install", configPath)}
            onUninstall={(client, configPath) => onRunConnectorAction(client, "uninstall", configPath)}
            onCopy={onCopyConnectorText}
          />
          <details className="rounded-lg border border-border bg-card p-3">
            <summary className="cursor-pointer text-sm font-semibold">{t("connector.telegramRemoteAccess")}</summary>
            <div className="mt-4">
              <TelegramSettingsPanel endpoint={endpoint} selectedProjectPath={selectedProjectPath} />
            </div>
          </details>
        </section>
        ) : null}

        {visibleSection === "instructions" ? (
        <section className="pb-6">
          <div className="flex min-w-0 items-center gap-2">
            <h2 className="truncate text-base font-semibold">{t("settings.customInstructions")}</h2>
            {notesMessage ? (
              <Badge tone="ok" className="shrink-0">
                {notesMessage}
              </Badge>
            ) : null}
          </div>
          <p className="mt-1 text-sm text-muted-foreground">{t("settings.customInstructionsDesc")}</p>
          {agentNotesPath ? <p className="mt-1 truncate text-xs text-muted-foreground/70">{agentNotesPath}</p> : null}
          <form onSubmit={onSaveNotes} className="mt-4">
            <textarea
              value={agentNotes}
              onChange={(event) => onAgentNotesChange(event.target.value)}
              disabled={!agentNotesLoaded}
              placeholder={agentNotesLoaded ? t("settings.customInstructionsPlaceholder") : t("settings.customInstructionsDisabled")}
              className="min-h-56 w-full resize-y rounded-xl border border-border bg-background px-4 py-3 text-sm leading-relaxed outline-none focus:border-primary disabled:bg-muted"
            />
            <div className="mt-3 flex justify-end">
              <Button type="submit" disabled={savingNotes || !agentNotesLoaded}>
                {savingNotes ? <Loader2 className="h-4 w-4 animate-spin" /> : null}
                {t("common.save")}
              </Button>
            </div>
          </form>
        </section>
        ) : null}
      </div>
    </div>
  );
}
