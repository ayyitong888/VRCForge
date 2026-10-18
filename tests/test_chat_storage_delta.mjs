import assert from 'node:assert/strict';
import { build } from 'esbuild';

const bundle = await build({
  entryPoints: ['src/lib/chat-storage-delta.ts'],
  bundle: true,
  write: false,
  format: 'esm',
  platform: 'node',
});
const { buildChatStoragePatches } = await import(
  `data:text/javascript,${encodeURIComponent(bundle.outputFiles[0].text)}`,
);

const digest = 'a'.repeat(64);
const shape = {
  id: true, sessionId: true, title: true, projectPath: true, projectType: true,
  revision: true, createdAt: true, updatedAt: true,
};
const toolResponse = {
  plan: { reply: 'keep the raw tool response' },
  steps: [{ id: 'step-1', status: 'completed', output: '[redacted]' }],
};
const baseline = {
  id: 'chat-1',
  sessionId: 'session-1',
  title: 'Original title',
  projectPath: '',
  projectType: 'general',
  revision: 1,
  createdAt: '2026-01-01T00:00:00Z',
  updatedAt: '2026-01-01T00:00:00Z',
  items: [
    { id: 'user-1', type: 'user', text: 'hello' },
    { id: 'tool-1', type: 'agent', response: toolResponse },
  ],
};

const reorderedWithAppend = {
  ...baseline,
  title: 'Edited title',
  items: [
    baseline.items[1],
    baseline.items[0],
    { id: 'user-2', type: 'user', text: 'tail' },
  ],
};
const patches = buildChatStoragePatches(
  [reorderedWithAppend],
  { 'chat-1': JSON.stringify(baseline) },
  { 'chat-1': digest },
  { 'chat-1': shape },
);
assert.ok(patches);
assert.deepEqual(patches['chat-1'].itemOrder, ['tool-1', 'user-1', 'user-2']);
assert.deepEqual(patches['chat-1'].newItems, { 'user-2': reorderedWithAppend.items[2] });
assert.deepEqual(patches['chat-1'].itemPatches, {});
assert.ok(patches['chat-1'].metadataPatch.some((operation) => operation.path === '/title'));
assert.ok(patches['chat-1'].metadataPatch.every((operation) => !operation.path.startsWith('/items')));
assert.equal(patches['chat-1'].baseDigest, digest);

const deleteAndEdit = {
  ...baseline,
  items: [{ ...baseline.items[0], text: 'edited text' }],
};
const deletePatches = buildChatStoragePatches(
  [deleteAndEdit],
  { 'chat-1': JSON.stringify(baseline) },
  { 'chat-1': digest },
  { 'chat-1': shape },
);
assert.deepEqual(deletePatches['chat-1'].itemOrder, ['user-1']);
assert.deepEqual(deletePatches['chat-1'].newItems, {});
assert.deepEqual(deletePatches['chat-1'].itemPatches['user-1'], [
  { op: 'replace', path: '/text', value: 'edited text' },
]);

const maskedProjectionEcho = {
  ...baseline,
  items: baseline.items.map((item) => structuredClone(item)),
};
const maskedPatches = buildChatStoragePatches(
  [maskedProjectionEcho],
  { 'chat-1': JSON.stringify(baseline) },
  { 'chat-1': digest },
  { 'chat-1': shape },
);
assert.deepEqual(maskedPatches['chat-1'].itemPatches, {},
  'an unchanged masked/tool response must remain an empty patch so raw fields survive');

const newChat = {
  ...baseline,
  id: 'chat-new',
  items: [],
};
const mixed = buildChatStoragePatches(
  [baseline, newChat],
  { 'chat-1': JSON.stringify(baseline) },
  { 'chat-1': digest },
  { 'chat-1': shape },
);
assert.ok(mixed['chat-1']);
assert.equal(mixed['chat-new'], undefined, 'new chats without a server digest use the full snapshot');

assert.throws(() => buildChatStoragePatches(
  [{ ...baseline, items: [{ ...baseline.items[0] }, { ...baseline.items[0] }] }],
  { 'chat-1': JSON.stringify(baseline) },
  { 'chat-1': digest },
  { 'chat-1': shape },
), /unique stable item IDs/);
assert.equal(buildChatStoragePatches(
  [baseline],
  { 'chat-1': JSON.stringify(baseline) },
  undefined,
), undefined, 'legacy responses without chat digests retain full-snapshot behavior');

const normalizedMissingParent = {
  ...baseline,
  contextUsageCache: { inputTokens: 12, totalTokens: 20 },
};
const synthesizedParent = buildChatStoragePatches(
  [normalizedMissingParent],
  { 'chat-1': JSON.stringify(baseline) },
  { 'chat-1': digest },
  { 'chat-1': shape },
);
assert.deepEqual(synthesizedParent['chat-1'].metadataPatch, [
  { op: 'add', path: '/contextUsageCache', value: { inputTokens: 12, totalTokens: 20 } },
], 'a normalized field absent from raw storage is added at its nearest absent parent');

const nestedBaseline = { ...baseline, contextUsageCache: { source: 'provider' } };
const nestedCurrent = { ...nestedBaseline, contextUsageCache: { source: 'provider', inputTokens: 12 } };
const nestedShape = { ...shape, contextUsageCache: { source: true } };
const nestedAdd = buildChatStoragePatches(
  [nestedCurrent],
  { 'chat-1': JSON.stringify(nestedBaseline) },
  { 'chat-1': digest },
  { 'chat-1': nestedShape },
);
assert.deepEqual(nestedAdd['chat-1'].metadataPatch, [
  { op: 'add', path: '/contextUsageCache/inputTokens', value: 12 },
], 'nested synthesized fields use add when their parent exists but the field does not');

assert.throws(() => buildChatStoragePatches(
  [baseline], { 'chat-1': JSON.stringify(baseline) }, { 'chat-1': digest }, undefined,
), /metadata shape/);

console.log('PASS chat storage deltas: metadata, item edit, reorder, append, delete, masked raw preservation');
