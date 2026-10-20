// Production message card + real payload serialization, with synthetic pixels only.
import assert from 'node:assert/strict';
import {createServer} from 'node:http';
import {createRequire} from 'node:module';
import {build} from 'esbuild';
const {chromium}=createRequire(import.meta.url)(process.env.VRCFORGE_PLAYWRIGHT_PATH || 'playwright');
const bundle=await build({stdin:{resolveDir:process.cwd(),loader:'tsx',contents:`
import React from 'react';
import {createRoot} from 'react-dom/client';
import {ConversationCard} from './src/components/chat/conversation-card';
import {persistAttachmentReference} from './src/lib/attachment-payloads';
const vault={};
const image={id:'image',name:'saved.png',type:'image/png',size:68,payloadKind:'data_url',dataUrl:'data:image/png;base64,iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mP8/x8AAusB9Wl6FmsAAAAASUVORK5CYII='};
const reference=persistAttachmentReference(image,vault);
const snapshot=JSON.parse(JSON.stringify({item:{id:'saved',type:'user',text:'Image stays visible',attachments:[reference]},vault}));
window.fixtureSnapshot=snapshot;
createRoot(document.getElementById('root')).render(<>
<section id='saved'><ConversationCard item={snapshot.item} attachmentPayloads={snapshot.vault} editingAttachments={[]}/></section>
<section id='missing'><ConversationCard item={snapshot.item} attachmentPayloads={{}}/></section>
<section id='corrupt'><ConversationCard item={snapshot.item} attachmentPayloads={{...snapshot.vault,[reference.payloadHash]:{...snapshot.vault[reference.payloadHash],dataUrl:'corrupt'}}}/></section>
</>);
`},bundle:true,write:false,format:'iife',jsx:'automatic'});
// Test-owned ephemeral loopback server and isolated browser; no credentials or app connection.
const server=createServer((req,res)=>{res.setHeader('Content-Type','text/html');res.end('<div id="root"></div><script>'+bundle.outputFiles[0].text.replaceAll('</script','<\\/script')+'</script>');});
let browser;
try {
 await new Promise(r=>server.listen(0,'127.0.0.1',r));
 browser=await chromium.launch({headless:true,channel:'msedge'});
 const page=await browser.newPage();
 const errors=[];page.on('pageerror',e=>errors.push(e.message));
 await page.goto('http://127.0.0.1:'+server.address().port);
 await page.locator('#saved').waitFor();
 assert.equal(await page.locator('#saved img').count(),1,'persisted image reference must render a thumbnail');
 assert(await page.locator('#saved img').evaluate(i=>i.complete&&i.naturalWidth>0));
 await page.locator('#saved button[title="saved.png"]').click();
 await page.getByRole('dialog').waitFor();
 assert.equal(await page.getByRole('dialog').locator('img').count(),1);
 await page.reload();
 await page.locator('#saved img').waitFor();
 assert(await page.locator('#saved img').evaluate(i=>i.complete&&i.naturalWidth>0));
 assert.equal(await page.locator('#missing img, #corrupt img').count(),0,'missing or corrupt bodies must not show misleading previews');
 assert(await page.evaluate(()=>!window.fixtureSnapshot.item.attachments[0].dataUrl),'rendering must not put image bytes back in persisted items');
 assert.deepEqual(errors,[]);
 console.log('image reference restore: thumbnail, expanded image, reload, missing/corrupt isolation passed');
} finally {await browser?.close();await new Promise(r=>server.close(r));}
