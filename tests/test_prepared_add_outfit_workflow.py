from __future__ import annotations

import ast
import copy
from pathlib import Path
from types import SimpleNamespace

import pytest

import dashboard_server
from prepared_add_outfit_workflow_service import ADD_OUTFIT_CONTINUATION_NONCE_KEY
from prepared_unity_execution import (
    PREPARED_UNITY_EXECUTION_ARGUMENT_KEY,
    build_prepared_execution_plan,
)
from unity_execution_plans_workflows import build_workflow_execution_plan
from vrchat_blendshape_agent import UnityMcpError


GUID = "a" * 32
DEPENDENCY_HASH = "b" * 32
WARDROBE_FINGERPRINT = "c" * 64
ROOT = Path(__file__).resolve().parents[1]


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

    prepared, preview = dashboard_server.PREPARED_ADD_OUTFIT_PREPARER.prepare(arguments, None)
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

    result = dashboard_server.PREPARED_ADD_OUTFIT_APPROVED_WRITE.execute(prepared)

    assert result["ok"] is True
    assert result["outfitPath"] == "Avatar/Hoodie"
    assert [tool for tool, _arguments in write_calls] == [tool for tool, _arguments in calls]


def test_add_outfit_target_drift_stops_before_any_write(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    project = _project(tmp_path)
    state = {"created": False, "wardrobe_added": False, "target_drift": False}
    write_calls: list[tuple[str, dict]] = []
    monkeypatch.setattr(dashboard_server, "load_dashboard_settings", lambda _request: SimpleNamespace(unity_mcp_timeout_seconds=30))
    monkeypatch.setattr(dashboard_server, "invoke_unity_mcp", _read_mock(project, state, write_calls))
    prepared, _preview = dashboard_server.PREPARED_ADD_OUTFIT_PREPARER.prepare({
        "projectPath": str(project), "avatarPath": "Avatar", "assetPath": "Assets/Outfits/Hoodie.prefab", "outfitName": "Hoodie",
    }, None)
    state["target_drift"] = True

    with pytest.raises(Exception, match="target already exists"):
        dashboard_server.PREPARED_ADD_OUTFIT_APPROVED_WRITE.execute(prepared)
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
    result = dashboard_server.PREPARED_ADD_OUTFIT_PREVIEW.preview({
        "projectPath": str(project), "avatarPath": "Avatar", "assetPath": "Assets/Outfits/Hoodie.prefab", "outfitName": "Hoodie",
    })
    assert result["ok"] is False
    assert "Approve vrcforge_create_wardrobe first" in result["error"]
    assert write_calls == []


def test_add_outfit_handler_requires_prepared_one_use_context() -> None:
    handler = dashboard_server.AGENT_GATEWAY._write_handlers["vrcforge_add_outfit"]  # noqa: SLF001
    assert handler.handler.__self__ is dashboard_server.PREPARED_ADD_OUTFIT_APPROVED_WRITE
    assert handler.handler.__func__ is type(
        dashboard_server.PREPARED_ADD_OUTFIT_APPROVED_WRITE
    ).execute
    assert handler.request_preparer.__self__ is dashboard_server.PREPARED_ADD_OUTFIT_PREPARER
    assert handler.request_preparer.__func__ is type(
        dashboard_server.PREPARED_ADD_OUTFIT_PREPARER
    ).prepare
    assert handler.requires_approved_execution_context is True
    assert handler.approved_execution_plan_builder is dashboard_server.build_prepared_execution_plan


def test_add_outfit_rejects_caller_supplied_continuation_nonce(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    project = _project(tmp_path)
    state = {"created": False, "wardrobe_added": False, "target_drift": False}
    monkeypatch.setattr(dashboard_server, "invoke_unity_mcp", _read_mock(project, state, []))
    with pytest.raises(RuntimeError, match="reserved Add Outfit continuation nonce"):
        dashboard_server.PREPARED_ADD_OUTFIT_PREPARER.prepare({
            "projectPath": str(project),
            "avatarPath": "Avatar",
            "assetPath": "Assets/Outfits/Hoodie.prefab",
            "outfitName": "Hoodie",
            ADD_OUTFIT_CONTINUATION_NONCE_KEY: "a" * 64,
        }, None)


def test_add_outfit_optional_unpack_freezes_one_nonce_and_exact_order(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    project = _project(tmp_path)
    state = {"created": False, "wardrobe_added": False, "target_drift": False}
    write_calls: list[tuple[str, dict]] = []
    monkeypatch.setattr(dashboard_server, "load_dashboard_settings", lambda _request: SimpleNamespace(unity_mcp_timeout_seconds=30))
    monkeypatch.setattr(dashboard_server, "invoke_unity_mcp", _read_mock(project, state, write_calls))
    prepared, _preview = dashboard_server.PREPARED_ADD_OUTFIT_PREPARER.prepare({
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

    result = dashboard_server.PREPARED_ADD_OUTFIT_APPROVED_WRITE.execute(prepared)
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
    prepared, _preview = dashboard_server.PREPARED_ADD_OUTFIT_PREPARER.prepare({
        "projectPath": str(project), "avatarPath": "Avatar", "assetPath": "Assets/Outfits/Hoodie.prefab", "outfitName": "Hoodie",
    }, None)

    result = dashboard_server.PREPARED_ADD_OUTFIT_APPROVED_WRITE.execute(prepared)
    assert result["ok"] is False
    assert result["committed"] is True
    assert result["commitState"] == "unknown"
    assert result["checkpointRecoveryRequired"] is True
    assert [tool for tool, _arguments in write_calls] == ["vrc_instantiate_prefab", "vrc_setup_outfit"]


@pytest.mark.parametrize(
    ("unpack_prefab", "setup_outfit", "manage_wardrobe"),
    [
        (False, False, False),
        (False, False, True),
        (False, True, False),
        (False, True, True),
        (True, False, False),
        (True, False, True),
        (True, True, False),
        (True, True, True),
    ],
)
def test_add_outfit_all_step_combinations_freeze_one_server_nonce_without_empty_continuation(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    unpack_prefab: bool,
    setup_outfit: bool,
    manage_wardrobe: bool,
) -> None:
    project = _project(tmp_path)
    state = {"created": False, "wardrobe_added": False, "target_drift": False}
    write_calls: list[tuple[str, dict]] = []
    monkeypatch.setattr(
        dashboard_server,
        "invoke_unity_mcp",
        _read_mock(project, state, write_calls),
    )

    prepared, _preview = dashboard_server.PREPARED_ADD_OUTFIT_PREPARER.prepare(
        {
            "projectPath": str(project),
            "avatarPath": "Avatar",
            "assetPath": "Assets/Outfits/Hoodie.prefab",
            "outfitName": "Hoodie",
            "parameterName": "Clothes",
            "unpackPrefab": unpack_prefab,
            "setupOutfit": setup_outfit,
            "manageWardrobe": manage_wardrobe,
        },
        None,
    )
    calls = build_prepared_execution_plan(prepared)
    expected_tools = ["vrc_instantiate_prefab"]
    if unpack_prefab:
        expected_tools.append("vrc_unpack_prefab")
    if setup_outfit:
        expected_tools.append("vrc_setup_outfit")
    if manage_wardrobe:
        expected_tools.append("vrc_add_wardrobe_outfit")

    assert [tool for tool, _arguments in calls] == expected_tools
    nonce = prepared[ADD_OUTFIT_CONTINUATION_NONCE_KEY]
    assert len(nonce) == 64
    continuation_tools = expected_tools[1:]
    if continuation_tools:
        assert calls[0][1]["approvedObjectReceiptNonce"] == nonce
        assert calls[0][1]["approvedContinuationTools"] == continuation_tools
        assert all(
            arguments["approvedObjectReceiptNonce"] == nonce
            for _tool, arguments in calls[1:]
        )
    else:
        assert "approvedObjectReceiptNonce" not in calls[0][1]
        assert "approvedContinuationTools" not in calls[0][1]
    assert write_calls == []


def test_add_outfit_preparer_rejects_reserved_prepared_execution_key_before_reads(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        dashboard_server.PREPARED_ADD_OUTFIT_STATE,
        "build",
        lambda _arguments: pytest.fail("reserved key must fail before state reads"),
    )
    with pytest.raises(RuntimeError, match="reserved prepared Unity execution key"):
        dashboard_server.PREPARED_ADD_OUTFIT_PREPARER.prepare(
            {PREPARED_UNITY_EXECUTION_ARGUMENT_KEY: {}},
            None,
        )


@pytest.mark.parametrize("failure", ["ok_false", "transport", "receipt"])
def test_add_outfit_first_write_failures_require_recovery_with_no_completed_steps(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    failure: str,
) -> None:
    project = _project(tmp_path)
    state = {"created": False, "wardrobe_added": False, "target_drift": False}
    write_calls: list[tuple[str, dict]] = []
    base = _read_mock(project, state, write_calls)
    monkeypatch.setattr(dashboard_server, "invoke_unity_mcp", base)
    monkeypatch.setattr(
        dashboard_server,
        "load_dashboard_settings",
        lambda _request: SimpleNamespace(unity_mcp_timeout_seconds=30),
    )
    prepared, _preview = dashboard_server.PREPARED_ADD_OUTFIT_PREPARER.prepare(
        {
            "projectPath": str(project),
            "avatarPath": "Avatar",
            "assetPath": "Assets/Outfits/Hoodie.prefab",
            "outfitName": "Hoodie",
            "setupOutfit": False,
            "manageWardrobe": False,
        },
        None,
    )

    def fail_first(settings, tool: str, arguments: dict, **kwargs):
        if tool != "vrc_instantiate_prefab":
            return base(settings, tool, arguments, **kwargs)
        if failure == "transport":
            write_calls.append((tool, arguments))
            raise UnityMcpError("first write transport failed")
        if failure == "ok_false":
            write_calls.append((tool, arguments))
            return _mcp({"ok": False, "error": "first write rejected"})
        result = base(settings, tool, arguments, **kwargs)
        payload = copy.deepcopy(result.payload["data"])
        payload["dependencyHash"] = "e" * 32
        return _mcp(payload)

    monkeypatch.setattr(dashboard_server, "invoke_unity_mcp", fail_first)
    result = dashboard_server.PREPARED_ADD_OUTFIT_APPROVED_WRITE.execute(prepared)

    assert result["ok"] is False
    assert result["committed"] is True
    assert result["commitState"] == "unknown"
    assert result["checkpointRecoveryRequired"] is True
    assert result["steps"] == []
    assert [tool for tool, _arguments in write_calls] == ["vrc_instantiate_prefab"]


@pytest.mark.parametrize(
    "drift",
    [
        "instantiate",
        "unpack",
        "setup_start",
        "setup_terminal",
        "wardrobe",
        "final_object",
        "final_wardrobe",
    ],
)
def test_add_outfit_receipt_and_final_readback_drift_always_requires_recovery(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    drift: str,
) -> None:
    project = _project(tmp_path)
    state = {"created": False, "wardrobe_added": False, "target_drift": False}
    write_calls: list[tuple[str, dict]] = []
    base = _read_mock(project, state, write_calls)
    monkeypatch.setattr(dashboard_server, "invoke_unity_mcp", base)
    monkeypatch.setattr(
        dashboard_server,
        "load_dashboard_settings",
        lambda _request: SimpleNamespace(unity_mcp_timeout_seconds=30),
    )
    prepared, _preview = dashboard_server.PREPARED_ADD_OUTFIT_PREPARER.prepare(
        {
            "projectPath": str(project),
            "avatarPath": "Avatar",
            "assetPath": "Assets/Outfits/Hoodie.prefab",
            "outfitName": "Hoodie",
            "parameterName": "Clothes",
            "unpackPrefab": True,
        },
        None,
    )

    def drift_one(settings, tool: str, arguments: dict, **kwargs):
        result = base(settings, tool, arguments, **kwargs)
        payload = copy.deepcopy(result.payload["data"])
        if drift == "instantiate" and tool == "vrc_instantiate_prefab":
            payload["parentGlobalObjectId"] = "gid-drift"
        elif drift == "unpack" and tool == "vrc_unpack_prefab":
            payload["unpacked"] = False
        elif (
            drift == "setup_start"
            and tool == "vrc_setup_outfit"
            and "jobId" not in arguments
        ):
            payload["continuationConsumed"] = True
        elif (
            drift == "setup_terminal"
            and tool == "vrc_setup_outfit"
            and "jobId" in arguments
        ):
            payload["checkpointRecoveryRequired"] = True
        elif drift == "wardrobe" and tool == "vrc_add_wardrobe_outfit":
            payload["assignedValue"] = int(payload["assignedValue"]) + 1
        elif (
            drift == "final_object"
            and tool == "vrc_get_gameobject"
            and arguments.get("gameObjectPath") == "Avatar/Hoodie"
        ):
            payload["globalObjectId"] = "gid-drift"
        elif (
            drift == "final_wardrobe"
            and tool == "vrc_scan_wardrobe"
            and state["wardrobe_added"]
        ):
            payload["fingerprint"] = "e" * 64
        return _mcp(payload)

    monkeypatch.setattr(dashboard_server, "invoke_unity_mcp", drift_one)
    result = dashboard_server.PREPARED_ADD_OUTFIT_APPROVED_WRITE.execute(prepared)

    assert result["ok"] is False
    assert result["committed"] is True
    assert result["commitState"] == "unknown"
    assert result["checkpointRecoveryRequired"] is True


def test_add_outfit_query_only_uses_selected_project_without_writes(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    project = _project(tmp_path)
    state = {"created": False, "wardrobe_added": False, "target_drift": False}
    write_calls: list[tuple[str, dict]] = []
    base = _read_mock(project, state, write_calls)
    searches: list[dict] = []

    def query_read(settings, tool: str, arguments: dict, **kwargs):
        if tool == "vrc_find_assets":
            searches.append(copy.deepcopy(arguments))
            return _mcp(
                {
                    "ok": True,
                    "assets": [
                        {
                            "assetPath": "Assets/Outfits/Hoodie.prefab",
                            "guid": GUID,
                            "name": "Hoodie",
                        }
                    ],
                }
            )
        return base(settings, tool, arguments, **kwargs)

    monkeypatch.setattr(dashboard_server, "invoke_unity_mcp", query_read)
    monkeypatch.setattr(
        dashboard_server.DASHBOARD_STATE,
        "selected_project_path",
        str(project),
    )
    result = dashboard_server.PREPARED_ADD_OUTFIT_PREVIEW.preview(
        {
            "avatarPath": "Avatar",
            "assetQuery": "hoodie",
            "outfitName": "Hoodie",
            "manageWardrobe": False,
            "setupOutfit": False,
        }
    )

    assert result["ok"] is True
    assert result["plan"]["projectPath"] == str(project.resolve())
    assert result["plan"]["asset"]["assetPath"] == "Assets/Outfits/Hoodie.prefab"
    assert searches == [
        {
            "query": "hoodie",
            "typeName": "Prefab",
            "folder": "",
            "limit": 1,
        }
    ]
    assert write_calls == []


def test_add_outfit_setup_poll_uses_approved_settings_empty_overrides_and_peer_lane(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    project = _project(tmp_path)
    state = {"created": False, "wardrobe_added": False, "target_drift": False}
    write_calls: list[tuple[str, dict]] = []
    base = _read_mock(project, state, write_calls)
    setup_calls: list[tuple[object, dict, dict]] = []

    def observe(settings, tool: str, arguments: dict, **kwargs):
        if tool == "vrc_setup_outfit":
            setup_calls.append((settings, copy.deepcopy(arguments), copy.deepcopy(kwargs)))
        return base(settings, tool, arguments, **kwargs)

    def settings_factory(_request):
        return SimpleNamespace(
            unity_mcp_timeout_seconds=30,
            connection_marker=object(),
        )

    monkeypatch.setattr(dashboard_server, "invoke_unity_mcp", observe)
    monkeypatch.setattr(dashboard_server, "load_dashboard_settings", settings_factory)
    prepared, _preview = dashboard_server.PREPARED_ADD_OUTFIT_PREPARER.prepare(
        {
            "projectPath": str(project),
            "avatarPath": "Avatar",
            "assetPath": "Assets/Outfits/Hoodie.prefab",
            "outfitName": "Hoodie",
            "manageWardrobe": False,
        },
        None,
    )
    result = dashboard_server.PREPARED_ADD_OUTFIT_APPROVED_WRITE.execute(prepared)

    assert result["ok"] is True
    assert len(setup_calls) == 2
    start_settings, start_arguments, start_kwargs = setup_calls[0]
    poll_settings, poll_arguments, poll_kwargs = setup_calls[1]
    assert start_settings.unity_mcp_timeout_seconds == 300
    assert start_arguments["outfitPath"] == "Avatar/Hoodie"
    assert start_kwargs == {}
    assert poll_settings.connection_marker is start_settings.connection_marker
    assert poll_settings.unity_mcp_timeout_seconds == 8
    assert poll_arguments == {"jobId": "1" * 32}
    assert poll_kwargs["execution_context"] == {"lane": "app_setup_outfit_poll"}


def test_add_outfit_owner_ports_are_narrow_and_registry_keeps_checkpoint_contract() -> None:
    source_path = ROOT / "prepared_add_outfit_workflow_service.py"
    source = source_path.read_text(encoding="utf-8")
    tree = ast.parse(source)
    assert "invoke_unity_mcp" not in source
    assert "AgentGateway" not in source
    assert "dashboard_server" not in source
    class_names = {
        node.name
        for node in tree.body
        if isinstance(node, ast.ClassDef)
    }
    assert {
        "PreparedAddOutfitStateBuilder",
        "PreparedAddOutfitPreviewService",
        "PreparedAddOutfitPreparer",
        "PreparedAddOutfitApprovedWriteService",
    }.issubset(class_names)
    assert set(
        dashboard_server.PREPARED_ADD_OUTFIT_APPROVED_WRITE._ports.__dataclass_fields__
    ) == {
        "state_builder",
        "digest",
        "verify_project_identity",
        "require_evidence",
        "load_settings",
        "instantiate",
        "unpack",
        "start_setup",
        "poll_setup",
        "add_wardrobe",
        "read_gameobject",
        "read_wardrobe",
        "log",
        "map_error",
        "handled_errors",
    }
    handler = dashboard_server.AGENT_GATEWAY._write_handlers["vrcforge_add_outfit"]  # noqa: SLF001
    assert handler.handler.__self__ is dashboard_server.PREPARED_ADD_OUTFIT_APPROVED_WRITE
    assert handler.request_preparer.__self__ is dashboard_server.PREPARED_ADD_OUTFIT_PREPARER
    assert handler.requires_approved_execution_context is True
    assert handler.checkpoint_prepare_handler is dashboard_server.prepare_authoritative_unity_checkpoint_sync
    assert handler.approved_execution_plan_builder is dashboard_server.build_prepared_execution_plan
    with pytest.raises(ValueError, match="needs runtime readback"):
        build_workflow_execution_plan("vrcforge_add_outfit", {})
