import assert from "node:assert/strict";
import { createRequire } from "node:module";
import { createServer } from "node:http";
import { build } from "esbuild";
const require = createRequire(import.meta.url);
const { chromium } = require(process.env.VRCFORGE_PLAYWRIGHT_PATH || "playwright");
const bundle = await build({stdin: {resolveDir: process.env.VRCFORGE_TEST_SOURCE_ROOT || process.cwd(), loader: "tsx", contents: `
import React, {useState} from 'react';
import {createRoot} from 'react-dom/client';
import {buildAgentTimelineRows} from './src/components/chat/conversation-timeline';
let serializations=0;
window.readSerializations=()=>serializations;
const payload={toJSON(){serializations++; return {proof:'complete original payload',values:Array.from({length:1000},(_,i)=>i)}}};
function Fixture(){
 const [revision,setRevision]=useState(0);
 const rows=buildAgentTimelineRows({response:{ok:true,plan:{nextStep:'done'}},
 skill:{tool:'fixture-tool',status:'completed',result:payload},showIntent:false,nextStep:'done',
 planLabel:'',providerLine:'',awaitingApproval:false,t:(key)=>key});
 return <><button onClick={()=>setRevision(revision+1)}>rerender</button><output>{revision}</output><section id="rows">{rows}</section></>;
}
createRoot(document.getElementById('root')).render(<Fixture/>);
`},bundle:true,write:false,format:'iife',platform:'browser'});
// Task-owned ephemeral loopback server exposes synthetic data only, no auth needed.
// Its browser and server are closed in finally, including failed assertions.
const server=createServer((_req,res)=>{res.setHeader('Content-Type','text/html');res.end('<div id="root"></div><script>'+bundle.outputFiles[0].text.replaceAll('</script','<\\/script')+'</script>')});
let browser;
try {
 await new Promise(resolve=>server.listen(0,'127.0.0.1',resolve));
 browser=await chromium.launch({channel:'msedge',headless:true});
 const page=await browser.newPage();
 const errors=[];page.on('pageerror',e=>errors.push(e.message));
 await page.goto('http://127.0.0.1:'+server.address().port);
 await page.locator('#rows button').waitFor();
 await page.getByRole('button',{name:'rerender',exact:true}).click();
 assert.equal(await page.evaluate(()=>window.readSerializations()),0,'collapsed results must not serialize on mount or parent rerender');
 await page.locator('#rows button').click();
 await page.getByText('complete original payload',{exact:false}).waitFor();
 assert.equal(await page.evaluate(()=>window.readSerializations()),1);
 assert.ok((await page.locator('pre').innerText()).includes('999'));
 assert.deepEqual(errors,[]);
 console.log('PASS: collapsed result serializations=0; expanded=1, full payload retained.');
} finally {await browser?.close();await new Promise(resolve=>server.close(resolve));}
