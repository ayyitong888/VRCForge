from __future__ import annotations

import hashlib
from pathlib import Path
import threading

import pytest

import vrchat_blendshape_agent as agent
from approved_unity_execution import (
    ApprovedUnityExecutionClaimError,
    bind_approved_unity_execution,
    create_approved_unity_execution_plan,
    freeze_approved_unity_execution_plan,
    validate_frozen_approved_unity_execution_plan,
)
from unity_mcp_core_client import UnityMcpCoreConnectionError, UnityMcpCoreError


def _context(project: Path, *, issued: int = 1_000, expires: int = 2_000) -> dict[str, object]:
    return {
        "lane": "approved_write",
        "approvalId": "approval-1",
        "checkpointId": "checkpoint-1",
        "targetTool": "gateway-write",
        "projectRoot": str(project),
        "issuedAtUnixMs": issued,
        "expiresAtUnixMs": expires,
    }


def _external_context(
    project: Path,
    *,
    issued: int = 1_000,
    expires: int = 2_000,
) -> dict[str, object]:
    return {
        "lane": "external_mcp_write",
        "operationId": "mcpwrite-1",
        "targetTool": "gateway-write",
        "projectRoot": str(project),
        "issuedAtUnixMs": issued,
        "expiresAtUnixMs": expires,
    }


def _settings(project: Path, *, retries: int = 3) -> agent.Settings:
    return agent.Settings(
        llm_provider="openai",
        llm_api_key="",
        llm_base_url="https://example.invalid/v1",
        llm_model="test",
        llm_api_key_env="",
        gemini_thinking_level="",
        unity_mcp_command=[],
        unity_mcp_host="127.0.0.1",
        unity_mcp_port=0,
        unity_mcp_instance="project-scoped",
        unity_mcp_retries=retries,
        unity_mcp_retry_backoff_seconds=0,
        unity_mcp_timeout_seconds=45,
        export_tool_name="vrc_read",
        execute_tool_name="vrc_write",
        export_path=project / "export.json",
        min_confidence=0.5,
        unity_project_path=str(project),
    )


def _descriptor(project: Path) -> None:
    descriptor = project / "Library" / "VRCForge" / "mcp-core.json"
    descriptor.parent.mkdir(parents=True)
    descriptor.write_text("{}", encoding="utf-8")


def _core_markers(project: Path) -> None:
    for relative_path in (
        "Assets/VRCForge/Core/MCP/VRCForgeCommandAttribute.cs",
        "Assets/VRCForge/Core/MCP/VRCForgeInputAttribute.cs",
        "Assets/VRCForge/Core/MCP/VRCForgeToolRegistry.cs",
        "Assets/VRCForge/Core/MCP/VRCForgeToolResult.cs",
        "Assets/VRCForge/Editor/MCP/VRCForgeMcpCoreServer.cs",
    ):
        marker = project / relative_path
        marker.parent.mkdir(parents=True, exist_ok=True)
        marker.write_text("// marker", encoding="utf-8")


def test_frozen_plan_is_digest_bound_but_not_a_runtime_capability(tmp_path: Path) -> None:
    frozen = freeze_approved_unity_execution_plan([("vrc_write", {"value": "汉字"})])
    validated = validate_frozen_approved_unity_execution_plan(frozen)

    assert validated.calls[0].tool_name == "vrc_write"
    assert validated.plan_digest == frozen["planDigest"]
    tampered = dict(frozen)
    tampered["calls"] = [{"toolName": "vrc_other", "argumentsSha256": frozen["calls"][0]["argumentsSha256"]}]
    with pytest.raises(ValueError, match="digest"):
        validate_frozen_approved_unity_execution_plan(tampered)


