from __future__ import annotations

import json
import os
import re
import shutil
import subprocess
from dataclasses import replace
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
    assert _deliver(app, gateway)["status"] == "application-owned"
    assert not (gateway.skills.user_skills_dir / delivery.SKILL_NAME).exists()
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

    support = {item["path"]: item["content"] for item in result["result"]["supportFiles"]}
    assert set(support) == {
        "references/zero-start.md",
        "workflows/first-run.json",
        "references/repair-guide.md",
        "references/user-tool-author-guide.md",
    }
    for relative, content in support.items():
        assert content == (delivery.bundled_source_dir() / relative).read_bytes().decode("utf-8")
    assert all(item["source"] == "builtin" for item in gateway.skills.build_skill_registry()["skills"] if item["name"] in {delivery.SKILL_NAME, delivery.DOCS_SKILL_NAME})
    assert not (root / "skill-packages" / "registry.json").exists()


def test_startup_preserves_user_edits_and_disabled_state(app_profile):
    app, gateway, root = app_profile
    target = gateway.skills.user_skills_dir / delivery.SKILL_NAME
    target.mkdir(parents=True)
    (target / "SKILL.md").write_text("---\nname: vrcforge-first-run-guide\ntitle: My notes\n---\nUser's repair notes\n", encoding="utf-8")
    before = (target / "SKILL.md").read_bytes()
    assert _deliver(app, gateway)["status"] == "application-owned"
    registry = gateway.skills.build_skill_registry()
    official = next(item for item in registry["skills"] if item["name"] == delivery.SKILL_NAME)
    assert official["source"] == "builtin" and official["sourceConflict"]["status"] == "rename_required"
    assert registry["sourceConflictCount"] == 1
    assert (target / "SKILL.md").read_bytes() == before


def test_content_tamper_is_not_authorized_by_projection_marker(app_profile):
    _app, gateway, _ = app_profile
    target = gateway.skills.user_skills_dir / delivery.SKILL_NAME
    target.mkdir(parents=True)
    target.joinpath("SKILL.md").write_text("---\nname: vrcforge-first-run-guide\ntitle: User copy\n---\nUnapproved replacement\n", encoding="utf-8")
    result = gateway.runtime_skills.execute(delivery.SKILL_NAME, {}, "test")
    assert result["ok"] and result["status"] == "loaded"
    assert result["result"]["source"] == "builtin"
    assert "Unapproved replacement" not in result["result"]["instructions"]
    assert target.joinpath("SKILL.md").read_text(encoding="utf-8").endswith("Unapproved replacement\n")


def test_app_upgrade_uses_new_source_and_preserves_owned_old_projection(app_profile, monkeypatch):
    app, gateway, root = app_profile
    old_source = delivery.bundled_source_dir()
    manifest = delivery._manifest(old_source, "1.9.1")
    projection_ports = app.SKILL_PACKAGE_PROJECTION._ports
    app.SKILL_PACKAGE_PROJECTION._ports = replace(projection_ports, validate_projection_name=lambda _name: None)
    app.SKILL_PACKAGE_PROJECTION.project_installed(old_source, manifest, enabled=True)
    app.SKILL_PACKAGE_PROJECTION._ports = projection_ports
    old_projection = gateway.skills.user_skills_dir / delivery.SKILL_NAME
    old_bytes = (old_projection / "SKILL.md").read_bytes()
    sentinel = gateway.skills.user_skills_dir / "keep-user-data.txt"
    sentinel.write_text("user-owned sentinel", encoding="utf-8")

    upgraded = root / "application-upgrade" / delivery.SKILL_NAME
    shutil.copytree(old_source, upgraded)
    skill_file = upgraded / "SKILL.md"
    skill_file.write_text(skill_file.read_text(encoding="utf-8") + "\nUpgrade-only instruction.\n", encoding="utf-8")
    monkeypatch.setattr(delivery, "bundled_source_dir", lambda: upgraded)

    registry = gateway.skills.build_skill_registry()
    official = next(item for item in registry["skills"] if item["name"] == delivery.SKILL_NAME)
    assert official["source"] == "builtin"
    assert not registry["sourceConflicts"]
    assert (old_projection / "SKILL.md").read_bytes() == old_bytes
    assert sentinel.read_text(encoding="utf-8") == "user-owned sentinel"
    replay = gateway.runtime_skills.execute(delivery.SKILL_NAME, {}, "upgrade-readback")
    assert replay["ok"] and "Upgrade-only instruction." in replay["result"]["instructions"]
    assert replay["result"]["supportFiles"]
    assert all(
        item["content"] == (upgraded / item["path"]).read_text(encoding="utf-8")
        for item in replay["result"]["supportFiles"]
    )


