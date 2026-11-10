import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import { createServer } from "node:http";
import { createRequire } from "node:module";
import { build } from "esbuild";

const { chromium } = createRequire(import.meta.url)(process.env.VRCFORGE_PLAYWRIGHT_PATH || "playwright");
const app = readFileSync("src/App.tsx", "utf8");
const submit = app.slice(app.indexOf("  async function submitMessage("), app.indexOf("  function stopInteractiveActivity("));
assert.match(app, /useComposerDraft\(\s*activeChatId,\s*activeProjectPath,\s*!activeChat \|\| !isUnstartedChat\(activeChat\),/);

const bundle = await build({
  stdin: { resolveDir: process.cwd(), loader: "tsx", contents: `
import React, {useRef,useState} from "react";
import {createRoot} from "react-dom/client";
import {useComposerDraft} from "./src/hooks/use-composer-draft";
import {isUnstartedChat} from "./src/lib/chat-thread";
function Fixture() {
  const initial = new URLSearchParams(location.search).get("chat") ?? "a";
  const [activeChatId,setChatId] = useState(initial);
  const [activeProjectPath,setProject] = useState("D:/project/a");
  const chats = useRef(new Map(["a","b"].map(id=>[id,{id,sessionId:id,items:[]}]))).current;
  const nextChatId = useRef(0);
  const getChatById = id => chats.get(id);
  const activeChat = getChatById(activeChatId);
  const {input,setInput,attachments,setAttachments,beginSubmission} = useComposerDraft(
    activeChatId, activeProjectPath, !activeChat || !isUnstartedChat(activeChat)
  );
  const activeChatPending = false, chatAvailable = true, pendingReplyQuestionId = "";
  const attachmentLoadsRef = useRef(0), setError = ()=>{};
  const sessionHandoff = {consume: async()=>({context:null})};
  const resolveContextLimit = ()=>({known:false}), providerSnapshot = {}, currentModelInfo = null, apiConfig = null;
  function ensureActiveChat() {
    if (activeChatId) return activeChatId;
    const id = "new-chat-" + (++nextChatId.current);
    chats.set(id,{id,sessionId:"",items:[]});
    setChatId(id);
    return id;
  }
  function submitTurn(turn) {
    window.lastTurn = turn;
    return new Promise(resolve=>{
      window.start = ()=>{getChatById(turn.chatId).sessionId="runtime-session";setProject("D:/project/A");};
      window.finish = resolve;
    });
  }
  ${submit}
  window.select = (id, project="D:/project/a")=>{setChatId(id);setProject(project);};
  window.submit = ()=>void submitMessage();
  window.attach = id=>setAttachments([{id}]);
  return <><textarea aria-label="Draft" value={input} onChange={e=>setInput(e.target.value)}/>
    <output id="attachments">{attachments.map(a=>a.id).join(",")}</output></>;
}
createRoot(document.getElementById("root")).render(<Fixture/>);
` },
  bundle: true, write: false, format: "esm", jsx: "automatic",
});

const server = createServer((req, res) => {
  if (req.url === "/fixture.js") {
    res.setHeader("Content-Type", "text/javascript");
    res.end(bundle.outputFiles[0].text);
  } else res.end('<div id="root"></div><script type="module" src="/fixture.js"></script>');
});
await new Promise(resolve=>server.listen(0, "127.0.0.1", resolve));
let browser;
try {
  browser = await chromium.launch({headless:true,channel:process.env.VRCFORGE_BROWSER_CHANNEL || "msedge"});
  const context = await browser.newContext();
  let page = await context.newPage();
  const url = `http://127.0.0.1:${server.address().port}`;
  const draft = () => page.getByRole("textbox", {name:"Draft"});
  const expectDraft = async text => {
    await page.waitForFunction(value=>document.querySelector("textarea")?.value===value, text);
    assert.equal(await draft().inputValue(), text);
  };
  await page.goto(url);
  await draft().fill("A 继续改模");
  await page.evaluate(()=>{window.attach("a");window.select("b");});
  await expectDraft("");
  assert.equal(await page.locator("#attachments").textContent(), "");
  await draft().fill("B 的输入");
  await page.evaluate(()=>window.select("a"));
  await expectDraft("A 继续改模");
  assert.equal(await page.locator("#attachments").textContent(), "a");
  await page.evaluate(()=>window.submit());
  await expectDraft("");
  await page.evaluate(()=>window.select("b"));
  await expectDraft("B 的输入");
  await page.close();
  page = await context.newPage();
  await page.goto(url);
  await expectDraft("A 继续改模");
  assert.equal(await page.evaluate(()=>window.lastTurn), undefined, "restoring a draft never sends it");

  await page.evaluate(()=>window.submit());
  await expectDraft("");
  await draft().fill("A 的下一条");
  await page.evaluate(()=>window.select("b"));
  await expectDraft("B 的输入");
  await page.evaluate(()=>window.finish("not_accepted"));
  await page.evaluate(()=>window.select("a"));
  await expectDraft("A 的下一条");
  await page.evaluate(()=>window.submit());
  await expectDraft("");
  await page.evaluate(()=>window.finish("started"));
  await page.reload();
  await expectDraft("");

  await page.evaluate(()=>window.select(""));
  await draft().fill("未开始的项目对话");
  await page.evaluate(()=>window.select("", "D:/project/b"));
  await expectDraft("");
  await page.evaluate(()=>window.select("", ["d:", "project", "a"].join(String.fromCharCode(92))));
  await expectDraft("未开始的项目对话");
  await page.evaluate(()=>window.submit());
  await expectDraft("");
  assert.equal(await page.evaluate(()=>window.lastTurn.chatId), "new-chat-1");
  await page.evaluate(()=>window.start());
  await page.evaluate(()=>window.finish("not_accepted"));
  await expectDraft("未开始的项目对话");
  await page.evaluate(()=>window.select(""));
  await expectDraft("");
  await draft().fill("新对话第一条");
  await page.evaluate(()=>window.submit());
  await expectDraft("");
  await draft().fill("启动时继续写下一条");
  await page.evaluate(()=>window.start());
  await expectDraft("启动时继续写下一条");
  await page.evaluate(()=>window.finish("started"));
  await page.evaluate(()=>window.select(""));
  await expectDraft("");
  await page.evaluate(()=>window.select("new-chat-2"));
  await expectDraft("启动时继续写下一条");

  const cdp = await context.newCDPSession(page);
  const crashed = page.waitForEvent("crash");
  void cdp.send("Page.crash").catch(()=>{});
  await crashed;
  await page.close();
  page = await context.newPage();
  await page.goto(url + "/?chat=new-chat-2");
  await expectDraft("启动时继续写下一条");
  assert.equal(await page.evaluate(()=>window.lastTurn), undefined);
  console.log("PASS composer draft: independent chats and project scratch, reopen, send outcome, newer text and first-session transition");
} finally {
  await browser?.close();
  server.closeAllConnections();
  await new Promise(resolve=>server.close(resolve));
}
