import base64
import hashlib
import json
from pathlib import Path
from unittest.mock import Mock

import pytest

from user_unity_tool_service import UserUnityToolService
from skill_packages import PackageIntegrityError, PackageSecurityError, SkillPackageService
from user_unity_tool_gateway import list_user_tools, invoke_user_tool


def _package(tmp_path: Path, *, description: str = "when-to-use: invoke. when-NOT-to-use: offline.", repairs=None, tool_id="example.tool") -> Path:
    source = tmp_path / "source"
    source.mkdir()
    manifest = {
        "id": "com.example.tool", "name": "Tool", "version": "1.0.0", "author": "Example",
        "description": "A tool", "min_vrcforge_version": "1.0.0", "permissions": [],
        "entrypoints": {"skill": "SKILL.md", "unityTools": "descriptor.json", "unity_tool_source": "Tool.cs"},
    }
    descriptor = {"schema": "vrcforge.user_unity_tools.v1", "tools": [{"toolId": tool_id, "typeName": "Example.Tool", "source": "Tool.cs", "description": description, "inputSchema": {"type": "object"}}]}
    if repairs is not None:
        descriptor["repairs"] = repairs
    (source / "manifest.json").write_text(json.dumps(manifest), encoding="utf-8")
    (source / "descriptor.json").write_text(json.dumps(descriptor), encoding="utf-8")
    (source / "Tool.cs").write_text("namespace Example { public sealed class Tool {} }", encoding="utf-8")
    (source / "SKILL.md").write_text("# Tool\n", encoding="utf-8")
    return SkillPackageService(tmp_path / "store", vrcforge_version="1.0.0").export_dev(source, tmp_path / "tool.vsk").package_path


def _installed(tmp_path: Path, *, description="when-to-use: invoke. when-NOT-to-use: offline.", repairs=None, core_tree_identity=None, tool_id="example.tool", official_tool_names=None):
    archive = _package(tmp_path, description=description, repairs=repairs, tool_id=tool_id)
    packages = SkillPackageService(tmp_path / "store", vrcforge_version="1.0.0")
    packages.install(archive)
    packages.set_enabled("com.example.tool", True)
    archive.unlink()  # Deployment must not depend on the original import archive.
    project = tmp_path / "project"
    (project / "Assets").mkdir(parents=True)
    return UserUnityToolService(
        packages,
        core_tree_identity=core_tree_identity,
        official_tool_names=official_tool_names,
    ), project


def test_official_tool_name_collision_blocks_install_and_preserves_user_package(tmp_path, monkeypatch):
    import unity_mcp_tool_contract

    official = unity_mcp_tool_contract.EXPECTED_TOOL_NAMES
    collision = "vrc_future_official_tool"
    assert collision not in official
    service, project = _installed(tmp_path, tool_id=collision)
    approved_plan = service.prepare_installed("com.example.tool", project)
    monkeypatch.setattr(unity_mcp_tool_contract, "EXPECTED_TOOL_NAMES", official | {collision})

    with pytest.raises(ValueError, match=rf"{collision}.*请重命名用户工具"):
        service.apply(approved_plan)

    with pytest.raises(ValueError, match=rf"{collision}.*请重命名用户工具"):
        service.prepare_installed("com.example.tool", project)

    metadata, _ = service.package_service.verified_installed_entrypoint_bytes(
        "com.example.tool", "manifest.json"
    )
    assert metadata["manifest"]["id"] == "com.example.tool"
    assert list((project / "Assets").iterdir()) == []


