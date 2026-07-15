import { Brain, Loader2, Plus, RefreshCw, Trash2 } from "lucide-react";
import type { FormEvent } from "react";
import { useEffect, useRef, useState } from "react";
import { useTranslation } from "react-i18next";
import type { AgentMemory } from "../../lib/api";
import { clearAgentMemory, createAgentMemory, deleteAgentMemory, fetchAgentMemory } from "../../lib/api";
import { cn } from "../../lib/utils";
import { Badge } from "../ui/badge";
import { Button } from "../ui/button";

type MemoryScopeFilter = "all" | "user" | "project";
type MemoryScope = "user" | "project";

type MemorySettingsPanelProps = {
  endpoint: string;
  runtimeConnected: boolean;
  selectedProjectPath: string;
};

function memoryEntryId(memory: AgentMemory): string {
  return memory.memoryId || memory.id || "";
}

export function MemorySettingsPanel({ endpoint, runtimeConnected, selectedProjectPath }: MemorySettingsPanelProps) {
  const { t } = useTranslation();
  const [memories, setMemories] = useState<AgentMemory[]>([]);
  const [loading, setLoading] = useState(false);
  const [message, setMessage] = useState("");
  const [error, setError] = useState("");
  const [scopeFilter, setScopeFilter] = useState<MemoryScopeFilter>("all");
  const [draftText, setDraftText] = useState("");
  const [draftScope, setDraftScope] = useState<MemoryScope>("user");
  const [saving, setSaving] = useState(false);
  const [busyMemoryId, setBusyMemoryId] = useState("");
  const [confirmDeleteId, setConfirmDeleteId] = useState("");
  const [confirmClearScope, setConfirmClearScope] = useState<"" | MemoryScope>("");
  const [clearingScope, setClearingScope] = useState<"" | MemoryScope>("");
  const refreshRequestId = useRef(0);
  const contextEpoch = useRef(0);
  const contextKey = `${endpoint}\u0000${runtimeConnected ? "connected" : "disconnected"}\u0000${selectedProjectPath}\u0000${scopeFilter}`;
  const previousContextKey = useRef(contextKey);
  if (previousContextKey.current !== contextKey) {
    previousContextKey.current = contextKey;
    contextEpoch.current += 1;
  }

  const projectDraftBlocked = draftScope === "project" && !selectedProjectPath;
  const visibleMemories = scopeFilter === "all" ? memories : memories.filter((memory) => (memory.scope || "project") === scopeFilter);

  async function refreshMemories(showLoading = true, expectedEpoch = contextEpoch.current) {
    if (expectedEpoch !== contextEpoch.current) {
      return;
    }
    const requestId = ++refreshRequestId.current;
    if (!runtimeConnected) {
      setMemories([]);
      setLoading(false);
      return;
    }
    if (showLoading) {
      setLoading(true);
    }
    try {
      const payload = await fetchAgentMemory(endpoint, {
        limit: 200,
        scope: scopeFilter === "all" ? "" : scopeFilter,
        projectRoot: selectedProjectPath,
      });
      if (expectedEpoch !== contextEpoch.current || requestId !== refreshRequestId.current) {
        return;
      }
      setMemories(Array.isArray(payload.memories) ? payload.memories : []);
      setError("");
    } catch (cause) {
      if (expectedEpoch === contextEpoch.current && requestId === refreshRequestId.current) {
        setError(cause instanceof Error ? cause.message : String(cause));
      }
    } finally {
      if (showLoading && expectedEpoch === contextEpoch.current && requestId === refreshRequestId.current) {
        setLoading(false);
      }
    }
  }

  useEffect(() => {
    const expectedEpoch = contextEpoch.current;
    refreshRequestId.current += 1;
    setConfirmDeleteId("");
    setConfirmClearScope("");
    setSaving(false);
    setBusyMemoryId("");
    setClearingScope("");
    if (!selectedProjectPath && scopeFilter === "project") {
      setMemories([]);
      setLoading(false);
      setScopeFilter("all");
      return;
    }
    void refreshMemories(true, expectedEpoch);
  }, [runtimeConnected, endpoint, scopeFilter, selectedProjectPath]);

  async function submitCreate(event: FormEvent) {
    event.preventDefault();
    const text = draftText.trim();
    if (!text || saving || projectDraftBlocked || !runtimeConnected) {
      return;
    }
    const expectedEpoch = contextEpoch.current;
    setSaving(true);
    setMessage("");
    try {
      await createAgentMemory(endpoint, {
        text,
        scope: draftScope,
        source: "settings",
        ...(draftScope === "project" ? { projectRoot: selectedProjectPath } : {}),
      });
      if (expectedEpoch !== contextEpoch.current) {
        return;
      }
      setDraftText("");
      setError("");
      setMessage(t("settings.memoryAdded"));
      await refreshMemories(false, expectedEpoch);
    } catch (cause) {
      if (expectedEpoch === contextEpoch.current) {
        setError(cause instanceof Error ? cause.message : String(cause));
      }
    } finally {
      if (expectedEpoch === contextEpoch.current) {
        setSaving(false);
      }
    }
  }

  async function removeMemory(memory: AgentMemory) {
    const memoryId = memoryEntryId(memory);
    if (!memoryId || busyMemoryId) {
      return;
    }
    if (confirmDeleteId !== memoryId) {
      setConfirmDeleteId(memoryId);
      setConfirmClearScope("");
      return;
    }
    const expectedEpoch = contextEpoch.current;
    setBusyMemoryId(memoryId);
    setMessage("");
    try {
      await deleteAgentMemory(endpoint, memoryId, { reason: "settings" });
      if (expectedEpoch !== contextEpoch.current) {
        return;
      }
      setError("");
      setMessage(t("settings.memoryDeleted"));
      await refreshMemories(false, expectedEpoch);
    } catch (cause) {
      if (expectedEpoch === contextEpoch.current) {
        setError(cause instanceof Error ? cause.message : String(cause));
      }
    } finally {
      if (expectedEpoch === contextEpoch.current) {
        setBusyMemoryId("");
        setConfirmDeleteId("");
      }
    }
  }

  async function clearScope(scope: MemoryScope) {
    if (clearingScope || (scope === "project" && !selectedProjectPath)) {
      return;
    }
    if (confirmClearScope !== scope) {
      setConfirmClearScope(scope);
      setConfirmDeleteId("");
      return;
    }
    const expectedEpoch = contextEpoch.current;
    setClearingScope(scope);
    setMessage("");
    try {
      const payload = await clearAgentMemory(endpoint, {
        scope,
        reason: "settings",
        ...(scope === "project" ? { projectRoot: selectedProjectPath } : {}),
      });
      if (expectedEpoch !== contextEpoch.current) {
        return;
      }
      setError("");
      setMessage(t("settings.memoryCleared", { count: payload.cleared ?? 0 }));
      await refreshMemories(false, expectedEpoch);
    } catch (cause) {
      if (expectedEpoch === contextEpoch.current) {
        setError(cause instanceof Error ? cause.message : String(cause));
      }
    } finally {
      if (expectedEpoch === contextEpoch.current) {
        setClearingScope("");
        setConfirmClearScope("");
      }
    }
  }

  return (
    <div>
      <div className="flex min-w-0 items-center gap-2">
        <h2 className="truncate text-base font-semibold">
          <Brain className="mr-1.5 inline-block h-4 w-4 align-text-bottom" />
          {t("settings.memoryTitle")}
        </h2>
        <Badge tone="muted" className="shrink-0">
          {memories.length}
        </Badge>
        {message ? (
          <Badge tone="ok" className="shrink-0">
            {message}
          </Badge>
        ) : null}
      </div>
      <p className="mt-1 text-sm text-muted-foreground">{t("settings.memoryDesc")}</p>
      {error ? <p className="mt-2 text-xs text-destructive">{error}</p> : null}

      <div className="mt-4 flex flex-wrap items-center gap-2">
        {(["all", "user", "project"] as const).map((scope) => (
          <button
            key={scope}
            type="button"
            onClick={() => setScopeFilter(scope)}
            aria-pressed={scopeFilter === scope}
            disabled={scope === "project" && !selectedProjectPath}
            className={cn(
              "rounded-md border px-3 py-1.5 text-sm font-medium transition-colors",
              scopeFilter === scope
                ? "border-primary bg-primary/10 text-primary"
                : "border-border bg-card text-foreground hover:bg-accent disabled:cursor-not-allowed disabled:opacity-50 disabled:hover:bg-card",
            )}
          >
            {scope === "all" ? t("settings.memoryScopeAll") : scope === "user" ? t("settings.memoryScopeUser") : t("settings.memoryScopeProject")}
          </button>
        ))}
        <Button
          type="button"
          variant="outline"
          className="ml-auto h-8 px-3 text-xs"
          disabled={!runtimeConnected || loading}
          onClick={() => void refreshMemories()}
        >
          {loading ? <Loader2 className="h-3.5 w-3.5 animate-spin" /> : <RefreshCw className="h-3.5 w-3.5" />}
          {t("common.refresh")}
        </Button>
      </div>

      <div className="mt-4 space-y-2">
        {visibleMemories.map((memory) => {
          const memoryId = memoryEntryId(memory);
          return (
            <div key={memoryId || memory.text} className="rounded-lg border border-border bg-card p-3">
              <div className="flex min-w-0 items-start gap-2">
                <div className="min-w-0 flex-1">
                  <div className="flex min-w-0 flex-wrap items-center gap-2">
                    <Badge tone={(memory.scope || "project") === "user" ? "default" : "muted"} className="h-6 shrink-0">
                      {(memory.scope || "project") === "user" ? t("settings.memoryScopeUser") : t("settings.memoryScopeProject")}
                    </Badge>
                    {memory.kind ? <span className="shrink-0 text-xs text-muted-foreground">{memory.kind}</span> : null}
                    {memory.updatedAt || memory.createdAt ? (
                      <span className="truncate text-xs text-muted-foreground/70">{memory.updatedAt || memory.createdAt}</span>
                    ) : null}
                  </div>
                  <div className="mt-1.5 whitespace-pre-wrap break-words text-sm">{memory.text}</div>
                  {memory.projectRoot ? <div className="mt-1 truncate text-xs text-muted-foreground/70">{memory.projectRoot}</div> : null}
                </div>
                <Button
                  type="button"
                  variant={confirmDeleteId === memoryId ? "danger" : "ghost"}
                  className="h-8 shrink-0 px-2 text-xs"
                  disabled={!runtimeConnected || !memoryId || Boolean(busyMemoryId)}
                  onClick={() => void removeMemory(memory)}
                  aria-label={confirmDeleteId === memoryId ? t("settings.memoryConfirmDelete") : t("common.delete")}
                >
                  {busyMemoryId === memoryId ? <Loader2 className="h-3.5 w-3.5 animate-spin" /> : <Trash2 className="h-3.5 w-3.5" />}
                  {confirmDeleteId === memoryId ? t("settings.memoryConfirmDelete") : null}
                </Button>
              </div>
            </div>
          );
        })}
        {!visibleMemories.length && !loading ? (
          <div className="rounded-lg border border-dashed border-border p-4 text-center text-sm text-muted-foreground">
            {t("settings.memoryEmpty")}
          </div>
        ) : null}
      </div>

      <div className="mt-4 flex flex-wrap gap-2">
        <Button
          type="button"
          variant={confirmClearScope === "user" ? "danger" : "outline"}
          className="h-8 px-3 text-xs"
          disabled={!runtimeConnected || Boolean(clearingScope)}
          onClick={() => void clearScope("user")}
        >
          {clearingScope === "user" ? <Loader2 className="h-3.5 w-3.5 animate-spin" /> : null}
          {confirmClearScope === "user" ? t("settings.memoryConfirmClear") : t("settings.memoryClearUser")}
        </Button>
        <Button
          type="button"
          variant={confirmClearScope === "project" ? "danger" : "outline"}
          className="h-8 px-3 text-xs"
          disabled={!runtimeConnected || Boolean(clearingScope) || !selectedProjectPath}
          onClick={() => void clearScope("project")}
        >
          {clearingScope === "project" ? <Loader2 className="h-3.5 w-3.5 animate-spin" /> : null}
          {confirmClearScope === "project" ? t("settings.memoryConfirmClear") : t("settings.memoryClearProject")}
        </Button>
      </div>

      <form onSubmit={submitCreate} className="mt-6 rounded-xl border border-border bg-card p-4">
        <label htmlFor="settings-memory-draft" className="text-sm font-medium">
          {t("settings.memoryAdd")}
        </label>
        <textarea
          id="settings-memory-draft"
          aria-label={t("settings.memoryAdd")}
          value={draftText}
          onChange={(event) => setDraftText(event.target.value)}
          disabled={!runtimeConnected}
          placeholder={t("settings.memoryAddPlaceholder")}
          className="mt-3 min-h-24 w-full resize-y rounded-lg border border-border bg-background px-3 py-2 text-sm leading-relaxed outline-none focus:border-primary disabled:bg-muted"
        />
        <div className="mt-3 flex flex-wrap items-center gap-2">
          {(["user", "project"] as const).map((scope) => (
            <button
              key={scope}
              type="button"
              onClick={() => setDraftScope(scope)}
              aria-pressed={draftScope === scope}
              disabled={scope === "project" && !selectedProjectPath}
              className={cn(
                "rounded-md border px-3 py-1.5 text-sm font-medium transition-colors",
                draftScope === scope
                  ? "border-primary bg-primary/10 text-primary"
                  : "border-border bg-card text-foreground hover:bg-accent disabled:cursor-not-allowed disabled:opacity-50 disabled:hover:bg-card",
              )}
            >
              {scope === "user" ? t("settings.memoryScopeUser") : t("settings.memoryScopeProject")}
            </button>
          ))}
          {projectDraftBlocked ? <span className="text-xs text-destructive">{t("settings.memoryProjectRequired")}</span> : null}
          <Button type="submit" className="ml-auto" disabled={!runtimeConnected || saving || !draftText.trim() || projectDraftBlocked}>
            {saving ? <Loader2 className="h-4 w-4 animate-spin" /> : <Plus className="h-4 w-4" />}
            {t("settings.memoryAdd")}
          </Button>
        </div>
      </form>
    </div>
  );
}
