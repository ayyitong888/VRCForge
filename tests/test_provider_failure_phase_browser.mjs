import assert from 'node:assert/strict';
import {readFile, writeFile} from 'node:fs/promises';
import {createServer} from 'node:http';
import {createRequire} from 'node:module';
import {build} from 'esbuild';
import {pathToFileURL} from 'node:url';
import {projectRuntimeResponseForDisplay} from '../src/lib/chat-timeline-presentation.ts';

const messages=JSON.parse(await readFile('src/locales/zh-CN.json','utf8'));
const translate=key=>key.split('.').reduce((value,part)=>value?.[part],messages)||key;
const fixture=process.env.VRCFORGE_TERMINAL_REPLAY
 ? JSON.parse(await readFile(process.env.VRCFORGE_TERMINAL_REPLAY,'utf8'))
 : {continuationEvent:{turnId:'failure-turn',sessionId:'session-fixture',plan:{reply:"Raw backend failure text.",nextStep:'planner_failed',plannerFailure:{code:'provider_connection_failed',phase:'post_tool',providerError:{type:'RuntimeError'}}},timeline:[{id:'final',sequence:1,kind:'assistant',payload:{phase:'final_answer',status:'planner_failed',summary:"Raw backend failure text."}}]},steps:[{tool:'vrcforge_scene_save',status:'executed',result:{success:true}}]};
const originalTimeout = fixture.chat?.items.find(item => item.response?.plan.plannerFailure?.code === 'provider_timeout')?.response;
const response={...(originalTimeout || fixture.continuationEvent),steps:fixture.steps || originalTimeout?.steps,ok:true,status:'failed'};
const original=JSON.stringify(response);
const projected=projectRuntimeResponseForDisplay(response,translate);
const terminal = originalTimeout ? messages.chat.runtimeTerminal.providerTimeoutStatus : messages.chat.runtimeTerminal.providerConnectionFailedStatus;
const nextStep = originalTimeout ? messages.chat.providerResponseTimedOut : messages.chat.runtimeTerminal.providerConnectionFailedMessage;
const expectedText = messages.chat.runtimeTerminal.previousResultsKept + ' ' + nextStep;
assert.equal(response.plan.plannerFailure.phase,'post_tool');
assert.ok(response.steps?.some(step=>JSON.stringify(step).includes('vrcforge_scene_save')&&step.status==='executed')
 || response.timeline?.some(event=>event.kind==='tool_result'&&event.payload.status==='ok'));
assert.equal(projected.plan.reply, expectedText, 'show retained results and the next action without a raw provider error');
assert.equal(JSON.stringify(response),original,'display never mutates raw runtime facts');
assert.deepEqual(projected.steps,response.steps);
if(process.env.VRCFORGE_TERMINAL_BEFORE_MODULE){
 const before=await import(pathToFileURL(process.env.VRCFORGE_TERMINAL_BEFORE_MODULE));
 const beforeLocale=JSON.parse(await readFile(process.env.VRCFORGE_TERMINAL_BEFORE_LOCALE,'utf8'));
 const red=before.projectRuntimeResponseForDisplay(response,key=>key.split('.').reduce((v,p)=>v?.[p],beforeLocale)||key);
 assert.doesNotMatch(red.plan.reply,/上一步的结果已保留/,'the identical original record must reproduce the misleading pre-fix assertion');
}
const polluted=structuredClone(response);
polluted.plan.reply='Provider 在响应开始前连接失败。本轮未执行工具，请在连接恢复后重试。';
for(const event of polluted.timeline||[])if(event.kind==='assistant'&&event.payload?.phase==='final_answer')event.payload.summary=polluted.plan.reply;
const pollutedBefore=JSON.stringify(polluted);
assert.equal(projectRuntimeResponseForDisplay(polluted,translate).plan.reply,expectedText,'typed evidence repairs only the display of old polluted copies');
assert.equal(JSON.stringify(polluted),pollutedBefore,'old history is never migrated');
for(const phase of [undefined,'initial']){
 const unknown=structuredClone(response);unknown.plan.plannerFailure.phase=phase;
 const text=projectRuntimeResponseForDisplay(unknown,translate).plan.reply;
 assert.doesNotMatch(text,/未执行工具|响应开始前|上一步工具已经执行/,'unknown/initial phase must not invent execution facts');
}
const success=structuredClone(response);delete success.plan.plannerFailure;success.plan.nextStep='done';success.plan.reply='正常完成';
assert.equal(projectRuntimeResponseForDisplay(success,translate),success);

