import assert from 'node:assert/strict';
import { build } from 'esbuild';
import { readFile } from 'node:fs/promises';
import { createServer } from 'node:http';
import { createRequire } from 'node:module';

const bundle = await build({ stdin: { resolveDir: process.cwd(), loader: 'tsx', contents: `
import React from 'react';
import {createRoot} from 'react-dom/client';
import {useRuntimeWorkspace} from './src/hooks/use-runtime-workspace';
const noop=()=>{};
function Harness(){
  const [selectedSessionId,setSelectedSessionId]=React.useState(window.fixture.selectedSessionId ?? window.fixture.sessionId);
  window.selectSession=setSelectedSessionId;
  window.state=useRuntimeWorkspace({endpoint:'fixture',runtimeConnected:true,sessionId:selectedSessionId,
    activeRuntimeProjectPath:'fixture-project',activeProjectPath:'fixture-project',sending:false,pendingApprovals:0,
    setBootstrap:noop,setAgentApprovals:noop,setError:noop});
  return <div>Runtime notification harness</div>;
}
createRoot(document.getElementById('root')).render(<Harness/>);
` }, bundle: true, write: false, platform: 'browser', format: 'iife', plugins: [{name:'runtime-fixtures',setup(b){
  if(process.env.VRCFORGE_NOTIFICATION_BASELINE_HOOK) b.onLoad({filter:/use-runtime-workspace\.ts$/},async()=>({loader:'ts',contents:await readFile(process.env.VRCFORGE_NOTIFICATION_BASELINE_HOOK,'utf8')}));
  b.onResolve({filter:/^react-i18next$/},()=>({path:'i18n',namespace:'fixture'}));
  b.onResolve({filter:/notification-sound-delivery$/},()=>({path:'sound',namespace:'fixture'}));
  b.onResolve({filter:/^\.\.\/lib\/api$/},a=>a.importer.endsWith('use-runtime-workspace.ts')?{path:'api',namespace:'fixture'}:undefined);
  b.onResolve({filter:/^\.\.\/lib\/api\/agent-runtime$/},a=>a.importer.endsWith('use-runtime-workspace.ts')?{path:'receipt',namespace:'fixture'}:undefined);
  b.onLoad({filter:/.*/,namespace:'fixture'},a=>({loader:'js',contents:a.path==='i18n'?`const t=k=>k;export const useTranslation=()=>({t});`:
    a.path==='sound'?`export const playNotificationSoundOnce=async(...args)=>{window.calls.push({sound:args});return true;};`:
    a.path==='receipt'?`export const fetchAgentRuns=async(...args)=>{window.calls.push({ledger:args});return structuredClone(window.fixture.snapshot.runs);};export const fetchAgentTurnResponse=async(...args)=>{window.calls.push({receipt:args});return structuredClone(window.fixture.receipt);};`:
    `export const fetchDesktopRuntimeSnapshot=async()=>structuredClone(window.fixture.snapshot);
     export const cancelAgentDesktopAction=async()=>({}); export const fetchActiveAgentDesktopActions=async()=>({actions:[]});
     export const fetchWorkspaceDiff=async()=>({});export const refreshUnityReadiness=async()=>({});`}));
}}]});
const server=createServer((req,res)=>{res.setHeader('Content-Type','text/html');res.end('<div id="root"></div><script>'+bundle.outputFiles[0].text.replaceAll('</script','<\\/script')+'</script>');});
await new Promise(resolve=>server.listen(0,'127.0.0.1',resolve));
const require=createRequire(import.meta.url);
const {chromium}=require(process.env.VRCFORGE_PLAYWRIGHT_PATH || 'playwright');
let browser;
try {
  browser=await chromium.launch({headless:true,channel:process.env.VRCFORGE_BROWSER_CHANNEL || 'msedge'});
  const errors=[];
  const open=async fixture=>{
    const page=await browser.newPage();
    page.on('pageerror',e=>errors.push(e.message));
    await page.addInitScript(value=>{window.fixture=value;window.calls=[];const timers=new Map();let timerId=0;window.setInterval=fn=>{timers.set(++timerId,fn);return timerId;};window.clearInterval=id=>timers.delete(id);window.tick=()=>{for(const fn of [...timers.values()])fn();};window.__TAURI_INTERNALS__={invoke:async(command,args)=>{window.calls.push({command,args});}};},fixture);
    await page.goto(`http://127.0.0.1:${server.address().port}`);
    return page;
  };
  const identity={sessionId:'session',clientTurnId:'client-turn',turnId:'backend-turn'};
  const run={...identity,status:'blocked',startedAt:'2026-01-01T00:00:00Z'};
  const response={...identity,plan:{completionGate:{status:'needs_user_action',reason:'question_pending'}}};
  const page=await open({sessionId:identity.sessionId,snapshot:{runs:{runs:[run]}},receipt:{...identity,ok:true,status:'missing'}});
  const waitToasts=async count=>page.waitForFunction(n=>window.calls.filter(c=>c.command==='show_background_goal_notification').length===n,count);
  const read=()=>page.evaluate(()=>({toasts:window.calls.filter(c=>c.command==='show_background_goal_notification'),sounds:window.calls.filter(c=>c.sound),reads:window.calls.filter(c=>c.receipt).length,attention:window.state.runtimeAttention}));
  await waitToasts(1);
  let state=await read();
  assert.equal(state.attention.length,1);
  assert.equal(state.sounds[0].sound[1],'blocking');
  assert.equal(state.toasts[0].args.request.title,'workspace.notificationBlockedTitle');

  // Same IDs, later durable lifecycle state after the response cache was lost.
  await page.evaluate(async()=>{window.fixture.snapshot.runs.runs[0].status='completed';window.tick();});
  await waitToasts(2);
  state=await read();
  assert.equal(state.reads,0,'durable lifecycle notification must not depend on volatile response cache');
  assert.equal(state.attention.length,0,'only blocked remains in review sidebar');
  assert.equal(state.sounds[1].sound[1],'ordinary');
  assert.equal(state.toasts[1].args.request.title,'workspace.notificationCompletedTitle');
  await page.evaluate(async()=>{window.tick();});
  await page.waitForTimeout(100);
  assert.equal((await read()).toasts.length,2,'polling does not redeliver terminal toast');

  let count=2;
  for (const status of ['failed','cancelled','stopped']) {
    await page.evaluate(async status=>{
      const run=window.fixture.snapshot.runs.runs[0];
      Object.assign(run,{status,clientTurnId:status,turnId:'backend-'+status});
      // Provider/transport failure can have no final model response at all.
      window.fixture.receipt={ok:true,status:'failed',sessionId:run.sessionId,clientTurnId:run.clientTurnId,error:'transport closed'};
      window.tick();
    },status);
    await waitToasts(++count);
    state=await read();
    assert.equal(state.attention.length,0,`${status} is not human blockage`);
    assert.equal(state.sounds.at(-1).sound[1],'ordinary');
    assert.equal(state.toasts.at(-1).args.request.title,`workspace.notification${status==='failed'?'Failed':'Stopped'}Title`);
  }
  await page.close();
  const empty=await open({sessionId:identity.sessionId,selectedSessionId:'',snapshot:{runs:{runs:[run]}},receipt:{...identity,ok:true,status:'missing'}});
  await empty.waitForFunction(()=>window.calls.some(c=>c.command==='show_background_goal_notification'));
  assert.equal(await empty.evaluate(()=>window.state.runtimeAttention.length),0,'empty new-task view still notifies without a sidebar review for another chat');
  await empty.evaluate(()=>window.selectSession('session'));
  await empty.waitForFunction(()=>window.state.runtimeAttention.length===1);
  await empty.evaluate(()=>{window.selectSession('other-chat');window.tick();});
  await empty.waitForFunction(()=>window.state.runtimeAttention.length===0);
  assert.equal(await empty.evaluate(()=>window.calls.filter(c=>c.command==='show_background_goal_notification').length),1,'scope switching does not duplicate app-wide notification');
  assert.ok(await empty.evaluate(()=>window.calls.some(c=>c.ledger)),'notification observes app-wide ledger');
  await empty.close();
  const multi=await open({sessionId:'',selectedSessionId:'',snapshot:{runs:{runs:[
    {sessionId:'older',clientTurnId:'older',turnId:'older',status:'blocked',startedAt:'2026-01-01T00:00:00Z'},
    {sessionId:'newer',clientTurnId:'newer',turnId:'newer',status:'completed',startedAt:'2026-01-01T01:00:00Z'},
  ]}},receipt:{ok:true,status:'missing'}});
  await multi.waitForFunction(()=>window.calls.filter(c=>c.command==='show_background_goal_notification').length===2);
  assert.deepEqual((await multi.evaluate(()=>window.calls.filter(c=>c.sound).map(c=>c.sound[1]))).sort(),['blocking','ordinary']);
  await multi.close();
  if(process.env.VRCFORGE_ATTENTION_RECEIPT && process.env.VRCFORGE_ATTENTION_SNAPSHOT){
    const receipt=JSON.parse(await readFile(process.env.VRCFORGE_ATTENTION_RECEIPT,'utf8'));
    const snapshot=JSON.parse(await readFile(process.env.VRCFORGE_ATTENTION_SNAPSHOT,'utf8'));
    const actual=snapshot.runs.runs.find(run=>run.sessionId===receipt.sessionId && run.clientTurnId===receipt.clientTurnId);
    assert.ok(actual);
    // Keep original raw run and receipt, excluding unrelated sessions/questions.
    const replay=await open({sessionId:receipt.sessionId,receipt,snapshot:{runs:{runs:[actual]}}});
    await replay.waitForFunction(()=>window.calls.some(c=>c.command==='show_background_goal_notification'));
    const observed=await replay.evaluate(()=>({toasts:window.calls.filter(c=>c.command==='show_background_goal_notification'),sounds:window.calls.filter(c=>c.sound),attention:window.state.runtimeAttention}));
    assert.equal(observed.toasts.length,1);
    assert.equal(observed.toasts[0].args.request.title,'workspace.notificationFailedTitle');
    assert.equal(observed.sounds[0].sound[1],'ordinary');
    assert.equal(observed.attention.length,0);
    await replay.close();
  }
  assert.deepEqual(errors,[]);
  console.log('PASS real workspace hook: all terminal notifications, blocked-only critical/sidebar, durable restart-safe lifecycle, no-response failure, optional original receipt replay');
} finally {
  await browser?.close();
  await new Promise(resolve=>server.close(resolve));
}
