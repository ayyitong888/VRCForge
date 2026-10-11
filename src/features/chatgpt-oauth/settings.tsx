import { useCallback, useEffect, useRef, useState } from "react";
import { useTranslation } from "react-i18next";
import { Check, Loader2, LogIn, LogOut, RefreshCw, UserPlus } from "lucide-react";
import {
  activateChatGptOAuthModel,
  beginChatGptOAuthLogin,
  cancelChatGptOAuthLogin,
  fetchChatGptOAuthModels,
  fetchChatGptOAuthStatus,
  logoutChatGptOAuthAccount,
  type ChatGptOAuthModel,
  type ChatGptOAuthStatus,
} from "./api";
import { Badge } from "../../components/ui/badge";
import { Button } from "../../components/ui/button";

const STATUS_POLL_INTERVAL_MS = 2000;
const STATUS_POLL_LIMIT_MS = 5 * 60 * 1000;

type ChatGptOAuthSettingsProps = {
  endpoint: string;
  runtimeConnected: boolean;
  apiProvider: string;
  apiModel: string;
  savingApiConfig: boolean;
  testingProvider: string;
  providerTestMessage: string;
  providerTestPassed: boolean;
  developerOptionsEnabled: boolean;
  onTestProvider: (capability: "text" | "structured" | "vision") => void;
  onProviderConfigChanged: () => Promise<void> | void;
};

