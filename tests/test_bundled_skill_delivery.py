from __future__ import annotations

import json
import os
import re
import shutil
import subprocess
from pathlib import Path

import pytest

import bundled_skill_delivery as delivery
from agent_gateway import AgentGatewayConfig
from external_installed_skill_registry import ExternalInstalledSkillRegistryService
from skill_packages import SkillPackageError, SkillPackageService

ROOT = Path(__file__).resolve().parents[1]


@pytest.fixture
def app_profile(tmp_path):
    import dashboard_server as app

    gateway = app.AGENT_GATEWAY
    previous = gateway.config_path, gateway.audit_dir
    gateway.configure_paths(tmp_path / "config" / "agent_gateway.json", tmp_path / "audit")
    gateway.save_config(AgentGatewayConfig(enabled=True, allow_write_requests=True))
    try:
        yield app, gateway, tmp_path
    finally:
        gateway.configure_paths(*previous)


def _deliver(app, gateway, version="1.8.1"):
    with app.SKILL_PACKAGE_WRITE_LOCK, gateway.skills.write_lock:
        return delivery.deliver_bundled_guide(
            app.SKILL_PACKAGE_PROJECTION, gateway.skills.user_skills_dir, version=version,
        )


def test_fresh_normal_profile_loads_complete_guide_internally_and_external_prompt(app_profile):
    app, gateway, root = app_profile
    assert _deliver(app, gateway)["status"] == "installed"
    skill = next(item for item in gateway.skills.build_skill_registry()["skills"] if item["name"] == delivery.SKILL_NAME)
    assert skill["enabled"] and skill["available"]
    assert skill["packageId"] == delivery.PACKAGE_ID
    assert skill["validation"]["status"] != "error"
    assert {
        "vrcforge_list_user_unity_tools",
        "vrcforge_install_user_unity_tools",
        "vrcforge_invoke_user_unity_tool",
        "vrcforge_refresh_asset_database",
        "vrcforge_exit_skill",
    }.issubset(set(skill["allowedTools"]))
    result = gateway.runtime_skills.execute(delivery.SKILL_NAME, {}, "test")
    assert result["ok"] and result["status"] == "loaded", result
    assert result["execution"] == "agentic"
    context = gateway._runtime_skill_package_audit_context_locked(skill)
    assert context["distribution"] == "bundled"
    assert context["signatureStatus"] == "not_signed"
    assert context["signerFingerprint"] is None
    assert not (root / "skill-packages" / "registry.json").exists()

    assert delivery.SKILL_NAME in {item["name"] for item in gateway.list_mcp_prompts()["prompts"]}
    prompt = gateway.get_mcp_prompt(delivery.SKILL_NAME)["structuredContent"]
    assert prompt["skill"]["instructions"] == skill["instructions"]
    assert prompt["context"]["status"] == "ready_for_planning"
    assert prompt["context"]["missingRequiredResources"] == []
    assert prompt["context"]["gmCases"] == []
    assert prompt["context"]["gameOnlyAcceptance"] == []
    support = {item["path"]: item["content"] for item in prompt["skill"]["supportFiles"]}
    assert set(support) == {
        "workflows/first-run.json",
        "references/repair-guide.md",
        "references/user-tool-author-guide.md",
    }
    for relative, content in support.items():
        assert content == (delivery.bundled_source_dir() / relative).read_bytes().decode("utf-8")
    installed = ExternalInstalledSkillRegistryService(gateway.skills)
    assert delivery.SKILL_NAME in {item["name"] for item in installed.list_installed_skills()["skills"]}
    read = installed.read_installed_skill({"name": delivery.SKILL_NAME, "file": "references/repair-guide.md"})
    assert read["content"] == support["references/repair-guide.md"]
    external = gateway.call_external_mcp_tool("vrcforge_read_installed_skill", {
        "name": delivery.SKILL_NAME, "file": "references/repair-guide.md",
    })
    assert external["ok"]
    assert external["result"]["content"] == support["references/repair-guide.md"]


