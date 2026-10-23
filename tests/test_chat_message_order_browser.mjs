import assert from "node:assert/strict";
import { execFileSync } from "node:child_process";
import { readFile } from "node:fs/promises";
import { createRequire } from "node:module";
import { resolve } from "node:path";
import { build } from "esbuild";

const root = process.cwd();
const { chromium } = createRequire(import.meta.url)(process.env.VRCFORGE_PLAYWRIGHT_PATH || "playwright");
const replayPath = process.env.VRCFORGE_TURN_REPLAY_PATH;
const replay = replayPath ? JSON.parse(await readFile(resolve(replayPath), "utf8")) : undefined;
const rawTimeline = replay?.response?.timeline;
const sourceEvents = rawTimeline || [
  { id: "fixture-call", sequence: 10, timestamp: "2026-10-23T08:50:00.000+09:00", kind: "tool_call", payload: { status: "started" } },
  { id: "fixture-result", sequence: 11, timestamp: "2026-10-23T08:50:01.000+09:00", kind: "tool_result", payload: { status: "ok" } },
];
const toolCall = sourceEvents.find((event) => event.kind === "tool_call");
const toolResult = sourceEvents.find((event) => event.kind === "tool_result" && event.sequence > toolCall?.sequence);
assert.ok(toolCall && toolResult, "timeline fixture contains a real or generic tool call/result pair");
// Project only structural fields and generic labels; do not persist receipt text or tool payloads.
const clientTurnId = replay?.response?.clientTurnId || "message-order-fixture";
const createdAt = sourceEvents[0].timestamp;
const timeline = [toolCall, toolResult].map((event, index) => ({
  id: `fixture-${index}`,
  sequence: event.sequence,
  timestamp: event.timestamp,
  kind: event.kind,
  payload: { actionId: "fixture-action", tool: "fixture_tool", status: event.payload.status, summary: event.kind === "tool_result" ? "fixture result" : undefined },
}));
const user = { id: `user-${clientTurnId}`, type: "user", text: "fixture user message", clientTurnId, createdAt };
const streaming = { id: `stream-${clientTurnId}`, type: "streaming", clientTurnId, text: "", phase: "waiting_for_model", createdAt, providerLastActivityAt: createdAt, timeline };

const mocks = {
  "./composer": 'export const Composer=()=>React.createElement("textarea");',
  "./background-goal-catch-up-card": "export const BackgroundGoalCatchUpCard=()=>null;",
  "./conversation-card": `export const MessageDateSeparator=({timestamp})=>React.createElement("div",{"data-chat-date-separator":true},timestamp); export const ConversationCard=({item,initialUserContent})=>React.createElement("div",{"data-conversation-card":item.id,"data-user-message":item.type==="user"?true:undefined},initialUserContent,item.timeline?.filter(event=>event.kind==="tool_call").map(event=>React.createElement("div",{"data-fixture-tool-call":true,key:event.id},event.payload.tool))); export const StreamingPhaseStatus=({item})=>React.createElement("div",{"data-chat-current-activity":true,"data-turn":item.clientTurnId},"Waiting for model");`,
  "./session-handoff-card": "export const SessionHandoffCard=()=>null;",
  "./session-handoff-send": "export const SessionHandoffSend=()=>null;",
  "../approvals/scoped-pending-approval-card": "export const ScopedPendingApprovalCard=()=>null;",
  "../ui/button": 'export const Button=({children,...props})=>React.createElement("button",props,children);',
  "../../lib/api": "export const updateAgentGoal=async()=>({});",
  "../../lib/path-to-skill-context": "export const matchPathToSkillRuntimeOperation=()=>null;",
  "../../lib/chat-thread": "export const mergeConversationTimelineItems=x=>x;",
};

