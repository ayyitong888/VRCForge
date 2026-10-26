import assert from "node:assert/strict";
import { createRequire } from "node:module";
import { createServer } from "node:http";
import { execFileSync } from "node:child_process";
import { readFile } from "node:fs/promises";
import { build } from "esbuild";
import postcss from "postcss";
import tailwindcss from "tailwindcss";

const { chromium } = createRequire(import.meta.url)(process.env.VRCFORGE_PLAYWRIGHT_PATH || "playwright");
const baseline = process.env.VRCFORGE_TIMELINE_BASELINE_REF;
const css = (await postcss([tailwindcss("./tailwind.config.ts")]).process((await readFile("src/theme.css", "utf8")) + "\n@tailwind base; @tailwind components; @tailwind utilities;", { from: undefined })).css;
const result = await build({ stdin: { resolveDir: process.cwd(), loader: "tsx", contents: `
import React from 'react'; import {createRoot} from 'react-dom/client';
import {ConversationCard} from './src/components/chat/conversation-card'; import {initializeI18n} from './src/i18n';
const e=(id,sequence,kind,payload)=>({id,sequence,kind,payload,timestamp:new Date(2026,0,1,0,0,sequence).toISOString()});
const timeline=[e('progress',0,'assistant',{summary:'Inspecting outfit bindings.',phase:'commentary'}),
e('a',1,'tool_call',{actionId:'a',tool:'vrcforge_list_directory',status:'started'}),e('ar',2,'tool_result',{actionId:'a',tool:'vrcforge_list_directory',status:'ok'}),
e('b',3,'tool_call',{actionId:'b',tool:'vrcforge_read_text_file',status:'started'}),e('br',4,'tool_result',{actionId:'b',tool:'vrcforge_read_text_file',status:'failed',summary:'Historical read failure'}),
e('final',5,'assistant',{summary:'The follow-up inspection completed.',phase:'final_answer',status:'done'})];
const durationTimeline=(seconds)=>[
 e('duration-start-'+seconds,0,'tool_call',{actionId:'duration-'+seconds,tool:'vrcforge_list_directory',status:'started'}),
 {...e('duration-end-'+seconds,seconds,'tool_result',{actionId:'duration-'+seconds,tool:'vrcforge_list_directory',status:'ok'}),sequence:1},
];
const item={id:'history',type:'agent',response:{clientTurnId:'history-turn',plan:{nextStep:'done',reply:'The follow-up inspection completed.'},timeline}};
const durationItem=(id,seconds)=>({id,type:'agent',response:{clientTurnId:id+'-turn',plan:{nextStep:'done',reply:'duration fixture'},timeline:durationTimeline(seconds)}});
const pending={id:'pending',type:'streaming',clientTurnId:'pending-turn',text:'',timeline:[e('p',0,'tool_call',{actionId:'p',tool:'vrcforge_write_file',status:'prepared'})]};
const recovered={id:'recovered',type:'streaming',clientTurnId:'recovered-turn',text:'',timeline:[e('r1',0,'tool_call',{actionId:'r1',tool:'unity_bind_execution_target',status:'started'}),e('r1r',1,'tool_result',{actionId:'r1',tool:'unity_bind_execution_target',status:'ok'}),e('r2',2,'tool_call',{actionId:'r2',tool:'unity_get_gameobject',status:'started'}),e('r2r',190,'tool_result',{actionId:'r2',tool:'unity_get_gameobject',status:'ok'})]};
const interrupted={id:'interrupted',type:'agent',response:{clientTurnId:'interrupted-turn',status:'interrupted',continuationSource:'foreground_interrupted',plan:{nextStep:'interrupted',reply:'Accepted partial model text.'},timeline:[e('earlier',0,'assistant',{summary:'Earlier public update.',phase:'commentary'})]}};
void initializeI18n().then(()=>createRoot(document.getElementById('root')).render(<><section id="history"><ConversationCard item={item}/></section><section id="pending"><ConversationCard item={pending}/></section><section id="interrupted"><ConversationCard item={interrupted}/></section><section id="recovered"><ConversationCard item={recovered}/></section><section id="duration-59m59s"><ConversationCard item={durationItem('duration-59m59s',3599)}/></section><section id="duration-1h"><ConversationCard item={durationItem('duration-1h',3600)}/></section><section id="duration-1h10m45s"><ConversationCard item={durationItem('duration-1h10m45s',4245)}/></section></>));
` }, bundle: true, write: false, format: "iife", platform: "browser", jsx: "automatic", plugins: baseline ? [{name:'frozen-timeline-baseline',setup(plugin){plugin.onLoad({filter:/conversation-timeline\.tsx$/},()=>({loader:'tsx',contents:execFileSync('git',['show',baseline+':src/components/chat/conversation-timeline.tsx'],{encoding:'utf8'})}));}}] : [] });
const server = createServer((_, response) => { response.setHeader("Content-Type", "text/html; charset=utf-8"); response.end(`<style>${css}</style><div id="root" style="width:720px"></div><script>${result.outputFiles[0].text.replaceAll("</script", "<\\/script")}</script>`); });
await new Promise((resolve) => server.listen(0, "127.0.0.1", resolve));
let browser;
try {
  browser = await chromium.launch({headless:true,channel:process.env.VRCFORGE_BROWSER_CHANNEL || "msedge"});
  const page = await browser.newPage(); const errors=[]; page.on('pageerror',e=>errors.push(e.message));
  await page.goto(`http://127.0.0.1:${server.address().port}`);
  const recovered=page.locator('#recovered');
  await recovered.locator('[data-agent-turn-process-title]').waitFor();
  assert.match(await recovered.locator('[data-agent-turn-process-title]').innerText(), /3m 10s/, 'restored timeline keeps its known elapsed duration in the process heading');
  assert.match(await page.locator('#duration-59m59s [data-agent-turn-process-title]').innerText(), /59m 59s/, 'sub-hour duration keeps minutes and seconds');
  assert.match(await page.locator('#duration-1h [data-agent-turn-process-title]').innerText(), /1h0m/, 'one-hour duration switches to hours and minutes');
  assert.match(await page.locator('#duration-1h10m45s [data-agent-turn-process-title]').innerText(), /1h10m/, 'hour-plus duration omits seconds');
  assert.doesNotMatch(await recovered.locator('[data-agent-tool-group-title]').first().innerText(), /unity_|vrcforge_/, 'folded tool headings stay readable while exact tool names remain in detail');
  const interrupted=page.locator('#interrupted'); await interrupted.locator('[data-vrcforge-agent-client-turn]').waitFor();
  assert.ok((await interrupted.innerText()).includes('Accepted partial model text.'),'transport interruption retains accepted partial words outside a false final answer');
  assert.equal(await interrupted.locator('[data-agent-transport-interrupted]').count(),1,'transport interruption has an unconfirmed-status notice');
  assert.equal(await interrupted.locator('[data-agent-turn-cancelled]').count(),0,'transport interruption does not claim backend cancellation');
  const history=page.locator('#history'); const processFold=history.locator('[data-agent-turn-process-group]');
  await processFold.waitFor();
  assert.equal(await processFold.locator('[data-agent-turn-process-summary]').innerText(),'Inspecting outfit bindings.','collapsed process retains the public action summary');
  assert.match(await processFold.locator('[data-agent-operation-summary-status]').innerText(),/1/,'collapsed process discloses historical failures');
  assert.ok((await history.innerText()).includes('The follow-up inspection completed.'),'history failure does not hide the final result');
  assert.equal(await history.locator('[role="alert"]').count(),0,'historical failure does not declare a new current blocker');
  await processFold.locator('button').first().click();
  const group=processFold.locator('[data-agent-tool-call-group]');
  const title=await group.locator('[data-agent-tool-group-title]').innerText();
  assert.doesNotMatch(title,/Called 2 tools|调用了 2 个工具/,'group heading describes operations rather than only count');
  assert.match(await group.locator('[data-agent-operation-summary-status]').innerText(),/1/,'group preserves failure records while collapsed');
  await group.locator('button').first().click(); await group.locator('button.group\\/run').last().click();
  assert.ok((await group.locator('[data-agent-invocation-output-card]').innerText()).includes('Historical read failure'),'exact error detail stays inspectable');
  const pending=page.locator('#pending');
  assert.match(await pending.locator('[data-agent-turn-process-title]').innerText(), /Handled in —/, 'missing duration stays explicitly unknown instead of changing the process heading');
  const pendingGroup=pending.locator('[data-agent-tool-call-group]'); await pendingGroup.locator('button').first().click();
  await pendingGroup.locator('button.group\\/run').click();
  assert.doesNotMatch(await pendingGroup.locator('[data-agent-invocation-output-card]').innerText(),/Done|已完成/,'prepared request is not a completed write');
  assert.deepEqual(errors,[]); console.log('work summary, historical failure, original detail and prepared stage browser passed');
} finally { await browser?.close(); await new Promise(resolve=>server.close(resolve)); }