def test_startup_preserves_user_edits_and_disabled_state(app_profile):
    app, gateway, root = app_profile
    _deliver(app, gateway)
    target = gateway.skills.user_skills_dir / delivery.SKILL_NAME
    manifest = json.loads((delivery.bundled_source_dir() / "manifest.json").read_text(encoding="utf-8"))
    app.SKILL_PACKAGE_PROJECTION.set_enabled_batch([manifest], False)
    guide = target / "references" / "repair-guide.md"
    guide.write_text("User's repair notes", encoding="utf-8")
    before = {p.relative_to(target).as_posix(): p.read_bytes() for p in target.rglob("*") if p.is_file()}
    assert _deliver(app, gateway)["status"] == "preserved"
    assert before == {p.relative_to(target).as_posix(): p.read_bytes() for p in target.rglob("*") if p.is_file()}
    skill = gateway.skills.find_user_skill(delivery.SKILL_NAME)
    assert skill["enabled"] is False
    assert delivery.bundled_guide_audit_context(skill, gateway.skills.user_skills_dir, root / "skill-packages") == {}


def test_content_tamper_is_not_authorized_by_projection_marker(app_profile):
    app, gateway, _ = app_profile
    _deliver(app, gateway)
    target = gateway.skills.user_skills_dir / delivery.SKILL_NAME / "SKILL.md"
    target.write_text(target.read_text(encoding="utf-8") + "\nUnapproved replacement\n", encoding="utf-8")
    result = gateway.runtime_skills.execute(delivery.SKILL_NAME, {}, "test")
    assert not result["ok"]
    assert "identity could not be verified" in result["error"]


def test_existing_package_governance_remains_authoritative(app_profile, tmp_path):
    app, gateway, root = app_profile
    _deliver(app, gateway)
    service = SkillPackageService(root / "skill-packages", vrcforge_version="1.8.1")
    archive = service.export_dev(delivery.bundled_source_dir(), tmp_path / "guide.vsk").package_path
    service.install(archive, dev_mode=True)
    skill = gateway.skills.find_user_skill(delivery.SKILL_NAME)
    assert delivery.bundled_guide_audit_context(skill, gateway.skills.user_skills_dir, root / "skill-packages") == {}


