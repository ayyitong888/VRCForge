import assert from 'node:assert/strict';
import test from 'node:test';
import {readFile} from 'node:fs/promises';
import path from 'node:path';
import {fileURLToPath} from 'node:url';
import {build} from 'esbuild';
const root=path.resolve(path.dirname(fileURLToPath(import.meta.url)),'..');
const bundled=await build({entryPoints:[path.join(root,'src/hooks/use-context-compaction-controller.ts')],bundle:true,write:false,platform:'node',format:'esm',plugins:[{name:'local-controller-ports',setup(b){
 b.onResolve({filter:/^(react|react-i18next)$|^\.\.\/lib\/api$/},a=>({path:a.path,namespace:'ports'}));
 b.onLoad({filter:/.*/,namespace:'ports'},a=>({contents:a.path==='react'?'export const useRef=x=>({current:x});export const useState=x=>[x,()=>{}];':a.path==='react-i18next'?'export const useTranslation=()=>({t:k=>k,i18n:{language:"zh-CN"}});':'export const compactAgentHistory=(...args)=>globalThis.__budgetHarness.compact(...args);',loader:'js'}));
}}]});
const {useContextCompactionController}=await import(`data:text/javascript;base64,${Buffer.from(bundled.outputFiles[0].contents).toString('base64')}`);
function harness(items, summary="历史仅检查，未修改。"){
 const chat={id:'budget-test',sessionId:'session',revision:0,items:structuredClone(items)};
 const requests=[];const errors=[];
 globalThis.__budgetHarness={compact:async(_endpoint,request)=>{requests.push(request);return {ok:true,summary,summaryDigest:'offline-summary'};}};
 const controller=useContextCompactionController({getChatById:()=>structuredClone(chat),updateChat:(_id,fn)=>Object.assign(chat,fn(chat)),updateChatIfRevision:(_id,revision,fn)=>{if(chat.revision!==revision)return false;Object.assign(chat,fn(chat));chat.revision++;return true;},persistChatsNow:async()=>{},setError:x=>errors.push(x),hasUnresolvedRuntimeState:()=>false});
 return {chat,requests,errors,run:()=>controller.compactChat({chatId:chat.id,endpoint:'offline',trigger:'manual',phase:'standalone',provider:'chatgpt',model:'fixture',contextLimit:500000})};
}
const user=(id,text)=>({id,type:'user',text});
const agent=(id,text,extra={})=>({id,type:'agent',response:{ok:true,plan:{reply:text},...extra},timeline:[]});

test('actual failed projection requests only old source and 100-token summary budget',{skip:!process.env.VRCFORGE_COMPACT_PROJECTION},async()=>{
 const raw=JSON.parse(await readFile(process.env.VRCFORGE_COMPACT_PROJECTION,'utf8'));
 const before=JSON.stringify(raw);
 const h=harness(raw.state.items);const outcome=await h.run();
 assert.equal(h.requests.length,1);
 assert.equal(h.requests[0].targetTokens,100);
 assert.equal(h.requests[0].history.length,5);
 assert.ok(h.requests[0].history.every(x=>x.role==='user'));
 assert.equal(outcome.status,'applied');
 assert.deepEqual(h.chat.items.slice(-2),raw.state.items.slice(-2));
 assert.equal(JSON.stringify(raw),before);
 // Replaying the original too-long summary must still fail the unchanged gate.
 const old=harness(raw.state.items,raw.response.summary);
 assert.equal((await old.run()).status,'failed');
 assert.equal(old.chat.compaction.failureClass,'reduction_gate');
 assert.equal(old.chat.sessionId,'session');
 assert.deepEqual(old.chat.items,raw.state.items);
 // Immediately re-compacting this short result has no useful 64-token room.
 assert.equal((await h.run()).status,'skipped');
 assert.equal(h.requests.length,1);
 assert.equal(h.errors.at(-1),'compact.noContent');
});
test('single latest turn or insufficient room skips with manual noContent feedback',async()=>{
 for(const items of [[user('u','current'),agent('a','current answer')],[user('old','tiny'),user('u','current '.repeat(100)),agent('a','answer '.repeat(100))]]){
  const h=harness(items);assert.equal((await h.run()).status,'skipped');assert.equal(h.requests.length,0);assert.deepEqual(h.errors,['compact.noContent']);assert.equal(h.chat.sessionId,'session');
 }
});
test('previous compact marker filters covered dialogue while durable references survive',async()=>{
 const items=[{id:'old-marker',type:'compact',detail:'Earlier summarized work '.repeat(80),summarizedItemIds:['covered-u','covered-a']},user('covered-u','DO_NOT_RESURRECT'),agent('covered-a','DO_NOT_RESURRECT',{approvalId:'approval-old'}),user('later-u','later work '.repeat(100)),agent('later-a','later answer '.repeat(100),{write:{result:{checkpointId:'checkpoint-old'}}}),{id:'retained-result',type:'result',approvalId:'approval-card',result:{ok:true}},user('latest','keep latest'),agent('latest-answer','unchanged')];
 const h=harness(items);assert.equal((await h.run()).status,'applied');
 const text=h.requests[0].history.map(x=>x.text).join('\n');
 assert.ok(!text.includes('DO_NOT_RESURRECT'));assert.ok(text.includes('Earlier summarized work'));assert.ok(text.includes('approval-old'));assert.ok(text.includes('checkpoint-old'));
 assert.deepEqual(h.chat.items.find(x=>x.id==='retained-result'),items.find(x=>x.id==='retained-result'));
 assert.deepEqual(h.chat.items.slice(-2),items.slice(-2));
});

test('budget probe cannot collide with a stored compaction id',async()=>{
 const now=Date.now,random=Math.random;
 Date.now=()=>42;Math.random=()=>0.5;
 try {
  const collision='compact-42-8';
  const h=harness([{id:collision,type:'compact',detail:'Previous summary '.repeat(100),summarizedItemIds:[]},user('latest','keep')]);
  assert.equal((await h.run()).status,'applied');
  assert.equal(h.chat.items.find(x=>x.id===collision).detail,'Previous summary '.repeat(100));
  assert.ok(h.chat.items.some(x=>x.type==='compact'&&x.id===collision+'-'));
 } finally {Date.now=now;Math.random=random;}
});

test('existing minimum allows exactly 64 summary tokens but rejects 63 before dispatch',async()=>{
 const enough=harness([user('old','中'.repeat(73)),user('latest','z')]);
 assert.equal((await enough.run()).status,'applied');assert.equal(enough.requests[0].targetTokens,64);
 const short=harness([user('old','中'.repeat(72)),user('latest','z')]);
 assert.equal((await short.run()).status,'skipped');assert.equal(short.requests.length,0);assert.deepEqual(short.errors,['compact.noContent']);
});
