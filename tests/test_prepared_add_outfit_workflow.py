from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace

import pytest

import dashboard_server
from prepared_unity_execution import build_prepared_execution_plan


GUID = "a" * 32
DEPENDENCY_HASH = "b" * 32
WARDROBE_FINGERPRINT = "c" * 64


def _project(tmp_path: Path) -> Path:
    project = tmp_path / "Project"
    (project / "Assets").mkdir(parents=True)
    (project / "Packages").mkdir()
    (project / "ProjectSettings").mkdir()
    return project


def _mcp(payload: dict) -> dashboard_server.McpResult:
    return dashboard_server.McpResult(0, "", "", {"data": payload})


def _read_mock(project: Path, state: dict[str, bool], write_calls: list[tuple[str, dict]]):
    def invoke(_settings, tool: str, arguments: dict, **_kwargs):
        if tool == "vrc_get_asset_info":
            return _mcp({
                "ok": True,
                "assetPath": "Assets/Outfits/Hoodie.prefab",
                "guid": GUID,
                "dependencyHash": DEPENDENCY_HASH,
                "name": "Hoodie",
                "assetType": "UnityEngine.GameObject",
                "prefabAssetType": "Regular",
                "isPrefab": True,
            })
        if tool == "vrc_get_gameobject":
            path = arguments["gameObjectPath"]
            if path == "Avatar/Hoodie":
                if not state["created"]:
                    return _mcp({"ok": False, "error": "not found"})
                return _mcp({
                    "ok": True,
                    "gameObjectPath": path,
                    "globalObjectId": "gid-outfit",
                    "scenePath": "Assets/Scenes/SampleScene.unity",
                    "hierarchyPathCount": 1,
                    "children": [],
                })
            children = [{"gameObjectPath": "Avatar/Hoodie"}] if state["target_drift"] else []
            return _mcp({
                "ok": True,
                "gameObjectPath": "Avatar",
                "globalObjectId": "gid-avatar",
                "scenePath": "Assets/Scenes/SampleScene.unity",
                "hierarchyPathCount": 1,
                "children": children,
            })
        if tool == "vrc_scan_wardrobe":
            outfits = [{"value": 0, "onObjects": []}, {"value": 2, "onObjects": ["Old"]}]
            if state["wardrobe_added"]:
                outfits.append({"value": 3, "onObjects": ["Hoodie"]})
            return _mcp({
                "ok": True,
                "avatarPath": "Avatar",
                "avatarName": "Avatar",
                "fxControllerPath": "Assets/FX.controller",
                "fingerprint": ("d" * 64) if state["wardrobe_added"] else WARDROBE_FINGERPRINT,
                "wardrobes": [{"parameterName": "Clothes", "outfits": outfits}],
                "wardrobeCandidates": [],
            })
        if tool == "vrc_setup_outfit" and "jobId" in arguments:
            return _mcp({
                "ok": True,
                "status": "completed",
                "outfitGlobalObjectId": "gid-outfit",
                "continuationConsumed": True,
                "committed": True,
                "commitState": "complete",
                "checkpointRecoveryRequired": False,
            })
        write_calls.append((tool, arguments))
        if tool == "vrc_instantiate_prefab":
            state["created"] = True
            return _mcp({
                "ok": True,
                "assetPath": arguments["assetPath"],
                "gameObjectPath": arguments["expectedResultPath"],
                "prefabGuid": arguments["expectedPrefabGuid"],
                "dependencyHash": arguments["expectedAssetDependencyHash"],
                "scenePath": arguments["expectedScenePath"],
                "parentGlobalObjectId": arguments["expectedParentGlobalObjectId"],
                "globalObjectId": "gid-outfit",
                "continuationRegistered": bool(arguments.get("approvedContinuationTools")),
                "continuationCount": len(arguments.get("approvedContinuationTools") or []),
            })
        if tool == "vrc_unpack_prefab":
            return _mcp({
                "ok": True,
                "gameObjectPath": arguments["gameObjectPath"],
                "globalObjectId": "gid-outfit",
                "unpacked": True,
                "continuationConsumed": bool(arguments.get("approvedObjectReceiptNonce")),
            })
        if tool == "vrc_setup_outfit":
            return _mcp({
                "ok": True,
                "pending": True,
                "status": "pending",
                "jobId": "1" * 32,
                "outfitGlobalObjectId": "gid-outfit",
                "continuationConsumed": False,
            })
        if tool == "vrc_add_wardrobe_outfit":
            state["wardrobe_added"] = True
            return _mcp({
                "ok": True,
                "parameterName": arguments["parameterName"],
                "outfitName": arguments["outfitName"],
                "assignedValue": arguments["expectedAssignedValue"],
                "continuationConsumed": bool(arguments.get("approvedObjectReceiptNonce")),
                "wardrobeFingerprint": "d" * 64,
            })
        raise AssertionError(tool)

    return invoke


