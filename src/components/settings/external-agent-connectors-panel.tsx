import { Copy, Download, Loader2, RefreshCw, Shield, Trash2 } from "lucide-react";
import { useState } from "react";
import { useTranslation } from "react-i18next";
import type { ExternalAgentConnectorClient, ExternalAgentConnectorStatus } from "../../lib/api";
import { normalizeConnectorClient } from "../../lib/connector-ui";
import { cn } from "../../lib/utils";
import { Badge } from "../ui/badge";
import { Button } from "../ui/button";
import { DataLine } from "../ui/data-line";

const GENERIC_CONFIG_PATH_STORAGE_KEY = "vrcforge.genericMcpConfigPath";

function readStoredGenericConfigPath(): string {
  try {
    return window.localStorage.getItem(GENERIC_CONFIG_PATH_STORAGE_KEY) || "";
  } catch {
    return "";
  }
}

function storeGenericConfigPath(value: string) {
  try {
    if (value) {
      window.localStorage.setItem(GENERIC_CONFIG_PATH_STORAGE_KEY, value);
    } else {
      window.localStorage.removeItem(GENERIC_CONFIG_PATH_STORAGE_KEY);
    }
  } catch {
    // Persistence is best-effort; the input value still drives the action.
  }
}

function sameConnectorConfigPath(left: string | undefined, right: string): boolean {
  const normalize = (value: string) => value.trim().replace(/[/\\]+/g, "\\").replace(/\\+$/, "").toLocaleLowerCase();
  return Boolean(left && right && normalize(left) === normalize(right));
}

type ExternalAgentConnectorsPanelProps = {
  status: ExternalAgentConnectorStatus | null;
  loading: boolean;
  message: string;
  selectedProjectPath: string;
  onRefresh: () => void;
  onToggleGateway: (enabled: boolean) => void;
  onToggleWriteRequests: (enabled: boolean) => void;
  onRevoke: () => void;
  onInstall: (client: ExternalAgentConnectorClient, configPath?: string) => void;
  onUninstall: (client: ExternalAgentConnectorClient, configPath?: string) => void;
  onCopy: (text: string, label: string) => void;
};

