import assert from 'node:assert/strict';
import { build } from 'esbuild';
const b=await build({entryPoints:['src/lib/chat-storage-reconcile.ts'],bundle:true,write:false,format:'esm',platform:'node'});
const {reconcileChatStorage:r,snapshotChatFingerprints:f,chatDigestsFromPayload:read,preserveConflictingChatCopies:preserve}=await import('data:text/javascript;base64,'+Buffer.from(b.outputFiles[0].text).toString('base64'));
const a={id:'a',items:[],title:'a',privateNote:'original'},bChat={id:'b',items:[],title:'b'};
const base=[a,bChat],fp=f(base),versions={a:'1'.repeat(64),b:'2'.repeat(64)};
const projected={...a,privateNote:'[redacted]'};
let result=r(fp,[{...a,title:'local'},bChat],[projected,{...bChat,title:'external'}],undefined,
  {baseline:versions,remote:{...versions,b:'3'.repeat(64)}});
assert.equal(result.status,'merged');
assert.deepEqual(result.chats,[{...a,title:'local'},{...bChat,title:'external'}]);
assert.equal(result.baselineFingerprints.a,fp.a,'local edit still compares to its acknowledged raw baseline');
assert.equal(result.baselineFingerprints.b,f([{...bChat,title:'external'}]).b);
result=r(fp,base,[bChat],undefined,{baseline:versions,remote:{b:versions.b}});
assert.deepEqual(result.chats,[bChat],'clean local chat accepts an external deletion');
assert.equal(result.baselineFingerprints.a,undefined);
result=r(fp,[{...a,title:'unsaved'},bChat],[bChat],undefined,{baseline:versions,remote:{b:versions.b}});
assert.equal(result.status,'conflict','external deletion cannot discard unsaved local content');
result=r(fp,[bChat],[projected,bChat],undefined,{baseline:versions,remote:versions});
assert.deepEqual(result.chats,[bChat],'local deletion survives an unchanged projected echo');
result=r(fp,base,[projected,bChat],undefined,{baseline:versions,remote:versions});
assert.deepEqual(result.chats,base,'an unchanged storage version cannot replace accepted raw content with a display projection');
assert.equal(read(undefined,base),undefined);
assert.throws(()=>read({a:'x',b:versions.b},base),/invalid/);
assert.throws(()=>read({a:versions.a},base),/invalid/);
assert.throws(()=>read(Object.create(versions),base),/invalid/);
assert.deepEqual(read(versions,base),versions);
const hiddenRemoteChange=preserve(
  fp,
  [{...a,title:'local edit'},bChat],
  [{...a,privateNote:'[redacted]'},{...bChat}],
  (chat)=>({...chat,id:`${chat.id}-local-copy`}),
  undefined,
  {baseline:versions,remote:{...versions,a:'3'.repeat(64)}},
);
assert.deepEqual(hiddenRemoteChange.conflictIds,['a'],'opaque digest conflict survives explicit resolution');
assert.equal(hiddenRemoteChange.chats.filter((chat)=>chat.id.startsWith('a')).length,2,
  'resolution preserves the raw remote chat and a local copy');
console.log('PASS chat versions: independent edits, deletion races, unchanged projection, invalid metadata');
