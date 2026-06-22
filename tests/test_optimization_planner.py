from __future__ import annotations

from pathlib import Path

import dashboard_server
from optimization_service import (
    OPTIMIZATION_TOOL_DEFINITIONS,
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

    registry = dashboard_server.AGENT_GATEWAY.build_tool_registry()
    optimization_entries = [entry for entry in registry["tools"] if entry["name"].startswith("vrcforge_optimization")]
    assert optimization_entries
    assert all(entry["category"] == "optimization" for entry in optimization_entries)
    assert {entry["risk"] for entry in optimization_entries} <= {"read_only", "plan"}


def test_public_optimization_docs_include_roadmap_sequence() -> None:
    text = Path("docs/OPTIMIZATION_STRATEGY.md").read_text(encoding="utf-8")
    for marker in ["0.7.2-beta", "0.8.0-beta", "0.8.1-beta", "0.9.0-beta", "0.9.5-rc", "1.0 Public Stable"]:
        assert marker in text
    assert "Calling third-party tools vs first-class VRCForge capabilities" in text
    assert "No direct apply" in text