export function ExternalAgentConnectorsPanel({
  status,
  loading,
  message,
  selectedProjectPath,
  onRefresh,
  onToggleGateway,
  onToggleWriteRequests,
  onRevoke,
  onInstall,
  onUninstall,
  onCopy,
}: ExternalAgentConnectorsPanelProps) {
  const { t } = useTranslation();
  const gateway = status?.gateway;
  const codexText = status?.clientConfigs?.codex?.text || "";
  const codexStdioText = status?.clientConfigs?.codexStdio?.text || "";
  const claudeText = status?.clientConfigs?.claudeCode?.text || "";
  const claudeStdioText = status?.clientConfigs?.claudeCodeStdio?.text || status?.clientConfigs?.claudeCowork?.text || "";
  const toolCount = status?.advertisedTools?.length ?? 0;
  const writeTargetCount = status?.writeTargets?.length ?? 0;
  const launcherArgs = status?.launcher?.stdioBridge?.args || [];
  const launcherCommand = [status?.launcher?.stdioBridge?.command, ...launcherArgs].filter(Boolean).join(" ");
  const smokeArgs = status?.launcher?.smoke?.args || [];
  const smokeLiveArgs = status?.launcher?.smoke?.liveWriteRollbackArgs || [];
  const smokeCommand = [status?.launcher?.smoke?.command, ...smokeArgs, ...smokeLiveArgs].filter(Boolean).join(" ");
  const genericStdioText = status?.clientConfigs?.generic?.text || claudeStdioText;
  const genericHttpText = status?.clientConfigs?.genericHttp?.text || claudeText;
  const clients = status?.clients;
  const lastAction = status?.lastConnectorAction;
  const connectorRows: Array<{
    client: ExternalAgentConnectorClient;
    title: string;
    mode: string;
    copyText: string;
    copyLabel: string;
    shared?: string;
  }> = [
    {
      client: "codexApp",
      title: "Codex App",
      mode: t("connector.userConfig"),
      copyText: codexStdioText,
      copyLabel: "Codex App config",
      shared: t("connector.sharedWithCli"),
    },
    {
      client: "codexCli",
      title: "Codex CLI",
      mode: t("connector.userConfig"),
      copyText: codexStdioText,
      copyLabel: "Codex CLI config",
      shared: t("connector.sharedWithApp"),
    },
    {
      client: "claudeCode",
      title: "Claude Code CLI",
      mode: t("connector.projectConfig"),
      copyText: claudeStdioText,
      copyLabel: "Claude Code config",
    },
    {
      client: "claudeCowork",
      title: "Claude Cowork App",
      mode: t("connector.desktopConfig"),
      copyText: claudeStdioText,
      copyLabel: "Claude Cowork config",
    },
  ];
  return (
    <div className="rounded-2xl border border-border bg-card p-5 shadow-composer">
      <div className="flex min-w-0 items-center gap-2">
        <Shield className="h-4 w-4 shrink-0 text-primary" />
        <h2 className="min-w-0 flex-1 truncate text-base font-semibold">{t("connector.title")}</h2>
        <Badge tone={gateway?.enabled ? "ok" : "muted"} className="shrink-0">
          {gateway?.enabled ? t("skills.enabled") : t("connector.disabled")}
        </Badge>
      </div>

      <div className="mt-4 grid gap-3">
        <DataLine label={t("connector.endpoint")} value={status?.mcp?.url || gateway?.mcpUrl || "http://127.0.0.1:8757/mcp"} mono />
        <DataLine label={t("connector.tokenEnv")} value={status?.auth?.tokenEnvVar || "VRCFORGE_AGENT_TOKEN"} mono />
        <DataLine label={t("connector.stdioBridge")} value={launcherCommand || "-"} mono />
        <DataLine label={t("connector.smoke")} value={smokeCommand || "-"} mono />
        <DataLine label={t("connector.tools")} value={`${toolCount} read tools / ${writeTargetCount} write-request targets`} />
        <DataLine label={t("connector.config")} value={gateway?.configPath || "-"} />
      </div>

      <div className="mt-5 grid gap-3 md:grid-cols-2">
        <ConnectorToggle
          label={t("connector.gateway")}
          checked={Boolean(gateway?.enabled)}
          disabled={loading || !status}
          onChange={onToggleGateway}
        />
        <ConnectorToggle
          label={t("connector.writeRequests")}
          checked={Boolean(gateway?.allowWriteRequests)}
          disabled={loading || !status}
          onChange={onToggleWriteRequests}
        />
      </div>

      <div className="mt-5 grid gap-3">
        {connectorRows.map((row) => (
          <ConnectorClientRow
            key={row.client}
            client={row.client}
            title={row.title}
            mode={row.mode}
            state={clients?.[row.client]}
            loading={loading}
            copyText={row.copyText}
            copyLabel={row.copyLabel}
            shared={row.shared}
            selectedProjectPath={selectedProjectPath}
            lastAction={lastAction}
            onInstall={onInstall}
            onUninstall={onUninstall}
            onCopy={onCopy}
          />
        ))}
        <GenericConnectorRow
          loading={loading}
          state={clients?.generic}
          lastAction={lastAction}
          stdioText={genericStdioText}
          httpText={genericHttpText}
          onInstall={onInstall}
          onUninstall={onUninstall}
          onCopy={onCopy}
        />
      </div>

      <div className="mt-5 flex flex-wrap justify-end gap-2">
        {message ? (
          <Badge tone={lastAction?.ok === false ? "danger" : "ok"} className="mr-auto shrink-0">
            {message}
          </Badge>
        ) : null}
        <Button type="button" variant="outline" disabled={loading} onClick={onRefresh}>
          {loading ? <Loader2 className="h-4 w-4 animate-spin" /> : <RefreshCw className="h-4 w-4" />}
          Refresh
        </Button>
        <Button type="button" variant="outline" disabled={!codexText} onClick={() => onCopy(codexText, "Codex HTTP config")}>
          <Copy className="h-4 w-4" />
          {t("connector.codexHttp")}
        </Button>
        <Button type="button" variant="outline" disabled={!claudeText} onClick={() => onCopy(claudeText, "Claude HTTP config")}>
          <Copy className="h-4 w-4" />
          {t("connector.claudeHttp")}
        </Button>
        <Button type="button" variant="danger" disabled={loading || !status} onClick={onRevoke}>
          {t("connector.revokeToken")}
        </Button>
      </div>

      {status?.lastCalls?.length ? (
        <div className="mt-5 overflow-hidden rounded-lg border border-border">
          <div className="grid grid-cols-[minmax(0,1fr)_minmax(0,1fr)_120px] gap-2 border-b border-border bg-muted/40 px-3 py-2 text-xs font-medium text-muted-foreground">
            <span className="truncate">{t("connector.event")}</span>
            <span className="truncate">{t("proof.tool")}</span>
            <span className="truncate">{t("connector.status")}</span>
          </div>
          {status.lastCalls.slice(0, 8).map((call, index) => (
            <div
              key={`${call.event}-${call.createdAt}-${index}`}
              className="grid grid-cols-[minmax(0,1fr)_minmax(0,1fr)_120px] gap-2 border-b border-border/60 px-3 py-2 text-xs last:border-b-0"
            >
              <span className="truncate">{call.event || "-"}</span>
              <span className="truncate font-mono">{call.targetTool || "-"}</span>
              <span className="truncate">{call.status || call.riskLevel || "-"}</span>
            </div>
          ))}
        </div>
      ) : null}
    </div>
  );
}

