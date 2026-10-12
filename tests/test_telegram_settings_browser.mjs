// Real component, i18n, CSS and HTTP helper; only the loopback backend is a fixture.
import assert from "node:assert/strict";
import { createServer } from "node:http";
import { createRequire } from "node:module";
import { readFileSync } from "node:fs";
import { resolve } from "node:path";
import { build } from "esbuild";
import postcss from "postcss";
import tailwindcss from "tailwindcss";
const require = createRequire(import.meta.url);
const { chromium } = require(process.env.VRCFORGE_PLAYWRIGHT_PATH || "playwright");
const source = `
import React from 'react';
import {createRoot} from 'react-dom/client';
import i18n from 'i18next';
import {initReactI18next} from 'react-i18next';
import {TelegramSettingsPanel} from './src/components/settings/telegram-settings-panel';
import {setAppSessionToken} from './src/lib/api/http';
await i18n.use(initReactI18next).init({lng:'en',resources:{en:{translation:{fixture:'fixture'}},zh:{translation:{fixture:'fixture'}}}});
setAppSessionToken('synthetic-app-auth');
let root=createRoot(document.getElementById('root'));
window.mountPanel=()=>root.render(<TelegramSettingsPanel endpoint={location.origin} selectedProjectPath="C:/CurrentProject"/>);
window.unmountPanel=()=>{root.unmount();root=createRoot(document.getElementById('root'));};
window.setLanguage=(language)=>i18n.changeLanguage(language);
window.mountPanel();
`;
const bundle = await build({stdin:{contents:source,loader:"tsx",resolveDir:process.cwd()},bundle:true,write:false,
  format:"esm",jsx:"automatic",define:{"process.env.NODE_ENV":'"test"'}});
