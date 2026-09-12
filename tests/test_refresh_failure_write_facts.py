"""Refresh 520 receipt reproduction; no Unity, processes, or scene changes."""
from copy import deepcopy

import pytest

from dashboard_server import normalize_refresh_asset_database_outcome
from external_tool_result_contract import build_external_tool_error, external_write_failure_view
from agent_tool_result_contract import normalize_agent_tool_result


# Core UnityAsyncJobRegistry.PublicPayload shape, with 520's completed compile
# snapshot. 520 only retains an already-normalized result, not original polls.
# The registry supplies no commit/mutation receipt; don't replay old inferred facts.
REFRESH_520_RESULT = {
    "job_id": "a" * 32, "before": {}, "status": "done",
    "after": {"compile": {
        "isCompiling": False, "captureComplete": True, "errorCount": 0,
        "warningCount": 3, "truncated": False, "errors": [],
    }},
}


def _failure_projections(raw):
    refreshed = normalize_refresh_asset_database_outcome(raw)
    error = build_external_tool_error(
        error="Unity reported new compile errors or warnings after the write.",
        error_code="unity_console_regression",
        failure_layer="completion_verification_finalize",
        operation_kind="write", tool="vrcforge_refresh_asset_database",
        raw_result=refreshed,
    )
    view = external_write_failure_view(error)
    outcome = normalize_agent_tool_result(
        {"ok": False, "status": "failed", "result": refreshed,
         "errorDetails": error, "writeFailure": view},
        fallback_summary="Refresh verification failed.", write=True,
    )
    return refreshed, error, view, outcome


def test_core_refresh_shape_with_520_compile_has_consistent_unknown_write_facts():
    refreshed, *failed = _failure_projections(deepcopy(REFRESH_520_RESULT))
    assert refreshed["status"] == "done"
    for projection in [refreshed, *failed]:
        assert projection["mutationStarted"] is None
        assert projection["committed"] is None
        assert projection["commitState"] == "unknown"
        assert projection["commitStateKnown"] is False
    for projection in failed:
        assert projection["recovery"]["required"] is True
        assert projection["errorCode"] == "unity_console_regression"


@pytest.mark.parametrize("facts", [
    {"mutationStarted": None, "committed": None, "commitState": "unknown", "commitStateKnown": False},
    {"mutationStarted": True, "committed": False, "commitState": "partial", "commitStateKnown": True},
    {"mutationStarted": True, "committed": True, "commitState": "complete", "commitStateKnown": True},
])
def test_refresh_does_not_replace_explicit_unknown_or_mutated_facts(facts):
    raw = {**deepcopy(REFRESH_520_RESULT), **facts}
    for projection in _failure_projections(raw):
        for key, value in facts.items():
            assert projection[key] == value


@pytest.mark.parametrize("required_field", ["recoveryRequired", "checkpointRecoveryRequired", "temporaryCleanupRequired"])
def test_refresh_preserves_explicit_recovery_requirements(required_field):
    recovery = {"required": True, "reason": "Explicit recovery requirement."}
    raw = {**deepcopy(REFRESH_520_RESULT), required_field: True, "recovery": recovery}
    refreshed, *failed = _failure_projections(raw)
    assert refreshed[required_field] is True
    for projection in failed:
        assert projection["recovery"] == recovery


def test_generic_not_started_alone_does_not_assert_no_mutation_or_clear_recovery():
    raw = {"commitState": "not_started", "recovery": {"required": True, "reason": "Inspect target."}}
    error = build_external_tool_error(raw_result=raw, operation_kind="write")
    assert error["mutationStarted"] is None
    assert error["committed"] is None
    assert error["recovery"] == raw["recovery"]


def test_refresh_preserves_explicit_no_write_receipt():
    raw = {**deepcopy(REFRESH_520_RESULT), "mutationStarted": False, "committed": False,
           "commitState": "not_started", "commitStateKnown": True}
    refreshed, *failed = _failure_projections(raw)
    for projection in (refreshed, *failed):
        assert projection["mutationStarted"] is False
        assert projection["committed"] is False
        assert projection["commitState"] == "not_started"
    for projection in failed:
        assert projection["recovery"]["required"] is False


@pytest.mark.parametrize("state,known", [("partial", True), ("complete", True), ("unknown", False), ("not_started", False)])
def test_refresh_commit_evidence_without_mutation_booleans_is_not_overwritten(state, known):
    raw = {**deepcopy(REFRESH_520_RESULT), "commitState": state, "commitStateKnown": known}
    normalized = normalize_refresh_asset_database_outcome(raw)
    assert normalized["commitState"] == state
    assert normalized["commitStateKnown"] is known
    assert normalized["mutationStarted"] is None
    assert normalized["committed"] is None


@pytest.mark.parametrize("facts", [
    {"commitState": "unknown", "mutationStarted": False, "committed": False},
    {"commitState": "partial", "mutationStarted": False, "committed": False},
    {"mutationStarted": None},
    {"committed": True},
])
def test_refresh_conflicting_or_unknown_facts_do_not_receive_no_write_defaults(facts):
    raw = {"status": "running", **facts}
    normalized = normalize_refresh_asset_database_outcome(raw)
    for key, value in facts.items():
        assert normalized[key] == value
    for key in ("mutationStarted", "committed"):
        if key not in facts:
            assert normalized[key] is None


def test_refresh_missing_commit_facts_remain_unknown_even_with_normal_status():
    normalized = normalize_refresh_asset_database_outcome({"status": "running"})
    assert normalized["mutationStarted"] is None
    assert normalized["committed"] is None
    assert normalized["commitState"] == "unknown"
    assert normalized["commitStateKnown"] is False
