import assert from "node:assert/strict";
import { createRequire } from "node:module";
import { createServer } from "node:http";
import { execFileSync } from "node:child_process";
import { readFile } from "node:fs/promises";
import { build } from "esbuild";
import postcss from "postcss";
import tailwindcss from "tailwindcss";

const { chromium } = createRequire(import.meta.url)(process.env.VRCFORGE_PLAYWRIGHT_PATH || "playwright");
const baseline = process.env.VRCFORGE_TIMELINE_BASELINE_REF;
const css = (await postcss([tailwindcss("./tailwind.config.ts")]).process((await readFile("src/theme.css", "utf8")) + "\n@tailwind base; @tailwind components; @tailwind utilities;", { from: undefined })).css;
const result = await build({ stdin: { resolveDir: process.cwd(), loader: "tsx", contents: `
import React, {useState} from 'react'; import {createRoot} from 'react-dom/client';
import {ConversationCard} from './src/components/chat/conversation-card'; import {questionAnswerItem} from './src/lib/question-answer'; import {initializeI18n} from './src/i18n';
const e=(id,sequence,kind,payload)=>({id,sequence,kind,payload,timestamp:new Date(2026,0,1,0,0,sequence).toISOString()});
const timeline=[e('progress',0,'assistant',{summary:'Inspecting outfit bindings.',phase:'commentary'}),
e('a',1,'tool_call',{actionId:'a',tool:'vrcforge_list_directory',status:'started'}),e('ar',2,'tool_result',{actionId:'a',tool:'vrcforge_list_directory',status:'ok'}),
e('b',3,'tool_call',{actionId:'b',tool:'vrcforge_read_text_file',status:'started'}),e('br',4,'tool_result',{actionId:'b',tool:'vrcforge_read_text_file',status:'failed',summary:'Historical read failure'}),
e('final',5,'assistant',{summary:'The follow-up inspection completed.',phase:'final_answer',status:'done'})];
const durationTimeline=(seconds)=>[
 e('duration-start-'+seconds,0,'tool_call',{actionId:'duration-'+seconds,tool:'vrcforge_list_directory',status:'started'}),
 {...e('duration-end-'+seconds,seconds,'tool_result',{actionId:'duration-'+seconds,tool:'vrcforge_list_directory',status:'ok'}),sequence:1},
];
const item={id:'history',type:'agent',response:{clientTurnId:'history-turn',plan:{nextStep:'done',reply:'The follow-up inspection completed.'},timeline}};
const toolListItem=(count)=>({
  id:'tool-list-'+count,type:'agent',response:{clientTurnId:'tool-list-turn-'+count,plan:{nextStep:'done',reply:'Inspection finished.'},
  timeline:Array.from({length:count},(_,i)=>[
    e('list-call-'+i,i*2,'tool_call',{actionId:'list-'+i,tool:'vrcforge_read_text_file',status:'started'}),
    e('list-result-'+i,i*2+1,'tool_result',{actionId:'list-'+i,tool:'vrcforge_read_text_file',status:'ok'}),
  ]).flat(),
  steps:Array.from({length:count},(_,i)=>({kind:'tool',actionId:'list-'+i,result:{path:'file-'+i+'.txt',content:'Exact receipt '+i}})),
}});
const durationItem=(id,seconds)=>({id,type:'agent',response:{clientTurnId:id+'-turn',plan:{nextStep:'done',reply:'duration fixture'},timeline:durationTimeline(seconds)}});
const pending={id:'pending',type:'streaming',clientTurnId:'pending-turn',text:'',timeline:[e('p',0,'tool_call',{actionId:'p',tool:'vrcforge_write_file',status:'prepared'})]};
const recovered={id:'recovered',type:'streaming',clientTurnId:'recovered-turn',text:'',timeline:[e('r1',0,'tool_call',{actionId:'r1',tool:'unity_bind_execution_target',status:'started'}),e('r1r',1,'tool_result',{actionId:'r1',tool:'unity_bind_execution_target',status:'ok'}),e('r2',2,'tool_call',{actionId:'r2',tool:'unity_get_gameobject',status:'started'}),e('r2r',190,'tool_result',{actionId:'r2',tool:'unity_get_gameobject',status:'ok'})]};
const recoveredAgent={id:'recovered-agent',type:'agent',response:{sessionId:'session-recovered',clientTurnId:'recovered-agent-turn',status:'running',plan:{nextStep:'continue',reply:'Partial response'},timeline:[e('recovered-agent-call',0,'tool_call',{actionId:'recovered-agent-action',tool:'vrcforge_read_text_file',status:'started'})]}};
const interrupted={id:'interrupted',type:'agent',response:{clientTurnId:'interrupted-turn',status:'interrupted',continuationSource:'foreground_interrupted',plan:{nextStep:'interrupted',reply:'Accepted partial model text.'},timeline:[e('earlier',0,'assistant',{summary:'Earlier public update.',phase:'commentary'})]}};
const questionInterrupted={id:'question-interrupted',type:'agent',response:{turnId:'interrupted:question:receipt',continuationSource:'question_answered',plan:{planner:'runtime',nextStep:'needs_user_action',reply:'Your answer was saved, but the task continuation stopped. Native continuation has already been settled.'}}};
const questionReceipt={questionId:'visual-review',question:'Review the avatar from the front.',questionFeedbackRevision:1};
const skipped=questionAnswerItem({...questionReceipt,selectedOptionId:'skip'},'Skip');
const typed=questionAnswerItem({...questionReceipt,selectedOptionId:'custom'},'Skipped');
const legacy=questionAnswerItem(questionReceipt,'Skipped');
const normal=questionAnswerItem({...questionReceipt,selectedOptionId:'custom'},'Keep the navy outfit.',[
  {id:'avatar-review',type:'image/png',name:'review.png',dataUrl:'data:image/png;base64,iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mP8/x8AAwMCAO+j05kAAAAASUVORK5CYII='},
]);
const withPlannerStep={id:'planner-step',type:'agent',response:{plan:{nextStep:'failed',reply:'Choose a project to continue.'},steps:[{kind:'planner',status:'failed',summary:'Internal planner output'}]}};
const transitionTimeline=[e('transition-call',0,'tool_call',{actionId:'transition-action',tool:'vrcforge_read_text_file',status:'started'}),e('transition-result',1,'tool_result',{actionId:'transition-action',tool:'vrcforge_read_text_file',status:'ok'})];
function RunningToTerminal(){const [terminal,setTerminal]=useState(false); const current=terminal
  ? {id:'transition',type:'agent',response:{clientTurnId:'transition-turn',status:'completed',plan:{nextStep:'done',reply:'The live inspection finished.'},timeline:[...transitionTimeline,e('transition-final',2,'assistant',{summary:'The live inspection finished.',phase:'final_answer',status:'done'})]}}
  : {id:'transition',type:'streaming',clientTurnId:'transition-turn',text:'',timeline:transitionTimeline};
  return <><button id="transition-to-terminal" type="button" onClick={()=>setTerminal(true)}>Finish turn</button><ConversationCard item={current}/></>;}
void initializeI18n().then(()=>createRoot(document.getElementById('root')).render(<><section id="transition"><RunningToTerminal/></section><section id="active-agent"><ConversationCard item={recoveredAgent} runtimeActive={true}/></section><section id="history"><ConversationCard item={item}/></section><section id="pending"><ConversationCard item={pending}/></section><section id="interrupted"><ConversationCard item={interrupted}/></section><section id="question-interrupted"><ConversationCard item={questionInterrupted}/></section><section id="planner-step"><ConversationCard item={withPlannerStep}/></section>{[skipped,typed,legacy,normal].map((answer,index)=><section key={index} id={'review-'+index}><ConversationCard item={answer}/></section>)}<section id="recovered"><ConversationCard item={recovered}/></section><section id="duration-59m59s"><ConversationCard item={durationItem('duration-59m59s',3599)}/></section><section id="duration-1h"><ConversationCard item={durationItem('duration-1h',3600)}/></section><section id="duration-1h10m45s"><ConversationCard item={durationItem('duration-1h10m45s',4245)}/></section>{[5,6].map(count=><section key={count} id={'tool-list-'+count}><ConversationCard item={toolListItem(count)}/></section>)}</>));
` }, bundle: true, write: false, format: "iife", platform: "browser", jsx: "automatic", plugins: baseline ? [{name:'frozen-timeline-baseline',setup(plugin){plugin.onLoad({filter:/conversation-timeline\.tsx$/},()=>({loader:'tsx',contents:execFileSync('git',['show',baseline+':src/components/chat/conversation-timeline.tsx'],{encoding:'utf8'})}));}}] : [] });
const server = createServer((_, response) => { response.setHeader("Content-Type", "text/html; charset=utf-8"); response.end(`<style>${css}</style><div id="root" style="width:720px"></div><script>${result.outputFiles[0].text.replaceAll("</script", "<\\/script")}</script>`); });
await new Promise((resolve) => server.listen(0, "127.0.0.1", resolve));
let browser;
try {
  browser = await chromium.launch({headless:true,channel:process.env.VRCFORGE_BROWSER_CHANNEL || "msedge"});
  const page = await browser.newPage(); const errors=[]; page.on('pageerror',e=>errors.push(e.message));
  await page.goto(`http://127.0.0.1:${server.address().port}`);
  const recovered=page.locator('#recovered');
  const skipped = page.locator('#review-0 [data-question-skipped]');
  await skipped.waitFor({timeout: 5000});
  assert.equal(await skipped.locator('summary').innerText(), 'Skipped confirmation');
  assert.equal(await skipped.getByText('Review the avatar from the front.').isVisible(), false);
  await skipped.locator('summary').click();
  assert.equal(await skipped.getByText('Review the avatar from the front.').isVisible(), true);
  for (const [id, answer] of [['#review-1', 'Skipped'], ['#review-2', 'Skipped'], ['#review-3', 'Keep the navy outfit.']]) {
    const item = page.locator(id);
    assert.equal(await item.locator('[data-question-skipped]').count(), 0, 'only a recorded skip action receives skipped status');
    assert.equal(await item.locator('[data-user-message-bubble]').count(), 1, 'a saved answer is a user bubble');
    assert.equal(await item.locator('[data-chat-question-prompt]').innerText(), 'Review the avatar from the front.', 'a legacy question is restored to the timeline');
    assert.equal(await item.locator('[data-question-answer-value]').innerText(), answer);
    assert.equal(await item.locator('[data-question-answer-value]').isVisible(), true);
    const originalQuestion = item.locator('[data-question-answer-question]');
    assert.equal(await originalQuestion.isVisible(), true, 'saved answers always show the original question');
    const bubble = item.locator('[data-user-message-bubble]');
    const prompt = item.locator('[data-chat-question-prompt]');
    assert.ok((await bubble.boundingBox()).x > (await prompt.boundingBox()).x, 'the reply is aligned to the right');
    assert.notEqual(await originalQuestion.evaluate(el => getComputedStyle(el.querySelector('.chat-markdown')).color), await bubble.evaluate(el => getComputedStyle(el.querySelector('[data-question-answer-value] .chat-markdown')).color), 'the quoted question is muted');
    assert.equal(await originalQuestion.innerText(), 'Review the avatar from the front.');
  }

  assert.equal(await page.locator('#review-3 [data-chat-quote-content][data-question-answer-value]').count(), 1, 'saved reply remains selectable as a chat quote');
  await page.locator('#review-3').getByRole('button', {name:'Image preview'}).click();
  await page.locator('dialog[open]').getByAltText('review.png').waitFor();
  await page.keyboard.press('Escape');
  await page.locator('dialog[open]').waitFor({state:'detached'});
  await recovered.locator('[data-agent-turn-process-title]').waitFor();
  assert.equal((await page.locator('#question-interrupted').innerText()).trim(), 'Your answer was saved, but the task could not continue.');
  assert.equal((await page.locator('#planner-step').innerText()).trim(), 'Choose a project to continue.');
  assert.match(await recovered.locator('[data-agent-turn-process-title]').innerText(), /3m 10s/, 'restored timeline keeps its known elapsed duration in the process heading');
  assert.match(await page.locator('#duration-59m59s [data-agent-turn-process-title]').innerText(), /59m 59s/, 'sub-hour duration keeps minutes and seconds');
  assert.match(await page.locator('#duration-1h [data-agent-turn-process-title]').innerText(), /1h0m/, 'one-hour duration switches to hours and minutes');
  assert.match(await page.locator('#duration-1h10m45s [data-agent-turn-process-title]').innerText(), /1h10m/, 'hour-plus duration omits seconds');
  assert.doesNotMatch(await recovered.locator('[data-agent-tool-group-title]').first().innerText(), /unity_|vrcforge_/, 'folded tool headings stay readable while exact tool names remain in detail');
  const interrupted=page.locator('#interrupted'); await interrupted.locator('[data-vrcforge-agent-client-turn]').waitFor();
  assert.ok((await interrupted.innerText()).includes('Accepted partial model text.'),'transport interruption retains accepted partial words outside a false final answer');
  assert.equal(await interrupted.locator('[data-agent-transport-interrupted]').count(),1,'transport interruption has an unconfirmed-status notice');
  assert.equal(await interrupted.locator('[data-agent-turn-cancelled]').count(),0,'transport interruption does not claim backend cancellation');
  const history=page.locator('#history'); const processFold=history.locator('[data-agent-turn-process-group]');
  await processFold.waitFor();
  const summaryButton=processFold.locator('button').first();
  assert.doesNotMatch(await summaryButton.innerText(), /Called \d+ tools|Inspecting outfit bindings/, 'process header omits the tool total and progress preview');
  assert.match(await processFold.locator('[data-agent-operation-summary-status]').innerText(),/1/,'collapsed process discloses historical failures');
  assert.ok((await history.innerText()).includes('The follow-up inspection completed.'),'history failure does not hide the final result');
  assert.equal(await history.locator('[role="alert"]').count(),0,'historical failure does not declare a new current blocker');
  const colors=(button)=>button.evaluate(async element=>{
    const style=getComputedStyle(element);
    void style.color;
    await Promise.all(element.getAnimations().map(animation=>animation.finished));
    return {text:style.color,background:style.backgroundColor};
  });
  const checkTextHover=async button=>{
    await page.mouse.move(0,0);
    const resting=await colors(button);
    await button.hover();
    const hovered=await colors(button);
    assert.notEqual(hovered.text,resting.text,'hover emphasizes the row text');
    assert.equal(hovered.background,resting.background,'hover adds no background fill');
  };
  await checkTextHover(summaryButton);
  await summaryButton.click();
  assert.match(await processFold.innerText(), /Inspecting outfit bindings/, 'expanding the process reveals the original progress update');
  const group=processFold.locator('[data-agent-tool-call-group]');
  const title=await group.locator('[data-agent-tool-group-title]').innerText();
  assert.doesNotMatch(title,/Called 2 tools|调用了 2 个工具/,'group heading describes operations rather than only count');
  assert.match(await group.locator('[data-agent-operation-summary-status]').innerText(),/1/,'group preserves failure records while collapsed');
  const toolGroupButton=group.locator('button').first();
  assert.equal(await toolGroupButton.getAttribute('aria-expanded'),'false','opening completed work leaves the nested tool group folded');
  assert.equal(await group.locator('[data-agent-process-content]').count(),0,'folded tool groups mount no tool rows');
  await checkTextHover(toolGroupButton);
  await toolGroupButton.click();
  assert.equal(await group.locator('button.group\\/run').count(),2,'clicking the tool group reveals individual calls');
  await checkTextHover(group.locator('button.group\\/run').first());
  await group.locator('button.group\\/run').last().click();
  assert.ok((await group.locator('[data-agent-invocation-output-card]').innerText()).includes('Historical read failure'),'exact error detail stays inspectable');
  await processFold.locator('button').first().click();
  assert.equal(await processFold.locator('button').first().getAttribute('aria-expanded'),'false','terminal turn work summary remains user-collapsible');
  await summaryButton.click();
  assert.equal(await toolGroupButton.getAttribute('aria-expanded'),'false','reopening the turn leaves tool details folded again');
  const pending=page.locator('#pending');
  assert.match(await pending.locator('[data-agent-turn-process-title]').innerText(), /Handled in —/, 'missing duration stays explicitly unknown instead of changing the process heading');
  const pendingProcess=pending.locator('[data-agent-turn-process-group]');
  assert.equal(await pendingProcess.locator('button').first().getAttribute('aria-expanded'),'true','running turn work summary starts expanded');
  assert.equal(await pendingProcess.locator('button').first().isDisabled(),true,'running turn summary exposes its non-collapsible state accessibly');
  const pendingGroup=pending.locator('[data-agent-tool-call-group]'); await pendingGroup.locator('button').first().click();
  await pendingGroup.locator('button.group\\/run').click();
  assert.doesNotMatch(await pendingGroup.locator('[data-agent-invocation-output-card]').innerText(),/Done|已完成/,'prepared request is not a completed write');
  const transition=page.locator('#transition');
  const liveSummary=transition.locator('[data-agent-turn-process-group] button').first();
  assert.equal(await liveSummary.getAttribute('aria-expanded'),'true','live clientTurn starts expanded');
  assert.equal(await liveSummary.isDisabled(),true,'live clientTurn exposes its non-collapsible state accessibly');
  await transition.locator('#transition-to-terminal').click();
  const terminalSummary=transition.locator('[data-agent-turn-process-group] button').first();
  await terminalSummary.waitFor();
  assert.equal(await terminalSummary.getAttribute('aria-expanded'),'false','the same clientTurn becomes collapsible only after its streaming item reaches a terminal agent response');
  await terminalSummary.click();
  assert.equal(await terminalSummary.getAttribute('aria-expanded'),'true','terminal clientTurn can be opened for review');
  await terminalSummary.click();
  assert.equal(await terminalSummary.getAttribute('aria-expanded'),'false','terminal clientTurn can be folded after review');
  const activeAgent=page.locator('#active-agent');
  const recoveredSummary=activeAgent.locator('[data-agent-turn-process-group] button').first();
  assert.equal(await recoveredSummary.getAttribute('aria-expanded'),'true','a recovered agent item with active CID state stays expanded');
  assert.equal(await recoveredSummary.isDisabled(),true,'active recovered summary exposes its non-collapsible state accessibly');
  for (const count of [5,6]) {
    const tools=page.locator('#tool-list-'+count);
    await tools.locator('[data-agent-turn-process-group] button').first().click();
    await tools.locator('[data-agent-tool-call-group] button').first().click();
    const list=tools.locator('[data-agent-tool-call-group] [data-agent-process-content]');
    const height=await list.evaluate(element=>element.clientHeight);
    const rowGap=await list.evaluate(element=>{
      const [first,next]=element.children;
      return next.getBoundingClientRect().top-first.getBoundingClientRect().bottom;
    });
    assert.ok(rowGap<=4,`expanded tool rows should have at most 4px between them, got ${rowGap}px`);
    assert.equal(await list.evaluate(element=>element.scrollHeight>element.clientHeight),count>5,'only groups over five calls scroll');
    assert.equal(await list.locator('button').count(),count,'all calls remain available');
    if (count>5) {
      await list.focus();
      await page.keyboard.press('End');
      await page.waitForFunction(element=>element.scrollTop>0,await list.elementHandle());
      await list.locator('button').last().click();
      assert.match(await list.locator('[data-agent-invocation-output-card]').innerText(),/Exact receipt 5/,'the last scrolled row opens its own full receipt');
      assert.equal(await list.evaluate(element=>element.clientHeight),height,'opening detail leaves the scroll area height fixed');
    }
  }
  assert.deepEqual(errors,[]); console.log('work summary, historical failure, original detail and prepared stage browser passed');
} finally { await browser?.close(); await new Promise(resolve=>server.close(resolve)); }
