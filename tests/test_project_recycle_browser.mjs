import assert from "node:assert/strict";
import { createServer } from "node:http";
import { createRequire } from "node:module";
import { build } from "esbuild";

const { chromium } = createRequire(import.meta.url)(process.env.VRCFORGE_PLAYWRIGHT_PATH || "playwright");
const projects = [
  { name: "Avatar", path: "D:/fixture/old/Avatar", projectType: "unity" },
  { name: "Avatar", path: "D:/fixture/keep/Avatar", projectType: "unity" },
];
const [target, retained] = projects;
let projectRecycled = false;
const requests = [];
const chatWrites = [];
const chats = projects.map((project, index) => ({
  id: `chat-${index}`, sessionId: `session-${index}`, title: `Conversation ${index}`,
  projectPath: project.path, projectType: "unity", items: [],
  createdAt: "2026-01-01T00:00:00Z", updatedAt: "2026-01-01T00:00:00Z",
}));

const bundle = await build({
  stdin: {
    resolveDir: process.cwd(), loader: "tsx",
    contents: `
      import React, {useMemo, useState} from "react";
      import {createRoot} from "react-dom/client";
      import i18n from "i18next";
      import {initReactI18next} from "react-i18next";
      import en from "./src/locales/en-US.json";
      import {SidebarMenus} from "./src/components/sidebar/sidebar-menus";
      import {useProjectManagement} from "./src/hooks/use-project-management";
      import {useChatSessions} from "./src/hooks/use-chat-sessions";
      await i18n.use(initReactI18next).init({lng:"en-US",resources:{"en-US":{translation:en}}});
      const projects = ${JSON.stringify(projects)};
      const noop = () => {};
      function Fixture(){
        const [activeProjectPath,setActiveProjectPath] = useState(projects[0].path);
        const management = useProjectManagement({
          endpoint:location.origin, runtimeConnected:true, activeProjectPath, projects,
          refresh:async()=>{}, startRuntime:async()=>null, setError:noop,
          onProjectAdded:noop, onActiveProjectHidden:noop,
        });
        const paths = useMemo(()=>management.projectItems.map(project=>project.path),[management.projectItems]);
        const sessions = useChatSessions({
          endpoint:location.origin, runtimeConnected:true, projectPrefsReady:management.projectPrefsReady,
          projectPaths:paths, customProjectPaths:management.projectPrefs.customPaths,
          activeProjectPath, setActiveProjectPath, activeView:"chat", setActiveView:noop,
          setError:noop, expandProjectGroup:noop, initialChatState:{chats:[],activeChatId:"chat-0"},
        });
        return <>
          {management.projectItems.map(project=><button key={project.path} onClick={()=>management.setProjectMenu({
            projectPath:project.path,x:20,y:window.innerHeight-20,
          })}>Menu {project.path}</button>)}
          <div data-testid="active">{activeProjectPath}</div>
          <div data-testid="chats">{sessions.sidebarChats.map(chat=>chat.id).join(",")}</div>
          <SidebarMenus projectMenu={management.projectMenu} chatMenu={null} selectionMenu={null}
            deleteTargetId="" chats={sessions.sidebarChats} customPathSet={management.customPathSet}
            collapsedProjects={management.collapsedProjects} pinnedProjectSet={management.pinnedProjectSet}
            selectionMenuRef={null} onCloseProjectMenu={()=>management.setProjectMenu(null)}
            onTogglePinProject={noop} onOpenProjectFolder={noop} onNewConversation={noop}
            onStartRenameProject={noop} onToggleProjectCollapse={noop} onArchiveProjectChats={noop}
            onHideProject={noop} onRemoveCustomProject={noop} onAskInNewSession={noop}
            onAddSelectionToComposer={noop} onCloseChatMenu={noop} onTogglePinChat={noop}
            onToggleTestChat={noop} onStartRenameChat={noop} onDeleteChat={noop}
            onCancelDeleteChat={noop} onConfirmDeleteChat={noop}
            canRecycleProject={management.canRecycleProject}
            onRecycleProject={(path,confirmation)=>sessions.recycleProjectWithChats(path,()=>management.recycleProject(path,confirmation))}
          />
        </>;
      }
      createRoot(document.getElementById("root")).render(<Fixture/>);
    `,
  },
  bundle: true, write: false, format: "esm", jsx: "automatic",
  define: { "process.env.NODE_ENV": '"test"' },
});

function json(res, status, body) {
  res.writeHead(status, { "Content-Type": "application/json" });
  res.end(JSON.stringify(body));
}

