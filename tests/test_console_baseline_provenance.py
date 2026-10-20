"""Unknown compile history cannot identify newly introduced diagnostics."""
import copy
import pytest
from agent_completion_verifier import AgentCompletionVerificationError, UnityConsoleCompletionVerifier
from test_agent_completion_verifier import _payload

PROFILE = "unity_asset_write_console"
SCENE_PROFILE = "persisted_scene_write_console"
REFRESH_PROFILE = "unity_refresh_console"
WARNING = {"assembly": "Editor", "file": "Assets/ThirdParty/Existing.cs", "line": 7, "message": "warning CS0618: existing obsolete API"}


def snapshot(
    source="compilation_pipeline",
    captured_at="2026-09-08T06:15:48Z",
    warnings=(),
    errors=(),
    truncated=False,
    project_digest="a" * 64,
    process_id=4242,
    process_started_at="2026-08-10T00:00:00Z",
    executable_digest="b" * 64,
):
    result = _payload(
        warnings=warnings,
        errors=errors,
        project_digest=project_digest,
        process_id=process_id,
        process_started_at=process_started_at,
        executable_digest=executable_digest,
    )
    result["result"]["data"].update(source=source, capturedAt=captured_at)
    result["result"]["data"]["truncated"] = truncated
    return result


def verify(before, after, *, profile=PROFILE, baseline_patch=None):
    reads = (
        [before, before, after, after]
        if profile == SCENE_PROFILE and before["result"]["data"].get("source") == "console_log"
        else [before, after, after]
    )
    return verify_reads(reads, profile=profile, baseline_patch=baseline_patch)


def verify_reads(reads, *, profile=PROFILE, baseline_patch=None):
    verifier = UnityConsoleCompletionVerifier(lambda _: reads.pop(0), timeout_seconds=2, sleep=lambda _: None)
    baseline = verifier.capture_baseline(profile, {})
    baseline.update(baseline_patch or {})
    result = verifier.finalize(profile, {}, baseline, {"ok": True, "status": "done", "completionKnown": True})
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


def test_scene_console_baseline_and_after_compare_as_console_evidence():
    _, result = verify(
        snapshot("console_log", ""),
        snapshot("console_log", ""),
        profile=SCENE_PROFILE,
    )
    check = result["consoleVerification"]
    assert result["consoleVerified"] is True
    assert check["evidenceKind"] == "console_snapshot"
    assert check["comparisonAvailable"] is True
    assert check["newErrorCount"] == 0 and check["newWarningCount"] == 0
    assert "not a full compilation" in check["summary"]


@pytest.mark.parametrize(
    ("errors", "warnings", "expected_count"),
    [([], [WARNING], 1), ([{"message": "error CS0000"}], [], 1)],
)
def test_scene_console_baseline_detects_new_console_diagnostics(errors, warnings, expected_count):
    _, result = verify(
        snapshot("console_log", ""),
        snapshot("console_log", "", errors=errors, warnings=warnings),
        profile=SCENE_PROFILE,
    )
    check = result["consoleVerification"]
    assert result["consoleVerified"] is False
    assert check["evidenceKind"] == "console_snapshot"
    assert check["comparisonAvailable"] is True
    assert check["newErrorCount"] + check["newWarningCount"] == expected_count


@pytest.mark.parametrize("profile", [PROFILE, REFRESH_PROFILE])
def test_non_scene_profiles_reject_console_only_evidence(profile):
    _, result = verify(
        snapshot("console_log", ""),
        snapshot("console_log", ""),
        profile=profile,
    )
    check = result["consoleVerification"]
    assert result["consoleVerified"] is False
    assert check["code"] == "unity_console_baseline_unavailable"
    assert check["comparisonAvailable"] is False
    assert check["evidenceKind"] == "console_snapshot_unavailable"


def test_scene_console_requires_two_stable_reads():
    _, result = verify_reads(
        [
            snapshot("console_log", ""),
            snapshot("console_log", ""),
            snapshot("console_log", "", warnings=[WARNING]),
            snapshot("console_log", ""),
        ],
        profile=SCENE_PROFILE,
    )
    assert result["consoleVerified"] is False
    assert result["consoleVerification"]["code"] == "unity_console_unstable"


def test_scene_console_rejects_unstable_baseline():
    with pytest.raises(AgentCompletionVerificationError, match="not stable"):
        verify_reads(
            [
                snapshot("console_log", ""),
                snapshot("console_log", "", warnings=[WARNING]),
                snapshot("console_log", ""),
                snapshot("console_log", ""),
            ],
            profile=SCENE_PROFILE,
        )


def test_scene_console_rejects_truncated_after_snapshot():
    _, result = verify(
        snapshot("console_log", ""),
        snapshot("console_log", "", truncated=True),
        profile=SCENE_PROFILE,
    )
    assert result["consoleVerified"] is False
    assert result["consoleVerification"]["code"] == "unity_console_unstable"


def test_scene_console_rejects_changed_process_identity():
    _, result = verify(
        snapshot("console_log", ""),
        snapshot("console_log", "", process_id=5252),
        profile=SCENE_PROFILE,
    )
    assert result["consoleVerified"] is False
    assert result["consoleVerification"]["code"] == "unity_process_changed"


def test_scene_console_mixed_source_uses_pipeline_current_state_not_console_delta():
    _, result = verify(
        snapshot("console_log", ""),
        snapshot("compilation_pipeline", "2026-09-08T06:15:48Z"),
        profile=SCENE_PROFILE,
    )
    check = result["consoleVerification"]
    assert result["consoleVerified"] is True
    assert check["evidenceKind"] == "compilation_pipeline"
    assert check["comparisonAvailable"] is False
    assert check["newErrorCount"] is None and check["newWarningCount"] is None


def test_scene_pipeline_baseline_console_after_is_rejected():
    _, result = verify(
        snapshot(),
        snapshot("console_log", ""),
        profile=SCENE_PROFILE,
    )
    check = result["consoleVerification"]
    assert result["consoleVerified"] is False
    assert check["code"] == "unity_console_baseline_unavailable"
    assert check["evidenceKind"] == "console_snapshot_unavailable"


def test_console_only_after_does_not_prove_pipeline_clean():
    _, result = verify(snapshot(), snapshot("console_log", ""))
    assert result["consoleVerified"] is False
    assert result["consoleVerification"]["comparisonAvailable"] is False


def test_lost_baseline_provenance_is_not_a_zero_baseline():
    _, result = verify(snapshot(), snapshot(warnings=[WARNING]), baseline_patch={"source": "", "capturedAt": ""})
    assert result["consoleVerification"]["code"] == "unity_console_baseline_unavailable"
