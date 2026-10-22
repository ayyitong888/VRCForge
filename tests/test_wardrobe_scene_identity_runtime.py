"""Compile and execute the production wardrobe scene-identity guard with Unity ports stubbed."""
import json
import os
from pathlib import Path
import re
import shutil
import subprocess

import pytest


ROOT = Path(__file__).resolve().parents[1]
SOURCE_PATH = "Assets/VRCForge/Editor/WardrobeOutfitWriter.cs"


def _production_source():
    source_dir = os.environ.get("VRCFORGE_WARDROBE_SCENE_IDENTITY_SOURCE_DIR")
    if source_dir:
        path = Path(source_dir) / "WardrobeOutfitWriter.cs"
        if path.is_file():
            return path.read_text(encoding="utf-8-sig")
    revision = os.environ.get("VRCFORGE_WARDROBE_SCENE_IDENTITY_GIT_REF")
    if revision:
        return subprocess.check_output(
            ["git", "show", f"{revision}:{SOURCE_PATH}"],
            cwd=ROOT,
            text=True,
            encoding="utf-8",
        )
    return (ROOT / SOURCE_PATH).read_text(encoding="utf-8-sig")


def _identity_block(source):
    """Extract the exact production normal-scene identity code, including its rejection."""
    start_markers = (
        "var sceneObjectId = GlobalObjectId.GetGlobalObjectIdSlow(targetTransform.gameObject);",
        "if (!AssetDatabase.TryGetGUIDAndLocalFileIdentifier(targetTransform.gameObject,",
    )
    start = next((source.find(marker) for marker in start_markers if source.find(marker) >= 0), -1)
    if start < 0:
        raise AssertionError("Production normal-scene identity branch was not found")
    end_marker = "sceneObjectIds[targetPath] = sceneObjectId"
    end = source.index(end_marker, start) + len(end_marker)
    end = source.index(";", end) + 1
    return source[start:end]


def test_wardrobe_scene_identity_guard_executes_production_code(tmp_path):
    block = _identity_block(_production_source())
    dictionary_type = "ulong" if "targetObjectId" in block else "long"
    value_type = "ulong" if dictionary_type == "ulong" else "long"
    expected_id = "id" if dictionary_type == "ulong" else "(long)id"
    program = r'''
using System;
using System.Collections.Generic;

sealed class GameObject { }
sealed class Transform { public GameObject gameObject = new GameObject(); }
sealed class GuidValue { readonly string value; public GuidValue(string s) { value=s; } public override string ToString() => value; }
struct GlobalObjectId {
 public int identifierType; public ulong targetObjectId; public GuidValue assetGUID;
 public static GlobalObjectId Current;
 public static GlobalObjectId GetGlobalObjectIdSlow(GameObject value) => Current;
}
static class AssetDatabase {
 public static bool TryGetGUIDAndLocalFileIdentifier(GameObject value, out string guid, out long localId) {
  guid = ""; localId = 0; return false; // ordinary scene objects have no asset GUID/file ID in this fixture
 }
}
static class Probe {
 static void Resolve(string sceneGuid, IDictionary< string, __VALUE_TYPE__ > ids) {
  var targetTransform = new Transform(); var targetPath = "selected"; var sceneObjectIds = ids;
  __BLOCK__
 }
 static void Allowed(int type, ulong id, string objectSceneGuid, string expectedSceneGuid) {
  GlobalObjectId.Current = new GlobalObjectId { identifierType=type, targetObjectId=id, assetGUID=new GuidValue(objectSceneGuid) };
  var ids = new Dictionary<string,__VALUE_TYPE__>(); Resolve(expectedSceneGuid, ids);
  if (!ids.ContainsKey("selected") || ids["selected"] != __EXPECTED_ID__) throw new Exception("scene object ID was not retained");
 }
 static void Rejected(int type, ulong id, string objectSceneGuid, string expectedSceneGuid) {
  GlobalObjectId.Current = new GlobalObjectId { identifierType=type, targetObjectId=id, assetGUID=new GuidValue(objectSceneGuid) };
  try { Resolve(expectedSceneGuid, new Dictionary<string,__VALUE_TYPE__>()); }
  catch (InvalidOperationException ex) { if (ex.Message != "Wardrobe scene default target has no stable scene object identity.") throw; return; }
  throw new Exception("invalid scene identity was accepted");
 }
 static void Main() {
  const string sceneGuid = "0123456789abcdef0123456789abcdef";
  Allowed(2, 42UL, sceneGuid, sceneGuid);
  Rejected(2, 42UL, "fedcba9876543210fedcba9876543210", sceneGuid);
  Rejected(3, 42UL, sceneGuid, sceneGuid);
  Rejected(2, 0UL, sceneGuid, sceneGuid);
  Console.WriteLine("PASS production wardrobe scene identity and negative cases");
 }
}
'''.replace("__BLOCK__", block).replace("__VALUE_TYPE__", value_type).replace("__EXPECTED_ID__", expected_id)

    dotnet = shutil.which("dotnet")
    base = Path(os.environ.get("ProgramFiles", "C:/Program Files")) / "dotnet"
    compilers = sorted((base / "sdk").glob("*/Roslyn/bincore/csc.dll"))
    packs = sorted((base / "packs/Microsoft.NETCore.App.Ref").glob("*/ref/netcoreapp3.1"))
    if not dotnet or not compilers or not packs:
        pytest.skip("Local .NET SDK and netcoreapp3.1 reference pack required")

    cs = tmp_path / "Probe.cs"
    cs.write_text(program, encoding="utf-8")
    dll = tmp_path / "Probe.dll"
    built = subprocess.run(
        [dotnet, str(compilers[-1]), "-nologo", "-target:exe", "-nostdlib+", f"-out:{dll}"]
        + [f"-r:{item}" for item in packs[-1].glob("*.dll")]
        + [str(cs)],
        capture_output=True,
        text=True,
        timeout=60,
    )
    assert built.returncode == 0, built.stdout + built.stderr
    (tmp_path / "Probe.runtimeconfig.json").write_text(
        json.dumps({"runtimeOptions": {"tfm": "netcoreapp3.1", "framework": {"name": "Microsoft.NETCore.App", "version": "3.1.0"}}})
    )
    run = subprocess.run([dotnet, str(dll)], capture_output=True, text=True, timeout=30)
    assert run.returncode == 0, run.stdout + run.stderr
    assert "PASS production wardrobe scene identity and negative cases" in run.stdout

