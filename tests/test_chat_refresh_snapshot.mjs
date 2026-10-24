import assert from 'node:assert/strict';
import { build } from 'esbuild';
const compiled = await build({ stdin: { contents: `export * from './src/lib/chat-refresh-snapshot'; export * from './src/lib/chat-storage-reconcile';`, resolveDir: process.cwd(), loader: 'ts' }, bundle: true, write: false, format: 'esm', platform: 'node' });
const { expandChatRefresh, snapshotChatFingerprints, reconcileChatStorage } = await import(`data:text/javascript;base64,${Buffer.from(compiled.outputFiles[0].text).toString('base64')}`);
const a = { id: 'a', title: 'original', items: [{ id: '1', type: 'user', text: 'keep' }] };
const b = { id: 'b', title: 'other', items: [] };
const baseline = snapshotChatFingerprints([a, b]);
const known = { a: 'a'.repeat(64), b: 'b'.repeat(64) };
const remote = { ...b, title: 'remote' };
const payload = { incremental: true, count: 2, chats: [remote], chatManifest: { schema: 'vrcforge.chat_manifest.v1', complete: true, ids: ['a', 'b'], scopeDigest: 'f'.repeat(64) }, chatDigests: { ...known, b: 'c'.repeat(64) }, chatMetadataShapes: { a: {id:true}, b: {id:true} } };
const restored = expandChatRefresh(payload, baseline, known);
assert.equal(restored[0], baseline.a, 'unchanged body reuses acknowledged snapshot');
assert.equal(restored[1], remote);
const dirty = { ...a, title: 'unsaved' };
const result = reconcileChatStorage(baseline, [dirty, b], restored, undefined, {baseline:known, remote:payload.chatDigests});
assert.equal(result.status, 'merged');
assert.equal(result.chats[0].title, 'unsaved');
assert.equal(result.baselineFingerprints.a.title, 'original', 'unsaved edit is not acknowledged by a refresh');
assert.equal(result.chats[1].title, 'remote');
for (const bad of [
  {...payload, chatManifest:undefined},
  {...payload, chatManifest:{...payload.chatManifest,scopeDigest:'invalid'}},
  {...payload, chatManifest:{...payload.chatManifest,complete:false}},
  {...payload, chats:[]},
  {...payload, chats:[remote,remote]},
  {...payload, chatDigests:{...payload.chatDigests,a:'d'.repeat(64)}},
  {...payload, count:1},
]) assert.throws(()=>expandChatRefresh(bad,baseline,known),/Incomplete/);
assert.throws(()=>expandChatRefresh(payload,{},known),/Incomplete/);
const deleted = {...payload,count:1,chats:[],chatManifest:{...payload.chatManifest,ids:['a']},chatDigests:{a:known.a},chatMetadataShapes:{a:{id:true}}};
assert.deepEqual(expandChatRefresh(deleted,baseline,known).map(c=>c.id),['a']);
assert.deepEqual(expandChatRefresh({chats:[a]},baseline,known),[a], 'full snapshots remain supported');
console.log('PASS: incremental refresh retains dirty content, reuses acknowledged data, validates manifest and deletion');
