import json
from types import SimpleNamespace

import pytest

from provider_request_metrics import (
    RequestMetrics,
    classify_request_phases,
)
from provider_request_ledger import ProviderRequestLedger, ledger_scope
from responses_native_protocol import stream_response


def _response_client(response, calls=None):
    calls = calls if calls is not None else []

    def create(**payload):
        calls.append(payload)
        return iter([{"type": "response.completed", "response": response}])

    return SimpleNamespace(
        responses=SimpleNamespace(create=create),
        close=lambda: None,
    )


def test_single_physical_request_persists_authoritative_id_and_task_binding(tmp_path):
    path = tmp_path / "audit" / "provider-requests.jsonl"
    ledger = ProviderRequestLedger(path)
    callbacks = []
    response = {
        "id": "resp_123",
        "status": "completed",
        "output": [{"type": "function_call", "id": "fc_1", "call_id": "call_1", "name": "preview_avatar"}],
        "usage": {
            "input_tokens": 0,
            "output_tokens": 0,
            "total_tokens": 0,
            "output_tokens_details": {"reasoning_tokens": 0},
            "input_tokens_details": {"cached_tokens": 0},
        },
    }
    with ledger_scope(ledger, {
        "sessionId": "session-1", "turnId": "turn-1", "clientTurnId": "task-1",
        "requestOrdinal": 1, "phase": "预览",
        "approvalTaskContext": {
            "firstApprovalRequestOrdinal": 1,
            "firstApprovalRequestIds": ["local-before-http", "resp_123"],
        },
    }, 1):
        stream_response(
            _response_client(response),
            {"model": "fixture", "input": [], "tools": []},
            None, None, callbacks.append,
            error_factory=lambda message, _response: RuntimeError(message),
            request_observer=callbacks.append,
        )
    rows = ledger.read_rows()
    assert len(rows) == 1
    row = rows[0]
    assert row["requestId"] == "resp_123"
    assert row["localDispatchId"] != row["requestId"]
    assert row["sessionId"] == "session-1"
    assert row["turnId"] == "turn-1"
    assert row["clientTurnId"] == "task-1"
    assert row["requestOrdinal"] == 1
    assert row["firstApprovalRequestOrdinal"] == 1
    assert row["firstApprovalRequestIds"] == ["local-before-http", "resp_123"]
    assert row["primaryPhase"] == "预览"
    assert row["toolCalls"] == [{"id": "call_1", "name": "preview_avatar"}]
    assert row["tokenUsage"] == {
        "inputTokens": 0, "outputTokens": 0, "totalTokens": 0,
        "reasoningTokens": 0, "cacheReadTokens": 0, "cacheWriteTokens": None,
    }
    assert len([item for item in callbacks if item["requestPhase"] == "terminal"]) == 1


def test_multitool_phase_array_uses_name_conventions_without_prompt_guessing():
    phases, primary = classify_request_phases(
        [
            {"id": "1", "name": "discover_tool_catalog"},
            {"id": "2", "name": "request_approval"},
            {"id": "3", "name": "restore_checkpoint"},
        ],
        {"sessionId": "s", "turnId": "t", "clientTurnId": "task"},
    )
    assert phases == ["找工具", "审批", "恢复"]
    assert primary == "找工具"


def test_phase_classifier_uses_action_metadata_without_tool_name_tables():
    phases, primary = classify_request_phases([
        {"id": "write", "name": "vrcforge_manage_fx_animator", "permission": "Write"},
        {"id": "scan", "name": "vrcforge_scan_fx_animator", "effect": "read"},
        {"id": "restore", "name": "vrcforge_restore_checkpoint", "actionKind": "restore"},
    ])
    assert phases == ["审批", "预览", "恢复"]
    assert primary == "审批"

    phases, primary = classify_request_phases([
        {"id": "preview-restore", "name": "preview_restore_shader", "action": "preview"},
    ])
    assert phases == ["预览", "恢复"]
    assert primary == "预览"


def test_runtime_action_extracts_only_tool_name_and_phase_from_arguments(tmp_path):
    ledger = ProviderRequestLedger(tmp_path / "provider.jsonl")
    callbacks = []
    response = {
        "id": "resp_action",
        "status": "completed",
        "output": [{
            "type": "function_call", "id": "fc", "call_id": "call",
            "name": "vrcforge_runtime_action",
            "arguments": '{"action":"write","write_tool":"apply_avatar_change","write_params":{"secret":"do-not-log"}}',
        }],
    }
    with ledger_scope(ledger):
        stream_response(
            _response_client(response), {"model": "fixture", "input": []}, None, None, None,
            error_factory=lambda message, _response: RuntimeError(message),
            request_observer=callbacks.append,
        )
    row = ledger.read_rows()[0]
    assert row["toolCalls"] == [{"id": "call", "name": "apply_avatar_change", "action": "write"}]
    assert row["primaryPhase"] == "审批"
    assert "do-not-log" not in json.dumps(row)


