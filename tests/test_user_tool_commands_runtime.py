from __future__ import annotations

import json
import os
import shutil
import subprocess
from pathlib import Path

import pytest

from skill_packages import SkillPackageService
from user_unity_tool_service import UserUnityToolService


ROOT = Path(__file__).resolve().parents[1]
SOURCES = [
    ROOT / "Assets/VRCForge/Core/MCP/VRCForgeCommandAttribute.cs",
    ROOT / "Assets/VRCForge/Core/MCP/VRCForgeInputAttribute.cs",
    ROOT / "Assets/VRCForge/Core/MCP/VRCForgeParameterSchema.cs",
    ROOT / "Assets/VRCForge/Core/MCP/VRCForgeToolRegistry.cs",
    ROOT / "Assets/VRCForge/Core/MCP/VRCForgeToolResult.cs",
    ROOT / "Assets/VRCForge/Editor/MCP/VRCForgeMcpToolContract.cs",
    ROOT / "Assets/VRCForge/Editor/UserToolCommands.cs",
]

HARNESS = r'''
using System;
using System.IO;
using System.Linq;
using System.Security.Cryptography;
using System.Text;
using Newtonsoft.Json.Linq;
using VRCForge.Core.MCP;

namespace UnityEngine { public static class Application { public static string dataPath; } }

namespace VRCForge.Editor {
    [VRCForgeCommand("user.echo", Summary = "echo")]
    public static class UserEchoTool {
        public const string VRCForgeUserToolPackageId = "com.example.user";
        public const string VRCForgeUserToolPackageDigest = "aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa";
        public const string VRCForgeUserToolGeneratedStamp = "stamp-1";
        public static object HandleCommand(JObject parameters) { return new JObject { ["echo"] = parameters["value"] }; }
    }
}

namespace VRCForge.UserTools { public static class P_0c57402ca6fb { public static class DigestStamp { public const string PackageId = "com.example.user"; public const string PackageDigest = "aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa"; } } }

internal static class Program {
    private static string Sha(string value) { using (var sha = SHA256.Create()) return BitConverter.ToString(sha.ComputeHash(Encoding.UTF8.GetBytes(value))).Replace("-", "").ToLowerInvariant(); }
    private static string Sha(byte[] value) { using (var sha = SHA256.Create()) return BitConverter.ToString(sha.ComputeHash(value)).Replace("-", "").ToLowerInvariant(); }
    private static void Require(bool value, string message) { if (!value) throw new Exception(message); }
    public static int Main(string[] args) {
        var root = Path.Combine(Path.GetTempPath(), "vrcforge-user-tool-runtime-" + Guid.NewGuid().ToString("N"));
        var recordDir = Path.Combine(root, "Assets", "VRCForgeUserTools", "com.example.user", "Editor");
        Directory.CreateDirectory(recordDir);
        UnityEngine.Application.dataPath = Path.Combine(root, "Assets");
        var sourcePath = Path.Combine(recordDir, "UserEcho.cs");
        File.WriteAllText(sourcePath, "user-tool-source");
        var descriptor = VRCForgeToolRegistry.Describe(typeof(VRCForge.Editor.UserEchoTool));
        var record = new JObject {
            ["schema"] = "vrcforge.user_unity_tool_install.v1", ["packageId"] = "com.example.user", ["packageDigest"] = VRCForge.Editor.UserEchoTool.VRCForgeUserToolPackageDigest,
            ["enabled"] = true, ["stampType"] = "VRCForge.UserTools.P_0c57402ca6fb.DigestStamp",
            ["tools"] = new JArray(new JObject { ["toolId"] = "user.echo", ["typeName"] = typeof(VRCForge.Editor.UserEchoTool).FullName, ["source"] = "Assets/VRCForgeUserTools/com.example.user/Editor/UserEcho.cs", ["sourceSha256"] = Sha(Encoding.UTF8.GetBytes("user-tool-source")), ["description"] = "when-to-use: echo. when-NOT-to-use: no.", ["inputSchema"] = descriptor.CreateInputSchema() })
        };
        File.WriteAllText(Path.Combine(recordDir, "tool-package.json"), record.ToString(Newtonsoft.Json.Formatting.None));
        var listed = VRCForge.Editor.VRCForgeUserToolCatalog.List();
        Require((bool)listed["tools"][0]["available"], "approved user tool was not listed as available: " + listed.ToString());
        File.WriteAllText(sourcePath, "drifted-source");
        var drifted = VRCForge.Editor.VRCForgeUserToolCatalog.List();
        Require((string)drifted["tools"][0]["tools"][0]["reason"] == "source_hash_mismatch", "source drift was not rejected");
        File.WriteAllText(sourcePath, "user-tool-source");
        var result = VRCForge.Editor.VRCForgeUserToolCatalog.Invoke(new JObject { ["packageId"] = "com.example.user", ["packageDigest"] = VRCForge.Editor.UserEchoTool.VRCForgeUserToolPackageDigest, ["toolId"] = "user.echo", ["arguments"] = new JObject { ["value"] = "ok" } });
        Require((string)result["status"] == "complete" && (string)result["result"]["echo"] == "ok", "approved user tool did not invoke");
        var listedEnvelope = (VRCForgeToolResult)VRCForge.Editor.ListUserToolsTool.HandleCommand(new JObject());
        Require((bool)listedEnvelope.ToStructuredContent()["success"], "list wrapper did not use the canonical success envelope");
        var invokeEnvelope = (VRCForgeToolResult)VRCForge.Editor.InvokeUserToolTool.HandleCommand(new JObject { ["packageId"] = "com.example.user", ["packageDigest"] = VRCForge.Editor.UserEchoTool.VRCForgeUserToolPackageDigest, ["toolId"] = "user.echo", ["arguments"] = new JObject { ["value"] = "ok" } });
        Require((bool)invokeEnvelope.ToStructuredContent()["success"], "invoke wrapper did not use the canonical success envelope");
        var bad = VRCForge.Editor.VRCForgeUserToolCatalog.Invoke(new JObject { ["packageId"] = "com.example.user", ["packageDigest"] = "bbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbb", ["toolId"] = "user.echo", ["arguments"] = new JObject() });
        Require((string)bad["errorCode"] == "package_digest_mismatch", "wrong package digest was not rejected");
        Directory.Delete(root, true);
        return 0;
    }
    private static string VRCForgeUserCatalogSchema() { return "vrcforge.user-tool-package.v1"; }
}
'''

