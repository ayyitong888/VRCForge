import assert from 'node:assert/strict';
import test from 'node:test';
import { readFile } from 'node:fs/promises';
import { fileURLToPath } from 'node:url';
import { dirname, resolve } from 'node:path';
import { build } from 'esbuild';

const root = resolve(dirname(fileURLToPath(import.meta.url)), '..');
const bundled = await build({
  stdin: { contents: 'export { buildChatHistory, visibleAgentDialogueText } from "./src/lib/conversation-utils.ts";', resolveDir: root, loader: 'ts' },
  bundle: true, write: false, platform: 'node', format: 'esm',
});
const { buildChatHistory, visibleAgentDialogueText } = await import(`data:text/javascript;base64,${Buffer.from(bundled.outputFiles[0].contents).toString('base64')}`);
const event = (id, sequence, phase, text) => ({ id, sequence, timestamp: '2026-01-01T00:00:00Z', kind: 'assistant', payload: { phase, summary: text, status: 'done' } });
const history = response => buildChatHistory([{ id: 'turn', type: 'agent', response }], key => key);

test('history preserves the visible explicit blocked final after completion guard changes reply', () => {
  const response = { plan: { reply: 'Completion unverified.', summary: 'Generic guard.', completionClaim: { satisfied: false } }, timeline: [event('progress', 0, 'commentary', 'Inspecting controls.'), event('final', 1, 'final_answer', 'Blocked by stale scan evidence. No project writes were made.')] };
  const original = structuredClone(response);
  assert.deepEqual(history(response), [{ role: 'agent', text: 'Blocked by stale scan evidence. No project writes were made.' }]);
  assert.deepEqual(response, original, 'history projection must not change the completion gate or stored evidence');
});

test('uses timeline sequence and explicit final phase, never a later commentary paragraph', () => {
  const response = { plan: { reply: 'fallback' }, timeline: [event('comment', 3, 'commentary', 'Later progress.'), event('final2', 2, 'final_answer', 'Second final.'), event('final1', 1, 'final_answer', 'First final.')] };
  assert.equal(visibleAgentDialogueText(response), 'Second final.');
});

test('public messages supply the explicit final when no timeline final exists', () => {
  const response = { plan: { reply: 'guard', publicMessages: [{ id: 'a', phase: 'commentary', text: 'Working.' }, { id: 'b', phase: 'final_answer', text: 'Exact public final.' }] } };
  assert.equal(visibleAgentDialogueText(response), 'Exact public final.');
});

test('timeline final follows the visible projection before an unprojected public message', () => {
  const response = { plan: { reply: 'guard', publicMessages: [{ phase: 'final_answer', text: 'Unprojected final.' }] }, timeline: [event('final', 0, 'final_answer', 'Visible final.')] };
  assert.equal(visibleAgentDialogueText(response), 'Visible final.');
});

test('phase-tagged commentary alone is not promoted into history', () => {
  assert.deepEqual(history({ plan: { reply: 'Duplicated progress.' }, timeline: [event('comment', 0, 'commentary', 'Duplicated progress.')] }), []);
  assert.deepEqual(history({ plan: { reply: 'Duplicated progress.', publicMessages: [{ phase: 'commentary', text: 'Duplicated progress.' }] } }), []);
});

test('structured commentary followed by unphased status-done final is retained', () => {
  const response = {
    plan: { reply: 'fallback should not win' },
    timeline: [
      event('comment', 0, 'commentary', 'Inspecting the current state.'),
      { id: 'final-done', sequence: 1, timestamp: '2026-01-01T00:00:01Z', kind: 'assistant', payload: { summary: 'Actual terminal answer.', status: 'done' } },
    ],
  };
  assert.deepEqual(history(response), [{ role: 'agent', text: 'Actual terminal answer.' }]);
});

test('legacy protocols without recognized phases keep reply then summary fallback', () => {
  assert.equal(visibleAgentDialogueText({ plan: { reply: 'Legacy reply.', summary: 'Legacy summary.' }, timeline: [event('old', 0, undefined, 'Old timeline.')] }), 'Legacy reply.');
  assert.equal(visibleAgentDialogueText({ plan: { summary: 'Legacy summary.' } }), 'Legacy summary.');
});

