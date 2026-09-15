from __future__ import annotations

import json
import os
from pathlib import Path
import shutil
import subprocess

import pytest


ROOT = Path(__file__).resolve().parents[1]
SOURCE = ROOT / "Assets/VRCForge/Editor/Generic/UnityAvatarAuthoringCrud.cs"


def _method(source: str) -> str:
    marker = "internal static JObject ReadFxSceneReference"
    start = source.index(marker)
    end = source.index("\n        private static object DescribeAnimatorState", start)
    return source[start:end].replace("internal static", "public static", 1)


def _program() -> str:
    source = SOURCE.read_text(encoding="utf-8-sig")
    helper = _method(source)
    return f'''
using System;
using System.IO;
using System.Collections.Generic;
using Newtonsoft.Json.Linq;
using VRC.SDK3.Avatars.Components;
namespace VRC.SDK3.Avatars.Components {{
    public class VRCAvatarDescriptor {{ public enum AnimLayerType {{ Base=0, Additive=1, Gesture=2, Action=3, FX=4 }} }}
}}
static class Probe {{
    {helper}
    static int failures;
    static void Check(bool value, string label) {{ if (!value) {{ Console.WriteLine("FAIL " + label); failures++; }} }}
    static bool Reject(Func<JObject> action) {{ try {{ action(); return false; }} catch (InvalidOperationException) {{ return true; }} }}
    static string Block(long id, string body) => "--- !u!114 &" + id + "\\nMonoBehaviour:\\n" + body + "\\n--- !u!1 &999\\nGameObject:\\n";
    static string Layers(string entries) => "  baseAnimationLayers:\\n" + entries + "  specialAnimationLayers:\\n  - isEnabled: 0\\n";
    static string Fx(string guid) => "  - isEnabled: 0\\n    type: 4\\n    animatorController: {{fileID: 9100000, guid: " + guid + ",\\n      type: 2}}\\n    mask: {{fileID: 0}}\\n    isDefault: 0\\n";
    static string EmptyFx() => "  baseAnimationLayers:\\n  specialAnimationLayers:\\n";
    public static int Main(string[] args) {{
        var a = new string('a', 32); var b = new string('b', 32);
        var valid = Block(42, Layers(Fx(a))) + Block(43, Layers(Fx(b)));
        var resolved = ReadFxSceneReference(valid, 42);
        Check((long)resolved["fileID"] == 9100000L && (string)resolved["guid"] == a, "exact descriptor and cross-line FX guid selected");
        Check((string)ReadFxSceneReference(valid, 42)["guid"] != b, "sibling descriptor guid is not accepted");
        Check(Reject(() => ReadFxSceneReference(valid + Block(42, Layers(Fx(a))), 42)), "duplicate descriptor rejected");
        Check(Reject(() => ReadFxSceneReference(Block(42, Layers(Fx(a) + Fx(b))), 42)), "duplicate FX layer rejected");
        Check(Reject(() => ReadFxSceneReference(valid, 99)), "missing descriptor rejected");
        Check(Reject(() => ReadFxSceneReference(valid.Replace("&42\\n", "&42 stripped\\n"), 42)), "stripped descriptor rejected");
        var empty = ReadFxSceneReference(Block(42, EmptyFx()), 42);
        Check((long)empty["fileID"] == 0L && (string)empty["guid"] == "", "empty FX list returns zero reference");
        if (args.Length == 3) {{
            var real = ReadFxSceneReference(File.ReadAllText(args[0]), ulong.Parse(args[1]));
            Check((long)real["fileID"] == 9100000L && (string)real["guid"] == args[2], "real scene FX reference matches expected identity");
        }}
        return failures == 0 ? 0 : 1;
    }}
}}
'''


def _compile_and_run(temp: Path, args: list[str]) -> subprocess.CompletedProcess[str]:
    dotnet_root = Path(os.environ.get("ProgramFiles", "C:/Program Files")) / "dotnet"
    dotnet = shutil.which("dotnet")
    compilers = sorted((dotnet_root / "sdk").glob("*/Roslyn/bincore/csc.dll"))
    refs = sorted((dotnet_root / "packs/Microsoft.NETCore.App.Ref").glob("*/ref/netcoreapp3.1"))
    if not dotnet or not compilers or not refs:
        pytest.skip("Local .NET 3.1 SDK/reference pack required")
    compiler = compilers[-1]
    newtonsoft = compiler.parents[2] / "Newtonsoft.Json.dll"
    source = temp / "Probe.cs"
    dll = temp / "Probe.dll"
    source.write_text(_program(), encoding="utf-8")
    command = [dotnet, str(compiler), "-nologo", "-target:exe", "-nostdlib+", "-langversion:8.0", f"-out:{dll}"]
    command += [f"-r:{path}" for path in refs[-1].glob("*.dll")]
    command += [f"-r:{newtonsoft}", str(source)]
    compiled = subprocess.run(command, capture_output=True, text=True, timeout=60)
    assert compiled.returncode == 0, compiled.stdout + compiled.stderr
    shutil.copy2(newtonsoft, temp / "Newtonsoft.Json.dll")
    (temp / "Probe.runtimeconfig.json").write_text(
        json.dumps({"runtimeOptions": {"tfm": "netcoreapp3.1", "framework": {"name": "Microsoft.NETCore.App", "version": "3.1.0"}}}),
        encoding="utf-8",
    )
    result = subprocess.run([dotnet, str(dll), *args], capture_output=True, text=True, timeout=30)
    return result


def test_read_fx_scene_reference_matches_exact_serialized_descriptor(tmp_path: Path) -> None:
    result = _compile_and_run(tmp_path, [])
    assert result.returncode == 0, result.stdout + result.stderr


def test_read_fx_scene_reference_matches_real_scene_copy_when_configured(tmp_path: Path) -> None:
    scene = os.environ.get("VRCFORGE_ENSURE_FX_SCENE_PATH", "").strip()
    descriptor_id = os.environ.get("VRCFORGE_ENSURE_FX_DESCRIPTOR_ID", "").strip()
    expected_guid = os.environ.get("VRCFORGE_ENSURE_FX_EXPECTED_GUID", "").strip()
    if not (scene and descriptor_id and expected_guid):
        pytest.skip("real scene evidence requires explicit scene path, descriptor id, and expected guid")
    result = _compile_and_run(tmp_path, [scene, descriptor_id, expected_guid])
    assert result.returncode == 0, result.stdout + result.stderr
