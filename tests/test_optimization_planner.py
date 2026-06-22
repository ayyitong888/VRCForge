from __future__ import annotations

from pathlib import Path

import dashboard_server
from optimization_service import (
    OPTIMIZATION_APPLY_REQUEST_GATEWAY_NAMES,
    OPTIMIZATION_TOOL_DEFINITIONS,
    STABLE_OPTIMIZATION_APPLY_REQUEST_GATEWAY_NAMES,
    build_dependency_doctor,
    build_optimization_report,
    build_optimization_tool_result,
)


def make_unity_project(root: Path) -> None:
    (root / "Assets").mkdir(parents=True)
    (root / "Packages").mkdir()
    (root / "ProjectSettings").mkdir()
    (root / "Packages" / "manifest.json").write_text('{"dependencies":{}}', encoding="utf-8")
    (root / "ProjectSettings" / "ProjectVersion.txt").write_text("m_EditorVersion: 2022.3.22f1", encoding="utf-8")


def fake_validation() -> dict:
    return {
        "ok": True,
        "schema": "vrcforge.validation.v1",
        "summary": {"severityCounts": {"Error": 0, "Warning": 0, "Suggestion": 1, "Info": 1, "Ignored": 0}},
        "gate": {"status": "pass"},
        "sources": {
            "performance_pc": {"ok": True, "payload": {"rank": "Good", "triangleCount": 42000}},
            "performance_quest": {"ok": True, "payload": {"rank": "Medium", "triangleCount": 42000}},
            "materials": {
                "ok": True,
                "payload": {
                    "renderers": [
                        {
                            "rendererPath": "Avatar/Body",
                            "materials": ["Skin", "Hair Transparent"],
                            "textures": [{"textureName": "Assets/Avatar/body_albedo_4096.png", "width": 4096, "height": 4096}],
                        }
                    ]
                },
            },
            "parameters": {
                "ok": True,
                "payload": {"syncedBits": 72, "parameters": [{"name": "FaceTrackingFloat", "type": "Float", "bits": 8}]},
            },
            "avatar_items": {
                "ok": True,
                "payload": {
                    "items": [
                        {"gameObjectPath": "Avatar/HatAccessory", "triangleCount": 3200, "componentTypes": ["SkinnedMeshRenderer"]},
                        {"gameObjectPath": "Avatar/Face", "triangleCount": 18000, "blendShapeCount": 80},
                    ]
                },
            },
            "fx": {
                "ok": True,
                "payload": {"layers": [{"layerName": "MA Responsive: Object Toggle Hat"}]},
            },
            "generated_residue": {"ok": True, "payload": {"residueCount": 0}},
        },
    }


def test_dependency_doctor_missing_plugins_is_graceful(tmp_path: Path) -> None:
    project = tmp_path / "UnityProject"
    make_unity_project(project)

    payload = build_dependency_doctor({"projectPath": str(project)})

    assert payload["schema"] == "vrcforge.optimization.v1"
    assert payload["projectReadable"] is True
    assert payload["summary"]["missing"] >= 8
    assert all(item["status"] in {"installed", "missing", "unknown"} for item in payload["dependencies"])
    assert all(item["installMethod"]["automatic"] is False for item in payload["dependencies"])


def test_optimization_report_schema_is_stable_and_plan_only(tmp_path: Path) -> None:
    project = tmp_path / "UnityProject"
    make_unity_project(project)

    payload = build_optimization_report({"projectPath": str(project), "targetProfile": "pc_medium"}, fake_validation())

    assert payload["schema"] == "vrcforge.optimization.v1"
    assert payload["readOnly"] is True
    assert payload["planOnly"] is True
    assert payload["noProjectWrites"] is True
    assert payload["directApplyExposed"] is False
    assert payload["targetProfile"]["id"] == "pc_medium"
    assert payload["baseline"]["performanceHeadline"]["pc"]["rank"] == "Good"
    assert payload["recommendedOrder"]
    assert {card["id"] for card in payload["actionCards"]} >= {
        "optimize_texture_memory",
        "reduce_material_slots",
        "check_parameter_budget",
        "plan_mesh_simplification",
    }
    assert all(tool["directApplyExposed"] is False for tool in payload["tools"])
    assert all(item["externalName"].endswith("apply-request") for item in payload["futureWriteRequestTools"])


