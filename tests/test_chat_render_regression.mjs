import assert from "node:assert/strict";
import { cpSync, mkdirSync, readFileSync, rmSync, writeFileSync } from "node:fs";
import { mkdtempSync } from "node:fs";
import Module from "node:module";
import { resolve } from "node:path";
import { execFileSync } from "node:child_process";
import { build } from "esbuild";

const root = resolve(import.meta.dirname, "..");
const fixtureSource = `
  import React from "react";
  import { renderToStaticMarkup } from "react-dom/server";
  import i18n from "./src/i18n";
  import { initReactI18next } from "react-i18next";
  import { ConversationCard } from "./src/components/chat/conversation-card";
  i18n.use(initReactI18next).init({lng:"en", resources:{en:{translation:{}}}, interpolation:{escapeValue:false}});
  const event = (id, sequence, kind, payload) => ({id, sequence, timestamp: ` + "`2026-09-22T00:00:0${sequence}.000Z`" + `, kind, payload});
  const timeline = [
    event("call-a", 1, "tool_call", {actionId:"a", tool:"vrcforge_read_text_file", status:"started"}),
    event("result-a", 2, "tool_result", {actionId:"a", tool:"vrcforge_read_text_file", status:"ok", summary:"first"}),
    event("call-b", 3, "tool_call", {actionId:"b", tool:"vrcforge_find_files", status:"started"}),
    event("result-b", 4, "tool_result", {actionId:"b", tool:"vrcforge_find_files", status:"ok", summary:"second"}),
    event("plan", 5, "planner", {label:"Checking", summary:"I will check the next item."}),
    event("call-c", 6, "tool_call", {actionId:"c", tool:"vrcforge_read_text_file", status:"started"}),
    event("result-c", 7, "tool_result", {actionId:"c", tool:"vrcforge_read_text_file", status:"ok", summary:"third"}),
    event("call-d", 8, "tool_call", {actionId:"d", tool:"vrcforge_find_files", status:"started"}),
    event("result-d", 9, "tool_result", {actionId:"d", tool:"vrcforge_find_files", status:"ok", summary:"fourth"}),
    event("answer", 10, "assistant", {summary:"final answer", status:"done"}),
  ];
  const render = (item) => renderToStaticMarkup(React.createElement(ConversationCard, {item}));
  export function run() {
    const durable = render({id:"agent", type:"agent", response:{plan:{reply:"final answer",summary:"done",planner:"fixture",shellNeeded:false,nextStep:"done"},timeline}});
    const streaming = render({id:"stream", type:"streaming", clientTurnId:"turn", text:"final answer", timeline});
    const pureFinal = render({id:"pure", type:"agent", response:{plan:{reply:"pure final",summary:"done",planner:"fixture",shellNeeded:false,nextStep:"done"},timeline:[timeline.at(-1)]}});
    const failedTimeline = timeline.map((item) => item.id === "result-d" ? {...item, payload:{...item.payload, status:"failed"}} : item);
    const failed = render({id:"failed", type:"agent", response:{plan:{reply:"failed final",summary:"done",planner:"fixture",shellNeeded:false,nextStep:"done"},timeline:failedTimeline}});
    return {durable, streaming, pureFinal, failed};
  }
`;

async function compileAt(resolveDir) {
  const compiled = await build({
    stdin: { resolveDir, loader: "tsx", contents: fixtureSource },
    bundle: true,
    write: false,
    platform: "node",
    format: "cjs",
    external: ["react", "react-dom/server", "react/jsx-runtime", "i18next", "react-i18next"],
    jsx: "automatic",
  });
  const filename = resolve(root, "tests", "chat-render-fixture.cjs");
  const module = new Module(filename);
  module.paths = Module._nodeModulePaths(root);
  module._compile(compiled.outputFiles[0].text, filename);
  return module.exports.run();
}

function checkGreen(result) {
  assert.equal((result.durable.match(/data-agent-turn-process-group/g) || []).length, 1,
    "one Agent turn must have one outer process group");
  assert.match(result.durable, /data-agent-turn-process-group[\s\S]*final answer/,
    "the final answer remains after the process group");
  assert.equal((result.streaming.match(/final answer/g) || []).length, 1,
    "the complete streaming answer must not duplicate the durable assistant event");
  assert.equal((result.pureFinal.match(/data-agent-turn-process-group/g) || []).length, 0,
    "a pure final answer must not create an empty process group");
  assert.match(result.failed, /data-agent-turn-process-group[\s\S]*text-destructive/,
    "a failed invocation exposes the outer process status");
}

const current = await compileAt(root);
checkGreen(current);

const baselineArg = process.argv.find((arg) => arg.startsWith("--baseline="));
if (baselineArg) {
  const baseline = baselineArg.slice("--baseline=".length).trim();
  assert.match(baseline, /^[A-Za-z0-9._/-]+$/, "baseline ref must be a simple git ref");
  const resolvedRef = execFileSync("git", ["rev-parse", "--verify", `${baseline}^{commit}`], { cwd: root, encoding: "utf8" }).trim();
  assert.match(resolvedRef, /^[0-9a-f]{40}$/i, "baseline ref must resolve to a commit");
  mkdirSync(resolve(root, "local-review"), { recursive: true });
  const oldRoot = mkdtempSync(resolve(root, "local-review", "chat-render-old-"));
  try {
    cpSync(resolve(root, "src"), resolve(oldRoot, "src"), { recursive: true });
    for (const file of ["components/chat/conversation-timeline.tsx", "components/chat/conversation-card.tsx"]) {
      const oldText = execFileSync("git", ["show", `${resolvedRef}:src/${file}`], { cwd: root, encoding: "utf8" });
      writeFileSync(resolve(oldRoot, "src", file), oldText);
    }
    const old = await compileAt(oldRoot);
    const oldRed = {
      processGroups: (old.durable.match(/data-agent-turn-process-group/g) || []).length,
      streamedAnswerCopies: (old.streaming.match(/final answer/g) || []).length,
    };
    assert.equal(oldRed.processGroups, 0, "the baseline fixture must lack the unified process group");
    assert.ok(oldRed.streamedAnswerCopies >= 2, "the baseline fixture must reproduce duplicate streaming final text");
    const logPath = resolve(root, "local-review", "chat-render-regression-red-green.log");
    writeFileSync(logPath, [
      `baseline ${baseline} (${resolvedRef}): RED`,
      `processGroups=${oldRed.processGroups}`,
      `streamedAnswerCopies=${oldRed.streamedAnswerCopies}`,
      "current working tree: GREEN",
      "processGroups=1",
      "streamedAnswerCopies=1",
    ].join("\n") + "\n");
  } finally {
    rmSync(oldRoot, { recursive: true, force: true });
  }
}

console.log("chat render regression: old RED / current GREEN");
