from __future__ import annotations

import pytest

import dashboard_server
from agent_tool_result_contract import normalize_agent_tool_result


def readiness_payload(
    *,
    ready: bool,
    blockers: list[str],
    builder_available: bool = True,
) -> dict[str, object]:
    return {
        "ok": True,
        "schema": "vrcforge.vrchat_avatar_upload_readiness.v1",
        "ready": ready,
        "sdkUserId": "" if blockers else "usr_owner",
        "canPublishAvatars": not blockers,
        "platform": "StandaloneWindows64",
        "requestedPlatforms": ["StandaloneWindows64"],
        "builderAvailable": builder_available,
        "builderBuildState": "Idle" if builder_available else "",
        "builderUploadState": "Idle" if builder_available else "",
        "blockingReasons": blockers,
        "readinessDigest": "a" * 64,
        "mutationStarted": False,
        "committed": False,
        "commitState": "not_started",
    }


def test_blocked_readiness_gives_agents_exact_causes_and_recovery() -> None:
    blockers = [
        "Unity Play Mode must be stopped before upload.",
        "The VRChat SDK has no authenticated current user.",
    ]
    raw = dashboard_server._with_avatar_upload_readiness_cause(
        readiness_payload(ready=False, blockers=blockers),
        request={"platforms": ["StandaloneWindows64"]},
    )
    outcome = normalize_agent_tool_result(
        raw,
        fallback_summary="Avatar upload readiness inspected.",
        write=False,
    )

    assert outcome["success"] is True
    assert outcome["status"] == "ok"
    assert outcome["ready"] is False
    assert outcome["blockingReasons"] == blockers
    assert outcome["failureLayer"] == "vrchat_sdk_upload_readiness"
    assert outcome["failureCause"]["reasons"] == blockers
    assert outcome["rootCause"]["blockingReasons"] == blockers
    assert [item["cause"] for item in outcome["causeChain"]] == blockers
    assert outcome["observed"]["playModeStopped"] is False
    assert outcome["observed"]["sdkUserAuthenticated"] is False
    assert outcome["expected"]["ready"] is True
    assert outcome["expected"]["blockingReasons"] == []
    assert outcome["delta"]["blockingReasonCount"] == 2
    assert outcome["recovery"]["required"] is False
    assert outcome["evidence"][0]["sha256"] == "a" * 64
    assert outcome["nextAction"] == [
        "Stop Unity Play Mode, then rerun upload readiness.",
        "Authenticate the intended avatar owner in the VRChat SDK, then rerun upload readiness.",
    ]


def test_ready_result_keeps_success_facts_without_inventing_failure() -> None:
    raw = dashboard_server._with_avatar_upload_readiness_cause(
        readiness_payload(ready=True, blockers=[]),
        request={"platforms": ["StandaloneWindows64"]},
    )
    outcome = normalize_agent_tool_result(
        raw,
        fallback_summary="Avatar upload readiness inspected.",
        write=False,
    )

    assert outcome["success"] is True
    assert outcome["status"] == "ok"
    assert outcome["ready"] is True
    assert "blockingReasons" not in outcome
    assert "failureCause" not in outcome
    assert "rootCause" not in outcome
    assert outcome["observed"]["ready"] is True
    assert outcome["delta"]["blockingReasonCount"] == 0
    assert "Bind this exact readinessDigest" in outcome["nextAction"]


def test_platform_mismatch_preserves_original_request_and_exact_delta() -> None:
    blocker = "The requested platform must equal Unity's current active build target."
    raw = dashboard_server._with_avatar_upload_readiness_cause(
        readiness_payload(ready=False, blockers=[blocker]),
        request={"platforms": ["Android"]},
    )

    assert raw["observed"]["activePlatform"] == "StandaloneWindows64"
    assert raw["observed"]["requestedPlatforms"] == ["Android"]
    assert raw["observed"]["platformRequestMatchesActiveTarget"] is False
    assert raw["expected"]["requestedPlatforms"] == ["StandaloneWindows64"]
    assert raw["expected"]["platformRequestMatchesActiveTarget"] is True
    assert raw["delta"]["platformRequestMismatch"] is True
    assert raw["delta"]["actualRequestedPlatforms"] == ["Android"]
    assert raw["delta"]["expectedRequestedPlatforms"] == ["StandaloneWindows64"]
    assert raw["nextAction"] == [
        "Set platforms to the active Unity build target (StandaloneWindows64), then rerun upload readiness."
    ]


def test_builder_can_be_unavailable_without_contradicting_ready_result() -> None:
    raw = dashboard_server._with_avatar_upload_readiness_cause(
        readiness_payload(ready=True, blockers=[], builder_available=False),
        request={"platforms": ["StandaloneWindows64"]},
    )
    outcome = normalize_agent_tool_result(
        raw,
        fallback_summary="Avatar upload readiness inspected.",
        write=False,
    )

    assert outcome["success"] is True
    assert outcome["status"] == "ok"
    assert outcome["ready"] is True
    assert outcome["observed"]["builderAvailable"] is False
    assert outcome["observed"]["builderBuildState"] == ""
    assert "builderAvailable" not in outcome["expected"]
    assert "builderBuildState" not in outcome["expected"]
    assert "builderUploadState" not in outcome["expected"]
    assert outcome["delta"]["blockingReasonCount"] == 0


def test_sync_uses_original_request_platforms_instead_of_core_projection(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    blocker = "The requested platform must equal Unity's current active build target."
    core_payload = readiness_payload(ready=False, blockers=[blocker])
    assert core_payload["requestedPlatforms"] == ["StandaloneWindows64"]

    monkeypatch.setattr(dashboard_server, "build_agent_connection_request", lambda params: {})
    monkeypatch.setattr(dashboard_server, "load_dashboard_settings", lambda request: object())
    monkeypatch.setattr(
        dashboard_server,
        "invoke_unity_mcp",
        lambda settings, tool_name, request: core_payload,
    )
    monkeypatch.setattr(dashboard_server, "extract_tool_result_payload", lambda payload: payload)

    result = dashboard_server.avatar_upload_readiness_sync(
        {"platforms": ["Android"]}
    )

    assert result["observed"]["requestedPlatforms"] == ["Android"]
    assert result["observed"]["activePlatform"] == "StandaloneWindows64"
    assert result["delta"]["actualRequestedPlatforms"] == ["Android"]
    assert result["delta"]["expectedRequestedPlatforms"] == ["StandaloneWindows64"]
