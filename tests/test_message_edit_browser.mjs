import assert from "node:assert/strict";
import { readFileSync, mkdirSync } from "node:fs";
import { resolve } from "node:path";
import { createRequire } from "node:module";
import { build } from "esbuild";
import postcss from "postcss";
import tailwindcss from "tailwindcss";
const { chromium } = createRequire(import.meta.url)(process.env.VRCFORGE_PLAYWRIGHT_PATH || "playwright");
const root = process.cwd();
const baseline = process.env.VRCFORGE_MESSAGE_EDIT_BASELINE;
const app = readFileSync(resolve(baseline || root, "src/App.tsx"), "utf8");
const editHandler = app.slice(app.indexOf("  function editConversationMessage("), app.indexOf("  function cancelMessageEdit("));
const output = await build({
  stdin: { resolveDir: root, loader: "tsx", contents: `
    import React, {useState} from 'react';
    import {createRoot} from 'react-dom/client';
    import {ChatWorkspace} from './src/components/chat/chat-workspace';
    import {initReactI18next} from 'react-i18next';
    import i18n from './src/i18n';
    import messages from './src/locales/zh-CN.json';
    i18n.use(initReactI18next).init({lng:'zh-CN',resources:{'zh-CN':{translation:messages}},interpolation:{escapeValue:false}});
    const t=i18n.t.bind(i18n), noop=()=>{};
    const original={id:'user',type:'user',text:'有没有简短向的命中'};
    const chat={id:'chat',sessionId:'session',items:[original]};
    window.sent=0; window.errors=[];
    window.fetch=()=>{throw new Error('Unexpected network request');};
    function Fixture(){
      const [sending,setSending]=useState(true);
      const [editingMessage,setEditingMessage]=useState(null);
      const activeChatId='chat', input='原先未发送草稿',attachments=[],compacting=false,visibleQueued=[];
      const isChatRunActive=()=>sending,getChatById=()=>chat,cloneChatAttachments=x=>structuredClone(x);
      const latestConversationItemId=(items,predicate)=>[...items].reverse().find(predicate)?.id;
      const setRuntimeNotice=noop,setError=message=>window.errors.push(message);
      ${editHandler}
      window.finishRuntime=()=>setSending(false);
      window.originalChat=chat;
      return <main className="flex h-screen flex-col bg-workspace"><ChatWorkspace
        input={input} setInput={noop} sending={sending} queueAllowed={false} onSubmit={noop} onSwitchMode={noop}
        conversation={chat.items} latestEditableUserItemId="user" latestRetryableItemId=""
        editingItemId={editingMessage?.itemId||''} editingText={editingMessage?.draftText||''} editingAttachments={[]}
        onEditItem={editConversationMessage} onEditItemChangeText={text=>setEditingMessage(current=>({...current,draftText:text}))}
        onEditItemCancel={()=>setEditingMessage(null)} onEditItemSave={()=>{window.sent++;window.savedText=editingMessage.draftText;}}
        queued={[]} agentQuestions={[]} runtimeRuns={[]} scopedPendingApprovals={[]} approvalActions={{}} activeGoal={null}
        attachments={[]} commands={[]} actions={[]} projects={[]} backgroundGoalDeliveries={[]} backgroundGoalProviderWarnings={[]}
        pendingApprovalForResponse={()=>null} onAnswerQuestion={noop} onStopQuestionContinuation={noop}
        onConversationMouseUp={noop} onConversationScroll={noop} onCopyItem={noop} onRetryItem={noop}
      /></main>;
    }
    createRoot(document.getElementById('root')).render(<Fixture/>);
  ` }, bundle: true, write: false, format: "iife", platform: "browser", jsx: "automatic",
  plugins: baseline ? [{name:"owned-baseline",setup(plugin){
    plugin.onLoad({filter:/[\\/]components[\\/]chat[\\/](chat-workspace|conversation-card)\.tsx$/},args=>({
      contents:readFileSync(resolve(baseline,"src/components/chat",args.path.split(/[\\/]/).at(-1)),"utf8"),loader:"tsx",resolveDir:resolve(root,"src/components/chat"),
    }));
  }}] : [],
});
const css=(await postcss([tailwindcss(resolve(root,"tailwind.config.ts"))]).process(
  readFileSync(resolve(root,"src/styles.css"),"utf8").replace('@import "./theme.css";',readFileSync(resolve(root,"src/theme.css"),"utf8")),
  {from:resolve(root,"src/styles.css")},
)).css;
const browser=await chromium.launch({channel:"msedge",headless:true});
try{
  const page=await browser.newPage({viewport:{width:1100,height:800}});
  const errors=[];page.on("pageerror",e=>errors.push(e.message));
  await page.setContent(`<style>${css}</style><div id="root"></div>`);
  await page.addScriptTag({content:output.outputFiles[0].text});
  await page.waitForTimeout(250);
  assert.deepEqual(errors,[]);
  const labels=JSON.parse(readFileSync(resolve(root,"src/locales/zh-CN.json"),"utf8")).chat;
  const edit=page.getByRole("button",{name:labels.editMessage,exact:true});
  assert.equal(await edit.count(),1,"latest user edit is visible immediately while the request runs");
  await edit.click();
  const editor=page.locator("textarea").first();
  assert.equal(await editor.inputValue(),"有没有简短向的命中");
  await editor.fill("有没有简短向的名字");
  const save=page.getByRole("button",{name:labels.saveEdit,exact:true});
  assert.equal(await save.isDisabled(),true,"draft is editable but save waits for runtime");
  assert.equal(await page.evaluate(()=>window.sent),0);
  assert.equal(await page.evaluate(()=>window.originalChat.items[0].text),"有没有简短向的命中");
  const screenshot=process.env.VRCFORGE_MESSAGE_EDIT_SCREENSHOT;
  if(screenshot){mkdirSync(resolve(screenshot,".."),{recursive:true});await page.screenshot({path:screenshot});}
  await page.evaluate(()=>window.finishRuntime());
  await page.waitForTimeout(30);
  assert.equal(await save.isEnabled(),true);
  await save.click();
  assert.deepEqual(await page.evaluate(()=>({sent:window.sent,text:window.savedText})),{sent:1,text:"有没有简短向的名字"});
  assert.deepEqual(errors,[]);
  console.log("PASS: real ChatWorkspace typo replay; immediate draft, busy save disabled, unchanged history, settled save enabled.");
}finally{await browser.close();}
