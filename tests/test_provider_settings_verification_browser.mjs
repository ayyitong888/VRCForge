// Real React hook regression for provider verification invalidation/races.
import assert from "node:assert/strict";
import { createServer } from "node:http";
import { createRequire } from "node:module";
import { build } from "esbuild";

const require = createRequire(import.meta.url);
const { chromium } = require(process.env.VRCFORGE_PLAYWRIGHT_PATH || "playwright");
const bundle = await build({ stdin: { resolveDir: process.cwd(), loader: "tsx", contents: `
 import React, {useEffect, useState} from "react";
 import {createRoot} from "react-dom/client";
 import i18n from "i18next"; import {initReactI18next} from "react-i18next";
 import en from "./src/locales/en-US.json";
 import {useProviderSettings} from "./src/hooks/use-provider-settings";
 i18n.use(initReactI18next).init({lng:"en-US",resources:{"en-US":{translation:en}}});
 function Fixture(){const [key,setKey]=useState(""); const [model,setModel]=useState("m"); const [credentialFingerprint,setCredentialFingerprint]=useState("saved-fingerprint"); const [runtimeConnected,setRuntimeConnected]=useState(true); const [passed,setPassed]=useState(false);
  const hook=useProviderSettings({endpoint:location.origin,runtimeConnected,apiConfig:{provider:"custom",base_url:"",model:"m",api_type:"auto",configured:true,apiKeyRequired:true,apiKeyPresent:true,credentialFingerprint},startRuntime:async()=>location.origin,refresh:async()=>{},setError:()=>{}});
  useEffect(()=>setPassed(hook.providerTestPassed),[hook.providerTestPassed]);
  return <main><button data-testid="test" onClick={()=>void hook.runProviderTest("text")}>test</button><button data-testid="structured" onClick={()=>void hook.runProviderTest("structured")}>structured</button><button data-testid="account-change" onClick={()=>setCredentialFingerprint("other-account")}>account change</button><button data-testid="runtime-off" onClick={()=>setRuntimeConnected(false)}>runtime off</button><input data-testid="key" value={key} onChange={e=>{setKey(e.target.value);hook.setApiKey(e.target.value)}}/><input data-testid="model" value={model} onChange={e=>{setModel(e.target.value);hook.setApiModel(e.target.value)}}/><div data-testid="passed">{String(passed)}</div><div data-testid="persisted">{String(hook.providerTestPassedForPersistedConfig)}</div><div data-testid="ready">{String(hook.providerReadyForOnboarding)}</div><div data-testid="testing">{hook.testingProvider}</div><div data-testid="message">{hook.providerTestMessage}</div><div data-testid="errors">{hook.modelsError}</div></main>;
 } createRoot(document.getElementById("root")).render(<Fixture/>);` }, bundle:true, write:false, format:"iife", jsx:"automatic", define:{"process.env.NODE_ENV":'"test"'} });