def test_add_outfit_preparer_freezes_exact_calls_and_executor_checks_readback(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    project = _project(tmp_path)
    state = {"created": False, "wardrobe_added": False, "target_drift": False}
    write_calls: list[tuple[str, dict]] = []
    monkeypatch.setattr(dashboard_server, "load_dashboard_settings", lambda _request: SimpleNamespace(unity_mcp_timeout_seconds=30))
    monkeypatch.setattr(dashboard_server, "invoke_unity_mcp", _read_mock(project, state, write_calls))
    arguments = {
        "projectPath": str(project),
        "avatarPath": "Avatar",
        "assetPath": "Assets/Outfits/Hoodie.prefab",
        "outfitName": "Hoodie",
        "parameterName": "Clothes",
    }

    prepared, preview = dashboard_server.prepare_add_outfit_request(arguments, None)
    calls = build_prepared_execution_plan(prepared)

    assert preview["ok"] is True
    assert [tool for tool, _arguments in calls] == [
        "vrc_instantiate_prefab",
        "vrc_setup_outfit",
        "vrc_add_wardrobe_outfit",
    ]
    assert calls[0][1]["expectedParentGlobalObjectId"] == "gid-avatar"
    assert calls[0][1]["expectedResultPath"] == "Avatar/Hoodie"
    nonce = calls[0][1]["approvedObjectReceiptNonce"]
    assert len(nonce) == 64
    assert calls[0][1]["approvedContinuationTools"] == ["vrc_setup_outfit", "vrc_add_wardrobe_outfit"]
    assert calls[1][1]["approvedObjectReceiptNonce"] == nonce
    assert calls[2][1]["approvedObjectReceiptNonce"] == nonce
    assert calls[-1][1]["value"] == 3
    assert calls[-1][1]["expectedAssignedValue"] == 3
    assert calls[-1][1]["expectedWardrobeFingerprint"] == WARDROBE_FINGERPRINT

    result = dashboard_server.add_outfit_workflow_approved_sync(prepared)

    assert result["ok"] is True
    assert result["outfitPath"] == "Avatar/Hoodie"
    assert [tool for tool, _arguments in write_calls] == [tool for tool, _arguments in calls]


def test_add_outfit_target_drift_stops_before_any_write(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    project = _project(tmp_path)
    state = {"created": False, "wardrobe_added": False, "target_drift": False}
    write_calls: list[tuple[str, dict]] = []
    monkeypatch.setattr(dashboard_server, "load_dashboard_settings", lambda _request: SimpleNamespace(unity_mcp_timeout_seconds=30))
    monkeypatch.setattr(dashboard_server, "invoke_unity_mcp", _read_mock(project, state, write_calls))
    prepared, _preview = dashboard_server.prepare_add_outfit_request({
        "projectPath": str(project), "avatarPath": "Avatar", "assetPath": "Assets/Outfits/Hoodie.prefab", "outfitName": "Hoodie",
    }, None)
    state["target_drift"] = True

    with pytest.raises(Exception, match="target already exists"):
        dashboard_server.add_outfit_workflow_approved_sync(prepared)
    assert write_calls == []


def test_missing_verified_wardrobe_requires_separate_approval(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    project = _project(tmp_path)
    state = {"created": False, "wardrobe_added": False, "target_drift": False}
    write_calls: list[tuple[str, dict]] = []
    invoke = _read_mock(project, state, write_calls)

    def no_wardrobe(settings, tool, arguments):
        if tool == "vrc_scan_wardrobe":
            return _mcp({"ok": True, "fingerprint": WARDROBE_FINGERPRINT, "wardrobes": [], "wardrobeCandidates": [{"parameterName": "MaybeClothes"}]})
        return invoke(settings, tool, arguments)

    monkeypatch.setattr(dashboard_server, "load_dashboard_settings", lambda _request: SimpleNamespace(unity_mcp_timeout_seconds=30))
    monkeypatch.setattr(dashboard_server, "invoke_unity_mcp", no_wardrobe)
    result = dashboard_server.preview_add_outfit_workflow_sync({
        "projectPath": str(project), "avatarPath": "Avatar", "assetPath": "Assets/Outfits/Hoodie.prefab", "outfitName": "Hoodie",
    })
    assert result["ok"] is False
    assert "Approve vrcforge_create_wardrobe first" in result["error"]
    assert write_calls == []


def test_add_outfit_handler_requires_prepared_one_use_context() -> None:
    handler = dashboard_server.AGENT_GATEWAY._write_handlers["vrcforge_add_outfit"]  # noqa: SLF001
    assert handler.handler is dashboard_server.add_outfit_workflow_approved_sync
    assert handler.request_preparer is dashboard_server.prepare_add_outfit_request
    assert handler.requires_approved_execution_context is True
    assert handler.approved_execution_plan_builder is dashboard_server.build_prepared_execution_plan


def test_add_outfit_rejects_caller_supplied_continuation_nonce(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    project = _project(tmp_path)
    state = {"created": False, "wardrobe_added": False, "target_drift": False}
    monkeypatch.setattr(dashboard_server, "invoke_unity_mcp", _read_mock(project, state, []))
    with pytest.raises(RuntimeError, match="reserved Add Outfit continuation nonce"):
        dashboard_server.prepare_add_outfit_request({
            "projectPath": str(project),
            "avatarPath": "Avatar",
            "assetPath": "Assets/Outfits/Hoodie.prefab",
            "outfitName": "Hoodie",
            dashboard_server.ADD_OUTFIT_CONTINUATION_NONCE_KEY: "a" * 64,
        }, None)


def test_add_outfit_optional_unpack_freezes_one_nonce_and_exact_order(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    project = _project(tmp_path)
    state = {"created": False, "wardrobe_added": False, "target_drift": False}
    write_calls: list[tuple[str, dict]] = []
    monkeypatch.setattr(dashboard_server, "load_dashboard_settings", lambda _request: SimpleNamespace(unity_mcp_timeout_seconds=30))
    monkeypatch.setattr(dashboard_server, "invoke_unity_mcp", _read_mock(project, state, write_calls))
    prepared, _preview = dashboard_server.prepare_add_outfit_request({
        "projectPath": str(project),
        "avatarPath": "Avatar",
        "assetPath": "Assets/Outfits/Hoodie.prefab",
        "outfitName": "Hoodie",
        "parameterName": "Clothes",
        "unpackPrefab": True,
    }, None)
    calls = build_prepared_execution_plan(prepared)
    nonce = calls[0][1]["approvedObjectReceiptNonce"]
    assert [tool for tool, _arguments in calls] == [
        "vrc_instantiate_prefab", "vrc_unpack_prefab", "vrc_setup_outfit", "vrc_add_wardrobe_outfit",
    ]
    assert calls[0][1]["approvedContinuationTools"] == [
        "vrc_unpack_prefab", "vrc_setup_outfit", "vrc_add_wardrobe_outfit",
    ]
    assert all(arguments["approvedObjectReceiptNonce"] == nonce for _tool, arguments in calls[1:])

    result = dashboard_server.add_outfit_workflow_approved_sync(prepared)
    assert result["ok"] is True
    assert [tool for tool, _arguments in write_calls] == [tool for tool, _arguments in calls]


def test_setup_continuation_rejection_stops_wardrobe_and_requires_recovery(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    project = _project(tmp_path)
    state = {"created": False, "wardrobe_added": False, "target_drift": False}
    write_calls: list[tuple[str, dict]] = []
    base_invoke = _read_mock(project, state, write_calls)

    def reject_setup(settings, tool: str, arguments: dict):
        if tool == "vrc_setup_outfit":
            write_calls.append((tool, arguments))
            return _mcp({"ok": False, "error": "Approval-bound object continuation order drifted."})
        return base_invoke(settings, tool, arguments)

    monkeypatch.setattr(dashboard_server, "load_dashboard_settings", lambda _request: SimpleNamespace(unity_mcp_timeout_seconds=30))
    monkeypatch.setattr(dashboard_server, "invoke_unity_mcp", reject_setup)
    prepared, _preview = dashboard_server.prepare_add_outfit_request({
        "projectPath": str(project), "avatarPath": "Avatar", "assetPath": "Assets/Outfits/Hoodie.prefab", "outfitName": "Hoodie",
    }, None)

    result = dashboard_server.add_outfit_workflow_approved_sync(prepared)
    assert result["ok"] is False
    assert result["committed"] is True
    assert result["commitState"] == "unknown"
    assert result["checkpointRecoveryRequired"] is True
    assert [tool for tool, _arguments in write_calls] == ["vrc_instantiate_prefab", "vrc_setup_outfit"]
