/// <reference lib="es2021.intl" />
import { Fragment } from "react";
import { useTranslation } from "react-i18next";
import type { ChatTimelineEvent } from "../../lib/chat-types";
import { subAgentTimelineTaskId } from "../../lib/chat-timeline-presentation";
import { SubAgentMark } from "./sub-agent-panel";

export function SubAgentStartRow({ events, onOpen }: {
  events: ChatTimelineEvent[];
  onOpen: (taskId: string) => void;
}) {
  const { t, i18n } = useTranslation();
  const names = events.map(event => event.payload.label || t("agent.subagentTask"));
  const parts = new Intl.ListFormat(i18n.resolvedLanguage || i18n.language).formatToParts(names);
  let member = 0;
  return (
    <div data-subagent-start-group role="group" aria-label={t("subagent.startedWork")}
      className="flex min-h-7 max-w-full items-center gap-1.5 px-1 text-chat-detail text-muted-foreground">
      <span aria-hidden="true" className="flex shrink-0 -space-x-1">
        {events.map(event => <SubAgentMark key={event.id} threadId={subAgentTimelineTaskId(event)!} compact />)}
      </span>
      <span className="flex min-w-0 items-center whitespace-pre">
        {parts.map((part, index) => {
          if (part.type === "literal") return <Fragment key={index}>{part.value}</Fragment>;
          const taskId = subAgentTimelineTaskId(events[member++])!;
          return <button key={taskId} type="button" onClick={() => onOpen(taskId)}
            data-subagent-activity={taskId} data-subagent-status="started"
            className="min-h-7 min-w-0 truncate rounded-sm hover:text-foreground hover:underline focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring">
            {part.value}
          </button>;
        })}
      </span>
      <span className="shrink-0 text-xs">{t("subagent.startedWork")}</span>
    </div>
  );
}