const pending=[]; const server=createServer((req,res)=>{if(req.url==="/api/app/provider/test"){let body="";req.on("data",c=>body+=c);req.on("end",()=>{pending.push({res,request:JSON.parse(body)});});return;} if(req.url==="/fixture.js"){res.setHeader("Content-Type","text/javascript");res.end(bundle.outputFiles[0].text);return;} if(req.url==="/"){res.setHeader("Content-Type","text/html");res.end("<div id=\"root\"></div><script src=\"/fixture.js\"></script>");return;} res.statusCode=404;res.end();});
await new Promise(resolve=>server.listen(0,"127.0.0.1",resolve));
let browser; try { browser=await chromium.launch({headless:true,channel:process.env.VRCFORGE_BROWSER_CHANNEL||"msedge"}); const page=await browser.newPage();
 const wait=async (p)=>{for(let i=0;i<300;i++){if(await p())return;await new Promise(r=>setTimeout(r,10));} assert.fail("timed out")};
  await page.goto(`http://127.0.0.1:${server.address().port}/`); assert.equal(await page.getByTestId("passed").innerText(),"false", "configured state starts yellow until a real probe passes"); await page.getByTestId("test").click(); await wait(()=>pending.length===1);
 await page.getByTestId("key").fill("changed"); assert.equal(await page.getByTestId("passed").innerText(),"false");
const reply=(payload,status=200)=>{const item=pending.shift(); item.res.statusCode=status; item.res.setHeader("Content-Type","application/json"); item.res.end(JSON.stringify(payload));};
 reply({ok:true,status:"ok",capability:"text",message:"old",credentialFingerprint:"saved-fingerprint"}); await new Promise(r=>setTimeout(r,30)); assert.equal(await page.getByTestId("passed").innerText(),"false"); assert.equal(await page.getByTestId("persisted").innerText(),"false");
 await page.getByTestId("test").click(); await wait(()=>pending.length===1); reply({ok:true,status:"ok",capability:"text",message:"new",credentialFingerprint:"new-fingerprint"}); await wait(async()=> (await page.getByTestId("passed").innerText()) === "true");
 assert.equal(await page.getByTestId("persisted").innerText(),"false", "tested but unsaved key cannot validate the persisted configuration");
 await page.getByTestId("model").fill("new-model"); assert.equal(await page.getByTestId("passed").innerText(),"false");
 await page.getByTestId("test").click(); await wait(()=>pending.length===1); reply({ok:false,status:"error",capability:"text",message:"provider rejected"}); await wait(async()=> (await page.getByTestId("testing").innerText()) === ""); assert.equal(await page.getByTestId("passed").innerText(),"false", "HTTP 200 with ok=false stays yellow");
 await page.getByTestId("test").click(); await wait(()=>pending.length===1); reply({detail:"bad request"},400); await wait(async()=> (await page.getByTestId("testing").innerText()) === ""); assert.equal(await page.getByTestId("passed").innerText(),"false", "HTTP 400 cannot become green");
 await page.getByTestId("model").fill("m"); await page.getByTestId("key").fill("changed"); await page.getByTestId("test").click(); await wait(()=>pending.length===1); await page.getByTestId("key").fill("changed-again");
 reply({ok:true,status:"ok",capability:"text",message:"old",credentialFingerprint:"saved-fingerprint"}); await new Promise(r=>setTimeout(r,30)); assert.equal(await page.getByTestId("passed").innerText(),"false", "an old 200 response cannot revalidate a changed key");
 await page.getByTestId("test").click(); await wait(()=>pending.length===1); reply({ok:true,status:"ok",capability:"text",message:"new",credentialFingerprint:"new-fingerprint"}); await wait(async()=> (await page.getByTestId("passed").innerText()) === "true"); assert.equal(await page.getByTestId("persisted").innerText(),"false");
 await page.getByTestId("key").fill(""); await page.getByTestId("test").click(); await wait(()=>pending.length===1); reply({ok:true,status:"ok",capability:"text",message:"saved config",credentialFingerprint:"saved-fingerprint"}); await wait(async()=> (await page.getByTestId("persisted").innerText()) === "true"); assert.equal(await page.getByTestId("ready").innerText(),"true", "the saved configuration is ready after HTTP 200 ok");
 await page.getByTestId("structured").click(); await wait(()=>pending.length===1); reply({ok:true,status:"ok",capability:"structured",message:"structured",credentialFingerprint:"saved-fingerprint"}); await wait(async()=> (await page.getByTestId("testing").innerText()) === ""); assert.equal(await page.getByTestId("passed").innerText(),"false", "a structured-only probe does not make the text authorization green"); assert.equal(await page.getByTestId("persisted").innerText(),"false");
 await page.getByTestId("test").click(); await wait(()=>pending.length===1); reply({ok:true,status:"ok",capability:"text",message:"saved config again",credentialFingerprint:"saved-fingerprint"}); await wait(async()=> (await page.getByTestId("persisted").innerText()) === "true");
 await page.getByTestId("test").click(); await wait(()=>pending.length===1); await page.getByTestId("account-change").click(); await wait(async()=> (await page.getByTestId("persisted").innerText()) === "false"); reply({ok:true,status:"ok",capability:"text",message:"old account",credentialFingerprint:"saved-fingerprint"}); await new Promise(r=>setTimeout(r,50)); assert.equal(await page.getByTestId("passed").innerText(),"false", "a late 200 from the old account cannot revalidate the changed account"); assert.equal(await page.getByTestId("persisted").innerText(),"false");
 await page.getByTestId("runtime-off").click(); await wait(async()=> (await page.getByTestId("persisted").innerText()) === "false"); assert.equal(await page.getByTestId("passed").innerText(),"false", "runtime changes invalidate the probe");
 console.log("provider verification race/capability contract: ok");
} finally {if(browser)await browser.close();await new Promise(resolve=>server.close(resolve));}
