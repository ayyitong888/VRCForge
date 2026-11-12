import assert from 'node:assert/strict';
import {build} from 'esbuild';
import {createServer} from 'node:http';
import {createRequire} from 'node:module';
const user=(id)=>({id:'user-'+id,type:'user',clientTurnId:id,text:id});
const owner={id:'opaque-owner',sessionId:'runtime-owner',title:'Existing owner',projectPath:'',items:[user('parent')]};
const other={id:'active-other',sessionId:'runtime-other',title:'Other visible chat',projectPath:'',items:[]};
const entry={queueId:'queued-answer',sequence:1,sessionId:'runtime-owner',runtimeSessionId:'runtime-owner',targetClientTurnId:'parent',clientTurnId:'opaque-input',message:'Original full task prompt',status:'claimed'};
const scenarios=[
 {name:'runtime-owner',chats:[owner,other],entries:[entry],expectedOwner:'opaque-owner'},
 {name:'unique-runtime',chats:[owner,other],entries:[{...entry,targetClientTurnId:undefined}],expectedOwner:'opaque-owner'},
 {name:'exact-target',chats:[{...owner,id:'copy-a',items:[user('different-parent')]},{...owner,id:'copy-b'},other],entries:[entry],expectedOwner:'copy-b'},
 {name:'ambiguous',chats:[{...owner,id:'copy-a'},{...owner,id:'copy-b'},other],entries:[entry],ambiguous:true},
 {name:'unowned-legacy-answer',chats:[owner,other],entries:[{...entry,sessionId:'missing-lane',runtimeSessionId:undefined,targetClientTurnId:undefined,clientTurnId:'question-answer:missing-qid'}],saveOnly:true},
 {name:'ordinary-lane',chats:[owner,other],entries:[{...entry,sessionId:'opaque-owner',targetClientTurnId:undefined}],expectedOwner:'opaque-owner'},
 {name:'old-branch-pending',chats:[{...owner,sessionId:'new-runtime'},other],entries:[{...entry,sessionId:'opaque-owner',status:'pending'}],staleBranch:true,unchangedSession:'new-runtime'},
 {name:'compacted-branch-pending',chats:[{...owner,sessionId:''},other],entries:[{...entry,sessionId:'opaque-owner',status:'pending'}],staleBranch:true,unchangedSession:''},
 {name:'answer-dispatch',chats:[{...owner,items:[...owner.items,{id:'saved-answer',type:'question_answer',questionId:'qid',text:'Actual answer'}]},other],entries:[{...entry,sessionId:'opaque-owner',clientTurnId:'opaque-question-input',questionId:'qid',continuationSource:'question_answered',status:'pending'}],saveOnly:true},
 {name:'nonblocking-independent-question',chats:[{...owner,items:[...owner.items,{id:'saved-answer',type:'question_answer',questionId:'qid',text:'Actual answer'}]},other],entries:[{...entry,sessionId:'opaque-owner',clientTurnId:'opaque-question-input',questionId:'qid',continuationSource:'question_answered',status:'pending'}],questions:[{questionId:'independent-q',sessionId:'runtime-owner',status:'pending',blocking:false,needsUserAction:false}],saveOnly:true},
 {name:'answer-before-chat-save',chats:[owner,other],entries:[{...entry,sessionId:'opaque-owner',clientTurnId:'opaque-question-input',questionId:'qid',continuationSource:'question_answered',status:'pending'}],saveOnly:true,noSavedAnswer:true},
 {name:'legacy-answer',chats:[{...owner,items:[...owner.items,{id:'saved-answer',type:'question_answer',questionId:'qid',text:'Actual answer'}]},other],entries:[{...entry,sessionId:'runtime-owner',clientTurnId:'question-answer:qid',status:'pending'}],saveOnly:true},
];
scenarios.push({
 name:'withdraw-stale-lane',
 chats:[{...owner,sessionId:'new-runtime',items:[
  ...owner.items,
  {...user('opaque-input'),queuedFrom:true,composerQueued:true,queueStatus:'queued',
   queueEnvelope:{queueId:'queued-answer',sequence:1,laneId:'opaque-owner',sessionId:'runtime-owner'}},
 ]},other],
 entries:[
  {...entry,sessionId:'opaque-owner',status:'pending'},
  {...entry,queueId:'queued-new',sequence:2,sessionId:'opaque-owner',runtimeSessionId:'new-runtime',clientTurnId:'new-input',status:'pending'},
  {...entry,queueId:'queued-independent',sequence:3,sessionId:'active-other',runtimeSessionId:'runtime-other',clientTurnId:'independent-input',status:'pending'},
 ],
 staleBranch:true, unchangedSession:'new-runtime', independentLane:true,
});
const api=`export const listAgentRunFollowups=async()=>({ok:true,items:window.fixture.entries});export const fetchAgentQuestions=async()=>({ok:true,questions:window.fixture.questions||[]});export const cancelAgentRunFollowup=async(_endpoint,queueId,{sessionId})=>{window.cancels.push({queueId,sessionId});window.fixture.entries=window.fixture.entries.filter(row=>row.queueId!==queueId||row.sessionId!==sessionId);return {ok:true,status:'cancelled'};};export const issueComputerUseTurnGrant=async()=>null;export const requestAgentRunCancel=async()=>({ok:true});export const requestChatTitle=async()=>({title:'Existing owner'});export const recordAgentRunQueued=async()=>({ok:true,accepted:true,mode:'followup'});export const sendAgentMessage=(_e,_m,sessionId,_h,_a,options)=>{window.sends.push({sessionId,clientTurnId:options.clientTurnId,queueId:options.followupQueueId});return new Promise(resolve=>window.resolveSend=()=>{window.fixture.entries=window.fixture.entries.filter(row=>row.queueId!==options.followupQueueId);resolve({ok:true,sessionId,clientTurnId:options.clientTurnId,turnId:'backend-'+options.clientTurnId,startedAt:new Date().toISOString(),plan:{nextStep:'done'}});});};`;
const bundle=await build({stdin:{resolveDir:process.cwd(),loader:'tsx',contents:`
import React,{useRef,useState,useCallback}from'react';import{createRoot}from'react-dom/client';import{useChatRunController}from'./src/hooks/use-chat-run-controller';
const cases=${JSON.stringify(scenarios)};window.fixture=structuredClone(cases.find(c=>c.name===new URL(location.href).searchParams.get('case')));window.ensureCalls=0;window.sends=[];window.cancels=[];window.errors=[];
function Harness(){const[chats,setChats]=useState(window.fixture.chats);const latest=useRef(chats);latest.current=chats;const updateChat=useCallback((id,fn)=>{latest.current=latest.current.map(c=>c.id===id?fn(c):c);setChats(latest.current);window.chats=latest.current;},[]);const c=useChatRunController({endpoint:'http://isolated-fixture',runtimeConnected:true,sessionId:'runtime-other',activeRuntimeProjectPath:'',getChatById:id=>latest.current.find(c=>c.id===id),ensureActiveChat:()=>{window.ensureCalls++;return'active-other';},updateChat,appendToChat:(id,item)=>updateChat(id,c=>({...c,items:[...c.items,item]})),touchChat:c=>c,startRuntime:async()=>'',refresh:async()=>{},refreshRuntimeRuns:async()=>{},refreshBackgroundGoals:()=>{},handleRuntimeSessionFailure:()=>{},setError:e=>window.errors.push(e),persistChatsNow:async()=>{},chats,chatStorageReady:true});window.controller=c;window.chats=chats;return <div id='mounted'/>;}createRoot(document.getElementById('root')).render(<Harness/>);
`},bundle:true,write:false,format:'iife',platform:'browser',jsx:'automatic',plugins:[{name:'restore-api-stub',setup(b){b.onResolve({filter:/^react-i18next$/},()=>({path:'i18n',namespace:'fixture'}));b.onResolve({filter:/^\.\.\/lib\/api$/},a=>a.importer.endsWith('use-chat-run-controller.ts')?{path:'api',namespace:'fixture'}:undefined);b.onResolve({filter:/^\.\.\/lib\/api\/agent-runtime$/},a=>a.importer.endsWith('use-chat-run-controller.ts')?{path:'constants',namespace:'fixture'}:undefined);b.onLoad({filter:/.*/,namespace:'fixture'},a=>({loader:'js',contents:a.path==='i18n'?"export const useTranslation=()=>({t:k=>k});":a.path==='constants'?'export const DEFAULT_BACKGROUND_MAX_AGENTIC_TURNS=12;':api}));}}]});
const server=createServer((_q,r)=>{r.setHeader('Content-Type','text/html');r.end(`<div id='root'></div><script>${bundle.outputFiles[0].text.replaceAll('</script','<\\/script')}</script>`);});const{chromium}=createRequire(import.meta.url)(process.env.VRCFORGE_PLAYWRIGHT_PATH||'playwright');let browser;
try{await new Promise(r=>server.listen(0,'127.0.0.1',r));browser=await chromium.launch({headless:true,channel:'msedge'});const page=await browser.newPage();const errors=[];page.on('pageerror',e=>errors.push(e.message));for(const s of scenarios.filter(s=>!process.env.VRCFORGE_RESTORE_CASE||s.name===process.env.VRCFORGE_RESTORE_CASE)){await page.goto(`http://127.0.0.1:${server.address().port}/?case=${s.name}`);await page.locator('#mounted').waitFor({state:'attached'});await page.waitForTimeout(80);assert.equal(await page.evaluate(()=>window.ensureCalls),0,s.name+': restoration must resolve an existing owner without selecting or creating the active chat');if(s.staleBranch){
 const stale=await page.evaluate(()=>window.controller.queued.find(row=>row.id==='opaque-input'));
 assert.equal(stale.queueStatus,'paused');
 assert.equal(stale.staleBranch,true);
 assert.equal(stale.sessionId,'runtime-owner');
 assert.equal(await page.evaluate(()=>window.chats.find(c=>c.id==='opaque-owner').sessionId),s.unchangedSession,'stale queue cannot rebind the current chat');
 if(s.independentLane){
  await page.waitForFunction(()=>window.sends.length===1);
  assert.deepEqual(await page.evaluate(()=>window.sends),[{sessionId:'runtime-other',clientTurnId:'independent-input',queueId:'queued-independent'}]);
 }else{
  await page.evaluate(()=>window.controller.resumeQueuedTurns(true));
  assert.equal(await page.evaluate(()=>window.sends.length),0,'manual resume cannot send the stale item');
 }
 assert.equal(await page.evaluate(()=>window.controller.steerQueuedTurn('opaque-input','opaque-owner')),'failed');
 assert.equal(await page.evaluate(()=>window.cancels.length),0,'steer cannot implicitly withdraw the old branch');
 assert.equal(await page.evaluate(()=>window.controller.removeQueuedTurn('opaque-input','opaque-owner')),true);
 assert.deepEqual(await page.evaluate(()=>window.cancels),[{queueId:'queued-answer',sessionId:'opaque-owner'}],'withdrawal is scoped to the old durable lane item');
 if(s.independentLane){
  assert.equal(await page.evaluate(()=>window.chats.find(c=>c.id==='opaque-owner').items.find(i=>i.id==='user-opaque-input').queueStatus),'cancelled','the withdrawn local envelope cannot be restored after restart');
  await page.evaluate(()=>window.resolveSend());
  await page.waitForFunction(()=>window.sends.length===2);
  assert.deepEqual(await page.evaluate(()=>window.sends[1]),{sessionId:'new-runtime',clientTurnId:'new-input',queueId:'queued-new'},'only explicit withdrawal releases the new turn on the same lane');
 }
 console.log('PASS '+s.name);
 continue;
}
if(s.ambiguous||s.saveOnly){assert.equal(await page.evaluate(()=>window.controller.queued.length),0,s.name+': no runnable follow-up');assert.equal(await page.evaluate(()=>window.sends.length),0,s.name+': no provider dispatch');if(s.saveOnly){assert.equal(await page.evaluate(()=>window.chats.flatMap(c=>c.items).filter(i=>i.type==='question_answer').length),s.noSavedAnswer||s.name==='unowned-legacy-answer'?0:1,'an existing answer stays in the transcript');assert.equal(await page.evaluate(()=>window.chats.flatMap(c=>c.items).filter(i=>i.type==='user').length),1,'no duplicate ordinary message');}console.log('PASS '+s.name);continue;}const q=await page.evaluate(()=>window.controller.queued[0]);assert.equal(q.chatId,s.expectedOwner,s.name+': queue restores to exact original chat');assert.equal(await page.evaluate(()=>window.chats.find(c=>c.id==='active-other').items.length),0,'a late answer does not steal the currently visible unrelated chat');console.log('PASS '+s.name);}assert.deepEqual(errors,[]);}finally{await browser?.close();await new Promise(r=>server.close(r));}
