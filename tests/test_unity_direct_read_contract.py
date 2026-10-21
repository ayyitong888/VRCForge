"""Execute the real C# no-write predicate against the Python routing contract."""
from __future__ import annotations

import json
import shutil
import subprocess
from pathlib import Path

import pytest

from test_animation_binding_selection_runtime import sdk
from test_approved_execution_payload_reads import READ_CASES
from unity_mcp_tool_contract import is_direct_read_call

ROOT = Path(__file__).resolve().parents[1]


def contract_cases():
    positives = list(READ_CASES)
    materials = READ_CASES[5][1]
    items = READ_CASES[7][1]
    animation = READ_CASES[9][1]
    positives += [
        ("vrc_scan_avatar_materials", {**materials, "includeTextures": False}),
        ("vrc_scan_avatar_items", {**items, "expectedSnapshotDigest": "a" * 64}),
        ("vrc_scan_avatar_items", {k: v for k, v in items.items() if k != "offset"}),
        ("vrc_scan_animation_bindings", {**animation, "maxKeysPerBinding": 30}),
        ("vrc_scan_animation_bindings", {**animation, "bindingView": "index", "bindingSelectors": [{"path": "", "propertyName": "m_IsActive"}], "bindingOffset": 0, "bindingLimit": 10, "clipOffset": 0, "keyOffset": 0, "maxTotalKeys": 4096, "expectedSnapshotDigest": "B" * 64}),
        ("vrc_capture_scene_view", {"statusOnly": True, "requirePlayMode": False, "captureMode": " Game_View "}),
        ("vrc_capture_scene_view", {"statusOnly": True, "requirePlayMode": True, "avatarPath": "Avatar", "includeGestureManagerParameters": True, "gestureManagerParameterNames": ["Blink"], "gestureManagerParameterPrefix": "Face"}),
    ]
    cases = []
    for tool, args in positives:
        cases.append((tool, args))
        cases.append((tool, {**args, "unknown": False}))
        for key in args:
            cases.append((tool, {k: v for k, v in args.items() if k != key}))
            for invalid in (None, [], {}, 0, 1, -1, 2001, "true", True, False):
                cases.append((tool, {**args, key: invalid}))
        if "outputPath" in args:
            cases.append((tool, {**args, "outputPath": "Assets/unapproved.json"}))
    cases += [("vrc_unknown", {}), ("vrc_apply_blendshapes", {"outputPath": "", "refreshAssets": False})]
    return cases


def test_mixed_read_routing_matches_executed_core_gate(tmp_path):
    base, compiler, refs = sdk()
    server = (ROOT / "Assets/VRCForge/Editor/MCP/VRCForgeMcpCoreServer.cs").read_text(encoding="utf-8")
    gate = server[server.index("        private static bool IsStrictNoWritePayloadRead"):server.index("        private static JObject QueueInvocation")]
    selection = (ROOT / "Assets/VRCForge/Editor/AnimationBindingReadSelection.cs").read_text(encoding="utf-8")
    selection = selection[selection.index("namespace VRCForge.Editor"):selection.index("        private sealed class Entry")] + "}}"
    source = "using System; using System.IO; using System.Linq; using Newtonsoft.Json.Linq;\n" + selection
    source += "public static class Probe {" + gate + """
 public static void Main(string[] argv) {
   var cases = JArray.Parse(File.ReadAllText(argv[0])); var results = new JArray();
   foreach (JObject item in cases) {
     try { results.Add(IsStrictNoWritePayloadRead((string)item["tool"], (JObject)item["arguments"])); }
     catch { results.Add(false); }
   }
   Console.WriteLine(results.ToString(Newtonsoft.Json.Formatting.None));
 }
}
"""
    cs, dll = tmp_path / "Probe.cs", tmp_path / "Probe.dll"
    cs.write_text(source, encoding="utf-8")
    newtonsoft = compiler.parents[2] / "Newtonsoft.Json.dll"
    compiled = subprocess.run([str(base / "dotnet.exe"), str(compiler), "-nologo", "-target:exe", "-langversion:8.0", f"-out:{dll}", *[f"-r:{p}" for p in refs.glob("*.dll")], f"-r:{newtonsoft}", str(cs)], capture_output=True, text=True, timeout=60)
    assert compiled.returncode == 0, compiled.stdout + compiled.stderr
    shutil.copy2(newtonsoft, tmp_path / "Newtonsoft.Json.dll")
    (tmp_path / "Probe.runtimeconfig.json").write_text(json.dumps({"runtimeOptions": {"tfm": "net8.0", "framework": {"name": "Microsoft.NETCore.App", "version": "8.0.0"}}}))
    cases = contract_cases()
    path = tmp_path / "cases.json"
    path.write_text(json.dumps([{"tool": name, "arguments": args} for name, args in cases]), encoding="utf-8")
    result = subprocess.run([str(base / "dotnet.exe"), str(dll), str(path)], capture_output=True, text=True, timeout=30)
    assert result.returncode == 0, result.stderr
    expected = json.loads(result.stdout)
    actual = [is_direct_read_call(name, args) for name, args in cases]
    mismatches = [(case, core, python) for case, core, python in zip(cases, expected, actual) if core != python]
    assert mismatches == []
    assert len(cases) > 1000