REPAIR_HARNESS = HARNESS.replace(
    '            ["tools"] = new JArray(new JObject {',
    '            ["repairs"] = new JArray(new JObject { ["toolId"] = "vrc_get_property", ["coreVersion"] = "wrong-core", ["toolContractVersion"] = "wrong-contract" }),\n            ["tools"] = new JArray(new JObject {',
    1,
).replace(
    'Require((bool)listed["tools"][0]["available"], "approved user tool was not listed as available: " + listed.ToString());',
    'Require((bool)listed["tools"][0]["available"] == false && (string)listed["tools"][0]["tools"][0]["reason"] == "core_baseline_mismatch", "foreign repair baseline was accepted: " + listed.ToString());',
    1,
).replace(
    '        var result = VRCForge.Editor.VRCForgeUserToolCatalog.Invoke(new JObject { ["packageId"] = "com.example.user", ["packageDigest"] = VRCForge.Editor.UserEchoTool.VRCForgeUserToolPackageDigest, ["toolId"] = "user.echo", ["arguments"] = new JObject { ["value"] = "ok" } });\n        Require((string)result["status"] == "complete" && (string)result["result"]["echo"] == "ok", "approved user tool did not invoke");',
    '        var result = VRCForge.Editor.VRCForgeUserToolCatalog.Invoke(new JObject { ["packageId"] = "com.example.user", ["packageDigest"] = VRCForge.Editor.UserEchoTool.VRCForgeUserToolPackageDigest, ["toolId"] = "user.echo", ["arguments"] = new JObject { ["value"] = "ok" } });\n        Require((string)result["status"] == "unavailable" && (string)result["errorCode"] == "package_unavailable", "foreign repair baseline was invokable");\n        Directory.Delete(root, true);\n        return 0;',
    1,
)