def test_plan_tools_do_not_write(tmp_path: Path) -> None:
    project = tmp_path / "UnityProject"
    make_unity_project(project)
    params = {"projectPath": str(project), "targetProfile": "pc_conservative"}

    for definition in OPTIMIZATION_TOOL_DEFINITIONS:
        payload = build_optimization_tool_result(definition["externalName"], params, fake_validation())
        assert payload["schema"] == "vrcforge.optimization.v1"
        assert payload["readOnly"] is True
        assert payload["noProjectWrites"] is True
        assert payload["directApplyExposed"] is False
        assert "apply" not in payload["gatewayTool"].replace("profile_plan", "").replace("atlas_plan", "").replace("trace_plan", "").replace("simplify_plan", "")


def test_mcp_projection_exposes_read_plan_without_direct_apply() -> None:
    manifest = dashboard_server.AGENT_GATEWAY.build_manifest()
    tool_names = {tool["name"] for tool in manifest["tools"]}
    write_targets = {target["name"] for target in manifest["writeTargets"]}

    assert "vrcforge_optimization_plan" in tool_names
    for definition in OPTIMIZATION_TOOL_DEFINITIONS:
        assert definition["gatewayName"] in tool_names
        assert definition["gatewayName"] not in write_targets
    assert not any(name.startswith("vrcforge_optimization_") for name in write_targets)
    assert "vrcforge_optimization_lac_apply" not in tool_names
    assert "vrcforge_optimization_aao_apply" not in tool_names
    assert set(STABLE_OPTIMIZATION_APPLY_REQUEST_GATEWAY_NAMES) <= tool_names
    assert all(name.endswith("_apply_request") for name in STABLE_OPTIMIZATION_APPLY_REQUEST_GATEWAY_NAMES)
    assert "vrcforge_optimization_ttt_atlas_apply_request" not in tool_names
    assert "vrcforge_optimization_meshia_simplify_apply_request" not in tool_names
    assert "vrcforge_configure_optimizer_component" not in tool_names
    assert "vrcforge_configure_optimizer_component" in write_targets

    registry = dashboard_server.AGENT_GATEWAY.build_tool_registry()
    optimization_entries = [entry for entry in registry["tools"] if entry["name"].startswith("vrcforge_optimization")]
    assert optimization_entries
    assert all(entry["category"] == "optimization" for entry in optimization_entries)
    apply_request_entries = [entry for entry in optimization_entries if entry["name"] in STABLE_OPTIMIZATION_APPLY_REQUEST_GATEWAY_NAMES]
    assert apply_request_entries
    assert {entry["risk"] for entry in apply_request_entries} == {"write_request"}
    read_plan_entries = [entry for entry in optimization_entries if entry["name"] not in STABLE_OPTIMIZATION_APPLY_REQUEST_GATEWAY_NAMES]
    assert {entry["risk"] for entry in read_plan_entries} <= {"read_only", "plan"}
    direct_apply_names = {
        name
        for name in tool_names
        if name.startswith("vrcforge_optimization_") and name.endswith("_apply") and not name.endswith("_apply_request")
    }
    assert direct_apply_names == set()


def test_unstable_optimizer_apply_requests_are_not_mcp_direct_tools() -> None:
    manifest = dashboard_server.AGENT_GATEWAY.build_manifest()
    tool_names = {tool["name"] for tool in manifest["tools"]}
    unstable = set(OPTIMIZATION_APPLY_REQUEST_GATEWAY_NAMES) - set(STABLE_OPTIMIZATION_APPLY_REQUEST_GATEWAY_NAMES)

    assert {"vrcforge_optimization_ttt_atlas_apply_request", "vrcforge_optimization_meshia_simplify_apply_request"} <= unstable
    assert unstable.isdisjoint(tool_names)


