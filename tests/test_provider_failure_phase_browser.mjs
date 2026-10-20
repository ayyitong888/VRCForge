import assert from 'node:assert/strict';
import {readFile, writeFile} from 'node:fs/promises';
import {createServer} from 'node:http';
import {createRequire} from 'node:module';
import {build} from 'esbuild';
import {pathToFileURL} from 'node:url';
import {dirname, relative, resolve} from 'node:path';
import {projectRuntimeResponseForDisplay} from '../src/lib/chat-timeline-presentation.ts';

const messages=JSON.parse(await readFile('src/locales/zh-CN.json','utf8'));
const translate=key=>key.split('.').reduce((value,part)=>value?.[part],messages)||key;
const fixture=process.env.VRCFORGE_TERMINAL_REPLAY
 ? JSON.parse(await readFile(process.env.VRCFORGE_TERMINAL_REPLAY,'utf8'))
 : {continuationEvent:{turnId:'failure-turn',sessionId:'session-fixture',plan:{reply:messages.chat.runtimeTerminal.providerPostToolFailureMessage,nextStep:'planner_failed',plannerFailure:{code:'provider_connection_failed',phase:'post_tool',providerError:{type:'RuntimeError'}}},timeline:[{id:'final',sequence:1,kind:'assistant',payload:{phase:'final_answer',status:'planner_failed',summary:messages.chat.runtimeTerminal.providerPostToolFailureMessage}}]},steps:[{tool:'vrcforge_scene_save',status:'executed',result:{success:true}}]};
const response={...fixture.continuationEvent,steps:fixture.steps,ok:true,status:'failed'};
const original=JSON.stringify(response);
const projected=projectRuntimeResponseForDisplay(response,translate);
assert.equal(response.plan.plannerFailure.phase,'post_tool');
assert.ok(response.steps.some(step=>JSON.stringify(step).includes('vrcforge_scene_save')&&step.status==='executed'));
assert.equal(projected.plan.reply,response.plan.reply,'the real post-tool reply remains word-for-word truthful');
assert.equal(JSON.stringify(response),original,'display never mutates raw runtime facts');
assert.deepEqual(projected.steps,response.steps);
if(process.env.VRCFORGE_TERMINAL_BEFORE_MODULE){
 const before=await import(pathToFileURL(process.env.VRCFORGE_TERMINAL_BEFORE_MODULE));
 const beforeLocale=JSON.parse(await readFile(process.env.VRCFORGE_TERMINAL_BEFORE_LOCALE,'utf8'));
 const red=before.projectRuntimeResponseForDisplay(response,key=>key.split('.').reduce((v,p)=>v?.[p],beforeLocale)||key);
 assert.match(red.plan.reply,/未执行工具/,'the identical original record must reproduce the misleading pre-fix assertion');
}
const polluted=structuredClone(response);
polluted.plan.reply='Provider 在响应开始前连接失败。本轮未执行工具，请在连接恢复后重试。';
for(const event of polluted.timeline||[])if(event.kind==='assistant'&&event.payload?.phase==='final_answer')event.payload.summary=polluted.plan.reply;
const pollutedBefore=JSON.stringify(polluted);
assert.equal(projectRuntimeResponseForDisplay(polluted,translate).plan.reply,response.plan.reply,'typed phase repairs only the display of old polluted copies');
assert.equal(JSON.stringify(polluted),pollutedBefore,'old history is never migrated');
for(const phase of [undefined,'initial']){
 const unknown=structuredClone(response);unknown.plan.plannerFailure.phase=phase;
 const text=projectRuntimeResponseForDisplay(unknown,translate).plan.reply;
 assert.doesNotMatch(text,/未执行工具|响应开始前|上一步工具已经执行/,'unknown/initial phase must not invent execution facts');
}
const success=structuredClone(response);delete success.plan.plannerFailure;success.plan.nextStep='done';success.plan.reply='正常完成';
assert.equal(projectRuntimeResponseForDisplay(success,translate),success);

