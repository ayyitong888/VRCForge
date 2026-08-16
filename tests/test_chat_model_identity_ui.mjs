import assert from "node:assert/strict";
import { readFile } from "node:fs/promises";
import { resolve } from "node:path";

const root = resolve(import.meta.dirname, "..");
const read = (path) => readFile(resolve(root, path), "utf8");
const [card, composer, timeline, streaming] = await Promise.all([
  read("src/components/chat/conversation-card.tsx"),
  read("src/components/chat/composer.tsx"),
  read("src/components/chat/conversation-timeline.tsx"),
  read("src/lib/chat-streaming.ts"),
]);

const mustContain = (content, fragment, label) => assert.ok(content.includes(fragment), label);
const mustNotContain = (content, fragment, label) => assert.ok(!content.includes(fragment), label);
mustContain(composer, "const providerModelLabel =", "composer computes full runtime provider+model label");
mustContain(composer, "{providerModelLabel ? (", "composer only renders label when available");
mustContain(composer, "title={providerModelLabel}", "composer exposes full model label in tooltip");
mustContain(composer, "aria-label={providerModelLabel}", "composer exposes full model label via aria");
mustContain(composer, "xl:grid-cols-[auto_minmax(0,1fr)_auto]", "wide composer keeps controls, full model identity, and usage together");
mustContain(composer, "col-span-2 col-start-1 row-start-2 min-w-0 break-words", "narrow composer gives the full provider/model identity a wrapping row");
mustNotContain(composer, "row-start-2 truncate", "narrow composer must not truncate the model identity");
mustContain(composer, "{providerModelLabel}", "composer renders provider model label body");
mustNotContain(composer, 'providerLabel || t("provider.apiProvider")', "composer should not fallback to static API provider text");
mustContain(composer, '.join(" · ")', "composer uses the intended provider/model separator");
mustNotContain(composer, "�", "composer source must not contain replacement characters");
mustNotContain(composer, "��", "composer source must not contain mojibake separators");
mustContain(composer, 'source: "unavailable" as const', "composer keeps an unavailable context meter when runtime usage is absent");
mustContain(composer, 'data-context-percent={knownRatio ? String(percent) : "unknown"}', "unknown usage is not rendered as fake zero percent");
mustContain(composer, "data-context-ring", "context usage uses the compact circular meter");
mustContain(composer, 'className="stroke-border"', "context usage keeps a visible continuous base ring");
mustNotContain(composer, 'strokeDasharray="4 5"', "unknown context usage must not render as a dashed ring");
mustContain(composer, 'percent >= 90 ? "stroke-destructive" : percent >= 60 ? "stroke-amber-500" : "stroke-primary"', "context ring preserves the 60 and 90 percent color thresholds");
mustContain(composer, 'data-context-segment={percent >= 90 ? "high" : percent >= 60 ? "medium" : "low"}', "context ring preserves semantic color segments");

mustNotContain(card, 'displayPlanner("llm")', "streaming/agent cards should not impersonate default planner");
mustContain(card, "const runtimeModelLabel = formatRuntimeModelLine(item.providerLabel, item.model);", "streaming card now uses runtime model formatter");
mustContain(card, "<span>{runtimeModelLabel}</span>", "streaming card renders runtime model label");
mustContain(card, "const providerLine = formatRuntimeModelLine(item.providerLabel, item.model);", "agent result card uses runtime source helper");
mustContain(card, "planLabel: replySource,", "agent timeline receives per-turn source identity label");

mustContain(timeline, "const shouldSurfaceModelIdentity = Boolean(response.plan.plannerFailure?.code);", "timeline tracks planner-failure path for identity");
mustContain(timeline, 'rows.push(renderPlanIdentityRow(planLabel, elapsedSeconds, t, "assistant-model"));', "durable assistant failure path surfaces identity");
mustContain(timeline, 'rows.push(renderPlanIdentityRow(planLabel, elapsedSeconds, t, "reply-model"));', "replyless failure path surfaces identity");
mustContain(timeline, "{planLabel ? <span>{planLabel}</span> : null}", "identity row only shown when metadata exists");
mustContain(timeline, "export function formatRuntimeModelLine", "shared runtime model formatter is exported");

mustContain(streaming, "type AgentRuntimePhase", "chat-streaming still owns runtime phase contract");

console.log("chat model identity UI contract: ok");
