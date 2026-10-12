"""Execute the real setup window with deterministic Unity editor state."""
from __future__ import annotations

import json
import os
import subprocess
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
SOURCE = ROOT / "Assets/VRCForge/Setup/Editor/VRCForgeSetupWindow.cs"
LOCALIZATION = SOURCE.with_name("VRCForgeLocalization.cs")
HARNESS = r'''
using System;
using System.Collections.Generic;
using System.Reflection;
using VRCForge.Setup;
namespace UnityEngine {
 public enum SystemLanguage { English, Chinese, ChineseSimplified, ChineseTraditional }
 public static class Application { public static SystemLanguage systemLanguage; public static bool isBatchMode; public static void OpenURL(string x) {} }
 public struct Vector2 { public Vector2(float x,float y) {} }
 public class GUIContent { public string text; public GUIContent(string value) {text=value;} }
 public static class GUILayout { public static string Click; public static readonly List<string> Buttons=new List<string>(); public static bool Button(string x) { Buttons.Add(x); if(Click==x){Click=null;return true;} return false; } }
}
namespace UnityEditor {
 public class EditorWindow { public UnityEngine.Vector2 minSize; public UnityEngine.GUIContent titleContent; public static T GetWindow<T>(string x) where T:new() { return new T(); } public void Repaint() {} }
 public static class EditorPrefs {
  private static readonly Dictionary<string,string> Values=new Dictionary<string,string>();
  public static string GetString(string key,string fallback) {return Values.TryGetValue(key,out var value)?value:fallback;}
  public static void SetString(string key,string value) {Values[key]=value;}
 }
 public class MenuItem:Attribute { public MenuItem(string x){} }
 public class InitializeOnLoadAttribute:Attribute {}
 public static class EditorApplication { public static bool isCompiling,isUpdating; public static double timeSinceStartup; public static Action delayCall; public static string Menu; public static bool ExecuteMenuItem(string path){Menu=path;return true;} }
 public static class EditorGUIUtility { public static string systemCopyBuffer; }
 public static class EditorUtility { public static bool scriptCompilationFailed; }
 public static class SessionState { public static bool GetBool(string k,bool d) { return d; } public static void SetBool(string k,bool v){} }
 public enum MessageType { Info, Warning, Error }
 public static class EditorStyles { public static object boldLabel,wordWrappedLabel; }
 public static class EditorGUILayout {
  public static int NextLanguage=-1;
  public static int Popup(string label,int selected,string[] choices) {int result=NextLanguage<0?selected:NextLanguage;NextLanguage=-1;return result;}
  public static int ScrollDepth; public static bool UsedScroll;
  public static UnityEngine.Vector2 BeginScrollView(UnityEngine.Vector2 position){ScrollDepth++;UsedScroll=true;return position;}
  public static void EndScrollView(){ScrollDepth--;}
  public static readonly List<string> Rows=new List<string>(); public static readonly List<string> Help=new List<string>(); public static readonly List<string> Labels=new List<string>();
  public static void LabelField(string text,object style) { Labels.Add(text); if(style is string) Rows.Add(text+":"+style); }
  public static void HelpBox(string text,MessageType type) { Help.Add(text); }
  public static void Space(float x){}
  public class HorizontalScope:IDisposable { public void Dispose(){} }
 }
}
namespace UnityEditor.PackageManager {
 public class PackageInfo {
  public string name,version; public static bool Complete=true,Json=true,TestFramework=false; public static string SdkVersion="3.9.0";
  public static PackageInfo[] GetAllRegisteredPackages() {
   var result=new List<PackageInfo>();
   if(Complete) { result.Add(new PackageInfo{name="com.vrchat.base",version=SdkVersion}); result.Add(new PackageInfo{name="com.vrchat.avatars",version=SdkVersion}); }
   if(Json) result.Add(new PackageInfo{name="com.unity.nuget.newtonsoft-json"});
   if(TestFramework) result.Add(new PackageInfo{name="com.unity.test-framework"});
   return result.ToArray();
  }
 }
}
class Probe {
 static void Invoke(object window,string name) { typeof(VRCForgeSetupWindow).GetMethod(name,BindingFlags.NonPublic|BindingFlags.Instance).Invoke(window,null); }
 static void Expect(object window,string status) {
  UnityEditor.EditorGUILayout.Rows.Clear(); UnityEditor.EditorGUILayout.Help.Clear(); UnityEditor.EditorGUILayout.Labels.Clear(); UnityEngine.GUILayout.Buttons.Clear(); Invoke(window,"OnGUI");
  if(!UnityEditor.EditorGUILayout.UsedScroll || UnityEditor.EditorGUILayout.ScrollDepth!=0) throw new Exception("setup guidance must stay in a balanced scroll view");
  var rows=UnityEditor.EditorGUILayout.Rows;
  if(rows.Count!=2 || rows[0]!="VRChat Avatar SDK:"+status) throw new Exception("Expected SDK row "+status+", actual: "+string.Join("|",rows));
  var expectedTestFramework=UnityEditor.PackageManager.PackageInfo.TestFramework ? "Installed" : "Not installed";
  if(rows[1]!="Unity Test Framework:"+expectedTestFramework) throw new Exception("Test Framework status must depend on package registration, not SDK/compiler status");
  if(UnityEngine.GUILayout.Buttons.Contains("Add Test Framework in Unity")==UnityEditor.PackageManager.PackageInfo.TestFramework) throw new Exception("Test Framework action must depend only on its own missing package");
  if(status!="Ready" && UnityEditor.EditorGUILayout.Help.Exists(x=>x.Contains("Core can compile and start"))) throw new Exception("premature readiness guidance");
  bool hasDownload=UnityEngine.GUILayout.Buttons.Contains("Download Avatar SDK");
  bool hasSteps=UnityEditor.EditorGUILayout.Labels.Exists(x=>x.Contains("Manual SDK Installation"));
  if(hasDownload==UnityEditor.PackageManager.PackageInfo.Complete || hasSteps==UnityEditor.PackageManager.PackageInfo.Complete) throw new Exception("SDK import guidance must appear only while SDK missing");
 }
 static int Main() {
  UnityEngine.Application.systemLanguage=UnityEngine.SystemLanguage.English;
  var window=new VRCForgeSetupWindow(); Invoke(window,"OnEnable");
  Expect(window,"Ready");
  UnityEditor.EditorUtility.scriptCompilationFailed=true;
  Expect(window,"Compilation failed");
  UnityEditor.EditorApplication.isCompiling=true;
  Expect(window,"Installing / compiling");
  UnityEditor.EditorApplication.isCompiling=false; UnityEditor.EditorApplication.isUpdating=true;
  Expect(window,"Installing / compiling");
  UnityEditor.EditorApplication.isUpdating=false; UnityEditor.EditorUtility.scriptCompilationFailed=false;
  Expect(window,"Ready");
  // Package changes must be picked up without manually pressing Refresh.
  UnityEditor.PackageManager.PackageInfo.Complete=false; UnityEditor.EditorApplication.timeSinceStartup=2;
  Invoke(window,"OnInspectorUpdate"); Expect(window,"Missing");
  UnityEditor.PackageManager.PackageInfo.Complete=true; UnityEditor.EditorApplication.timeSinceStartup=4;
  Invoke(window,"OnInspectorUpdate"); Expect(window,"Ready");
  // Newer SDKs must not acquire a speculative Test Framework requirement.
  if(UnityEditor.EditorGUILayout.Help.Exists(x=>x.Contains("com.unity.test-framework"))) throw new Exception("new SDK false Test Framework requirement");
  UnityEditor.PackageManager.PackageInfo.SdkVersion="3.5.0"; Invoke(window,"OnEnable"); Expect(window,"Missing");
  if(!UnityEditor.EditorGUILayout.Help.Exists(x=>x.Contains("com.unity.test-framework"))) throw new Exception("SDK 3.5.0 missing Test Framework guidance");
  if(!UnityEngine.GUILayout.Buttons.Contains("Add Test Framework in Unity")) throw new Exception("missing Test Framework repair entry");
  UnityEditor.PackageManager.PackageInfo.TestFramework=true; Invoke(window,"OnEnable"); Expect(window,"Ready");
  UnityEditor.PackageManager.PackageInfo.Json=false; Invoke(window,"OnEnable"); Expect(window,"Missing");
  if(!UnityEditor.EditorGUILayout.Help.Exists(x=>x.Contains("com.unity.nuget.newtonsoft-json"))) throw new Exception("missing JSON guidance");
  UnityEngine.GUILayout.Click="Add missing JSON package in Unity"; Expect(window,"Missing");
  if(UnityEditor.EditorGUIUtility.systemCopyBuffer!="com.unity.nuget.newtonsoft-json" || UnityEditor.EditorApplication.Menu!="Window/Package Manager") throw new Exception("JSON button must copy package name and open Package Manager");
  window=new VRCForgeSetupWindow();
  UnityEditor.PackageManager.PackageInfo.Complete=false; UnityEditor.PackageManager.PackageInfo.TestFramework=false;
  Invoke(window,"OnEnable"); Expect(window,"Missing");
  if(UnityEditor.EditorGUILayout.Help.Exists(x=>x.Contains("com.unity."))) throw new Exception("empty project should guide SDK import before transitive packages");
  if(!UnityEditor.EditorGUILayout.Help.Exists(x=>x.Contains("older manual Avatar SDK 3.5.0"))) throw new Exception("optional old SDK guidance must be visible before SDK import");
  UnityEngine.GUILayout.Click="Add Test Framework in Unity"; Expect(window,"Missing");
  if(UnityEditor.EditorGUIUtility.systemCopyBuffer!="com.unity.test-framework" || UnityEditor.EditorApplication.Menu!="Window/Package Manager") throw new Exception("Test Framework button must copy package name and open Package Manager");
  if(!UnityEditor.EditorGUILayout.Help.Exists(x=>x.Contains("Package name copied:") && x.Contains("1.1.33"))) throw new Exception("copied Test Framework instruction missing");
  if(UnityEditor.PackageManager.PackageInfo.TestFramework) throw new Exception("button must not install packages");
  // Four independent SDK / Test Framework combinations; SDK 3.9 stays ready without TF.
  UnityEditor.PackageManager.PackageInfo.Json=true;
  UnityEditor.PackageManager.PackageInfo.SdkVersion="3.9.0";
  foreach(var sdk in new[]{false,true}) foreach(var tf in new[]{false,true}) {
   UnityEditor.PackageManager.PackageInfo.Complete=sdk; UnityEditor.PackageManager.PackageInfo.TestFramework=tf;
   window=new VRCForgeSetupWindow(); Invoke(window,"OnEnable"); Expect(window,sdk ? "Ready" : "Missing");
   if(tf && UnityEditor.EditorGUILayout.Help.Exists(x=>x.Contains("older manual Avatar SDK 3.5.0"))) throw new Exception("installed Test Framework must not show install guidance even without SDK");
  }
  // Language selection remains a display preference and survives new windows.
  UnityEngine.Application.systemLanguage=UnityEngine.SystemLanguage.ChineseSimplified;
  if(VRCForgeLocalization.Text("English","中文")!="中文") throw new Exception("default language must follow the system");
  int changed=0; VRCForgeLocalization.LanguageChanged+=()=>changed++;
  UnityEditor.EditorGUILayout.NextLanguage=2; VRCForgeLocalization.DrawLanguageSelector();
  if(VRCForgeLocalization.Text("English","中文")!="English" || UnityEditor.EditorPrefs.GetString("VRCForge.Language","")!="en") throw new Exception("English override must persist");
  window=new VRCForgeSetupWindow(); Invoke(window,"OnEnable"); Expect(window,"Ready");
  if(window.titleContent.text!="VRCForge Setup") throw new Exception("English window title");
  UnityEngine.Application.systemLanguage=UnityEngine.SystemLanguage.English;
  UnityEditor.EditorGUILayout.NextLanguage=1; Invoke(window,"OnGUI");
  if(VRCForgeLocalization.Text("English","中文")!="中文" || window.titleContent.text!="VRCForge 设置") throw new Exception("Simplified Chinese selection and title refresh");
  window=new VRCForgeSetupWindow(); Invoke(window,"OnEnable"); Invoke(window,"OnGUI");
  if(window.titleContent.text!="VRCForge 设置") throw new Exception("language must persist for new windows");
  if(!VRCForgeSetupWindow.DependenciesReady()) throw new Exception("language must not change readiness");
  UnityEditor.EditorGUILayout.NextLanguage=0; VRCForgeLocalization.DrawLanguageSelector();
  if(VRCForgeLocalization.Text("English","中文")!="English" || changed!=3) throw new Exception("system selection/event contract");
  Console.WriteLine("setup readiness state transitions passed"); return 0;
 }
}
'''