def test_external_context_uses_operation_binding_without_internal_approval_state(
    tmp_path: Path,
) -> None:
    plan = create_approved_unity_execution_plan(
        _external_context(tmp_path),
        [("vrc_write", {"value": 1})],
    )
    context = plan.diagnostic_context()
    assert context["lane"] == "external_mcp_write"
    assert context["operationId"] == "mcpwrite-1"
    assert "approvalId" not in context
    assert "checkpointId" not in context

    contaminated = _external_context(tmp_path)
    contaminated["checkpointId"] = "must-not-be-accepted"
    with pytest.raises(ValueError, match="context is invalid"):
        create_approved_unity_execution_plan(
            contaminated,
            [("vrc_write", {"value": 1})],
        )


def test_plan_claim_is_exact_ordered_one_use_and_rejects_drift(tmp_path: Path) -> None:
    plan = create_approved_unity_execution_plan(_context(tmp_path), [("vrc_write", {"value": 1})])

    claim = plan.claim("vrc_write", {"value": 1}, tmp_path, now_unix_ms=1_500)
    assert claim.execution_context["unityToolName"] == "vrc_write"
    assert claim.execution_context["argumentsSha256"]
    claim.complete()
    with pytest.raises(ApprovedUnityExecutionClaimError, match="consumed"):
        plan.claim("vrc_write", {"value": 1}, tmp_path, now_unix_ms=1_500)

    tool_plan = create_approved_unity_execution_plan(_context(tmp_path), [("vrc_write", {"value": 1})])
    with pytest.raises(ApprovedUnityExecutionClaimError, match="tool"):
        tool_plan.claim("vrc_other", {"value": 1}, tmp_path, now_unix_ms=1_500)
    with pytest.raises(ApprovedUnityExecutionClaimError, match="arguments"):
        tool_plan.claim("vrc_write", {"value": 2}, tmp_path, now_unix_ms=1_500)
    with pytest.raises(ApprovedUnityExecutionClaimError, match="expired"):
        tool_plan.claim("vrc_write", {"value": 1}, tmp_path, now_unix_ms=2_000)
    other_project = tmp_path / "other"
    other_project.mkdir()
    with pytest.raises(ApprovedUnityExecutionClaimError, match="project root drifted"):
        tool_plan.claim("vrc_write", {"value": 1}, other_project, now_unix_ms=1_500)


def test_plan_rejects_cross_thread_claim_and_burns_remaining_calls(tmp_path: Path) -> None:
    plan = create_approved_unity_execution_plan(
        _context(tmp_path),
        [("vrc_write", {"value": 1})],
    )
    failures: list[str] = []

    def cross_thread_claim() -> None:
        try:
            plan.claim("vrc_write", {"value": 1}, tmp_path, now_unix_ms=1_500)
        except ApprovedUnityExecutionClaimError as exc:
            failures.append(str(exc))

    worker = threading.Thread(target=cross_thread_claim)
    worker.start()
    worker.join()
    assert failures == ["Approved Unity execution cannot cross its handler thread."]

    plan.burn()
    with pytest.raises(ApprovedUnityExecutionClaimError, match="uncertain and closed"):
        plan.claim("vrc_write", {"value": 1}, tmp_path, now_unix_ms=1_500)


def test_frozen_plan_has_a_fixed_call_bound() -> None:
    with pytest.raises(ValueError, match="too many calls"):
        freeze_approved_unity_execution_plan(
            [("vrc_write", {"index": index}) for index in range(65)]
        )


