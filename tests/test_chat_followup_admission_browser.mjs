import assert from 'node:assert/strict';
import {build} from 'esbuild';
import {createServer} from 'node:http';
import {createRequire} from 'node:module';
const user=(id)=>({id:'user-'+id,type:'user',clientTurnId:id,text:id});
const owner={id:'opaque-owner',sessionId:'runtime-owner',title:'Existing owner',projectPath:'',items:[user('parent')]};
const other={id:'active-other',sessionId:'runtime-other',title:'Other visible chat',projectPath:'',items:[]};
const entry={queueId:'queued-answer',sequence:1,sessionId:'runtime-owner',runtimeSessionId:'runtime-owner',targetClientTurnId:'parent',clientTurnId:'opaque-input',message:'Original full task prompt',status:'claimed'};
const scenarios=[{name:'rejection',chats:[owner,other],entries:[{...entry,sessionId:'opaque-owner',clientTurnId:'input',status:'pending'}]}];
const api=`export const listAgentRunFollowups=async()=>({ok:true,items:window.fixture.entries});export const fetchAgentQuestions=async()=>({ok:true,questions:[]});export const cancelAgentRunFollowup=async()=>({ok:true,status:'cancelled'});export const issueComputerUseTurnGrant=async()=>null;export const requestAgentRunCancel=async()=>({ok:true});export const requestChatTitle=async()=>({title:'Existing owner'});export const recordAgentRunQueued=async()=>({ok:true,accepted:true,mode:'followup',queueId:'queued-answer'});export const sendAgentMessage=async(_e,_m,sessionId,_h,_a,options)=>{window.sends.push({sessionId,clientTurnId:options.clientTurnId});if(window.sends.length===1)throw new window.ApiError('HTTP 409',409,{code:'followup_admission_rejected',message:'not admitted'});return {ok:true,sessionId,clientTurnId:options.clientTurnId,turnId:'successful-retry',observe:{},plan:{reply:'done',summary:'done',nextStep:'done',planner:'fixture',shellNeeded:false},timeline:[]};};`;
const bundle=await build({stdin:{resolveDir:process.cwd(),loader:'tsx',contents:`
import {ApiError} from './src/lib/api/http';window.ApiError=ApiError;
import React,{useRef,useState,useCallback}from'react';import{createRoot}from'react-dom/client';import{useChatRunController}from'./src/hooks/use-chat-run-controller';
const cases=${JSON.stringify(scenarios)};window.fixture=structuredClone(cases.find(c=>c.name===new URL(location.href).searchParams.get('case')));window.ensureCalls=0;window.sends=[];window.errors=[];
function Harness(){const[chats,setChats]=useState(window.fixture.chats);const latest=useRef(chats);latest.current=chats;const updateChat=useCallback((id,fn)=>{latest.current=latest.current.map(c=>c.id===id?fn(c):c);setChats(latest.current);window.chats=latest.current;},[]);const c=useChatRunController({endpoint:'http://isolated-fixture',runtimeConnected:true,sessionId:'runtime-other',activeRuntimeProjectPath:'',getChatById:id=>latest.current.find(c=>c.id===id),ensureActiveChat:()=>{window.ensureCalls++;return'active-other';},updateChat,appendToChat:(id,item)=>updateChat(id,c=>({...c,items:[...c.items,item]})),touchChat:c=>c,startRuntime:async()=>'',refresh:async()=>{},refreshRuntimeRuns:async()=>{},refreshBackgroundGoals:()=>{},handleRuntimeSessionFailure:()=>{},setError:e=>window.errors.push(e),persistChatsNow:async()=>{},chats,chatStorageReady:true});window.controller=c;window.chats=chats;return <div id='mounted'/>;}createRoot(document.getElementById('root')).render(<Harness/>);
`},bundle:true,write:false,format:'iife',platform:'browser',jsx:'automatic',plugins:[{name:'restore-api-stub',setup(b){b.onResolve({filter:/^react-i18next$/},()=>({path:'i18n',namespace:'fixture'}));b.onResolve({filter:/^\.\.\/lib\/api$/},a=>a.importer.endsWith('use-chat-run-controller.ts')?{path:'api',namespace:'fixture'}:undefined);b.onResolve({filter:/^\.\.\/lib\/api\/agent-runtime$/},a=>a.importer.endsWith('use-chat-run-controller.ts')?{path:'constants',namespace:'fixture'}:undefined);b.onLoad({filter:/.*/,namespace:'fixture'},a=>({loader:'js',contents:a.path==='i18n'?"export const useTranslation=()=>({t:k=>k});":a.path==='constants'?'export const DEFAULT_BACKGROUND_MAX_AGENTIC_TURNS=12;':api}));}}]});
const server=createServer((_q,r)=>{r.setHeader('Content-Type','text/html');r.end(`<div id='root'></div><script>${bundle.outputFiles[0].text.replaceAll('</script','<\\/script')}</script>`);});const{chromium}=createRequire(import.meta.url)(process.env.VRCFORGE_PLAYWRIGHT_PATH||'playwright');let browser;
try {
 await new Promise(r=>server.listen(0,'127.0.0.1',r));
 browser=await chromium.launch({headless:true,channel:'msedge'});
 const page=await browser.newPage();const errors=[];page.on('pageerror',e=>errors.push(e.message));
 await page.goto(`http://127.0.0.1:${server.address().port}/?case=rejection`);
 await page.waitForFunction(()=>window.controller?.queued[0]?.queueStatus==='paused');
 assert.equal(await page.evaluate(()=>window.sends.length),1);
 assert.equal(await page.evaluate(()=>window.chats.find(c=>c.id==='opaque-owner').items.find(i=>i.clientTurnId==='input').queueEnvelope.queueId),'queued-answer');
 await page.evaluate(()=>window.controller.resumeQueuedTurns());
 await page.waitForTimeout(150);
 assert.equal(await page.evaluate(()=>window.sends.length),1,'automatic drain must not retry admission errors');
 await page.evaluate(()=>window.controller.resumeQueuedTurns(true));
 await page.waitForFunction(()=>window.sends.length===2&&window.chats.some(c=>c.items.some(i=>i.type==='agent')));
 assert.equal(await page.evaluate(()=>window.controller.queued.length),0);
 assert.equal(await page.evaluate(()=>window.sends[1].clientTurnId),'input');
 assert.deepEqual(errors,[]);
 console.log('PASS rejection retains queued input; automatic drain stays paused; manual retry succeeds');
} finally {await browser?.close();await new Promise(r=>server.close(r));}