def test_setup_registered_packages_do_not_mask_compilation_failure(tmp_path: Path):
    dotnet = Path(os.environ.get("DOTNET_ROOT", Path.home() / "AppData/Local/Microsoft/dotnet"))
    compilers = sorted((dotnet / "sdk").glob("*/Roslyn/bincore/csc.dll"))
    refs = sorted((dotnet / "packs/Microsoft.NETCore.App.Ref").glob("*/ref/net8.0"))
    if not compilers or not refs:
        pytest.skip("Local .NET SDK required; no Unity is launched")
    harness = tmp_path / "Probe.cs"
    harness.write_text(HARNESS, encoding="utf-8")
    dll = tmp_path / "Probe.dll"
    # Child processes are test-owned, finite, local-only, and receive no credentials.
    result = subprocess.run(
        [str(dotnet / "dotnet.exe"), str(compilers[-1]), "-nologo", "-target:exe", "-langversion:9.0",
         f"-out:{dll}", *[f"-r:{path}" for path in refs[-1].glob("*.dll")], str(SOURCE), str(LOCALIZATION), str(harness)],
        capture_output=True, text=True, timeout=60,
    )
    assert result.returncode == 0, result.stdout + result.stderr
    (tmp_path / "Probe.runtimeconfig.json").write_text(json.dumps({"runtimeOptions": {
        "tfm": "net8.0", "framework": {"name": "Microsoft.NETCore.App", "version": "8.0.0"}}}), encoding="utf-8")
    result = subprocess.run([str(dotnet / "dotnet.exe"), str(dll)], capture_output=True, text=True, timeout=30)
    assert result.returncode == 0, result.stdout + result.stderr


def test_setup_manual_install_guidance_uses_official_sdk_entry():
    source = SOURCE.read_text(encoding="utf-8")
    assert '"https://vrchat.com/home/download"' in source
    assert "Manual SDK Installation" in source
    assert "Download Avatars SDK" in source
    assert "Assets > Import Package > Custom Package" in source
    assert "VCC" not in source and "ALCOM" not in source