def test_new_official_tool_collision_rechecked_on_discovery_and_each_call(tmp_path, monkeypatch):
    import unity_mcp_tool_contract

    official = unity_mcp_tool_contract.EXPECTED_TOOL_NAMES
    collision = "vrc_future_official_tool"
    assert collision not in official
    service, project = _installed(tmp_path, tool_id=collision)
    plan = service.prepare_installed("com.example.tool", project)
    installed = service.apply(plan)
    assert installed["verified"] is True

    metadata, _ = service.package_service.verified_installed_entrypoint_bytes(
        "com.example.tool", "manifest.json"
    )
    monkeypatch.setattr(unity_mcp_tool_contract, "EXPECTED_TOOL_NAMES", official | {collision})
    core_catalog = Mock(return_value={"tools": [{
        "packageId": "com.example.tool", "packageDigest": metadata["package_sha256"],
        "available": True, "status": "ready", "tools": [{"toolId": collision, "available": True}],
    }]})
    listed = list_user_tools({"projectPath": str(project)}, service, core_catalog)
    assert listed["tools"][0]["available"] is False
    assert "请重命名用户工具" in listed["tools"][0]["reasons"][0]

    invoke = Mock()
    with pytest.raises(ValueError, match=rf"{collision}.*请重命名用户工具"):
        invoke_user_tool({
            "projectPath": str(project), "packageId": "com.example.tool",
            "packageDigest": metadata["package_sha256"], "toolId": collision, "arguments": {},
        }, service, invoke)
    invoke.assert_not_called()
    target = project / "Assets/VRCForgeUserTools/com.example.tool/Editor"
    assert (target / "Tool.cs").is_file()
    assert (target / "tool-package.json").is_file()


@pytest.mark.parametrize("tool_id", ["vrc_capture_scan_scope", "vrc_validate_scan_scope"])
def test_core_contract_auxiliary_tools_are_reserved_without_duplicate_name_table(tmp_path, tool_id):
    service, project = _installed(tmp_path, tool_id=tool_id)
    with pytest.raises(ValueError, match=rf"{tool_id}.*请重命名用户工具"):
        service.prepare_installed("com.example.tool", project)


def test_new_official_app_registration_rechecked_on_discovery_and_each_call(tmp_path):
    app_registry: set[str] = set()
    tool_id = "vrcforge_read_text_file"
    service, project = _installed(
        tmp_path,
        tool_id=tool_id,
        official_tool_names=lambda: tuple(app_registry),
    )
    plan = service.prepare_installed("com.example.tool", project)
    service.apply(plan)
    metadata, _ = service.package_service.verified_installed_entrypoint_bytes(
        "com.example.tool", "manifest.json"
    )

    app_registry.add(tool_id)
    core_catalog = Mock(return_value={"tools": [{
        "packageId": "com.example.tool", "packageDigest": metadata["package_sha256"],
        "available": True, "status": "ready", "tools": [{"toolId": tool_id, "available": True}],
    }]})
    listed = list_user_tools({"projectPath": str(project)}, service, core_catalog)
    assert listed["tools"][0]["available"] is False
    assert tool_id in listed["tools"][0]["reasons"][0]
    assert "请重命名用户工具" in listed["tools"][0]["reasons"][0]

    invoke = Mock()
    with pytest.raises(ValueError, match=rf"{tool_id}.*请重命名用户工具"):
        invoke_user_tool({
            "projectPath": str(project), "packageId": "com.example.tool",
            "packageDigest": metadata["package_sha256"], "toolId": tool_id, "arguments": {},
        }, service, invoke)
    invoke.assert_not_called()


def test_unavailable_official_app_registry_fails_closed(tmp_path):
    def unavailable():
        raise RuntimeError("registry unavailable")

    service, project = _installed(
        tmp_path,
        official_tool_names=unavailable,
    )
    with pytest.raises(ValueError, match="官方工具名当前无法验证"):
        service.prepare_installed("com.example.tool", project)


