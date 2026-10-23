import assert from "node:assert/strict";
import { createServer } from "node:http";
import { createRequire } from "node:module";
import { readFileSync } from "node:fs";
import { build } from "esbuild";
const original = process.env.VRCFORGE_CHAT_METADATA_REPLAY ? JSON.parse(readFileSync(process.env.VRCFORGE_CHAT_METADATA_REPLAY, "utf8")) : { id: "chat", sessionId: "session", items: [] };
const bundle = await build({ stdin: { loader: "tsx", resolveDir: process.cwd(), contents: `
import React, { useCallback, useRef, useState } from 'react';
import { createRoot } from 'react-dom/client';
import { useRuntimeTurnActivity } from './src/hooks/use-runtime-turn-activity';
import { useQuestionContinuationActivity, useRuntimeTurnContinuationDelivery } from './src/hooks/use-runtime-turn-continuation';
const original = ${JSON.stringify(original)};
const identity = {sessionId:original.sessionId,clientTurnId:'continuation:question:owned',turnId:'backend-owned'};
const run = {...identity,status:'running',startedAt:'2026-10-24T00:00:00Z'};
const question={questionId:'owned',sessionId:identity.sessionId,status:'answered',runtimeContinuationStatus:'running',runtimeContinuation:{...identity,status:'running'}};
window.identity=identity;window.pending=[];
function Harness(){
 const [chats,setChats]=useState([original]); const ref=useRef(chats);ref.current=chats;
 const [questions,setQuestions]=useState(location.search ? [question] : []);const [runs,setRuns]=useState([run]);
 const updateChat=useCallback((id,updater)=>{let changed=false;ref.current=ref.current.map(c=>{if(c.id!==id)return c;const n=updater(c);changed ||= n!==c;return n});if(changed)setChats(ref.current);return changed},[]);
 const append=useCallback((id,item)=>updateChat(id,c=>({...c,items:[...c.items,item]})),[updateChat]);
 const deliver=useRuntimeTurnContinuationDelivery({chats,updateChat,appendToChat:append});
 const questionDelta=useQuestionContinuationActivity({chats,questions,runs,updateChat,settledClientTurnIds:new Set()});
 const externalDelta=useRuntimeTurnActivity({endpoint:'http://fixture',runtimeConnected:true,chats,runs,updateChat,deliver});
 window.discoverQuestion=()=>setQuestions([question]);
 window.delta=d=>{questionDelta(d);externalDelta(d)};window.deliver=deliver;window.chats=chats;window.setRuns=setRuns;
 return <main>{chats[0].items.filter(i=>i.type==='streaming'&&i.clientTurnId===identity.clientTurnId).map(i=><section data-owner={i.id} key={i.id}><output>{i.text}</output>{(i.timeline||[]).map(e=><p data-event={e.id} key={e.id}>{e.payload?.summary}</p>)}</section>)}</main>
}createRoot(document.getElementById('root')).render(<Harness/>);
` }, bundle:true,write:false,platform:"browser",format:"iife",jsx:"automatic",plugins:[{name:"transport",setup(b){
 b.onResolve({filter:/^\.\.\/lib\/api\/agent-runtime$/},args=>args.importer.endsWith('use-runtime-turn-activity.ts')?{path:'transport',namespace:'fixture'}:undefined);
 b.onLoad({filter:/.*/,namespace:'fixture'},()=>({loader:'js',contents:`export const fetchAgentRunTimeline=()=>new Promise(resolve=>window.pending.push(resolve));export const fetchAgentTurnResponse=async()=>({ok:false});`}));
}}]});
const server=createServer((_q,r)=>{r.setHeader('Content-Type','text/html');r.end(`<div id="root"></div><script>${bundle.outputFiles[0].text.replaceAll('</script','<\\/script')}</script>`)});
let browser;
try {
 await new Promise(resolve=>server.listen(0,'127.0.0.1',resolve));
 const {chromium}=createRequire(import.meta.url)(process.env.VRCFORGE_PLAYWRIGHT_PATH||'playwright');
 browser=await chromium.launch({headless:true,channel:'msedge'});
 for (const mode of ['', '?question-first']) {
 const page=await browser.newPage();const errors=[];page.on('pageerror',e=>errors.push(e.message));
 await page.goto(`http://127.0.0.1:${server.address().port}${mode}`);await page.waitForSelector('[data-owner]', { state: 'attached' });
 const event={id:'accepted-before-question',sequence:0,timestamp:'2026-10-24T00:00:01Z',kind:'assistant',payload:{phase:'commentary',summary:'保留已经接收的进度'}};
 await page.evaluate(e=>window.delta({type:'agentRuntimeDelta',...window.identity,timelineEvent:e}),event);
 await page.waitForSelector('[data-event="accepted-before-question"]');await page.evaluate(()=>window.discoverQuestion());
 await page.waitForTimeout(100);assert.equal(await page.locator('[data-owner]').count(),1,'question discovery reuses the runtime owner in the DOM');
 await page.evaluate(()=>window.delta({type:'agentRuntimeDelta',...window.identity,textDelta:'once'}));
 await page.waitForFunction(()=>document.querySelector('output')?.textContent==='once');
 assert.equal(await page.locator('[data-event="accepted-before-question"]').count(),1);
 const final={schema:'vrcforge.runtime_turn_event.v1',continuationSource:'question_answered',sessionId:original.sessionId,clientTurnId:'continuation:question:owned',turnId:'backend-owned',plan:{reply:'需要确认下一步',nextStep:'needs_user_action'},timeline:[]};
 await page.evaluate(e=>window.deliver(e),final);await page.waitForFunction(()=>!document.querySelector('[data-owner]'));
 await page.evaluate(e=>window.deliver(e),final);
 const result=await page.evaluate(()=>window.chats[0]);
 assert.equal(result.items.filter(i=>i.type==='agent'&&i.response.clientTurnId===final.clientTurnId).length,1,'one durable reply survives replay');
 assert.equal(result.items.find(i=>i.type==='agent'&&i.response.clientTurnId===final.clientTurnId).timeline[0].id,event.id,'terminal delivery keeps accepted history');
 assert.deepEqual(errors,[]);await page.close();
 }
 console.log(`PASS browser actual hooks + DOM (${original.items.length} original items), both discovery orders, shared CID owner, once-only delta, terminal timeline transfer`);
} finally {await browser?.close();await new Promise(resolve=>server.close(resolve));}