export function ChatGptOAuthSettings({
  endpoint,
  runtimeConnected,
  apiProvider,
  apiModel,
  savingApiConfig,
  testingProvider,
  providerTestMessage,
  providerTestPassed,
  developerOptionsEnabled,
  onTestProvider,
  onProviderConfigChanged,
}: ChatGptOAuthSettingsProps) {
  const { t } = useTranslation();
  const [status, setStatus] = useState<ChatGptOAuthStatus | null>(null);
  const [selectedAccountId, setSelectedAccountId] = useState("");
  const [models, setModels] = useState<ChatGptOAuthModel[]>([]);
  const [modelsAccountId, setModelsAccountId] = useState("");
  const [modelLoadFailed, setModelLoadFailed] = useState(false);
  const [selectedModel, setSelectedModel] = useState("");
  const [busy, setBusy] = useState("");
  const [statusLoading, setStatusLoading] = useState(false);
  const [error, setError] = useState("");
  const [notice, setNotice] = useState("");
  const [statusRefresh, setStatusRefresh] = useState(0);
  const statusController = useRef<AbortController | null>(null);
  const statusGeneration = useRef(0);
  const pollStopAt = useRef(0);
  const modelGeneration = useRef(0);
  const modelController = useRef<AbortController | null>(null);
  const modelLoadingAccountRef = useRef("");
  const selectedAccountRef = useRef(selectedAccountId);
  selectedAccountRef.current = selectedAccountId;

  const reloadStatus = useCallback(async (): Promise<ChatGptOAuthStatus | null> => {
    statusController.current?.abort();
    const controller = new AbortController();
    statusController.current = controller;
    const generation = ++statusGeneration.current;
    setStatusLoading(true);
    try {
      const next = await fetchChatGptOAuthStatus(endpoint, controller.signal);
      if (generation !== statusGeneration.current || controller.signal.aborted) {
        return null;
      }
      setStatus(next);
      setError(next.error || "");
      return next;
    } catch (cause) {
      if (generation === statusGeneration.current && !controller.signal.aborted) {
        setError(cause instanceof Error ? cause.message : String(cause));
      }
      return null;
    } finally {
      if (generation === statusGeneration.current) {
        setStatusLoading(false);
      }
    }
  }, [endpoint]);

  useEffect(() => {
    if (!runtimeConnected || !endpoint) {
      return undefined;
    }
    let disposed = false;
    let timer: number | undefined;
    const poll = async () => {
      const current = await reloadStatus();
      if (current?.pending && !pollStopAt.current) {
        pollStopAt.current = Date.now() + STATUS_POLL_LIMIT_MS;
      } else if (current && !current.pending) {
        pollStopAt.current = 0;
      }
      if (!disposed && current?.pending && Date.now() < pollStopAt.current) {
        timer = window.setTimeout(poll, STATUS_POLL_INTERVAL_MS);
      }
    };
    void poll();
    return () => {
      disposed = true;
      if (timer !== undefined) {
        window.clearTimeout(timer);
      }
      statusGeneration.current += 1;
      statusController.current?.abort();
    };
  }, [apiModel, apiProvider, endpoint, reloadStatus, runtimeConnected, statusRefresh]);

  useEffect(() => {
    modelGeneration.current += 1;
    modelController.current?.abort();
    modelController.current = null;
    setModels([]);
    setModelsAccountId("");
    setModelLoadFailed(false);
    modelLoadingAccountRef.current = "";
    setSelectedModel("");
    setBusy((current) => current === "models" ? "" : current);
    return () => {
      modelGeneration.current += 1;
      modelController.current?.abort();
      modelController.current = null;
    };
  }, [endpoint, runtimeConnected]);

  useEffect(() => {
    if (!status) {
      return;
    }
    if (status.accounts.some((account) => account.id === selectedAccountId)) {
      const account = status.accounts.find((item) => item.id === selectedAccountId);
      if (!account?.connected) {
        modelGeneration.current += 1;
        modelController.current?.abort();
        modelController.current = null;
        setModels([]);
        setModelsAccountId("");
        setModelLoadFailed(false);
        setSelectedModel("");
        setBusy((current) => current === "models" ? "" : current);
      }
      return;
    }
    modelGeneration.current += 1;
    modelController.current?.abort();
    modelController.current = null;
    setModels([]);
    setSelectedModel("");
    setBusy((current) => current === "models" ? "" : current);
    const nextAccount = status.accounts.find((account) => account.id === status.activeAccountId)
      ?? status.accounts.find((account) => account.connected)
      ?? status.accounts[0];
    const nextId = nextAccount?.id ?? "";
    if (nextId !== selectedAccountId) {
      modelGeneration.current += 1;
      setSelectedAccountId(nextId);
      setSelectedModel("");
      setModels([]);
    }
  }, [selectedAccountId, status]);

  const selectedAccount = status?.accounts.find((account) => account.id === selectedAccountId);
  const activeAccount = status?.activeAccountId
    ? status.accounts.find((account) => account.id === status.activeAccountId)
    : undefined;
  const activeModel = status?.activeModel || "";
  const active = Boolean(status?.activeAccountId && activeModel);
  const activeConnectionVerified = Boolean(
    active && apiProvider === "chatgpt" && providerTestPassed && activeAccount?.connected,
  );

  useEffect(() => {
    const account = selectedAccount;
    if (
      !runtimeConnected
      || !account?.connected
      || modelsAccountId === account.id
      || modelLoadingAccountRef.current === account.id
    ) {
      return;
    }
    void loadModels(account.id);
  }, [modelsAccountId, runtimeConnected, selectedAccount?.connected, selectedAccount?.id]);

  function selectAccount(accountId: string) {
    modelGeneration.current += 1;
    modelController.current?.abort();
    modelController.current = null;
    setBusy((current) => current === "models" ? "" : current);
    selectedAccountRef.current = accountId;
    setSelectedAccountId(accountId);
    setModels([]);
    setModelsAccountId("");
    setModelLoadFailed(false);
    setSelectedModel("");
    setError("");
    setNotice("");
  }

  async function runAction(action: string, operation: () => Promise<unknown>, successMessage?: string) {
    if (busy || !runtimeConnected) {
      return;
    }
    setBusy(action);
    setError("");
    setNotice("");
    try {
      await operation();
      if (successMessage) {
        setNotice(successMessage);
      }
      setStatusRefresh((current) => current + 1);
    } catch (cause) {
      setError(cause instanceof Error ? cause.message : String(cause));
    } finally {
      setBusy("");
    }
  }

  async function loadModels(accountId = selectedAccount?.id || "") {
    const account = status?.accounts.find((item) => item.id === accountId);
    if (!account?.connected || busy || !runtimeConnected || modelLoadingAccountRef.current === accountId) {
      return;
    }
    const generation = ++modelGeneration.current;
    const controller = new AbortController();
    modelController.current?.abort();
    modelController.current = controller;
    modelLoadingAccountRef.current = accountId;
    setBusy("models");
    setError("");
    setNotice("");
    setModelLoadFailed(false);
    try {
      const response = await fetchChatGptOAuthModels(endpoint, accountId, controller.signal);
      if (generation !== modelGeneration.current || selectedAccountRef.current !== accountId) {
        return;
      }
      setModels(response.models);
      setModelsAccountId(accountId);
      setSelectedModel((current) => {
        if (
          status?.activeAccountId === accountId
          && activeModel
          && response.models.some((model) => model.id === activeModel)
        ) {
          return activeModel;
        }
        if (response.models.some((model) => model.id === current)) {
          return current;
        }
        return response.models[0]?.id ?? "";
      });
      if (response.models.length === 0) {
        setNotice(t("chatgptOAuth.noModels"));
      }
    } catch (cause) {
      if (generation === modelGeneration.current && !controller.signal.aborted) {
        setModelLoadFailed(true);
        setError(cause instanceof Error ? cause.message : String(cause));
      }
    } finally {
      if (modelLoadingAccountRef.current === accountId) {
        modelLoadingAccountRef.current = "";
      }
      if (generation === modelGeneration.current) {
        modelController.current = null;
        setBusy("");
      }
    }
  }

  const displayedModels = modelsAccountId === selectedAccount?.id ? models : [];
  const activeModelOption = activeAccount?.id === selectedAccount?.id
    && activeModel
    && !displayedModels.some((model) => model.id === activeModel)
    ? { id: activeModel, name: activeModel }
    : null;
  const modelChoicesAvailable = displayedModels.length > 0 || Boolean(activeModelOption);

  async function logoutSelectedAccount() {
    if (!selectedAccount || busy || !runtimeConnected) {
      return;
    }
    setBusy("logout");
    setError("");
    setNotice("");
    try {
      const result = await logoutChatGptOAuthAccount(endpoint, selectedAccount.id);
      modelGeneration.current += 1;
      modelController.current?.abort();
      modelController.current = null;
      setModels([]);
      setSelectedModel("");
      if (!result.revoked) {
        setNotice(t("chatgptOAuth.logoutNotConfirmed"));
      }
      await onProviderConfigChanged();
      setStatusRefresh((current) => current + 1);
    } catch (cause) {
      setError(cause instanceof Error ? cause.message : String(cause));
    } finally {
      setBusy("");
    }
  }

  return (
    <section className="rounded-lg border border-border bg-card p-4" data-chatgpt-oauth-panel>
      <div className="flex min-w-0 flex-wrap items-center gap-2">
        <div className="min-w-0 flex-1 basis-40">
          <h3 className="truncate text-sm font-semibold">{t("chatgptOAuth.title")}</h3>
        </div>
        <div className="flex min-w-0 flex-wrap items-center gap-2">
          {active ? (
            <Badge data-chatgpt-active-badge tone={activeConnectionVerified ? "ok" : "warn"}>
              {activeAccount
                ? activeAccount.connected
                  ? t("chatgptOAuth.activeAccountModel", { account: activeAccount.label || activeAccount.email || activeAccount.id, model: activeModel })
                  : t("chatgptOAuth.disconnectedActiveAccountModel", { account: activeAccount.label || activeAccount.email || activeAccount.id, model: activeModel })
                : t("chatgptOAuth.activeAccountModel", { account: status?.activeAccountId, model: activeModel })}
            </Badge>
          ) : null}
          {status?.pending ? (
            <Badge tone="warn">{t("chatgptOAuth.pending")}</Badge>
          ) : null}
          <Button
            type="button"
            variant="outline"
            className="h-10 w-10 shrink-0 px-0"
            data-chatgpt-refresh
            aria-label={t("chatgptOAuth.refreshStatus")}
            title={t("chatgptOAuth.refreshStatus")}
            disabled={!runtimeConnected || statusLoading || Boolean(busy)}
            onClick={() => setStatusRefresh((current) => current + 1)}
          >
            {statusLoading ? <Loader2 className="h-4 w-4 animate-spin" /> : <RefreshCw className="h-4 w-4" />}
          </Button>
        </div>
      </div>
      {status?.pending ? (
        <div className="mt-2 text-xs text-muted-foreground" role="status">
          {activeConnectionVerified
            ? t("chatgptOAuth.pendingWithCurrentAccount")
            : t("chatgptOAuth.pendingDetails")}
        </div>
      ) : null}

      <div className="mt-4 grid gap-3">
        <label className="grid gap-1.5 text-xs font-medium text-foreground">
          {t("chatgptOAuth.account")}
          <select
            value={selectedAccountId}
            onChange={(event) => selectAccount(event.target.value)}
            disabled={!runtimeConnected || Boolean(busy && busy !== "models")}
            className="h-10 w-full rounded-md border border-border bg-background px-3 text-sm font-normal outline-none focus:border-primary disabled:opacity-60"
          >
            {!status?.accounts.length ? <option value="">{t("chatgptOAuth.noAccounts")}</option> : null}
            {status?.accounts.map((account) => (
              <option key={account.id} value={account.id}>
                {account.label || account.email || account.id}{account.email && account.email !== account.label ? ` (${account.email})` : ""}
              </option>
            ))}
          </select>
        </label>

        <div className="grid min-w-0 gap-2 sm:grid-cols-2 md:grid-cols-3">
          <Button
            type="button"
            variant="outline"
            className="w-full"
            disabled={!runtimeConnected || Boolean(busy) || Boolean(status?.pending) || savingApiConfig}
            onClick={() => void runAction(
              "login",
              () => beginChatGptOAuthLogin(
                endpoint,
                status?.accounts.some((account) => account.id === selectedAccountId)
                  ? selectedAccountId
                  : "",
              ),
            )}
          >
            {busy === "login" ? <Loader2 className="h-4 w-4 animate-spin" /> : <LogIn className="h-4 w-4" />}
            {selectedAccount?.connected ? t("chatgptOAuth.relogin") : t("chatgptOAuth.continue")}
          </Button>
          <Button
            type="button"
            variant="outline"
            className="w-full"
            disabled={!runtimeConnected || Boolean(busy) || Boolean(status?.pending) || savingApiConfig}
            onClick={() => void runAction(
              "login",
              () => beginChatGptOAuthLogin(endpoint, ""),
            )}
          >
            <UserPlus className="h-4 w-4" />
            {t("chatgptOAuth.addAnotherAccount")}
          </Button>
          {status?.pending ? (
            <Button
              type="button"
              variant="outline"
              className="w-full"
              disabled={Boolean(busy) || !runtimeConnected}
              onClick={() => void runAction("cancel", () => cancelChatGptOAuthLogin(endpoint))}
            >
              {t("chatgptOAuth.cancel")}
            </Button>
          ) : null}
          {selectedAccount ? (
            <Button
              type="button"
              variant="outline"
              className="w-full"
              disabled={Boolean(busy) || !runtimeConnected}
              onClick={() => void logoutSelectedAccount()}
            >
              {busy === "logout" ? <Loader2 className="h-4 w-4 animate-spin" /> : <LogOut className="h-4 w-4" />}
              {selectedAccount.connected ? t("chatgptOAuth.logout") : t("chatgptOAuth.removeAccount")}
            </Button>
          ) : null}
        </div>

        <div className="grid min-w-0 gap-2 sm:grid-cols-2">
          <Button
            type="button"
            variant="outline"
            className="w-full"
            disabled={!runtimeConnected || !selectedAccount?.connected || Boolean(busy)}
            onClick={() => void loadModels()}
          >
            {busy === "models" ? <Loader2 className="h-4 w-4 animate-spin" /> : <RefreshCw className="h-4 w-4" />}
            {t("chatgptOAuth.loadModels")}
          </Button>
          {apiProvider === "chatgpt" ? (
            <Button
              type="button"
              variant="outline"
              className="w-full"
              disabled={!runtimeConnected || !active || Boolean(busy) || Boolean(testingProvider) || savingApiConfig}
              onClick={() => onTestProvider("text")}
            >
              {testingProvider === "text" ? <Loader2 className="h-4 w-4 animate-spin" /> : <Check className="h-4 w-4" />}
              {t("chatgptOAuth.testConnection")}
            </Button>
          ) : null}
        </div>

        <div className="grid min-w-0 gap-2 sm:grid-cols-[minmax(0,1fr)_auto] sm:items-end">
          <label className="grid min-w-0 flex-1 gap-1.5 text-xs font-medium text-foreground">
            {t("chatgptOAuth.model")}
            <select
              value={selectedModel}
              onChange={(event) => setSelectedModel(event.target.value)}
              disabled={Boolean(busy) || !modelChoicesAvailable}
              data-chatgpt-model-select
              className="h-10 w-full min-w-0 rounded-md border border-border bg-background px-3 text-sm font-normal outline-none focus:border-primary disabled:opacity-60"
            >
              {!modelChoicesAvailable ? (
                <option value="">
                  {busy === "models"
                    ? t("chatgptOAuth.modelsLoading")
                    : !selectedAccount
                      ? t("chatgptOAuth.modelsNoAccount")
                      : !selectedAccount.connected
                        ? t("chatgptOAuth.modelsDisconnected")
                        : modelLoadFailed
                          ? t("chatgptOAuth.modelsLoadFailed")
                          : t("chatgptOAuth.modelsNoResults")}
                </option>
              ) : null}
              {activeModelOption ? <option value={activeModelOption.id}>{activeModelOption.name}</option> : null}
              {displayedModels.map((model) => <option key={model.id} value={model.id}>{model.name || model.id}</option>)}
            </select>
          </label>
          <Button
            type="button"
            className="w-full sm:w-auto"
            disabled={!runtimeConnected || !selectedAccount?.connected || !selectedModel || Boolean(busy) || savingApiConfig}
            onClick={() => void runAction(
              "activate",
              async () => {
                await activateChatGptOAuthModel(endpoint, selectedAccountId, selectedModel);
                await onProviderConfigChanged();
              },
              t("chatgptOAuth.activated"),
            )}
          >
            {busy === "activate" ? <Loader2 className="h-4 w-4 animate-spin" /> : <Check className="h-4 w-4" />}
            {t("chatgptOAuth.activate")}
          </Button>
        </div>

        {!runtimeConnected ? <div className="text-xs text-muted-foreground">{t("provider.coreDisconnectedModels")}</div> : null}
        {apiProvider === "chatgpt" && providerTestMessage ? (
          <div aria-live="polite" className="text-xs text-muted-foreground">
            {providerTestPassed ? t("chatgptOAuth.testConnectionSucceeded") : t("chatgptOAuth.testConnectionNeedsRetest")}
            {developerOptionsEnabled ? <span className="ml-1">{providerTestMessage}</span> : null}
          </div>
        ) : null}
        {error ? <div role="alert" className="text-xs text-destructive/80">{error}</div> : null}
        {notice ? <div aria-live="polite" className="text-xs text-muted-foreground">{notice}</div> : null}
      </div>
    </section>
  );
}
