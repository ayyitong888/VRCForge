"""Compile both exact production setter branches; isolate Unity persistence seams."""
import json
import os
import shutil
import subprocess
from pathlib import Path


def test_scene_component_write_does_not_save_unrelated_assets(tmp_path):
    root = Path(__file__).resolve().parents[1]
    ref = os.environ.get("VRCFORGE_COMPONENT_SAVE_GIT_REF")
    raw = (
        subprocess.check_output(
            ["git", "show", f"{ref}:Assets/VRCForge/Editor/Generic/UnityComponentCrud.cs"],
            cwd=root, text=True, encoding="utf-8",
        )
        if ref
        else (root / "Assets/VRCForge/Editor/Generic/UnityComponentCrud.cs").read_text(encoding="utf-8")
    )
    tool = raw.index("public static class SetPropertyTool")
    mutation = raw.index('failureStage = "unity_mutation";', tool)
    start = raw.index("if (serializedPath)", mutation)
    end_marker = "var afterScene = ComponentCrudCore.SaveAndResolveScene(beforeScene);"
    end = raw.index(end_marker, start) + len(end_marker)
    production_slice = raw[start:end]
    program = r'''using System;using Newtonsoft.Json.Linq;
    static class EditorUtility {public static void SetDirty(object value){}}
    static class AssetDatabase {public static bool UnrelatedDirty=true,UnrelatedSaved=false;public static void SaveAssets(){UnrelatedSaved=UnrelatedDirty;UnrelatedDirty=false;}}
    sealed class SerializedProperty { public string propertyPath="value"; }
    sealed class SerializedObject : IDisposable {
     public SerializedObject(object value){} public void UpdateIfRequiredOrScript(){}
     public SerializedProperty FindProperty(string path){ComponentCrudCore.trace+="find;";return new SerializedProperty{propertyPath=path};}
     public bool ApplyModifiedProperties(){ComponentCrudCore.trace+="apply;";return true;}
     public void Update(){ComponentCrudCore.trace+="update;";} public void Dispose(){}
    }
    static class ComponentCrudCore {public static string trace="";public static bool PrefabOverride,PrefabOverrideSaved;
     public static void SetMemberValue(object a,object b,object c){trace+="set;";}
     public static SerializedProperty ResolveSerializedProperty(SerializedObject s,string p){return s.FindProperty(p);}
     public static object ReadSerializedPropertyValue(SerializedProperty p){return 7;}
     public static void WriteSerializedPropertyValue(SerializedProperty p,object v){trace+="serialized-set;";}
     public static bool ValuesEqual(object a,object b){return true;}
     public static void RecordPropertyPrefabOverride(object c){trace+="prefab;";PrefabOverride=true;}
     public static bool SceneSaved; public static object SaveAndResolveScene(object s){trace+="save;";PrefabOverrideSaved=PrefabOverride;SceneSaved=true;return s;}}
    class Probe{static JObject Run(bool serializedPath){ComponentCrudCore.trace="";ComponentCrudCore.PrefabOverride=false;ComponentCrudCore.PrefabOverrideSaved=false;ComponentCrudCore.SceneSaved=false;
     object component=new object(),member=new object(),newValue=true,go=new object(),beforeScene=new object();string propertyPath="m_Flag";bool mutationApplied=false;string failureStage="";
    PRODUCTION_SLICE
     return new JObject{["sceneSaved"]=ComponentCrudCore.SceneSaved,["prefabOverrideSaved"]=ComponentCrudCore.PrefabOverrideSaved,["trace"]=ComponentCrudCore.trace,["unrelatedAssetSaved"]=AssetDatabase.UnrelatedSaved,["unrelatedAssetDirty"]=AssetDatabase.UnrelatedDirty,["mutationApplied"]=mutationApplied};}
     static void Main(){Console.WriteLine(new JArray(Run(false),Run(true)).ToString(Newtonsoft.Json.Formatting.None));}}
    '''.replace("PRODUCTION_SLICE", production_slice)
    base = Path(os.environ.get("ProgramFiles", "C:/Program Files")) / "dotnet"
    compiler = sorted((base / "sdk").glob("*/Roslyn/bincore/csc.dll"))[-1]
    references = sorted((base / "packs/Microsoft.NETCore.App.Ref").glob("*/ref/netcoreapp3.1"))[-1]
    dotnet = shutil.which("dotnet")
    newtonsoft = compiler.parents[2] / "Newtonsoft.Json.dll"
    source = tmp_path / "Probe.cs"
    source.write_text(program, encoding="utf-8")
    output = tmp_path / "Probe.dll"
    compile_result = subprocess.run(
        [dotnet, str(compiler), "-nologo", "-target:exe", "-nostdlib+", f"-out:{output}", f"-r:{newtonsoft}"]
        + [f"-r:{reference}" for reference in references.glob("*.dll")]
        + [str(source)], capture_output=True, text=True,
    )
    assert compile_result.returncode == 0, compile_result.stdout + compile_result.stderr
    shutil.copy2(newtonsoft, tmp_path / "Newtonsoft.Json.dll")
    (tmp_path / "Probe.runtimeconfig.json").write_text(json.dumps({
        "runtimeOptions": {"tfm": "netcoreapp3.1", "framework": {"name": "Microsoft.NETCore.App", "version": "3.1.0"}},
    }))
    run_result = subprocess.run([dotnet, str(output)], capture_output=True, text=True)
    assert run_result.returncode == 0, run_result.stdout + run_result.stderr
    member_result, serialized_result = json.loads(run_result.stdout)
    for result in (member_result, serialized_result):
        assert result["sceneSaved"] is True
        assert result["prefabOverrideSaved"] is True, result
        assert result["unrelatedAssetSaved"] is False, result
        assert result["unrelatedAssetDirty"] is True, result
        assert result["mutationApplied"] is True
    assert member_result["trace"] == "set;prefab;save;"
    assert serialized_result["trace"] == "find;serialized-set;apply;update;find;prefab;save;"