REPAIR_HARNESS = REPAIR_HARNESS.replace(
    '        Directory.Delete(root, true);\n        return 0;',
    r'''        var repair = (JObject)record["repairs"][0];
        repair["coreVersion"] = VRCForge.Editor.VRCForgeMcpToolContract.ProductVersion;
        repair["toolContractVersion"] = VRCForge.Editor.VRCForgeMcpToolContract.ToolContractVersion;
        repair["toolId"] = "vrc.official";
        File.WriteAllText(Path.Combine(recordDir, "tool-package.json"), record.ToString(Newtonsoft.Json.Formatting.None));
        var unknown = VRCForge.Editor.VRCForgeUserToolCatalog.List();
        Require((bool)unknown["tools"][0]["available"] == false && (string)unknown["tools"][0]["tools"][0]["reason"] == "repair_tool_unknown", "unknown original repair tool was accepted");
        var blocked = VRCForge.Editor.VRCForgeUserToolCatalog.Invoke(new JObject { ["packageId"] = "com.example.user", ["packageDigest"] = VRCForge.Editor.UserEchoTool.VRCForgeUserToolPackageDigest, ["toolId"] = "user.echo", ["arguments"] = new JObject() });
        Require((string)blocked["status"] == "unavailable", "unknown original repair tool remained invokable");
        repair["toolId"] = "vrc_get_property";
        File.WriteAllText(Path.Combine(recordDir, "tool-package.json"), record.ToString(Newtonsoft.Json.Formatting.None));
        var known = VRCForge.Editor.VRCForgeUserToolCatalog.List();
        Require((bool)known["tools"][0]["available"], "known official repair baseline rejected");
        Directory.Delete(root, true);
        return 0;''',
    1,
)


@pytest.fixture(scope="module")
def user_tool_probe(tmp_path_factory: pytest.TempPathFactory):
    out = tmp_path_factory.mktemp("user-tool-runtime")
    dotnet_root = Path(os.environ["DOTNET_ROOT"]) if os.environ.get("DOTNET_ROOT") else Path.home() / "AppData" / "Local" / "Microsoft" / "dotnet"
    compilers = sorted((dotnet_root / "sdk").glob("*/Roslyn/bincore/csc.dll"))
    refs = sorted((dotnet_root / "packs/Microsoft.NETCore.App.Ref").glob("*/ref/net8.0"))
    if not compilers or not refs:
        pytest.skip("Local .NET SDK required; no Unity is launched")
    compiler = compilers[-1]
    newtonsoft = compiler.parents[2] / "Newtonsoft.Json.dll"
    if not newtonsoft.is_file():
        pytest.skip("Bundled Newtonsoft.Json compiler reference is unavailable")
    files = []
    for source in SOURCES:
        target = out / source.name
        target.write_text(source.read_text(encoding="utf-8-sig"), encoding="utf-8")
        files.append(str(target))
    probe = out / "Probe.cs"
    probe.write_text(HARNESS, encoding="utf-8")
    dll = out / "Probe.dll"
    cmd = [str(dotnet_root / "dotnet.exe"), str(compiler), "-nologo", "-target:exe", "-langversion:9.0", f"-out:{dll}", *[f"-r:{path}" for path in refs[-1].glob("*.dll")], f"-r:{newtonsoft}", *files, str(probe)]
    result = subprocess.run(cmd, capture_output=True, text=True, timeout=60)
    assert result.returncode == 0, result.stdout + result.stderr
    shutil.copy2(newtonsoft, out / newtonsoft.name)
    (out / "Probe.runtimeconfig.json").write_text(json.dumps({"runtimeOptions": {"tfm": "net8.0", "framework": {"name": "Microsoft.NETCore.App", "version": "8.0.0"}}}), encoding="utf-8")
    return dotnet_root / "dotnet.exe", dll


def test_user_tool_catalog_runtime_validates_identity_and_invokes_only_approved_loaded_type(user_tool_probe) -> None:
    dotnet, dll = user_tool_probe
    result = subprocess.run([str(dotnet), str(dll)], capture_output=True, text=True, timeout=30)
    assert result.returncode == 0, result.stdout + result.stderr


