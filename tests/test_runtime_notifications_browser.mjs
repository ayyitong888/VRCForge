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
    chatForSessionId:id=>window.fixture.chats?.[id],
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
  const childRuns = process.env.VRCFORGE_CHILD_NOTIFICATION_REPLAY
    ? JSON.parse(await readFile(process.env.VRCFORGE_CHILD_NOTIFICATION_REPLAY, 'utf8'))
    : [{...run, agent:'sub-agent',status:'failed'},{...run, agent:'sub-agent',status:'completed'}];
  const pending = await open({sessionId:identity.sessionId, snapshot:{runs:{runs:childRuns}}});
  await pending.waitForFunction(() => window.calls.some(call => call.ledger));
  const final = {...run, agent:'desktop-agent', continuationSource:'sub_agent_finished', status:'completed'};
  await pending.evaluate(parent => {
    window.fixture.snapshot.runs.runs.push(parent);
    window.tick();
  }, final);
  await pending.waitForFunction(() => window.calls.some(c => c.command === 'show_background_goal_notification'));
  assert.deepEqual(await pending.evaluate(() => window.calls.filter(c => c.command).map(c => c.args.request.title)),
    ['workspace.notificationCompletedTitle'], 'the main turn alone notifies after the child receipts and a second poll');
  assert.equal(await pending.evaluate(() => window.calls.filter(c => c.sound).length), 1);
  await pending.close();
  // A parent can ask for a decision while it continues other work.
  const asyncQuestion = process.env.VRCFORGE_ASYNC_QUESTION_REPLAY
    ? JSON.parse(await readFile(process.env.VRCFORGE_ASYNC_QUESTION_REPLAY, 'utf8'))
    : {questionId:'async-decision', sessionId:identity.sessionId, kind:'decision', question:'Keep the existing outfit?',
      status:'pending', blocking:false, needsUserAction:true, notifyOnCreate:true};
  const suppressedQuestion = {...asyncQuestion, questionId:'internal', notifyOnCreate:false};
  const ongoing = await open({sessionId:asyncQuestion.sessionId, chats:{[asyncQuestion.sessionId]:{id:'parent-chat'}},
    snapshot:{questions:{questions:[asyncQuestion,suppressedQuestion]},
      runs:{runs:[{...identity, sessionId:asyncQuestion.sessionId, agent:'desktop-agent', status:'running'}]}}});
  await ongoing.waitForFunction(id => window.state.agentQuestions.some(q => q.questionId === id), asyncQuestion.questionId);
  await ongoing.evaluate(() => window.state.refreshRuntimeRuns());
  const actual = await ongoing.evaluate(() => window.calls.filter(c => c.command || c.sound));
  assert.deepEqual(actual.filter(c => c.command).map(c => [c.command,c.args.request.chatId,c.args.request.questionId]),
    [['show_agent_question_notification','parent-chat',asyncQuestion.questionId]]);
  assert.equal(actual.find(c => c.command).args.request.body, asyncQuestion.question.replace(/\s+/g, ' ').trim());
  assert.deepEqual(actual.filter(c => c.sound).map(c => c.sound[1]), ['ordinary']);
  await ongoing.close();
  // A pending call_user question and its terminal run arrive through independent observers.
  const questionReplay = process.env.VRCFORGE_BLOCKED_QUESTION_REPLAY
    ? JSON.parse(await readFile(process.env.VRCFORGE_BLOCKED_QUESTION_REPLAY, 'utf8'))
    : { run: {...run, notificationQuestionId:'decision'}, question: {
      sessionId:identity.sessionId, originTurnId:identity.turnId, originClientTurnId:identity.clientTurnId,
      questionId:'decision', status:'pending', sourceTool:'call_user', notifyOnCreate:true, needsUserAction:true, question:'Which project?',
    }};
  for (const ledgerFirst of [true, false]) {
    const {run:blocked, question} = questionReplay;
    const both = await open({sessionId:blocked.sessionId, selectedSessionId:ledgerFirst ? blocked.sessionId : 'other',
      chats:{[blocked.sessionId]:{id:'owner-chat'}},
      snapshot:{runs:{runs:ledgerFirst ? [blocked] : []}}});
    await both.waitForFunction(() => window.calls.some(c => c.ledger));
    if (ledgerFirst) await both.waitForFunction(() => window.state.runtimeAttention.length === 1);
    else {
      await both.evaluate(question => window.state.notifyPendingAgentQuestions([question]), question);
      await both.waitForFunction(() => window.calls.some(c => c.command === 'show_agent_question_notification'));
    }
    await both.evaluate(({blocked, question}) => {
      window.fixture.snapshot.runs.runs = [blocked];
      window.state.notifyPendingAgentQuestions([question]);
      window.tick();
    }, {blocked, question});
    await both.waitForFunction(() => window.calls.some(c => c.command === 'show_agent_question_notification')
      && window.calls.filter(c => c.ledger).length >= 2);
    await both.evaluate(() => window.tick());
    const deliveries = await both.evaluate(() => window.calls.filter(c => c.command || c.sound));
    assert.deepEqual(deliveries.filter(c => c.command).map(c => c.command), ['show_agent_question_notification'],
      'the clickable question owns the only toast in either arrival order');
    const request = deliveries.find(c => c.command).args.request;
    assert.equal(request.questionId, question.questionId);
    assert.equal(request.chatId, 'owner-chat');
    assert.deepEqual(deliveries.filter(c => c.sound).map(c => c.sound[1]), ['blocking']);
    await both.evaluate(question => window.state.notifyPendingAgentQuestions([
      question, {...question, questionId:'second-decision'},
    ]), question);
    await both.waitForFunction(() => window.calls.filter(c => c.command === 'show_agent_question_notification').length === 2);
    assert.equal(await both.evaluate(() => window.calls.filter(c => c.sound).length), 2, 'a new question in the same chat sounds once');
    await both.close();
  }
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
  const detailed=await open({sessionId:'elsewhere',chats:{session:{id:'owner-chat',title:'工程依赖检查'},elsewhere:{id:'foreign-chat',title:'另一个对话'}},
    snapshot:{runs:{runs:[{...identity,status:'completed',notificationPreview:'**VRCFury** 已安装。\n\n已读取版本。'},
      {...identity,turnId:'different-backend',clientTurnId:'different-client',sessionId:'missing-chat',status:'failed',messageSummary:'检查包配置',notificationPreview:'无法读取包清单。'}]}}});
  await detailed.waitForFunction(()=>window.calls.filter(c=>c.command==='show_background_goal_notification').length===2);
  assert.deepEqual(await detailed.evaluate(()=>window.calls.filter(c=>c.command==='show_background_goal_notification').map(c=>c.args.request).sort((a,b)=>a.title.localeCompare(b.title))),
    [{title:'工程依赖检查',body:'VRCFury 已安装。 已读取版本。',silent:true},{title:'检查包配置',body:'workspace.notificationFailedTitle · 无法读取包清单。',silent:true}]);
  await detailed.close();
  const manual = process.env.VRCFORGE_MANUAL_STOP_REPLAY
    ? JSON.parse(await readFile(process.env.VRCFORGE_MANUAL_STOP_REPLAY,'utf8'))
    : {...identity,status:'cancelled',cancellationReason:'user_stop'};
  // A fresh window has never seen the click. The durable reason still suppresses both deliveries.
  const reopened = await open({sessionId:manual.sessionId,snapshot:{runs:{runs:[manual,{
    ...identity,clientTurnId:'independent-failure',turnId:'failure',status:'failed',
  }]}},receipt:{ok:true,status:'missing'}});
  await reopened.waitForFunction(()=>window.calls.some(c=>c.command==='show_background_goal_notification'));
  const stoppedCalls = await reopened.evaluate(()=>window.calls);
  assert.deepEqual(stoppedCalls.filter(c=>c.command==='show_background_goal_notification').map(c=>c.args.request.title),
    ['workspace.notificationFailedTitle']);
  assert.equal(stoppedCalls.filter(c=>c.sound).length,1,'only the independent failure plays a sound');
  await reopened.close();
  const empty=await open({sessionId:identity.sessionId,selectedSessionId:'',snapshot:{runs:{runs:[run]}},receipt:{...identity,ok:true,status:'missing'}});
  await empty.waitForFunction(()=>window.calls.some(c=>c.command==='show_background_goal_notification'));
  assert.equal(await empty.evaluate(()=>window.state.runtimeAttention.length),0,'empty new-task view still notifies without a sidebar review for another chat');
  await empty.evaluate(()=>window.selectSession('session'));
  await empty.waitForFunction(()=>window.state.runtimeAttention.length===1);
  await empty.evaluate(()=>{window.selectSession('other-chat');window.tick();});
  await empty.waitForFunction(()=>window.state.runtimeAttention.length===0);
  assert.equal(await empty.evaluate(()=>window.calls.filter(c=>c.command==='show_background_goal_notification').length),1,'scope switching does not duplicate app-wide notification');
  assert.deepEqual(
    await empty.evaluate(()=>window.calls.find(c=>c.ledger)?.ledger[1]),
    {limit:200,scope:'notifications'},
    'notification polls the app-wide lifecycle projection',
  );
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