def test_failed_physical_request_is_durable_with_unknown_usage_and_intended_phase(tmp_path):
    ledger = ProviderRequestLedger(tmp_path / "provider.jsonl")
    callbacks = []
    calls = []

    def create(**payload):
        calls.append(payload)
        raise OSError("network fixture")

    client = _response_client({}, calls)
    client.responses.create = create
    with pytest.raises(OSError):
        with ledger_scope(ledger, {"sessionId": "s", "turnId": "t", "clientTurnId": "task", "phase": "审批"}):
            stream_response(
                client, {"model": "fixture", "input": []}, None, None, callbacks.append,
                error_factory=lambda message, _response: RuntimeError(message),
                request_observer=callbacks.append,
            )
    row = ledger.read_rows()[0]
    assert row["status"] == "failed"
    assert row["primaryPhase"] == "审批"
    assert row["usagePresent"] is False
    assert all(value is None for value in row["tokenUsage"].values())
    assert len(calls) == 1


def test_ledger_duplicate_event_id_is_idempotent(tmp_path):
    ledger = ProviderRequestLedger(tmp_path / "provider.jsonl")
    row = {"rowId": "prq_1", "eventId": "prq_1:start", "status": "started"}
    assert ledger.append(row) is True
    assert ledger.append(row) is False
    assert ledger.append({"rowId": "prq_1", "eventId": "prq_1:terminal", "status": "failed"}) is True
    assert ledger.append({"rowId": "prq_1", "eventId": "prq_1:terminal", "status": "failed"}) is False
    assert ledger.read_rows() == [{"rowId": "prq_1", "eventId": "prq_1:terminal", "status": "failed", "schema": "vrcforge.provider_request.v1", "createdAt": ledger.read_rows()[0]["createdAt"]}]
    assert len(path_lines := (tmp_path / "provider.jsonl").read_text(encoding="utf-8").splitlines()) == 2
    assert all("prompt" not in line.lower() for line in path_lines)


def test_request_metrics_keeps_reasoning_and_cache_unknown_separate_from_zero(tmp_path):
    ledger = ProviderRequestLedger(tmp_path / "provider.jsonl")
    with ledger_scope(ledger, {"taskId": "task", "phase": "找工具"}):
        metrics = RequestMetrics(lambda _event: None, {"model": "fixture", "input": []})
        metrics.finish(
            {"input_tokens": 7, "output_tokens": 0, "total_tokens": 7,
             "input_tokens_details": {"cached_tokens": 0}},
            success=True,
            tool_calls=[{"id": "call", "name": "load_tool_block"}],
        )
    row = ledger.read_rows()[0]
    assert row["tokenUsage"]["inputTokens"] == 7
    assert row["tokenUsage"]["outputTokens"] == 0
    assert row["tokenUsage"]["cacheReadTokens"] == 0
    assert row["tokenUsage"]["reasoningTokens"] is None
    assert row["tokenUsage"]["cacheWriteTokens"] is None
    assert "task" in json.dumps(row, ensure_ascii=False)

    ledger2 = ProviderRequestLedger(tmp_path / "provider-with-cache-write.jsonl")
    with ledger_scope(ledger2):
        metrics2 = RequestMetrics(lambda _event: None, {"model": "fixture", "input": []})
        metrics2.finish({"prompt_tokens_details": {"cache_write_tokens": 0}}, success=True)
    assert ledger2.read_rows()[0]["tokenUsage"]["cacheWriteTokens"] == 0


def test_native_composition_context_is_content_free(tmp_path):
    ledger = ProviderRequestLedger(tmp_path / "provider.jsonl")
    with ledger_scope(ledger, {
        "nativeComposition": {
            "messages": {"chars": 12, "sha256": "a" * 64, "secret": "raw-prompt"},
            "prompt": "must-not-persist",
        }
    }):
        metrics = RequestMetrics(lambda _event: None, {"model": "fixture", "input": []})
        metrics.finish({}, success=False, error="network")
    encoded = json.dumps(ledger.read_rows(), ensure_ascii=False)
    assert "raw-prompt" not in encoded
    assert "must-not-persist" not in encoded
    assert "a" * 64 in encoded