const server = createServer(async (req, res) => {
  const url = new URL(req.url, "http://localhost");
  if (url.pathname === "/fixture.js") {
    res.setHeader("Content-Type", "text/javascript");
    res.end(bundle.outputFiles[0].text);
  } else if (url.pathname === "/api/app/projects/prefs") {
    json(res, 200, { customPaths: [], customProjects: [], hiddenPaths: [] });
  } else if (url.pathname === "/api/app/chats" && req.method === "GET") {
    const visible = projectRecycled ? chats.slice(1) : chats;
    if (url.searchParams.get("catalog") === "1") {
      const entries = visible.map(({ items, ...chat }) => ({
        ...chat, sourceProjectPath: chat.projectPath, sourceStoreId: chat.id,
      }));
      json(res, 200, { ok: true, entries, sources: [], pendingChatIds: [], recoveries: [] });
    } else {
      const chat = visible.find(({ id }) => url.searchParams.get("chatId") === id);
      json(res, 200, {
        ok: true, chats: chat ? [chat] : [], chatDigests: {}, chatMetadataShapes: {},
        hasOlder: false, nextBefore: null,
      });
    }
  } else if (url.pathname === "/api/projects/recycle" && req.method === "POST") {
    let raw = "";
    for await (const data of req) raw += data;
    const body = JSON.parse(raw);
    requests.push(body);
    if (requests.length === 1) {
      json(res, 409, { detail: "Close Unity Editor before deleting this project." });
    } else {
      projectRecycled = true;
      json(res, 200, { ok: true, path: body.path });
    }
  } else if (url.pathname === "/api/app/chats/single" && req.method === "POST") {
    chatWrites.push(url.pathname);
    json(res, 500, { detail: "Unexpected chat write" });
  } else if (url.pathname === "/") {
    res.setHeader("Content-Type", "text/html");
    res.end('<div id="root"></div><script type="module" src="/fixture.js"></script>');
  } else {
    json(res, 404, {});
  }
});

let browser;
try {
  await new Promise((resolve) => server.listen(0, "127.0.0.1", resolve));
  browser = await chromium.launch({ headless: true, channel: process.env.VRCFORGE_BROWSER_CHANNEL || "msedge" });
  const page = await browser.newPage();
  await page.goto(`http://127.0.0.1:${server.address().port}`);
  await page.waitForFunction(() => document.querySelector('[data-testid="chats"]').textContent.includes("chat-1"));
  const openDialog = async () => {
    await page.getByRole("button", { name: `Menu ${target.path}` }).click();
    await page.getByRole("menuitem", { name: "Delete Project" }).click();
    await dialog.waitFor({ state: "visible" });
  };
  const dialog = page.getByRole("dialog", { name: "Delete Project" });
  const input = dialog.getByLabel("Type delete to confirm.");
  const confirm = dialog.getByRole("button", { name: "Delete Project" });
  await openDialog();
  assert.equal(await dialog.getByText(target.path).isVisible(), true);
  assert.equal(await confirm.isDisabled(), true);
  await input.fill("Delete");
  await input.press("Enter");
  assert.equal(await confirm.isDisabled(), true);
  await dialog.getByRole("button", { name: "Cancel" }).click();
  await dialog.waitFor({ state: "hidden" });
  assert.deepEqual(requests, []);

  await openDialog();
  await input.fill("delete");
  await confirm.click();
  await dialog.getByRole("alert").waitFor();
  assert.match(await dialog.getByRole("alert").textContent(), /Close Unity Editor/);
  assert.equal(await confirm.isDisabled(), true);
  assert.equal(await page.getByTestId("active").textContent(), target.path);
  assert.match(await page.getByTestId("chats").textContent(), /chat-0/);
  await input.fill("delete");
  await confirm.click();
  await dialog.waitFor({ state: "hidden" });
  await page.getByRole("button", { name: `Menu ${target.path}` }).waitFor({ state: "hidden" });
  assert.equal(await page.getByRole("button", { name: `Menu ${retained.path}` }).isVisible(), true);
  assert.equal(await page.getByTestId("active").textContent(), "");
  assert.equal(await page.getByTestId("chats").textContent(), "chat-1");
  assert.deepEqual(requests, [
    { path: target.path, confirmation: "delete" }, { path: target.path, confirmation: "delete" },
  ]);
  assert.deepEqual(chatWrites, []);
  console.log("PASS project recycle dialog: literal confirmation, rejection, exact path, and chat/list removal");
} finally {
  await browser?.close();
  await new Promise((resolve) => server.close(resolve));
}