const {chromium}=createRequire(import.meta.url)(process.env.VRCFORGE_PLAYWRIGHT_PATH||'playwright');
const stub=`
export const cancelAgentRunFollowup=async()=>({ok:true});
export const listAgentRunFollowups=async()=>({ok:true,items:[]});
export const fetchAgentQuestions=async()=>({ok:true,questions:[]});
export const issueComputerUseTurnGrant=async()=>null;
export const requestAgentRunCancel=async()=>({ok:true});
export const requestChatTitle=async()=>({title:'Fixture'});
export const recordAgentRunQueued=async()=>({ok:true});
export const sendAgentMessage=async()=>window.__rawResponse;
`;
const bundle=await build({stdin:{resolveDir:process.cwd(),loader:'tsx',contents:`
import React from 'react';import {createRoot} from 'react-dom/client';
import {useChatRunController} from './src/hooks/use-chat-run-controller';
import {ConversationCard} from './src/components/chat/conversation-card';
import {initializeI18n,setLocale} from './src/i18n';
window.__rawResponse=${JSON.stringify(response)};
const chat={id:'chat-fixture',sessionId:'session-fixture',title:'Existing chat',items:[]};
function Harness(){const controller=useChatRunController({endpoint:'http://127.0.0.1:8757',runtimeConnected:true,sessionId:'session-fixture',activeRuntimeProjectPath:'',getChatById:id=>id===chat.id?chat:undefined,ensureActiveChat:()=>chat.id,updateChat:(id,update)=>Object.assign(chat,update(chat)),appendToChat:(id,item)=>{chat.items=[...chat.items,item]},touchChat:value=>value,startRuntime:async()=>'',refresh:async()=>{},refreshRuntimeRuns:async()=>{},refreshBackgroundGoals:()=>{},handleRuntimeSessionFailure:()=>{},setError:()=>{},persistChatsNow:async()=>{},chats:[chat]});window.__run=()=>controller.submitTurn({id:'active-turn-fixture',text:'read-only fixture',attachments:[],provider:'fixture',providerLabel:'Fixture',model:'fixture',sessionId:'session-fixture',planMode:false});window.__chat=chat;return <div id="hook-mounted"/>;}
void initializeI18n().then(()=>setLocale('zh-CN')).then(()=>{createRoot(document.getElementById('hook')).render(<Harness/>);createRoot(document.getElementById('card')).render(<ConversationCard item={{id:'old-failure',type:'agent',response:${JSON.stringify(polluted)}}}/>);});
`},bundle:true,write:false,format:'iife',jsx:'automatic',platform:'browser',plugins:[{name:'failure-hook-stub',setup(api){
 if(process.env.VRCFORGE_TERMINAL_BROWSER_BEFORE)api.onLoad({filter:/(?:use-chat-run-controller|chat-timeline-presentation)\.ts$/},async args=>({contents:await readFile(resolve(process.env.VRCFORGE_TERMINAL_BROWSER_BEFORE,relative(process.cwd(),args.path)),'utf8'),resolveDir:dirname(args.path),loader:'ts'}));
 api.onResolve({filter:/^\.\.\/lib\/api$/},args=>args.importer.endsWith('use-chat-run-controller.ts')?{path:'api',namespace:'stub'}:undefined);api.onResolve({filter:/^\.\.\/lib\/api\/agent-runtime$/},args=>args.importer.endsWith('use-chat-run-controller.ts')?{path:'runtime',namespace:'stub'}:undefined);api.onLoad({filter:/.*/,namespace:'stub'},args=>({contents:args.path==='runtime'?'export const DEFAULT_BACKGROUND_MAX_AGENTIC_TURNS=12;':stub,loader:'js'}));}}]});
const server=createServer((_req,res)=>{res.setHeader('Content-Type','text/html');res.end(`<div id="hook"></div><div id="card"></div><script>${bundle.outputFiles[0].text.replaceAll('</script','<\\/script')}</script>`)});
let browser;
try{
 await new Promise(resolve=>server.listen(0,'127.0.0.1',resolve));browser=await chromium.launch({headless:true,channel:'msedge'});
 const page=await browser.newPage();const errors=[];page.on('pageerror',e=>errors.push(e.message));await page.goto(`http://127.0.0.1:${server.address().port}`);
 await page.locator('#hook-mounted').waitFor({state:'attached'});await page.evaluate(()=>window.__run());
 const stored=await page.evaluate(()=>window.__chat.items.find(item=>item.type==='agent')?.response);
 assert.equal(stored.plan.reply,response.plan.reply,'real controller persists raw reply rather than the localized display');
 assert.deepEqual(stored.plan.plannerFailure,response.plan.plannerFailure);
 const storedFinal=stored.timeline.filter(event=>event.kind==='assistant').at(-1);
 const rawFinal=response.timeline.filter(event=>event.kind==='assistant').at(-1);
 assert.equal(storedFinal.payload.summary,rawFinal.payload.summary,'real controller retains raw final timeline prose');
 const body=await page.locator('#card').innerText();
 assert.ok(body.includes(response.plan.reply),'real ConversationCard corrects old polluted display from typed post_tool phase');
 assert.doesNotMatch(body,/本轮未执行工具|响应开始前|连接失败 · 未完成/);
 assert.ok(body.includes(messages.chat.runtimeTerminal.providerConnectionFailedStatus));
 assert.deepEqual(errors,[]);
 if(process.env.VRCFORGE_TERMINAL_EVIDENCE_DIR){const dir=process.env.VRCFORGE_TERMINAL_EVIDENCE_DIR;await page.locator('#card').screenshot({path:dir+'/original-turn-card.png'});await writeFile(dir+'/replay-validation.json',JSON.stringify({originalTurnId:response.turnId,phase:response.plan.plannerFailure.phase,sceneSaveExecuted:true,rawReplyPersisted:true,rawTimelinePersisted:true,postToolReplyWordEqual:true,legacyPollutionCorrectedOnlyInDisplay:true,unknownAndInitialNeutral:true,successIdentityUnchanged:true,realCardNoFalseZeroToolClaim:true,pageErrors:errors},null,2));}
 console.log('provider failure original-turn replay: raw persistence, phase-aware actual Card, legacy/unknown/initial/success passed');
}finally{await browser?.close();await new Promise(resolve=>server.close(resolve));}
