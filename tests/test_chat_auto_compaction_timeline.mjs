import assert from "node:assert/strict";
import Module from "node:module";
import { fileURLToPath } from "node:url";
import { build } from "esbuild";

const bundle = await build({
  stdin: { resolveDir: process.cwd(), loader: "tsx", contents: `
    import React from "react";
    import { renderToStaticMarkup } from "react-dom/server";
    import { ConversationCard } from "./src/components/chat/conversation-card";
    import { buildDurableTimelineRows } from "./src/components/chat/conversation-timeline";
    import { normalizeAgentRuntimeTimelineEvent } from "./src/lib/chat-streaming";
    import { buildTimelinePresentation, materializeRuntimeTimeline, mergeRuntimeTimelines } from "./src/lib/chat-timeline-presentation";
    import { buildChatHistory } from "./src/lib/conversation-utils";

    const before = { id: "first", sequence: 0, timestamp: "2026-11-09T12:00:00Z",
      kind: "assistant", payload: { phase: "commentary", summary: "Before compacting" } };
    const compacted = { id: "compacted", sequence: 1, timestamp: "2026-11-09T12:00:01Z",
      kind: "context_compaction", payload: { status: "completed" } };
    const after = { id: "last", sequence: 2, timestamp: "2026-11-09T12:00:02Z",
      kind: "assistant", payload: { phase: "commentary", summary: "After compacting" } };
    const response = { plan: { planner: "llm" }, timeline: [before, compacted, after] };
    const streamed = normalizeAgentRuntimeTimelineEvent(compacted);
    const recovered = materializeRuntimeTimeline(JSON.parse(JSON.stringify(response)));
    const childEvents = JSON.parse(JSON.stringify(response.timeline));
    const hiddenSummary = { id: "summary", type: "compact", status: "completed", timelineEventId: "compacted",
      text: "Context compressed", detail: "Summary for model context" };
    const render = (events) => renderToStaticMarkup(<>{buildDurableTimelineRows(events, undefined, "", [], { live: true })}</>);
    export const live = render(mergeRuntimeTimelines([streamed], recovered));
    export const child = render(mergeRuntimeTimelines(childEvents));
    export const retry = buildTimelinePresentation([{ ...compacted, payload: { status: "failed" } }]).entries;
    export const hidden = renderToStaticMarkup(<ConversationCard item={hiddenSummary} />);
    export const history = buildChatHistory([hiddenSummary], (key) => key);
  ` },
  bundle: true, write: false, platform: "node", format: "cjs", jsx: "automatic",
  external: ["react", "react-dom/server", "react/jsx-runtime", "i18next", "react-i18next"],
});
const filename = fileURLToPath(new URL("./auto-compaction-fixture.cjs", import.meta.url));
const compiled = new Module(filename);
compiled.paths = Module._nodeModulePaths(process.cwd());
compiled._compile(bundle.outputFiles[0].text, filename);
const { live, child, retry, hidden, history } = compiled.exports;
for (const markup of [live, child]) {
  assert.equal((markup.match(/data-agent-context-compaction/g) || []).length, 1);
  assert.ok(markup.indexOf("Before compacting") < markup.indexOf("data-agent-context-compaction"));
  assert.ok(markup.indexOf("data-agent-context-compaction") < markup.indexOf("After compacting"));
}
assert.deepEqual(retry, [], "a failed compaction never looks completed");
assert.equal(hidden, "", "the legacy summary carrier does not render a second marker");
assert.deepEqual(history, [{ role: "agent", text: "Summary for model context" }]);
console.log("parent and child auto-compaction timeline: ok");