const fixturePath = process.env.VRCFORGE_HISTORY_RECEIPT_FIXTURE;
test('unaccepted original stays out of copy/history until the accepted control final', () => {
  const original = event('proposed', 0, 'final_answer', 'Original proposed final.');
  original.payload.status = 'completion_unverified';
  const response = { plan: { reply: 'Original proposed final.', publicMessages: [{ phase: 'final_answer', text: 'Original proposed final.' }] }, timeline: [original] };
  assert.equal(visibleAgentDialogueText(response), '');
  assert.deepEqual(history(response), []);
  response.timeline.push(event('accepted', 1, undefined, 'Accepted final.'));
  assert.equal(visibleAgentDialogueText(response), 'Accepted final.');
  assert.equal(response.timeline[0].payload.phase, 'final_answer');
  assert.equal(response.timeline[0].payload.summary, 'Original proposed final.');
  response.timeline[1].payload.status = 'cancelled';
  response.timeline[1].payload.summary = 'Request cancelled.';
  assert.equal(visibleAgentDialogueText(response), 'Request cancelled.');
});
test('actual local receipt keeps the accepted final and excludes the earlier unaccepted proposal', { skip: !fixturePath }, async () => {
  const report = JSON.parse(await readFile(fixturePath, 'utf8'));
  const response = report.receipt || report;
  const original = structuredClone(response);
  const proposed = response.timeline.find(entry => entry.kind === 'assistant'
    && entry.payload?.phase === 'final_answer' && entry.payload?.status === 'completion_unverified');
  const accepted = response.timeline.find(entry => entry.kind === 'assistant'
    && entry.payload?.status === 'done' && entry.sequence > proposed?.sequence);
  assert.ok(proposed, 'fixture must retain the original unaccepted final');
  assert.ok(accepted, 'fixture must retain the later accepted final');
  assert.notEqual(proposed.payload.summary, accepted.payload.summary);
  assert.deepEqual(history(response), [{ role: 'agent', text: accepted.payload.summary.trim() }]);
  // Replay the recorded timeline prefix through the production projection.
  // This is an offline intermediate-state replay, not another live receipt.
  const beforeAcceptance = { ...response,
    timeline: response.timeline.filter(entry => entry.sequence <= proposed.sequence) };
  assert.deepEqual(history(beforeAcceptance), [], 'a stale plan reply cannot promote the unaccepted final');
  assert.deepEqual(response, original, 'replay leaves the raw receipt unchanged');
});

for (const status of ['tool_failed', 'needs_user_action', 'planner_failed', 'loop_suppressed']) {
  test(`runtime terminal ${status} preserves the actual reply after commentary`, () => {
    const response = {plan: {reply: 'The tool failed; the outfit remains unfinished.', nextStep: status}, timeline: [
      event('comment', 0, 'commentary', 'Checking the outfit.'),
      {id:'terminal',sequence:1,timestamp:'2026-01-01T00:00:01Z',kind:'assistant',payload:{summary:'The tool failed; the outfit remains unfinished.',status}},
    ]};
    assert.equal(visibleAgentDialogueText(response), response.plan.reply);
  });
}

const runtimeEvent = (id, sequence, kind, payload) => ({ id, sequence, timestamp: '2026-01-01T00:00:00Z', kind, payload });
const cancelledWorkTimeline = [
  event('comment', 0, 'commentary', '已读取 fixture，正在核对调参块。'),
  runtimeEvent('call-1', 1, 'tool_call', { actionId: 'action-1', tool: 'vrcforge_read_text_file', status: 'dispatched' }),
  runtimeEvent('result-1', 2, 'tool_result', { actionId: 'action-1', tool: 'vrcforge_read_text_file', status: 'ok', summary: 'read fixture' }),
];
const cancelledResponse = {
  plan: { summary: 'Runtime turn was cancelled by the user.', reply: 'Request cancelled.', nextStep: 'cancelled' },
  timeline: cancelledWorkTimeline,
};

test('cancelled turn history keeps the marker, model words, and completed work instead of the placeholder', () => {
  const entries = history(cancelledResponse);
  assert.equal(entries.length, 1);
  assert.equal(entries[0].role, 'agent');
  assert.match(entries[0].text, /Runtime turn was cancelled by the user\./);
  assert.match(entries[0].text, /已读取 fixture，正在核对调参块。/);
  assert.match(entries[0].text, /read_text_file \(ok\)/);
  assert.ok(!entries[0].text.includes('Request cancelled.'), 'the content-free placeholder must not reach the next model turn');
});

test('cancelled turn never promotes an unaccepted final but still keeps the completed work', () => {
  const unaccepted = event('proposed', 0, 'final_answer', 'Unaccepted proposed final.');
  unaccepted.payload.status = 'completion_unverified';
  const response = { ...cancelledResponse, timeline: [
    unaccepted,
    runtimeEvent('call-1', 1, 'tool_call', { actionId: 'action-1', tool: 'vrcforge_read_text_file', status: 'dispatched' }),
    runtimeEvent('result-1', 2, 'tool_result', { actionId: 'action-1', tool: 'vrcforge_read_text_file', status: 'ok', summary: 'read fixture' }),
  ] };
  const text = visibleAgentDialogueText(response);
  assert.ok(!text.includes('Unaccepted proposed final.'));
  assert.match(text, /Runtime turn was cancelled by the user\./);
  assert.match(text, /read_text_file \(ok\)/);
});

test('a turn that finished its accepted final before cancellation keeps that final answer', () => {
  const response = { ...cancelledResponse, timeline: [
    ...cancelledWorkTimeline,
    event('final', 3, 'final_answer', '检查完成，调参块保持原样。'),
  ] };
  assert.equal(visibleAgentDialogueText(response), '检查完成，调参块保持原样。');
});

test('frontend-aborted cancelled turns keep partial dialogue as the reply', () => {
  const response = { plan: { summary: '已取消', reply: '已定位到调参块，准备写入。', nextStep: 'cancelled' }, timeline: [] };
  const text = visibleAgentDialogueText(response);
  assert.match(text, /已取消/);
  assert.match(text, /已定位到调参块，准备写入。/);
});
