import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';
import { createServer } from 'node:http';
import { createRequire } from 'node:module';
import { resolve } from 'node:path';
import { build } from 'esbuild';

const root = resolve(import.meta.dirname, '..');
const require = createRequire(import.meta.url);
const { chromium } = require(process.env.VRCFORGE_PLAYWRIGHT_PATH || 'playwright');
const snapshot = process.env.VRCFORGE_NOTIFICATION_TASK_SNAPSHOT;
const task = snapshot
  ? JSON.parse(readFileSync(snapshot, 'utf8')).tasks.find(task => task.status === 'completed' && !task.mergeDecision)
  : { id: 'review-task', revision: 4, parentChatId: 'parent-chat', status: 'completed', role: 'selected_context_review', displayName: 'Review', task: 'Review the selected passage.', result: { summaryText: 'Review ready.' } };
assert.ok(task);
const key = `${task.id}:${task.revision}:${task.parentChatId}`;
const bundled = await build({
  stdin: { resolveDir: root, loader: 'tsx', contents: `
    import React, {useState} from 'react';
    import {createRoot} from 'react-dom/client';
    import {SubAgentPanel} from './src/components/subagents/sub-agent-panel';
    import {SubAgentWorkspaceSurface} from './src/components/subagents/sub-agent-workspace-surface';
    import {loadSubAgentReviewNotificationIds,saveSubAgentReviewNotificationIds} from './src/lib/approval-notifications';
    import i18n from './src/i18n';
    import {initReactI18next} from 'react-i18next';
    import messages from './src/locales/en-US.json';
    const task = ${JSON.stringify(task)};
    window.originalTask = JSON.stringify(task);
    window.task = task;
    window.mutations = [];
    window.persisted = () => [...loadSubAgentReviewNotificationIds()];
    function Fixture() {
      const [open,setOpen] = useState(false);
      const mutate = (...args) => window.mutations.push(args);
      return <><SubAgentPanel tasks={[task]} loading={false} error="" onOpen={()=>setOpen(true)}/>
        {open && <SubAgentWorkspaceSurface tasks={[task]} selected={task} onSelect={()=>{}}
          onCancel={mutate} onRetry={mutate} onMerge={mutate} onAdoptNextAction={mutate}
          busyTaskIds={new Set()} onClose={()=>setOpen(false)} onIgnoreNotification={current=>{
            const ids=loadSubAgentReviewNotificationIds();
            ids.add(current.id+':'+current.revision+':'+current.parentChatId);
            saveSubAgentReviewNotificationIds(ids); setOpen(false);
          }}/>}</>;
    }
    i18n.use(initReactI18next).init({lng:'en-US',resources:{'en-US':{translation:messages}},interpolation:{escapeValue:false}}).then(()=>createRoot(document.getElementById('root')).render(<Fixture/>));
  ` }, bundle: true, write: false, platform: 'browser', format: 'iife', jsx: 'automatic',
});
const server = createServer((req,res)=>{
  res.setHeader('Content-Type','text/html; charset=utf-8');
  res.end(`<div id="root"></div><script>${bundled.outputFiles[0].text.replaceAll('</script','<\\/script')}</script>`);
});
let browser;
try {
  await new Promise(done=>server.listen(0,'127.0.0.1',done));
  browser=await chromium.launch({channel:'msedge',headless:true});
  const page=await browser.newPage();
  const errors=[];
  page.on('pageerror',error=>errors.push(error.message));
  await page.goto(`http://127.0.0.1:${server.address().port}`);
  await page.locator('[data-vrcforge-open-subagent-surface]').click();
  const surface=page.locator('[data-vrcforge-subagent-workspace-surface]');
  await surface.getByRole('button',{name:'Ignore this reminder',exact:true}).click();
  assert.equal(await surface.count(),0);
  assert.deepEqual(await page.evaluate(()=>window.persisted()),[key]);
  assert.deepEqual(await page.evaluate(()=>window.mutations),[],'ignore must not merge, retry, cancel or adopt');
  assert.equal(await page.evaluate(()=>JSON.stringify(window.task)===window.originalTask),true);
  await page.reload();
  await page.locator('[data-vrcforge-open-subagent-surface]').waitFor();
  assert.deepEqual(await page.evaluate(()=>window.persisted()),[key],'production storage survives page reload');
  assert.deepEqual(errors,[]);
  console.log('PASS: real panel -> workspace -> ignore, unchanged task, persisted exact identity after reload. Native notification lifecycle is not exercised.');
} finally {
  await browser?.close();
  await new Promise(done=>server.close(done));
}
