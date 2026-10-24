import assert from 'node:assert/strict';
import {createRequire} from 'node:module';
import {readFile} from 'node:fs/promises';
import {build} from 'esbuild';
const require=createRequire(import.meta.url);
async function bundle(path){const r=await build({entryPoints:[path],bundle:true,write:false,platform:'node',format:'cjs',external:['react']});const m={exports:{}};new Function('require','module','exports',r.outputFiles[0].text)(require,m,m.exports);return m.exports;}
const activity=await bundle('src/hooks/use-runtime-turn-activity.ts');
const continuation=await bundle('src/hooks/use-runtime-turn-continuation.ts');
const identity={sessionId:'session',clientTurnId:'cid',turnId:'backend'};
const stream={id:'runtime-activity:cid',type:'streaming',clientTurnId:'cid',phase:'verifying',text:'',timeline:[{id:'progress',kind:'assistant',payload:{summary:'accepted progress'}}]};
const base={...identity,ok:true,observe:{},plan:{nextStep:'done'}};
for(const status of ['completed','blocked','failed','cancelled']){
 const response={...base,status,ok:status==='completed',plan:{nextStep:status==='blocked'?'needs_user_action':status,completionGate:{status:'verification_required',reason:'original reason'}}};
 const state={...identity,ok:true,status:'completed',response};
 const recovered=continuation.responseFromRuntimeTurnEvent(activity.runtimeEventFromExactResponse(state,identity));
 assert.equal(recovered.status,status);assert.equal(recovered.ok,response.ok);assert.deepEqual(recovered.plan.completionGate,response.plan.completionGate);
 const chat={id:'owner',sessionId:'session',items:[stream,{id:'reply',type:'agent',response}, {...stream,id:'runtime-activity:new',clientTurnId:'new'}]};
 const settled=activity.settleRuntimeTurnActivity(chat,identity);
 assert.equal(settled.items.some(i=>i.type==='streaming'&&i.clientTurnId==='cid'),false);
 assert.equal(settled.items.some(i=>i.type==='streaming'&&i.clientTurnId==='new'),true);
 assert.equal(settled.items.find(i=>i.type==='agent').timeline[0].id,'progress');
 const foreign={...chat,sessionId:'other'};assert.equal(activity.settleRuntimeTurnActivity(foreign,identity),foreign);
 const local={...chat,items:[{...stream,id:'stream-cid'},chat.items[1]]};assert.equal(activity.settleRuntimeTurnActivity(local,identity),local);
}
const waiting={id:'owner',sessionId:'session',items:[stream]};assert.equal(activity.settleRuntimeTurnActivity(waiting,identity),waiting,'no receipt means no fabricated termination');
if(process.env.VRCFORGE_TERMINAL_RESPONSE_REPLAY){const state=JSON.parse(await readFile(process.env.VRCFORGE_TERMINAL_RESPONSE_REPLAY,'utf8'));const r=state.response;const id={sessionId:r.sessionId,clientTurnId:r.clientTurnId,turnId:r.turnId};const e=activity.runtimeEventFromExactResponse(state,id);assert.ok(e);const result=continuation.responseFromRuntimeTurnEvent(e);assert.deepEqual(result.plan.completionGate,r.plan.completionGate);assert.equal(result.plan.nextStep,r.plan.nextStep);assert.equal(result.plan.reply,r.plan.reply);console.log('PASS original terminal response replay');}
console.log('PASS terminal lifecycle: completed/blocked/failed/cancelled, exact ownership, preserved history, live questions/local sends');