type ConnectorClientState = NonNullable<ExternalAgentConnectorStatus["clients"]>[ExternalAgentConnectorClient];

function ConnectorClientRow({
  client,
  title,
  mode,
  state,
  loading,
  copyText,
  copyLabel,
  shared,
  selectedProjectPath,
  lastAction,
  onInstall,
  onUninstall,
  onCopy,
}: {
  client: ExternalAgentConnectorClient;
  title: string;
  mode: string;
  state?: ConnectorClientState;
  loading: boolean;
  copyText: string;
  copyLabel: string;
  shared?: string;
  selectedProjectPath: string;
  lastAction?: ExternalAgentConnectorStatus["lastConnectorAction"];
  onInstall: (client: ExternalAgentConnectorClient) => void;
  onUninstall: (client: ExternalAgentConnectorClient) => void;
  onCopy: (text: string, label: string) => void;
}) {
  const { t } = useTranslation();
  const installed = Boolean(state?.installed);
  const needsProject = client === "claudeCode" && !selectedProjectPath;
  const installable = state?.installable !== false && !needsProject;
  const installActionDisabled = loading || !state;
  const actionMatches = normalizeConnectorClient(lastAction?.client) === client;
  const action = actionMatches ? lastAction : undefined;
  const handshake = action?.handshake;
  const statusTone = installed ? "ok" : installable ? "muted" : "warn";
  const statusLabel = installed ? t("connector.installed") : needsProject ? t("connector.needsProject") : installable ? t("connector.notInstalled") : t("connector.needsAttention");
  return (
    <div className="grid min-w-0 gap-3 rounded-lg border border-border bg-background/40 p-3 md:grid-cols-[minmax(0,1fr)_auto]">
      <div className="min-w-0">
        <div className="flex min-w-0 flex-wrap items-center gap-2">
          <span className="min-w-0 truncate text-sm font-semibold">{title}</span>
          <Badge tone={statusTone} className="shrink-0">
            {statusLabel}
          </Badge>
          <Badge tone="muted" className="shrink-0">
            {mode}
          </Badge>
          {shared ? (
            <Badge tone="muted" className="shrink-0">
              {shared}
            </Badge>
          ) : null}
          {state?.cliDetected !== null && state?.cliDetected !== undefined ? (
            <Badge tone={state.cliDetected ? "ok" : "muted"} className="shrink-0">
              CLI {state.cliDetected ? "found" : "not found"}
            </Badge>
          ) : null}
          {state?.appDetected !== null && state?.appDetected !== undefined ? (
            <Badge tone={state.appDetected ? "ok" : "muted"} className="shrink-0">
              App {state.appDetected ? "found" : "not found"}
            </Badge>
          ) : null}
        </div>
        <div className="mt-2 grid gap-1 text-xs text-muted-foreground">
          <div className="min-w-0 truncate">
            <span className="mr-2 text-foreground/70">{t("connector.config")}</span>
            <span className="font-mono">{state?.configPath || "-"}</span>
          </div>
          {state?.cliPath ? (
            <div className="min-w-0 truncate">
              <span className="mr-2 text-foreground/70">CLI</span>
              <span className="font-mono">{state.cliPath}</span>
              {state.cliSource ? <span className="ml-2">({state.cliSource})</span> : null}
            </div>
          ) : null}
          {state?.cliError ? <div className="break-words text-amber-700 dark:text-amber-300">{state.cliError}</div> : null}
          {state?.appError ? <div className="break-words text-amber-700 dark:text-amber-300">{state.appError}</div> : null}
          {state?.lastError ? <div className="text-amber-700 dark:text-amber-300">{state.lastError}</div> : null}
          {needsProject ? (
            <div className="text-amber-700 dark:text-amber-300">{t("connector.needsProjectHint")}</div>
          ) : !installable ? (
            <div className="text-amber-700 dark:text-amber-300">{t("connector.notInstallableHint")}</div>
          ) : null}
          {action ? (
            <div
              className={cn(
                "mt-1 grid gap-1 rounded-md px-2 py-1.5",
                action.ok ? "bg-emerald-500/10 text-emerald-700 dark:text-emerald-300" : "bg-destructive/10 text-destructive",
              )}
            >
              <div className="flex min-w-0 flex-wrap items-center gap-2">
                <span className="font-medium">{action.ok ? t("connector.selfTestPassed") : t("connector.selfTestFailed")}</span>
                {handshake?.toolCount !== undefined ? <span>{handshake.toolCount} tools</span> : null}
                {handshake?.connected ? <span>{t("connector.connected")}</span> : null}
                {handshake?.ready ? <span>{t("connector.ready")}</span> : null}
              </div>
              {action.error ? <div className="break-words">{action.error}</div> : null}
              {handshake?.warning ? <div className="break-words">{handshake.warning}</div> : null}
              {action.suggestion || handshake?.suggestion ? <div className="break-words">{action.suggestion || handshake?.suggestion}</div> : null}
              {action.backupPath ? <div className="truncate font-mono text-[11px]">Backup {action.backupPath}</div> : null}
            </div>
          ) : null}
        </div>
      </div>
      <div className="flex flex-wrap items-start justify-end gap-2">
        <Button type="button" variant="outline" className="h-8 px-3 text-xs" disabled={loading || !copyText} onClick={() => onCopy(copyText, copyLabel)}>
          <Copy className="h-3.5 w-3.5" />
          {t("connector.copy")}
        </Button>
        <Button type="button" variant="outline" className="h-8 px-3 text-xs" disabled={installActionDisabled} onClick={() => onInstall(client)}>
          {loading ? <Loader2 className="h-3.5 w-3.5 animate-spin" /> : <Download className="h-3.5 w-3.5" />}
          Install
        </Button>
        <Button type="button" variant="danger" className="h-8 px-3 text-xs" disabled={loading || !installed} onClick={() => onUninstall(client)}>
          <Trash2 className="h-3.5 w-3.5" />
          {t("connector.remove")}
        </Button>
      </div>
    </div>
  );
}