def test_reviewed_guide_version_import_updates_prior_immutable_version(app_profile, tmp_path):
    app, gateway, _root = app_profile
    old_source = tmp_path / "old-guide"
    shutil.copytree(delivery.bundled_source_dir(), old_source)
    old_manifest_path = old_source / "manifest.json"
    old_manifest = json.loads(old_manifest_path.read_text(encoding="utf-8"))
    old_manifest["version"] = "1.0.2"
    old_manifest_path.write_text(json.dumps(old_manifest, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    service = SkillPackageService(gateway.user_constraints_path.parent / "skill-packages", vrcforge_version="1.8.5")
    old_archive = service.export_dev(old_source, tmp_path / "guide-1.0.2.vsk").package_path
    old_result = app.SKILL_PACKAGE_CONTROLLER.import_package({"packagePath": str(old_archive), "devMode": True, "projectToUserSkills": True})
    assert old_result["ok"] and old_result["projectedSkill"]["name"] == delivery.SKILL_NAME

    current_archive = service.export_dev(delivery.bundled_source_dir(), tmp_path / "guide-1.0.3.vsk").package_path
    current_result = app.SKILL_PACKAGE_CONTROLLER.import_package({"packagePath": str(current_archive), "devMode": True, "projectToUserSkills": True})
    assert current_result["ok"] and current_result["projectedSkill"]["name"] == delivery.SKILL_NAME
    installed = service.load_registry()["skills"][delivery.PACKAGE_ID]
    assert installed["version"] == "1.0.3"
    projected = gateway.skills.user_skills_dir / delivery.SKILL_NAME / "references" / "user-tool-author-guide.md"
    assert projected.read_bytes() == (delivery.bundled_source_dir() / "references" / "user-tool-author-guide.md").read_bytes()


@pytest.mark.parametrize("failure", ["minimum_version", "missing_support"])
def test_incompatible_or_incomplete_bundle_never_leaves_partial_skill(app_profile, monkeypatch, failure):
    app, gateway, root = app_profile
    source = root / "application-bundle"
    shutil.copytree(delivery.bundled_source_dir(), source)
    if failure == "missing_support":
        (source / "references" / "repair-guide.md").unlink()
    monkeypatch.setattr(delivery, "bundled_source_dir", lambda: source)
    with pytest.raises(SkillPackageError):
        _deliver(app, gateway, "1.8.0" if failure == "minimum_version" else "1.8.1")
    assert not (gateway.skills.user_skills_dir / delivery.SKILL_NAME).exists()


def test_normal_release_collects_full_source_and_startup_delivers_it():
    root = Path(__file__).resolve().parents[1]
    build = (root / "packaging" / "build_backend.ps1").read_text(encoding="utf-8")
    assert '--add-data "$bundledGuide;examples/skill-packages/vrcforge-first-run-guide"' in build
    assert "$bundledManifest.entrypoints.PSObject.Properties.Value" in build
    assert "Get-FileHash -LiteralPath $collectedPath" in build
    release = (root / "packaging" / "build_release.ps1").read_text(encoding="utf-8")
    assert "packaging\\build_backend.ps1" in release
    import inspect
    import dashboard_server
    assert "deliver_bundled_guide(" in inspect.getsource(dashboard_server.on_startup)


def test_backend_builder_reads_utf8_bomless_manifest_with_powershell():
    """The Windows build runner must decode the shipped Chinese manifest as UTF-8."""
    if os.name != "nt":
        pytest.skip("PowerShell encoding regression is Windows-specific")
    root = Path(__file__).resolve().parents[1]
    build = (root / "packaging" / "build_backend.ps1").read_text(encoding="utf-8")
    manifest_line = next(
        line.strip()
        for line in build.splitlines()
        if "$bundledManifest = Get-Content" in line
    )
    guide = (root / "examples" / "skill-packages" / "vrcforge-first-run-guide").resolve()
    expected_name = json.loads((guide / "manifest.json").read_text(encoding="utf-8"))["name"]
    command = (
        f'$ErrorActionPreference = "Stop"; $bundledGuide = \'{guide}\'; '
        f"{manifest_line}; "
        '$bytes = [Text.Encoding]::UTF8.GetBytes([string]$bundledManifest.name); '
        '[Convert]::ToBase64String($bytes)'
    )
    completed = subprocess.run(
        ["powershell.exe", "-NoProfile", "-NonInteractive", "-Command", command],
        cwd=root,
        text=True,
        encoding="utf-8",
        errors="replace",
        capture_output=True,
        timeout=20,
        check=False,
    )
    assert completed.returncode == 0, completed.stdout + completed.stderr
    assert __import__("base64").b64decode(completed.stdout.strip()).decode("utf-8") == expected_name


@pytest.fixture(scope="module")
def author_example_runtime(tmp_path_factory):
    """Compile the documented source and generator against the actual registry."""
    tmp_path = tmp_path_factory.mktemp("author-example-runtime")
    guide = (delivery.bundled_source_dir() / "references" / "user-tool-author-guide.md").read_text(encoding="utf-8")
    match = re.search(r"```csharp\n(?P<source>.*?)\n```", guide, re.DOTALL)
    assert match, "author guide is missing its C# example"
    dotnet_root = Path(os.environ["DOTNET_ROOT"]) if os.environ.get("DOTNET_ROOT") else Path.home() / "AppData" / "Local" / "Microsoft" / "dotnet"
    compilers = sorted((dotnet_root / "sdk").glob("*/Roslyn/bincore/csc.dll"))
    refs = sorted((dotnet_root / "packs/Microsoft.NETCore.App.Ref").glob("*/ref/net8.0"))
    if not compilers or not refs:
        pytest.skip("Local .NET SDK required; no Unity is launched")
    compiler = compilers[-1]
    newtonsoft = compiler.parents[2] / "Newtonsoft.Json.dll"
    stubs = tmp_path / "UnityStubs.cs"
    stubs.write_text(
        """
namespace UnityEngine { public class Object {} public class TextAsset : Object { public string text { get; set; } } }
namespace UnityEditor {
 public enum ImportAssetOptions { ForceSynchronousImport = 1, ForceUpdate = 2 }
 public static class AssetDatabase {
  public static UnityEngine.Object LoadMainAssetAtPath(string path) => null;
  public static T LoadAssetAtPath<T>(string path) where T : UnityEngine.Object => null;
  public static void ImportAsset(string path, ImportAssetOptions options) { }
  public static bool DeleteAsset(string path) => true;
 }
}
""",
        encoding="utf-8",
    )
    source = tmp_path / "AssetNoteCreateTool.cs"
    source.write_text(match.group("source"), encoding="utf-8")
    output = tmp_path / "AuthorExample.dll"
    attrs = [
        ROOT / "Assets/VRCForge/Core/MCP/VRCForgeCommandAttribute.cs",
        ROOT / "Assets/VRCForge/Core/MCP/VRCForgeInputAttribute.cs",
        ROOT / "Assets/VRCForge/Core/MCP/VRCForgeParameterSchema.cs",
        ROOT / "Assets/VRCForge/Core/MCP/VRCForgeToolRegistry.cs",
    ]
    snippets = re.findall(r"```csharp\n(.*?)\n```", guide, re.DOTALL)
    generator = snippets[1] if len(snippets) > 1 else "JObject packageDescriptor = null;"
    probe = tmp_path / "Probe.cs"
    probe.write_text(
        "using System; using Newtonsoft.Json.Linq; using VRCForge.Core.MCP;\n"
        "internal static class Program { public static void Main(string[] args) {\n"
        + generator
        + '\nvar runtime = VRCForgeToolRegistry.Describe(typeof(Example.UserTools.AssetNoteCreateTool));\n'
        + 'var expected = JObject.Parse(System.IO.File.ReadAllText(args[0]));\n'
        + 'if (!JToken.DeepEquals(expected["tools"][0]["inputSchema"], runtime.CreateInputSchema())) '
        + 'throw new Exception("Documented inputSchema differs from runtime: " + runtime.CreateInputSchema());\n'
        + 'Console.WriteLine(new JObject { ["runtimeSchema"] = runtime.CreateInputSchema(), '
        + '["runtimeDescription"] = runtime.Description, ["generated"] = packageDescriptor }.ToString());\n}} ',
        encoding="utf-8",
    )
    descriptor = json.loads(re.search(r"```json\n(.*?)\n```", guide, re.DOTALL).group(1))
    expected = tmp_path / "descriptor.json"
    expected.write_text(json.dumps(descriptor), encoding="utf-8")
    command = [
        str(dotnet_root / "dotnet.exe"), str(compiler), "-nologo", "-target:exe",
        "-langversion:9.0", f"-out:{output}",
        *[f"-r:{path}" for path in refs[-1].glob("*.dll")], f"-r:{newtonsoft}",
        *(str(path) for path in attrs), str(stubs), str(source), str(probe),
    ]
    result = subprocess.run(command, capture_output=True, text=True, timeout=60)
    assert result.returncode == 0, result.stdout + result.stderr
    shutil.copy2(newtonsoft, tmp_path / newtonsoft.name)
    output.with_suffix(".runtimeconfig.json").write_text(json.dumps({"runtimeOptions": {
        "tfm": "net8.0", "framework": {"name": "Microsoft.NETCore.App", "version": "8.0.0"},
    }}), encoding="utf-8")
    result = subprocess.run([str(dotnet_root / "dotnet.exe"), str(output), str(expected)],
                            capture_output=True, text=True, timeout=30)
    assert result.returncode == 0, result.stdout + result.stderr
    return descriptor, json.loads(result.stdout)


def test_user_tool_author_example_compiles_with_real_core_attributes(author_example_runtime):
    descriptor, actual = author_example_runtime
    # Compare all generated fields, including descriptions/defaults and required order.
    assert descriptor["tools"][0]["inputSchema"] == actual["runtimeSchema"]
    assert descriptor["tools"][0]["description"] == actual["runtimeDescription"]


def test_user_tool_author_generator_emits_runtime_descriptor(author_example_runtime):
    descriptor, actual = author_example_runtime
    assert actual["generated"] == descriptor, "Execute the documented descriptor generator"
