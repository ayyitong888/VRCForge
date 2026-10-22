import assert from "node:assert/strict";
import fs from "node:fs/promises";
import path from "node:path";
import { pathToFileURL } from "node:url";
import { build } from "esbuild";

const root = process.cwd();
for (const locale of ["en-US", "zh-CN", "zh-TW", "ja-JP"]) {
  const messages = JSON.parse(await fs.readFile(path.join(root, "src", "locales", `${locale}.json`), "utf8"));
  assert.ok(messages.questionCard?.visualCheckpoint?.options, `${locale} visual checkpoint options must live under questionCard`);
  for (const key of ["pass", "fail", "uncertain", "later"]) {
    assert.equal(typeof messages.questionCard.visualCheckpoint.options[key], "string", `${locale} missing questionCard visual option ${key}`);
    assert.equal(messages.encryption?.visualCheckpoint?.options?.[key], undefined, `${locale} must not source visual options from encryption`);
  }
}
const temp = await fs.mkdtemp(path.join(root, "tests", ".question-dock-"));
const output = path.join(temp, "render.mjs");
const mocks = {
  "./composer": 'export const Composer=(props)=>{globalThis.__questionDockComposerProps=props; return React.createElement(React.Fragment,null,React.createElement("textarea",{"data-ordinary-composer":true,defaultValue:props.input}),React.createElement("input",{type:"file","data-composer-attachment":true,onChange:()=>{}}),props.sending?React.createElement("button",{"data-composer-stop":true,disabled:props.stopDisabled,onClick:props.onStop},"Stop"):null);};',
  "./background-goal-catch-up-card": "export const BackgroundGoalCatchUpCard=()=>null;",
  "./conversation-card": 'export const ConversationCard=()=>React.createElement("p",null,"Earlier conversation"); export const MessageDateSeparator=()=>null;',
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
export function render(questions=[],approvals=[],hasHistory=true,sending=false,pendingReplyQuestionId=''){
  let stoppedQuestionId=''; let ordinaryStopCount=0; globalThis.__questionDockComposerProps=undefined;
  const html=renderToStaticMarkup(React.createElement(ChatWorkspace,{
  input:'Keep my draft',sending,queueAllowed:sending,conversation:hasHistory?[{id:'old',type:'user',text:'Earlier conversation'}]:[],
  queued:[],agentQuestions:questions,runtimeRuns:[],scopedPendingApprovals:approvals,approvalActions:{},activeGoal:null,
  pendingReplyQuestionId,onReplyInChat:()=>{},onCancelReply:()=>{},focusInput:Boolean(pendingReplyQuestionId),
  attachments:[],commands:[],actions:[],projects:[],backgroundGoalDeliveries:[],backgroundGoalProviderWarnings:[],
  pendingApprovalForResponse:()=>null,onAnswerQuestion:()=>{},onStopQuestionContinuation:(question)=>{stoppedQuestionId=question.questionId;},onStop:()=>{ordinaryStopCount++;},
}));
  return {html,composer:globalThis.__questionDockComposerProps,stoppedQuestionId:()=>stoppedQuestionId,ordinaryStopCount:()=>ordinaryStopCount};
}
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
          ? 'export const AlertTriangle=()=>null,ArrowDown=()=>null,ArrowUp=()=>null,Loader2=()=>null,MoreHorizontal=()=>null,Pause=()=>null,Play=()=>null,X=()=>null,ChevronLeft=()=>null,ChevronRight=()=>null,HelpCircle=()=>null,Pencil=()=>null;'
          : `import React from 'react';${mocks[args.path]}`, resolveDir: root }));
      if (process.env.QUESTION_DOCK_COUNTERFACTUAL === "1") {
        plugin.onLoad({ filter: /chat-workspace\.tsx$/ }, async args => ({
          contents: (await fs.readFile(args.path, "utf8"))
            .replaceAll('questionContinuationDisplay(question) !== "settled"', 'Boolean(question.runtimeContinuation) || question.status === "pending"')
            .replaceAll('approvalComposer || composer(false)', 'approvalComposer || (pendingQuestionPrompts.length ? null : composer(false))')
            .replaceAll('approvalComposer || composer(true)', 'approvalComposer || (pendingQuestionPrompts.length ? null : composer(true))'), loader: "tsx",
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
    const {html, composer} = render([pending], [], hasHistory);
    assert.equal(composer.questionPromptVisible, true, "pending ask uses the single-row chat composer");
    assert.match(html, /data-ordinary-composer/, "pending Question must retain the ordinary composer");
    assert.match(html, /data-composer-attachment/, "normal chat attachments remain available below ask");
    assert.equal((html.match(/data-ordinary-composer/g) || []).length, 1);
    assert.match(html, /Reinstall/); assert.match(html, /Later/);
    const custom = html.match(/<textarea\b[^>]*>/)?.[0];
    assert.ok(custom && !custom.includes("disabled"), "pending custom reply must be editable");
    if (hasHistory) assert.match(html, /Earlier conversation/);
    const settled = render([{ ...pending, status: "answered", runtimeContinuation: { status: "blocked" } }], [], hasHistory).html;
    assert.ok(!settled.includes("Choose a repair"), "answered historical cards must leave the dock");
    assert.match(settled, /data-ordinary-composer/); assert.match(settled, /Keep my draft/);
    assert.equal(render([], [], hasHistory).composer.questionPromptVisible, false, "without ask the ordinary composer height is restored");
    const approval = render([], [{ id: "a", status: "pending" }], hasHistory).html;
    assert.match(approval, /data-approval-card/); assert.ok(!approval.includes("data-ordinary-composer"));
  }
  const running = render([{ ...pending, status: "answered", runtimeContinuation: { status: "running", turnId: "t" } }]);
  assert.ok(!running.html.includes("data-question-continuation-status"), "active continuation must not leave a question-specific dock bar");
  assert.ok(!running.html.includes("Choose a repair"), "answered question card must leave the dock");
  assert.equal((running.html.match(/data-composer-stop/g) || []).length, 1, "active continuation uses the ordinary Composer Stop control");
  assert.equal(running.composer.sending, true, "continuation state must reach the shared Composer busy state");
  assert.equal(running.composer.stopDisabled, false, "the shared Stop is enabled once the continuation has a cancellable identity");
  await running.composer.onStop();
  assert.equal(running.stoppedQuestionId(), "q", "shared Composer Stop cancels the active question continuation");
  const concurrent = render([{ ...pending, status: "answered", runtimeContinuation: { status: "running", turnId: "t" } }], [], true, true);
  await concurrent.composer.onStop();
  assert.equal(concurrent.ordinaryStopCount(), 1, "an existing chat run keeps ownership of the shared Stop control");
  assert.equal(concurrent.stoppedQuestionId(), "", "question Stop does not intercept an unrelated active chat run");
  const queued = render([{ ...pending, status: "answered", runtimeContinuation: { status: "queued" } }]);
  assert.equal(queued.composer.sending, true, "queued continuation reaches the shared Composer busy state");
  assert.equal(queued.composer.stopDisabled, true, "Stop stays disabled until the queued run exposes a cancellable identity");
  const cancelling = render([{ ...pending, status: "answered", runtimeContinuation: { status: "cancelling", turnId: "t" } }]);
  assert.equal(cancelling.composer.sending, true, "cancelling continuation remains in the shared Composer busy state");
  assert.equal(cancelling.composer.stopDisabled, true, "a second stop is disabled while cancellation is pending");
  const replying = render([pending], [], true, false, "q");
  assert.match(replying.html, /data-question-reply-mode/);
  assert.match(replying.html, /data-ordinary-composer/);
  assert.match(replying.html, /data-composer-attachment/);
  assert.ok(!replying.html.includes("Reinstall"), "reply mode hides the option card while retaining the composer");
  const restored = render([pending], [], true, false, "");
  assert.match(restored.html, /Reinstall/);
  assert.ok(!restored.html.includes("data-question-reply-mode"));
  console.log("question dock: pending retains composer and attachments; custom reply editable; success exits; continuation uses shared Composer Stop");
} finally {
  assert.ok(path.resolve(temp).startsWith(path.join(root, "tests", ".question-dock-")));
  await fs.rm(temp, { recursive: true, force: true });
}
