import assert from 'node:assert/strict';
import {createRequire} from 'node:module';
import {createServer} from 'node:http';
import {readFile,writeFile} from 'node:fs/promises';
import {dirname} from 'node:path';
import {build} from 'esbuild';

const {chromium}=createRequire(import.meta.url)(process.env.VRCFORGE_PLAYWRIGHT_PATH||'playwright');
const original='**本轮已实际调用正式导入工具，但导入失败，包仍未成功导入。**阻断发生在检查点准备阶段，与后续配置入口能否调用无关。';
const cases=[
 {id:'original-agent',text:original,variant:'agent'},
 {id:'original-user',text:original,variant:'user'},
 {id:'cjk-punctuation',text:'**中文。**后续\n\n**日本語。**続き\n\n**한국어。**이어서'},
 {id:'literal-markers',text:'`**示例。**后续`\n\n```txt\n**示例。**后续\n```\n\n\\*\\*原样。\\*\\*后续'},
 {id:'math',text:'公式 $x^2 + y^2$。\n\n$$\na \\ast b\n$$'},
 {id:'gfm',text:'A **bold** word.\n\n| X | Y |\n| - | - |\n| ~~old~~ | new |\n\n- [x] done\n\n[link](https://example.com)'},
 {id:'invalid-whitespace',text:'**中文。 **后续'},
 {id:'sanitized-html',text:'<strong>正常</strong><script>alert(1)</script><img src="javascript:alert(1)" onerror="alert(1)">'}
];
// Private evidence can supply the complete original reply without embedding session data in source.
if(process.env.VRCFORGE_MARKDOWN_CJK_ORIGINAL)cases.push({id:'complete-original',text:await readFile(process.env.VRCFORGE_MARKDOWN_CJK_ORIGINAL,'utf8')});
const bundle=await build({stdin:{resolveDir:process.cwd(),loader:'tsx',contents:`
import React from 'react';import {createRoot} from 'react-dom/client';
import {ChatMarkdown} from './src/components/chat/chat-markdown';
import {initializeI18n} from './src/i18n';
const cases=${JSON.stringify(cases)};
void initializeI18n().then(()=>createRoot(document.getElementById('root')).render(<>{cases.map(c=><section key={c.id} data-case={c.id}><ChatMarkdown text={c.text} variant={c.variant}/></section>)}</>));
`},bundle:true,write:false,format:'iife',platform:'browser',jsx:'automatic',plugins:process.env.VRCFORGE_MARKDOWN_BEFORE?[{name:'original-markdown',setup(api){api.onLoad({filter:/chat-markdown\.tsx$/},async args=>({contents:await readFile(process.env.VRCFORGE_MARKDOWN_BEFORE,'utf8'),resolveDir:dirname(args.path),loader:'tsx'}));}}]:[]});
const server=createServer((_req,res)=>{res.setHeader('Content-Type','text/html; charset=utf-8');res.end(`<div id="root"></div><script>${bundle.outputFiles[0].text.replaceAll('</script','<\\/script')}</script>`)});
let browser;
try{
 await new Promise(resolve=>server.listen(0,'127.0.0.1',resolve));browser=await chromium.launch({channel:'msedge',headless:true});const page=await browser.newPage();const errors=[];page.on('pageerror',e=>errors.push(e.message));
 await page.goto(`http://127.0.0.1:${server.address().port}`);await page.locator('[data-case="gfm"] table').waitFor();
 const observations=await page.evaluate(()=>Array.from(document.querySelectorAll('[data-case]')).map(el=>({id:el.getAttribute('data-case'),text:el.textContent,firstParagraph:el.querySelector('p')?.innerHTML,strong:Array.from(el.querySelectorAll('strong')).map(e=>e.textContent),code:Array.from(el.querySelectorAll('code')).map(e=>e.textContent),math:Array.from(el.querySelectorAll('.katex annotation')).map(e=>e.textContent),tables:el.querySelectorAll('table').length,deletions:el.querySelectorAll('del').length,checkboxes:el.querySelectorAll('input[type="checkbox"][disabled]').length,links:Array.from(el.querySelectorAll('a')).map(e=>e.getAttribute('href')),scripts:el.querySelectorAll('script').length,eventAttributes:Array.from(el.querySelectorAll('*')).flatMap(e=>Array.from(e.attributes).filter(a=>a.name.startsWith('on')).map(a=>a.name)),unsafeUrls:Array.from(el.querySelectorAll('[src],[href]')).flatMap(e=>['src','href'].map(a=>e.getAttribute(a)||'').filter(u=>u.startsWith('javascript:')))})));
 const report={cases:observations,errors};
 if(process.env.VRCFORGE_MARKDOWN_CJK_REPORT)await writeFile(process.env.VRCFORGE_MARKDOWN_CJK_REPORT,JSON.stringify(report,null,2));
 const byId=Object.fromEntries(observations.map(x=>[x.id,x]));
 for(const id of ['original-agent','original-user']){
  assert.equal(byId[id].strong[0],'本轮已实际调用正式导入工具，但导入失败，包仍未成功导入。',`${id}: CJK punctuation closes strong before adjacent Han text`);
  assert.equal(byId[id].text,original.replaceAll('**',''),`${id}: text stays exact except parsed delimiters`);
 }
 assert.deepEqual(byId['cjk-punctuation'].strong,['中文。','日本語。','한국어。']);
 assert.deepEqual(byId['literal-markers'].code,['**示例。**后续','**示例。**后续\n']);
 assert.equal(byId['literal-markers'].strong.length,0);
 assert.ok(byId['literal-markers'].text.endsWith('**原样。**后续'));
 assert.deepEqual(byId.math.math,['x^2 + y^2','a \\ast b']);
 assert.deepEqual(byId.gfm.strong,['bold']);assert.equal(byId.gfm.tables,1);assert.equal(byId.gfm.deletions,1);assert.equal(byId.gfm.checkboxes,1);assert.deepEqual(byId.gfm.links,['https://example.com']);
 assert.equal(byId['invalid-whitespace'].strong.length,0,'whitespace inside closing delimiter remains literal invalid Markdown');
 assert.deepEqual(byId['sanitized-html'].strong,['正常']);assert.equal(byId['sanitized-html'].scripts,0);assert.deepEqual(byId['sanitized-html'].eventAttributes,[]);assert.deepEqual(byId['sanitized-html'].unsafeUrls,[]);
 if(byId['complete-original']){assert.equal(byId['complete-original'].strong[0],byId['original-agent'].strong[0]);assert.ok(!byId['complete-original'].text.includes('**'));assert.equal(byId['complete-original'].tables,1);}
 assert.deepEqual(errors,[]);console.log(`real ChatMarkdown CJK emphasis, literal code/escape, math, GFM, and sanitize: ${cases.length} cases passed`);
}finally{await browser?.close();await new Promise(resolve=>server.close(resolve));}
