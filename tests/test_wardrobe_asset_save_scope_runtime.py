from pathlib import Path
import json, os, re, shutil, subprocess
import pytest
ROOT=Path(__file__).resolve().parents[1]

def cls(raw):
 s=raw.index('internal sealed class WardrobeAssetSaveScope'); b=raw.index('{',s); d=0; q=False
 for i in range(b,len(raw)):
  c=raw[i]
  if c=='"': q=not q
  if q: continue
  if c=='{': d+=1
  elif c=='}':
   d-=1
   if d==0:return raw[s:i+1].replace('internal sealed','public sealed').replace('internal Wardrobe','public Wardrobe').replace('internal void Save','public void Save')
 raise AssertionError('class not closed')

def test_wardrobe_asset_save_scope_runtime(tmp_path):
 dotnet=shutil.which('dotnet'); pf=Path(os.environ.get('ProgramFiles','C:/Program Files')); cs=sorted((pf/'dotnet/sdk').glob('*/Roslyn/bincore/csc.dll')); refs=sorted((pf/'dotnet/packs/Microsoft.NETCore.App.Ref').glob('*/ref/netcoreapp3.1'))
 if not dotnet or not cs or not refs: pytest.skip('Local .NET SDK/reference pack required')
 product='namespace VRCForge.Editor { using System;using System.Collections.Generic;using System.Linq;using UnityEngine;using UnityEditor;using VRC.SDK3.Avatars.ScriptableObjects;'+cls((ROOT/'Assets/VRCForge/Editor/WardrobeManagerWriter.cs').read_text(encoding='utf-8-sig'))+'}'
 stubs=r'''namespace UnityEngine { public class Object { public string name="";public bool dirty;public int GetInstanceID()=>GetHashCode(); } }
namespace UnityEditor { using UnityEngine;public struct GUID { public string value; public GUID(string s){value=s;} }public static class EditorUtility { public static bool IsDirty(Object o)=>o!=null&&o.dirty; }public static class AssetDatabase { public static Dictionary<string,List<Object>> Files=new Dictionary<string,List<Object>>();public static int Saves,GlobalSaves;public static string GetAssetPath(Object o)=>Files.FirstOrDefault(x=>x.Value.Contains(o)).Key??"";public static Object[] LoadAllAssetsAtPath(string p)=>Files.TryGetValue(p,out var a)?a.ToArray():Array.Empty<Object>();public static Object LoadMainAssetAtPath(string p)=>Files.TryGetValue(p,out var a)&&a.Count>0?a[0]:null;public static string AssetPathToGUID(string p)=>"guid:"+p;public static void SaveAssetIfDirty(GUID g){Saves++;foreach(var a in Files[g.value.Substring(5)])a.dirty=false;}public static void SaveAssets(){GlobalSaves++;foreach(var a in Files.Values.SelectMany(x=>x))a.dirty=false;} } }
namespace VRC.SDK3.Avatars.ScriptableObjects { using System.Collections.Generic;using UnityEngine;public class VRCExpressionsMenu:Object { public List<Control> controls=new List<Control>();public class Control { public VRCExpressionsMenu subMenu; } } }
'''
 runner=r'''class Probe { static int f;static void C(bool x,string n){System.Console.WriteLine((x?"PASS ":"FAIL ")+n);if(!x)f++;}static bool R(System.Action a){try{a();return false;}catch(InvalidOperationException){return true;}}public static int Main(){var a=new Object{name="a"};var b=new Object{name="b"};var r=new VRCExpressionsMenu{name="r"};var c=new VRCExpressionsMenu{name="c"};r.controls.Add(new VRCExpressionsMenu.Control{subMenu=c});c.controls.Add(new VRCExpressionsMenu.Control{subMenu=r});var n=new VRCExpressionsMenu{name="n"};var clip=new Object{name="clip"};var u=new Object{name="u",dirty=true};AssetDatabase.Files["A"]=new List<Object>{a};AssetDatabase.Files["B"]=new List<Object>{b};AssetDatabase.Files["R"]=new List<Object>{r};AssetDatabase.Files["C"]=new List<Object>{c};AssetDatabase.Files["N"]=new List<Object>{n};AssetDatabase.Files["Clip"]=new List<Object>{clip};AssetDatabase.Files["U"]=new List<Object>{u};var s=new VRCForge.Editor.WardrobeAssetSaveScope(a,b,r);s.Save(n,clip);C(AssetDatabase.Saves==6&&AssetDatabase.GlobalSaves==0&&u.dirty,"scoped cyclic graph save preserves unrelated dirty");var dm=new VRCExpressionsMenu{name="dirty",dirty=true};AssetDatabase.Files["D"]=new List<Object>{new VRCExpressionsMenu{name="clean"},dm};C(R(()=>new VRCForge.Editor.WardrobeAssetSaveScope(AssetDatabase.Files["D"][0]))&&AssetDatabase.Saves==6,"dirty subasset preflight rejects");return f==0?0:1;}}'''
 # Execute the exact save boundary from each real writer, not the whole handler.
 statements=[]
 revision=os.environ.get("VRCFORGE_WARDROBE_SAVE_GIT_REF")
 for name in ("WardrobeManagerWriter", "WardrobeOutfitPartWriter", "WardrobeOutfitWriter"):
  path=f"Assets/VRCForge/Editor/{name}.cs"
  raw=subprocess.check_output(["git","show",f"{revision}:{path}"],cwd=ROOT,text=True,encoding="utf-8") if revision else (ROOT/path).read_text(encoding="utf-8-sig")
  statement=re.search(r"AssetDatabase\.SaveAssets\(\);|saveScope\.Save\([\s\S]*?\);",raw)
  assert statement, name
  statements.append('u.dirty=false;var saveScope=new VRCForge.Editor.WardrobeAssetSaveScope(a,b,r);u.dirty=true;a.dirty=true;'+statement.group(0)+'C(u.dirty&&!a.dirty,"'+name+' actual save boundary preserves unrelated dirty asset");')
 boundary='var fxController=a;var parametersAsset=b;var descriptor=new {expressionsMenu=r};bool boolParamExists=false,addMenuToggle=true;var onClip=clip;var offClip=clip;var saveRoots=new Object[]{a,b,r};' + ''.join('{'+statement+'}' for statement in statements)
 runner=runner.replace('return f==0?0:1;',boundary+'return f==0?0:1;')
 csfile=tmp_path/'Probe.cs';csfile.write_text('using System;using System.Collections.Generic;using System.Linq;using UnityEngine;using UnityEditor;using VRC.SDK3.Avatars.ScriptableObjects;'+stubs+product+runner.replace('Object','UnityEngine.Object'),encoding='utf-8');dll=tmp_path/'Probe.dll';nw=cs[-1].parents[2]/'Newtonsoft.Json.dll';cmd=[dotnet,str(cs[-1]),'-nologo','-target:exe','-nostdlib+','-langversion:8.0',f'-out:{dll}']+[f'-r:{x}' for x in refs[-1].glob('*.dll')]+[str(csfile)];built=subprocess.run(cmd,capture_output=True,text=True,timeout=60);assert built.returncode==0,built.stdout+built.stderr;shutil.copy2(nw,tmp_path/'Newtonsoft.Json.dll');(tmp_path/'Probe.runtimeconfig.json').write_text(json.dumps({'runtimeOptions':{'tfm':'netcoreapp3.1','framework':{'name':'Microsoft.NETCore.App','version':'3.1.0'}}}));run=subprocess.run([dotnet,str(dll)],capture_output=True,text=True,timeout=30);assert run.returncode==0,run.stdout+run.stderr;assert run.stdout.count('PASS ')==5,run.stdout

