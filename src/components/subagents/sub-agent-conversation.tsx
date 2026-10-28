import { useEffect, useState } from "react";
import { Loader2, RefreshCw } from "lucide-react";
import { useTranslation } from "react-i18next";
import { fetchAgentRunTimeline, type SubAgentTask } from "../../lib/api";
import type { ChatTimelineEvent } from "../../lib/chat-types";
import { mergeRuntimeTimelines } from "../../lib/chat-timeline-presentation";
import { buildDurableTimelineRows } from "../chat/conversation-timeline";
import { ChatMarkdown } from "../chat/chat-markdown";
import { Button } from "../ui/button";

type TurnTimeline = { events: ChatTimelineEvent[]; nextSequence: number };
const RUNNING = new Set(["queued", "running", "cancelling"]);

/** Read the child's existing public ledger; opening a conversation never starts a turn. */
export function SubAgentConversation({ endpoint, turns }: { endpoint: string; turns: SubAgentTask[] }) {
  const { t } = useTranslation();
  const [timelines, setTimelines] = useState<Record<string, TurnTimeline>>({});
  const [error, setError] = useState("");
  const [loading, setLoading] = useState(false);
  const [refresh, setRefresh] = useState(0);
  const sessionId = turns[0]?.childSessionId || "";
  const turnKey = turns.map((turn) => `${turn.id}:${turn.status}`).join("|");

  useEffect(() => {
    if (!endpoint || !sessionId) return;
    const controller = new AbortController();
    const loaded: Record<string, TurnTimeline> = {};
    let timer: ReturnType<typeof setTimeout> | undefined;
    async function read() {
      setLoading(true);
      try {
        await Promise.all(turns.filter((turn) => !loaded[turn.id] || RUNNING.has(turn.status)).map(async (turn) => {
          let current = loaded[turn.id] || { events: [], nextSequence: 0 };
          let more: boolean;
          do {
            const page = await fetchAgentRunTimeline(endpoint, sessionId, turn.id, current.nextSequence, controller.signal);
            if (page.hasMore && page.nextSequence <= current.nextSequence) throw new Error("Timeline page did not advance.");
            current = { events: mergeRuntimeTimelines(current.events, page.events), nextSequence: page.nextSequence };
            more = page.hasMore;
          } while (more && !controller.signal.aborted);
          loaded[turn.id] = current;
        }));
        if (!controller.signal.aborted) {
          setTimelines({ ...loaded });
          setError("");
        }
      } catch (cause) {
        if (!controller.signal.aborted) setError(cause instanceof Error ? cause.message : String(cause));
      } finally {
        if (!controller.signal.aborted) {
          setLoading(false);
          if (turns.some((turn) => RUNNING.has(turn.status))) timer = setTimeout(() => void read(), 1500);
        }
      }
    }
    void read();
    return () => { controller.abort(); if (timer !== undefined) clearTimeout(timer); };
    // Poll cursors belong to this child and these turn identities, not list-object refreshes.
  }, [endpoint, sessionId, turnKey, refresh]);

  return (
    <div className="space-y-8" data-vrcforge-subagent-conversation data-subagent-session={sessionId}>
      {turns.map((turn) => {
        const events = timelines[turn.id]?.events || [];
        const rows = buildDurableTimelineRows(events, undefined, turn.status === "failed" ? turn.error || "failed" : "", [], {
          live: RUNNING.has(turn.status),
        });
        return (
          <section key={turn.id} className="space-y-3" data-subagent-conversation-turn={turn.id}>
            <div className="flex justify-end">
              <div className="max-w-[88%] rounded-2xl bg-muted/70 px-4 py-3 text-sm" data-subagent-user-message>
                <ChatMarkdown text={turn.task} />
              </div>
            </div>
            <div className="space-y-4 px-1 text-sm [&_.chat-markdown]:leading-7" data-subagent-assistant-messages>
              {rows.length ? rows : turn.summary ? <ChatMarkdown text={turn.summary} /> : (
                <p className="text-xs text-muted-foreground">{t("subagent.noConversation")}</p>
              )}
              {turn.error ? <p role="alert" className="rounded-lg border border-destructive/20 bg-destructive/5 px-3 py-2 text-sm text-destructive">{turn.error}</p> : null}
            </div>
          </section>
        );
      })}
      {loading ? <div className="flex items-center gap-2 text-xs text-muted-foreground"><Loader2 className="h-3.5 w-3.5 animate-spin" />{t("common.loading")}</div> : null}
      {error ? <div role="alert" className="rounded-lg border border-destructive/30 p-3 text-xs text-destructive">
        <p>{error}</p>
        <Button variant="ghost" className="mt-1 h-7 px-2" onClick={() => setRefresh((value) => value + 1)}><RefreshCw className="h-3 w-3" />{t("common.refresh")}</Button>
      </div> : null}
    </div>
  );
}
