from __future__ import annotations

import json
import os
from pathlib import Path
import shutil
import subprocess

import pytest


ROOT = Path(__file__).resolve().parents[1]


def test_parameter_writer_runtime_failure_and_readback_paths(tmp_path: Path) -> None:
    dotnet = shutil.which("dotnet")
    program_files = Path(os.environ.get("ProgramFiles", "C:/Program Files"))
    compilers = sorted((program_files / "dotnet" / "sdk").glob("*/Roslyn/bincore/csc.dll"))
    refs = sorted((program_files / "dotnet" / "packs/Microsoft.NETCore.App.Ref").glob("*/ref/netcoreapp3.1"))
    if not dotnet or not compilers or not refs:
        pytest.skip("Local .NET SDK/reference pack required")
    newtonsoft = compilers[-1].parents[2] / "Newtonsoft.Json.dll"
    source_ref = os.environ.get("VRCFORGE_PARAMETER_WRITER_GIT_REF", "")
    if source_ref:
        checked_out = subprocess.run(
            ["git", "show", f"{source_ref}:Assets/VRCForge/Editor/AvatarParameterWriter.cs"],
            cwd=ROOT, capture_output=True, text=True, timeout=30, check=False,
        )
        assert checked_out.returncode == 0, checked_out.stderr
        raw_product = checked_out.stdout
    else:
        raw_product = (ROOT / "Assets/VRCForge/Editor/AvatarParameterWriter.cs").read_text(encoding="utf-8-sig")
    product = "\n".join(line for line in raw_product.splitlines() if not line.startswith("using "))
    stubs = r'''
namespace VRCForge.Core.MCP {
 public sealed class VRCForgeCommandAttribute:System.Attribute {public VRCForgeCommandAttribute(string toolId){} public string Summary{get;set;} }
 public sealed class VRCForgeInputAttribute:System.Attribute {public VRCForgeInputAttribute(string x){ } public bool IsRequired{get;set;} }
 public static class VRCForgeToolResult {
  public static object Failed(string m)=>new JObject{{"ok",false},{"status","failed"},{"message",m}};
  public static object Completed(string m,object p)=>new JObject{{"ok",true},{"status","completed"},{"message",m},{"payload",JObject.FromObject(p)}};
 }
}
namespace UnityEngine {
 public class Object {public string name="";public bool dirty;}
 public class Scene {public bool isLoaded=true;public bool IsValid()=>true;}
 public class GameObject:Object {public Scene scene=new Scene();}
 public class Transform:Object {public Transform parent;}
}
namespace UnityEditor {
 using UnityEngine;
 public static class EditorUtility {public static bool IsDirty(Object o)=>o!=null&&o.dirty;public static bool IsPersistent(Object o)=>false;public static void SetDirty(Object o){o.dirty=true;}}
 public static class Undo {public static void RegisterCompleteObjectUndo(Object o,string s){} }
 public static class AssetDatabase {
  public static VRC.SDK3.Avatars.ScriptableObjects.VRCExpressionParameters live;
  public static bool ReturnNull; public static bool SaveFailure; public static void Backup(string path){backup=live.parameters.Select(Clone).ToArray();}
  static VRC.SDK3.Avatars.ScriptableObjects.VRCExpressionParameters.Parameter[] backup;
  public static void Restore(string path){if(backup!=null)live.parameters=backup.Select(Clone).ToArray();}
  public static string GetAssetPath(Object o)=>"Assets/Avatar/Parameters.asset";
  public static string AssetPathToGUID(string p)=>"guid:"+p;
  public static void SaveAssets(){if(SaveFailure)throw new InvalidOperationException("save failed");live.dirty=false;}
  public static void Refresh(){}
  public static void ImportAsset(string p,ImportAssetOptions options){}
  public static T LoadAssetAtPath<T>(string p) where T:Object => ReturnNull?null:live as T;
 static VRC.SDK3.Avatars.ScriptableObjects.VRCExpressionParameters.Parameter Clone(VRC.SDK3.Avatars.ScriptableObjects.VRCExpressionParameters.Parameter p)=>new VRC.SDK3.Avatars.ScriptableObjects.VRCExpressionParameters.Parameter{name=p.name,valueType=p.valueType,defaultValue=p.defaultValue,saved=p.saved,networkSynced=p.networkSynced};
 }
 public enum ImportAssetOptions{ForceSynchronousImport=1,ForceUpdate=2}
 public static class Resources {public static VRC.SDK3.Avatars.Components.VRCAvatarDescriptor[] Items=Array.Empty<VRC.SDK3.Avatars.Components.VRCAvatarDescriptor>();public static T[] FindObjectsOfTypeAll<T>()=>Items as T[] ?? Array.Empty<T>();}
}
namespace VRC.SDK3.Avatars.ScriptableObjects { public class VRCExpressionParameters:UnityEngine.Object {public Parameter[] parameters;public enum ValueType{Bool,Int,Float} public class Parameter {public string name;public ValueType valueType;public float defaultValue;public bool saved,networkSynced;} } }
namespace VRC.SDK3.Avatars.Components { public class VRCAvatarDescriptor:UnityEngine.Object {public UnityEngine.GameObject gameObject=new UnityEngine.GameObject();public UnityEngine.Transform transform=new UnityEngine.Transform();public VRC.SDK3.Avatars.ScriptableObjects.VRCExpressionParameters expressionParameters;} }
namespace VRCForge.Editor {
 using UnityEngine;using UnityEditor;using VRC.SDK3.Avatars.Components;using VRC.SDK3.Avatars.ScriptableObjects;
 internal static class ExpressionWritePersistence {public static bool Throw;public static int Calls;public static object Capture(Object o)=>o;public static object SaveAndVerify(object b,Object a,VRCAvatarDescriptor d,bool n,bool m){Calls++;if(Throw)throw new InvalidOperationException("save failed");a.dirty=false;return new JObject{{"persisted",true},{"verified",true}};}}
 public static class WriteAnimationCurveTool {internal sealed class AssetEditRecovery {string path;public void Capture(string p){path=p;AssetDatabase.Backup(p);}public void Begin(){}public void Complete(){}public bool Restore(){AssetDatabase.Restore(path);return true;}}}
}
'''
    runner = r'''
class Probe {
 static int failures; static void Check(bool ok,string name){Console.WriteLine((ok?"PASS ":"FAIL ")+name);if(!ok)failures++;}
 static Newtonsoft.Json.Linq.JObject Run(bool rollback,bool saveFailure,bool badReadback,bool dirty){
  var p=new VRCExpressionParameters.Parameter{name="Mode",valueType=VRCExpressionParameters.ValueType.Int,defaultValue=3,saved=true,networkSynced=true};
  var asset=new VRCExpressionParameters{parameters=new[]{p},dirty=dirty}; UnityEditor.AssetDatabase.live=asset;
  var d=new VRCAvatarDescriptor{name="Avatar",transform=new UnityEngine.Transform{name="Avatar"},expressionParameters=asset}; UnityEditor.Resources.Items=new[]{d};
  VRCForge.Editor.ExpressionWritePersistence.Throw=saveFailure; VRCForge.Editor.ExpressionWritePersistence.Calls=0; UnityEditor.AssetDatabase.SaveFailure=saveFailure; UnityEditor.AssetDatabase.ReturnNull=badReadback;
  var q=rollback?new JObject{{"avatarPath","Avatar"},{"parameterNames",new JArray{new JObject{{"name","Mode"},{"valueType","Bool"},{"defaultValue",1.0},{"saved",false},{"networkSynced",false}}}}}:new JObject{{"avatarPath","Avatar"},{"suggestions",new JArray{new JObject{{"name","Mode"}}}}};
  return JObject.FromObject(rollback?VRCForge.Editor.AvatarParameterRollbackTool.HandleCommand(q):VRCForge.Editor.AvatarParameterOptimizationApplier.HandleCommand(q));
 }
 public static int Main(){
  var failed=Run(false,true,false,false);Check((bool)failed["ok"]==false&&UnityEditor.AssetDatabase.live.parameters[0].valueType==VRCExpressionParameters.ValueType.Int,"save failure returns failed and restores optimization target");
  var good=Run(true,false,false,false);var after=(JArray)good["payload"]["after"];Check((bool)good["ok"]&&VRCForge.Editor.ExpressionWritePersistence.Calls==1&&(string)after[0]["valueType"]=="Bool"&&(bool)after[0]["saved"]==false,"rollback success persists and reads back requested values");
  var bad=Run(false,false,true,false);Check((bool)bad["ok"]==false&&VRCForge.Editor.ExpressionWritePersistence.Calls==1,"helper readback failure is rejected");
  var dirty=Run(false,false,false,true);Check((bool)dirty["ok"]==false&&VRCForge.Editor.ExpressionWritePersistence.Calls==0,"dirty target is rejected before persistence");
  return failures==0?0:1;
 }
}
'''
    cs = tmp_path / "Probe.cs"
    cs.write_text("using System;using System.Linq;using System.Collections.Generic;using Newtonsoft.Json.Linq;using VRCForge.Core.MCP;using UnityEditor;using UnityEngine;using VRC.SDK3.Avatars.Components;using VRC.SDK3.Avatars.ScriptableObjects;" + stubs + product + runner, encoding="utf-8")
    dll = tmp_path / "Probe.dll"
    command = [dotnet, str(compilers[-1]), "-nologo", "-target:exe", "-nostdlib+", "-langversion:8.0", f"-out:{dll}"] + [f"-r:{p}" for p in refs[-1].glob("*.dll")] + [f"-r:{newtonsoft}", str(cs)]
    compiled = subprocess.run(command, capture_output=True, text=True, timeout=60)
    assert compiled.returncode == 0, compiled.stdout + compiled.stderr
    shutil.copy2(newtonsoft, tmp_path / "Newtonsoft.Json.dll")
    (tmp_path / "Probe.runtimeconfig.json").write_text(json.dumps({"runtimeOptions":{"tfm":"netcoreapp3.1","framework":{"name":"Microsoft.NETCore.App","version":"3.1.0"}}}), encoding="utf-8")
    result = subprocess.run([dotnet, str(dll)], capture_output=True, text=True, timeout=30)
    assert result.returncode == 0, result.stdout + result.stderr
    assert result.stdout.count("PASS ") == 4, result.stdout