def test_signed_trusted_installed_projection_is_verified_before_exemption(app_profile, tmp_path):
    app, gateway, root = app_profile
    source = delivery.bundled_source_dir()
    version = app.read_vrcforge_version()
    service = SkillPackageService(root / "skill-packages", vrcforge_version=version)
    key_pair = service.generate_signing_keypair()
    package = service.export_release(source, tmp_path / "official.vsk", key_pair.private_key_pem).package_path
    service.trust_signer(key_pair.fingerprint, reason="focused regression fixture")
    service.install(package, source="focused-test")
    service.set_enabled(delivery.PACKAGE_ID, True)
    manifest = delivery._manifest(source, version)
    ports = app.SKILL_PACKAGE_PROJECTION._ports
    app.SKILL_PACKAGE_PROJECTION._ports = replace(ports, validate_projection_name=lambda _name: None)
    app.SKILL_PACKAGE_PROJECTION.project_installed(source, manifest, enabled=True)
    app.SKILL_PACKAGE_PROJECTION._ports = ports

    projected = gateway.skills.user_skills_dir / delivery.SKILL_NAME / "SKILL.md"
    skill = {
        **next(item for item in delivery.application_skills(version) if item["packageId"] == delivery.PACKAGE_ID),
        "storagePath": str(projected),
    }
    verified, _ = service.verified_installed_entrypoint_bytes(delivery.PACKAGE_ID, "manifest.json")
    assert verified["signature_status"] == "signed"
    assert verified["governance"]["signer_trust_status"] == "trusted"
    # A community-trusted signer is not an application publisher.
    assert not delivery.is_exact_legacy_projection(skill, root / "skill-packages")
    service.designate_official_signer(key_pair.fingerprint, reason="focused regression fixture")
    assert delivery.is_exact_legacy_projection(skill, root / "skill-packages")

    projected.write_bytes(projected.read_bytes() + b"\nuser edit\n")
    assert not delivery.is_exact_legacy_projection(skill, root / "skill-packages")


def test_forged_registry_row_cannot_authorize_legacy_projection(app_profile):
    app, gateway, root = app_profile
    source = delivery.bundled_source_dir()
    manifest = delivery._manifest(source, app.read_vrcforge_version())
    ports = app.SKILL_PACKAGE_PROJECTION._ports
    app.SKILL_PACKAGE_PROJECTION._ports = replace(ports, validate_projection_name=lambda _name: None)
    app.SKILL_PACKAGE_PROJECTION.project_installed(source, manifest, enabled=True)
    app.SKILL_PACKAGE_PROJECTION._ports = ports
    service = SkillPackageService(root / "skill-packages", vrcforge_version=app.read_vrcforge_version())
    registry = service.load_registry()
    registry["skills"][delivery.PACKAGE_ID] = {
        "id": delivery.PACKAGE_ID, "version": manifest["version"], "enabled": True,
        "signature_status": "signed", "signer_trust_status": "trusted",
    }
    service.registry_path.parent.mkdir(parents=True, exist_ok=True)
    service.registry_path.write_text(json.dumps(registry), encoding="utf-8")
    projected = gateway.skills.user_skills_dir / delivery.SKILL_NAME / "SKILL.md"
    skill = {
        **next(item for item in delivery.application_skills(app.read_vrcforge_version()) if item["packageId"] == delivery.PACKAGE_ID),
        "storagePath": str(projected),
    }
    assert not delivery.is_exact_legacy_projection(skill, root / "skill-packages")


def test_modified_old_official_projection_is_preserved_and_reports_rename(app_profile):
    app, gateway, _root = app_profile
    source = delivery.bundled_source_dir()
    manifest = delivery._manifest(source, "1.9.1")
    projection_ports = app.SKILL_PACKAGE_PROJECTION._ports
    app.SKILL_PACKAGE_PROJECTION._ports = replace(projection_ports, validate_projection_name=lambda _name: None)
    app.SKILL_PACKAGE_PROJECTION.project_installed(source, manifest, enabled=True)
    app.SKILL_PACKAGE_PROJECTION._ports = projection_ports
    projected = gateway.skills.user_skills_dir / delivery.SKILL_NAME / "SKILL.md"
    projected.write_text(projected.read_text(encoding="utf-8") + "\nUser change\n", encoding="utf-8")
    modified = projected.read_bytes()

    registry = gateway.skills.build_skill_registry()
    official = next(item for item in registry["skills"] if item["name"] == delivery.SKILL_NAME)
    assert official["source"] == "builtin"
    assert official["sourceConflict"]["status"] == "rename_required"
    assert registry["sourceConflictCount"] == 1
    assert projected.read_bytes() == modified


def test_existing_package_governance_remains_authoritative(app_profile, tmp_path):
    app, gateway, root = app_profile
    _deliver(app, gateway)
    service = SkillPackageService(root / "skill-packages", vrcforge_version="1.8.1")
    archive = service.export_dev(delivery.bundled_source_dir(), tmp_path / "guide.vsk").package_path
    service.install(archive, dev_mode=True)
    registry = gateway.skills.build_skill_registry()
    skill = next(item for item in registry["skills"] if item["name"] == delivery.SKILL_NAME)
    assert skill["source"] == "builtin"
    assert delivery.bundled_guide_audit_context(skill, gateway.skills.user_skills_dir, root / "skill-packages")["distribution"] == "bundled"
    assert registry["sourceConflictCount"] == 0


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
    old_result = app.SKILL_PACKAGE_CONTROLLER.import_package({"packagePath": str(old_archive), "devMode": True, "projectToUserSkills": False})
    assert old_result["ok"]

    current_archive = service.export_dev(delivery.bundled_source_dir(), tmp_path / "guide-1.0.5.vsk").package_path
    current_result = app.SKILL_PACKAGE_CONTROLLER.import_package({"packagePath": str(current_archive), "devMode": True, "projectToUserSkills": False})
    assert current_result["ok"]
    installed = service.load_registry()["skills"][delivery.PACKAGE_ID]
    assert installed["version"] == "1.0.5"
    assert not (gateway.skills.user_skills_dir / delivery.SKILL_NAME).exists()


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