function GenericConnectorRow({
  loading,
  state,
  lastAction,
  stdioText,
  httpText,
  onInstall,
  onUninstall,
  onCopy,
}: {
  loading: boolean;
  state?: ConnectorClientState;
  lastAction?: ExternalAgentConnectorStatus["lastConnectorAction"];
  stdioText: string;
  httpText: string;
  onInstall: (client: ExternalAgentConnectorClient, configPath?: string) => void;
  onUninstall: (client: ExternalAgentConnectorClient, configPath?: string) => void;
  onCopy: (text: string, label: string) => void;
}) {
  const { t } = useTranslation();
  const [configPath, setConfigPath] = useState(readStoredGenericConfigPath);
  const trimmedPath = configPath.trim();
  const statusMatchesCurrent = sameConnectorConfigPath(state?.requestedConfigPath || state?.configPath, trimmedPath);
  const actionMatches =
    normalizeConnectorClient(lastAction?.client) === "generic" &&
    statusMatchesCurrent &&
    (sameConnectorConfigPath(lastAction?.configPath, trimmedPath) || sameConnectorConfigPath(lastAction?.configPath, state?.configPath || ""));
  const action = actionMatches ? lastAction : undefined;
  const handshake = action?.handshake;
  const installedHere = Boolean(state?.installed && statusMatchesCurrent);
  return (
    <div className="grid min-w-0 gap-3 rounded-lg border border-border bg-background/40 p-3 md:grid-cols-[minmax(0,1fr)_auto]">
      <div className="min-w-0">
        <div className="flex min-w-0 flex-wrap items-center gap-2">
          <span className="min-w-0 truncate text-sm font-semibold">{t("connector.genericTitle")}</span>
          <Badge tone={installedHere ? "ok" : state?.conflict ? "warn" : "muted"} className="shrink-0">
            {installedHere ? t("connector.installed") : t("connector.notInstalled")}
          </Badge>
          <Badge tone="muted" className="shrink-0">
            {t("connector.customConfig")}
          </Badge>
        </div>
        <div className="mt-2 grid gap-2 text-xs text-muted-foreground">
          <div className="break-words">{t("connector.genericHint")}</div>
          <input
            type="text"
            value={configPath}
            disabled={loading}
            placeholder={t("connector.genericPathPlaceholder")}
            onChange={(event) => {
              setConfigPath(event.target.value);
              storeGenericConfigPath(event.target.value.trim());
            }}
            className="h-9 w-full min-w-0 rounded-md border border-border bg-background px-2 font-mono text-xs text-foreground outline-none focus:border-primary"
          />
          {state?.lastError && statusMatchesCurrent ? (
            <div className="break-words text-amber-700 dark:text-amber-300">{state.lastError}</div>
          ) : null}
          {state?.restartInstruction ? <div className="break-words">{state.restartInstruction}</div> : null}
          {action ? (
            <div
              className={cn(
                "mt-1 grid gap-1 rounded-md px-2 py-1.5",
                action.ok ? "bg-emerald-500/10 text-emerald-700 dark:text-emerald-300" : "bg-destructive/10 text-destructive",
              )}
            >
              <div className="flex min-w-0 flex-wrap items-center gap-2">
                <span className="font-medium">{action.ok ? t("connector.selfTestPassed") : t("connector.selfTestFailed")}</span>
                {handshake?.toolCount !== undefined ? <span>{handshake.toolCount} tools</span> : null}
                {handshake?.connected ? <span>{t("connector.connected")}</span> : null}
                {handshake?.ready ? <span>{t("connector.ready")}</span> : null}
              </div>
              {action.configPath ? <div className="truncate font-mono text-[11px]">{action.configPath}</div> : null}
              {action.error ? <div className="break-words">{action.error}</div> : null}
              {handshake?.warning ? <div className="break-words">{handshake.warning}</div> : null}
              {action.suggestion || handshake?.suggestion ? <div className="break-words">{action.suggestion || handshake?.suggestion}</div> : null}
              {action.backupPath ? <div className="truncate font-mono text-[11px]">Backup {action.backupPath}</div> : null}
            </div>
          ) : null}
        </div>
      </div>
      <div className="flex flex-wrap items-start justify-end gap-2">
        <Button type="button" variant="outline" className="h-8 px-3 text-xs" disabled={loading || !stdioText} onClick={() => onCopy(stdioText, "Generic stdio config")}>
          <Copy className="h-3.5 w-3.5" />
          {t("connector.copyStdio")}
        </Button>
        <Button type="button" variant="outline" className="h-8 px-3 text-xs" disabled={loading || !httpText} onClick={() => onCopy(httpText, "Generic HTTP config")}>
          <Copy className="h-3.5 w-3.5" />
          {t("connector.copyHttp")}
        </Button>
        <Button type="button" variant="outline" className="h-8 px-3 text-xs" disabled={loading || !trimmedPath} onClick={() => onInstall("generic", trimmedPath)}>
          {loading ? <Loader2 className="h-3.5 w-3.5 animate-spin" /> : <Download className="h-3.5 w-3.5" />}
          Install
        </Button>
        <Button type="button" variant="danger" className="h-8 px-3 text-xs" disabled={loading || !installedHere} onClick={() => onUninstall("generic", trimmedPath)}>
          <Trash2 className="h-3.5 w-3.5" />
          {t("connector.remove")}
        </Button>
      </div>
    </div>
  );
}

function ConnectorToggle({
  label,
  checked,
  disabled,
  onChange,
}: {
  label: string;
  checked: boolean;
  disabled: boolean;
  onChange: (checked: boolean) => void;
}) {
  const { t } = useTranslation();
  return (
    <button
      type="button"
      disabled={disabled}
      onClick={() => onChange(!checked)}
      className={cn(
        "flex h-11 min-w-0 items-center gap-3 rounded-md border px-3 text-left text-sm transition-colors disabled:opacity-60",
        checked ? "border-primary bg-primary/5" : "border-border bg-background hover:bg-muted",
      )}
    >
      <span className="min-w-0 flex-1 truncate">{label}</span>
      <Badge tone={checked ? "ok" : "muted"} className="h-6 shrink-0 px-2">
        {checked ? t("connector.on") : t("connector.off")}
      </Badge>
    </button>
  );
}
