"""Focused regression for per-frame SkinnedMeshRenderer BlendShape probe evidence."""
import copy
import math
import json
import os
import shutil
import subprocess
from pathlib import Path
import pytest
import runtime_observation as domain
from test_runtime_observation import args, complete_receipt
from test_curve_fx_authoring_runtime_contract import method

ROOT = Path(__file__).resolve().parents[1]


def probe():
    return [{
        "rendererPath": "Root/Avatar/Body",
        "materialIndex": 0,
        "propertyNames": ["_DissolveParams"],
        "blendShapeNames": ["BodyHide", "Smile.L"],
    }]


def test_blendshape_probe_receipt_validates_real_weight_rows(tmp_path):
    request, receipt = complete_receipt(tmp_path)
    request["rendererProbes"] = probe()
    receipt["rendererProbes"] = copy.deepcopy(request["rendererProbes"])
    row = {**probe()[0], "rendererComponentIndex": 0, "rendererInstanceId": 77,
           "properties": [{"name": "_DissolveParams", "type": "Vector", "value": [0, 0, 0, 0], "valueSource": "material"}],
           "blendShapes": [{"name": "BodyHide", "index": 3, "weight": 42.5}, {"name": "Smile.L", "index": 7, "weight": 0.0}]}
    for frame in receipt["frames"]:
        frame["rendererProbes"] = [copy.deepcopy(row)]
    result = domain.validate_result(receipt, request)
    assert result["ok"] is True
    assert result["frames"][0]["rendererProbes"][0]["blendShapes"][0]["weight"] == 42.5


def test_blendshape_probe_rejects_missing_or_invalid_shape_evidence(tmp_path):
    request, receipt = complete_receipt(tmp_path)
    request["rendererProbes"] = probe()
    receipt["rendererProbes"] = copy.deepcopy(request["rendererProbes"])
    row = {**probe()[0], "rendererComponentIndex": 0,
           "properties": [{"name": "_DissolveParams", "value": 0, "valueSource": "material"}],
           "blendShapes": [{"name": "BodyHide", "index": 3, "weight": math.nan}]}
    for frame in receipt["frames"]:
        frame["rendererProbes"] = [copy.deepcopy(row)]
    with pytest.raises(ValueError, match="blendshape"):
        domain.validate_result(receipt, request)


def test_blendshape_probe_request_rejects_empty_or_duplicate_names(tmp_path):
    for names in ([], ["Smile.L", "Smile.L"], ["x" * 129]):
        with pytest.raises(ValueError, match="blendShapeNames"):
            domain.prepare_request({**args(), "rendererProbes": [{**probe()[0], "blendShapeNames": names}]}, tmp_path)


def test_actual_csharp_blendshape_sampling_changes_between_frames_and_rejects_invalid_state(tmp_path):
    source = (ROOT / "Assets/VRCForge/Editor/RuntimeObservationTool.cs").read_text(encoding="utf-8")
    actual = method(source, "internal static JArray ReadRendererBlendShapes")
    seam = r'''using System;using System.Collections.Generic;using Newtonsoft.Json.Linq;
class Renderer {}
class Mesh { public Dictionary<string,int> Indices=new Dictionary<string,int>(); public int GetBlendShapeIndex(string n)=>Indices.TryGetValue(n,out var i)?i:-1; }
class SkinnedMeshRenderer:Renderer { public Mesh sharedMesh; public Dictionary<int,float> Weights=new Dictionary<int,float>(); public float GetBlendShapeWeight(int i)=>Weights[i]; }
'''
    runner = r'''static void Check(bool ok,string message){if(!ok)throw new Exception(message);}
static int Main(){var mesh=new Mesh();mesh.Indices["BodyHide"]=3;mesh.Indices["Smile.L"]=7;var r=new SkinnedMeshRenderer{sharedMesh=mesh};r.Weights[3]=10;r.Weights[7]=20;var a=ReadRendererBlendShapes(r,"Root/Avatar/Body",new[]{"BodyHide","Smile.L"});Check((float)a[0]["weight"]==10&& (int)a[0]["index"]==3,"first frame");r.Weights[3]=75;r.Weights[7]=2;var b=ReadRendererBlendShapes(r,"Root/Avatar/Body",new[]{"BodyHide","Smile.L"});Check((float)b[0]["weight"]==75&& (float)b[1]["weight"]==2,"second frame");bool missing=false;try{ReadRendererBlendShapes(r,"Body",new[]{"Missing"});}catch(ArgumentException){missing=true;}Check(missing,"missing shape");bool noMesh=false;try{ReadRendererBlendShapes(new SkinnedMeshRenderer(),"Body",new[]{"BodyHide"});}catch(InvalidOperationException){noMesh=true;}Check(noMesh,"missing mesh");r.Weights[3]=float.NaN;bool nonFinite=false;try{ReadRendererBlendShapes(r,"Body",new[]{"BodyHide"});}catch(InvalidOperationException){nonFinite=true;}Check(nonFinite,"nonfinite weight");Console.WriteLine("PASS two frame weights, missing shape/mesh, nonfinite rejection");return 0;}'''
    base = Path(os.environ["DOTNET_ROOT"]) if os.environ.get("DOTNET_ROOT") else Path.home() / "AppData" / "Local" / "Microsoft" / "dotnet"
    compiler = sorted((base / "sdk").glob("*/Roslyn/bincore/csc.dll"))[-1]
    refs = sorted((base / "packs/Microsoft.NETCore.App.Ref").glob("*/ref/net8.0"))[-1]
    newtonsoft = compiler.parents[2] / "Newtonsoft.Json.dll"
    cs = tmp_path / "BlendshapeProbe.cs"
    cs.write_text(seam + "class Probe {" + actual + runner + "}", encoding="utf-8")
    dll = tmp_path / "BlendshapeProbe.dll"
    command = [str(base / "dotnet.exe"), str(compiler), "/nologo", "/target:exe", f"/out:{dll}", *[f"/r:{p}" for p in refs.glob("*.dll")], f"/r:{newtonsoft}", str(cs)]
    result = subprocess.run(command, capture_output=True, text=True, timeout=60)
    assert result.returncode == 0, result.stdout + result.stderr
    shutil.copy2(newtonsoft, tmp_path / "Newtonsoft.Json.dll")
    (tmp_path / "BlendshapeProbe.runtimeconfig.json").write_text(json.dumps({"runtimeOptions": {"tfm": "net8.0", "framework": {"name": "Microsoft.NETCore.App", "version": "8.0.0"}}}))
    result = subprocess.run([str(base / "dotnet.exe"), str(dll)], capture_output=True, text=True, timeout=30)
    assert result.returncode == 0, result.stdout + result.stderr