def test_prepare_and_apply_is_pending_compile(tmp_path):
    service, project = _installed(tmp_path)
    plan = service.prepare_installed("com.example.tool", project)
    result = service.apply(plan)
    assert result["status"] == "pending_compile"
    assert result["pendingCompile"] is True
    assert result["verified"] is True
    assert result["readback"]["scope"] == "user_tool_files"
    assert result["readback"]["files"] == [
        {"path": item["target"]["targetRelativePath"], "sha256": item["sha256"]}
        for item in plan["files"]
    ]
    target = project / "Assets/VRCForgeUserTools/com.example.tool/Editor"
    record = json.loads((target / "tool-package.json").read_text(encoding="utf-8"))
    stamp = (target / "UserUnityToolDigestStamp.cs").read_text(encoding="utf-8")
    assert record["stampType"].split(".")[-1].split("+", 1)[0] in stamp
    assert "class DigestStamp" in stamp
    assert record["tools"][0]["source"] == "Assets/VRCForgeUserTools/com.example.tool/Editor/Tool.cs"
    projection_sha = hashlib.sha256((target / "Tool.cs").read_bytes()).hexdigest()
    assert record["tools"][0]["sourceSha256"] == projection_sha
    assert record["tools"][0]["sourceProvenance"] == {
        "authority": "verified_installed_user_tool_package",
        "sourceRelativePath": "Tool.cs",
        "sourceSha256": projection_sha,
        "projectionSha256": projection_sha,
    }
    assert result["sourceProvenance"] == {
        **record["tools"][0]["sourceProvenance"],
        "packageId": record["packageId"],
        "packageVersion": record["packageVersion"],
        "packageDigest": record["packageDigest"],
        "recordAssetPath": f"{plan['targetFolder']}/tool-package.json",
        "projectionPath": record["tools"][0]["source"],
        "expectedProjectionSha256": projection_sha,
        "projectionReadbackSha256": projection_sha,
        "projectionReadbackVerified": True,
    }
    assert 'public const string PackageVersion = "1.0.0";' in stamp
    assert 'public const string SourceRelativePath = "Tool.cs";' in stamp
    assert f'public const string SourceSha256 = "{projection_sha}";' in stamp
    assert f'public const string ProjectionSha256 = "{projection_sha}";' in stamp
    assert str(tmp_path) not in stamp
    assert "repairs" not in record


def test_repair_baseline_is_bound_at_prepare_and_apply(tmp_path):
    baseline = "a" * 64
    repairs = [{"toolId": "example.tool", "coreVersion": "1.0.0", "toolContractVersion": "1", "coreSourceTreeSha256": baseline}]
    current = {"sha256": baseline}
    service, project = _installed(tmp_path, repairs=repairs, core_tree_identity=lambda _root: current)
    plan = service.prepare_installed("com.example.tool", project)
    assert plan["files"]
    current["sha256"] = "b" * 64
    with pytest.raises(ValueError, match="core source tree baseline mismatch"):
        service.apply(plan)
    assert list((project / "Assets").iterdir()) == []


def test_catalog_uses_the_descriptor_snapshot_it_validated(tmp_path, monkeypatch):
    first = tmp_path / "first"
    second = tmp_path / "second"
    first.mkdir(); second.mkdir()
    original, project = _installed(first)
    changed, _ = _installed(second, description="when-to-use: a different installed package. when-NOT-to-use: the original.")
    changed_metadata, _ = changed.package_service.verified_installed_entrypoint_bytes("com.example.tool", "manifest.json")
    read_original = original.package_service.verified_installed_entrypoint_bytes
    manifest_reads = 0

    def read_snapshot(package_id, relative):
        nonlocal manifest_reads
        if relative == "manifest.json":
            manifest_reads += 1
        store_read = read_original if manifest_reads == 1 else changed.package_service.verified_installed_entrypoint_bytes
        return store_read(package_id, relative)

    monkeypatch.setattr(original.package_service, "verified_installed_entrypoint_bytes", read_snapshot)
    core = Mock(return_value={"tools": [{"packageId": "com.example.tool", "packageDigest": changed_metadata["package_sha256"], "available": True, "status": "ready", "tools": [{"toolId": "example.tool", "available": True}]}]})
    result = list_user_tools({"projectPath": str(project)}, original, core)
    assert result["tools"][0]["available"] is False
    assert "differs from the installed package" in result["tools"][0]["reasons"][0]
    assert manifest_reads == 1


