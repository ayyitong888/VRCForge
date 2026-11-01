import React, { useEffect, useState } from "react";
import { createRoot } from "react-dom/client";
import { invoke, isTauri } from "@tauri-apps/api/core";
import { listen } from "@tauri-apps/api/event";
import { currentMonitor, getCurrentWindow, LogicalSize, PhysicalPosition } from "@tauri-apps/api/window";
import { AlertCircle, Check, ChevronDown, ChevronUp, Download, ExternalLink, Folder, Loader2 } from "lucide-react";
import { Button } from "../../src/components/ui/button";
import { Card } from "../../src/components/ui/card";
import { Checkbox } from "../../src/components/ui/checkbox";
import { Progress } from "../../src/components/ui/progress";
import { DesktopTitlebarFrame } from "../../src/components/workspace/desktop-titlebar";
import { initializeI18n, setLocale } from "../../src/i18n";
import { copy } from "./copy";
import "../../src/styles.css";
import "./homepage-tokens.css";
import "./layout.css";

type Language = keyof typeof copy;
type Screen = "loading" | "ready" | "detect" | "fresh-confirm" | "running" | "progress" | "complete" | "failed";
type SetupInfo = {version:string;uninstall:boolean;installDir:string;dataDir:string;installedVersion:string|null;requiredBytes:number;availableBytes:number;running:boolean};
type Location = {valid:boolean;availableBytes:number;reason:string};
type Result = {installDir:string;dataDir:string;dataCleared:boolean;logPath:string};
type ProgressEvent = {stage:string;percent:number};
const arts = ["wardrobe","materials","ai","unity","skills","backup"];
const initialLanguage:Language = navigator.language.startsWith("ja") ? "ja" : navigator.language.startsWith("zh") ? "zh" : "en";
const extra = {
 zh:{loading:"正在读取安装信息",need:"需要",free:"可用",agree:"请先同意用户协议",prepare:"正在准备",extract:"正在解压程序文件",activate:"正在更新程序",register:"正在创建快捷方式和安装登记",cleanup:"正在完成安装",uninstall:"正在移除程序文件和快捷方式",webview:"正在准备 WebView2",failed:"未能完成操作",exported:"日志已导出",working:"请等待当前操作完成",terms:"用户协议",privacy:"隐私说明"},
 en:{loading:"Reading setup information",need:"Required",free:"Available",agree:"Please agree to the terms first",prepare:"Preparing",extract:"Unpacking application files",activate:"Updating the application",register:"Creating shortcuts and application registration",cleanup:"Finishing setup",uninstall:"Removing application files and shortcuts",webview:"Preparing WebView2",failed:"The operation could not be completed",exported:"Logs exported",working:"Please wait for the operation to finish",terms:"User Agreement",privacy:"Privacy Notice"},
 ja:{loading:"インストール情報を読み込み中",need:"必要",free:"空き",agree:"先に利用規約に同意してください",prepare:"準備中",extract:"プログラムを展開中",activate:"プログラムを更新中",register:"ショートカットとアプリを登録中",cleanup:"インストールを完了しています",uninstall:"プログラムとショートカットを削除中",webview:"WebView2 を準備中",failed:"操作を完了できませんでした",exported:"ログを出力しました",working:"処理が終わるまでお待ちください",terms:"利用規約",privacy:"プライバシー"},
};

