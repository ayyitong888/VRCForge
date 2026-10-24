import assert from 'node:assert/strict';
import { build } from 'esbuild';
import { readFileSync } from 'node:fs';
const built = await build({entryPoints:['src/lib/chat-storage-reconcile.ts'],bundle:true,write:false,platform:'node',format:'esm',
  plugins:process.env.VRCFORGE_CHAT_RECONCILE_BASELINE ? [{name:'baseline',setup(b){b.onLoad({filter:/chat-storage-reconcile\.ts$/},()=>({loader:'ts',contents:readFileSync(process.env.VRCFORGE_CHAT_RECONCILE_BASELINE,'utf8')}));}}] : []});
const {snapshotChatFingerprints:snapshot,reconcileChatStorage:reconcile,chatStorageValuesEqual:equal}=await import(`data:text/javascript;base64,${Buffer.from(built.outputFiles[0].text).toString('base64')}`);
const text='payload '.repeat(800000);
const chat={id:'chat',title:'original',items:[{id:'item',type:'agent',response:{steps:[{output:text}],plan:{reply:'done'}}}]};
const originalStringify=JSON.stringify;
let baseline,result;
try {
  JSON.stringify=()=>{throw new Error('Chat reconciliation must not serialize entire transcripts');};
  baseline=snapshot([chat]);
  result=reconcile(baseline,[chat],[chat]);
} finally { JSON.stringify=originalStringify; }
assert.equal(result.status,'merged');
assert.ok(Object.isFrozen(baseline.chat.items[0].response.steps[0]));
assert.equal(baseline.chat.items[0].response.steps[0].output,text);
chat.items[0].response.steps[0].output='local edit';
assert.equal(baseline.chat.items[0].response.steps[0].output,text,'live in-place edits cannot corrupt baseline');
const remote={...chat,items:[{...chat.items[0],response:{...chat.items[0].response,steps:[{output:'remote edit'}]}}]};
assert.equal(reconcile(baseline,[chat],[remote]).status,'conflict','in-place local edit remains protected from remote overwrite');
assert.equal(equal({b:2,a:1,absent:undefined},{a:1,b:2}),true);
assert.equal(equal({a:1},{a:2}),false);
assert.equal(equal([undefined],[null]),true);
assert.equal(equal([1,2],[2,1]),false);
console.log('PASS allocation-safe chat snapshots, immutable baselines, original conflict semantics');

const deltaBuilt=await build({entryPoints:['src/lib/chat-storage-delta.ts'],bundle:true,write:false,platform:'node',format:'esm'});
const {buildChatStoragePatches}=await import(`data:text/javascript;base64,${Buffer.from(deltaBuilt.outputFiles[0].text).toString('base64')}`);
const clone=globalThis.structuredClone;
try {
  globalThis.structuredClone=(value)=>{
    assert.ok(!value?.items,'metadata extraction must not clone the entire chat transcript');
    return clone(value);
  };
  const patches=buildChatStoragePatches([chat],baseline,{chat:'a'.repeat(64)},{chat:{id:true,title:true}});
  assert.deepEqual(patches.chat.itemPatches.item,[{op:'replace',path:'/response/steps/0/output',value:'local edit'}]);
  assert.equal(baseline.chat.items[0].response.steps[0].output,text);
} finally {globalThis.structuredClone=clone;}
console.log('PASS immutable baseline delta and metadata extraction without transcript cloning');
