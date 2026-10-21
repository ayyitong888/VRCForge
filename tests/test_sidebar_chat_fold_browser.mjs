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
      // App owns conversation sorting. Nonalphabetical names catch component reordering.
      const makeProjects=(type,names)=>names.map((name,index)=>({
        name:(type==="general"?"General":"Unity")+" fixture "+name,
        path:type==="general" && index===0?projectPath:"C:/fixture/"+type+"-"+index,
        projectType:type,
      }));
      const generalProjects=makeProjects("general",["Delta","Alpha","Foxtrot","Bravo","Echo","Charlie","Golf"]);
      const unityProjects=makeProjects("unity",["Zulu","Alpha","Kilo","Bravo","Victor","Charlie","Omega","Delta"]);
      const projectItems=generalProjects.flatMap((project,index)=>[project,...(unityProjects[index]?[unityProjects[index]]:[])]).concat(unityProjects.slice(generalProjects.length));
      const makeChats = (prefix, count) => Array.from({length:count},(_,index)=>({
        id:prefix+"-"+index,title:prefix+" conversation "+index,projectPath:prefix==="project"?projectPath:"",
      }));
      const temporaryChats = makeChats("temporary",8);
      temporaryChats[0].pinned = true;
      const projectChats = makeChats("project",6);
      const unityProjectChats = makeChats("unity-project",6);
      const testChats = makeChats("test",7).map(chat=>({...chat,isTestChat:true}));
      const chatSidebar = {
        temporaryChats,testChats,
        projectChatsByPath:new Map([[normalizeProjectPathKey(projectPath),projectChats],[normalizeProjectPathKey(unityProjects[0].path),unityProjectChats]]),
        times:new Map(),activityByChat:new Map(),
      };
      const noop=()=>{};
      const asyncNoop=async()=>{};
      const probeProjects=[
        {name:"Hook project A",path:"C:/hook-probe/a",projectType:"unity"},
        {name:"Hook project B",path:"C:/hook-probe/b",projectType:"unity"},
        ...["C","D","E","F","G"].map(letter=>({name:"Hook project "+letter,path:"C:/hook-probe/"+letter.toLowerCase(),projectType:"unity"})),
      ];
      const probeChats=[
        {id:"hook-chat-a",projectPath:probeProjects[0].path,title:"A saved conversation",updatedAt:"2025-01-01T00:00:00.000Z"},
        {id:"hook-chat-b",projectPath:probeProjects[1].path,title:"B saved conversation",updatedAt:"2026-01-01T00:00:00.000Z"},
      ];
      const emptyChats={temporaryChats:[],testChats:[],projectChatsByPath:new Map(),times:new Map(),activityByChat:new Map()};
      function ProjectManagementProbe(){
        const [activeProjectPath,setActiveProjectPath]=React.useState("");
        const state=useProjectManagement({endpoint:"http://127.0.0.1:9",runtimeConnected:false,
          activeProjectPath,activeProjectType:"unity",projects:probeProjects,
          refresh:asyncNoop,startRuntime:async()=>null,setError:noop,
          onProjectAdded:noop,onActiveProjectHidden:noop});
        const sorted=sortSidebarProjects(state.projectItems,probeChats,state.pinnedProjectSet,state.projectOrder);
        return <div data-testid="project-order-probe">
          <output data-testid="project-hook-probe">{JSON.stringify({activeProjectPath,
            projectItems:state.projectItems,projectOrder:state.projectOrder,sortedPaths:sorted.map(project=>project.path)})}</output>
          <AppSidebar {...callbacks}
            collapsed={false} activeView="chat" activeSettingsSection="general" developerOptionsEnabled={false}
            temporaryChatActive={false} activeProjectPath={activeProjectPath} activeChatId=""
            runtimeConnected={false} loadingProjects={false} projectItems={sorted} chatSidebar={emptyChats}
            backgroundGoalUnreadByChat={{}} collapsedProjects={{}} temporaryChatsCollapsed={false}
            pinnedProjectSet={state.pinnedProjectSet} renamingProjectPath="" projectRenameDraft=""
            renamingChatId="" renameDraft="" projectDisplayName={item=>item.name}
            onSelectProject={setActiveProjectPath} onReorderProjects={state.reorderProjects}
            onResetProjectOrder={state.resetProjectOrder}/>
        </div>;
      }
      const callbacks=Object.fromEntries([
        "onNewTemporaryChat","onOpenProjectPicker","onOpenDoctor","onOpenOptimization","onOpenProtection",
        "onOpenSkills","onOpenCheckpoints","onOpenSettings","onOpenSettingsSection",
        "onBackFromSettings","onRefreshProjects","onSelectProject","onToggleProjectCollapse",
        "onReorderProjects","onResetProjectOrder",
        "onProjectMenu","onProjectRenameChange","onProjectRenameCommit","onOpenChat",
        "onTogglePinChat","onDeleteChat","onChatMenu","onChatRenameChange","onChatRenameCommit",
      ].map(name=>[name,noop]));
      callbacks.onOpenChat=(chat)=>{(window.__openedChats ||= []).push(chat.id);};
      callbacks.onTogglePinChat=(id)=>{(window.__pinnedChatClicks ||= []).push(id);};
      callbacks.onDeleteChat=(id)=>{(window.__deleteChatClicks ||= []).push(id);};
      function Fixture(){
        const [activeProjectPath,setActiveProjectPath]=React.useState(generalProjects.at(-1).path);
        return <>
        <button onClick={()=>setActiveProjectPath(generalProjects.at(-1).path)}>Select older general fixture</button>
        <button onClick={()=>setActiveProjectPath(unityProjects.at(-1).path)}>Select older Unity fixture</button>
        <ProjectManagementProbe/>
        <div data-testid="fold-sidebar">
        <AppSidebar {...callbacks}
        collapsed={false} activeView="chat" activeSettingsSection="general" developerOptionsEnabled={false}
        temporaryChatActive={false} activeProjectPath={activeProjectPath} activeChatId="temporary-7"
        runtimeConnected={false} loadingProjects={false} projectItems={projectItems} chatSidebar={chatSidebar}
        backgroundGoalUnreadByChat={{}} collapsedProjects={{}} temporaryChatsCollapsed={false}
        pinnedProjectSet={new Set()} renamingProjectPath="" projectRenameDraft=""
        renamingChatId="" renameDraft="" projectDisplayName={item=>item.name}/>
        </div>
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
    // Old open-times deliberately oppose chat recency and must no longer sort projects.
    if (!localStorage.getItem("vrcforge_project_ui_prefs")) {
      localStorage.setItem("vrcforge_project_ui_prefs", JSON.stringify({
        pinnedPaths: [], aliases: {}, projectOrder: [], lastOpenedAt: {
          "c:\\hook-probe\\a": "2099-01-01T00:00:00.000Z",
          "c:\\hook-probe\\b": "2000-01-01T00:00:00.000Z",
        },
      }));
    }
    window.__projectPreferenceWrites = [];
    const originalSetItem = Storage.prototype.setItem;
    Storage.prototype.setItem = function(key, value) {
      if (key === "vrcforge_project_ui_prefs") window.__projectPreferenceWrites.push(value);
      return originalSetItem.call(this, key, value);
    };
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

  const foldSidebar = page.getByTestId("fold-sidebar");
  const generalSection = foldSidebar.locator("section").filter({ has: page.getByText("General project", { exact: true }) });
  const unitySection = foldSidebar.locator("section").filter({ has: page.getByText("Unity project", { exact: true }) });
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
  await generalSection.getByRole("button", { name: "More conversations (1)" }).click();
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
  const orderProbe = page.getByTestId("project-order-probe");
  const probeProjectRows = orderProbe.getByRole("button", { name: /^Hook project [A-G]$/ });
  const remainingPaths = ["c", "d", "e", "f", "g"].map((name) => "C:/hook-probe/" + name);
  const conversationOrder = ["C:/hook-probe/b", "C:/hook-probe/a", ...remainingPaths];
  const manualOrder = ["C:/hook-probe/a", "C:/hook-probe/b", ...remainingPaths];
  const conversationNames = ["B", "A", "C", "D", "E", "F", "G"].map((name) => "Hook project " + name);
  const manualNames = ["A", "B", "C", "D", "E", "F", "G"].map((name) => "Hook project " + name);
  const flushEffects = () => page.evaluate(() => new Promise((resolve) => requestAnimationFrame(() => requestAnimationFrame(resolve))));
  await flushEffects();
  assert.deepEqual((await readProbe()).sortedPaths, conversationOrder, "default project order follows saved conversation updatedAt, ignoring legacy open times");
  assert.deepEqual(await probeProjectRows.allTextContents(), conversationNames.slice(0, 5), "the real hook group starts folded, hiding its last two projects");
  const prefsBeforeClick = await readPrefs();
  const writesBeforeClick = await page.evaluate(() => window.__projectPreferenceWrites.length);
  await orderProbe.getByRole("button", { name: "Hook project A", exact: true }).click();
  await page.waitForFunction(() => JSON.parse(document.querySelector('[data-testid="project-hook-probe"]').textContent).activeProjectPath === "C:/hook-probe/a");
  await flushEffects();
  assert.deepEqual((await readProbe()).sortedPaths, conversationOrder, "selecting an older project cannot promote it above a newer conversation");
  assert.deepEqual(await readPrefs(), prefsBeforeClick, "selecting a project leaves stored preferences and times unchanged");
  assert.equal(await page.evaluate(() => window.__projectPreferenceWrites.length), writesBeforeClick, "selecting a project does not write project preferences");

  // The whole project button activates dragging only after a hold; quick movement
  // must not turn normal project selection into a saved reordering.
  const handle = orderProbe.getByRole("button", { name: "Hook project A", exact: true });
  const target = orderProbe.getByRole("button", { name: "Hook project B", exact: true });
  await handle.scrollIntoViewIfNeeded();
  const sourceBox = await handle.boundingBox();
  const targetBox = await target.boundingBox();
  assert.ok(sourceBox && targetBox, "both project rows have browser geometry");
  assert.equal(await orderProbe.locator('[data-vrcforge-project-drag-handle]').count(), 0, "project dragging does not require a separate handle");
  await page.mouse.move(sourceBox.x + sourceBox.width / 2, sourceBox.y + sourceBox.height / 2);
  await page.mouse.down();
  await page.mouse.move(sourceBox.x + sourceBox.width / 2, sourceBox.y + sourceBox.height / 2 - 12, { steps: 2 });
  await page.mouse.up();
  await flushEffects();
  assert.deepEqual((await readProbe()).sortedPaths, conversationOrder, "movement before the hold threshold cannot reorder a project");
  assert.deepEqual(await readPrefs(), prefsBeforeClick, "a quick gesture does not persist an accidental order");
  await target.click();
  await page.mouse.move(sourceBox.x + sourceBox.width / 2, sourceBox.y + sourceBox.height / 2);
  await page.mouse.down();
  await page.waitForTimeout(260);
  await page.mouse.move(sourceBox.x + sourceBox.width / 2, sourceBox.y + sourceBox.height / 2 - 10, { steps: 3 });
  await page.mouse.move(targetBox.x + targetBox.width / 2, targetBox.y + targetBox.height / 2 - 4, { steps: 12 });
  await page.mouse.up();
  await page.waitForFunction(() => {
    const order = JSON.parse(localStorage.getItem("vrcforge_project_ui_prefs") || "{}").projectOrder || [];
    return order[0]?.replaceAll("\\", "/").toLowerCase() === "c:/hook-probe/a";
  });
  assert.deepEqual((await readProbe()).sortedPaths, manualOrder, "real pointer drag overrides conversation recency while retaining all hidden projects");
  assert.equal((await readProbe()).activeProjectPath, "C:/hook-probe/b", "releasing a held drag does not switch the selected project");
  assert.deepEqual(await probeProjectRows.allTextContents(), manualNames.slice(0, 5), "dragging keeps the group folded and reflects its new first five rows");
  const persistedOrder = (await readPrefs()).projectOrder;
  assert.equal(persistedOrder.length, 7, "dragging visible rows saves the complete seven-project group, including two hidden rows");
  assert.deepEqual(persistedOrder.map((path) => path.replaceAll("\\", "/").toLowerCase()), manualOrder.map((path) => path.toLowerCase()), "all seven paths are persisted and the final five retain their relative order");
  await page.reload();
  await page.getByTestId("project-hook-probe").waitFor();
  assert.deepEqual((await readPrefs()).projectOrder, persistedOrder, "manual project order survives an actual reload");
  assert.deepEqual((await readProbe()).sortedPaths, manualOrder, "reloaded hook state retains manual order over newer conversation time");
  assert.equal(await probeProjectRows.count(), 5, "reloading restores the collapsed five-project preview");
  const orderUnitySection = orderProbe.locator("section").filter({ has: page.getByText("Unity project", { exact: true }) });
  await orderUnitySection.getByRole("button", { name: "More projects (2)", exact: true }).click();
  assert.deepEqual(await probeProjectRows.allTextContents(), manualNames, "expanding after reload recovers both hidden projects in preserved order");
  const prefsBeforeManualSelection = await readPrefs();
  const writesBeforeManualSelection = await page.evaluate(() => window.__projectPreferenceWrites.length);
  await orderProbe.getByRole("button", { name: "Hook project B", exact: true }).click();
  await page.waitForFunction(() => JSON.parse(document.querySelector('[data-testid="project-hook-probe"]').textContent).activeProjectPath === "C:/hook-probe/b");
  await flushEffects();
  assert.deepEqual((await readProbe()).sortedPaths, manualOrder, "project selection also preserves an existing manual order");
  assert.deepEqual(await readPrefs(), prefsBeforeManualSelection, "project selection cannot replace saved manual order or timestamps");
  assert.equal(await page.evaluate(() => window.__projectPreferenceWrites.length), writesBeforeManualSelection, "manual-order project selection does not persist preferences");
  await orderUnitySection.getByRole("button", { name: "Sort by last conversation", exact: true }).click();
  await page.waitForFunction(() => {
    const order = JSON.parse(localStorage.getItem("vrcforge_project_ui_prefs") || "{}").projectOrder || [];
    return order.length === 0;
  });
  assert.deepEqual((await readProbe()).sortedPaths, conversationOrder, "reset returns all seven projects to conversation order while keeping unknown-time relative order");
  assert.deepEqual(await probeProjectRows.allTextContents(), conversationNames, "reset reorders expanded rows without losing the formerly hidden projects");
  await page.reload();
  await page.getByTestId("project-hook-probe").waitFor();
  assert.deepEqual((await readPrefs()).projectOrder, [], "reset persists removal of the manual group order");
  assert.deepEqual((await readProbe()).sortedPaths, conversationOrder, "conversation ordering remains the default after reloading a reset");
  assert.deepEqual(await probeProjectRows.allTextContents(), conversationNames.slice(0, 5), "reset plus reload folds the complete restored list back to five");

  const keyboardProject = orderProbe.getByRole("button", { name: "Hook project A", exact: true });
  await keyboardProject.focus();
  await keyboardProject.press("Space");
  await page.waitForFunction(() => document.querySelector('[data-vrcforge-project-drag-row="Hook project A"]')?.getAttribute("aria-pressed") === "true");
  await keyboardProject.press("ArrowUp");
  await flushEffects();
  await keyboardProject.press("Space");
  await page.waitForFunction(() => {
    const order = JSON.parse(localStorage.getItem("vrcforge_project_ui_prefs") || "{}").projectOrder || [];
    return order[0]?.replaceAll("\\", "/").toLowerCase() === "c:/hook-probe/a";
  });
  assert.deepEqual((await readProbe()).sortedPaths, manualOrder, "the project button also supports keyboard reordering without a separate handle");

  const chatOrderKey = "vrcforge_sidebar_chat_order";
  const readChatOrder = () => page.evaluate((key) => JSON.parse(localStorage.getItem(key) || "[]"), chatOrderKey);
  const chatList = (prefix) => foldSidebar.locator('[data-vrcforge-chat-list]').filter({ has: page.locator('[data-vrcforge-chat-id="' + prefix + '-0"]') });
  const visibleChatIds = (prefix) => chatList(prefix).locator('[data-vrcforge-chat-id]').evaluateAll((rows) => rows.map((row) => row.getAttribute("data-vrcforge-chat-id")));
  const holdDrag = async (source, destination) => {
    await source.scrollIntoViewIfNeeded();
    const from = await source.boundingBox();
    const to = await destination.boundingBox();
    assert.ok(from && to, "chat rows have real pointer geometry");
    await page.mouse.move(from.x + from.width / 2, from.y + from.height / 2);
    await page.mouse.down();
    await page.waitForTimeout(260);
    await page.mouse.move(to.x + to.width / 2, to.y + to.height / 2 - 4, { steps: 12 });
    await page.mouse.up();
    // PointerSensor retains its click-capture listener for 50ms after release.
    // Keep the next independent gesture outside that intentional suppression.
    await page.waitForTimeout(60);
    await flushEffects();
  };
  const groups = [["temporary", 8], ["project", 6], ["unity-project", 6], ["test", 7]];
  const projectPrefsBeforeChatDrag = await readPrefs();
  for (const [prefix, count] of groups) {
    const others = new Map(await Promise.all(groups.filter(([name]) => name !== prefix).map(async ([name]) => [name, await visibleChatIds(name)])));
    await holdDrag(chatList(prefix).locator('[data-vrcforge-chat-drag-row="' + prefix + '-2"]'), chatList(prefix).locator('[data-vrcforge-chat-drag-row="' + prefix + '-1"]'));
    const expected = Array.from({ length: count }, (_, index) => prefix + "-" + index);
    [expected[1], expected[2]] = [expected[2], expected[1]];
    assert.deepEqual((await readChatOrder()).filter((id) => expected.includes(id)), expected, prefix + " saves the full group including hidden chats");
    assert.deepEqual((await visibleChatIds(prefix)).slice(0, 5), expected.slice(0, 5), prefix + " uses whole-row hold dragging in its folded preview");
    for (const [name, before] of others) assert.deepEqual(await visibleChatIds(name), before, "dragging " + prefix + " cannot reorder " + name);
  }
  assert.deepEqual(await page.evaluate(() => window.__openedChats || []), [], "drag release does not open a conversation");
  assert.deepEqual(await readPrefs(), projectPrefsBeforeChatDrag, "chat dragging leaves project order and metadata untouched");
  assert.equal(await foldSidebar.locator('[data-vrcforge-chat-id="temporary-7"]').getAttribute("data-vrcforge-chat-active"), "true", "the selected older conversation remains visible after sorting");
  const allManualChatIds = await readChatOrder();
  await page.reload();
  await page.getByTestId("fold-sidebar").waitFor();
  assert.deepEqual(await readChatOrder(), allManualChatIds, "all four chat groups retain their manual order after reload");
  for (const [prefix] of groups) assert.deepEqual((await visibleChatIds(prefix)).slice(0, 3), [prefix + "-0", prefix + "-2", prefix + "-1"], prefix + " restores its folded manual order");

  await holdDrag(chatList("temporary").locator('[data-vrcforge-chat-drag-row="temporary-1"]'), chatList("temporary").locator('[data-vrcforge-chat-drag-row="temporary-0"]'));
  assert.deepEqual(await readChatOrder(), allManualChatIds, "dragging across the pinned boundary cannot silently remove pin priority");
  const temporaryOne = chatList("temporary").locator('[data-vrcforge-chat-id="temporary-1"]');
  await holdDrag(temporaryOne.getByTitle("Pin Chat", { exact: true }), chatList("temporary").locator('[data-vrcforge-chat-drag-row="temporary-2"]'));
  assert.deepEqual(await readChatOrder(), allManualChatIds, "holding a pin control cannot activate conversation dragging");
  await temporaryOne.getByTitle("Pin Chat", { exact: true }).click();
  await temporaryOne.getByTitle("Delete Permanently", { exact: true }).click();
  assert.deepEqual(await page.evaluate(() => window.__pinnedChatClicks || []), ["temporary-1"], "pin control still invokes only its existing action");
  assert.deepEqual(await page.evaluate(() => window.__deleteChatClicks || []), ["temporary-1"], "delete control still invokes only its existing action");
  assert.deepEqual(await page.evaluate(() => window.__openedChats || []), [], "action controls never open a conversation");
  await temporaryOne.locator('[data-vrcforge-chat-drag-row]').click();
  assert.deepEqual(await page.evaluate(() => window.__openedChats || []), ["temporary-1"], "a short click still opens the exact conversation");
  assert.deepEqual(await readChatOrder(), allManualChatIds, "navigation and action controls do not change manual order");
  for (const [prefix] of groups) {
    await chatList(prefix).getByRole("button", { name: "Sort by last conversation", exact: true }).click();
    await page.waitForFunction(({ key, ids }) => !JSON.parse(localStorage.getItem(key) || "[]").some((id) => ids.includes(id)), { key: chatOrderKey, ids: Array.from({ length: groups.find(([name]) => name === prefix)[1] }, (_, index) => prefix + "-" + index) });
    assert.deepEqual((await visibleChatIds(prefix)).slice(0, 5), [0, 1, 2, 3, 4].map((index) => prefix + "-" + index), prefix + " resets independently to its original conversation order");
  }
  assert.deepEqual(await readChatOrder(), [], "resetting every chat group clears the custom order");
  assert.deepEqual(unexpectedRequests, [], "offline hook persistence makes no API, provider, asset, or WebSocket requests");
  assert.deepEqual(errors, []);
  console.log("PASS: project/chat folding, conversation-only default order, short click and movement guards, whole-row hold drag persistence/reload/reset, keyboard drag, all four chat groups, pinned boundary and selected older chat; no API/provider requests.");
} finally {
  await browser?.close();
  await new Promise((resolve) => server.close(resolve));
}
