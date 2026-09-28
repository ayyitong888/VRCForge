// Deterministic history-read reproduction for switching chat sessions.
import assert from "node:assert/strict";
import { createServer } from "node:http";
import { createRequire } from "node:module";
import { build } from "esbuild";

const require = createRequire(import.meta.url);
const { chromium } = require(process.env.VRCFORGE_PLAYWRIGHT_PATH || "playwright");
const itemCount = 1200;
const chatCount = 24;
const bundle = await build({
  stdin: {
    resolveDir: process.env.VRCFORGE_TEST_SOURCE_ROOT || process.cwd(), loader: "tsx",
    contents: `
      import React from "react";
      import { createRoot } from "react-dom/client";
      import i18n from "i18next";
      import { initReactI18next } from "react-i18next";
      import { useChatSessions } from "./src/hooks/use-chat-sessions";
      i18n.use(initReactI18next).init({lng:"en",resources:{en:{translation:{}}},interpolation:{escapeValue:false}});
      let historyReads = 0;
      const chats = Array.from({length:${chatCount}},(_,chatIndex)=>{
        const items = new Proxy(Array.from({length:${itemCount}},(_,itemIndex)=>({
          id:"item-"+chatIndex+"-"+itemIndex,type:"assistant",text:"synthetic history"
        })),{get(target,key,receiver){
          if(typeof key==="string" && /^(0|[1-9][0-9]*)$/.test(key)) historyReads++;
          return Reflect.get(target,key,receiver);
        }});
        return {id:"chat-"+chatIndex,sessionId:"session-"+chatIndex,title:"History "+chatIndex,
          projectPath:"",projectType:"general",createdAt:"2026-01-01T00:00:00Z",
          updatedAt:"2026-01-01T00:00:00Z",revision:0,items};
      });
      window.resetHistoryReads=()=>{historyReads=0};
      window.readHistoryReads=()=>historyReads;
      const noop=()=>{};
      function Fixture(){
        const sessions=useChatSessions({activeView:"chat",endpoint:"",runtimeConnected:false,projectPrefsReady:true,
          projectPaths:[],customProjectPaths:[],activeProjectPath:"",activeProjectType:"general",
          setActiveProjectPath:noop,setActiveProjectType:noop,setActiveView:noop,setError:noop,
          expandProjectGroup:noop,initialChatState:{chats,activeChatId:chats[0].id}});
        return <><output data-testid="active">{sessions.activeChatId}</output>
          <button onClick={()=>sessions.openChat(chats[1])}>open target</button></>;
      }
      createRoot(document.getElementById("root")).render(<Fixture/>);
    `,
  },
  bundle: true, write: false, format: "iife", platform: "browser",
});
// Task-owned loopback fixture: synthetic public data, no credentials/auth needed.
// Browser and ephemeral server are both closed in finally.
const server = createServer((_request, response) => {
  response.setHeader("Content-Type", "text/html; charset=utf-8");
  response.end('<!doctype html><div id="root"></div><script>'+bundle.outputFiles[0].text.replaceAll("</script", "<\\/script")+'</script>');
});
let browser;
try {
  await new Promise((resolve) => server.listen(0, "127.0.0.1", resolve));
  browser = await chromium.launch({ channel: "msedge", headless: true });
  const page = await browser.newPage();
  const errors = [];
  page.on("pageerror", (error) => errors.push(error.message));
  await page.goto(`http://127.0.0.1:${server.address().port}`);
  await page.getByTestId("active").waitFor();
  await page.evaluate(() => window.resetHistoryReads());
  await page.getByRole("button", { name: "open target" }).click();
  await page.getByTestId("active").filter({ hasText: "chat-1" }).waitFor();
  const reads = await page.evaluate(() => window.readHistoryReads());
  assert.ok(reads <= itemCount * 2,
    `one switch scanned ${reads} history entries; target-only lastViewed scan budget is ${itemCount * 2}`);
  assert.deepEqual(errors, []);
  console.log(`PASS: ${chatCount} synthetic histories × ${itemCount} entries; switching read ${reads} history entries.`);
} finally {
  await browser?.close();
  await new Promise((resolve) => server.close(resolve));
}
