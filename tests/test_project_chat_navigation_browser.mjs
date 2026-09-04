// Real React sidebar -> session hook navigation; synthetic chats only.
// The loopback server/browser belong to this test and are closed in finally.
import assert from "node:assert/strict";
import { createServer } from "node:http";
import { createRequire } from "node:module";
import { build } from "esbuild";

const require = createRequire(import.meta.url);
const { chromium } = require(process.env.VRCFORGE_PLAYWRIGHT_PATH || "playwright");
const bundle = await build({
  stdin: {
    resolveDir: process.cwd(), loader: "tsx",
    contents: `
      import React, { useState } from "react";
      import { createRoot } from "react-dom/client";
      import i18n from "i18next";
      import { initReactI18next } from "react-i18next";
      import en from "./src/locales/en-US.json";
      import { AppSidebar } from "./src/components/sidebar/app-sidebar";
      import { useChatSessions } from "./src/hooks/use-chat-sessions";
      i18n.use(initReactI18next).init({lng:"en",resources:{en:{translation:en}},interpolation:{escapeValue:false}});
      const projects = [
        {name:"General fixture",path:"/fixture/general",projectType:"general"},
        {name:"Unity fixture",path:"/fixture/unity",projectType:"unity"},
      ];
      const paths = projects.map(p=>p.path);
      const initial = {activeChatId:"unity-old",chats:projects.map(p=>({
        id:p.projectType+"-old", sessionId:p.projectType+"-session",
        title:p.projectType+" history", projectPath:p.path, projectType:p.projectType,
        createdAt:"2026-01-01T00:00:00Z",updatedAt:"2026-01-01T00:00:00Z",
        revision:0,items:[{type:"assistant",text:p.projectType+" saved value"}],
      }))};
      const noop=()=>{};
      const callbacks=Object.fromEntries([
        "onOpenProjectPicker","onOpenDoctor","onOpenOptimization","onOpenProtection",
        "onOpenSkills","onOpenCheckpoints","onOpenSettings","onOpenSettingsSection",
        "onBackFromSettings","onRefreshProjects","onProjectMenu","onProjectRenameChange",
        "onProjectRenameCommit","onChatMenu",
      ].map(name=>[name,noop]));
      function Fixture(){
        const [activeProjectPath,setActiveProjectPath]=useState("/fixture/unity");
        const [activeProjectType,setActiveProjectType]=useState("unity");
        const [activeView,setActiveView]=useState("chat");
        const [collapsedProjects,setCollapsed]=useState({});
        const sessions=useChatSessions({endpoint:"",runtimeConnected:false,projectPrefsReady:true,
          projectPaths:paths,customProjectPaths:paths,activeProjectPath,activeProjectType,
          setActiveProjectPath,setActiveProjectType,setActiveView,setError:noop,
          expandProjectGroup:noop,initialChatState:initial});
        const state={activeChatId:sessions.activeChatId,activeProjectPath,activeProjectType,
          title:sessions.activeChat?.title||"new conversation",items:sessions.activeChat?.items||[],
          chatCount:sessions.chats.length};
        return <>
          <AppSidebar {...callbacks} collapsed={false} activeView={activeView}
            activeSettingsSection="general" developerOptionsEnabled={false}
            temporaryChatActive={!activeProjectPath&&!sessions.activeChat}
            activeProjectPath={activeProjectPath} activeChatId={sessions.activeChatId}
            runtimeConnected={false} loadingProjects={false} projectItems={projects}
            chatSidebar={sessions.chatSidebar} backgroundGoalUnreadByChat={{}}
            collapsedProjects={collapsedProjects} temporaryChatsCollapsed={false}
            pinnedProjectSet={new Set()} renamingProjectPath="" projectRenameDraft=""
            renamingChatId="" renameDraft="" projectDisplayName={p=>p.name}
            onSelectProject={path=>sessions.newConversation(path,projects.find(p=>p.path===path).projectType)}
            onOpenChat={sessions.openChat} onNewTemporaryChat={sessions.newTemporaryChat}
            onToggleProjectCollapse={path=>setCollapsed(v=>({...v,[path]:!v[path]}))}
            onTogglePinChat={sessions.togglePinChat} onDeleteChat={noop}
            onChatRenameChange={noop} onChatRenameCommit={noop}/>
          <pre data-testid="state">{JSON.stringify(state)}</pre>
        </>;
      }
      createRoot(document.getElementById("root")).render(<Fixture/>);
    `,
  },
  bundle:true,write:false,format:"iife",platform:"browser",
});
const server=createServer((_request,response)=>{
  response.setHeader("Content-Type","text/html; charset=utf-8");
  response.end('<!doctype html><div id="root"></div><script>'+bundle.outputFiles[0].text.replaceAll('</script','<\\/script')+'</script>');
});
let browser;
try {
  await new Promise(resolve=>server.listen(0,"127.0.0.1",resolve));
  browser=await chromium.launch({channel:"msedge",headless:true});
  const page=await browser.newPage();
  const errors=[];
  page.on("pageerror",error=>errors.push(error.message));
  await page.goto(`http://127.0.0.1:${server.address().port}`);
  const read=async()=>JSON.parse(await page.getByTestId("state").textContent());
  await page.getByTestId("state").waitFor();
  assert.equal((await read()).activeChatId,"unity-old");
  for(const type of ["unity","general","unity","general"]){
    const label=type==="unity"?"Unity fixture":"General fixture";
    await page.getByRole("button",{name:label,exact:true}).click();
    const draft=await read();
    assert.equal(draft.activeChatId,"");
    assert.equal(draft.activeProjectPath,`/fixture/${type}`);
    assert.equal(draft.activeProjectType,type);
    assert.equal(draft.title,"new conversation");
    assert.deepEqual(draft.items,[]);
    assert.equal(draft.chatCount,2,"project navigation must not persist an empty chat");
    await page.getByRole("button",{name:new RegExp(type+" history")}).click();
    const restored=await read();
    assert.equal(restored.activeChatId,type+"-old");
    assert.equal(restored.activeProjectPath,`/fixture/${type}`);
    assert.equal(restored.items[0].text,type+" saved value");
  }
  assert.deepEqual(errors,[]);
  console.log("PASS: General/Unity project rows open drafts; chat rows restore exact history; repeat/same-project navigation preserves stored chats.");
} finally {
  await browser?.close();
  await new Promise(resolve=>server.close(resolve));
}
