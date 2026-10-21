// Exercises the real React sidebar with preordered project and saved-chat groups.
import assert from "node:assert/strict";
import { createServer } from "node:http";
import { createRequire } from "node:module";
import { build } from "esbuild";

const require = createRequire(import.meta.url);
const bundle = await build({
  stdin: {
    resolveDir: process.cwd(),
    loader: "tsx",
    contents: `
      import React from "react";
      import { createRoot } from "react-dom/client";
      import i18n from "i18next";
      import { initReactI18next } from "react-i18next";
      import en from "./src/locales/en-US.json";
      import { AppSidebar } from "./src/components/sidebar/app-sidebar";
      import { useProjectManagement } from "./src/hooks/use-project-management";
      import { sortSidebarProjects } from "./src/lib/sidebar-project-order";
      import { normalizeProjectPathKey } from "./src/lib/project-path";

      i18n.use(initReactI18next).init({lng:"en",resources:{en:{translation:en}},interpolation:{escapeValue:false}});
      const projectPath = "C:/fixture/project";
      // App owns activity sorting. Nonalphabetical names catch component reordering.
      const makeProjects=(type,names)=>names.map((name,index)=>({
        name:(type==="general"?"General":"Unity")+" fixture "+name,
        path:type==="general" && index===0?projectPath:"C:/fixture/"+type+"-"+index,
        projectType:type,lastOpenedAt:new Date(Date.UTC(2026,9,21,12-index)).toISOString(),
      }));
      const generalProjects=makeProjects("general",["Delta","Alpha","Foxtrot","Bravo","Echo","Charlie","Golf"]);
      const unityProjects=makeProjects("unity",["Zulu","Alpha","Kilo","Bravo","Victor","Charlie","Omega","Delta"]);
      const projectItems=generalProjects.flatMap((project,index)=>[project,...(unityProjects[index]?[unityProjects[index]]:[])]).concat(unityProjects.slice(generalProjects.length));
      const makeChats = (prefix, count) => Array.from({length:count},(_,index)=>({
        id:prefix+"-"+index,title:prefix+" conversation "+index,projectPath:prefix==="project"?projectPath:"",
      }));
      const temporaryChats = makeChats("temporary",8);
      const projectChats = makeChats("project",6);
      const testChats = makeChats("test",7).map(chat=>({...chat,isTestChat:true}));
      const chatSidebar = {
        temporaryChats,testChats,
        projectChatsByPath:new Map([[normalizeProjectPathKey(projectPath),projectChats]]),
        times:new Map(),activityByChat:new Map(),
      };
      const noop=()=>{};
      const asyncNoop=async()=>{};
      const probeProjects=[
        {name:"Hook project A",path:"C:/hook-probe/a",projectType:"unity"},
        {name:"Hook project B",path:"C:/hook-probe/b",projectType:"unity"},
      ];
      function ProjectManagementProbe(){
        const [activeProjectPath,setActiveProjectPath]=React.useState("");
        const state=useProjectManagement({endpoint:"http://127.0.0.1:9",runtimeConnected:false,
          activeProjectPath,activeProjectType:"unity",projects:probeProjects,
          refresh:asyncNoop,startRuntime:async()=>null,setError:noop,
          onProjectAdded:noop,onActiveProjectHidden:noop});
        const sorted=sortSidebarProjects(state.projectItems,[],state.pinnedProjectSet);
        return <div>
          <button onClick={()=>setActiveProjectPath(probeProjects[0].path)}>Open hook project A</button>
          <button onClick={()=>setActiveProjectPath(probeProjects[1].path)}>Open hook project B</button>
          <output data-testid="project-hook-probe">{JSON.stringify({activeProjectPath,
            projectItems:state.projectItems,sortedPaths:sorted.map(project=>project.path)})}</output>
        </div>;
      }
      const callbacks=Object.fromEntries([
        "onNewTemporaryChat","onOpenProjectPicker","onOpenDoctor","onOpenOptimization","onOpenProtection",
        "onOpenSkills","onOpenCheckpoints","onOpenSettings","onOpenSettingsSection",
        "onBackFromSettings","onRefreshProjects","onSelectProject","onToggleProjectCollapse",
        "onProjectMenu","onProjectRenameChange","onProjectRenameCommit","onOpenChat",
        "onTogglePinChat","onDeleteChat","onChatMenu","onChatRenameChange","onChatRenameCommit",
      ].map(name=>[name,noop]));
      function Fixture(){
        const [activeProjectPath,setActiveProjectPath]=React.useState(generalProjects.at(-1).path);
        return <>
        <button onClick={()=>setActiveProjectPath(generalProjects.at(-1).path)}>Select older general fixture</button>
        <button onClick={()=>setActiveProjectPath(unityProjects.at(-1).path)}>Select older Unity fixture</button>
        <ProjectManagementProbe/>
        <AppSidebar {...callbacks}
        collapsed={false} activeView="chat" activeSettingsSection="general" developerOptionsEnabled={false}
        temporaryChatActive={false} activeProjectPath={activeProjectPath} activeChatId="temporary-7"
        runtimeConnected={false} loadingProjects={false} projectItems={projectItems} chatSidebar={chatSidebar}
        backgroundGoalUnreadByChat={{}} collapsedProjects={{}} temporaryChatsCollapsed={false}
        pinnedProjectSet={new Set()} renamingProjectPath="" projectRenameDraft=""
        renamingChatId="" renameDraft="" projectDisplayName={item=>item.name}/>
        </>;
      }
      createRoot(document.getElementById("root")).render(<Fixture/>);
    `,
  },
  bundle: true,
  write: false,
  format: "iife",
  platform: "browser",
});
const { chromium } = require(process.env.VRCFORGE_PLAYWRIGHT_PATH || "playwright");

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
  const unexpectedRequests = [];
  page.on("pageerror", (error) => errors.push(error.message));
  const fixtureUrl = `http://127.0.0.1:${server.address().port}/`;
  await page.route("**/*", async (route) => {
    const request = route.request();
    if (request.url() === fixtureUrl && request.resourceType() === "document") {
      await route.continue();
    } else {
      unexpectedRequests.push({ url: request.url(), resourceType: request.resourceType() });
      await route.abort();
    }
  });
  page.on("websocket", (socket) => unexpectedRequests.push({ url: socket.url(), resourceType: "websocket" }));
  await page.addInitScript(() => {
    // Seed real historical preference data once; a reload must read persisted hook writes.
    if (!localStorage.getItem("vrcforge_project_ui_prefs")) {
      localStorage.setItem("vrcforge_project_ui_prefs", JSON.stringify({
        pinnedPaths: [], aliases: {}, lastOpenedAt: {
          "c:\\hook-probe\\a": "2019-01-01T00:00:00.000Z",
          "c:\\hook-probe\\b": "2020-01-01T00:00:00.000Z",
        },
      }));
    }
  });
  await page.goto(fixtureUrl);

  const tempRows = page.locator('[data-vrcforge-chat-id^="temporary-"]');
  assert.equal(await tempRows.count(), 6, "the latest five and selected older chat remain visible");
  assert.equal(await page.locator('[data-vrcforge-chat-id="temporary-7"]').getAttribute("data-vrcforge-chat-active"), "true");
  const tempMore = page.getByRole("button", { name: "More conversations (2)" }).first();
  assert.equal(await tempMore.getAttribute("aria-expanded"), "false");
  await tempMore.click();
  assert.equal(await tempRows.count(), 8, "expanding shows every temporary conversation");
  const tempFewer = page.getByRole("button", { name: "Show fewer conversations" }).first();
  assert.equal(await tempFewer.getAttribute("aria-expanded"), "true");
  await tempFewer.click();
  assert.equal(await tempRows.count(), 6, "collapsing restores the preview and keeps the selected chat");

  const generalSection = page.locator("section").filter({ has: page.getByText("General project", { exact: true }) });
  const unitySection = page.locator("section").filter({ has: page.getByText("Unity project", { exact: true }) });
  const generalRows = generalSection.getByRole("button", { name: /^General fixture / });
  const unityRows = unitySection.getByRole("button", { name: /^Unity fixture / });
  const generalNames = ["Delta", "Alpha", "Foxtrot", "Bravo", "Echo", "Charlie", "Golf"].map((name) => "General fixture " + name);
  const unityNames = ["Zulu", "Alpha", "Kilo", "Bravo", "Victor", "Charlie", "Omega", "Delta"].map((name) => "Unity fixture " + name);
  assert.deepEqual(await generalRows.allTextContents(), [...generalNames.slice(0, 5), generalNames.at(-1)], "general projects show the first five plus the selected older project in supplied order");
  assert.deepEqual(await unityRows.allTextContents(), unityNames.slice(0, 5), "Unity projects without an older selection show five in supplied order");
  const generalMore = generalSection.getByRole("button", { name: "More projects (1)" });
  assert.equal(await generalMore.getAttribute("aria-expanded"), "false");
  await generalMore.click();
  assert.deepEqual(await generalRows.allTextContents(), generalNames, "expanding general projects preserves the complete preordered list");
  const generalFewer = generalSection.getByRole("button", { name: "Show fewer projects" });
  assert.equal(await generalFewer.getAttribute("aria-expanded"), "true");
  await generalFewer.click();
  assert.deepEqual(await generalRows.allTextContents(), [...generalNames.slice(0, 5), generalNames.at(-1)], "collapsing general projects keeps the older selected project");

  await page.getByRole("button", { name: "Select older Unity fixture", exact: true }).click();
  await page.waitForFunction(() => [...document.querySelectorAll("button")].some((button) => button.textContent === "More projects (2)"));
  assert.deepEqual(await generalRows.allTextContents(), generalNames.slice(0, 5), "changing selection removes the extra general row while keeping its five-project preview");
  assert.deepEqual(await unityRows.allTextContents(), [...unityNames.slice(0, 5), unityNames.at(-1)], "an older selected Unity project remains available below the first five");
  const unityMore = unitySection.getByRole("button", { name: "More projects (2)" });
  assert.equal(await unityMore.getAttribute("aria-expanded"), "false");
  await unityMore.click();
  assert.deepEqual(await unityRows.allTextContents(), unityNames, "all eight Unity projects expand in supplied order");
  const unityFewer = unitySection.getByRole("button", { name: "Show fewer projects" });
  assert.equal(await unityFewer.getAttribute("aria-expanded"), "true");
  await unityFewer.click();
  assert.deepEqual(await unityRows.allTextContents(), [...unityNames.slice(0, 5), unityNames.at(-1)], "collapsing Unity projects restores five plus the selected older project");

  const projectRows = page.locator('[data-vrcforge-chat-id^="project-"]');
  assert.equal(await projectRows.count(), 5, "project groups use the same five-chat preview");
  await page.getByRole("button", { name: "More conversations (1)" }).click();
  assert.equal(await projectRows.count(), 6, "project conversations can be expanded");
  await generalSection.getByRole("button", { name: "Show fewer conversations" }).click();
  assert.equal(await projectRows.count(), 5, "project conversations collapse back to five independently of project folding");

  const testRows = page.locator('[data-vrcforge-chat-id^="test-"]');
  assert.equal(await testRows.count(), 5, "test conversations use the same five-chat preview");
  const testSection = page.locator("section").filter({ has: page.getByText("Test chats", { exact: true }) });
  await testSection.getByRole("button", { name: "More conversations (2)" }).click();
  assert.equal(await testRows.count(), 7, "test conversations can be expanded");
  await testSection.getByRole("button", { name: "Show fewer conversations" }).click();
  assert.equal(await testRows.count(), 5, "test conversations collapse back to five");

  const readProbe = async () => JSON.parse(await page.getByTestId("project-hook-probe").textContent());
  const readPrefs = () => page.evaluate(() => JSON.parse(localStorage.getItem("vrcforge_project_ui_prefs")));
  assert.deepEqual((await readProbe()).sortedPaths, ["C:/hook-probe/b", "C:/hook-probe/a"], "the real hook projects expose existing local activity timestamps to the production sorter");
  const openedAfter = await page.evaluate(() => Date.now());
  await page.getByRole("button", { name: "Open hook project A", exact: true }).click();
  await page.waitForFunction(() => {
    const prefs = JSON.parse(localStorage.getItem("vrcforge_project_ui_prefs") || "{}");
    return Date.parse(prefs.lastOpenedAt?.["c:\\hook-probe\\a"]) > Date.parse("2020-01-01T00:00:00.000Z");
  });
  const firstPrefs = await readPrefs();
  const aOpenedAt = firstPrefs.lastOpenedAt["c:\\hook-probe\\a"];
  assert.equal(new Date(aOpenedAt).toISOString(), aOpenedAt, "last opened time is an actual ISO timestamp");
  assert.ok(Date.parse(aOpenedAt) >= openedAfter && Date.parse(aOpenedAt) <= await page.evaluate(() => Date.now()), "the real active-project effect records the current browser time");
  assert.equal(firstPrefs.lastOpenedAt["c:\\hook-probe\\b"], "2020-01-01T00:00:00.000Z", "opening A preserves B's historical timestamp");
  assert.equal((await readProbe()).projectItems.find((project) => project.path === "C:/hook-probe/a").lastOpenedAt, aOpenedAt, "hook projectItems project the persisted open time");
  assert.deepEqual((await readProbe()).sortedPaths, ["C:/hook-probe/a", "C:/hook-probe/b"], "the newly opened project moves ahead without a chat or network connection");
  await page.reload();
  await page.getByTestId("project-hook-probe").waitFor();
  const reloadedPrefs = await readPrefs();
  assert.equal(reloadedPrefs.lastOpenedAt["c:\\hook-probe\\a"], aOpenedAt, "reload restores A's recorded timestamp rather than synthesizing another open");
  assert.equal(reloadedPrefs.lastOpenedAt["c:\\hook-probe\\b"], "2020-01-01T00:00:00.000Z", "reload preserves the unopened project's old timestamp");
  assert.deepEqual((await readProbe()).sortedPaths, ["C:/hook-probe/a", "C:/hook-probe/b"], "activity ordering survives a real page reload");
  await page.getByRole("button", { name: "Open hook project B", exact: true }).click();
  await page.waitForFunction((previous) => {
    const prefs = JSON.parse(localStorage.getItem("vrcforge_project_ui_prefs") || "{}");
    return Date.parse(prefs.lastOpenedAt?.["c:\\hook-probe\\b"]) > Date.parse(previous);
  }, aOpenedAt);
  assert.equal((await readPrefs()).lastOpenedAt["c:\\hook-probe\\a"], aOpenedAt, "opening B keeps A's prior open time");
  assert.deepEqual((await readProbe()).sortedPaths, ["C:/hook-probe/b", "C:/hook-probe/a"], "switching to B updates persisted activity and the production sort order");
  assert.deepEqual(unexpectedRequests, [], "offline hook persistence makes no API, provider, asset, or WebSocket requests");
  assert.deepEqual(errors, []);
  console.log("PASS: project/chat folding and real useProjectManagement last-opened persistence, reload, and activity ordering; no API/provider requests.");
} finally {
  await browser?.close();
  await new Promise((resolve) => server.close(resolve));
}
