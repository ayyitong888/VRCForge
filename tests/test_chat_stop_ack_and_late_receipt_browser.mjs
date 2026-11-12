import assert from 'node:assert/strict';
import { createServer } from 'node:http';
import { createRequire } from 'node:module';
import { build } from 'esbuild';
import { readFile } from 'node:fs/promises';

const recoveryResponse = process.env.VRCFORGE_LATE_RECEIPT_REPLAY_PATH
 ? JSON.parse(await readFile(process.env.VRCFORGE_LATE_RECEIPT_REPLAY_PATH,'utf8')) : undefined;
const recoveryChat = process.env.VRCFORGE_RECOVERY_CHAT_PATH
 ? JSON.parse(await readFile(process.env.VRCFORGE_RECOVERY_CHAT_PATH,'utf8')) : undefined;

const api = `
export const fetchProviderModels = async () => ({models:[]});
export const fetchReasoningVariants = async () => ({});
export const testProviderCapability = async () => { throw new Error('Unexpected provider probe'); };
export const updateApiConfig = async () => ({});
export const updateVisionConfig = async () => ({});
export const cancelAgentRunFollowup = async () => ({ok:true});
export const listAgentRunFollowups = async () => ({ok:true,items:[]});
export const fetchAgentQuestions = async () => ({ok:true,questions:[]});
export const issueComputerUseTurnGrant = async () => null;
export const requestChatTitle = async () => ({title:'Fixture'});
export const recordAgentRunQueued = async () => ({ok:true,accepted:true,mode:'followup',queueId:'q',sequence:1});
export const requestAgentRunCancel = (_endpoint,payload) => new Promise((resolve,reject) => {window.cancelRequests.push(payload);window.cancelResolve=resolve;window.cancelReject=reject;});
export const sendAgentMessage = (_endpoint,_message,session,_history,_agent,options) => new Promise((resolve,reject) => {
 window.sendCount++;window.sentSession=session;window.sendSignal=options.signal;window.sendResolve=resolve;window.sendReject=reject;
 options.signal.addEventListener('abort',()=>reject(new Error('Request cancelled.')),{once:true});
});
export const fetchAgentRunTimeline = async (_endpoint,sessionId,clientTurnId,afterSequence) => {
 window.reads.push({sessionId,clientTurnId,afterSequence});
 if(window.replayEvents){const events=window.replayEvents.filter(e=>e.sequence>=afterSequence).slice(0,200);const next=events.length?events.at(-1).sequence+1:afterSequence;return {ok:true,run:{...window.identity,status:window.ledgerStatus},events,nextSequence:next,hasMore:window.replayEvents.some(e=>e.sequence>=next)};}
 return {ok:true,run:{...window.identity,status:window.ledgerStatus},events:afterSequence===0?[{id:'accepted-progress',kind:'assistant',sequence:0,timestamp:'2026-10-23T23:41:00Z',payload:{phase:'commentary',summary:'Accepted public progress'}}]:[],nextSequence:Math.max(1,afterSequence),hasMore:false};
};
export const fetchAgentTurnResponse = async () => {window.receiptReads++;return window.receipt;};
`;
const bundle=await build({stdin:{resolveDir:process.cwd(),loader:'tsx',contents:`
import React,{useRef,useState,useCallback} from 'react';
import {createRoot} from 'react-dom/client';
import {useChatRunController} from './src/hooks/use-chat-run-controller';
import {useProviderSettings} from './src/hooks/use-provider-settings';
import {resolveRuntimeAuthorizationStatus} from './src/lib/runtime-authorization-status';
import {AgentTurnDeliveryUnconfirmedError} from './src/lib/api/http';
import {ConversationCard} from './src/components/chat/conversation-card';
import {useRuntimeTurnActivity} from './src/hooks/use-runtime-turn-activity';
import {useRuntimeTurnContinuationDelivery} from './src/hooks/use-runtime-turn-continuation';
const initial={id:'owner',sessionId:'session',projectPath:'',title:'Existing',items:[]};
window.identity={sessionId:'session',clientTurnId:'cid',turnId:'backend-turn'};
window.cancelRequests=[];window.errors=[];window.sendCount=0;window.reads=[];window.receiptReads=0;window.ledgerStatus='running';
window.receipt={ok:true,sessionId:'session',clientTurnId:'cid',status:'running'};
function Harness(){
 const[chats,setChats]=useState([initial]);const latest=useRef(chats);latest.current=chats;
 const[runs,setRuns]=useState([]);
 const endpoint='http://isolated-fixture';
 const [apiConfig,setApiConfig]=useState({configured:true,provider:'chatgpt',model:'fixture',apiKeyPresent:true,credentialFingerprint:'first'});
 const settings=useProviderSettings({endpoint,runtimeConnected:true,apiConfig,startRuntime:async()=>endpoint,refresh:async()=>{},setError:()=>{}});
 const auth=resolveRuntimeAuthorizationStatus({runtimeConnected:true,apiConfig,providerRuntimeConnectedAt:settings.providerRuntimeConnectedAt});
 window.rotateCredential=()=>setApiConfig(config=>({...config,credentialFingerprint:'changed'}));
 const updateChat=useCallback((id,fn)=>{let changed=false;latest.current=latest.current.map(c=>{if(c.id!==id)return c;const n=fn(c);changed||=n!==c;return n;});if(changed)setChats(latest.current);window.chats=latest.current;return changed;},[]);
 const appendToChat=useCallback((id,item)=>updateChat(id,c=>({...c,items:[...c.items,item]})),[updateChat]);
 const prepareTurnContext=()=>window.holdPreparation?new Promise(resolve=>{window.finishPreparation=resolve;}):Promise.resolve(null);
 const controller=useChatRunController({endpoint,runtimeConnected:true,sessionId:'session',activeRuntimeProjectPath:'',getChatById:id=>latest.current.find(c=>c.id===id),ensureActiveChat:()=> 'owner',updateChat,appendToChat,touchChat:c=>c,startRuntime:async()=>'',refresh:async()=>{},refreshRuntimeRuns:async()=>{},refreshBackgroundGoals:()=>{},handleRuntimeSessionFailure:()=>{},setError:e=>window.errors.push(e),prepareTurnContext,persistChatsNow:async()=>{},chats,bindRuntimeProviderConnection:settings.bindRuntimeProviderConnection});
 const deliver=useRuntimeTurnContinuationDelivery({chats,appendToChat,updateChat});
 const delta=useRuntimeTurnActivity({endpoint:'http://isolated-fixture',runtimeConnected:true,chats,runs,updateChat,deliver});
 window.controller=controller;window.chats=chats;window.delta=delta;
 window.failStatusRead=()=>window.sendReject(new AgentTurnDeliveryUnconfirmedError('temporary read failure'));
 window.start=()=>{window.turnPromise=controller.submitTurn({id:'cid',text:'fixture only',attachments:[],provider:'chatgpt',providerLabel:'ChatGPT',model:'fixture',sessionId:'session',planMode:false});};
 window.startBranch=(id)=>{window.turnPromise=controller.runTurnNow('owner',{id,text:'fixture only',attachments:[],provider:'chatgpt',providerLabel:'ChatGPT',model:'fixture',planMode:false},{baseItems:[],sessionId:''});};
 window.replace=(items,nextRuns)=>{latest.current=[{...initial,items}];setChats(latest.current);setRuns(nextRuns);};
 window.replaceChat=(chat,nextRuns)=>{latest.current=[chat];setChats(latest.current);setRuns(nextRuns);};
 window.setRuns=setRuns;
 const outcome=chats[0]?.items.find(i=>i.type==='error'||i.response?.continuationSource==='foreground_interrupted');
 return <div id='mounted' data-provider-status={auth.status} data-credential={apiConfig.credentialFingerprint}>
   {outcome && <ConversationCard item={outcome} canRetry onOpenDoctor={()=>{}} onRetryItem={()=>{window.retryClicked=true;}}/>}
 </div>;
}
createRoot(document.getElementById('root')).render(<Harness/>);
`},bundle:true,write:false,format:'iife',jsx:'automatic',platform:'browser',plugins:[{name:'isolated-stop-receipt-api',setup(b){
 b.onResolve({filter:/^react-i18next$/},()=>({path:'translation',namespace:'fixture'}));
 b.onResolve({filter:/^\.\.\/lib\/api$/},a=>/use-(chat-run-controller|provider-settings)\.ts$/.test(a.importer)?{path:'api',namespace:'fixture'}:undefined);
 b.onResolve({filter:/^\.\.\/lib\/api\/agent-runtime$/},a=>a.importer.endsWith('use-chat-run-controller.ts')?{path:'constants',namespace:'fixture'}:a.importer.endsWith('use-runtime-turn-activity.ts')?{path:'api',namespace:'fixture'}:undefined);
 b.onLoad({filter:/.*/,namespace:'fixture'},a=>({loader:'js',contents:a.path==='translation'?"export const useTranslation=()=>({t:k=>k}); export const initReactI18next={type:'3rdParty',init(){}};":a.path==='constants'?'export const DEFAULT_BACKGROUND_MAX_AGENTIC_TURNS=12;':api}));
}}]});
const server=createServer((_req,res)=>{res.setHeader('Content-Type','text/html');res.end(`<div id='root'></div><script>${bundle.outputFiles[0].text.replaceAll('</script','<\\/script')}</script>`);});
const{chromium}=createRequire(import.meta.url)(process.env.VRCFORGE_PLAYWRIGHT_PATH||'playwright');
let browser;
try{
 await new Promise(r=>server.listen(0,'127.0.0.1',r));browser=await chromium.launch({headless:true,channel:'msedge'});const page=await browser.newPage();const errors=[];page.on('pageerror',e=>errors.push(e.message));
 const reload=async()=>{await page.goto(`http://127.0.0.1:${server.address().port}`);await page.locator('#mounted').waitFor({state:'attached'});};
 await reload();
 if(!process.env.VRCFORGE_STOP_TEST_CASE?.startsWith('late')){
 const mounted=page.locator('#mounted');
 const progress={type:'agentRuntimeDelta',sessionId:'session',clientTurnId:'cid',timelineEvent:{
   id:'first-model-progress',sequence:0,kind:'assistant',timestamp:'2026-11-08T14:26:21Z',
   payload:{messageId:'model-message-fixture',phase:'commentary',status:'progress',summary:'Accepted model progress'},
 }};
 await page.evaluate(()=>window.start());await page.waitForFunction(()=>window.sendCount===1);
 await page.evaluate(delta=>{
   window.controller.applyRuntimeDelta({...delta,clientTurnId:'another-turn'});
   window.controller.applyRuntimeDelta({type:'agentRuntimeDelta',sessionId:'session',clientTurnId:'cid',phase:'waiting_for_model'});
 },progress);
 assert.equal(await mounted.getAttribute('data-provider-status'),'warning','dispatch and unrelated model events leave the connection unverified');
 await page.evaluate(delta=>window.controller.applyRuntimeDelta(delta),progress);
 await page.waitForFunction(()=>document.querySelector('#mounted').dataset.providerStatus==='ok',null,{timeout:2000});
 assert.equal(await page.evaluate(()=>window.sendCount),1,'real Agent output makes the status green without a separate probe');
 await page.evaluate(()=>window.rotateCredential());
 await page.waitForFunction(()=>document.querySelector('#mounted').dataset.credential==='changed');
 assert.equal(await mounted.getAttribute('data-provider-status'),'warning','changing the saved credential invalidates the old green status');
 await reload();await page.evaluate(()=>window.start());await page.waitForFunction(()=>window.sendCount===1);
 await page.evaluate(()=>window.rotateCredential());
 await page.waitForFunction(()=>document.querySelector('#mounted').dataset.credential==='changed');
 await page.evaluate(delta=>window.controller.applyRuntimeDelta(delta),progress);
 assert.equal(await mounted.getAttribute('data-provider-status'),'warning','late output for the old credential cannot verify the new credential');
 console.log('PASS real owned model output verifies only its saved provider connection');
 await reload();
 await page.evaluate(()=>{window.holdPreparation=true;window.start();});await page.waitForFunction(()=>typeof window.finishPreparation==='function');await page.evaluate(async()=>{window.controller.stopCurrentRun();window.finishPreparation(null);await window.turnPromise;});
 assert.equal(await page.evaluate(()=>window.sendCount),0,'Stop before runtime dispatch prevents admission instead of abandoning a pending turn into execution');
 assert.equal(await page.evaluate(()=>window.cancelRequests.length),0,'local preparation Stop does not claim to cancel an unadmitted backend turn');
 assert.equal(await page.evaluate(()=>window.chats[0].items.some(i=>i.type==='agent'&&i.response.status==='cancelled')),false,'local preparation interruption does not invent a backend cancelled receipt');
 console.log('PASS Stop during context preparation prevents runtime admission');
 for (const id of ['edit-fixture', 'retry-fixture']) {
   await reload();
   await page.evaluate(id=>window.startBranch(id),id);
   await page.waitForFunction(()=>window.sendCount===1);
   await page.evaluate(()=>window.controller.stopCurrentRun());
   const result=await page.evaluate(()=>({send:window.sentSession,cancel:window.cancelRequests[0]}));
   assert.equal(result.send, 'session-'+id);
   assert.equal(result.cancel.sessionId, result.send, 'Stop must use the dispatched branch session');
   assert.equal(result.cancel.clientTurnId, id);
 }
 console.log('PASS edited and retried messages cancel their dispatched session');
 await reload();
 await page.evaluate(()=>window.start());await page.waitForFunction(()=>window.sendCount===1);await page.evaluate(()=>window.controller.stopCurrentRun());await page.waitForTimeout(50);
 assert.equal(await page.evaluate(()=>window.sendSignal.aborted),false,'pending cancel acknowledgement must not abort the owned response reader');
 assert.equal(await page.evaluate(()=>window.chats[0].items.some(i=>i.type==='agent'&&i.response.status==='cancelled')),false,'pending stop cannot invent a cancelled receipt');
 await page.evaluate(()=>window.cancelResolve({ok:false,status:'rejected'}));await page.waitForTimeout(30);
 assert.equal(await page.evaluate(()=>window.sendSignal.aborted),false,'rejected cancellation leaves the real turn and reader active');
 assert.ok(await page.evaluate(()=>window.errors.length>0),'rejected cancellation is visible instead of swallowed');
 await page.evaluate(()=>window.controller.stopCurrentRun());await page.evaluate(()=>window.cancelReject(new Error('fixture cancellation identity mismatch')));await page.waitForTimeout(30);
 assert.equal(await page.evaluate(()=>window.sendSignal.aborted),false,'failed cancellation request does not abort the reader');
 assert.ok(await page.evaluate(()=>window.errors.includes('fixture cancellation identity mismatch')),'HTTP cancellation failure remains visible');
 await page.evaluate(()=>window.controller.stopCurrentRun());await page.evaluate(()=>window.cancelResolve({ok:true,status:'cancel_requested'}));await page.waitForTimeout(30);
 assert.equal(await page.evaluate(()=>window.sendSignal.aborted),false,'accepted request is not a terminal stop acknowledgement');
 await page.evaluate(()=>window.sendResolve({ok:true,status:'cancelled',...window.identity,observe:{},plan:{reply:'',summary:'',nextStep:'cancelled',planner:'fixture',shellNeeded:false},timeline:[]}));
 await page.waitForFunction(()=>window.chats[0].items.some(i=>i.type==='agent'&&i.response.plan.nextStep==='cancelled'));
 assert.equal(await page.evaluate(()=>window.sendCount),1,'stop requests never resubmit a write-bearing model turn');
 console.log('PASS pending/rejected/accepted Stop acknowledgements retain the real reader until backend terminal receipt');
 await reload();await page.evaluate(()=>window.start());await page.waitForFunction(()=>window.sendCount===1);
 await page.evaluate(()=>{window.controller.applyRuntimeDelta({type:'agentRuntimeDelta',sessionId:'session',clientTurnId:'cid',textDelta:'Accepted partial dialogue'});window.sendReject(new Error('Response transport cancellation uncertain'));});
 await page.waitForFunction(()=>window.chats[0].items.some(i=>i.type==='agent'));
 assert.equal(await page.evaluate(()=>window.chats[0].items.find(i=>i.type==='agent').response.status),'interrupted','transport cancellation wording is not backend cancellation proof');
 assert.equal(await page.evaluate(()=>window.chats[0].items.find(i=>i.type==='agent').response.plan.reply),'Accepted partial dialogue','transport interruption retains model dialogue');
 assert.equal(await page.evaluate(()=>window.chats[0].items.find(i=>i.type==='agent').response.turnId),'','local interruption does not fabricate a backend turn identity');
 await page.evaluate(()=>{window.ledgerStatus='completed';window.receipt={ok:true,sessionId:'session',clientTurnId:'cid',status:'completed',response:{ok:true,...window.identity,observe:{},plan:{reply:'Recovered after transport interruption',summary:'actual',nextStep:'done',planner:'fixture',shellNeeded:false},timeline:[]}};window.setRuns([{...window.identity,status:'completed',updatedAt:'transport-terminal'}]);});
 await page.waitForFunction(()=>window.chats[0].items.find(i=>i.type==='agent')?.response.plan.reply==='Recovered after transport interruption');
 assert.equal(await page.evaluate(()=>window.chats[0].items.filter(i=>i.type==='agent').length),1,'actual late receipt replaces the new interrupted placeholder once');
 assert.equal(await page.evaluate(()=>window.sendCount),1,'transport recovery never redispatches model work');
 console.log('PASS unknown transport cancellation preserves partial dialogue without claiming backend stop');

 await reload();await page.evaluate(()=>window.start());await page.waitForFunction(()=>window.sendCount===1);
 await page.evaluate(()=>window.failStatusRead());
 await page.waitForFunction(()=>window.chats[0].items.some(i=>i.type==='agent'||i.type==='error'));
 assert.deepEqual(await page.evaluate(()=>({
   outcomes:window.chats[0].items.filter(i=>i.type==='agent'||i.type==='error').map(i=>i.response?.status||i.type),
   globalErrors:window.errors,
   posts:window.sendCount,
 })),{outcomes:['interrupted'],globalErrors:[],posts:1},'an unreadable receipt leaves one unconfirmed turn and does not resubmit');
 await page.locator('[data-agent-transport-interrupted]').waitFor();
 assert.equal(await page.getByRole('button',{name:'chat.retryMessage',exact:true}).count(),0,'uncertain delivery is read back instead of offered for replay');

 await reload();await page.evaluate(()=>window.start());await page.waitForFunction(()=>window.sendCount===1);
 await page.evaluate(()=>window.sendReject(new Error('VRCForge runtime is still starting.')));
 await page.waitForFunction(()=>window.chats[0].items.some(i=>i.type==='error'));
 assert.deepEqual(await page.evaluate(()=>({
   failures:window.chats[0].items.filter(i=>i.type==='error').map(i=>i.text),
   globalErrors:window.errors,
   posts:window.sendCount,
 })),{failures:['VRCForge runtime is still starting.'],globalErrors:[],posts:1},'the reported preflight failure appears only in the conversation');
 await page.getByRole('alert').getByText('errors.runtime.runtime_unreachable',{exact:true}).waitFor();
 await page.getByRole('button',{name:'chat.retryMessage',exact:true}).click();
 assert.equal(await page.evaluate(()=>window.retryClicked),true);
 }
 await reload();
 await page.evaluate(synthetic=>{
 window.replace([{id:'user-cid',type:'user',text:'fixture',clientTurnId:'cid'},{id:'cancelled-cid',type:'agent',response:{ok:false,status:'cancelled',continuationSource:synthetic?undefined:'foreground_interrupted',sessionId:'session',clientTurnId:'cid',turnId:synthetic?'cid':'backend-turn',plan:{reply:'partial accepted dialogue',summary:'',nextStep:'cancelled',planner:'runtime',shellNeeded:false}}}],[{...window.identity,status:'running',updatedAt:'one'}]);
 }, process.env.VRCFORGE_STOP_TEST_CASE!=='late-exact');
 await page.waitForFunction(()=>window.reads.length===1);await page.waitForTimeout(50);
 await page.evaluate(()=>{
 window.ledgerStatus='completed';window.receipt={ok:true,sessionId:'session',clientTurnId:'cid',status:'completed',response:{ok:true,...window.identity,observe:{},plan:{reply:'Actual late tool receipt',summary:'actual',nextStep:'done',planner:'fixture',shellNeeded:false},timeline:[]}};
 window.setRuns([{...window.identity,status:'completed',updatedAt:'two'}]);
 });
 await page.waitForTimeout(150);
 assert.equal(await page.evaluate(()=>window.chats[0].items.filter(i=>i.type==='agent').length),1,'same CID late response replaces its local placeholder exactly once');
 assert.equal(await page.evaluate(()=>window.chats[0].items.find(i=>i.type==='agent')?.response.plan.reply),'Actual late tool receipt','running snapshot catchup cannot seal a placeholder before the late terminal receipt');
 assert.equal(await page.evaluate(()=>window.sendCount),0,'late recovery performs readback only, never model/write replay');
 const count=await page.evaluate(()=>window.reads.length);
 await page.evaluate(()=>window.setRuns([{...window.identity,status:'running',updatedAt:'stale'}]));await page.waitForTimeout(50);
 assert.equal(await page.evaluate(()=>window.chats[0].items.some(i=>i.type==='streaming')),false,'stale running ledger cannot resurrect a terminal reply');
 assert.equal(await page.evaluate(()=>window.reads.length),count,'stale running ledger does not refetch an already authoritative receipt');
 assert.deepEqual(errors,[]);console.log('PASS synthetic legacy cancelled placeholder, late exact receipt, stale snapshot protection, no write replay');
 await reload();
 await page.evaluate(()=>{window.ledgerStatus='completed';window.replace([{id:'user-cid',type:'user',text:'fixture without CID field'}],[{...window.identity,status:'completed',updatedAt:'same-terminal-stamp'}]);});
 await page.waitForFunction(()=>window.receiptReads===1);
 await page.evaluate(()=>{window.receipt={ok:true,sessionId:'session',clientTurnId:'cid',status:'completed',response:{ok:true,...window.identity,observe:{},plan:{reply:'Receipt persisted after terminal ledger',summary:'actual',nextStep:'needs_user_action',planner:'fixture',shellNeeded:false},timeline:[]}};window.setRuns([{...window.identity,status:'completed',updatedAt:'same-terminal-stamp'}]);});
 await page.waitForTimeout(100);
 assert.equal(await page.evaluate(()=>window.chats[0].items.find(i=>i.type==='agent')?.response.plan.reply),'Receipt persisted after terminal ledger','unchanged terminal snapshot retries an exact receipt that was not yet durable');
 assert.equal(await page.evaluate(()=>window.sendCount),0,'terminal-receipt persistence race never redispatches the model');
 console.log('PASS same-stamp terminal ledger before response persistence, legacy exact user-id owner, readback-only retry');
 for(const status of ['failed','cancelled','error']){
  await reload();
  await page.evaluate(status=>{window.ledgerStatus=status;window.replace([{id:'user-cid',type:'user',text:'fixture',clientTurnId:'cid'},{id:'actual-terminal',type:'agent',response:{ok:false,...window.identity,status,observe:{},plan:{reply:'Actual backend terminal '+status,summary:'actual',nextStep:status,planner:'runtime',shellNeeded:false}}}],[{...window.identity,status:'running',updatedAt:'stale-after-reload'}]);},status);
  await page.waitForTimeout(50);
  assert.equal(await page.evaluate(()=>window.reads.length),0,'authoritative backend '+status+' receipt blocks stale running history reads after reload');
  await page.evaluate(()=>window.setRuns([{...window.identity,status:'completed',updatedAt:'another-stale-snapshot'}]));await page.waitForTimeout(50);
  assert.equal(await page.evaluate(()=>window.receiptReads),0,'authoritative backend '+status+' terminal does not request another receipt');
  assert.equal(await page.evaluate(()=>window.chats[0].items.some(i=>i.type==='streaming')),false,'authoritative failed/cancelled terminal does not regain activity');
  assert.equal(await page.evaluate(()=>window.chats[0].items.find(i=>i.type==='agent').response.status),status,'authoritative backend terminal stays unchanged');
 }
 console.log('PASS authoritative backend failed/cancelled/error replies survive stale snapshots after reload without reads');
 if(recoveryResponse&&recoveryChat){
  await reload();
  const response={ok:recoveryResponse.ok,sessionId:recoveryResponse.sessionId,clientTurnId:recoveryResponse.clientTurnId,turnId:recoveryResponse.turnId,observe:{},plan:recoveryResponse.plan,timeline:recoveryResponse.timeline,startedAt:recoveryResponse.startedAt,completedAt:recoveryResponse.completedAt,model:recoveryResponse.model};
  assert.equal(recoveryChat.items.some(i=>i.type==='agent'&&i.response.clientTurnId===response.clientTurnId),false,'original disk snapshot reproduces the missing terminal reply');
  await page.evaluate(({response,chat})=>{window.identity={sessionId:response.sessionId,clientTurnId:response.clientTurnId,turnId:response.turnId};window.replayEvents=response.timeline;window.ledgerStatus='completed';window.receipt={ok:true,sessionId:response.sessionId,clientTurnId:response.clientTurnId,status:'completed',response};window.replaceChat(chat,[{...window.identity,status:'completed',updatedAt:'actual-terminal'}]);},{response,chat:recoveryChat});
  await page.waitForFunction(cid=>window.chats[0].items.some(i=>i.type==='agent'&&i.response.clientTurnId===cid),response.clientTurnId);
  const recovered=await page.evaluate(cid=>window.chats[0].items.filter(i=>i.type==='agent'&&i.response.clientTurnId===cid),response.clientTurnId);
  assert.equal(recovered.length,1,'actual missing wardrobe response is recovered once');
  assert.equal(recovered[0].response.plan.reply,response.plan.reply,'actual wardrobe terminal words survive readback exactly');
  assert.equal(recovered[0].response.plan.nextStep,response.plan.nextStep,'actual needs-user-action stays distinct from completion');
  const timeline=[...(recovered[0].response.timeline||[]),...(recovered[0].timeline||[])];
  assert.deepEqual(new Set(timeline.map(e=>e.id)),new Set(response.timeline.map(e=>e.id)),'all original wardrobe event occurrences survive paginated recovery');
  assert.equal(await page.evaluate(()=>window.sendCount),0,'actual wardrobe replay never dispatches model work or asset writes');
  assert.ok(await page.evaluate(()=>window.reads.length)>=Math.ceil(response.timeline.length/200),'actual original timeline follows bounded history pagination');
  console.log('PASS original missing wardrobe response replay ('+response.timeline.length+' events; exact CID; paginated; no model/write replay)');
 }
}finally{await browser?.close();await new Promise(r=>server.close(r));}
