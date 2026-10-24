import assert from 'node:assert/strict';
import { build } from 'esbuild';
const compiled = await build({ entryPoints:['src/lib/api/notes-chats.ts'], bundle:true, write:false, format:'esm', platform:'node', plugins:[{
  name:'transport-fixtures', setup(build) {
    build.onResolve({filter:/\/http$|^\.\/http$/}, () => ({path:'http', namespace:'fixture'}));
    build.onResolve({filter:/chat-storage-origin$/}, () => ({path:'origin', namespace:'fixture'}));
    build.onLoad({filter:/.*/, namespace:'fixture'}, args => ({contents: args.path === 'origin'
      ? 'export const chatStorageOriginId=()=>"fixture-origin";'
      : 'export const hasTauriInternals=()=>globalThis.native; export const invokeTauriWithAbort=async (command,args)=>{globalThis.capture={command,args};return {};}; export const requestJson=async(url,options)=>{globalThis.capture={url,options};return {};};'}));
  }
}]});
const {fetchChats, saveChats}=await import('data:text/javascript;base64,'+Buffer.from(compiled.outputFiles[0].text).toString('base64'));
const existing={id:'old',items:[{id:'one',text:'x'.repeat(1000000)}]};
const added={id:'new',items:[{id:'two',text:'new content'}]};
const patches={old:{baseDigest:'a'.repeat(64),metadataPatch:[],itemOrder:['one'],itemPatches:{},newItems:{}}};
for (const native of [false,true]) {
  globalThis.native=native;
  await fetchChats('http://localhost:8757', ['project'], {old:'a'.repeat(64)});
  if(native) assert.deepEqual(globalThis.capture.args.request.knownChatDigests,{old:'a'.repeat(64)});
  else assert.deepEqual(JSON.parse(new URL(globalThis.capture.url).searchParams.get('knownChatDigests')),{old:'a'.repeat(64)});
  await saveChats('http://localhost:8757',[existing,added],[],patches);
  const body=native ? globalThis.capture.args.request.body : JSON.parse(globalThis.capture.options.body);
  assert.deepEqual(body.chats,[{id:'old'},added]);
  assert.deepEqual(body.chatPatches,patches);
  assert.ok(JSON.stringify(body).length<1000);
  await saveChats('http://localhost:8757',[existing],[]);
  const full=native ? globalThis.capture.args.request.body : JSON.parse(globalThis.capture.options.body);
  assert.deepEqual(full.chats,[existing]);
}
assert.equal(existing.items[0].text.length,1000000);
console.log('chat incremental fetch and patch-only save: HTTP/native passed');
