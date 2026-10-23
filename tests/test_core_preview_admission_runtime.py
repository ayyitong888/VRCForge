"""Execute the production Core preview admission predicate through a C# harness."""

import json
import os
import shutil
import subprocess
from pathlib import Path

import pytest


ROOT = Path(__file__).resolve().parents[1]
CORE_SOURCE = ROOT / "Assets" / "VRCForge" / "Editor" / "MCP" / "VRCForgeMcpCoreServer.cs"


def _extract_method(source: str, marker: str) -> str:
    start = source.index(marker)
    brace = source.index("{", start)
    depth = 0
    for index in range(brace, len(source)):
        if source[index] == "{":
            depth += 1
        elif source[index] == "}":
            depth -= 1
            if depth == 0:
                return source[start : index + 1]
    raise AssertionError(marker)


def _build_program() -> str:
    source = CORE_SOURCE.read_text(encoding="utf-8-sig")
    preview_start = source.index("private static readonly HashSet<string> PreviewTools")
    preview_end = source.index("        };", preview_start) + len("        };")
    preview_tools = source[preview_start:preview_end]
    admission = _extract_method(source, "private static bool HasAllowedPreviewRequest")
    explicit = _extract_method(source, "private static bool HasExplicitPreviewRequest")
    return f'''using System;
using System.Collections.Generic;
using Newtonsoft.Json.Linq;

static class VRCForgeMcpCoreServer {{
    {preview_tools}
    {admission}
    {explicit}
    private static bool HasStrictRestoreBackupPreviewRequest(JObject arguments) => false;
    private static bool HasStrictSetupOutfitPreviewRequest(JObject arguments) => false;
    public static bool IsAllowed(string toolName, JObject arguments) => HasAllowedPreviewRequest(toolName, arguments);
}}

static class Probe {{
    static void Require(bool ok, string message) {{ if (!ok) throw new Exception(message); }}
    public static int Main() {{
        var preview = new JObject {{ ["preview"] = true }};
        Require(VRCForgeMcpCoreServer.IsAllowed("vrc_unpack_prefab", preview),
            "vrc_unpack_prefab preview was rejected by Core admission");
        Require(!VRCForgeMcpCoreServer.IsAllowed("vrc_unpack_prefab", new JObject {{ ["preview"] = false }}),
            "non-preview unpack request entered preview admission");
        Require(!VRCForgeMcpCoreServer.IsAllowed("vrc_unpack_prefab", new JObject()),
            "implicit unpack request entered preview admission");
        Console.WriteLine("PASS production Core preview admission");
        return 0;
    }}
}}
'''


@pytest.fixture(scope="module")
def compiled_probe(tmp_path_factory):
    base = Path(os.environ.get("ProgramFiles", r"C:\Program Files")) / "dotnet"
    sdks = sorted((base / "sdk").glob("*/Roslyn/bincore/csc.dll"))
    refs = sorted((base / "packs/Microsoft.NETCore.App.Ref").glob("*/ref/netcoreapp3.1"))
    newtonsoft = base / "sdk" / "3.1.426" / "Newtonsoft.Json.dll"
    dotnet = base / "dotnet.exe"
    if not sdks or not refs or not newtonsoft.exists() or not dotnet.exists():
        pytest.skip("local .NET 3.1 Roslyn references unavailable")
    folder = tmp_path_factory.mktemp("core-preview-admission")
    source = folder / "Probe.cs"
    dll = folder / "Probe.dll"
    source.write_text(_build_program(), encoding="utf-8")
    command = [str(dotnet), str(sdks[-1]), "-nologo", "-target:exe", "-nostdlib+", f"-out:{dll}"]
    command += [f"-r:{ref}" for ref in refs[-1].glob("*.dll")]
    command += [f"-r:{newtonsoft}", str(source)]
    result = subprocess.run(command, capture_output=True, text=True, timeout=60)
    assert result.returncode == 0, result.stdout + result.stderr
    (folder / "Probe.runtimeconfig.json").write_text(
        json.dumps({"runtimeOptions": {"tfm": "netcoreapp3.1", "framework": {"name": "Microsoft.NETCore.App", "version": "3.1.0"}}}),
        encoding="utf-8",
    )
    shutil.copy2(newtonsoft, folder / "Newtonsoft.Json.dll")
    return dotnet, dll


def test_core_preview_admission_runtime(compiled_probe):
    result = subprocess.run([str(compiled_probe[0]), str(compiled_probe[1])], capture_output=True, text=True, timeout=30)
    assert result.returncode == 0, result.stdout + result.stderr
    assert "PASS production Core preview admission" in result.stdout
