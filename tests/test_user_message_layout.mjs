// Real card + production Tailwind styles. Synthetic content only; no app/provider calls.
import assert from "node:assert/strict";
import { readFileSync, mkdirSync } from "node:fs";
import { createServer } from "node:http";
import { createRequire } from "node:module";
import { resolve } from "node:path";
import { execFileSync } from "node:child_process";
import { build } from "esbuild";
import postcss from "postcss";
import tailwindcss from "tailwindcss";

const root = resolve(import.meta.dirname, "..");
const require = createRequire(import.meta.url);
const { chromium } = require(process.env.VRCFORGE_PLAYWRIGHT_PATH || "playwright");
const baseline = process.argv.includes("--baseline");
const baselineTimeline = process.argv.includes("--baseline-timeline");
const cardPath = resolve(root, "src/components/chat/conversation-card.tsx");
const oldCard = baseline ? execFileSync("git", ["show", "HEAD:src/components/chat/conversation-card.tsx"], { cwd: root, encoding: "utf8" }) : null;
const compiled = await build({
  stdin: { resolveDir: root, loader: "tsx", contents: `
    import React, {useState} from 'react';
    import {createRoot} from 'react-dom/client';
    import {ConversationCard} from './src/components/chat/conversation-card';
    import i18n from './src/i18n';
    import {initReactI18next} from 'react-i18next';
    import messages from './src/locales/en-US.json';
    i18n.use(initReactI18next).init({lng:'en-US',resources:{'en-US':{translation:messages}},interpolation:{escapeValue:false}});
    const text = '请检查并保留完整原文。\\n\\n' + 'animator/layerType/pathMode/mergeAnimatorMode/deleteAttachedAnimator/matchAvatarWriteDefaults/layerPriority'.repeat(18)
      + '\\n\\n' + 'Paragraph with **formatted content**.\\n\\n'.repeat(20) + 'END_OF_ORIGINAL';
    const rich = '## Structured input\\n\\n' + '| Field | Value |\\n|---|---|\\n| long | ' + 'Property'.repeat(150) + ' |\\n\\n'
      + '\\\`\\\`\\\`json\\n' + 'x'.repeat(1000) + '\\n\\\`\\\`\\\`';
    window.original = text;
    const event = (id, sequence, kind, payload) => ({id,sequence,timestamp:'2026-10-13T00:00:0'+sequence+'Z',kind,payload});
    const timeline = [event('plan',0,'planner',{summary:'First inspect the exact source.'}),
      event('call',1,'tool_call',{actionId:'a',tool:'vrcforge_read_text_file',status:'started'}),
      event('result',2,'tool_result',{actionId:'a',tool:'vrcforge_read_text_file',status:'ok',summary:'SOURCE_CODE_OUTPUT'}),
      event('next',3,'planner',{summary:'The source is verified; checking references next.'}),
      event('call2',4,'tool_call',{actionId:'b',tool:'vrcforge_find_files',status:'started'})];
    function Fixture() {
      const [editing,setEditing] = useState(false);
      const [complete,setComplete] = useState(false);
      return <><button id="finish" onClick={()=>setComplete(true)}>Finish fixture turn</button><main id="history" style={{height:650, overflow:'auto', padding:24}}>
        <section id="long"><ConversationCard item={{id:'long',type:'user',text}} canEdit editing={editing} editingText={text}
          onCopyItem={item=>window.copied=item.text} onEditItem={()=>setEditing(true)} onEditItemCancel={()=>setEditing(false)}/></section>
        <section id="short"><ConversationCard item={{id:'short',type:'user',text:'A short message.'}}/></section>
        <section id="token"><ConversationCard item={{id:'token',type:'user',text:'z'.repeat(500)}}/></section>
        <section id="lines"><ConversationCard item={{id:'lines',type:'user',text:Array.from({length:12},(_,i)=>'Line '+i).join('\\n')}}/></section>
        <section id="rich"><ConversationCard item={{id:'rich',type:'user',text:rich}}/></section>
        <section id="agent"><ConversationCard item={complete
          ? {id:'agent',type:'agent',response:{plan:{reply:'Final answer stays visible.',summary:'done',nextStep:'done'},timeline:[...timeline,event('final',5,'assistant',{summary:'Final answer stays visible.',status:'done'})]}}
          : {id:'stream',type:'streaming',text:'',timeline}}/></section>
      </main></>;
    }
    createRoot(document.getElementById('root')).render(<Fixture/>);
  ` },
  bundle: true, write: false, format: "iife", platform: "browser", jsx: "automatic",
  plugins: baseline || baselineTimeline ? [{ name: "original-component", setup(builder) {
    if (baseline) builder.onLoad({ filter: /conversation-card\.tsx$/ }, () => ({ contents: oldCard, loader: "tsx", resolveDir: resolve(cardPath, "..") }));
    if (baselineTimeline) builder.onLoad({ filter: /conversation-timeline\.tsx$/ }, () => ({
      contents: execFileSync("git", ["show", "HEAD:src/components/chat/conversation-timeline.tsx"], {cwd:root,encoding:"utf8"}),
      loader:"tsx", resolveDir:resolve(cardPath,".."),
    }));
  } }] : [],
});
const css = (await postcss([tailwindcss(resolve(root, "tailwind.config.ts"))]).process(readFileSync(resolve(root, "src/styles.css"), "utf8").replace('@import "./theme.css";', readFileSync(resolve(root, "src/theme.css"), "utf8")), { from: resolve(root, "src/styles.css") })).css;
// Test-owned ephemeral loopback HTTP server: synthetic fixture only, no authenticated services.
// Both it and the isolated headless browser are closed in finally.
const server = createServer((req, res) => {
  if (req.url !== "/") { res.writeHead(404); res.end(); return; }
  res.setHeader("Content-Type", "text/html; charset=utf-8");
  res.end(`<style>${css}</style><div id="root"></div><script>${compiled.outputFiles[0].text.replaceAll("</script", "<\\/script")}</script>`);
});
let browser;
try {
  await new Promise(done => server.listen(0, "127.0.0.1", done));
  browser = await chromium.launch({ channel: "msedge", headless: true });
  const page = await browser.newPage();
  const errors = [];
  page.on("pageerror", error => { errors.push(error.message); console.error(error.message); });
  await page.goto(`http://127.0.0.1:${server.address().port}`);
  await page.locator("#long").waitFor();
  const assertBounds = async id => {
    const box = await page.locator(id).evaluate(section => {
      const bounds = section.getBoundingClientRect();
      const bubble = section.querySelector('[data-user-message-bubble]') || section.querySelector('.bg-muted');
      const rect = bubble.getBoundingClientRect();
      return { left: rect.left >= bounds.left - 1, right: rect.right <= bounds.right + 1, width: rect.width <= bounds.width * .78 + 1, overflow: bubble.scrollWidth <= bubble.clientWidth + 1 };
    });
    assert.deepEqual(box, {left:true,right:true,width:true,overflow:true}, `${id} must remain within its row at ${page.viewportSize().width}px`);
  };
  for (const width of [1280, 768, 375]) {
    await page.setViewportSize({width,height:900});
    for (const id of ["#long", "#token", "#rich"]) await assertBounds(id);
    const disclosure = page.locator("#long details");
    assert.equal(await disclosure.count(), 1, "long user text has a disclosure");
    assert.equal(await disclosure.evaluate(e => e.open), false, "defaults collapsed");
    assert.ok((await disclosure.boundingBox()).height < 185, "collapsed preview stays short");
    await disclosure.locator("summary").focus();
    await page.keyboard.press("Enter");
    assert.equal(await disclosure.evaluate(e => e.open), true, "keyboard expands");
    assert.match(await disclosure.locator(".chat-markdown").innerText(), /END_OF_ORIGINAL$/);
    await assertBounds("#long");
    await page.keyboard.press("Space");
    assert.equal(await disclosure.evaluate(e => e.open), false, "keyboard collapses");
    await page.locator("#rich summary").click();
    await assertBounds("#rich");
    assert.equal(await page.locator("#rich table").count(), 1);
    assert.equal(await page.locator("#rich pre").count(), 1);
    await page.locator("#rich summary").click();
  }
  assert.equal(await page.locator("#short details, #token details, #agent [data-user-message-disclosure]").count(), 0);
  assert.equal(await page.locator("#lines details").count(), 1, "many short lines also collapse");
  await page.locator('#long button[title="Copy"]').click();
  assert.equal(await page.evaluate(() => window.copied === window.original), true, "copy receives the original unabridged item");
  await page.locator('#long button[title="Edit"]').click();
  assert.equal(await page.locator("#long textarea").inputValue(), await page.evaluate(() => window.original));
  await page.locator('#long button[title="Cancel"]').click();
  const live = page.locator('#agent');
  assert.equal(await live.locator('[data-agent-turn-process-group]').count(), 0, 'running process must be visible without opening a group');
  assert.equal(await live.getByText('First inspect the exact source.', {exact:true}).isVisible(), true);
  assert.equal(await live.getByText('The source is verified; checking references next.', {exact:true}).isVisible(), true);
  const liveText = await live.innerText();
  assert.ok(liveText.indexOf('First inspect') < liveText.indexOf('read_text_file'));
  assert.ok(liveText.indexOf('read_text_file') < liveText.indexOf('The source is verified'));
  const screenshot = process.env.VRCFORGE_USER_MESSAGE_SCREENSHOT;
  const captureTurn = async name => {
    if (!screenshot) return;
    mkdirSync(resolve(screenshot, ".."), {recursive:true});
    await page.setViewportSize({width:768,height:900});
    await live.screenshot({path:resolve(screenshot,"..",name)});
  };
  await captureTurn('turn-live.png');
  await live.getByRole('button', {name:'read_text_file',exact:true}).click();
  const output = live.locator('pre');
  assert.equal(await output.innerText(), 'SOURCE_CODE_OUTPUT');
  // Actual mouse selection, so the existing history mouseup quote handler can read it.
  await output.scrollIntoViewIfNeeded();
  const rect = await output.boundingBox();
  await page.mouse.move(rect.x + 13, rect.y + 16);
  await page.mouse.down();
  await page.mouse.move(rect.x + Math.min(rect.width - 13, 150), rect.y + 16, {steps:12});
  await page.mouse.up();
  assert.ok((await page.evaluate(()=>window.getSelection().toString())).length > 3, 'tool output supports native drag selection');
  await page.locator('#finish').click();
  assert.equal(await live.locator('[data-agent-turn-process-group]').count(), 1, 'terminal work has one folded process');
  assert.equal(await live.getByText('First inspect the exact source.', {exact:true}).count(), 0, 'terminal commentary defaults folded');
  assert.equal(await live.getByText('Final answer stays visible.', {exact:true}).isVisible(), true);
  await captureTurn('turn-completed.png');
  await live.locator('[data-agent-turn-process-group] > div > button').click();
  assert.equal(await live.getByText('First inspect the exact source.', {exact:true}).isVisible(), true);
  assert.equal(await live.getByText('Final answer stays visible.', {exact:true}).isVisible(), true);
  await captureTurn('turn-expanded.png');
  assert.deepEqual(errors, []);
  if (screenshot) {
    mkdirSync(resolve(screenshot, ".."), {recursive:true});
    await page.setViewportSize({width:768,height:900});
    await page.locator('#history').evaluate(e => e.scrollTop=0);
    await page.screenshot({path:screenshot});
  }
  console.log("PASS: real user cards at 1280/768/375px; bounded long tokens + Markdown table/code; collapsed preview, keyboard expand/collapse, full copy/edit, short/assistant unaffected.");
} finally {
  await browser?.close();
  await new Promise(done => server.close(done));
}
