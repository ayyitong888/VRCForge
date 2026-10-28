import assert from "node:assert/strict";
import { createServer } from "node:http";
import { createRequire } from "node:module";
import { resolve } from "node:path";
import { build } from "esbuild";

const root = resolve(import.meta.dirname, "..");
const require = createRequire(import.meta.url);
const { chromium } = require(process.env.VRCFORGE_PLAYWRIGHT_PATH || "playwright");
const initialTasks = [
  { id: "running-child", status: "running", canResume: true, childSessionId: "shared-child-context", role: "custom_reviewer", displayName: "Running child", task: "Still working." },
  { id: "no-resume", status: "failed", canResume: false, role: "custom_reviewer", displayName: "Retry-only child", task: "Retry creates a new task." },
  { id: "eligible-child", status: "completed", canResume: true, childSessionId: "shared-child-context", role: "custom_reviewer", displayName: "Eligible follow-up", task: "Check the first report." },
];
const bundled = await build({
  stdin: { resolveDir: root, loader: "tsx", contents: `
    import React, {useState} from 'react';
    import {createRoot} from 'react-dom/client';
    import {SubAgentWorkspaceSurface} from './src/components/subagents/sub-agent-workspace-surface';
    import {resumeSubAgent} from './src/lib/api/sub-agents';
    import i18n from './src/i18n';
    import {initReactI18next} from 'react-i18next';
    import messages from './src/locales/en-US.json';
    const initialTasks = ${JSON.stringify(initialTasks)};
    window.apiEndpoint = window.location.origin;
    window.resumeRequests = [];
    function Fixture() {
      const [tasks,setTasks] = useState(initialTasks);
      const [selected,setSelected] = useState(initialTasks[0]);
      const busy = new Set();
      const noop = () => {};
      async function onResume(id,task) {
        const response = await resumeSubAgent(window.apiEndpoint,id,{task});
        window.resumeRequests.push({id,task});
        setTasks(current=>[response.task,...current.filter(item=>item.id!==response.task.id)]);
        setSelected(response.task);
        return true;
      }
      return <SubAgentWorkspaceSurface tasks={tasks} roles={[{id:'custom_reviewer',title:'Custom reviewer title'}]} selected={selected}
        onSelect={id=>setSelected(tasks.find(item=>item.id===id))} onCancel={noop} onRetry={noop} onResume={onResume}
        onMerge={noop} onIgnoreNotification={noop} onAdoptNextAction={noop} busyTaskIds={busy} onClose={noop}/>;
    }
    i18n.use(initReactI18next).init({lng:'en-US',resources:{'en-US':{translation:messages}},interpolation:{escapeValue:false}})
      .then(()=>createRoot(document.getElementById('root')).render(<Fixture/>));
  ` }, bundle: true, write: false, platform: "browser", format: "iife", jsx: "automatic",
});

let resumeRequest = null;
const server = createServer(async (req, res) => {
  if (req.method === "GET" && req.url === "/") {
    res.writeHead(200, { "Content-Type": "text/html; charset=utf-8" });
    res.end(`<div id="root"></div><script>${bundled.outputFiles[0].text.replaceAll("</script", "<\\/script")}</script>`);
    return;
  }
  if (req.method === "POST" && req.url === "/api/app/sub-agents/eligible-child/resume") {
    let body = "";
    for await (const chunk of req) body += chunk;
    resumeRequest = { method: req.method, url: req.url, body: JSON.parse(body) };
    const resumed = {
      id: "continued-child", status: "running", canResume: false,
      resumeOf: "eligible-child", childSessionId: "shared-child-context",
      role: "custom_reviewer", displayName: "Eligible follow-up", task: resumeRequest.body.task,
    };
    res.writeHead(200, { "Content-Type": "application/json" });
    res.end(JSON.stringify({ ok: true, task: resumed }));
    return;
  }
  res.writeHead(404, { "Content-Type": "application/json" });
  res.end(JSON.stringify({ detail: "unexpected request" }));
});

let browser;
try {
  await new Promise((done) => server.listen(0, "127.0.0.1", done));
  browser = await chromium.launch({ channel: "msedge", headless: true });
  const page = await browser.newPage();
  const errors = [];
  page.on("pageerror", (error) => errors.push(error.message));
  await page.goto(`http://127.0.0.1:${server.address().port}`);
  const surface = page.locator("[data-vrcforge-subagent-workspace-surface]");
  assert.equal(await surface.getByRole("button", { name: "Ask a follow-up", exact: true }).count(), 0,
    "running task must not show follow-up controls even when backend metadata says resumable");

  await surface.getByRole("button", { name: /Retry-only child/ }).click();
  assert.equal(await surface.getByRole("button", { name: "Ask a follow-up", exact: true }).count(), 0,
    "task without canResume metadata must not show follow-up controls");
  assert.equal(await surface.getByRole("button", { name: "Retry as new task", exact: true }).count(), 1,
    "retry remains clearly a separate new-task action");

  await surface.getByRole("button", { name: /Eligible follow-up/ }).click();
  await surface.getByText("Custom reviewer title", { exact: true }).waitFor();
  const question = surface.getByLabel("New question for this child");
  const submit = surface.getByRole("button", { name: "Ask a follow-up", exact: true });
  assert.equal(await submit.isDisabled(), true, "an explicit new question is required before resume");
  await question.fill("Compare the latest scan with the earlier finding.");
  await submit.click();
  await page.waitForFunction(() => window.resumeRequests.length === 1);
  await page.waitForFunction(() => document.querySelector("[data-vrcforge-subagent-resume]") === null);
  assert.deepEqual(resumeRequest, {
    method: "POST",
    url: "/api/app/sub-agents/eligible-child/resume",
    body: { task: "Compare the latest scan with the earlier finding." },
  });
  assert.deepEqual(await page.evaluate(() => window.resumeRequests), [
    { id: "eligible-child", task: "Compare the latest scan with the earlier finding." },
  ]);
  assert.equal(await surface.getByRole("button", { name: "Ask a follow-up", exact: true }).count(), 0,
    "resumed running child has no follow-up form");
  assert.deepEqual(errors, []);
  console.log("PASS: browser requires a new question, gates by canResume/status, posts through the fake backend, preserves custom role title, and keeps retry distinct.");
} finally {
  await browser?.close();
  await new Promise((done) => server.close(done));
}
