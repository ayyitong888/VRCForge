from __future__ import annotations

from pathlib import Path

import pytest

from agent_completion_verifier import (
    AgentCompletionVerificationError,
    UnityConsoleCompletionVerifier,
)


def _payload(
    *,
    compiling: bool = False,
    complete: bool = True,
    errors=None,
    warnings=None,
    project_digest: str = "a" * 64,
    process_id: int = 4242,
    process_started_at: str = "2026-08-10T00:00:00Z",
    executable_digest: str = "b" * 64,
):
    return {
        "ok": True,
        "result": {
            "data": {
                "ok": True,
                "isCompiling": compiling,
                "captureComplete": complete,
                "truncated": False,
                "capturedAt": "2026-08-10T00:00:00Z",
                "projectPathDigest": project_digest,
                "unityProcessId": process_id,
                "unityProcessStartedAtUtc": process_started_at,
                "unityExecutableDigest": executable_digest,
                "errors": list(errors or []),
                "warnings": list(warnings or []),
            }
        },
    }


def test_console_verifier_accepts_stable_no_delta() -> None:
    reads = [_payload(), _payload(), _payload()]
    verifier = UnityConsoleCompletionVerifier(
        lambda _params: reads.pop(0),
        timeout_seconds=2,
        poll_seconds=0.01,
        sleep=lambda _seconds: None,
    )
    baseline = verifier.capture_baseline("persisted_scene_write_console", {"projectPath": "A"})
    result = verifier.finalize(
        "persisted_scene_write_console",
        {"projectPath": "A"},
        baseline,
        {"ok": True, "persistedReadback": True, "sceneSaved": True},
    )
    assert result["consoleVerified"] is True
    assert result["consoleVerification"]["status"] == "passed"


def test_console_verifier_passes_only_the_remaining_deadline_to_each_read() -> None:
    reads = [_payload(), _payload(), _payload()]
    observed_timeouts: list[int] = []

    def read(params):
        observed_timeouts.append(params["_completionVerifierTimeoutSeconds"])
        return reads.pop(0)

    verifier = UnityConsoleCompletionVerifier(
        read,
        timeout_seconds=2,
        poll_seconds=0.01,
        sleep=lambda _seconds: None,
    )

    baseline = verifier.capture_baseline("persisted_scene_write_console", {})
    result = verifier.finalize(
        "persisted_scene_write_console",
        {},
        baseline,
        {"ok": True},
    )

    assert result["consoleVerified"] is True
    assert observed_timeouts
    assert all(1 <= timeout <= 2 for timeout in observed_timeouts)


def test_console_verifier_rejects_new_warning() -> None:
    warning = {"file": "Assets/Test.cs", "line": 3, "message": "warning CS0219: unused"}
    reads = [_payload(), _payload(warnings=[warning]), _payload(warnings=[warning])]
    verifier = UnityConsoleCompletionVerifier(
        lambda _params: reads.pop(0),
        timeout_seconds=2,
        poll_seconds=0.01,
        sleep=lambda _seconds: None,
    )
    baseline = verifier.capture_baseline("persisted_scene_write_console", {})
    result = verifier.finalize("persisted_scene_write_console", {}, baseline, {"ok": True})
    assert result["consoleVerified"] is False
    assert result["consoleVerification"]["code"] == "unity_console_regression"
    assert result["consoleVerification"]["newWarningCount"] == 1


def test_console_verifier_waits_for_compile_and_requires_two_stable_reads() -> None:
    reads = [_payload(), _payload(compiling=True), _payload(), _payload()]
    sleeps: list[float] = []
    verifier = UnityConsoleCompletionVerifier(
        lambda _params: reads.pop(0),
        timeout_seconds=2,
        poll_seconds=0.01,
        sleep=sleeps.append,
    )
    baseline = verifier.capture_baseline("persisted_scene_write_console", {})
    result = verifier.finalize("persisted_scene_write_console", {}, baseline, {"ok": True})
    assert result["consoleVerified"] is True
    assert len(sleeps) == 2


def test_console_verifier_fails_closed_when_baseline_is_unreadable() -> None:
    verifier = UnityConsoleCompletionVerifier(lambda _params: {"ok": True})
    try:
        verifier.capture_baseline("persisted_scene_write_console", {})
    except AgentCompletionVerificationError as exc:
        assert "not stable" in str(exc)
    else:
        raise AssertionError("unreadable baseline must fail closed")


@pytest.mark.parametrize(
    ("overrides"),
    [
        {"project_digest": ""},
        {"process_id": 0},
        {"process_started_at": ""},
        {"executable_digest": ""},
    ],
)
def test_console_verifier_rejects_incomplete_baseline_identity(overrides) -> None:
    verifier = UnityConsoleCompletionVerifier(lambda _params: _payload(**overrides))
    with pytest.raises(AgentCompletionVerificationError, match="not stable"):
        verifier.capture_baseline("persisted_scene_write_console", {})


@pytest.mark.parametrize(
    ("final_overrides", "expected_code"),
    [
        ({"project_digest": "c" * 64}, "unity_project_changed"),
        ({"process_id": 5252}, "unity_process_changed"),
        ({"process_started_at": "2026-08-10T00:05:00Z"}, "unity_process_changed"),
        ({"executable_digest": "d" * 64}, "unity_process_changed"),
    ],
)
def test_console_verifier_rejects_changed_final_identity(final_overrides, expected_code) -> None:
    reads = [_payload(), _payload(**final_overrides), _payload(**final_overrides)]
    verifier = UnityConsoleCompletionVerifier(
        lambda _params: reads.pop(0),
        timeout_seconds=2,
        poll_seconds=0.01,
        sleep=lambda _seconds: None,
    )
    baseline = verifier.capture_baseline("persisted_scene_write_console", {})
    result = verifier.finalize("persisted_scene_write_console", {}, baseline, {"ok": True})
    assert result["consoleVerified"] is False
    assert result["consoleVerification"]["code"] == expected_code


def test_console_verifier_fails_closed_on_malformed_diagnostics() -> None:
    malformed = _payload(errors=[{"line": "not-an-integer", "message": "error CS0000"}])
    verifier = UnityConsoleCompletionVerifier(lambda _params: malformed)
    try:
        verifier.capture_baseline("persisted_scene_write_console", {})
    except AgentCompletionVerificationError as exc:
        assert "not stable" in str(exc)
    else:
        raise AssertionError("malformed diagnostics must not become a clean baseline")


def test_unity_compile_reader_captures_errors_warnings_and_completion_state() -> None:
    root = Path(__file__).resolve().parents[1]
    source = (root / "Assets" / "VRCForge" / "Editor" / "CompileErrorReader.cs").read_text(
        encoding="utf-8"
    )
    assert "CompilationPipeline.compilationFinished += OnCompilationFinished" in source
    assert "PrimitiveBasisLiveGuard.RequireBoundRequest(@params)" in source
    assert "?? InspectCurrentProcessIdentity()" in source
    assert "PrimitiveBasisLiveGuard.Sha256File(executablePath)" in source
    assert "PrimitiveBasisLiveGuard.NormalizeProjectRoot(projectRoot)" in source
    assert "CompilerMessageType.Error && message.type != CompilerMessageType.Warning" in source
    assert '["severity"] = message.type == CompilerMessageType.Error ? "error" : "warning"' in source
    assert "captureComplete" in source
    assert "warningCount" in source
    assert "diagnosticsTruncated || diagnostics.Count > maxErrors" in source
    assert "for (var i = 0; i < count; i++)" in source
    assert "if (results.Count >= maxEntries)" in source
    assert "truncated = true" in source
