import assert from "node:assert/strict";
import fs from "node:fs/promises";
import path from "node:path";
import { pathToFileURL } from "node:url";
import { build } from "esbuild";

const root = process.cwd();
const temp = await fs.mkdtemp(path.join(root, "tests", ".question-dock-"));
const output = path.join(temp, "render.mjs");
const mocks = {
  "./composer": 'export const Composer=({input})=>React.createElement("textarea",{"data-ordinary-composer":true,defaultValue:input});',
  "./background-goal-catch-up-card": "export const BackgroundGoalCatchUpCard=()=>null;",
  "./conversation-card": 'export const ConversationCard=()=>React.createElement("p",null,"Earlier conversation");',
  "./session-handoff-card": "export const SessionHandoffCard=()=>null;",
  "./session-handoff-send": "export const SessionHandoffSend=()=>null;",
  "../approvals/scoped-pending-approval-card": 'export const ScopedPendingApprovalCard=()=>React.createElement("section",{"data-approval-card":true},"Approve or reject");',
  "../ui/button": "export const Button=({children,...props})=>React.createElement('button',props,children);",
  "../../lib/api": "export const updateAgentGoal=async()=>({});",
  "../../lib/path-to-skill-context": "export const matchPathToSkillRuntimeOperation=()=>null;",
  "../../lib/chat-thread": "export const mergeConversationTimelineItems=x=>x;",
};
const entry = `
import React from 'react';
import {renderToStaticMarkup} from 'react-dom/server';
import {ChatWorkspace} from ${JSON.stringify(path.join(root, "src/components/chat/chat-workspace.tsx"))};
export function render(questions=[],approvals=[],hasHistory=true){return renderToStaticMarkup(React.createElement(ChatWorkspace,{
  input:'Keep my draft',sending:false,queueAllowed:true,conversation:hasHistory?[{id:'old',type:'user',text:'Earlier conversation'}]:[],
  queued:[],agentQuestions:questions,runtimeRuns:[],scopedPendingApprovals:approvals,approvalActions:{},activeGoal:null,
  attachments:[],commands:[],actions:[],projects:[],backgroundGoalDeliveries:[],backgroundGoalProviderWarnings:[],
  pendingApprovalForResponse:()=>null,onAnswerQuestion:()=>{},onStopQuestionContinuation:()=>{},
}));}
`;
try {
  await build({ stdin: { contents: entry, resolveDir: root, sourcefile: "question-dock-entry.mjs" },
    outfile: output, bundle: true, format: "esm", platform: "node", external: ["react", "react-dom/server"],
    plugins: [{ name: "dock-ports", setup(plugin) {
      plugin.onResolve({ filter: /^react-i18next$/ }, () => ({ path: "translation", namespace: "dock" }));
      plugin.onResolve({ filter: /^lucide-react$/ }, () => ({ path: "icons", namespace: "dock" }));
      plugin.onResolve({ filter: /.*/ }, args => args.importer.endsWith("chat-workspace.tsx") && mocks[args.path]
        ? { path: args.path, namespace: "dock" } : undefined);
      plugin.onLoad({ filter: /.*/, namespace: "dock" }, args => ({ contents: args.path === "translation"
        ? 'export const useTranslation=()=>({t:(key,fallback)=>typeof fallback==="string"?fallback:key});'
        : args.path === "icons"
          ? 'export const AlertTriangle=()=>null,ArrowDown=()=>null,Loader2=()=>null,Pause=()=>null,Play=()=>null,X=()=>null,ChevronLeft=()=>null,ChevronRight=()=>null;'
          : `import React from 'react';${mocks[args.path]}`, resolveDir: root }));
      if (process.env.QUESTION_DOCK_COUNTERFACTUAL === "1") {
        plugin.onLoad({ filter: /chat-workspace\.tsx$/ }, async args => ({
          contents: (await fs.readFile(args.path, "utf8"))
            .replaceAll('questionContinuationDisplay(question) !== "settled"', 'Boolean(question.runtimeContinuation) || question.status === "pending"')
            .replaceAll('(waitingForQuestion ? null : composer(false))', 'composer(false)')
            .replaceAll('(waitingForQuestion ? null : composer(true))', 'composer(true)'), loader: "tsx",
        }));
      }
    } }],
  });
  const { render } = await import(pathToFileURL(output).href);
  const pending = { questionId: "q", status: "pending", question: "Choose a repair", options: [
    { id: "install", label: "Reinstall", description: "Keep all user tools and the wardrobe." },
    { id: "later", label: "Later", description: "Leave the project unchanged." },
  ] };
  for (const hasHistory of [false, true]) {
    const html = render([pending], [], hasHistory);
    assert.ok(!html.includes("data-ordinary-composer"), "pending Question must replace the ordinary composer");
    assert.match(html, /Reinstall/); assert.match(html, /Later/);
    const custom = html.match(/<textarea\b[^>]*>/)?.[0];
    assert.ok(custom && !custom.includes("disabled"), "pending custom reply must be editable");
    if (hasHistory) assert.match(html, /Earlier conversation/);
    const settled = render([{ ...pending, status: "answered", runtimeContinuation: { status: "blocked" } }], [], hasHistory);
    assert.ok(!settled.includes("Choose a repair"), "answered historical cards must leave the dock");
    assert.match(settled, /data-ordinary-composer/); assert.match(settled, /Keep my draft/);
    const approval = render([], [{ id: "a", status: "pending" }], hasHistory);
    assert.match(approval, /data-approval-card/); assert.ok(!approval.includes("data-ordinary-composer"));
  }
  const running = render([{ ...pending, status: "answered", runtimeContinuation: { status: "running", turnId: "t" } }]);
  assert.match(running, />Stop</); assert.ok(!running.includes("Choose a repair"));
  assert.equal((running.match(/<textarea\b/g) || []).length, 1, "only ordinary composer returns while the continuation runs");
  console.log("question dock: pending replaces composer; editable custom answer; settled exits; active Stop retained");
} finally {
  assert.ok(path.resolve(temp).startsWith(path.join(root, "tests", ".question-dock-")));
  await fs.rm(temp, { recursive: true, force: true });
}
