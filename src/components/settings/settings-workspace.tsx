import { Check, Download, Eye, Globe, Loader2, RefreshCw } from "lucide-react";
import type { FormEvent } from "react";
import { useTranslation } from "react-i18next";
import { SUPPORTED_LOCALES } from "../../i18n";
import type {
  DiagnosticsStatus,
  ExecutionMode,
  ExternalAgentConnectorClient,
  ExternalAgentConnectorStatus,
  PermissionState,
  ProviderModelInfo,
  VisionConfig,
} from "../../lib/api";
import { EXECUTION_MODES, executionModeLabel, permissionVisualState } from "../../lib/permission-ui";
import { cn } from "../../lib/utils";
import { Badge } from "../ui/badge";
import { Button } from "../ui/button";
import { CheckpointStoragePanel } from "./checkpoint-storage-panel";
import { ExternalAgentConnectorsPanel } from "./external-agent-connectors-panel";
import { ProviderSetup, VisionProfileSetup } from "./provider-settings";

type SettingsWorkspaceProps = {
  permission: PermissionState | null;
  loading: boolean;
  runtimeConnected: boolean;
  currentLanguage: string;
  apiProvider: string;
  apiKey: string;
  apiBaseUrl: string;
  apiModel: string;
  apiKeySaved: boolean;
  savingApiConfig: boolean;
  modelOptions: ProviderModelInfo[];
  loadingModels: boolean;
  modelsError: string;
  testingProvider: string;
  providerTestMessage: string;
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
  isDesktop: boolean;
  checkpointArchiveLimitInput: string;
  agentNotes: string;
  agentNotesLoaded: boolean;
  agentNotesPath: string;
  notesMessage: string;
  savingNotes: boolean;
  onSwitchMode: (mode: ExecutionMode) => void;
  onRestartOnboarding: () => void;
  onLocaleChange: (code: string) => void;
  onLoadModels: () => void;
  onProviderTest: (capability: "text" | "structured" | "vision") => void;
  onProviderChange: (value: string) => void;
  onApiKeyChange: (value: string) => void;
  onApiBaseUrlChange: (value: string) => void;
  onApiModelChange: (value: string) => void;
  onSaveApiProvider: (event?: FormEvent) => void;
  onVisionProviderChange: (value: string) => void;
  onVisionApiKeyChange: (value: string) => void;
  onVisionBaseUrlChange: (value: string) => void;
  onVisionModelChange: (value: string) => void;
  onVisionEnabledChange: (value: boolean) => void;
  onSaveVisionProfile: (event?: FormEvent) => void;
  onClearVisionProfile: () => void;
  onSetDebugLogging: (enabled: boolean) => void;
  onCreateSupportBundle: () => void;
  onCheckpointArchiveLimitInputChange: (value: string) => void;
  onSaveCheckpointArchiveLimit: () => void;
  onOpenCheckpointArchiveFolder: (targetPath: string) => void;
  onPickCheckpointArchiveDirectory: (currentPath: string) => Promise<string>;
  onDeleteCheckpointArchives: (ids: string[]) => void;
  onRelocateCheckpointArchives: (directory: string) => void;
  onLoadConnectors: () => void;
  onUpdateGatewaySettings: (settings: { enabled?: boolean; allowWriteRequests?: boolean; revokeToken?: boolean }) => void;
  onRunConnectorAction: (client: ExternalAgentConnectorClient, action: "install" | "uninstall") => void;
  onCopyConnectorText: (text: string, label: string) => void;
  onAgentNotesChange: (value: string) => void;
  onSaveNotes: (event: FormEvent) => void;
};

