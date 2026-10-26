// Production workspace and scroll owner, synthetic conversations, no model or asset writes.
import assert from "node:assert/strict";
import { readFileSync, mkdirSync } from "node:fs";
import { createServer } from "node:http";
import { createRequire } from "node:module";
import { resolve } from "node:path";
import { build } from "esbuild";
import postcss from "postcss";
import tailwindcss from "tailwindcss";

const { chromium } = createRequire(import.meta.url)("playwright");
const root = process.cwd();
const css = (await postcss([tailwindcss(resolve(root, "tailwind.config.ts"))]).process(
  readFileSync("src/styles.css", "utf8").replace('@import "./theme.css";', readFileSync("src/theme.css", "utf8")),
  { from: resolve(root, "src/styles.css") },
)).css;
const bundled = await build({ stdin: { resolveDir: root, loader: "tsx", contents: `
import React, {useState, useRef} from 'react';
import {createRoot} from 'react-dom/client';
import {ChatWorkspace} from './src/components/chat/chat-workspace';
import {useConversationScroll} from './src/hooks/use-conversation-scroll';
import {initializeI18n} from './src/i18n';
const noop=()=>{};
const user=(i)=>({id:'user-'+i,type:'user',clientTurnId:'turn-'+i,text:'Prompt '+i+': inspect this part of the conversation.',createdAt:'2026-10-26T00:00:00Z'});
const answer=(i)=>({id:'answer-'+i,type:'agent',response:{ok:true,clientTurnId:'turn-'+i,plan:{nextStep:'done',reply:('Answer '+i+'. Content that makes this a scrollable conversation.\\n\\n').repeat(7)},observe:{}}});
const history=Array.from({length:80},(_,i)=>[user(i),answer(i)]).flat();
function Fixture(){
 const [chat,setChat]=useState('chat-a');const [items,setItems]=useState(history);const [draft,setDraft]=useState('');const [runs,setRuns]=useState([]);const endRef=useRef(null);
 const scroll=useConversationScroll({chatKey:chat,endRef});
 window.switchChat=()=>{setChat('chat-b');setItems([user(100),answer(100),user(101),answer(101)]);};
 window.grow=()=>setItems(s=>s.map((item,i)=>i===s.length-1?{...item,response:{...item.response,plan:{...item.response.plan,reply:item.response.plan.reply+'\\n\\nExtra streamed content.'.repeat(80)}}}:item));
 window.single=()=>setItems([user(1),answer(1)]);
 window.streaming=()=>setItems([user(1),answer(1),user(2),{id:'stream-2',type:'streaming',clientTurnId:'turn-2',text:'Working',phase:'receiving_response'}]);
 window.settle=()=>setItems([user(1),answer(1),user(2),answer(2)]);
 window.userOnly=()=>setItems([user(1),answer(1),{id:'notification',type:'user',text:'<task-notification-trigger/>'},{id:'chosen-answer',type:'question_answer',questionId:'q-choice',question:'Assistant question should not be the preview',text:'My selected answer'},answer(2)]);
 window.long=()=>setItems(Array.from({length:260},(_,i)=>[user(i),answer(i)]).flat());
 window.restoreRun=(status,sessionId='fixture-session')=>{setItems([user(5),{...answer(5),response:{...answer(5).response,sessionId:'fixture-session',timeline:[{id:'tool-start',sequence:0,kind:'tool_call',payload:{actionId:'read-5',tool:'vrcforge_read_text_file',status:'started'}}]}}]);setRuns([{sessionId,clientTurnId:'turn-5',status}]);};
 return <><output id='pinned' className='sr-only'>{String(scroll.pinned)}</output><ChatWorkspace
 input={draft} setInput={setDraft} sending={false} queueAllowed={false} onSubmit={noop} onSwitchMode={noop}
 commands={[]} actions={[]} onAction={noop} disabledReason='' attachments={[]} onAttachFiles={noop} onRemoveAttachment={noop}
 providerLabel='' model='' activeGoal={null} onGoalChanged={noop} goalEndpoint='' projects={[]} onBindProject={noop}
 conversation={items} queued={[]} agentQuestions={[]} backgroundGoalDeliveries={[]} backgroundGoalProviderWarnings={[]}
 onBackgroundGoalCatchUpRendered={noop} onBackgroundGoalProviderWarningsRendered={noop} onBackgroundGoalCatchUpDismiss={noop} onAnswerQuestion={noop}
 conversationEndRef={endRef} onConversationMouseUp={noop} onConversationScroll={scroll.onScroll} showScrollToBottom={!scroll.pinned} onScrollToBottom={scroll.scrollToBottom}
 pendingApprovalForResponse={()=>null} approvalActions={{}} latestRetryableItemId='' latestEditableUserItemId='' editingItemId='' editingText='' editingAttachments={[]}
 onEditItemChangeText={noop} onEditItemRemoveAttachment={noop} onCopyItem={noop} onRetryItem={noop} onEditItem={noop} onEditItemSave={noop} onEditItemCancel={noop}
 scopedPendingApprovals={[]} onApprove={noop} onReject={noop} onModifyApproval={noop} onOpenDoctor={noop} runtimeRuns={runs} runtimeSessionId='fixture-session' onSaveOperationAsSkill={noop}
 sessionHandoffEndpoint='' sessionHandoffSourceChatId={chat} sessionHandoffTargetChats={[]} handoffSendOpen={false} onHandoffSendOpenChange={noop}/></>;
}
void initializeI18n().then(()=>createRoot(document.getElementById('root')).render(<React.StrictMode><Fixture/></React.StrictMode>));
` }, bundle: true, write: false, format: "iife", jsx: "automatic", platform: "browser" });
// Loopback fixture belongs to this test; always closed below.
const server = createServer((_req, res) => {
  res.setHeader("Content-Type", "text/html; charset=utf-8");
  res.end(`<style>${css}html,body{margin:0;height:100%}#root{display:flex;flex-direction:column;height:100vh}</style><main id="root"></main><script>${bundled.outputFiles[0].text.replaceAll("</script", "<\\/script")}</script>`);
});
let browser;
try {
  await new Promise(r => server.listen(0, "127.0.0.1", r));
  browser = await chromium.launch({ channel: "msedge", headless: true });
  const page = await browser.newPage({ viewport: { width: 1200, height: 850 }, reducedMotion: "reduce" });
  const errors = []; page.on("pageerror", e => errors.push(e.message));
  await page.goto(`http://127.0.0.1:${server.address().port}`);
  const rail = page.locator("[data-chat-position-rail]");
  const marks = rail.locator("button[data-chat-position-marker]");
  const scroller = page.locator("[data-chat-history-scroll]");
  await rail.waitFor(); assert.equal(await marks.count(), 80, "each human turn has exactly one reachable marker");
  const lastMarkBox = await marks.last().boundingBox(); const initialRailBox = await rail.boundingBox();
  assert.ok(lastMarkBox.y + lastMarkBox.height <= initialRailBox.y + initialRailBox.height, "80 ticks fit in the rail without a second long scroll");
  const key = await marks.nth(30).getAttribute("data-chat-position-marker");
  await marks.nth(30).click();
  await page.waitForFunction(() => document.getElementById("pinned").textContent === "false");
  await page.waitForFunction(key => document.querySelector('[data-chat-position-marker][aria-current="location"]')?.getAttribute('data-chat-position-marker') === key, key);
  const top = await scroller.evaluate(e => e.scrollTop);
  await page.evaluate(() => window.grow());
  assert.ok(Math.abs(await scroller.evaluate(e => e.scrollTop) - top) < 3, "rail navigation must stop automatic follow of streamed replies");
  await scroller.evaluate(e => { e.scrollTop = 0; });
  await page.waitForFunction(() => document.querySelector('[data-chat-position-marker][aria-current="location"]') === document.querySelector('[data-chat-position-marker]'));
  await marks.nth(10).focus(); await marks.nth(10).press("Enter");
  await page.waitForFunction(() => document.querySelector('[data-chat-history-scroll]').scrollTop > 100);
  const out = process.env.VRCFORGE_RAIL_SCREENSHOT_DIR;
  if (out) { mkdirSync(out, { recursive: true }); await page.screenshot({ path: resolve(out, "rail-wide.png") }); }
  await page.setViewportSize({ width: 480, height: 750 });
  const railBox = await rail.boundingBox(); const dockBox = await page.locator('[data-chat-composer-dock]').boundingBox();
  assert.ok(railBox.y + railBox.height <= dockBox.y + 1, "rail stays above the composer");
  assert.ok(railBox.width <= 40, "navigation stays a narrow left edge rail");
  if (out) await page.screenshot({ path: resolve(out, "rail-narrow.png") });
  await page.evaluate(() => window.switchChat());
  await page.waitForFunction(() => document.querySelectorAll('[data-chat-position-marker]').length === 2);
  assert.equal(await marks.count(), 2, "switching chats removes all previous anchors");
  await page.evaluate(() => window.streaming()); await page.waitForFunction(() => document.querySelectorAll('[data-chat-position-marker]').length === 2);
  const before = await marks.evaluateAll(nodes => nodes.map(n => n.getAttribute('data-chat-position-marker')));
  await page.evaluate(() => window.settle());
  assert.deepEqual(await marks.evaluateAll(nodes => nodes.map(n => n.getAttribute('data-chat-position-marker'))), before, "settling a stream preserves turn identity");
  await page.evaluate(() => window.userOnly());
  await page.waitForFunction(() => document.querySelectorAll('[data-chat-position-marker]').length === 2);
  const labels = await marks.evaluateAll(nodes => nodes.map(n => n.getAttribute('title')));
  assert.ok(labels[0].includes('Prompt 1') && labels[1].includes('My selected answer'), 'previews contain only human input, including submitted answers');
  assert.ok(labels.every(label => !label.includes('Assistant question') && !label.includes('Answer 2') && !label.includes('notification-trigger')), 'assistant, tool and internal notification content never becomes a navigation preview');
  await page.evaluate(() => window.long());
  await page.waitForFunction(() => document.querySelectorAll('[data-chat-position-marker]').length === 260);
  await scroller.evaluate(e => { e.scrollTop = e.scrollHeight; });
  await page.waitForFunction(() => {
    const marker=document.querySelector('[data-chat-position-marker][aria-current="location"]');
    if(!marker)return false;const r=marker.getBoundingClientRect(),p=marker.parentElement.getBoundingClientRect();
    return r.top>=p.top && r.bottom<=p.bottom;
  });
  await page.evaluate(() => window.single()); await rail.waitFor({ state: "detached" });
  for (const status of ["running", "waiting_for_model", "waiting_for_tool"]) {
    await page.evaluate(status => window.restoreRun(status), status);
    const heading = page.locator('[data-agent-turn-process-group] button').first();
    await heading.waitFor();
    assert.equal(await heading.isDisabled(), true, `restored ${status} turn cannot fold`);
    assert.equal(await heading.getAttribute('aria-expanded'), 'true');
  }
  await page.evaluate(() => window.restoreRun('running', 'different-session'));
  const heading = page.locator('[data-agent-turn-process-group] button').first();
  await page.waitForFunction(() => !document.querySelector('[data-agent-turn-process-group] button')?.disabled);
  assert.equal(await heading.isDisabled(), false, 'same CID from another session cannot lock this turn');
  await page.evaluate(() => window.restoreRun('completed'));
  const beforeToggle = await heading.getAttribute('aria-expanded');
  await heading.click();
  assert.notEqual(await heading.getAttribute('aria-expanded'), beforeToggle, 'only a terminal turn permits folding');
  assert.deepEqual(errors, []);
  console.log("PASS: workspace rail, 80 turns, click and keyboard jump, reading position, streaming follow, chat switch, narrow layout; restored running summaries stay open until terminal.");
} finally { await browser?.close(); await new Promise(r => server.close(r)); }
