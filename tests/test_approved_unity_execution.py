from __future__ import annotations

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


def test_frozen_plan_is_digest_bound_but_not_a_runtime_capability(tmp_path: Path) -> None:
    frozen = freeze_approved_unity_execution_plan([("vrc_write", {"value": "汉字"})])
    validated = validate_frozen_approved_unity_execution_plan(frozen)

    assert validated.calls[0].tool_name == "vrc_write"
    assert validated.plan_digest == frozen["planDigest"]
    tampered = dict(frozen)
    tampered["calls"] = [{"toolName": "vrc_other", "argumentsSha256": frozen["calls"][0]["argumentsSha256"]}]
    with pytest.raises(ValueError, match="digest"):
        validate_frozen_approved_unity_execution_plan(tampered)


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
            raise OSError("connection dropped")

    monkeypatch.setattr(agent, "UnityMcpCoreClient", FailingCoreClient)
    plan = create_approved_unity_execution_plan(_context(tmp_path, issued=0, expires=9_999_999_999_999), [("vrc_write", {"value": 1})])
    with bind_approved_unity_execution(plan):
        with pytest.raises(agent.UnityMcpError, match="single transport attempt"):
            agent.invoke_unity_mcp(_settings(tmp_path), "vrc_write", {"value": 1})
        with pytest.raises(agent.UnityMcpError, match="uncertain"):
            agent.invoke_unity_mcp(_settings(tmp_path), "vrc_write", {"value": 1})
    assert attempts == 1
    assert plan.uncertain_state is True


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
                raise OSError("temporary")
            return {"isError": False, "content": []}

    monkeypatch.setattr(agent, "UnityMcpCoreClient", FlakyCoreClient)
    result = agent.invoke_unity_mcp(_settings(tmp_path, retries=2), "vrc_read", {})
    assert result.exit_code == 0
    assert attempts == 2


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