const {chromium}=createRequire(import.meta.url)(process.env.VRCFORGE_PLAYWRIGHT_PATH||'playwright');
const withoutFinal = structuredClone(polluted);
withoutFinal.timeline = [
  ...withoutFinal.timeline.filter(event => event.kind !== 'assistant'),
  { id: 'partial-commentary', kind: 'assistant', timestamp: '2026-01-01T00:00:00Z', sequence: 10_000,
    payload: { phase: 'commentary', summary: 'Progress already reported.' } },
];
const toolError = "HTTP 500 raw tool failure sentinel";
const invalidAction = structuredClone(response);
invalidAction.plan.plannerFailure = { code: 'planner_invalid_response', phase: 'post_tool' };
invalidAction.plan.unresolvedArgumentValidation = { actionId: 'rejected-inspection' };
const legacyTool = {
  ...response, plan: { nextStep: "done" }, timeline: [], steps: [],
  shell: { status: "failed", error: toolError },
};
const nativeTool = {
  ...response, plan: { nextStep: "done" }, steps: [],
  timeline: [{ id: "tool-error", sequence: 0, kind: "tool_result",
    payload: { tool: "vrcforge_read_text_file", status: "failed", summary: toolError } }],
};
const bundle=await build({stdin:{resolveDir:process.cwd(),loader:'tsx',contents:`
import React from 'react';import {createRoot} from 'react-dom/client';
import {ConversationCard} from './src/components/chat/conversation-card';
import {initializeI18n,setLocale} from './src/i18n';
void initializeI18n().then(()=>setLocale('zh-CN')).then(()=>{
  createRoot(document.getElementById('card')).render(<ConversationCard item={{id:'old-failure',type:'agent',response:${JSON.stringify(polluted)}}}/>);
  createRoot(document.getElementById('partial')).render(<ConversationCard item={{id:'partial',type:'agent',response:${JSON.stringify(withoutFinal)}}}/>);
  createRoot(document.getElementById('legacy')).render(<ConversationCard item={{id:'legacy',type:'error',text:'Runtime stack trace sentinel'}}/>);
  for (const [id, response, developerOptionsEnabled] of [
    ['legacy-tool', ${JSON.stringify(legacyTool)}, false],
    ['invalid-action', ${JSON.stringify(invalidAction)}, false],
    ['native-tool', ${JSON.stringify(nativeTool)}, true],
  ]) {
    createRoot(document.getElementById(id)).render(<ConversationCard item={{id,type:'agent',response}} developerOptionsEnabled={developerOptionsEnabled}/>);
  }
});
`},bundle:true,write:false,format:'iife',jsx:'automatic',platform:'browser'});
const server=createServer((_req,res)=>{res.setHeader('Content-Type','text/html');res.end(`<div id="card"></div><div id="partial"></div><div id="legacy"></div><div id="legacy-tool"></div><div id="native-tool"></div><div id="invalid-action"></div><script>${bundle.outputFiles[0].text.replaceAll('</script','<\\/script')}</script>`)});
let browser;
try{
 await new Promise(resolve=>server.listen(0,'127.0.0.1',resolve));browser=await chromium.launch({headless:true,channel:'msedge'});
 const page=await browser.newPage();const errors=[];page.on('pageerror',e=>errors.push(e.message));await page.goto(`http://127.0.0.1:${server.address().port}`);
 await page.locator('#card [data-vrcforge-terminal-status]').waitFor();
 const invalidActionText = await page.locator('#invalid-action').innerText();
 assert.ok(invalidActionText.includes(messages.chat.runtimeTerminal.previousResultsKept));
 assert.match(invalidActionText, /助手没能修正下一步操作/);
 await page.locator('#partial [data-vrcforge-terminal-status]').waitFor();
 assert.equal(await page.locator('#partial [data-vrcforge-terminal-status]').count(),1,'commentary-only turns still get one error card');
 assert.ok((await page.locator('#legacy').innerText()).includes(messages.chat.runError));
 assert.doesNotMatch(await page.locator('#legacy').innerText(),/stack trace sentinel/);
 await page.locator('#legacy-tool button[aria-expanded]').first().click();
 await page.locator('#legacy-tool').getByText(messages.chat.toolFailure.shell).waitFor();
 assert.doesNotMatch(await page.locator('#legacy-tool').textContent(), /raw tool failure sentinel/);
 await page.locator('#native-tool [data-agent-turn-process-group] button').click();
 await page.locator('#native-tool [data-agent-tool-call-group] button').first().click();
 await page.locator('#native-tool [data-agent-tool-call-group] button[aria-expanded="false"]').click();
 await page.locator('#native-tool').getByText(messages.chat.toolFailure.skill).waitFor();
 const diagnostics = page.locator('#native-tool [data-chat-tool-diagnostics]');
 assert.equal(await diagnostics.getByText(toolError, { exact: true }).isVisible(), false);
 await diagnostics.locator('summary').click();
 await diagnostics.getByText(toolError, { exact: true }).waitFor();
 const body=await page.locator('#card').innerText();
 assert.ok(body.includes(expectedText),'real ConversationCard corrects old polluted display from typed post_tool phase');
 assert.doesNotMatch(body,/本轮未执行工具|响应开始前|连接失败 · 未完成/);
 assert.ok(body.includes(terminal));
 assert.deepEqual(errors,[]);
 if(process.env.VRCFORGE_TERMINAL_EVIDENCE_DIR){const dir=process.env.VRCFORGE_TERMINAL_EVIDENCE_DIR;await page.locator('#card').screenshot({path:dir+'/original-turn-card.png'});await writeFile(dir+'/replay-validation.json',JSON.stringify({originalTurnId:response.turnId,phase:response.plan.plannerFailure.phase,confirmedResultRetained:true,postToolReplyLocalized:true,legacyPollutionCorrectedOnlyInDisplay:true,unknownAndInitialNeutral:true,successIdentityUnchanged:true,realCardNoFalseZeroToolClaim:true,pageErrors:errors},null,2));}
 console.log('provider failure original-turn replay: phase-aware Card, missing final, legacy, and unknown/initial/success passed');
}finally{await browser?.close();await new Promise(resolve=>server.close(resolve));}