async function bundleWorkspace(sourceOverride, conversationItems) {
  const entry = `import React from 'react'; import {createRoot} from 'react-dom/client'; import {ChatWorkspace} from './src/components/chat/chat-workspace'; window.renderFixture=()=>createRoot(document.getElementById('root')).render(React.createElement(ChatWorkspace,{
    projectPromptTitle:'',input:'',setInput:()=>{},sending:true,queueAllowed:false,onSubmit:()=>{},onSwitchMode:()=>{},commands:[],actions:[],onAction:()=>{},disabledReason:'',attachments:[],onAttachFiles:()=>{},onRemoveAttachment:()=>{},providerLabel:'',model:'',activeGoal:null,onGoalChanged:()=>{},goalEndpoint:'',projects:[],onBindProject:()=>{},conversation:${JSON.stringify(conversationItems)},queued:[],agentQuestions:[],backgroundGoalDeliveries:[],backgroundGoalProviderWarnings:[],onBackgroundGoalCatchUpRendered:()=>{},onBackgroundGoalProviderWarningsRendered:()=>{},onBackgroundGoalCatchUpDismiss:()=>{},onAnswerQuestion:()=>{},conversationEndRef:null,onConversationMouseUp:()=>{},onConversationScroll:()=>{},showScrollToBottom:false,onScrollToBottom:()=>{},pendingApprovalForResponse:()=>null,scopedPendingApprovals:[],approvalActions:{},latestRetryableItemId:'',latestEditableUserItemId:'',editingItemId:'',editingText:'',editingAttachments:[],onEditItemChangeText:()=>{},onEditItemRemoveAttachment:()=>{},onCopyItem:()=>{},onRetryItem:()=>{},onEditItem:()=>{},onEditItemSave:()=>{},onEditItemCancel:()=>{},onApprove:()=>{},onReject:()=>{},onModifyApproval:()=>{},onImportAttachment:()=>{},onOpenDoctor:()=>{},runtimeRuns:[],onSaveOperationAsSkill:()=>{}
  }));`;
  const plugins = [{ name: "workspace-order-fixture", setup(plugin) {
    plugin.onResolve({ filter: /^react-i18next$/ }, () => ({ path: "translation", namespace: "fixture" }));
    plugin.onResolve({ filter: /^lucide-react$/ }, () => ({ path: "icons", namespace: "fixture" }));
    plugin.onLoad({ filter: /.*/, namespace: "fixture" }, (args) => ({ contents: args.path === "icons"
      ? 'export const AlertTriangle=()=>null,ArrowDown=()=>null,ArrowUp=()=>null,Loader2=()=>null,MoreHorizontal=()=>null,Pause=()=>null,Play=()=>null,X=()=>null,ChevronLeft=()=>null,ChevronRight=()=>null,HelpCircle=()=>null,Pencil=()=>null;'
      : 'export const useTranslation=()=>({t:key=>key});' }));
    plugin.onResolve({ filter: /.*/ }, (args) => args.importer.endsWith("chat-workspace.tsx") && mocks[args.path]
      ? { path: args.path, namespace: "workspace-mock" } : undefined);
    plugin.onLoad({ filter: /.*/, namespace: "workspace-mock" }, (args) => ({ contents: `import React from 'react';${mocks[args.path]}`, resolveDir: root }));
    if (sourceOverride) plugin.onLoad({ filter: /chat-workspace\.tsx$/ }, () => ({ contents: sourceOverride, loader: "tsx" }));
  } }];
  const built = await build({ stdin: { contents: entry, resolveDir: root, loader: "tsx" }, bundle: true, write: false, format: "iife", platform: "browser", jsx: "automatic", plugins });
  return built.outputFiles[0].text;
}

async function orderFrom(sourceOverride, conversationItems) {
  const bundle = await bundleWorkspace(sourceOverride, conversationItems);
  const page = await browser.newPage();
  await page.setContent('<div id="root"></div>');
  await page.addScriptTag({ content: bundle });
  await page.evaluate(() => window.renderFixture());
  await page.locator("[data-chat-current-activity]").waitFor();
  const order = await page.locator("[data-chat-history-scroll]").evaluate((history) => {
    const date = history.querySelector("[data-chat-date-separator]");
    const userNode = history.querySelector("[data-user-message]");
    const tool = history.querySelector("[data-fixture-tool-call]");
    const status = history.querySelector("[data-chat-current-activity]");
    return [date, userNode, tool, status].map((node) => node ? [...history.querySelectorAll("*")].indexOf(node) : -1);
  });
  assert.ok(order.every((index) => index >= 0), "date, user message, tool call and waiting status must all be present");
  await page.close();
  return order;
}

const browser = await chromium.launch({ channel: "msedge", headless: true });
try {
  const baselineRef = process.env.VRCFORGE_BASELINE_REF;
  const oldSource = baselineRef
    ? execFileSync("git", ["show", `${baselineRef}:src/components/chat/chat-workspace.tsx`], { cwd: root, encoding: "utf8" })
    : undefined;
  const externalUser = { ...user, id: "external-user", clientTurnId: undefined };
  for (const [layout, items] of [["projected-user", [user, streaming]], ["standalone-user", [externalUser, streaming]]]) {
    if (oldSource) {
      const before = await orderFrom(oldSource, items);
      assert.ok(before.every((index) => index >= 0), `${layout}: 修前组件必须包含所有目标节点`);
      assert.ok(before[3] < before[0], `${layout}: 修前回放应将等待状态错误地放在日期分隔之前`);
    }
    const after = await orderFrom(undefined, items);
    assert.ok(after[0] < after[1] && after[1] < after[2] && after[2] < after[3], `${layout}: 日期、用户、工具调用、等待状态按预期顺序渲染`);
  }
  console.log(oldSource ? "chat message order: baseline RED / current GREEN" : "chat message order: current GREEN");
} finally {
  await browser.close();
}
