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
 function Fixture(){const [key,setKey]=useState(""); const [model,setModel]=useState("m"); const [passed,setPassed]=useState(false);
  const hook=useProviderSettings({endpoint:location.origin,runtimeConnected:true,apiConfig:{provider:"custom",base_url:"",model:"m",api_type:"auto",apiKeyRequired:true,apiKeyPresent:false},startRuntime:async()=>location.origin,refresh:async()=>{},setError:()=>{}});
  useEffect(()=>setPassed(hook.providerTestPassed),[hook.providerTestPassed]);
  return <main><button data-testid="test" onClick={()=>void hook.runProviderTest("text")}>test</button><button data-testid="structured" onClick={()=>void hook.runProviderTest("structured")}>structured</button><input data-testid="key" value={key} onChange={e=>{setKey(e.target.value);hook.setApiKey(e.target.value)}}/><input data-testid="model" value={model} onChange={e=>{setModel(e.target.value);hook.setApiModel(e.target.value)}}/><div data-testid="passed">{String(passed)}</div><div data-testid="testing">{hook.testingProvider}</div></main>;
 } createRoot(document.getElementById("root")).render(<Fixture/>);` }, bundle:true, write:false, format:"iife", jsx:"automatic", define:{"process.env.NODE_ENV":'"test"'} });
const pending=[]; const server=createServer((req,res)=>{if(req.url==="/api/app/provider/test"){let body="";req.on("data",c=>body+=c);req.on("end",()=>{pending.push({res,request:JSON.parse(body)});});return;} if(req.url==="/fixture.js"){res.setHeader("Content-Type","text/javascript");res.end(bundle.outputFiles[0].text);return;} if(req.url==="/"){res.setHeader("Content-Type","text/html");res.end("<div id=\"root\"></div><script src=\"/fixture.js\"></script>");return;} res.statusCode=404;res.end();});
await new Promise(resolve=>server.listen(0,"127.0.0.1",resolve));
let browser; try { browser=await chromium.launch({headless:true,channel:process.env.VRCFORGE_BROWSER_CHANNEL||"msedge"}); const page=await browser.newPage();
 const wait=async (p)=>{for(let i=0;i<300;i++){if(await p())return;await new Promise(r=>setTimeout(r,10));} assert.fail("timed out")};
 await page.goto(`http://127.0.0.1:${server.address().port}/`); await page.getByTestId("test").click(); await wait(()=>pending.length===1);
 await page.getByTestId("key").fill("changed"); assert.equal(await page.getByTestId("passed").innerText(),"false");
 const reply=(payload)=>{const item=pending.shift(); item.res.setHeader("Content-Type","application/json"); item.res.end(JSON.stringify(payload));};
 reply({ok:true,status:"ok",capability:"text",message:"old"}); await new Promise(r=>setTimeout(r,30)); assert.equal(await page.getByTestId("passed").innerText(),"false");
 await page.getByTestId("test").click(); await wait(()=>pending.length===1); reply({ok:true,status:"ok",capability:"text",message:"new"}); await wait(async()=> (await page.getByTestId("passed").innerText()) === "true");
 await wait(async()=> (await page.getByTestId("testing").innerText()) === "");
 await page.getByTestId("structured").click(); await wait(()=>pending.length===1); reply({ok:true,status:"ok",capability:"structured",message:"structured"}); await wait(async()=> (await page.getByTestId("passed").innerText()) === "false");
 console.log("provider verification race/capability contract: ok");
} finally {if(browser)await browser.close();await new Promise(resolve=>server.close(resolve));}
