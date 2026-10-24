import assert from 'node:assert/strict';
import { build } from 'esbuild';
import { mkdtemp, writeFile, rm, readFile } from 'node:fs/promises';
import path from 'node:path';
import { pathToFileURL } from 'node:url';

const directory = await mkdtemp(path.resolve('local-review/runtime-attention-test-'));
try {
  const bundle = await build({ entryPoints: ['src/lib/runtime-attention.ts'], bundle: true, write: false, format: 'esm', platform: 'node' });
  const file = path.join(directory, 'runtime.mjs');
  await writeFile(file, bundle.outputFiles[0].text);
  const storage = new Map(), deliveries = [];
  globalThis.window = { localStorage: { getItem: k => storage.get(k) ?? null, setItem: (k,v) => storage.set(k,v) }, __TAURI_INTERNALS__: { invoke: async (command,args) => deliveries.push({command,args}) } };
  const sounds = [];
  storage.set('vrcforge.agent-question.notification-sound.v1',JSON.stringify({
    blocking:{enabled:true,dataUrl:'data:audio/mp3;base64,YmxvY2tpbmc=',name:'blocking'},
    ordinary:{enabled:true,dataUrl:'data:audio/mp3;base64,b3JkaW5hcnk=',name:'ordinary'},
  }));
  globalThis.Audio=class { constructor(src) {this.src=src;} play(){sounds.push(this.src); return Promise.resolve();} pause(){} };
  const api = await import(pathToFileURL(file));
  // Sanitized original terminal data: visual audit tool_result followed by final_answer.
  let run = { sessionId: 'session', clientTurnId: 'turn', turnId: 'backend-turn', status: 'blocked', startedAt: '2026-10-24T08:10:03.290772+00:00', updatedAt: '2026-10-24T08:47:16.321553+00:00' };
  let response = { sessionId: run.sessionId, clientTurnId: run.clientTurnId, turnId: run.turnId, plan: { nextStep: 'needs_user_action', reply: 'The visual audit did not pass its required checks: visualVerified.', completionGate: {status:'needs_user_action',reason:'verification_required'} } };
  // Optional direct local evidence replay; no credential/native reasoning files are read.
  if (process.env.VRCFORGE_ATTENTION_REPLAY) {
    const raw = JSON.parse(await readFile(process.env.VRCFORGE_ATTENTION_REPLAY, 'utf8'));
    run = raw.run;
    const final = raw.events.find(e => e.kind === 'assistant' && e.payload?.phase === 'final_answer');
    assert.equal(final.payload.status, 'needs_user_action');
    assert.match(final.payload.summary, /visualVerified/);
    response = {...response, sessionId:run.sessionId,clientTurnId:run.clientTurnId,turnId:run.turnId,plan:{...response.plan,reply:final.payload.summary}};
  }
  assert.deepEqual(api.latestRuntimeAttentionCandidates([run]), [run]);
  const attention = api.runtimeAttentionFromResponse(run, response);
  assert.ok(attention);
  const failedRun={...run,status:'failed'};
  const failedResponse={...response,plan:{completionGate:{status:'failed',reason:'verification_failed'}}};
  assert.deepEqual(api.latestRuntimeAttentionCandidates([failedRun]),[failedRun]);
  assert.ok(api.runtimeAttentionFromResponse(failedRun,failedResponse),'terminal verification failure requires attention');
  assert.equal(api.runtimeAttentionFromResponse({...failedRun,status:'running'},failedResponse),null,'intermediate failed checks do not notify');
  assert.equal(api.runtimeAttentionFromResponse(failedRun,{...failedResponse,plan:{completionGate:{status:'failed',reason:'tool_failed'}}}).status,'failed','all final failures notify ordinarily');

  assert.equal(api.runtimeAttentionFromResponse(run, {...response,clientTurnId:'wrong'}), null);
  assert.equal(api.runtimeAttentionFromResponse(run, {...response,plan:{nextStep:'needs_user_action',completionGate:{status:'needs_user_action',reason:'question_pending'}}}).status, 'blocked');
  assert.equal(api.latestRuntimeAttentionCandidates([{...run,status:'running'}]).length, 0);
  for (const status of ['running','completed','blocked']) {
    const newer = {...run,status,clientTurnId:'new',startedAt:'2026-10-24T09:00:00Z'};
    assert.equal(api.latestRuntimeAttentionCandidates([run,newer]).some(r => r.clientTurnId===run.clientTurnId),false);
  }
  for (const status of ['completed','blocked','failed','cancelled','stopped','denied']) {
    const terminalRun={...run,status};
    assert.deepEqual(api.latestRuntimeAttentionCandidates([terminalRun]),[terminalRun]);
    assert.equal(api.runtimeAttentionFromResponse(terminalRun,response)?.status,status==='denied'?'failed':status);
  }
  if (process.env.VRCFORGE_ATTENTION_RECEIPT && process.env.VRCFORGE_ATTENTION_SNAPSHOT) {
    const receipt=JSON.parse(await readFile(process.env.VRCFORGE_ATTENTION_RECEIPT,'utf8'));
    const snapshot=JSON.parse(await readFile(process.env.VRCFORGE_ATTENTION_SNAPSHOT,'utf8'));
    const actualRun=snapshot.runs.runs.find(row=>row.clientTurnId===receipt.clientTurnId && row.sessionId===receipt.sessionId);
    assert.ok(actualRun);
    assert.equal(api.runtimeAttentionFromTurnState(actualRun,receipt)?.status,'failed','actual stopped visual audit is ordinary failure');
  }
  const failedState={ok:true,sessionId:run.sessionId,clientTurnId:run.clientTurnId,status:'failed',error:'Provider connection lost'};
  for (const status of ['failed','cancelled','stopped','denied']) {
    assert.equal(api.runtimeAttentionFromTurnState({...run,status},failedState)?.status,status==='denied'?'failed':status);
  }
  for (const status of ['running','missing']) assert.equal(api.runtimeAttentionFromTurnState(failedRun,{...failedState,status}),null);
  assert.equal(api.runtimeAttentionFromTurnState(failedRun,{...failedState,clientTurnId:'other'}),null);
  assert.equal(api.runtimeAttentionFromTurnState(failedRun,{...failedState,ok:false}),null);
  assert.equal(api.runtimeAttentionFromTurnState(failedRun,{...failedState,response:{...response,turnId:'other'}}),null);
  assert.equal(api.runtimeAttentionFromTurnState({...run,status:'completed'},failedState),null);
  assert.equal(api.runtimeAttentionFromTurnState(run,failedState),null,'missing response cannot establish human blockage');
  const olderFinished={...run,status:'completed'};
  const newerRunning={...run,status:'running',clientTurnId:'newer',turnId:'newer-backend',startedAt:'2026-10-25T00:00:00Z'};
  assert.deepEqual(api.runtimeAttentionCandidates([olderFinished,newerRunning]),[olderFinished],'a newer running turn cannot hide a watched terminal notification');
  assert.deepEqual(api.runtimeAttentionCandidates([{...olderFinished,turnId:undefined}]),[],'anonymous events are not round notifications');
  let valid = true;
  const delivery = api.createRuntimeAttentionDelivery();
  await Promise.all([delivery('endpoint', attention, 'title', 'body', () => valid), delivery('endpoint', attention, 'title', 'body', () => valid)]);
  assert.equal(deliveries.length, 1, 'one native toast for concurrent local/external observations');
  assert.equal(deliveries[0].command,'show_background_goal_notification');
  assert.equal(sounds[0],'data:audio/mp3;base64,YmxvY2tpbmc=','only blocked uses critical sound');
  assert.equal('questionId' in deliveries[0].args.request,false);
  await api.createRuntimeAttentionDelivery()('endpoint',attention,'title','body',() => valid);
  assert.equal(deliveries.length,1,'durable duplicate suppression after reconnect');
  const completedAttention=api.runtimeAttentionFromResponse({...run,status:'completed'},response);
  await delivery('endpoint',completedAttention,'completed','body',()=>true);
  assert.equal(deliveries.length,2,'blocked then completed notifies both transitions');
  assert.equal(sounds[1],'data:audio/mp3;base64,b3JkaW5hcnk=','completed uses ordinary sound');
  await api.createRuntimeAttentionDelivery()('endpoint',completedAttention,'completed','body',()=>true);
  assert.equal(deliveries.length,2,'terminal notification deduplicates after reconnect');
  valid = false;
  await delivery('other-endpoint',attention,'title','body',() => valid);
  assert.equal(deliveries.length,2,'stale async read must not notify');
  valid=true;
  for (const status of ['failed','cancelled','stopped']) {
    await delivery('endpoint',api.runtimeAttentionFromResponse({...run,status},response),status,'body',()=>true);
    assert.equal(sounds.at(-1),'data:audio/mp3;base64,b3JkaW5hcnk=',`${status} uses ordinary sound`);
  }
  assert.equal(deliveries.length,5,'all terminal outcomes use native delivery');
  console.log('PASS: all terminal outcomes, critical only when blocked, exact receipt replay and status-aware durable dedup');
} finally { await rm(directory, {recursive:true,force:true}); }