def test_same_version_official_source_change_disables_repair_without_removing_it(tmp_path):
    from dashboard_server import _unity_core_tree_identity

    core_root = tmp_path / "project/Assets/VRCForge"
    core_root.mkdir(parents=True)
    official_source = core_root / "Official.cs"
    official_source.write_text("// official implementation A", encoding="utf-8")
    baseline = _unity_core_tree_identity(core_root)["sha256"]
    repairs = [{"toolId": "vrc_get_property", "coreVersion": "1.8.0", "toolContractVersion": "160", "coreSourceTreeSha256": baseline}]
    source_root = tmp_path / "package"
    source_root.mkdir()
    service, _ = _installed(source_root, repairs=repairs, core_tree_identity=_unity_core_tree_identity)
    project = core_root.parent.parent
    plan = service.prepare_installed("com.example.tool", project)
    service.apply(plan)
    assert _unity_core_tree_identity(core_root)["sha256"] == baseline
    files = {path: path.read_bytes() for path in (project / "Assets/VRCForgeUserTools").rglob("*") if path.is_file()}
    core = Mock(return_value={"tools": [{"packageId": "com.example.tool", "packageDigest": plan["installed"]["sha256"], "available": True, "status": "ready", "tools": [{"toolId": "example.tool", "available": True}]}]})
    arguments = {"projectPath": str(project), "packageId": "com.example.tool", "packageDigest": plan["installed"]["sha256"], "toolId": "example.tool", "arguments": {}}
    assert list_user_tools(arguments, service, core)["tools"][0]["available"] is True
    official_source.write_text("// official implementation B; same versions", encoding="utf-8")
    assert list_user_tools(arguments, service, core)["tools"][0]["available"] is False
    invoke = Mock()
    with pytest.raises(ValueError, match="core source tree baseline mismatch"):
        invoke_user_tool(arguments, service, invoke)
    invoke.assert_not_called()
    assert all(path.read_bytes() == payload for path, payload in files.items())
    metadata, _ = service.package_service.verified_installed_entrypoint_bytes("com.example.tool", "manifest.json")
    assert metadata["package_sha256"] == plan["installed"]["sha256"]


@pytest.mark.parametrize("phase", ["prepare", "apply"])
def test_disabled_installed_package_cannot_deploy(tmp_path, phase):
    service, project = _installed(tmp_path)
    plan = service.prepare_installed("com.example.tool", project) if phase == "apply" else None
    service.package_service.set_enabled("com.example.tool", False)
    with pytest.raises(PackageSecurityError, match="not enabled"):
        service.apply(plan) if plan else service.prepare_installed("com.example.tool", project)
    assert list((project / "Assets").iterdir()) == []


@pytest.mark.parametrize("relative", ["Tool.cs", "descriptor.json"])
def test_installed_bytes_tampered_after_approval_cannot_deploy(tmp_path, relative):
    service, project = _installed(tmp_path)
    plan = service.prepare_installed("com.example.tool", project)
    installed = service.package_service.skill_store / "com.example.tool/versions/1.0.0"
    (installed / relative).write_text("changed", encoding="utf-8")
    with pytest.raises(PackageIntegrityError):
        service.apply(plan)
    assert list((project / "Assets").iterdir()) == []


@pytest.mark.parametrize("name", ["Tool.cs", "tool-package.json", "UserUnityToolDigestStamp.cs"])
def test_prepared_payload_cannot_replace_verified_payload(tmp_path, name):
    service, project = _installed(tmp_path)
    plan = service.prepare_installed("com.example.tool", project)
    item = next(item for item in plan["files"] if item["name"] == name)
    payload = base64.b64decode(item["payload"])
    if name == "tool-package.json":
        record = json.loads(payload)
        record["packageId"] = "com.example.other"
        payload = json.dumps(record).encode()
    else:
        payload += b"\n// changed after approval\n"
    item.update(payload=base64.b64encode(payload).decode(), sha256=hashlib.sha256(payload).hexdigest())
    with pytest.raises(ValueError, match="payload drifted"):
        service.apply(plan)
    assert list((project / "Assets").iterdir()) == []


def test_prepared_file_set_cannot_omit_generated_record(tmp_path):
    service, project = _installed(tmp_path)
    plan = service.prepare_installed("com.example.tool", project)
    plan["files"].pop(1)
    with pytest.raises(ValueError, match="file set drifted"):
        service.apply(plan)
    assert list((project / "Assets").iterdir()) == []


def test_description_policy_is_enforced(tmp_path):
    service, project = _installed(tmp_path, description="invoke this tool")
    with pytest.raises(ValueError, match="when-to-use"):
        service.prepare_installed("com.example.tool", project)


def test_existing_target_is_preserved_and_partial_write_is_cleaned(tmp_path):
    service, project = _installed(tmp_path)
    target = project / "Assets/VRCForgeUserTools/com.example.tool/Editor"
    target.mkdir(parents=True)
    plan = service.prepare_installed("com.example.tool", project)
    (target / "tool-package.json").write_text("foreign", encoding="utf-8")
    with pytest.raises(ValueError, match="already exists|appeared"):
        service.apply(plan)
    assert (target / "tool-package.json").read_text(encoding="utf-8") == "foreign"
    assert not (target / "Tool.cs").exists()
    assert not (target / "UserUnityToolDigestStamp.cs").exists()