def test_avatar_optimization_skill_group_contains_stable_request_tools_only() -> None:
    skills = dashboard_server.AGENT_GATEWAY.build_skill_registry()["skills"]
    group = next(skill for skill in skills if skill["name"] == "avatar-optimization-skills")
    allowed = set(group["allowedTools"])

    assert set(STABLE_OPTIMIZATION_APPLY_REQUEST_GATEWAY_NAMES) <= allowed
    assert "vrcforge_optimization_ttt_atlas_apply_request" not in allowed
    assert "vrcforge_optimization_meshia_simplify_apply_request" not in allowed
    assert "vrcforge_package_install_plan" in allowed
    assert "vrcforge_package_install_request" in allowed
    assert "vrcforge_configure_optimizer_component" in allowed


def test_stable_apply_request_preview_is_lightweight_and_ready_for_installed_dependency(tmp_path: Path, monkeypatch) -> None:
    project = tmp_path / "UnityProject"
    make_unity_project(project)
    package_dir = project / "Packages" / "dev.limitex.avatar-compressor"
    package_dir.mkdir()
    (package_dir / "package.json").write_text('{"name":"dev.limitex.avatar-compressor","version":"0.8.0"}', encoding="utf-8")

    def fail_full_plan(_params):
        raise AssertionError("apply-request preview must not run the full optimization plan")

    monkeypatch.setattr(dashboard_server, "build_optimization_plan_sync", fail_full_plan)
    monkeypatch.setattr(
        dashboard_server,
        "package_install_plan_sync",
        lambda _params: (_ for _ in ()).throw(AssertionError("installed dependency must not build an install plan")),
    )

    payload = dashboard_server.build_optimization_apply_request_preview_sync(
        {
            "tool": "optimization.lac.apply-request",
            "projectPath": str(project),
            "avatarPath": "Avatar",
            "targetProfile": "pc_conservative",
        }
    )

    assert payload["readyToRequest"] is True
    assert payload["stableCallable"] is True
    assert payload["writeSupported"] is True
    assert payload["dependencyInstallPlan"] is None
    assert payload["applyArguments"]["componentType"] == "dev.limitex.avatar.compressor.TextureCompressor"


def test_vrc_get_install_command_uses_prerelease_before_package(monkeypatch, tmp_path: Path) -> None:
    project = tmp_path / "UnityProject"
    make_unity_project(project)
    calls: list[list[str]] = []

    monkeypatch.setattr(
        dashboard_server,
        "locate_vpm_package_managers",
        lambda: [
            {
                "name": "vrc-get",
                "path": "C:/tools/vrc-get.exe",
                "kind": "managed-cli",
                "supportsCommandInstall": True,
                "supportsUiHandoff": False,
            }
        ],
    )

    class Proc:
        returncode = 0
        stdout = "installed"
        stderr = ""

    def fake_run(command, **kwargs):
        calls.append(command)
        return Proc()

    monkeypatch.setattr(dashboard_server.subprocess, "run", fake_run)

    result = dashboard_server.install_vpm_package_sync(
        {
            "projectPath": str(project),
            "packageId": "com.anatawa12.avatar-optimizer",
            "includePrerelease": True,
        }
    )

    assert result["ok"] is True
    assert calls
    command = calls[0]
    assert command[:4] == ["C:/tools/vrc-get.exe", "install", "-p", str(project)]
    assert "--prerelease" in command
    assert command[-1] == "com.anatawa12.avatar-optimizer"


def test_public_optimization_docs_include_roadmap_sequence() -> None:
    text = Path("docs/OPTIMIZATION_STRATEGY.md").read_text(encoding="utf-8")
    for marker in ["0.7.2-beta", "0.8.0-beta", "0.8.1-beta", "0.9.0-beta", "0.9.5-rc", "1.0 Public Stable"]:
        assert marker in text
    assert "Calling third-party tools vs first-class VRCForge capabilities" in text
    assert "No direct apply" in text
