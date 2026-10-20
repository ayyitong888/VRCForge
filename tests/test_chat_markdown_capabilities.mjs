import assert from "node:assert/strict";
import Module from "node:module";
import { build } from "esbuild";
import { createRequire } from "node:module";
import { readFileSync, mkdirSync, writeFileSync } from "node:fs";
import { resolve } from "node:path";

const root = resolve(import.meta.dirname, "..");
const { chromium } = createRequire(import.meta.url)(process.env.VRCFORGE_PLAYWRIGHT_PATH || "playwright");
const evidenceDir = process.env.VRCFORGE_MARKDOWN_EVIDENCE_DIR;
const markdown = `# Heading 1
## Heading 2
### Heading 3
#### Heading 4
##### Heading 5
###### Heading 6

3. third
4. fourth

1. outer
   - inner one
   - inner two
2. second

- [x] completed
- [ ] pending

| left | center | right |
| :--- | :----: | ---: |
| a | b | c |

> quoted line
>
> second quote

---

[reference][docs]

[docs]: https://example.com/docs

<mark>safe HTML</mark> [bad](javascript:alert(1)) 😀 \\*escaped\\*

    indented code

[^one] and [^two] and [^中文].

Inline $x^2$ and $\\alpha + \\beta$.

$$
E = mc^2
$$

[^one]: first note
[^two]: second note
[^中文]: 中文脚注`;

const serverSource = `import React from "react"; import {renderToStaticMarkup} from "react-dom/server"; import {ChatMarkdown} from "./src/components/chat/chat-markdown"; export const render=(text)=>renderToStaticMarkup(React.createElement(ChatMarkdown,{text}));`;
const serverBundle = await build({stdin:{resolveDir:root,loader:"tsx",contents:serverSource},bundle:true,write:false,platform:"node",format:"cjs",jsx:"automatic",external:["react","react-dom/server","react/jsx-runtime","i18next","react-i18next"],loader:{".css":"empty"}});
const serverFilename = resolve(root,"tests","chat-markdown-capabilities-fixture.cjs");
const serverFixture = new Module(serverFilename); serverFixture.paths = Module._nodeModulePaths(root); serverFixture._compile(serverBundle.outputFiles[0].text,serverFilename);
const rendered = serverFixture.exports.render(markdown);
for (const heading of ["h1","h2","h3","h4","h5","h6"]) assert.match(rendered,new RegExp(`<${heading}\\b`),`${heading} renders`);
assert.match(rendered,/<ol[^>]*start="3"/); assert.match(rendered,/<ul[^>]*>[\s\S]*inner one/); assert.match(rendered,/type="checkbox"/);
assert.match(rendered,/<table[\s\S]*<th[^>]*text-align:center/); assert.match(rendered,/<blockquote/); assert.match(rendered,/<hr/);
assert.match(rendered,/href="https:\/\/example.com\/docs"/); assert.match(rendered,/<mark[^>]*>safe HTML/); assert.doesNotMatch(rendered,/javascript:alert/);
assert.match(rendered,/indented code/); assert.match(rendered,/katex/); assert.doesNotMatch(rendered,/user-content-user-content-/);

const incomplete = "Streaming\n\n```js\nconst answer = 42;";
const clientSource = `import React from "react"; import {createRoot} from "react-dom/client"; import {ChatMarkdown} from "./src/components/chat/chat-markdown"; const text=${JSON.stringify(markdown)}; const incomplete=${JSON.stringify(incomplete)}; createRoot(document.getElementById("root")).render(React.createElement(React.Fragment,null,React.createElement(ChatMarkdown,{text}),React.createElement(ChatMarkdown,{text}),React.createElement(ChatMarkdown,{text:incomplete}),React.createElement(ChatMarkdown,{text:"formula $x^2$"})));`;
const clientBundle = await build({stdin:{resolveDir:root,loader:"tsx",contents:clientSource},bundle:true,write:false,format:"iife",platform:"browser",jsx:"automatic",loader:{".css":"empty"}});
const katexCss = readFileSync(resolve(root,"node_modules/katex/dist/katex.min.css"),"utf8");
const browser = await chromium.launch({headless:true,channel:process.env.VRCFORGE_BROWSER_CHANNEL||"msedge"});
try {
  const page = await browser.newPage({viewport:{width:1200,height:1000},deviceScaleFactor:1});
  await page.setContent(`<style>${katexCss}</style><style>body{margin:0}.chat-markdown{font:14px/1.5 Arial,sans-serif;width:900px}</style><div id="root"></div><script>${clientBundle.outputFiles[0].text}</script>`);
  await page.waitForSelector("[data-testid='chat-markdown-agent'] sup a");
  const messages = page.locator("[data-testid='chat-markdown-agent']"); assert.equal(await messages.count(),4);
  const footnotes = await messages.nth(0).locator("section[data-footnotes]").evaluate((section) => { const root=section.closest("[data-testid]"); const ids=new Set([...root.querySelectorAll("[id]")].map((n)=>n.id)); const refs=[...root.querySelectorAll("sup a[data-footnote-ref]")]; const backs=[...root.querySelectorAll("a[data-footnote-backref]")]; const aria=[...root.querySelectorAll("[aria-describedby]")]; return {refs:refs.map((n)=>ids.has(n.getAttribute("href").slice(1))),backs:backs.map((n)=>ids.has(n.getAttribute("href").slice(1))),aria:aria.map((n)=>n.getAttribute("aria-describedby").split(/\\s+/).every((id)=>ids.has(id)))}; });
  assert.ok(footnotes.refs.every(Boolean)); assert.ok(footnotes.backs.every(Boolean)); assert.ok(footnotes.aria.every(Boolean));
  const idsA=await messages.nth(0).locator("[id*='fn-one']").evaluateAll((n)=>n.map((x)=>x.id)); const idsB=await messages.nth(1).locator("[id*='fn-one']").evaluateAll((n)=>n.map((x)=>x.id)); assert.notDeepEqual(idsA,idsB);
  const ref=messages.nth(0).locator("sup a[data-footnote-ref]").first(); const target=(await ref.getAttribute("href")).slice(1); await ref.click(); assert.equal(await page.evaluate(()=>location.hash.slice(1)),target); assert.equal(await messages.nth(0).locator(`#${target}`).count(),1);
  assert.equal(await messages.nth(0).locator(".katex").evaluateAll((nodes)=>nodes.filter((node)=>!node.closest(".katex-display")).length),2); assert.equal(await messages.nth(0).locator(".katex-display").count(),1); const box=await messages.nth(0).locator(".katex-display").boundingBox(); assert.ok(box?.height>0&&box?.width>0);
  assert.match(await messages.nth(2).textContent(), /Streaming/); assert.equal(await messages.nth(3).locator(".katex").count(),1);
  if(evidenceDir){mkdirSync(evidenceDir,{recursive:true});writeFileSync(resolve(evidenceDir,"capabilities-green.html"),await page.content());await page.screenshot({path:resolve(evidenceDir,"capabilities-green.png"),fullPage:true});}
} finally { await browser.close(); }
console.log("chat markdown capabilities: green");
