import assert from 'node:assert/strict';
import test from 'node:test';
import { build } from 'esbuild';
import { readFile, writeFile, mkdtemp, rm } from 'node:fs/promises';
import path from 'node:path';
import { pathToFileURL } from 'node:url';

const root = process.cwd();
const source = await readFile('src/hooks/use-runtime-workspace.ts', 'utf8');
const start = source.indexOf('  function notifyCallUserQuestions(');
const end = source.indexOf('  function clearScopedRuntimeProjection()', start);
assert(start > 0 && end > start);
const directory = await mkdtemp(path.resolve('local-review/notification-failure-test-'));
const bundle = await build({ stdin: { resolveDir: root, loader: 'ts', contents: `
import { isTauriRuntime } from './src/lib/app-runtime';
import { isCallUserQuestion, isBlockingAgentQuestion, saveAgentQuestionNotificationIds, shouldStartAgentQuestionNotification, normalizeAgentQuestionNotificationBody, markAgentQuestionNotificationDelivered, showAgentQuestionNotification } from './src/lib/agent-question-notifications';
import { playNotificationSoundOnce } from './src/lib/notification-sound-delivery';
export { showApprovalNotification, showSubAgentReviewNotification } from './src/lib/approval-notifications';
export { notifyBackgroundGoal } from './src/lib/background-goal-notifications';
export function questionFixture() {
 const agentQuestionNotificationIdsRef = {current:new Set<string>()}, agentQuestionNotificationInflightRef = {current:new Set<string>()}, agentQuestionNotificationStatusRef = {current:new Map<string,string>()};
 const chatIdForSessionId = () => 'chat-stub', t = (key:string) => key;
 ${source.slice(start, end)}
 return { notifyCallUserQuestions, agentQuestionNotificationInflightRef };
}
` }, bundle: true, write: false, format: 'esm', platform: 'node', plugins: [{ name: 'no-native-notification', setup(builder) {
  builder.onResolve({ filter: /^@tauri-apps\/api\/core$/ }, () => ({path:'core',namespace:'stub'}));
  builder.onLoad({filter:/.*/,namespace:'stub'}, () => ({contents:'export async function invoke(command,args){ globalThis.__notificationCalls.push({command,request:args.request}); }',loader:'js'}));
} }] });
await writeFile(path.join(directory, 'runtime.mjs'), bundle.outputFiles[0].text);
const values = new Map();
globalThis.__notificationCalls = [];
globalThis.window = {__TAURI_INTERNALS__:{},localStorage:{getItem:key=>values.get(key)??null,setItem:(key,value)=>values.set(key,value)}};
values.set('vrcforge.agent-question.notification-sound.v1',JSON.stringify({ordinary:{enabled:true,dataUrl:'data:audio/wav;base64,AAAA'},blocking:{enabled:true,dataUrl:'data:audio/wav;base64,AAAA'}}));
let mode = 'success';
const audios = [];
globalThis.Audio = class {
 constructor(){ if(mode==='constructor-throw') throw new Error('Audio setup failed'); this.mode=mode;this.paused=false;audios.push(this); }
 play(){ if(this.mode==='pending') return new Promise(()=>{}); if(this.mode==='play-reject') return Promise.reject(new Error('media rejected')); return Promise.resolve(); }
 pause(){this.paused=true;if(this.mode==='stop-throw')throw new Error('pause failed');}
 set currentTime(value){if(this.mode==='stop-throw')throw new Error('seek failed');}
};
const api = await import(pathToFileURL(path.join(directory, 'runtime.mjs')));
const fixture = api.questionFixture();
function sendAll(prefix) {
 fixture.notifyCallUserQuestions([{questionId:prefix,sourceTool:'call_user',status:'pending',sessionId:'session-stub',question:'Stub only',needsUserAction:true}]);
 return Promise.allSettled([
  api.showApprovalNotification({id:prefix},'title','body'),
  api.showSubAgentReviewNotification({taskId:prefix,revision:1,parentChatId:'chat-stub',title:'title',body:'body',openLabel:'open'}),
  api.notifyBackgroundGoal({status:'blocked_question'},true,key=>key,prefix),
 ]);
}
const settle = ms => new Promise(resolve=>setTimeout(resolve,ms));
try {
 await test('all four native notification paths survive Audio construction failure',async()=>{
  mode='constructor-throw';const before=__notificationCalls.length;await sendAll('constructor-failure');await settle(20);
  assert.equal(__notificationCalls.length-before,4);
  assert(__notificationCalls.slice(before).every(call=>call.request.silent===false));
 });
 await test('unsettled playback is stopped and cannot indefinitely block any native notification',async()=>{
  mode='pending';const before=__notificationCalls.length;void sendAll('pending-playback');await settle(1800);
  assert.equal(__notificationCalls.length-before,4);
  assert(!fixture.agentQuestionNotificationInflightRef.current.has('pending-playback'));
  assert(audios.filter(audio=>audio.mode==='pending').every(audio=>audio.paused));
  assert(__notificationCalls.slice(before).every(call=>call.request.silent===false));
 });
 await test('failed pause/seek cannot reject the next notification and successful sound stays deduplicated',async()=>{
  mode='stop-throw';await api.showApprovalNotification({id:'stop-failure-primer'},'title','body');
  mode='success';const before=__notificationCalls.length;await sendAll('stop-failure');await settle(20);
  assert.equal(__notificationCalls.length-before,4);
  const audioCount=audios.length;await api.showApprovalNotification({id:'stop-failure'},'title','body');
  assert.equal(audios.length,audioCount,'retry reuses played sound receipt');
  assert.equal(__notificationCalls.at(-1).request.silent,true);
 });
} finally { await rm(directory,{recursive:true,force:true}); }