@pytest.fixture(scope="module")
def repair_tool_probe(tmp_path_factory: pytest.TempPathFactory):
    out = tmp_path_factory.mktemp("user-tool-repair-runtime")
    dotnet_root = Path(os.environ["DOTNET_ROOT"]) if os.environ.get("DOTNET_ROOT") else Path.home() / "AppData" / "Local" / "Microsoft" / "dotnet"
    compilers = sorted((dotnet_root / "sdk").glob("*/Roslyn/bincore/csc.dll"))
    refs = sorted((dotnet_root / "packs/Microsoft.NETCore.App.Ref").glob("*/ref/net8.0"))
    if not compilers or not refs: pytest.skip("Local .NET SDK required; no Unity is launched")
    compiler = compilers[-1]; newtonsoft = compiler.parents[2] / "Newtonsoft.Json.dll"
    files = []
    for source in SOURCES:
        target = out / source.name; target.write_text(source.read_text(encoding="utf-8-sig"), encoding="utf-8"); files.append(str(target))
    probe = out / "Probe.cs"; probe.write_text(REPAIR_HARNESS, encoding="utf-8"); dll = out / "Probe.dll"
    result = subprocess.run([str(dotnet_root / "dotnet.exe"), str(compiler), "-nologo", "-target:exe", "-langversion:9.0", f"-out:{dll}", *[f"-r:{path}" for path in refs[-1].glob("*.dll")], f"-r:{newtonsoft}", *files, str(probe)], capture_output=True, text=True, timeout=60)
    assert result.returncode == 0, result.stdout + result.stderr
    shutil.copy2(newtonsoft, out / newtonsoft.name); (out / "Probe.runtimeconfig.json").write_text(json.dumps({"runtimeOptions": {"tfm": "net8.0", "framework": {"name": "Microsoft.NETCore.App", "version": "8.0.0"}}}), encoding="utf-8")
    return dotnet_root / "dotnet.exe", dll


def test_foreign_repair_baseline_cannot_authorize_user_tool(repair_tool_probe) -> None:
    dotnet, dll = repair_tool_probe
    result = subprocess.run([str(dotnet), str(dll)], capture_output=True, text=True, timeout=30)
    assert result.returncode == 0, result.stdout + result.stderr


def test_real_export_import_prepare_apply_emits_runtime_record(tmp_path: Path) -> None:
    source = tmp_path / "package"
    source.mkdir()
    (source / "manifest.json").write_text(json.dumps({
        "id": "com.example.user", "name": "User", "version": "1.0.0", "author": "Example",
        "description": "A verified user tool", "min_vrcforge_version": "1.0.0", "permissions": [],
        "entrypoints": {"unityTools": "descriptor.json", "unitytoolsource": "UserEcho.cs"},
    }), encoding="utf-8")
    (source / "descriptor.json").write_text(json.dumps({
        "schema": "vrcforge.user_unity_tools.v1", "tools": [{
            "toolId": "user.echo", "typeName": "VRCForge.Editor.UserEchoTool", "source": "UserEcho.cs",
            "description": "when-to-use: echo. when-NOT-to-use: other tools.",
            "inputSchema": {"type": "object", "properties": {}, "additionalProperties": False},
        }],
    }), encoding="utf-8")
    (source / "UserEcho.cs").write_text("""using Newtonsoft.Json.Linq;
using VRCForge.Core.MCP;
namespace VRCForge.Editor {
    [VRCForgeCommand(\"user.echo\", Summary = \"when-to-use: echo. when-NOT-to-use: other tools.\")]
    public static class UserEchoTool {
        public static object HandleCommand(JObject parameters) { return new JObject { [\"echo\"] = parameters[\"value\"] }; }
    }
}
""", encoding="utf-8")
    store = SkillPackageService(tmp_path / "store", vrcforge_version="1.8.0")
    package = store.export_dev(source, tmp_path / "user.vsk").package_path
    store.import_package(package, dev_mode=True)
    store.set_enabled("com.example.user", True)
    project = tmp_path / "UnityProject"
    (project / "Assets").mkdir(parents=True)
    plan = UserUnityToolService(store).prepare_installed("com.example.user", project)
    applied = UserUnityToolService(store).apply(plan)
    assert applied["status"] == "pending_compile"
    record = json.loads((project / "Assets/VRCForgeUserTools/com.example.user/Editor/tool-package.json").read_text(encoding="utf-8"))
    assert record["schema"] == "vrcforge.user_unity_tool_install.v1"
    assert record["tools"][0]["source"].startswith("Assets/VRCForgeUserTools/com.example.user/Editor/")
    assert len(record["tools"][0]["sourceSha256"]) == 64
    assert "+DigestStamp" in record["stampType"]


