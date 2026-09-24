from __future__ import annotations

import json
import os
import shutil
import subprocess
from pathlib import Path

import pytest


ROOT = Path(__file__).resolve().parents[1]
REGISTRY = ROOT / "Assets/VRCForge/Core/MCP/VRCForgeToolRegistry.cs"
COMMAND = ROOT / "Assets/VRCForge/Core/MCP/VRCForgeCommandAttribute.cs"
INPUT = ROOT / "Assets/VRCForge/Core/MCP/VRCForgeInputAttribute.cs"
PARAMETERS = ROOT / "Assets/VRCForge/Core/MCP/VRCForgeParameterSchema.cs"
CONTRACT = ROOT / "Assets/VRCForge/Editor/MCP/VRCForgeMcpToolContract.cs"


HARNESS = r'''
using System;
using System.Linq;
using Newtonsoft.Json.Linq;
using VRCForge.Core.MCP;

namespace VRCForge.Editor {
    [VRCForgeCommand("vrc_add_component")]
    public static class AddComponentTool {
        public static object HandleCommand(JObject parameters) { return new JObject(); }
    }

    [VRCForgeCommand(null)]
    public static class NullForeignTool {
        public static object HandleCommand(JObject parameters) { return new JObject(); }
    }

    [VRCForgeCommand("vrc_add_component")]
    public static class ForeignDuplicateTool {
        public static object HandleCommand(JObject parameters) { return new JObject(); }
    }

    [VRCForgeCommand("vrc_add_component")]
    public static class AddComponentToolDrift {
        public static object HandleCommand(JObject parameters) { return new JObject(); }
    }
}

internal static class Program {
    private static void Require(bool value, string message) {
        if (!value) throw new Exception(message);
    }

    public static int Main() {
        var registry = VRCForge.Core.MCP.VRCForgeToolRegistry.DiscoverOwnedLoadedAssemblies(
            VRCForge.Editor.VRCForgeMcpToolContract.IsExpectedDeclaration);
        var tools = registry.Tools.ToArray();
        var excluded = registry.ExcludedTools.ToArray();
        Require(tools.Length == 1 && tools[0].Name == "vrc_add_component",
            "owned discovery did not retain the official declaration");
        Require(excluded.Length == 3, "foreign declarations were not retained as diagnostics");
        Require(excluded.Any(item => item.Name == "" && item.TypeName.EndsWith("NullForeignTool", StringComparison.Ordinal)),
            "null foreign ToolId was not safely excluded");
        Require(excluded.Any(item => item.TypeName.EndsWith("ForeignDuplicateTool", StringComparison.Ordinal)),
            "foreign duplicate ToolId was not excluded");
        Require(excluded.Any(item => item.TypeName.EndsWith("AddComponentToolDrift", StringComparison.Ordinal)),
            "official type drift was not excluded for SnapshotExact to reject");
        return 0;
    }
}
'''


@pytest.fixture(scope="module")
def registry_probe(tmp_path_factory: pytest.TempPathFactory):
    out = tmp_path_factory.mktemp("mcp-tool-registry-runtime")
    dotnet_root = Path(os.environ["DOTNET_ROOT"]) if os.environ.get("DOTNET_ROOT") else Path.home() / "AppData" / "Local" / "Microsoft" / "dotnet"
    compilers = sorted((dotnet_root / "sdk").glob("*/Roslyn/bincore/csc.dll"))
    refs = sorted((dotnet_root / "packs/Microsoft.NETCore.App.Ref").glob("*/ref/net8.0"))
    if not compilers or not refs:
        pytest.skip("Local .NET SDK required; no Unity is launched")
    compiler = compilers[-1]
    newtonsoft = compiler.parents[2] / "Newtonsoft.Json.dll"
    if not newtonsoft.is_file():
        pytest.skip("Bundled Newtonsoft.Json compiler reference is unavailable")
    source = out / "Probe.cs"
    source.write_text(HARNESS, encoding="utf-8")
    production_sources = []
    for path in (COMMAND, INPUT, PARAMETERS, REGISTRY, CONTRACT):
        target = out / path.name
        target.write_text(path.read_text(encoding="utf-8-sig"), encoding="utf-8")
        production_sources.append(str(target))
    dll = out / "Probe.dll"
    cmd = [str(dotnet_root / "dotnet.exe"), str(compiler), "-nologo", "-target:exe", "-langversion:9.0", f"-out:{dll}", *[f"-r:{path}" for path in refs[-1].glob("*.dll")], f"-r:{newtonsoft}", *production_sources, str(source)]
    result = subprocess.run(cmd, capture_output=True, text=True, timeout=60)
    assert result.returncode == 0, result.stdout + result.stderr
    shutil.copy2(newtonsoft, out / newtonsoft.name)
    (out / "Probe.runtimeconfig.json").write_text(json.dumps({"runtimeOptions": {"tfm": "net8.0", "framework": {"name": "Microsoft.NETCore.App", "version": "8.0.0"}}}), encoding="utf-8")
    return dotnet_root / "dotnet.exe", dll


def test_owned_registry_runtime_filters_null_duplicate_and_drifted_foreign_commands(registry_probe) -> None:
    dotnet, dll = registry_probe
    result = subprocess.run([str(dotnet), str(dll)], capture_output=True, text=True, timeout=30)
    assert result.returncode == 0, result.stdout + result.stderr
