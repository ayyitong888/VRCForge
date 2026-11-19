// Regression for the production projectDisplayLabel hook behavior.
// Two projects deliberately share a display name; the fixture never supplies
// its own label formatter, so suffixes must come from useProjectManagement.
import assert from "node:assert/strict";
import { createServer } from "node:http";
import { readFileSync } from "node:fs";
import { dirname } from "node:path";
import { createRequire } from "node:module";
import { build } from "esbuild";
import postcss from "postcss";
import tailwindcss from "tailwindcss";

const require = createRequire(import.meta.url);
const { chromium } = require(process.env.VRCFORGE_PLAYWRIGHT_PATH || "playwright");
const { css } = await postcss([tailwindcss("./tailwind.config.ts")]).process("@tailwind utilities;", { from: undefined });
const longProjectName = "A project title long enough to scroll across the sidebar";
const longChatName = "A conversation title long enough to scroll across the sidebar";
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
      import { SidebarChat, SidebarProject } from "./src/components/sidebar/sidebar";
      import { AppTooltipLayer } from "./src/components/ui/app-tooltip";
      import { useProjectManagement } from "./src/hooks/use-project-management";
      i18n.use(initReactI18next).init({lng:"en-US",resources:{"en-US":{translation:en}}});
      const projects = [
        {name:"VRCForge",path:"C:/fixture/left/VRCForge",projectType:"unity",sources:["fixture"]},
        {name:"VRCForge",path:"C:/fixture/right/VRCForge",projectType:"unity",sources:["fixture"]},
      ];
      const noop = () => {};
      window.projectIndexRequests = 0;
      const request = window.fetch.bind(window);
      window.fetch = (url, options) => {
        if (new URL(url, location.origin).pathname === "/api/app/project-index/scan") {
          window.projectIndexRequests += 1;
        }
        return request(url, options);
      };
      function Fixture(){
        const management = useProjectManagement({
          endpoint: location.origin,
          runtimeConnected: true,
          activeProjectPath: projects[0].path,
          projects,
          refresh: async () => {},
          refreshProjects: async () => {},
          startRuntime: async () => null,
          setError: noop,
          onProjectAdded: noop,
          onActiveProjectHidden: noop,
        });
        const [activeChat, setActiveChat] = React.useState("a");
        return <main style={{ width: 190 }} data-project-prefs-ready={management.projectPrefsReady}>{management.projectItems.map((project) =>
          <div data-testid={project.path.includes("left") ? "left" : "right"} key={project.path}>
            <SidebarProject name={management.projectDisplayLabel(project)} meta="6000.0.1f1" onClick={noop} />
          </div>
        )}{["a", "b"].map(id => <SidebarChat key={id} chatId={id} title={id.toUpperCase()} active={activeChat===id}
          onClick={() => setActiveChat(id)} />)}
          <SidebarProject name="Short project" onClick={noop} />
          <SidebarProject name={${JSON.stringify(longProjectName)}} onClick={noop} onOpenMenu={noop} />
          <SidebarChat title={${JSON.stringify(longChatName)}} onClick={noop} />
          <AppTooltipLayer />
        </main>;
      }
      createRoot(document.getElementById("root")).render(<Fixture/>);
    `,
  },
  plugins: process.env.VRCFORGE_PROJECT_HOOK_SOURCE ? [{
    name: "original-project-hook",
    setup(builder) {
      builder.onLoad({ filter: /use-project-management\.ts$/ }, args => ({
        contents: readFileSync(process.env.VRCFORGE_PROJECT_HOOK_SOURCE, "utf8"),
        loader: "ts", resolveDir: dirname(args.path),
      }));
    },
  }] : [],
  bundle: true,
  write: false,
  format: "iife",
  jsx: "automatic",
  define: { "process.env.NODE_ENV": '"test"' },
});

const server = createServer((request, response) => {
  if (request.url === "/fixture.js") {
    response.setHeader("Content-Type", "text/javascript");
    response.end(bundle.outputFiles[0].text);
    return;
  }
  if (request.url === "/") {
    response.setHeader("Content-Type", "text/html");
    response.end('<style>' + css + '</style><div id="root"></div><script src="/fixture.js"></script>');
    return;
  }
  if (request.url === "/api/app/projects/prefs") {
    response.setHeader("Content-Type", "application/json");
    response.end(JSON.stringify({ ok: true, customPaths: [], customProjects: [], hiddenPaths: [] }));
    return;
  }
  if (request.url === "/api/app/project-index/scan") {
    response.setHeader("Content-Type", "application/json");
    response.end(JSON.stringify({ ok: true, schema: "vrcforge.project_memory_index.v1" }));
    return;
  }
  response.statusCode = 404;
  response.end();
});

let browser;
try {
  await new Promise((resolve) => server.listen(0, "127.0.0.1", resolve));
  browser = await chromium.launch({ headless: true, channel: process.env.VRCFORGE_BROWSER_CHANNEL || "msedge" });
  const page = await browser.newPage();
  await page.clock.install();
  await page.goto(`http://127.0.0.1:${server.address().port}/`);
  await page.getByTestId("left").waitFor({ timeout: 3000 });
  const left = await page.getByTestId("left").getByRole("button").innerText();
  const right = await page.getByTestId("right").getByRole("button").innerText();
  assert.equal(left, "VRCForge (left/VRCForge)");
  assert.equal(right, "VRCForge (right/VRCForge)");
  assert.notEqual(left, right, "same-name projects must remain distinguishable");
  assert.equal(await page.locator('[aria-current="page"]').innerText(), "A");
  await page.getByRole("button", { name: "B", exact: true }).click();
  assert.equal(await page.locator('[aria-current="page"]').innerText(), "B");
  console.log("same-name sidebar projects remain distinct and expose the current conversation");
  await page.locator('main[data-project-prefs-ready="true"]').waitFor({ timeout: 3000 });
  await page.clock.runFor(1500);
  assert.equal(await page.evaluate(() => window.projectIndexRequests), 0, "opening the project list must not start an unused project index scan");
  console.log("PASS project labels stay distinct and connecting does not start an unused project index scan");

  await page.clock.pauseAt(await page.evaluate(() => Date.now() + 1000));
  const moveTo = async (label) => {
    const { x, y, width, height } = await label.boundingBox();
    await page.mouse.move(x + width / 2, y + height / 2);
  };
  const scroll = (label) => label.evaluate((node) => node.scrollLeft);
  const visibleTooltip = page.locator('[data-app-tooltip-layer]:visible');
  const projectLabel = page.getByText(longProjectName, { exact: true });
  const chatLabel = page.getByText(longChatName, { exact: true });
  const projectOverflow = await projectLabel.evaluate((node) => node.scrollWidth - node.clientWidth);
  assert.ok(projectOverflow > 25, "the project test uses a clipped title");
  await moveTo(projectLabel);
  assert.equal(await visibleTooltip.count(), 0, "hovering a project name shows no tooltip");
  await page.clock.runFor(450);
  assert.equal(await scroll(projectLabel), 0, "wait half a second before moving");

  await page.clock.runFor(300);
  const outboundDistance = await scroll(projectLabel);
  assert.ok(outboundDistance >= 7 && outboundDistance <= 11, "move at about 36 px/s");
  await page.clock.runFor(Math.ceil(projectOverflow / 0.036) - 250 + 80);
  assert.equal(await scroll(projectLabel), projectOverflow, "reveal the final characters");
  await page.clock.runFor(250);
  assert.equal(await scroll(projectLabel), projectOverflow, "pause at the end");
  await page.clock.runFor(570);
  const returnDistance = projectOverflow - await scroll(projectLabel);
  assert.ok(Math.abs(returnDistance - outboundDistance) <= 2, "return at the same speed");
  await page.clock.runFor(Math.ceil(projectOverflow / 0.036));
  assert.equal(await scroll(projectLabel), 0, "restore the leading characters after one cycle");

  const restingChatWidth = await chatLabel.evaluate((node) => node.clientWidth);
  await moveTo(chatLabel);
  assert.ok(await chatLabel.evaluate((node) => node.clientWidth) < restingChatWidth, "hovering shows the chat actions beside the title");
  assert.equal(await visibleTooltip.count(), 0, "hovering a conversation name shows no tooltip");
  await page.clock.runFor(750);
  assert.ok(await scroll(chatLabel) > 0, "long chat titles use the same behavior");
  for (const name of ["Short project", "B"]) {
    const fittedLabel = page.getByText(name, { exact: true });
    await moveTo(fittedLabel);
    assert.equal(await visibleTooltip.count(), 0, "fitted names show no tooltip");
    assert.equal(await scroll(chatLabel), 0, "leaving a title restores its normal view");
    await page.clock.runFor(750);
    assert.equal(await fittedLabel.evaluate((node) => node.style.textOverflow), "", "fitted names never animate");
  }
  await page.mouse.move(600, 50);
  for (const name of [longProjectName, longChatName, "Short project", "B"]) {
    await page.keyboard.press("Tab");
    const button = page.getByRole("button", { name, exact: true });
    await button.focus();
    assert.equal(await button.evaluate((node) => node.matches(":focus-visible")), true);
    assert.equal(await visibleTooltip.count(), 0, "focusing a name shows no tooltip");
    assert.equal(await button.getAttribute("title"), null, "the name has no native tooltip");
  }
  for (const label of [projectLabel, chatLabel]) {
    await moveTo(label);
    const action = label.locator("../..").locator("button[data-app-tooltip]").first();
    await moveTo(action);
    assert.equal(await visibleTooltip.innerText(), await action.getAttribute("data-app-tooltip"));
  }
  await page.emulateMedia({ reducedMotion: "reduce" });
  for (const label of [projectLabel, chatLabel]) {
    await moveTo(label);
    await page.clock.runFor(750);
    assert.equal(await scroll(label), 0, "reduced motion keeps the name still");
  }
  console.log("PASS clipped sidebar names scroll after a delay, pause, and return at the same speed");
} finally {
  await browser?.close();
  await new Promise((resolve) => server.close(resolve));
}