const cssPath = resolve("src/styles.css");
const css = await postcss([tailwindcss()]).process(readFileSync(cssPath,"utf8"),{from:cssPath});
const state = {enabled:false,configured:false,ownerId:null,projects:[],state:"disabled"};
const requests = [];
const held = [];
let delayStatus = false, failNext = false;
const server = createServer((req,res)=>{
  if(req.url.startsWith("/api/telegram/")) {
    assert.equal(req.headers.authorization,"Bearer synthetic-app-auth");
    let body="";
    req.on("data",chunk=>{body+=chunk;});
    req.on("end",()=>{
      requests.push({path:req.url,method:req.method,body:body?JSON.parse(body):null});
      res.setHeader("Content-Type","application/json");
      if(failNext){failNext=false;res.statusCode=500;res.end(JSON.stringify({detail:"SYNTHETIC_PRIVATE_ERROR"}));return;}
      if(req.url.endsWith("/status")&&delayStatus){held.push(res);return;}
      if(req.url.endsWith("/config")) Object.assign(state,{configured:true,enabled:false,state:"configured",ownerId:JSON.parse(body).ownerId,projects:JSON.parse(body).projects,proxyMode:JSON.parse(body).proxyMode,proxyUrl:JSON.parse(body).proxyUrl});
      if(req.url.endsWith("/test-connection")){res.end(JSON.stringify({ok:true}));return;}
      if(req.url.endsWith("/start")) Object.assign(state,{enabled:true,state:"starting"});
      if(req.url.endsWith("/stop")) Object.assign(state,{enabled:false,state:"configured"});
      res.end(JSON.stringify(state));
    });
    return;
  }
  if(req.url==="/fixture.js"){res.setHeader("Content-Type","text/javascript");res.end(bundle.outputFiles[0].text);return;}
  if(req.url==="/fixture.css"){res.setHeader("Content-Type","text/css");res.end(css.css);return;}
  res.setHeader("Content-Type","text/html");
  res.end('<meta name="viewport" content="width=device-width"><link rel="stylesheet" href="/fixture.css"><div id="root"></div><script type="module" src="/fixture.js"></script>');
});
await new Promise(done=>server.listen(0,"127.0.0.1",done));
let browser;
try {
  browser=await chromium.launch({headless:true,channel:process.env.VRCFORGE_BROWSER_CHANNEL||"msedge"});
  const page=await browser.newPage();
  const origin=`http://127.0.0.1:${server.address().port}`;
  const errors=[];
  const consoleMessages=[];
  page.on("pageerror",error=>errors.push(String(error)));
  page.on("console",message=>consoleMessages.push(message.text()));
  await page.route("**/*",route=>route.request().url().startsWith(origin)?route.continue():route.abort());
  await page.goto(origin);
  const panel=page.locator("[data-vrcforge-telegram]");
  const ready=()=>page.waitForFunction(()=>document.querySelector('[data-vrcforge-telegram]')?.getAttribute('aria-busy')==='false');
  const noGreen=async()=>assert.equal(await panel.locator('span[class*="bg-emerald"]').count(),0);
  const tokenInput=panel.locator('input[type="password"]');
  await ready();
  assert.equal(await panel.getByText("Token saved",{exact:true}).count(),0);
  assert.equal(await panel.getByText("Token entered · not saved",{exact:true}).count(),0);
  assert.notEqual(await tokenInput.getAttribute("placeholder"),"Token saved — leave blank to keep it");
  assert.ok(await panel.getByRole("heading",{name:"Telegram remote access"}).isVisible());
  assert.ok(await panel.getByText(/send \/newbot/).isVisible());
  assert.ok(await panel.getByText(/send an ordinary message to start a continuing conversation/).isVisible());
  await panel.getByRole("button",{name:"Add current project",exact:true}).click();
  assert.equal(await panel.locator("textarea").inputValue(),"C:/CurrentProject");
  await page.evaluate(()=>{window.open=(...args)=>{window.openedHelp=args;return null;};});
  await panel.getByRole("button",{name:"Open BotFather",exact:true}).click();
  assert.deepEqual(await page.evaluate(()=>window.openedHelp),["https://t.me/BotFather","_blank","noopener,noreferrer"]);
  await noGreen();
  await panel.locator('input[type="password"]').fill("123456:synthetic-not-real-token");
  assert.ok(await panel.getByText("Token entered · not saved",{exact:true}).isVisible());
  assert.equal(await panel.getByText("Token saved",{exact:true}).count(),0);
  await noGreen();
  await panel.locator('input[inputmode="numeric"]').fill("12345");
  await panel.locator("textarea").fill("C:\\SyntheticProject\nD:\\AnotherSyntheticProject");
  await panel.getByRole("button",{name:"Save configuration",exact:true}).click();
  await ready();
  assert.equal(await panel.locator('input[type="password"]').inputValue(),"");
  assert.ok(await panel.getByText("Token saved",{exact:true}).isVisible());
  assert.equal(await tokenInput.getAttribute("placeholder"),"Token saved — leave blank to keep it");
  assert.match(await panel.getByText("Token saved",{exact:true}).getAttribute("class"),/amber/);
  assert.deepEqual(requests.find(row=>row.method==="PUT").body,{token:"123456:synthetic-not-real-token",ownerId:12345,projects:["C:\\SyntheticProject","D:\\AnotherSyntheticProject"],proxyMode:"environment",proxyUrl:""});
  assert.ok(await panel.getByText("Configured · disconnected",{exact:true}).isVisible());
  await noGreen();
  await panel.locator("textarea").fill("");
  await panel.getByRole("button",{name:"Save configuration",exact:true}).click();await ready();
  assert.deepEqual(requests.filter(row=>row.method==="PUT").at(-1).body.projects,[]);
  assert.equal(requests.filter(row=>row.method==="PUT").at(-1).body.token,"");
  assert.equal(await panel.getByRole("alert").count(),0);
  await panel.getByRole("combobox").selectOption("custom");
  await panel.getByPlaceholder("http://127.0.0.1:7890").fill("http://127.0.0.1:18888");
  assert.ok(await panel.getByRole("button",{name:"Test connection",exact:true}).isDisabled());
  await panel.getByRole("button",{name:"Save configuration",exact:true}).click();await ready();
  assert.equal(requests.filter(row=>row.method==="PUT").at(-1).body.proxyUrl,"http://127.0.0.1:18888");
  await panel.getByRole("button",{name:"Test connection",exact:true}).click();await ready();
  assert.ok(await panel.getByText("Bot API connection verified",{exact:true}).isVisible());
  assert.equal(requests.filter(row=>row.path.endsWith('/start')).length,0);
  await panel.getByRole("combobox").selectOption("direct");
  assert.equal(await panel.getByText("Bot API connection verified",{exact:true}).count(),0);
  await panel.getByRole("button",{name:"Save configuration",exact:true}).click();await ready();
  await panel.getByRole("button",{name:"Connect",exact:true}).click();await ready();
  assert.ok(await panel.getByText("Connecting",{exact:true}).isVisible());await noGreen();
  Object.assign(state,{state:"running"});
  await panel.getByRole("button",{name:"Refresh status",exact:true}).click();await ready();
  assert.equal(await panel.locator('span[class*="bg-emerald"]').count(),1);
  assert.ok(await panel.getByText("Working normally · receiving messages",{exact:true}).isVisible());
  await page.evaluate(()=>window.setLanguage("zh"));
  assert.ok(await panel.getByText("正常运作 · 正在接收消息",{exact:true}).isVisible());
  await page.evaluate(()=>window.setLanguage("en"));
  await panel.getByRole("button",{name:"Disconnect",exact:true}).click();await ready();await noGreen();
  failNext=true;
  await panel.getByRole("button",{name:"Refresh status",exact:true}).click();await ready();
  assert.ok(await panel.getByText(/Request failed/).isVisible());
  assert.ok(await panel.getByText("Status unavailable",{exact:true}).isVisible());
  assert.equal(await panel.getByText("Token saved",{exact:true}).count(),0);
  assert.notEqual(await tokenInput.getAttribute("placeholder"),"Token saved — leave blank to keep it");
  assert.equal(await page.getByText("SYNTHETIC_PRIVATE_ERROR").count(),0);await noGreen();
  await panel.getByRole("button",{name:"Refresh status",exact:true}).click();await ready();
  for(const language of ["en","zh"]){
    await page.evaluate(value=>window.setLanguage(value),language);
    const savedLabel=language==="zh"?"已填写 Token":"Token saved";
    const draftLabel=language==="zh"?"Token 待保存":"Token entered · not saved";
    assert.ok(await panel.getByText(savedLabel,{exact:true}).isVisible());
    assert.equal(await tokenInput.inputValue(),"");
    assert.equal(await tokenInput.getAttribute("placeholder"),language==="zh"?"已填写 Token，留空保留":"Token saved — leave blank to keep it");
    await tokenInput.fill("123456:synthetic-draft-token");
    assert.ok(await panel.getByText(draftLabel,{exact:true}).isVisible());
    assert.equal(await panel.getByText(savedLabel,{exact:true}).count(),0);
    await noGreen();
    await tokenInput.fill("");
    assert.ok(await panel.getByText(savedLabel,{exact:true}).isVisible());
    assert.ok(await panel.getByRole("heading",{name:language==="zh"?"Telegram 远程访问":"Telegram remote access"}).isVisible());
    assert.equal(await panel.locator('h4').count(),4);
    if(language==="zh") {
      assert.ok(await panel.getByText(/普通聊天可以留空/).isVisible());
      assert.ok(await panel.getByText(/发送 \/newbot/).isVisible());
      assert.ok(await panel.getByText(/第三方查询机器人/).isVisible());
      assert.ok(await panel.getByText(/不用重发/).isVisible());
      assert.ok(await panel.getByText(/直接发需求/).isVisible());
      assert.ok(await panel.getByRole("heading",{name:"4. 保存 → 测试 → 连接",exact:true}).isVisible());
      assert.ok(await panel.getByText(/开始聊天时不必先选工程或会话/).isVisible());
      assert.ok(await panel.getByText(/首次涉及工程操作时/).isVisible());
      assert.ok(await panel.getByText(/继续桌面端会话/).isVisible());
      const guide=await panel.getByText(/直接发需求/).textContent();
      for(const label of ["命令菜单","/session","/project","/model","/compact","/status","/stop","/help","审批、问答、预览","追加要求"])assert.ok(guide.includes(label));
      assert.ok(!guide.includes("点击“切换对话”"));
      assert.ok(!guide.includes("先选工程，再"));
    } else {
      assert.ok(await panel.getByText(/Leave this list empty for ordinary conversation/).isVisible());
      assert.ok(await panel.getByRole("heading",{name:"4. Save, test, then connect",exact:true}).isVisible());
      assert.ok(await panel.getByText(/without selecting a project or conversation first/).isVisible());
      const guide=await panel.getByText(/send an ordinary message to start a continuing conversation/).textContent();
      for(const phrase of ["first needs a project","original request","continue a desktop conversation","command menu","/session","/project","/model","/compact","/status","/stop","/help","approvals, questions and previews","add instructions"])assert.ok(guide.includes(phrase));
      assert.ok(!guide.includes("切换对话"));
      assert.ok(!guide.includes("On first use, tap the project and session buttons"));
    }
    for(const width of [1320,390]){
      await page.setViewportSize({width,height:860});
      assert.ok(await page.evaluate(()=>document.documentElement.scrollWidth<=window.innerWidth),`${language} overflow at ${width}px`);
    }
  }
  await page.evaluate(()=>window.unmountPanel());
  delayStatus=true;
  await page.evaluate(()=>window.mountPanel());
  await page.waitForFunction(()=>document.querySelector('[data-vrcforge-telegram]')?.getAttribute('aria-busy')==='true');
  for(let attempt=0;!held.length&&attempt<100;attempt++)await page.waitForTimeout(10);
  assert.equal(held.length,1);
  assert.equal(await panel.getByText("已填写 Token",{exact:true}).count(),0);
  await noGreen();
  await page.evaluate(()=>window.unmountPanel());
  delayStatus=false;
  await page.evaluate(()=>window.mountPanel());await ready();
  held.pop().end(JSON.stringify({...state,enabled:true,state:"running",ownerId:99999}));
  await page.waitForTimeout(50);
  assert.equal(await panel.locator('input[inputmode="numeric"]').inputValue(),"12345");await noGreen();
  assert.ok(await panel.getByText("已填写 Token",{exact:true}).isVisible());
  assert.equal(await tokenInput.inputValue(),"");
  assert.deepEqual(errors,[]);
  assert.equal(await page.evaluate(()=>localStorage.length),0);
  assert.equal(await page.evaluate(()=>sessionStorage.length),0);
  for(const secret of ["123456:synthetic-not-real-token","123456:synthetic-draft-token"]){
    assert.ok(!(await page.locator("body").innerText()).includes(secret));
    assert.ok(!page.url().includes(secret));
    assert.ok(!consoleMessages.some(message=>message.includes(secret)));
    assert.ok(!requests.some(request=>request.path.includes(secret)));
  }
  console.log("Telegram real React browser: save/status/actions/errors/languages/lifecycle/390px+1320px passed");
} finally {
  for(const response of held)response.destroy();
  if(browser)await browser.close();
  server.closeAllConnections();
  await new Promise(done=>server.close(done));
}
