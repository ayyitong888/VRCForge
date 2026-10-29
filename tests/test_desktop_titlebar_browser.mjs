import assert from "node:assert/strict";
import { readFile } from "node:fs/promises";
import { createServer } from "node:http";
import { build } from "esbuild";
import { chromium } from "playwright";

const bundle = await build({
  stdin: { resolveDir: process.cwd(), loader: "tsx", contents: `
    import React from 'react';
    import {createRoot} from 'react-dom/client';
    import i18n from 'i18next';
    import {initReactI18next} from 'react-i18next';
    import {DesktopTitlebar} from './src/components/workspace/desktop-titlebar';
    import {WorkspaceHeader} from './src/components/workspace/workspace-header';
    import {LayoutSplitter} from './src/components/workspace/layout-splitter';
    import messages from './src/locales/en-US.json';
    await i18n.use(initReactI18next).init({lng:'en',resources:{en:{translation:messages}}});
    window.calls=[];window.maximized=false;window.stops=0;
    const root=createRoot(document.getElementById('root'));
    function Fixture(){const [theme,setTheme]=React.useState('light');const [view,setView]=React.useState('chat');window.showSettings=()=>setView('settings');
      React.useEffect(()=>{document.documentElement.classList.toggle('dark',theme==='dark');},[theme]);
      return <main style={{height:'100vh',background:'linear-gradient(90deg,rgb(245,232,230),rgb(230,235,245))'}}>
        <DesktopTitlebar theme={theme} onToggleTheme={()=>setTheme(theme==='light'?'dark':'light')}/>
        <div style={{display:'grid',height:'calc(100vh - 36px)',gridTemplateColumns:'240px 4px minmax(0,1fr)',gridTemplateRows:'minmax(0,1fr)'}}>
          <aside style={{height:'100%',overflow:'auto'}}>Navigation</aside>
          <LayoutSplitter side="left" value={240} min={200} max={400} title="Resize sidebar" onPointerDown={()=>{}}/>
          <section data-workspace-panel style={{height:'100%',minHeight:0,display:'flex',flexDirection:'column',overflow:'hidden'}}>
            <WorkspaceHeader activeProjectLabel="Selected Project" activeView={view} activeChatTitle="Conversation" theme={theme}
              showDoctorStartupPrompt={false} hasStartupIssue={false} healthErrors={0} healthWarnings={0} startupIssue=""
              loadingDoctor={false} loading={false} error="" onToggleTheme={()=>{}} onOpenDoctor={()=>{}}
              onRetryStartupOrHealth={()=>{}} onDismissDoctorPrompt={()=>{}} onStartRuntime={()=>{}}/>
            <div style={{flex:1,minHeight:0,overflow:'auto'}}>Workspace</div>
            <footer data-test-composer style={{height:180,flexShrink:0}}><button>Send</button></footer>
          </section>
        </div>
      </main>;}
    root.render(<Fixture/>);window.unmount=()=>root.unmount();
  ` }, bundle: true, write: false, format: "esm", jsx: "automatic",
  plugins: [{name:"native-window-fixture",setup(builder){
    builder.onResolve({filter:/^@tauri-apps\/api\/(app|core|window)$/},args=>({path:args.path,namespace:"native-fixture"}));
    builder.onLoad({filter:/.*/,namespace:"native-fixture"},()=>({contents:`
      export const isTauri=()=>true;
      export const getVersion=async()=>"7.8.9";
      export const getCurrentWindow=()=>({
        isMaximized:async()=>window.maximized,
        onResized:async fn=>{window.resizeListener=fn;return ()=>{window.stops++;window.resizeListener=null;}},
        minimize:async()=>{window.calls.push('minimize');},
        toggleMaximize:async()=>{window.calls.push('maximize');window.maximized=!window.maximized;window.resizeListener?.({});},
        close:async()=>{window.calls.push('close');}
      });`,loader:"js"}));
  }}],
});
const css = await readFile("src/theme.css", "utf8");
const server = createServer((req,res)=>{
  if(req.url==="/fixture.js"){res.setHeader("content-type","text/javascript");res.end(bundle.outputFiles[0].text);return;}
  res.end(`<style>${css} html,body{margin:0;} button{font:inherit;} .h-screen{height:100vh;} .h-full{height:100%;}</style><div id="root"></div><script type="module" src="/fixture.js"></script>`);
});
await new Promise(resolve=>server.listen(0,"127.0.0.1",resolve));
let browser;
try {
  browser=await chromium.launch({headless:true,channel:process.env.VRCFORGE_BROWSER_CHANNEL||"msedge"});
  const page=await browser.newPage({viewport:{width:1320,height:860}});
  await page.goto(`http://127.0.0.1:${server.address().port}`);
  await page.locator('[data-app-version]').filter({hasText:'7.8.9'}).waitFor();
  assert.equal(await page.locator('[data-desktop-titlebar] img').count(),0);
  assert.equal(await page.locator('[data-desktop-titlebar]').textContent(), 'VRCForge7.8.9');
  const visual=await page.locator('[data-desktop-titlebar]').evaluate(el=>({
    background:getComputedStyle(el).backgroundColor,
    border:getComputedStyle(el).borderBottomWidth,
    height:el.getBoundingClientRect().height,
    versionBackground:getComputedStyle(el.querySelector('[data-app-version]')).backgroundColor,
    drag:el.hasAttribute('data-tauri-drag-region'),
  }));
  assert.deepEqual(visual,{background:'rgba(0, 0, 0, 0)',border:'0px',height:36,versionBackground:'rgba(0, 0, 0, 0)',drag:true});
  for(const size of [{width:1100,height:720},{width:1320,height:860},{width:1920,height:1080}]) {
    await page.setViewportSize(size);
    for(const selector of ['[data-workspace-panel]','[data-layout-splitter]','[data-test-composer]']) {
      const rect=await page.locator(selector).boundingBox();
      assert.ok(rect.y+rect.height<=size.height+1,selector+' clipped below viewport');
    }
  }
  await page.getByText('Selected Project',{exact:true}).waitFor();
  await page.evaluate(()=>window.showSettings());
  await page.getByText('Settings',{exact:true}).waitFor();
  assert.equal(await page.getByText('Selected Project',{exact:true}).count(),0);
  await page.getByRole('button',{name:'Toggle theme',exact:true}).click();
  assert.equal(await page.evaluate(()=>document.documentElement.classList.contains('dark')),true);
  await page.getByRole('button',{name:'Minimize',exact:true}).click();
  await page.getByRole('button',{name:'Maximize',exact:true}).click();
  await page.getByRole('button',{name:'Restore window',exact:true}).waitFor();
  await page.getByRole('button',{name:'Restore window',exact:true}).click();
  await page.getByRole('button',{name:'Maximize',exact:true}).waitFor();
  await page.getByRole('button',{name:'Close',exact:true}).click();
  assert.deepEqual(await page.evaluate(()=>window.calls),['minimize','maximize','maximize','close']);
  await page.evaluate(()=>window.unmount());
  await page.waitForFunction(()=>window.stops===1);
  console.log('Titlebar: runtime version, continuous background, theme, native control dispatch and listener cleanup passed.');
} finally {await browser?.close();await new Promise(resolve=>server.close(resolve));}