function Setup() {
 const [lang,setLang] = useState<Language>(initialLanguage);
 const [languageOpen,setLanguageOpen] = useState(false);
 const [info,setInfo] = useState<SetupInfo>();
 const [screen,setScreen] = useState<Screen>("loading");
 const [custom,setCustom] = useState(false);
 const [details,setDetails] = useState(false);
 const [installDir,setInstallDir] = useState("");
 const [location,setLocation] = useState<Location>();
 const [desktop,setDesktop] = useState(true);
 const [startMenu,setStartMenu] = useState(true);
 const [keep,setKeep] = useState(true);
 const [agreed,setAgreed] = useState(false);
 const [fresh,setFresh] = useState(false);
 const [progress,setProgress] = useState<ProgressEvent>({stage:"prepare",percent:0});
 const [result,setResult] = useState<Result>();
 const [error,setError] = useState("");
 const [notice,setNotice] = useState("");
 const [card,setCard] = useState(0);
 const t=copy[lang],e=extra[lang],uninstall=info?.uninstall ?? false,busy=screen==="progress";
 const formatBytes=(n:number)=>new Intl.NumberFormat(lang,{maximumFractionDigits:1}).format(n/1073741824)+" GB";
 const transition=(next:Screen)=>{setCustom(false);setDetails(false);setLanguageOpen(false);setNotice("");setScreen(next);};
 const report=(cause:unknown)=>{setError(String(cause));transition("failed");};
 const changeLanguage=(next:Language)=>{setLang(next);setLanguageOpen(false);void setLocale(next==="zh"?"zh-CN":next==="ja"?"ja-JP":"en-US");document.documentElement.lang=next;};
 const openDocument=async(kind:"guide"|"terms"|"privacy"="guide")=>{
  const file=kind==="terms"?"LICENSE":kind==="privacy"?"USER_MANUAL.md#privacy-boundary":{zh:"README.md",ja:"README.ja.md",en:"README.en.md"}[lang];
  try {await invoke("plugin:opener|open_url",{url:`https://github.com/ayyitong888/VRCForge/blob/main/${file}`});}
  catch(cause){setError(String(cause));}
 };
 useEffect(()=>{
  if(!isTauri()){setError("Open the Windows setup executable to install or uninstall VRCForge.");setScreen("failed");return;}
  void invoke<SetupInfo>("load_state").then(value=>{setInfo(value);setInstallDir(value.installDir);setScreen(value.installedVersion&&!value.uninstall?"detect":"ready");}).catch(report);
 },[]);
 useEffect(()=>{
  if(!isTauri())return;
  let disposed=false,stop:(()=>void)|undefined;
  void listen<ProgressEvent>("setup-progress",event=>setProgress(event.payload)).then(fn=>{if(disposed)fn();else stop=fn;});
  return()=>{disposed=true;stop?.();};
 },[]);
 useEffect(()=>{
  if(!installDir||uninstall)return;
  const timer=window.setTimeout(()=>{void invoke<Location>("check_install_path",{path:installDir}).then(setLocation).catch(cause=>setLocation({valid:false,availableBytes:0,reason:String(cause)}));},300);
  return()=>window.clearTimeout(timer);
 },[installDir,uninstall]);
 useEffect(()=>{
  if(!busy||uninstall)return;
  const timer=window.setInterval(()=>setCard(value=>(value+1)%arts.length),4000);
  return()=>window.clearInterval(timer);
 },[busy,uninstall]);
 useEffect(()=>{
  if(!isTauri())return;
  const appWindow=getCurrentWindow();
  let cancelled=false,stopScale:(()=>void)|undefined;
  const fit=async()=>{
   const monitor=await currentMonitor();
   if(cancelled)return;
   const width=monitor?Math.min(720,Math.floor(monitor.workArea.size.width/monitor.scaleFactor)-24):720;
   const maxHeight=monitor?Math.floor(monitor.workArea.size.height/monitor.scaleFactor)-24:1000;
   document.documentElement.style.setProperty("--setup-width",`${width}px`);
   document.documentElement.style.setProperty("--setup-max-height",`${maxHeight}px`);
   const height=document.querySelector(".setup-window")?.getBoundingClientRect().height||440;
   await appWindow.setSize(new LogicalSize(width,Math.min(maxHeight,Math.ceil(height))));
   if(monitor){
    const position=await appWindow.outerPosition();
    const x=Math.max(monitor.workArea.position.x,Math.min(position.x,monitor.workArea.position.x+monitor.workArea.size.width-width*monitor.scaleFactor));
    const y=Math.max(monitor.workArea.position.y,Math.min(position.y,monitor.workArea.position.y+monitor.workArea.size.height-Math.min(maxHeight,height)*monitor.scaleFactor));
    if(x!==position.x||y!==position.y)await appWindow.setPosition(new PhysicalPosition(Math.round(x),Math.round(y)));
   }
  };
  const observer=new ResizeObserver(()=>void fit());
  const element=document.querySelector(".setup-window");if(element)observer.observe(element);
  void appWindow.onScaleChanged(()=>void fit()).then(stop=>{if(cancelled)stop();else stopScale=stop;});
  return()=>{cancelled=true;observer.disconnect();stopScale?.();};
 },[]);
 const perform=async()=>{
  setError("");setProgress({stage:"prepare",percent:0});transition("progress");
  try {
   const value=uninstall
    ? await invoke<Result>("begin_uninstall",{request:{keepData:keep}})
    : await invoke<Result>("begin_install",{request:{installDir,fresh,desktopShortcut:desktop,startMenu}});
   setResult(value);transition("complete");
  }catch(cause){report(cause);}
 };
 const start=async()=>{
  if(!uninstall&&!agreed){setNotice(e.agree);return;}
  try{
   if(await invoke<boolean>("is_app_running",{path:installDir})){transition("running");return;}
   await perform();
  }catch(cause){report(cause);}
 };
 const closeAndContinue=async()=>{
  try{await invoke("close_running_app",{path:installDir});await perform();}catch(cause){report(cause);}
 };
 const browse=async()=>{
  try{const path=await invoke<string|null>("choose_install_dir");if(path)setInstallDir(path);}catch(cause){setError(String(cause));}
 };
 const exportLogs=async()=>{
  try{
   const text=await invoke<string>("read_setup_log");
   const url=URL.createObjectURL(new Blob([text],{type:"text/plain;charset=utf-8"}));
   const anchor=document.createElement("a");anchor.href=url;anchor.download="VRCForge-setup.log";anchor.click();URL.revokeObjectURL(url);setNotice(e.exported);
  }catch(cause){setError(String(cause));}
 };
 const close=()=>{if(busy)setNotice(e.working);else void getCurrentWindow().close();};
 const done=screen==="complete",failed=screen==="failed",original=screen==="ready"&&!uninstall;
 const hero=uninstall?"farewell":busy?arts[card]:failed?"backup":screen==="detect"||screen==="fresh-confirm"?"wardrobe":"ai";
 const heading=screen==="loading"?e.loading:uninstall?done?t.uninstalled:failed?t.uninstallFail:busy?t.uninstalling:screen==="running"?t.running:t.uninstallTitle:busy?t.cards[card][0]:done?t.installed:failed?t.fail:screen==="running"?t.running:screen==="detect"?`${t.detected} ${info?.installedVersion||""}`:t.confirmFresh;
 const description=uninstall?done?result?.dataCleared?t.cleared:t.retained:failed?"":screen==="running"?t.runningNote:t.uninstallNote:busy?t.cards[card][1]:screen==="fresh-confirm"?t.freshNote:screen==="running"?t.runningNote:screen==="detect"?t.upgradeNote:done?t.slogan:"";
 return <div className="preview-desk"><main className="setup-window">
  <DesktopTitlebarFrame version={info?.version||""} theme="light" compact onMinimize={()=>void getCurrentWindow().minimize()} onClose={close}>
   {!busy&&!done?<nav className="demo-language" aria-label="Language"><button className="language-trigger" type="button" aria-haspopup="menu" aria-expanded={languageOpen} onClick={()=>setLanguageOpen(!languageOpen)}>{({zh:"中文",ja:"日本語",en:"English"})[lang]}<ChevronDown size={11}/></button>{languageOpen?<div className="language-menu" role="menu">{([["zh","中文"],["ja","日本語"],["en","English"]] as const).map(([code,label])=><button key={code} role="menuitemradio" aria-checked={code===lang} onClick={()=>changeLanguage(code)}>{label}{code===lang?<Check size={12}/>:null}</button>)}</div>:null}</nav>:null}
  </DesktopTitlebarFrame>
  <section className={`brand-art ${original?"original-hero":""}`}>
   <img className="hero-image" src={`./assets/${original?"hero":hero}.png`} alt=""/>
   {!original?<div className="art-copy"><div className="eyebrow">{uninstall?t.uninstallTag:t.tag} ✦</div><h1>{heading}</h1><p>{description}</p></div>:null}
   {busy&&!uninstall?<div className="carousel-dots">{arts.map((_,i)=><button key={i} aria-label={t.cards[i][0]} className={card===i?"active":""} onClick={()=>setCard(i)}/>)}</div>:null}
  </section>
  <section className={`action-area ${screen==="detect"?"detection-area":""}`}>
   {screen==="loading"?<Loader2 className="animate-spin"/>:busy?<div className="progress-copy"><div className="flex items-center justify-between"><span className="flex items-center gap-2"><Loader2 size={14} className="animate-spin"/>{uninstall?t.uninstalling:t.installing}</span><span>{progress.percent}%</span></div><Progress value={progress.percent}/><div className="text-xs">{e[progress.stage as keyof typeof e]||e.prepare}</div></div>
    :done?<><Button className="hero-button" onClick={uninstall?close:()=>void invoke("launch_installed_app").then(close).catch(cause=>setError(String(cause)))}>{uninstall?t.finish:t.launch}</Button>{!uninstall?<Button variant="ghost" className="h-7 text-xs text-primary" onClick={()=>void openDocument()}>{t.guide}<ExternalLink size={12}/></Button>:result?.dataCleared?<span className="data-location">{result.dataDir}</span>:null}</>
    :failed?<><Button className="hero-button" onClick={()=>{if(info)void start();else window.location.reload();}}>{t.retry}</Button><Button variant="ghost" className="h-7 text-xs text-primary" onClick={()=>void exportLogs()}><Download size={12}/>{t.export}</Button></>
    :screen==="running"?<Button className="hero-button" onClick={()=>void closeAndContinue()}>{t.closeContinue}</Button>
    :screen==="fresh-confirm"?<><Button className="hero-button" onClick={()=>{setFresh(true);transition("ready");}}>{t.clearInstall}</Button><Button variant="ghost" onClick={()=>{setFresh(false);transition("detect");}}>{t.keepUpgrade}</Button></>
    :screen==="detect"?<><Card className="upgrade-card"><span className="flex items-center gap-2"><Check size={14}/>{t.upgrade}</span><button className="text-xs" onClick={()=>transition("fresh-confirm")}>{t.fresh}</button></Card><Button className="hero-button" disabled={!agreed} onClick={()=>void start()}>{t.upgradeButton}</Button></>
    :uninstall?<><div className="flex items-center justify-center gap-3"><Button className="hero-button" onClick={close}>{t.stay}</Button><Button variant="outline" className="h-11 rounded-full px-6" onClick={()=>void start()}>{t.uninstall}</Button></div><div className="flex items-center justify-center gap-2 text-xs"><Checkbox checked={keep} onCheckedChange={setKeep}/><span>{t.keep}</span><button aria-label={t.details} onClick={()=>setDetails(!details)}>{details?<ChevronUp size={13}/>:<ChevronDown size={13}/>}</button></div></>
    :<><Button className="hero-button" disabled={!agreed||!location?.valid} onClick={()=>void start()}>{t.install}</Button><span className="action-note">{t.slogan}</span></>}
  </section>
  {custom&&!busy&&!done?<section className="custom-panel"><div className="mb-3 flex items-center justify-between"><span className="text-sm font-semibold">{t.path}</span><span className="text-xs">{e.need} {formatBytes(info?.requiredBytes||0)} · {e.free} {formatBytes(location?.availableBytes||0)}</span></div><div className="flex gap-2"><div className="path-input flex items-center gap-2"><Folder size={15}/><input aria-label={t.path} className="min-w-0 flex-1 bg-transparent outline-none" value={installDir} readOnly={!!info?.installedVersion} onChange={ev=>setInstallDir(ev.target.value)}/></div><Button variant="outline" className="h-10 rounded-full" disabled={!!info?.installedVersion} onClick={()=>void browse()}>{t.change}</Button></div><div className="mt-2 text-xs">{location?.valid?t.valid:location?.reason}</div><div className="my-4 flex gap-6 text-xs"><label className="flex items-center gap-2"><Checkbox checked={desktop} onCheckedChange={setDesktop}/>{t.desktop}</label><label className="flex items-center gap-2"><Checkbox checked={startMenu} onCheckedChange={setStartMenu}/>{t.start}</label></div></section>:null}
  {details&&uninstall&&!busy?<section className="data-details"><span className="text-xs font-semibold">{t.data}</span><div className="data-location mt-1">{info?.dataDir}</div></section>:null}
  {error?<div role="alert" className="mx-6 mb-2 rounded-lg bg-secondary p-3 text-xs"><span className="flex items-start gap-2"><AlertCircle size={14} className="shrink-0"/>{error}</span></div>:null}
  {notice?<div role="status" className="mx-6 mb-2 text-xs">{notice}</div>:null}
  <footer className="setup-footer">{!uninstall&&!busy&&!done&&!failed&&screen!=="running"&&screen!=="loading"?<><label className="flex items-center gap-2"><Checkbox checked={agreed} onCheckedChange={setAgreed}/><span>{t.agree} <button className="text-primary" onClick={()=>void openDocument("terms")}>{t.terms}</button> · <button className="text-primary" onClick={()=>void openDocument("privacy")}>{t.privacy}</button></span></label><button onClick={()=>setCustom(!custom)} className="flex items-center gap-1">{t.custom}{custom?<ChevronUp size={12}/>:<ChevronDown size={12}/>}</button></>:<span className="ml-auto">VRCForge · Windows</span>}</footer>
 </main></div>;
}

document.documentElement.classList.remove("dark");
document.documentElement.classList.add("native-demo");
document.documentElement.dataset.theme="light";
await initializeI18n();
await setLocale(initialLanguage==="zh"?"zh-CN":initialLanguage==="ja"?"ja-JP":"en-US");
createRoot(document.getElementById("root")!).render(<Setup/>);
