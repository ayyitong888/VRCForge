import assert from "node:assert/strict";
import { createServer } from "node:http";
import { createRequire } from "node:module";
import { build } from "esbuild";

const require = createRequire(import.meta.url);
const { chromium } = require(process.env.VRCFORGE_PLAYWRIGHT_PATH || "playwright");

const bundle = await build({
  stdin: {
    resolveDir: process.cwd(),
    loader: "tsx",
    contents: `
      import React, { useEffect, useState } from "react";
      import { createRoot } from "react-dom/client";
      import { useDashboardProjectSelection } from "./src/hooks/use-dashboard-project-selection";

      function Fixture() {
        const [projectPath, setProjectPath] = useState("/fixture/original");
        const [confirmedProjectPath, setConfirmedProjectPath] = useState("/fixture/original");
        const [projectType, setProjectType] = useState("unity");
        const [activeChatId, setActiveChatId] = useState("");
        const [runtimeConnected, setRuntimeConnected] = useState(true);
        const [navigations, setNavigations] = useState([]);
        useEffect(() => {
          const onSelected = (event) => setConfirmedProjectPath(event.detail);
          window.addEventListener("fixture-runtime-selected", onSelected);
          return () => window.removeEventListener("fixture-runtime-selected", onSelected);
        }, []);
        useDashboardProjectSelection({
          endpoint: "http://fixture",
          runtimeConnected,
          projectPath,
          projectType,
          activeChatId,
          confirmedProjectPath,
          onConfirmedProjectPath: (path) => {
            setNavigations((current) => [...current, path]);
            setProjectPath(path);
          },
          setBootstrap: () => undefined,
          setError: () => undefined,
        });
        return <>
          <button id="external-c" onClick={() => window.dispatchEvent(new CustomEvent("fixture-runtime-selected", { detail: "/fixture/external" }))}>external</button>
          <button id="pick-a" onClick={() => setProjectPath("/fixture/a")}>A</button>
          <button id="pick-b" onClick={() => setProjectPath("/fixture/b")}>B</button>
          <button id="disconnect" onClick={() => setRuntimeConnected(false)}>disconnect</button>
          <button id="connect" onClick={() => setRuntimeConnected(true)}>connect</button>
          <button id="existing-chat" onClick={() => setActiveChatId("chat-1")}>existing chat</button>
          <button id="general" onClick={() => setProjectType("general")}>general</button>
          <button id="clear-project" onClick={() => setProjectPath("")}>clear project</button>
          <pre id="state">{JSON.stringify({ projectPath, confirmedProjectPath, projectType, activeChatId, runtimeConnected, navigations })}</pre>
        </>;
      }
      createRoot(document.getElementById("root")).render(<Fixture />);
    `,
  },
  bundle: true,
  write: false,
  format: "iife",
  platform: "browser",
  plugins: [{
    name: "fixture-api",
    setup(buildApi) {
      buildApi.onResolve({ filter: /^\.\.\/lib\/api$/ }, () => ({ path: "fixture-api", namespace: "fixture-api" }));
      buildApi.onLoad({ filter: /^fixture-api$/, namespace: "fixture-api" }, () => ({
        loader: "ts",
        contents: `
          const calls = [];
          window.__fixtureSelectionCalls = calls;
          export async function refreshUnityReadiness() { return { health: {} }; }
          export async function selectUnityProject(_endpoint, projectPath) {
            calls.push(projectPath);
            if (projectPath === "/fixture/a") {
              return new Promise((resolve) => {
                window.__resolveFixtureA = () => {
                  window.dispatchEvent(new CustomEvent("fixture-runtime-selected", { detail: projectPath }));
                  resolve({ selectedProjectPath: projectPath });
                };
              });
            }
            window.dispatchEvent(new CustomEvent("fixture-runtime-selected", { detail: projectPath }));
            return { selectedProjectPath: projectPath };
          }
        `,
      }));
    },
  }],
});

const server = createServer((_request, response) => {
  response.setHeader("Content-Type", "text/html; charset=utf-8");
  response.end(`<!doctype html><div id="root"></div><script>${bundle.outputFiles[0].text.replaceAll("</script", "<\\/script")}</script>`);
});

let browser;
try {
  await new Promise((resolve) => server.listen(0, "127.0.0.1", resolve));
  browser = await chromium.launch({ channel: "msedge", headless: true });

  const readState = async (page) => JSON.parse(await page.locator("#state").textContent());
  const openFixture = async () => {
    const page = await browser.newPage();
    await page.goto(`http://127.0.0.1:${server.address().port}`);
    await page.locator("#state").waitFor();
    return page;
  };

  {
    const page = await openFixture();
    await page.locator("#external-c").click();
    await page.waitForFunction(() => JSON.parse(document.querySelector("#state").textContent).navigations.length === 1);
    const state = await readState(page);
    assert.equal(state.projectPath, "/fixture/external");
    assert.deepEqual(state.navigations, ["/fixture/external"]);
    assert.deepEqual(await page.evaluate(() => window.__fixtureSelectionCalls), []);
    await page.close();
  }

  {
    const page = await openFixture();
    await page.locator("#disconnect").click();
    await page.locator("#pick-b").click();
    assert.deepEqual(await page.evaluate(() => window.__fixtureSelectionCalls), []);
    await page.locator("#connect").click();
    await page.waitForFunction(() => (window.__fixtureSelectionCalls || []).includes("/fixture/b"));
    assert.deepEqual(await page.evaluate(() => window.__fixtureSelectionCalls), ["/fixture/b"]);
    await page.close();
  }

  {
    const page = await openFixture();
    await page.locator("#existing-chat").click();
    await page.locator("#external-c").click();
    await new Promise((resolve) => setTimeout(resolve, 30));
    let state = await readState(page);
    assert.equal(state.projectPath, "/fixture/original");
    assert.deepEqual(state.navigations, []);
    await page.locator("#general").click();
    await page.locator("#external-c").click();
    await new Promise((resolve) => setTimeout(resolve, 30));
    state = await readState(page);
    assert.equal(state.projectType, "general");
    assert.deepEqual(state.navigations, []);
    await page.close();
  }

  {
    const page = await openFixture();
    await page.locator("#pick-a").click();
    await page.waitForFunction(() => (window.__fixtureSelectionCalls || []).includes("/fixture/a"));
    await page.locator("#pick-b").click();
    await page.evaluate(() => window.__resolveFixtureA());
    await page.waitForFunction(() => (window.__fixtureSelectionCalls || []).includes("/fixture/b"));
    await new Promise((resolve) => setTimeout(resolve, 30));
    const state = await readState(page);
    assert.equal(state.projectPath, "/fixture/b", "an old A acknowledgement must not navigate back from B");
    assert.deepEqual(state.navigations, []);
    assert.deepEqual(await page.evaluate(() => window.__fixtureSelectionCalls), ["/fixture/a", "/fixture/b"]);
    await page.close();
  }

  for (const mode of ["general", "clear-project"]) {
    const page = await openFixture();
    await page.locator(`#${mode}`).click();
    await page.locator("#external-c").click();
    await new Promise((resolve) => setTimeout(resolve, 30));
    const state = await readState(page);
    assert.deepEqual(state.navigations, [], "an unbound or general task must remain unbound");
    assert.deepEqual(await page.evaluate(() => window.__fixtureSelectionCalls), []);
    if (mode === "clear-project") assert.equal(state.projectPath, "");
    await page.close();
  }

  console.log("PASS: external confirmed changes navigate only blank Unity tasks; own stale A acknowledgements cannot override B.");
} finally {
  await browser?.close();
  await new Promise((resolve) => server.close(resolve));
}