export function SettingsWorkspace({
  permission,
  loading,
  runtimeConnected,
  currentLanguage,
  apiProvider,
  apiKey,
  apiBaseUrl,
  apiModel,
  apiKeySaved,
  savingApiConfig,
  modelOptions,
  loadingModels,
  modelsError,
  testingProvider,
  providerTestMessage,
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
  isDesktop,
  checkpointArchiveLimitInput,
  agentNotes,
  agentNotesLoaded,
  agentNotesPath,
  notesMessage,
  savingNotes,
  onSwitchMode,
  onRestartOnboarding,
  onLocaleChange,
  onLoadModels,
  onProviderTest,
  onProviderChange,
  onApiKeyChange,
  onApiBaseUrlChange,
  onApiModelChange,
  onSaveApiProvider,
  onVisionProviderChange,
  onVisionApiKeyChange,
  onVisionBaseUrlChange,
  onVisionModelChange,
  onVisionEnabledChange,
  onSaveVisionProfile,
  onClearVisionProfile,
  onSetDebugLogging,
  onCreateSupportBundle,
  onCheckpointArchiveLimitInputChange,
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
  const currentPermissionVisual = permissionVisualState(permission);
  const visionKeySaved = Boolean(visionConfig?.apiKeyPresent && (visionConfig?.provider || "") === visionProvider);

  return (
    <div className="app-scrollbar min-h-0 flex-1 overflow-y-auto px-6 py-10">
      <div className="mx-auto w-full max-w-3xl">
        <h1 className="text-2xl font-semibold tracking-tight">{t("sidebar.settings")}</h1>
        <p className="mt-1 text-sm text-muted-foreground">{t("settings.subtitle")}</p>

        <section className="mt-10">
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
                    {selected ? <Check className={cn("ml-auto h-4 w-4 shrink-0", modeVisual.textClass)} /> : null}
                  </div>
                  <div className="text-xs text-muted-foreground">{mode.description}</div>
                </button>
              );
            })}
          </div>
        </section>

        <section className="mt-12">
          <h2 className="text-base font-semibold">{t("settings.onboarding")}</h2>
          <p className="mt-1 text-sm text-muted-foreground">{t("settings.onboardingDesc")}</p>
          <div className="mt-4">
            <Button type="button" variant="outline" onClick={onRestartOnboarding}>
              <RefreshCw className="mr-1 h-4 w-4" />
              {t("settings.restartOnboarding")}
            </Button>
          </div>
        </section>

        <section className="mt-12">
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

        <section className="mt-12">
          <h2 className="text-base font-semibold">{t("settings.modelProvider")}</h2>
          <p className="mt-1 text-sm text-muted-foreground">{t("settings.providerDesc")}</p>
          <div className="mt-4">
            <ProviderSetup
              provider={apiProvider}
              apiKey={apiKey}
              baseUrl={apiBaseUrl}
              model={apiModel}
              saving={savingApiConfig}
              models={modelOptions}
              loadingModels={loadingModels}
              modelsError={modelsError}
              testingProvider={testingProvider}
              providerTestMessage={providerTestMessage}
              runtimeConnected={runtimeConnected}
              keySaved={apiKeySaved}
              onLoadModels={onLoadModels}
              onTestProvider={onProviderTest}
              onProviderChange={onProviderChange}
              onApiKeyChange={onApiKeyChange}
              onBaseUrlChange={onApiBaseUrlChange}
              onModelChange={onApiModelChange}
              onSubmit={onSaveApiProvider}
            />
          </div>
        </section>

        <section className="mt-12">
          <div className="flex min-w-0 items-center gap-2">
            <h2 className="text-base font-semibold">
              <Eye className="mr-1.5 inline-block h-4 w-4 align-text-bottom" />
              {t("settings.visionProfile")}
            </h2>
            {visionConfig?.configured ? (
              <Badge tone={visionConfig.enabled ? "ok" : "muted"} className="shrink-0">
                {visionConfig.enabled ? t("vision.statusActive") : t("vision.statusDisabled")}
              </Badge>
            ) : null}
          </div>
          <p className="mt-1 text-sm text-muted-foreground">{t("settings.visionProfileDesc")}</p>
          <div className="mt-4">
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
        </section>

        <section className="mt-12">
          <div className="flex min-w-0 items-center gap-2">
            <h2 className="truncate text-base font-semibold">{t("settings.diagnostics")}</h2>
            {diagnosticsMessage ? (
              <Badge tone="ok" className="shrink-0">
                {diagnosticsMessage}
              </Badge>
            ) : null}
          </div>
          <div className="mt-4 rounded-lg border border-border bg-card p-4">
            <div className="flex min-w-0 flex-wrap items-center gap-3">
              <div className="min-w-0 flex-1">
                <div className="truncate text-sm font-medium">Debug logging</div>
                <div className="mt-1 truncate text-xs text-muted-foreground">
                  {diagnosticsStatus?.debugLogging ? "Recording local API, MCP, agent, checkpoint, and runtime interactions" : t("connector.off")}
                </div>
              </div>
              <Badge tone={diagnosticsStatus?.debugLogging ? "warn" : "muted"} className="shrink-0">
                {diagnosticsStatus?.debugLogging ? "Debug on" : "Debug off"}
              </Badge>
              <Button
                type="button"
                variant={diagnosticsStatus?.debugLogging ? "outline" : "primary"}
                disabled={loadingDiagnostics}
                onClick={() => onSetDebugLogging(!diagnosticsStatus?.debugLogging)}
              >
                {loadingDiagnostics ? <Loader2 className="h-4 w-4 animate-spin" /> : null}
                {diagnosticsStatus?.debugLogging ? "Turn off" : "Turn on"}
              </Button>
              <Button type="button" variant="outline" disabled={exportingSupportBundle} onClick={onCreateSupportBundle}>
                {exportingSupportBundle ? <Loader2 className="h-4 w-4 animate-spin" /> : <Download className="h-4 w-4" />}
                Export Bundle
              </Button>
            </div>
            {diagnosticsStatus?.logsDir ? <div className="mt-3 truncate text-xs text-muted-foreground/70">{diagnosticsStatus.logsDir}</div> : null}
          </div>
        </section>

        <section className="mt-12">
          <CheckpointStoragePanel
            status={connectorStatus}
            loading={loadingConnectors}
            isDesktop={isDesktop}
            limitInput={checkpointArchiveLimitInput}
            onLimitInputChange={onCheckpointArchiveLimitInputChange}
            onSaveLimit={onSaveCheckpointArchiveLimit}
            onOpenFolder={onOpenCheckpointArchiveFolder}
            onPickDirectory={onPickCheckpointArchiveDirectory}
            onDeleteSelected={onDeleteCheckpointArchives}
            onRelocate={onRelocateCheckpointArchives}
          />
        </section>

        <section className="mt-12">
          <ExternalAgentConnectorsPanel
            status={connectorStatus}
            loading={loadingConnectors}
            message={connectorMessage}
            selectedProjectPath={selectedProjectPath}
            onRefresh={onLoadConnectors}
            onToggleGateway={(enabled) => onUpdateGatewaySettings({ enabled })}
            onToggleWriteRequests={(allowWriteRequests) => onUpdateGatewaySettings({ allowWriteRequests })}
            onRevoke={() => onUpdateGatewaySettings({ revokeToken: true })}
            onInstall={(client) => onRunConnectorAction(client, "install")}
            onUninstall={(client) => onRunConnectorAction(client, "uninstall")}
            onCopy={onCopyConnectorText}
          />
        </section>

        <section className="mt-12 pb-6">
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
      </div>
    </div>
  );
}
