import assert from "node:assert/strict";
import fs from "node:fs/promises";
import path from "node:path";
import { fileURLToPath, pathToFileURL } from "node:url";
import { build } from "esbuild";

const root = path.resolve(path.dirname(fileURLToPath(import.meta.url)), "..");
const temp = path.join(root, "tests", ".tmp-runtime-tool-loop-question");
await fs.rm(temp, { recursive: true, force: true });
await fs.mkdir(temp, { recursive: true });
const output = path.join(temp, "question-card.mjs");
const entry = path.join(temp, "entry.mjs");
const mocks = {
  translation: path.join(temp, "react-i18next.mjs"),
  icons: path.join(temp, "lucide-react.mjs"),
  utils: path.join(temp, "utils.mjs"),
  continuation: path.join(temp, "continuation.mjs"),
  markdown: path.join(temp, "markdown.mjs"),
};
await Promise.all([
  fs.writeFile(mocks.translation, 'export function useTranslation(){return {t:(key,fallback)=>typeof fallback === "string" ? fallback : key};}\n'),
  fs.writeFile(mocks.icons, 'export const ChevronLeft=()=>null; export const ChevronRight=()=>null; export const HelpCircle=()=>null; export const Pencil=()=>null; export const X=()=>null; export const Minus=()=>null; export const Plus=()=>null;\n'),
  fs.writeFile(mocks.utils, 'export const cn=(...values)=>values.filter(Boolean).join(" "); export const formatCount=(value)=>String(value);\n'),
  fs.writeFile(mocks.continuation, 'export const questionContinuationDisplay=()=>"none";\n'),
  fs.writeFile(mocks.markdown, 'export const ChatMarkdown=()=>null; export const ChatCaptureThumbnail=()=>null; export const ChatResourceImageContext=({children})=>children;\n'),
  fs.writeFile(entry, `
    import React from "react";
    import { renderToStaticMarkup } from "react-dom/server";
    import { AgentQuestionCard } from ${JSON.stringify(path.join(root, "src/components/chat/agent-question-card.tsx"))};
    const special = renderToStaticMarkup(React.createElement(AgentQuestionCard, {
      questions: [{ questionId: "q-loop", sourceTool: "runtime_tool_loop", status: "pending",
        question: "Three rounds repeated the same read.", options: [
          { id: "continue", label: "Continue", value: "Continue" },
          { id: "stop", label: "Stop", value: "Stop" },
        ] }],
      onAnswerQuestion: () => {}, onReplyInChat: () => {},
    }));
    if (!special.includes("Three rounds repeated the same read.") || !special.includes("Continue") || !special.includes("Stop"))
      throw new Error("runtime tool-loop pause must show its reason and the two decisions");
    for (const hidden of ["questionCard.customPlaceholder", "questionCard.skip", "data-question-reply-in-chat"])
      if (special.includes(hidden)) throw new Error("runtime tool-loop pause exposed an extra answer path: " + hidden);
    const ordinary = renderToStaticMarkup(React.createElement(AgentQuestionCard, {
      questions: [{ questionId: "q-normal", sourceTool: "call_user", status: "pending", question: "Ordinary?", options: [] }],
      onAnswerQuestion: () => {}, onReplyInChat: () => {},
    }));
    if (!ordinary.includes("questionCard.customPlaceholder") || !ordinary.includes("questionCard.skip") || !ordinary.includes("data-question-reply-in-chat"))
      throw new Error("ordinary Question behavior must remain available");
    console.log(JSON.stringify({ runtimeToolLoop: "continue-stop-only", ordinaryQuestion: "unchanged" }));
  `),
]);

try {
  await build({
    entryPoints: [entry], outfile: output, bundle: true, format: "esm", platform: "node",
    external: ["react", "react-dom/server"],
    plugins: [{ name: "runtime-tool-loop-question-mocks", setup(plugin) {
      plugin.onResolve({ filter: /^react-i18next$/ }, () => ({ path: mocks.translation }));
      plugin.onResolve({ filter: /^lucide-react$/ }, () => ({ path: mocks.icons }));
      plugin.onResolve({ filter: /\/lib\/utils$/ }, () => ({ path: mocks.utils }));
      plugin.onResolve({ filter: /\/use-runtime-turn-continuation$/ }, () => ({ path: mocks.continuation }));
      plugin.onResolve({ filter: /\/chat-markdown$/ }, () => ({ path: mocks.markdown }));
    } }],
  });
  await import(`${pathToFileURL(output).href}?case=runtime-tool-loop-question`);
} finally {
  await fs.rm(temp, { recursive: true, force: true });
}
