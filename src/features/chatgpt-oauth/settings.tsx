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
};

export function ChatGptOAuthSettings({
  endpoint,
  runtimeConnected,
  apiProvider,
  apiModel,
  savingApiConfig,
}: ChatGptOAuthSettingsProps) {
  const { t } = useTranslation();
  const [status, setStatus] = useState<ChatGptOAuthStatus | null>(null);
  const [selectedAccountId, setSelectedAccountId] = useState("");
  const [models, setModels] = useState<ChatGptOAuthModel[]>([]);
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

  function selectAccount(accountId: string) {
    modelGeneration.current += 1;
    modelController.current?.abort();
    modelController.current = null;
    setBusy((current) => current === "models" ? "" : current);
    selectedAccountRef.current = accountId;
    setSelectedAccountId(accountId);
    setModels([]);
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

  async function loadModels() {
    if (!selectedAccount?.connected || busy || !runtimeConnected) {
      return;
    }
    const accountId = selectedAccount.id;
    const generation = ++modelGeneration.current;
    const controller = new AbortController();
    modelController.current?.abort();
    modelController.current = controller;
    setBusy("models");
    setError("");
    setNotice("");
    try {
      const response = await fetchChatGptOAuthModels(endpoint, accountId, controller.signal);
      if (generation !== modelGeneration.current || selectedAccountRef.current !== accountId) {
        return;
      }
      setModels(response.models);
      setSelectedModel((current) => {
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
        setError(cause instanceof Error ? cause.message : String(cause));
      }
    } finally {
      if (generation === modelGeneration.current) {
        modelController.current = null;
        setBusy("");
      }
    }
  }

  async function logoutSelectedAccount() {
    if (!selectedAccount?.connected || busy || !runtimeConnected) {
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
      setStatusRefresh((current) => current + 1);
    } catch (cause) {
      setError(cause instanceof Error ? cause.message : String(cause));
    } finally {
      setBusy("");
    }
  }

  return (
    <section className="rounded-lg border border-border bg-card p-4" data-chatgpt-oauth-panel>
      <div className="flex min-w-0 flex-wrap items-start justify-between gap-3">
        <div className="min-w-0">
          <h3 className="text-sm font-semibold">{t("chatgptOAuth.title")}</h3>
        </div>
        {active ? (
          <Badge tone={activeAccount?.connected ? "ok" : "warn"}>
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
      </div>

      <div className="mt-4 grid gap-3">
        <label className="grid gap-1.5 text-xs font-medium text-foreground">
          {t("chatgptOAuth.account")}
          <select
            value={selectedAccountId}
            onChange={(event) => selectAccount(event.target.value)}
            disabled={!runtimeConnected || Boolean(busy)}
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

        <div className="flex min-w-0 flex-wrap items-center gap-2">
          <Button
            type="button"
            variant="outline"
            disabled={!runtimeConnected || Boolean(busy) || Boolean(status?.pending) || savingApiConfig}
            onClick={() => void runAction(
              "login",
              () => beginChatGptOAuthLogin(endpoint, selectedAccountId),
            )}
          >
            {busy === "login" ? <Loader2 className="h-4 w-4 animate-spin" /> : <LogIn className="h-4 w-4" />}
            {selectedAccount?.connected ? t("chatgptOAuth.relogin") : t("chatgptOAuth.continue")}
          </Button>
          <Button
            type="button"
            variant="outline"
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
              disabled={Boolean(busy) || !runtimeConnected}
              onClick={() => void runAction("cancel", () => cancelChatGptOAuthLogin(endpoint))}
            >
              {t("chatgptOAuth.cancel")}
            </Button>
          ) : null}
          {selectedAccount?.connected ? (
            <Button
              type="button"
              variant="outline"
              disabled={Boolean(busy) || !runtimeConnected}
              onClick={() => void logoutSelectedAccount()}
            >
              {busy === "logout" ? <Loader2 className="h-4 w-4 animate-spin" /> : <LogOut className="h-4 w-4" />}
              {t("chatgptOAuth.logout")}
            </Button>
          ) : null}
          <Button
            type="button"
            variant="outline"
            disabled={!runtimeConnected || !selectedAccount?.connected || Boolean(busy)}
            onClick={() => void loadModels()}
          >
            {busy === "models" ? <Loader2 className="h-4 w-4 animate-spin" /> : <RefreshCw className="h-4 w-4" />}
            {t("chatgptOAuth.loadModels")}
          </Button>
          <Button
            type="button"
            variant="outline"
            className="ml-auto"
            aria-label={t("chatgptOAuth.refreshStatus")}
            title={t("chatgptOAuth.refreshStatus")}
            disabled={!runtimeConnected || statusLoading || Boolean(busy)}
            onClick={() => setStatusRefresh((current) => current + 1)}
          >
            {statusLoading ? <Loader2 className="h-4 w-4 animate-spin" /> : <RefreshCw className="h-4 w-4" />}
          </Button>
        </div>

        {models.length > 0 ? (
          <div className="flex min-w-0 flex-wrap items-end gap-2">
            <label className="grid min-w-0 flex-1 gap-1.5 text-xs font-medium text-foreground">
              {t("chatgptOAuth.model")}
              <select
                value={selectedModel}
                onChange={(event) => setSelectedModel(event.target.value)}
                disabled={Boolean(busy)}
                className="h-10 w-full min-w-0 rounded-md border border-border bg-background px-3 text-sm font-normal outline-none focus:border-primary disabled:opacity-60"
              >
                {models.map((model) => <option key={model.id} value={model.id}>{model.name || model.id}</option>)}
              </select>
            </label>
            <Button
              type="button"
              disabled={!runtimeConnected || !selectedAccount?.connected || !selectedModel || Boolean(busy) || savingApiConfig}
              onClick={() => void runAction(
                "activate",
                () => activateChatGptOAuthModel(endpoint, selectedAccountId, selectedModel),
                t("chatgptOAuth.activated"),
              )}
            >
              {busy === "activate" ? <Loader2 className="h-4 w-4 animate-spin" /> : <Check className="h-4 w-4" />}
              {t("chatgptOAuth.activate")}
            </Button>
          </div>
        ) : null}

        {!runtimeConnected ? <div className="text-xs text-muted-foreground">{t("provider.coreDisconnectedModels")}</div> : null}
        {error ? <div role="alert" className="text-xs text-destructive/80">{error}</div> : null}
        {notice ? <div aria-live="polite" className="text-xs text-muted-foreground">{notice}</div> : null}
      </div>
    </section>
  );
}
