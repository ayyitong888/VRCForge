import assert from 'node:assert/strict';
import {build} from 'esbuild';
import {createServer} from 'node:http';
import {createRequire} from 'node:module';

const user=(id)=>({id:'user-'+id,type:'user',clientTurnId:id,text:id});
const owner={id:'chat-lane',sessionId:'runtime-owner',title:'Modification chat',projectPath:'E:/unity/zcode test',items:[user('parent')]};
const other={id:'active-other',sessionId:'runtime-other',title:'Other visible chat',projectPath:'',items:[]};
const relayInputId='agent-forward-source-action';
const relayOrigin={sourceChatId:'active-other',sourceClientTurnId:'source-turn',sourceActionId:'source-action'};
const externalEntry={
  queueId:'followup_00000005',sequence:5,sessionId:'chat-lane',runtimeSessionId:'runtime-owner',
  clientTurnId:relayInputId,message:'Continue the modification using the author layer instructions.',
  status:'pending',projectType:'unity',projectPath:'E:/unity/zcode test',
};
const api=`export const listAgentRunFollowups=async()=>{return {ok:true,items:window.fixture.entries};};export const fetchAgentQuestions=async()=>({ok:true,questions:[]});export const cancelAgentRunFollowup=async()=>({ok:true});export const issueComputerUseTurnGrant=async()=>null;export const requestAgentRunCancel=async()=>({ok:true});export const requestChatTitle=async()=>({title:'Modification chat'});export const recordAgentRunQueued=async()=>({ok:true,accepted:true,mode:'followup'});export const sendAgentMessage=(_e,_m,sessionId,_h,_a,options)=>{window.sends.push({sessionId,clientTurnId:options.clientTurnId,followupQueueId:options.followupQueueId});return new Promise(resolve=>window.resolveSend=resolve);};`;
const bundle=await build({stdin:{resolveDir:process.cwd(),loader:'tsx',contents:`
import React,{useRef,useState,useCallback}from'react';import{createRoot}from'react-dom/client';import{useChatRunController}from'./src/hooks/use-chat-run-controller';
window.fixture={entries:[]};window.sends=[];window.errors=[];window.ensureCalls=0;
function Harness(){const[chats,setChats]=useState([${JSON.stringify(owner)},${JSON.stringify(other)}]);const[queueVersion,setQueueVersion]=useState(0);const latest=useRef(chats);latest.current=chats;const updateChat=useCallback((id,fn)=>{latest.current=latest.current.map(c=>c.id===id?fn(c):c);setChats(latest.current);window.chats=latest.current;},[]);const c=useChatRunController({endpoint:'http://isolated-fixture',runtimeConnected:true,sessionId:'runtime-other',activeRuntimeProjectPath:'',getChatById:id=>latest.current.find(c=>c.id===id),ensureActiveChat:()=>{window.ensureCalls++;return'active-other';},updateChat,appendToChat:(id,item)=>updateChat(id,c=>({...c,items:[...c.items,item]})),touchChat:c=>c,startRuntime:async()=>'',refresh:async()=>{},refreshRuntimeRuns:async()=>{},refreshBackgroundGoals:()=>{},handleRuntimeSessionFailure:()=>{},setError:e=>window.errors.push(e),persistChatsNow:async()=>{},chats,chatStorageReady:true,runtimeQueueVersion:queueVersion});window.controller=c;window.receiveRelay=item=>updateChat('chat-lane',chat=>({...chat,items:[...chat.items,item]}));window.bump=()=>setQueueVersion(v=>v+1);return <div id='mounted'/>;}createRoot(document.getElementById('root')).render(<Harness/>);
`},bundle:true,write:false,format:'iife',platform:'browser',jsx:'automatic',plugins:[{name:'live-resync-stub',setup(b){b.onResolve({filter:/^react-i18next$/},()=>({path:'i18n',namespace:'fixture'}));b.onResolve({filter:/^\.\.\/lib\/api$/},a=>a.importer.endsWith('use-chat-run-controller.ts')?{path:'api',namespace:'fixture'}:undefined);b.onResolve({filter:/^\.\.\/lib\/api\/agent-runtime$/},a=>a.importer.endsWith('use-chat-run-controller.ts')?{path:'constants',namespace:'fixture'}:undefined);b.onLoad({filter:/.*/,namespace:'fixture'},a=>({loader:'js',contents:a.path==='i18n'?"export const useTranslation=()=>({t:k=>k});":a.path==='constants'?'export const DEFAULT_BACKGROUND_MAX_AGENTIC_TURNS=12;':api}));}}]});
const server=createServer((_q,r)=>{r.setHeader('Content-Type','text/html');r.end(`<div id='root'></div><script>${bundle.outputFiles[0].text.replaceAll('</script','<\\/script')}</script>`);});const{chromium}=createRequire(import.meta.url)(process.env.VRCFORGE_PLAYWRIGHT_PATH||'playwright');let browser;
try{
  await new Promise(r=>server.listen(0,'127.0.0.1',r));
  browser=await chromium.launch({headless:true,channel:'msedge'});
  const page=await browser.newPage();
  const errors=[];page.on('pageerror',e=>errors.push(e.message));page.on('console',m=>{if(m.type()==='error')errors.push(m.text());});
  await page.goto(`http://127.0.0.1:${server.address().port}/`);
  await page.locator('#mounted').waitFor({state:'attached'});
  await page.waitForTimeout(80);
  assert.equal(await page.evaluate(()=>window.controller.queued.length),0,'mount with an empty durable queue restores nothing');
  // A queue event can arrive while the tab still holds the older transcript.
  await page.evaluate(entry=>{window.fixture.entries=[entry];window.bump();},externalEntry);
  await page.waitForTimeout(100);
  assert.equal(await page.evaluate(()=>window.sends.length),0,'an unhydrated relay is not recreated as a human input');
  await page.evaluate(({entry,origin})=>{
    window.receiveRelay({id:'user-'+entry.clientTurnId,type:'user',text:entry.message,clientTurnId:entry.clientTurnId,
      forwardedByAgent:true,forwardedFrom:origin,deliveryMode:'trigger_turn'});
    window.fixture.entries=[entry];
    window.bump();
  },{entry:externalEntry,origin:relayOrigin});
  await page.waitForFunction(()=>window.sends.length===1);
  const getRelay=()=>page.evaluate(id=>window.chats.find(c=>c.id==='chat-lane').items.find(i=>i.type==='user'&&i.clientTurnId===id),relayInputId);
  const queuedBubble=await getRelay();
  assert.equal(queuedBubble?.composerQueued,true,'externally queued follow-up stays above the composer during dispatch');
  assert.equal(queuedBubble.forwardedByAgent,true);
  assert.deepEqual(queuedBubble.forwardedFrom,relayOrigin);
  assert.equal(queuedBubble.deliveryMode,'trigger_turn');
  const send=await page.evaluate(()=>window.sends[0]);
  assert.equal(send.followupQueueId,'followup_00000005','dispatch claims the durable queue entry');
  assert.equal(await page.evaluate(()=>window.ensureCalls),0,'resync never fabricates a new chat for a matching lane');
  // An in-flight claimed entry must not resurrect as a ghost during a re-sync.
  await page.evaluate(()=>{window.resolveSend({ok:true,sessionId:'runtime-owner',clientTurnId:window.sends[0].clientTurnId,turnId:'t1',startedAt:new Date().toISOString(),observe:{},plan:{reply:'done',summary:'done',nextStep:'needs_user_action',planner:'fixture',shellNeeded:false},timeline:[]});});
  await page.waitForFunction(()=>window.controller.queued.length===0);
  assert.deepEqual((await getRelay()).forwardedFrom,relayOrigin);
  const reply=await page.evaluate(()=>window.chats.find(c=>c.id==='chat-lane').items.find(i=>i.type==='agent'));
  assert.equal(reply.id,'t1');
  assert.equal(reply.response.clientTurnId,relayInputId);
  await page.evaluate(entry=>{window.fixture.entries=[{...entry,status:'claimed'}];window.bump();});
  await page.waitForTimeout(120);
  assert.equal(await page.evaluate(()=>window.controller.queued.length),0,'claimed in-flight entries are not resurrected by a re-sync');
  assert.deepEqual(errors,[]);
  console.log('PASS followup live re-sync');
}finally{
  await browser?.close();
  await new Promise(r=>server.close(r));
}