def test_approved_write_uses_bound_claim_once_and_rejects_explicit_dict(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _descriptor(tmp_path)
    observed: list[dict[str, object]] = []

    class FakeCoreClient:
        def __init__(self, _project: str, *, timeout_seconds: int) -> None:
            assert timeout_seconds == 45

        def call_tool(self, _name: str, _arguments: dict, *, execution_context=None) -> dict:
            observed.append(dict(execution_context or {}))
            return {"isError": False, "content": []}

    monkeypatch.setattr(agent, "UnityMcpCoreClient", FakeCoreClient)
    settings = _settings(tmp_path)
    plan = create_approved_unity_execution_plan(_context(tmp_path, issued=0, expires=9_999_999_999_999), [("vrc_write", {"value": 1})])
    with bind_approved_unity_execution(plan):
        result = agent.invoke_unity_mcp(settings, "vrc_write", {"value": 1})
    assert result.exit_code == 0
    assert len(observed) == 1
    assert observed[0]["unityToolName"] == "vrc_write"
    assert observed[0]["targetTool"] == "vrc_write"
    assert observed[0]["gatewayTargetTool"] == "gateway-write"
    assert observed[0]["executionId"]

    with pytest.raises(agent.UnityMcpError, match="explicit contexts"):
        agent.invoke_unity_mcp(
            settings,
            "vrc_write",
            {"value": 1},
            execution_context={"lane": "approved_write"},
        )


def test_previous_core_upgrade_gate_requires_exact_current_vrcforge_package(tmp_path: Path) -> None:
    package = tmp_path / "VRCForge.unitypackage"
    package.write_bytes(b"exact core upgrade package")
    arguments = {
        "projectPath": str(tmp_path),
        "unityPackagePath": str(package),
        "expectedSha256": hashlib.sha256(package.read_bytes()).hexdigest(),
        "expectedSize": package.stat().st_size,
        "expectedAssetPaths": list(agent._CORE_UPGRADE_REQUIRED_ASSETS),
        "interactive": False,
    }
    assert agent._is_previous_core_upgrade_call(
        "vrc_import_unitypackage", arguments, {"lane": "approved_write"}
    ) is True
    assert agent._is_previous_core_upgrade_call(
        "vrc_import_unitypackage", {**arguments, "interactive": True}, {"lane": "approved_write"}
    ) is False
    assert agent._is_previous_core_upgrade_call(
        "vrc_import_unitypackage",
        {**arguments, "expectedAssetPaths": []},
        {"lane": "approved_write"},
    ) is False
    assert agent._is_previous_core_upgrade_call(
        "vrc_import_unitypackage",
        {"jobId": "a" * 32},
        {"lane": "app_unitypackage_import_poll"},
    ) is True
    refresh = {
        "projectPath": str(tmp_path),
        "resolvePackages": False,
        "packageResolveTimeoutSeconds": 120,
    }
    assert agent._is_previous_core_upgrade_call(
        "vrc_refresh_asset_database", refresh, {"lane": "approved_write"}
    ) is True
    assert agent._is_previous_core_upgrade_call(
        "vrc_refresh_asset_database", refresh, {"lane": "external_mcp_write"}
    ) is True
    assert agent._is_previous_core_upgrade_call(
        "vrc_refresh_asset_database",
        {**refresh, "resolvePackages": True},
        {"lane": "approved_write"},
    ) is False


def test_approved_core_upgrade_alone_enables_and_verifies_previous_contract(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _descriptor(tmp_path)
    package = tmp_path / "VRCForge.unitypackage"
    package.write_bytes(b"exact core upgrade package")
    arguments = {
        "projectPath": str(tmp_path),
        "unityPackagePath": str(package),
        "expectedSha256": hashlib.sha256(package.read_bytes()).hexdigest(),
        "expectedSize": package.stat().st_size,
        "expectedAssetPaths": list(agent._CORE_UPGRADE_REQUIRED_ASSETS),
        "interactive": False,
    }
    events: list[str] = []

    class PreviousCoreClient:
        uses_previous_contract = True

        def __init__(self, _project: str, *, timeout_seconds: int, allow_previous_contract: bool) -> None:
            assert timeout_seconds == 45
            assert allow_previous_contract is True
            events.append("opened_previous")

        def list_tools(self, *, exposure_layer: str) -> list[dict]:
            assert exposure_layer == "execution"
            events.append("verified_previous")
            return []

        def call_tool(self, name: str, actual: dict, *, execution_context=None) -> dict:
            assert name == "vrc_import_unitypackage"
            assert actual == arguments
            assert execution_context["unityToolName"] == name
            events.append("called_upgrade")
            return {"isError": False, "content": []}

    monkeypatch.setattr(agent, "UnityMcpCoreClient", PreviousCoreClient)
    plan = create_approved_unity_execution_plan(
        _context(tmp_path, issued=0, expires=9_999_999_999_999),
        [("vrc_import_unitypackage", arguments)],
    )
    with bind_approved_unity_execution(plan):
        result = agent.invoke_unity_mcp(
            _settings(tmp_path),
            "vrc_import_unitypackage",
            arguments,
        )
    assert result.exit_code == 0
    assert events == ["opened_previous", "verified_previous", "called_upgrade"]
    assert plan.consumed is True


def test_approved_write_transport_failure_is_uncertain_and_never_retries(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _descriptor(tmp_path)
    attempts = 0

    class FailingCoreClient:
        def __init__(self, _project: str, *, timeout_seconds: int) -> None:
            pass

        def call_tool(self, _name: str, _arguments: dict, *, execution_context=None) -> dict:
            nonlocal attempts
            attempts += 1
            raise UnityMcpCoreConnectionError("Unity MCP Core connection failed.")

    monkeypatch.setattr(agent, "UnityMcpCoreClient", FailingCoreClient)
    plan = create_approved_unity_execution_plan(_context(tmp_path, issued=0, expires=9_999_999_999_999), [("vrc_write", {"value": 1})])
    with bind_approved_unity_execution(plan):
        with pytest.raises(agent.UnityMcpError, match="single transport attempt") as raised:
            agent.invoke_unity_mcp(
                _settings(tmp_path),
                "vrc_write",
                {"value": 1},
            )
        with pytest.raises(agent.UnityMcpError, match="uncertain"):
            agent.invoke_unity_mcp(_settings(tmp_path), "vrc_write", {"value": 1})
    assert attempts == 1
    assert plan.uncertain_state is True
    assert raised.value.cause_code == "unity_core_unavailable"
    assert raised.value.retryable is True
    assert raised.value.core_tool == "vrc_write"
    assert "vrc_write" not in str(raised.value)


def test_approved_write_waits_for_missing_core_descriptor_before_claim(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _core_markers(tmp_path)
    sleep_calls: list[float] = []
    transport_attempts = 0

    def restore_descriptor(seconds: float) -> None:
        sleep_calls.append(seconds)
        _descriptor(tmp_path)

    class FakeCoreClient:
        def __init__(self, _project: str, *, timeout_seconds: int) -> None:
            assert timeout_seconds == 45

        def call_tool(self, _name: str, _arguments: dict, *, execution_context=None) -> dict:
            nonlocal transport_attempts
            transport_attempts += 1
            assert execution_context["unityToolName"] == "vrc_write"
            return {"isError": False, "content": []}

    monkeypatch.setattr(agent.time, "sleep", restore_descriptor)
    monkeypatch.setattr(agent, "UnityMcpCoreClient", FakeCoreClient)
    plan = create_approved_unity_execution_plan(
        _context(tmp_path, issued=0, expires=9_999_999_999_999),
        [("vrc_write", {"value": 1})],
    )

    with bind_approved_unity_execution(plan):
        result = agent.invoke_unity_mcp(_settings(tmp_path, retries=3), "vrc_write", {"value": 1})

    assert result.exit_code == 0
    assert len(sleep_calls) == 1
    assert transport_attempts == 1
    assert plan.consumed is True


def test_missing_core_descriptor_is_a_known_pre_route_no_mutation_failure(tmp_path: Path) -> None:
    _core_markers(tmp_path)
    plan = create_approved_unity_execution_plan(
        _context(tmp_path, issued=0, expires=9_999_999_999_999),
        [("vrc_write", {"value": 1})],
    )

    with bind_approved_unity_execution(plan):
        with pytest.raises(agent.UnityMcpError, match="runtime descriptor is missing") as raised:
            agent.invoke_unity_mcp(
                _settings(tmp_path, retries=1),
                "vrc_write",
                {"value": 1},
            )

    error = raised.value.external_error
    assert raised.value.cause_code == "unity_core_starting"
    assert error["failureLayer"] == "unity_core_pre_route"
    assert error["failurePhase"] == "domain_reload"
    assert error["toolRoutingStarted"] is False
    assert error["mutationStarted"] is False
    assert error["committed"] is False
    assert error["commitState"] == "not_started"
    assert error["commitStateKnown"] is True
    assert plan.consumed is False
    assert plan.uncertain_state is False


def test_reload_dialog_is_reported_before_core_routing_or_retry(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _descriptor(tmp_path)
    transport_attempts = 0

    class UnexpectedCoreClient:
        def __init__(self, *_args, **_kwargs) -> None:
            nonlocal transport_attempts
            transport_attempts += 1

    monkeypatch.setattr(agent, "UnityMcpCoreClient", UnexpectedCoreClient)
    monkeypatch.setattr(
        agent.time,
        "sleep",
        lambda _seconds: pytest.fail("Reload refusal must not enter a retry wait"),
    )
    monkeypatch.setattr(
        agent,
        "probe_unity_reload_dialog",
        lambda _project: {
            "schema": "vrcforge.unity_editor_window_blocker.v1",
            "blocked": True,
            "blockerCode": "unity_editor_reload_dialog",
            "dialog": {"title": "Unity", "reloadLabel": "reload"},
        },
    )

    with pytest.raises(agent.UnityMcpError) as raised:
        agent.invoke_unity_mcp(_settings(tmp_path, retries=3), "vrc_read", {})

    error = raised.value.external_error
    assert transport_attempts == 0
    assert raised.value.cause_code == "unity_editor_reload_dialog"
    assert raised.value.retryable is True
    assert error["failureLayer"] == "unity_core_pre_route"
    assert error["failurePhase"] == "domain_reload_confirmation"
    assert error["toolRoutingStarted"] is False
    assert error["mutationStarted"] is False
    assert error["committed"] is False
    assert error["commitState"] == "not_started"
    assert error["details"]["editorBlocker"]["dialog"]["reloadLabel"] == "reload"


def test_reload_probe_failure_uses_the_same_pre_route_no_mutation_contract(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _descriptor(tmp_path)
    monkeypatch.setattr(
        agent,
        "probe_unity_reload_dialog",
        lambda _project: {
            "schema": "vrcforge.unity_editor_window_blocker.v1",
            "blocked": False,
            "probeError": {
                "code": "unity_editor_window_probe_failed",
                "message": "bounded fixture failure",
            },
        },
    )
    monkeypatch.setattr(
        agent,
        "UnityMcpCoreClient",
        lambda *_args, **_kwargs: pytest.fail("Core must not be called after probe failure"),
    )

    with pytest.raises(agent.UnityMcpError) as raised:
        agent.invoke_unity_mcp(_settings(tmp_path, retries=3), "vrc_write", {})

    error = raised.value.external_error
    assert raised.value.cause_code == "unity_editor_window_probe_failed"
    assert error["failureLayer"] == "unity_core_pre_route"
    assert error["failurePhase"] == "editor_window_probe"
    assert error["toolRoutingStarted"] is False
    assert error["mutationStarted"] is False
    assert error["committed"] is False
    assert error["commitState"] == "not_started"


def test_reload_dialog_classifier_requires_dialog_or_reload_button() -> None:
    from unity_editor_window_probe import classify_reload_dialog

    assert classify_reload_dialog(
        [
            {
                "windowHandle": 12,
                "ownerWindow": 11,
                "title": "Unity",
                "className": "#32770",
                "visible": True,
                "enabled": True,
                "controls": [{"className": "Button", "text": "Reload"}],
            }
        ]
    )["reloadLabel"] == "reload"
    assert classify_reload_dialog(
        [{"title": "Reload Notes", "className": "UnityContainerWndClass", "controls": []}]
    ) is None
    assert classify_reload_dialog(
        [
            {
                "title": "Unity",
                "className": "#32770",
                "visible": False,
                "controls": [{"className": "Button", "text": "&Reload"}],
            }
        ]
    ) is None


def test_approved_write_contract_failure_is_terminal_but_still_uncertain(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _descriptor(tmp_path)
    attempts = 0

    class InvalidCoreClient:
        def __init__(self, _project: str, *, timeout_seconds: int) -> None:
            pass

        def call_tool(self, _name: str, _arguments: dict, *, execution_context=None) -> dict:
            nonlocal attempts
            attempts += 1
            raise UnityMcpCoreError("Unity MCP Core tool contract is invalid.")

    monkeypatch.setattr(agent, "UnityMcpCoreClient", InvalidCoreClient)
    plan = create_approved_unity_execution_plan(
        _context(tmp_path, issued=0, expires=9_999_999_999_999),
        [("vrc_write", {"value": 1})],
    )

    with bind_approved_unity_execution(plan):
        with pytest.raises(agent.UnityMcpError, match="single transport attempt") as raised:
            agent.invoke_unity_mcp(_settings(tmp_path), "vrc_write", {"value": 1})

    assert attempts == 1
    assert plan.uncertain_state is True
    assert raised.value.cause_code == "unity_core_contract_invalid"
    assert raised.value.retryable is False


def test_approved_write_preserves_a_bounded_safe_core_rejection_reason(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _descriptor(tmp_path)
    attempts = 0

    class RejectingCoreClient:
        def __init__(self, _project: str, *, timeout_seconds: int) -> None:
            pass

        def call_tool(self, _name: str, _arguments: dict, *, execution_context=None) -> dict:
            nonlocal attempts
            attempts += 1
            return {
                "isError": True,
                "structuredContent": {
                    "success": False,
                    "code": "unitypackage_import_failed",
                    "error": "api_key=must-not-appear C:\\Users\\private\\fixture.unitypackage",
                },
                "content": [{"type": "text", "text": "password=also-private"}],
            }

    monkeypatch.setattr(agent, "UnityMcpCoreClient", RejectingCoreClient)
    plan = create_approved_unity_execution_plan(
        _context(tmp_path, issued=0, expires=9_999_999_999_999),
        [("vrc_write", {"value": 1})],
    )
    with bind_approved_unity_execution(plan):
        with pytest.raises(agent.UnityMcpError, match="Reason code: unitypackage_import_failed") as error:
            agent.invoke_unity_mcp(
                _settings(tmp_path),
                "vrc_write",
                {"value": 1},
                preserve_tool_error=False,
            )

    assert "must-not-appear" not in str(error.value)
    assert "also-private" not in str(error.value)
    assert "C:\\Users" not in str(error.value)
    assert attempts == 1
    assert plan.consumed is True


def test_managed_write_can_preserve_structured_unity_handler_failure(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _descriptor(tmp_path)

    class RejectingCoreClient:
        def __init__(self, _project: str, *, timeout_seconds: int) -> None:
            pass

        def call_tool(self, _name: str, _arguments: dict, *, execution_context=None) -> dict:
            return {
                "isError": True,
                "structuredContent": {
                    "success": False,
                    "code": "Set property failed: exact handler reason.",
                    "error": "Set property failed: exact handler reason.",
                    "data": {
                        "mutationStarted": False,
                        "committed": False,
                        "commitState": "not_started",
                    },
                },
                "content": [{"type": "text", "text": "bounded failure"}],
            }

    monkeypatch.setattr(agent, "UnityMcpCoreClient", RejectingCoreClient)
    plan = create_approved_unity_execution_plan(
        _context(tmp_path, issued=0, expires=9_999_999_999_999),
        [("vrc_write", {"value": 1})],
    )

    with bind_approved_unity_execution(plan):
        result = agent.invoke_unity_mcp(
            _settings(tmp_path),
            "vrc_write",
            {"value": 1},
            preserve_tool_error=True,
        )

    assert result.exit_code == 1
    assert result.payload["structuredContent"]["data"]["mutationStarted"] is False
    assert plan.consumed is True


def test_core_rejection_summary_rejects_free_text_and_noncanonical_codes() -> None:
    assert agent.summarize_unity_mcp_core_rejection(
        {"isError": True, "content": [{"type": "text", "text": "password=private"}]}
    ) == ""
    assert agent.summarize_unity_mcp_core_rejection(
        {"isError": True, "structuredContent": {"code": "invalid code: C:\\Users\\private"}}
    ) == ""


def test_read_calls_keep_retry_behavior(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    _descriptor(tmp_path)
    attempts = 0

    class FlakyCoreClient:
        def __init__(self, _project: str, *, timeout_seconds: int) -> None:
            pass

        def call_tool(self, _name: str, _arguments: dict, *, execution_context=None) -> dict:
            nonlocal attempts
            attempts += 1
            if attempts == 1:
                raise UnityMcpCoreConnectionError("Unity MCP Core connection failed.")
            return {"isError": False, "content": []}

    monkeypatch.setattr(agent, "UnityMcpCoreClient", FlakyCoreClient)
    result = agent.invoke_unity_mcp(_settings(tmp_path, retries=2), "vrc_read", {})
    assert result.exit_code == 0
    assert attempts == 2


def test_unclassified_read_failure_is_terminal_and_never_retries(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _descriptor(tmp_path)
    attempts = 0
    sleep_calls: list[float] = []

    class BrokenCoreClient:
        def __init__(self, _project: str, *, timeout_seconds: int) -> None:
            pass

        def call_tool(self, _name: str, _arguments: dict, *, execution_context=None) -> dict:
            nonlocal attempts
            attempts += 1
            raise ValueError("implementation bug")

    monkeypatch.setattr(agent, "UnityMcpCoreClient", BrokenCoreClient)
    monkeypatch.setattr(agent.time, "sleep", sleep_calls.append)

    with pytest.raises(agent.UnityMcpError, match="could not prepare") as raised:
        agent.invoke_unity_mcp(_settings(tmp_path), "vrc_read", {})

    assert attempts == 1
    assert sleep_calls == []
    assert raised.value.cause_code == "unity_request_failed"
    assert raised.value.retryable is False


def test_bound_plan_does_not_consume_the_direct_read_lane(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _descriptor(tmp_path)
    observed: list[tuple[str, object]] = []

    class FakeCoreClient:
        def __init__(self, _project: str, *, timeout_seconds: int) -> None:
            pass

        def call_tool(self, name: str, _arguments: dict, *, execution_context=None) -> dict:
            observed.append((name, execution_context))
            return {"isError": False, "content": []}

    monkeypatch.setattr(agent, "UnityMcpCoreClient", FakeCoreClient)
    plan = create_approved_unity_execution_plan(
        _context(tmp_path, issued=0, expires=9_999_999_999_999),
        [("vrc_write", {"value": 1})],
    )
    with bind_approved_unity_execution(plan):
        agent.invoke_unity_mcp(_settings(tmp_path), "vrc_get_gameobject", {"gameObjectPath": "Avatar"})
        agent.invoke_unity_mcp(_settings(tmp_path), "vrc_write", {"value": 1})

    assert observed[0] == ("vrc_get_gameobject", None)
    assert observed[1][0] == "vrc_write"
    assert isinstance(observed[1][1], dict)
    assert plan.consumed is True