REAL_HARNESS = r'''
using System;
using System.IO;
using Newtonsoft.Json.Linq;
using VRCForge.Core.MCP;
namespace UnityEngine { public static class Application { public static string dataPath; } }
internal static class Program {
    private static void Require(bool value, string message) { if (!value) throw new Exception(message); }
    public static int Main(string[] args) {
        var project = args[0]; UnityEngine.Application.dataPath = Path.Combine(project, "Assets");
        var recordPath = Path.Combine(project, "Assets/VRCForgeUserTools/com.example.user/Editor/tool-package.json");
        var record = JObject.Parse(File.ReadAllText(recordPath));
        var digest = (string)record["packageDigest"];
        var listed = (VRCForgeToolResult)VRCForge.Editor.ListUserToolsTool.HandleCommand(new JObject());
        Require((bool)listed.ToStructuredContent()["success"], "list envelope failed");
        var invoked = (VRCForgeToolResult)VRCForge.Editor.InvokeUserToolTool.HandleCommand(new JObject { ["packageId"] = "com.example.user", ["packageDigest"] = digest, ["toolId"] = "user.echo", ["arguments"] = new JObject { ["value"] = "from-installed-source" } });
        var payload = invoked.ToStructuredContent();
        Require((bool)payload["success"] && (string)payload["data"]["echo"] == "from-installed-source", "installed source did not invoke");
        var failed = (VRCForgeToolResult)VRCForge.Editor.InvokeUserToolTool.HandleCommand(new JObject { ["packageId"] = "com.example.user", ["packageDigest"] = digest, ["toolId"] = "user.echo", ["arguments"] = new JObject { ["value"] = "fail" } });
        Require(!(bool)failed.ToStructuredContent()["success"], "user failure was reported as success");
        var failedResult = (VRCForgeToolResult)VRCForge.Editor.InvokeUserToolTool.HandleCommand(new JObject { ["packageId"] = "com.example.user", ["packageDigest"] = digest, ["toolId"] = "user.echo", ["arguments"] = new JObject { ["value"] = "result-fail" } });
        Require(!(bool)failedResult.ToStructuredContent()["success"], "VRCForgeToolResult failure was reported as success");
        var waiting = (VRCForgeToolResult)VRCForge.Editor.InvokeUserToolTool.HandleCommand(new JObject { ["packageId"] = "com.example.user", ["packageDigest"] = digest, ["toolId"] = "user.echo", ["arguments"] = new JObject { ["value"] = "waiting" } });
        var waitingPayload = waiting.ToStructuredContent();
        Require((bool)waitingPayload["success"] && (string)waitingPayload["_mcp_status"] == "pending" && waitingPayload["_mcp_poll_interval"] != null, "VRCForgeToolResult waiting state was completed or lost");
        var source = Path.Combine(project, "Assets/VRCForgeUserTools/com.example.user/Editor/UserEcho.cs"); File.WriteAllText(source, "drift");
        var drift = (VRCForgeToolResult)VRCForge.Editor.InvokeUserToolTool.HandleCommand(new JObject { ["packageId"] = "com.example.user", ["packageDigest"] = digest, ["toolId"] = "user.echo", ["arguments"] = new JObject() });
        Require(!(bool)drift.ToStructuredContent()["success"], "source drift was accepted");
        return 0;
    }
}
'''


