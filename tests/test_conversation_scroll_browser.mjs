import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import { createServer } from "node:http";
import { createRequire } from "node:module";
import { build } from "esbuild";

const baseline = process.env.VRCFORGE_SCROLL_BASELINE === "1";
const { chromium } = createRequire(import.meta.url)(process.env.VRCFORGE_PLAYWRIGHT_PATH || "playwright");
const app = readFileSync(process.env.VRCFORGE_SCROLL_SOURCE_APP || "src/App.tsx", "utf8");
const oldEffects = app.slice(app.indexOf("  useEffect(() => {\n    updateConversationPinned(true);"), app.indexOf("  useEffect(() => {\n    if (projectInitRef", app.indexOf("  useEffect(() => {\n    updateConversationPinned(true);")));
if (baseline) assert.match(oldEffects, /\[activeChatId, conversation.length\]/, "baseline must replay the actual old App effects");
else {
  assert.match(app, /const conversationScroll = useConversationScroll\(/, "App must use the tested transcript scroll owner");
  assert.match(app, /onScrollToBottom=\{conversationScroll\.scrollToBottom\}/);
  assert.doesNotMatch(app, /\[activeChatId, conversation.length\]/, "remove the incomplete old follow effect");
}
const bundle = await build({ stdin: { resolveDir: process.cwd(), loader: "tsx", contents: `
import React, {useState,useRef,useEffect,useCallback} from 'react';
import {createRoot} from 'react-dom/client';
import {ConversationCard} from './src/components/chat/conversation-card';
${baseline ? "" : "import {useConversationScroll} from './src/hooks/use-conversation-scroll';"}
${baseline ? `function useConversationScroll({chatKey:activeChatId,conversation,endRef:conversationEndRef}) {
const conversationPinnedRef=useRef(true); const [pinned,setPinned]=useState(true);
const updateConversationPinned=useCallback(v=>{conversationPinnedRef.current=v;setPinned(v)},[]);
${oldEffects}
return {pinned,onScroll:e=>updateConversationPinned(e.scrollHeight-e.scrollTop-e.clientHeight<24),scrollToBottom:()=>{updateConversationPinned(true);conversationEndRef.current?.scrollIntoView({block:'end'})}};
}` : ""}
const mk=(text)=>({id:'same-stream',type:'streaming',clientTurnId:'fixture-turn',createdAt:'2026-10-23T23:00:00Z',text,phase:'receiving_response'});
const seed=Array.from({length:45},(_,i)=>'Original paragraph '+i+' in conversation.').join('\\n\\n');
function Fixture(){const [id,setId]=useState('a');const [visible,setVisible]=useState(true);const [chats,setChats]=useState({a:[mk(seed)],b:[mk(seed)]}); const endRef=useRef(null);
const conversation=chats[id];const scroll=useConversationScroll({chatKey:id,${baseline ? "conversation," : ""}endRef});
window.grow=()=>setChats(s=>({...s,[id]:[mk(s[id][0].text+'\\n\\n'+seed)]}));
window.switchChat=setId;window.setVisible=setVisible;window.lateGrow=()=>{document.getElementById('late-size').style.height='600px'};
return <><output id='chat'>{id}</output><output id='pinned'>{String(scroll.pinned)}</output><button id='bottom' onClick={scroll.scrollToBottom}>bottom</button>
{visible?<main data-chat-history-scroll onScroll={e=>scroll.onScroll(e.currentTarget)} style={{height:400,overflowY:'auto'}}><section>
{conversation.map(i=><ConversationCard key={i.id} item={i}/>)}<div id='late-size'/><div ref={endRef}/></section></main>:null}</>}
createRoot(document.getElementById('root')).render(<React.StrictMode><Fixture/></React.StrictMode>);
` }, bundle: true, write: false, format: "iife", platform: "browser" });
const server = createServer((_req, res) => { res.setHeader("Content-Type", "text/html"); res.end(`<style>svg{width:14px;height:14px}p{line-height:22px;margin:12px 0}body{font:14px sans-serif}</style><div id="root"></div><script>${bundle.outputFiles[0].text.replaceAll("</script", "<\\/script")}</script>`); });
let browser;
try {
  await new Promise(r => server.listen(0, "127.0.0.1", r));
  browser = await chromium.launch({ channel: "msedge", headless: true });
  const page = await browser.newPage(); const errors=[];page.on("pageerror", e=>errors.push(e.message));
  await page.goto(`http://127.0.0.1:${server.address().port}`);
  const scroller=page.locator('[data-chat-history-scroll]');
  const metrics=()=>scroller.evaluate(e=>({top:e.scrollTop,max:e.scrollHeight-e.clientHeight}));
  await page.waitForFunction(()=>{let e=document.querySelector('[data-chat-history-scroll]');return e.scrollTop>500});
  await page.evaluate(()=>window.grow());
  await page.waitForTimeout(120);
  let m=await metrics();assert.ok(m.max-m.top<2,`same stream item content growth must follow bottom: ${JSON.stringify(m)}`);
  await scroller.evaluate(e=>{e.scrollTop=250});await page.waitForFunction(()=>document.getElementById('pinned').textContent==='false');
  await page.evaluate(()=>window.grow());await page.waitForTimeout(100);
  assert.ok(Math.abs((await metrics()).top-250)<2,'reading old messages must not jump on stream growth');
  await page.evaluate(()=>window.switchChat('b'));await page.waitForFunction(()=>document.getElementById('chat').textContent==='b');
  await page.evaluate(()=>window.switchChat('a'));await page.waitForFunction(()=>document.getElementById('chat').textContent==='a');
  assert.ok(Math.abs((await metrics()).top-250)<2,'returning to a chat must restore its reading position');
  await page.evaluate(()=>window.setVisible(false));await scroller.waitFor({state:'detached'});
  await page.evaluate(()=>window.setVisible(true));await scroller.waitFor();
  assert.ok(Math.abs((await metrics()).top-250)<2,'returning from another workspace view must restore reading position');
  await page.locator('#bottom').click();await page.waitForFunction(()=>document.getElementById('pinned').textContent==='true');
  await page.evaluate(()=>window.lateGrow());await page.waitForTimeout(100);
  m=await metrics();assert.ok(m.max-m.top<2,'late image/layout height changes must follow bottom');
  assert.deepEqual(errors,[]);console.log('PASS: actual ConversationCard same-stream growth, manual reading, chat return, bottom action and late layout growth.');
} finally {await browser?.close();await new Promise(r=>server.close(r));}
