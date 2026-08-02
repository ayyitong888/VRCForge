from types import SimpleNamespace

import dashboard_server


def test_setup_outfit_wait_uses_the_narrow_app_poll_lane(monkeypatch) -> None:
    job_id = "a" * 32
    calls = []

    def invoke(_settings, tool_name, arguments, *, execution_context=None):
        calls.append((tool_name, arguments, execution_context))
        return dashboard_server.McpResult(
            exit_code=0,
            stdout="",
            stderr="",
            payload={
                "data": {
                    "ok": True,
                    "pending": False,
                    "status": "completed",
                    "jobId": job_id,
                    "outfitGlobalObjectId": "GlobalObjectId_V1-2-complete",
                    "committed": True,
                    "commitState": "complete",
                    "checkpointRecoveryRequired": False,
                }
            },
        )

    monkeypatch.setattr(dashboard_server, "invoke_unity_mcp", invoke)
    result = dashboard_server.wait_for_setup_outfit_job(
        SimpleNamespace(unity_mcp_timeout_seconds=5),
        {
            "setupOutfitPollIntervalSeconds": 0,
            "setupOutfitPollTimeoutSeconds": 1,
        },
        {"ok": True, "pending": True, "status": "pending", "jobId": job_id},
    )

    assert result["status"] == "completed"
    assert calls == [
        (
            "vrc_setup_outfit",
            {"jobId": job_id},
            {"lane": "app_setup_outfit_poll"},
        )
    ]


def test_setup_outfit_unavailable_is_terminal_failure() -> None:
    payload = {"status": "unavailable", "pending": False, "jobId": "a" * 32}
    assert dashboard_server.normalize_setup_outfit_terminal_payload(payload)["ok"] is False


def test_setup_outfit_unavailable_after_continuation_is_recovery_required() -> None:
    payload = {
        "status": "unavailable",
        "pending": False,
        "jobId": "a" * 32,
        "continuationConsumed": True,
        "mutationStarted": False,
    }

    result = dashboard_server.normalize_setup_outfit_terminal_payload(payload)

    assert result["ok"] is False
    assert result["committed"] is True
    assert result["commitState"] == "unknown"
    assert result["checkpointRecoveryRequired"] is True


def test_setup_outfit_wait_rejects_incomplete_completed_receipt(monkeypatch) -> None:
    job_id = "d" * 32
    responses = iter(
        [
            {
                "ok": True,
                "pending": True,
                "status": "running",
                "jobId": job_id,
                "outfitGlobalObjectId": "GlobalObjectId_V1-2-running",
                "mutationStarted": True,
            },
            {
                "ok": True,
                "pending": False,
                "status": "completed",
                "jobId": job_id,
            },
        ]
    )

    monkeypatch.setattr(
        dashboard_server,
        "invoke_unity_mcp",
        lambda *_args, **_kwargs: dashboard_server.McpResult(
            exit_code=0,
            stdout="",
            stderr="",
            payload={"data": next(responses)},
        ),
    )
    result = dashboard_server.wait_for_setup_outfit_job(
        SimpleNamespace(unity_mcp_timeout_seconds=5),
        {
            "setupOutfitPollIntervalSeconds": 0,
            "setupOutfitPollTimeoutSeconds": 1,
        },
        {"ok": True, "pending": True, "status": "pending", "jobId": job_id},
    )

    assert result["ok"] is False
    assert result["status"] == "error"
    assert result["committed"] is True
    assert result["commitState"] == "unknown"
    assert result["checkpointRecoveryRequired"] is True


def test_setup_outfit_timeout_preserves_running_mutation_authority() -> None:
    job_id = "b" * 32
    last_payload = {
        "status": "running",
        "jobId": job_id,
        "avatarPath": "Avatar",
        "outfitPath": "Avatar/Outfit",
        "outfitGlobalObjectId": "GlobalObjectId_V1-2-test",
        "continuationConsumed": True,
        "mutationStarted": True,
    }

    result = dashboard_server.setup_outfit_timeout_payload(job_id, last_payload, "poll failed")

    assert result["ok"] is False
    assert result["status"] == "timeout"
    assert result["outfitGlobalObjectId"] == "GlobalObjectId_V1-2-test"
    assert result["committed"] is True
    assert result["commitState"] == "unknown"
    assert result["checkpointRecoveryRequired"] is True


def test_setup_outfit_wait_preserves_running_authority_across_bare_unavailable(monkeypatch) -> None:
    job_id = "c" * 32
    responses = iter(
        [
            {
                "ok": True,
                "pending": True,
                "status": "running",
                "jobId": job_id,
                "outfitGlobalObjectId": "GlobalObjectId_V1-2-running",
                "continuationConsumed": True,
                "mutationStarted": True,
            },
            {
                "ok": False,
                "pending": False,
                "status": "unavailable",
                "jobId": job_id,
                "reason": "job_not_found_or_editor_reloaded",
            },
        ]
    )

    def invoke(_settings, _tool_name, _arguments, *, execution_context=None):
        del execution_context
        return dashboard_server.McpResult(
            exit_code=0,
            stdout="",
            stderr="",
            payload={"data": next(responses)},
        )

    monkeypatch.setattr(dashboard_server, "invoke_unity_mcp", invoke)
    result = dashboard_server.wait_for_setup_outfit_job(
        SimpleNamespace(unity_mcp_timeout_seconds=5),
        {
            "setupOutfitPollIntervalSeconds": 0,
            "setupOutfitPollTimeoutSeconds": 1,
        },
        {"ok": True, "pending": True, "status": "pending", "jobId": job_id},
    )

    assert result["status"] == "unavailable"
    assert result["outfitGlobalObjectId"] == "GlobalObjectId_V1-2-running"
    assert result["committed"] is True
    assert result["commitState"] == "unknown"
    assert result["checkpointRecoveryRequired"] is True