def test_emitted_installed_sources_compile_and_invoke(tmp_path: Path) -> None:
    source = tmp_path / "package"; source.mkdir()
    manifest = {"id": "com.example.user", "name": "User", "version": "1.0.0", "author": "Example", "description": "A verified user tool", "min_vrcforge_version": "1.0.0", "permissions": [], "entrypoints": {"unityTools": "descriptor.json", "unitytoolsource": "UserEcho.cs"}}
    descriptor = {"schema": "vrcforge.user_unity_tools.v1", "tools": [{"toolId": "user.echo", "typeName": "VRCForge.Editor.UserEchoTool", "source": "UserEcho.cs", "description": "when-to-use: echo. when-NOT-to-use: other tools.", "inputSchema": {"type": "object", "properties": {}, "additionalProperties": False}}]}
    (source / "manifest.json").write_text(json.dumps(manifest), encoding="utf-8")
    (source / "descriptor.json").write_text(json.dumps(descriptor), encoding="utf-8")
    user_source = "using Newtonsoft.Json.Linq; using VRCForge.Core.MCP; namespace VRCForge.Editor { [VRCForgeCommand(\"user.echo\", Summary=\"when-to-use: echo. when-NOT-to-use: other tools.\")] public static class UserEchoTool { public static object HandleCommand(JObject p) { if ((string)p[\"value\"] == \"fail\") return new JObject { [\"success\"] = false, [\"error\"] = \"user failure\", [\"code\"] = \"user_failed\" }; if ((string)p[\"value\"] == \"result-fail\") return VRCForgeToolResult.FailedWithCode(\"user_failed_result\", \"user result failure\"); if ((string)p[\"value\"] == \"waiting\") return VRCForgeToolResult.Waiting(\"still running\", 0.25, new JObject { [\"phase\"] = \"queued\" }); return new JObject { [\"echo\"] = p[\"value\"] }; } } }"
    (source / "UserEcho.cs").write_text(user_source, encoding="utf-8")
    store = SkillPackageService(tmp_path / "store", vrcforge_version="1.8.0"); package = store.export_dev(source, tmp_path / "user.vsk").package_path; store.import_package(package, dev_mode=True); store.set_enabled("com.example.user", True)
    project = tmp_path / "UnityProject"; (project / "Assets").mkdir(parents=True)
    UserUnityToolService(store).apply(UserUnityToolService(store).prepare_installed("com.example.user", project))
    dotnet_root = Path(os.environ["DOTNET_ROOT"]) if os.environ.get("DOTNET_ROOT") else Path.home() / "AppData" / "Local" / "Microsoft" / "dotnet"
    compiler = sorted((dotnet_root / "sdk").glob("*/Roslyn/bincore/csc.dll"))[-1]; refs = sorted((dotnet_root / "packs/Microsoft.NETCore.App.Ref").glob("*/ref/net8.0"))[-1]; newtonsoft = compiler.parents[2] / "Newtonsoft.Json.dll"
    out = tmp_path / "compile"; out.mkdir(); files = []
    for src in SOURCES + [project / "Assets/VRCForgeUserTools/com.example.user/Editor/UserEcho.cs", project / "Assets/VRCForgeUserTools/com.example.user/Editor/UserUnityToolDigestStamp.cs"]:
        dst = out / src.name; dst.write_text(src.read_text(encoding="utf-8-sig"), encoding="utf-8"); files.append(str(dst))
    probe = out / "Probe.cs"; probe.write_text(REAL_HARNESS, encoding="utf-8"); dll = out / "Probe.dll"
    cmd = [str(dotnet_root / "dotnet.exe"), str(compiler), "-nologo", "-target:exe", "-langversion:9.0", f"-out:{dll}", *[f"-r:{p}" for p in refs.glob("*.dll")], f"-r:{newtonsoft}", *files, str(probe)]
    compiled = subprocess.run(cmd, capture_output=True, text=True, timeout=60); assert compiled.returncode == 0, compiled.stdout + compiled.stderr
    shutil.copy2(newtonsoft, out / newtonsoft.name); (out / "Probe.runtimeconfig.json").write_text(json.dumps({"runtimeOptions": {"tfm": "net8.0", "framework": {"name": "Microsoft.NETCore.App", "version": "8.0.0"}}}), encoding="utf-8")
    run = subprocess.run([str(dotnet_root / "dotnet.exe"), str(dll), str(project)], capture_output=True, text=True, timeout=30); assert run.returncode == 0, run.stdout + run.stderr
