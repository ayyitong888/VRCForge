import { Bot, ChevronRight, Loader2 } from "lucide-react";
import { useTranslation } from "react-i18next";
import type { SubAgentTask } from "../../lib/api";
import { groupSubAgentThreads } from "../../lib/subagent-threads";
import { isActiveSubAgentStatus } from "../../lib/subagent-ui";

const MARK_COLORS = [
  "bg-violet-500/15 text-violet-700 dark:text-violet-300",
  "bg-sky-500/15 text-sky-700 dark:text-sky-300",
  "bg-emerald-500/15 text-emerald-700 dark:text-emerald-300",
  "bg-amber-500/15 text-amber-700 dark:text-amber-300",
  "bg-rose-500/15 text-rose-700 dark:text-rose-300",
];

export function SubAgentMark({ threadId, compact = false }: { threadId: string; compact?: boolean }) {
  let hash = 0x811c9dc5;
  for (let index = 0; index < threadId.length; index += 1) {
    hash = Math.imul(hash ^ threadId.charCodeAt(index), 0x01000193);
  }
  const color = MARK_COLORS[(hash >>> 0) % MARK_COLORS.length];
  return (
    <span className={`inline-flex ${compact ? "h-4 w-4" : "h-6 w-6"} shrink-0 items-center justify-center rounded-full ${color}`} aria-hidden="true">
      <Bot className="h-3.5 w-3.5" />
    </span>
  );
}

export function SubAgentPanel({
  tasks,
  loading,
  error,
  onOpen,
}: {
  tasks: SubAgentTask[];
  loading: boolean;
  error: string;
  onOpen: () => void;
}) {
  const { t } = useTranslation();
  const threads = groupSubAgentThreads(tasks);
  const active = threads.filter(({ task }) => isActiveSubAgentStatus(task.status));
  if (threads.length === 0 && !loading && !error) return null;

  const marks = (active.length ? active : threads).slice(0, 4);
  const title = t("workspace.subAgents");
  const summary = active.length ? t("subagent.statusRunning", { count: active.length })
    : threads.length ? t("subagent.statusDone", { count: threads.length })
      : loading ? t("common.loading") : t("subagent.noTasks");
  return (
    <section className="border-b border-border pb-2 pt-2.5" aria-label={title}>
      <h2 className="px-1 text-sm font-semibold">{title}</h2>
      <button type="button" aria-label={`${title}, ${summary}`} onClick={onOpen}
        className="mt-1.5 flex min-h-9 w-full min-w-0 items-center gap-2 rounded-md px-1 text-left text-muted-foreground hover:bg-muted/70 hover:text-foreground focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring"
        data-vrcforge-open-subagent-surface>
        {marks.length ? <span className="flex shrink-0 -space-x-0.5">
          {marks.map(({ id }) => <SubAgentMark key={id} threadId={id} />)}
        </span> : null}
        <span className="min-w-0 flex-1 truncate text-xs">{summary}</span>
        {loading && !threads.length ? <Loader2 className="h-3.5 w-3.5 shrink-0 animate-spin" aria-hidden="true" /> : null}
        <ChevronRight className="h-3.5 w-3.5 shrink-0" aria-hidden="true" />
      </button>
      {error ? <p className="mt-1 break-words px-1 text-xs text-destructive" role="alert">{error}</p> : null}
    </section>
  );
}
