// Real conversation cards, synthetic histories: no model calls or user data.
import assert from 'node:assert/strict';
import {createServer} from 'node:http';
import {createRequire} from 'node:module';
import {build} from 'esbuild';
const require=createRequire(import.meta.url);
const {chromium}=require(process.env.VRCFORGE_PLAYWRIGHT_PATH || 'playwright');
const bundle=await build({stdin:{resolveDir:process.env.VRCFORGE_TEST_SOURCE_ROOT||process.cwd(),loader:'tsx',contents:`
import React,{useState} from 'react';
import {createRoot} from 'react-dom/client';
import {ConversationCard} from './src/components/chat/conversation-card';
let serializations=0;
const result={toJSON(){serializations++;return {data:'synthetic tool evidence '.repeat(12000)}}};
window.countSerializations=()=>serializations;
const histories=[0,1].map(n=>Array.from({length:100},(_,i)=>({id:n+'-'+i,type:'agent',createdAt:'2026-01-01T00:00:00Z',
 response:{ok:true,status:'completed',plan:{nextStep:'done',reply:'## History '+n+' / '+i+'\\n\\n'+('A synthetic answer with **formatted text** and a useful explanation.\\n\\n').repeat(5)},
 skill:{tool:'synthetic_reader',status:'completed',result}}})));
function Fixture(){const [chat,setChat]=useState(0);return <><button onClick={()=>setChat(1-chat)}>switch history</button><output id="active">{chat}</output>
<main id="scroll" style={{height:600,overflowY:'scroll'}}>{histories[chat].map(item=><div key={item.id} style={{minHeight:240}}><ConversationCard item={item}/></div>)}</main></>}
createRoot(document.getElementById('root')).render(<Fixture/>);
`},bundle:true,write:false,format:'iife',platform:'browser'});
// Ephemeral loopback fixture owned by this test; all content is synthetic/public.
const server=createServer((_req,res)=>{res.setHeader('Content-Type','text/html');res.end('<style>svg{width:16px;height:16px}body{font:14px sans-serif}</style><div id="root"></div><script>'+bundle.outputFiles[0].text.replaceAll('</script','<\\/script')+'</script>')});
let browser;
try {
 await new Promise(resolve=>server.listen(0,'127.0.0.1',resolve));
 browser=await chromium.launch({channel:'msedge',headless:true});
 const page=await browser.newPage();const errors=[];page.on('pageerror',e=>errors.push(e.message));
 await page.goto('http://127.0.0.1:'+server.address().port);
 await page.locator('#scroll > div').last().waitFor();
 assert.equal(await page.locator('#scroll > div').count(),100);
 assert.equal(await page.evaluate(()=>window.countSerializations()),0);
 await page.locator('#scroll').hover();await page.mouse.wheel(0,1500);
 await page.waitForFunction(()=>document.getElementById('scroll').scrollTop>0);
 const down=await page.locator('#scroll').evaluate(e=>e.scrollTop);
 await page.mouse.wheel(0,-1500);
 await page.waitForFunction(y=>document.getElementById('scroll').scrollTop<y,down);
 await page.getByRole('button',{name:'switch history',exact:true}).click();
 await page.waitForFunction(()=>document.getElementById('active').textContent==='1');
 assert.equal(await page.locator('#scroll > div').count(),100);
 assert.equal(await page.evaluate(()=>window.countSerializations()),0);
 assert.deepEqual(errors,[]);
 console.log('PASS: two 100-answer histories using real ConversationCard; wheel down/up and switch work; folded tool serializations=0.');
} finally {await browser?.close();await new Promise(resolve=>server.close(resolve));}
