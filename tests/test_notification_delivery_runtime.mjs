import assert from "node:assert/strict";
import { build } from "esbuild";
import { mkdtemp, rm, writeFile } from "node:fs/promises";
import path from "node:path";
import { pathToFileURL } from "node:url";

const directory = await mkdtemp(path.resolve("local-review/notification-runtime-test-"));
try {
  const bundle = await build({stdin:{resolveDir:process.cwd(), contents:`
    export {showApprovalNotification, showSubAgentReviewNotification} from './src/lib/approval-notifications';
    export {notifyBackgroundGoal, backgroundGoalNotificationIsBlocking} from './src/lib/background-goal-notifications';
    export {saveAgentQuestionNotificationSound} from './src/lib/agent-question-notification-sound';
  `}, bundle:true, write:false, format:"esm", platform:"node"});
  const file = path.join(directory,"runtime.mjs");
  await writeFile(file,bundle.outputFiles[0].text);
  const storage = new Map(), requests = [];
  let plays = 0, rejectToast = false;
  globalThis.window = {
    localStorage: {getItem:k=>storage.get(k)??null,setItem:(k,v)=>storage.set(k,v)},
    __TAURI_INTERNALS__: {invoke:async (command,args)=>{
      requests.push({command,...args});
      if(rejectToast) throw new Error("native unavailable");
    }},
  };
  globalThis.Audio = class { play(){ plays++;return Promise.resolve(); } pause(){} };
  const api = await import(pathToFileURL(file));
  api.saveAgentQuestionNotificationSound({enabled:true,dataUrl:"data:audio/wav;base64,AAAA",name:"test.wav"},"ordinary");
  const senders = [
    ()=>api.showApprovalNotification({id:"approval-retry"},"title","body"),
    ()=>api.showSubAgentReviewNotification({taskId:"review-retry",revision:1,parentChatId:"chat",title:"title",body:"body",openLabel:"open"}),
    ()=>api.notifyBackgroundGoal({status:"blocked_question"},true,k=>k,"goal-retry:1"),
  ];
  for(const send of senders){
    const before=plays;
    rejectToast=true;await send().catch(()=>{});
    rejectToast=false;await send();
    assert.equal(plays,before+1,"retry must not replay custom sound");
    assert.equal(requests.at(-1).request.silent,true);
  }
  assert.equal(api.backgroundGoalNotificationIsBlocking({status:"failed"}),false);
  assert.equal(api.backgroundGoalNotificationIsBlocking({status:"blocked_approval"}),true);
  assert.equal(api.backgroundGoalNotificationIsBlocking({status:"blocked",blockedKind:"question"}),true);
  console.log("PASS: real approval/review/goal calls retry silently after native failure; classification is based on waiting for user.");
} finally {
  await rm(directory,{recursive:true,force:true});
}
