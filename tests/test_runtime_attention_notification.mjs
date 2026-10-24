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
  assert.equal(api.runtimeAttentionFromResponse(run, {...response,clientTurnId:'wrong'}), null);
  assert.equal(api.runtimeAttentionFromResponse(run, {...response,plan:{nextStep:'needs_user_action',completionGate:{status:'needs_user_action',reason:'question_pending'}}}), null);
  assert.equal(api.latestRuntimeAttentionCandidates([{...run,status:'running'}]).length, 0);
  for (const status of ['running','completed','blocked']) {
    const newer = {...run,status,clientTurnId:'new',startedAt:'2026-10-24T09:00:00Z'};
    assert.equal(api.latestRuntimeAttentionCandidates([run,newer]).some(r => r.clientTurnId===run.clientTurnId),false);
  }
  let valid = true;
  const delivery = api.createRuntimeAttentionDelivery();
  await Promise.all([delivery('endpoint', attention, 'title', 'body', () => valid), delivery('endpoint', attention, 'title', 'body', () => valid)]);
  assert.equal(deliveries.length, 1, 'one native toast for concurrent local/external observations');
  assert.equal(deliveries[0].command,'show_background_goal_notification');
  assert.equal('questionId' in deliveries[0].args.request,false);
  await api.createRuntimeAttentionDelivery()('endpoint',attention,'title','body',() => valid);
  assert.equal(deliveries.length,1,'durable duplicate suppression after reconnect');
  valid = false;
  await delivery('other-endpoint',attention,'title','body',() => valid);
  assert.equal(deliveries.length,1,'stale async read must not notify');
  console.log('PASS: terminal verification attention, current-run authority, native delivery and durable dedup');
} finally { await rm(directory, {recursive:true,force:true}); }
