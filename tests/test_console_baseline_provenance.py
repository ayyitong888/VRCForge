"""Unknown compile history cannot identify newly introduced diagnostics."""
import copy
import pytest
from agent_completion_verifier import UnityConsoleCompletionVerifier
from test_agent_completion_verifier import _payload

PROFILE = "unity_asset_write_console"
WARNING = {"assembly": "Editor", "file": "Assets/ThirdParty/Existing.cs", "line": 7, "message": "warning CS0618: existing obsolete API"}


def snapshot(source="compilation_pipeline", captured_at="2026-09-08T06:15:48Z", warnings=(), errors=()):
    result = _payload(warnings=warnings, errors=errors)
    result["result"]["data"].update(source=source, capturedAt=captured_at)
    return result


def verify(before, after, *, baseline_patch=None):
    reads = [before, after, after]
    verifier = UnityConsoleCompletionVerifier(lambda _: reads.pop(0), timeout_seconds=2, sleep=lambda _: None)
    baseline = verifier.capture_baseline(PROFILE, {})
    baseline.update(baseline_patch or {})
    result = verifier.finalize(PROFILE, {}, baseline, {"ok": True, "status": "done", "completionKnown": True})
    return baseline, result


@pytest.mark.parametrize("before", [snapshot("console_log", ""), snapshot("compilation_pipeline", ""), snapshot("unavailable", "")])
def test_unknown_baseline_does_not_label_observed_warnings_new(before):
    baseline, result = verify(before, snapshot(warnings=[WARNING]))
    check = result["consoleVerification"]
    assert result["status"] == "done" and result["completionKnown"] is True
    assert result["consoleVerified"] is False
    assert check["code"] == "unity_console_baseline_unavailable"
    assert check["newWarningCount"] is None and check["newErrorCount"] is None
    assert check["newWarnings"] == []
    assert check["observedWarningCount"] == 1
    assert check["observedWarnings"][0]["message"] == WARNING["message"]
    assert check["comparisonAvailable"] is False
    assert baseline["source"] == before["result"]["data"]["source"]


def test_real_new_warning_from_complete_pipeline_baseline_still_fails():
    _, result = verify(snapshot(), snapshot(warnings=[WARNING]))
    assert result["consoleVerified"] is False
    assert result["consoleVerification"]["code"] == "unity_console_regression"
    assert result["consoleVerification"]["newWarningCount"] == 1


def test_known_existing_warning_still_passes():
    _, result = verify(snapshot(warnings=[WARNING]), snapshot(warnings=[WARNING]))
    assert result["consoleVerified"] is True


def test_unknown_baseline_with_current_errors_is_not_approved():
    _, result = verify(snapshot("console_log", ""), snapshot(errors=[{"message": "error CS0000"}]))
    assert result["consoleVerified"] is False
    assert result["consoleVerification"]["observedErrorCount"] == 1
    assert result["consoleVerification"]["newErrorCount"] is None


def test_unknown_baseline_clean_complete_after_is_current_clean_evidence():
    _, result = verify(snapshot("console_log", ""), snapshot())
    assert result["consoleVerified"] is True
    assert result["consoleVerification"]["comparisonAvailable"] is False
    assert result["consoleVerification"]["newWarningCount"] is None
    assert "no new" not in result["consoleVerification"]["summary"].lower()


def test_console_only_after_does_not_prove_pipeline_clean():
    _, result = verify(snapshot(), snapshot("console_log", ""))
    assert result["consoleVerified"] is False
    assert result["consoleVerification"]["comparisonAvailable"] is False


def test_lost_baseline_provenance_is_not_a_zero_baseline():
    _, result = verify(snapshot(), snapshot(warnings=[WARNING]), baseline_patch={"source": "", "capturedAt": ""})
    assert result["consoleVerification"]["code"] == "unity_console_baseline_unavailable"
