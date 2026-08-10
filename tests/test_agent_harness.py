from __future__ import annotations

import copy
import json
from pathlib import Path
import subprocess
import sys
import time

import pytest

from agent_harness import AgentHarnessError, evaluate_agent_harness, load_agent_harness_matrix
from scripts.evaluate_agent_harness import (
    _find_runtime_receipt,
    _matching_runtime_receipt_ids,
    _wait_for_runtime_receipt,
)


ROOT = Path(__file__).resolve().parents[1]
MATRIX = ROOT / "tests" / "fixtures" / "agent_harness_matrix.json"
SCRIPT = ROOT / "scripts" / "evaluate_agent_harness.py"


def test_offline_agent_harness_runs_the_real_runtime_selection_and_completion_gates() -> None:
    completed = subprocess.run(
        [sys.executable, str(SCRIPT), "--matrix", str(MATRIX)],
        cwd=ROOT,
        text=True,
        capture_output=True,
        timeout=30,
        check=False,
    )

    assert completed.returncode == 0, completed.stdout + completed.stderr
    report = json.loads(completed.stdout)
    assert report["schema"] == "vrcforge.agent_harness_report.v5"
    assert report["accepted"] is True
    assert report["releaseAccepted"] is False
    assert report["toolsExecuted"] is False
    assert report["selection"]["passed"] == report["selection"]["total"] == 16
    assert report["completion"]["passed"] == report["completion"]["total"] == 7
    assert report["loop"]["passed"] == report["loop"]["total"] == 9
    assert report["chain"]["passed"] == report["chain"]["total"] == 6
    assert report["selectionOnly"] is False


def test_agent_harness_rejects_duplicate_case_ids(tmp_path: Path) -> None:
    matrix = json.loads(MATRIX.read_text(encoding="utf-8"))
    matrix["completionCases"][0]["id"] = matrix["selectionCases"][0]["id"]
    duplicate = tmp_path / "agent_harness_matrix.duplicate.json"
    duplicate.write_text(json.dumps(matrix), encoding="utf-8")

    with pytest.raises(AgentHarnessError, match="duplicate matrix case id"):
        load_agent_harness_matrix(duplicate)


@pytest.mark.parametrize(
    ("mutation", "error"),
    [
        (lambda case: case.pop("expectedActionKind"), "requires expectedActionKind"),
        (
            lambda case: case.update(
                {"expectedActionKind": "write", "exposureLayer": "planning"}
            ),
            "must expose writes only in execution",
        ),
    ],
)
def test_agent_harness_selection_contract_requires_action_kind_and_write_exposure(
    tmp_path: Path,
    mutation,
    error: str,
) -> None:
    matrix = json.loads(MATRIX.read_text(encoding="utf-8"))
    mutation(matrix["selectionCases"][0])
    invalid = tmp_path / "agent_harness_matrix.invalid-selection.json"
    invalid.write_text(json.dumps(matrix), encoding="utf-8")

    with pytest.raises(AgentHarnessError, match=error):
        load_agent_harness_matrix(invalid)


def test_real_journey_request_requires_an_authenticated_app_backend(tmp_path: Path) -> None:
    request_path = tmp_path / "journey.json"
    request_path.write_text(json.dumps({"message": "inspect"}), encoding="utf-8")

    completed = subprocess.run(
        [
            sys.executable,
            str(SCRIPT),
            "--matrix",
            str(MATRIX),
            "--journey-request",
            str(request_path),
        ],
        cwd=ROOT,
        text=True,
        capture_output=True,
        timeout=30,
        check=False,
    )

    assert completed.returncode != 0
    assert "--journey-request requires --app-backend-url" in completed.stderr


def test_async_journey_receipt_requires_an_authenticated_app_backend(tmp_path: Path) -> None:
    receipt_path = tmp_path / "receipt.json"
    receipt_path.write_text(json.dumps({"receipt": {"token": "opaque"}}), encoding="utf-8")

    completed = subprocess.run(
        [
            sys.executable,
            str(SCRIPT),
            "--matrix",
            str(MATRIX),
            "--journey-receipt",
            str(receipt_path),
        ],
        cwd=ROOT,
        text=True,
        capture_output=True,
        timeout=30,
        check=False,
    )

    assert completed.returncode != 0
    assert "--journey-receipt requires --app-backend-url" in completed.stderr


def test_async_journey_receipt_matches_exact_runtime_task_identity() -> None:
    expires_at_ms = int(time.time() * 1000) + 60_000
    expected = {
        "receiptId": "receipt-current",
        "expiresAtMs": expires_at_ms,
        "token": "task-owned",
    }
    payload = {
        "runtimeContinuations": [
            {
                "sessionId": "session-1",
                "plan": {"taskCompletion": {"taskId": "task-other"}},
                "harnessJourneyReceipt": {
                    "receiptId": "receipt-other",
                    "expiresAtMs": expires_at_ms,
                    "token": "wrong-task",
                },
            },
            {
                "sessionId": "session-1",
                "plan": {"taskCompletion": {"taskId": "task-1"}},
                "harnessJourneyReceipt": expected,
            },
        ]
    }

    assert _find_runtime_receipt(
        payload,
        session_id="session-1",
        task_id="task-1",
    ) == expected
    assert _find_runtime_receipt(
        payload,
        session_id="session-2",
        task_id="task-1",
    ) is None


def test_async_journey_receipt_ignores_old_and_expired_same_task_receipts() -> None:
    now_ms = int(time.time() * 1000)
    payload_before_run = {
        "runtimeContinuations": [
            {
                "sessionId": "session-1",
                "plan": {"taskCompletion": {"taskId": "task-1"}},
                "harnessJourneyReceipt": {
                    "receiptId": "receipt-old",
                    "expiresAtMs": now_ms + 60_000,
                },
            }
        ]
    }
    excluded = _matching_runtime_receipt_ids(
        payload_before_run,
        session_id="session-1",
        task_id="task-1",
    )
    payload_after_run = {
        "runtimeContinuations": [
            *payload_before_run["runtimeContinuations"],
            {
                "sessionId": "session-1",
                "plan": {"taskCompletion": {"taskId": "task-1"}},
                "harnessJourneyReceipt": {
                    "receiptId": "receipt-expired",
                    "expiresAtMs": now_ms - 1,
                },
            },
            {
                "sessionId": "session-1",
                "plan": {"taskCompletion": {"taskId": "task-1"}},
                "harnessJourneyReceipt": {
                    "receiptId": "receipt-new",
                    "expiresAtMs": now_ms + 60_000,
                },
            },
        ]
    }

    assert excluded == {"receipt-old"}
    assert _find_runtime_receipt(
        payload_after_run,
        session_id="session-1",
        task_id="task-1",
        excluded_receipt_ids=excluded,
    ) == {
        "receiptId": "receipt-new",
        "expiresAtMs": now_ms + 60_000,
    }


def test_async_journey_poll_clamps_each_http_read_to_the_remaining_deadline(
    monkeypatch,
) -> None:
    observed_timeouts: list[float] = []

    def read(_origin, _token, _path, *, timeout):
        observed_timeouts.append(timeout)
        expires_at_ms = int(time.time() * 1000) + 60_000
        return {
            "runtimeContinuations": [
                {
                    "sessionId": "session-1",
                    "plan": {"taskCompletion": {"taskId": "task-1"}},
                    "harnessJourneyReceipt": {
                        "receiptId": "receipt-current",
                        "expiresAtMs": expires_at_ms,
                        "token": "opaque",
                    },
                }
            ]
        }

    monkeypatch.setattr("scripts.evaluate_agent_harness._get_app_json", read)

    receipt = _wait_for_runtime_receipt(
        "http://127.0.0.1:8757",
        "token",
        session_id="session-1",
        task_id="task-1",
        timeout_seconds=1,
    )

    assert receipt["token"] == "opaque"
    assert len(observed_timeouts) == 1
    assert 0 < observed_timeouts[0] <= 1


def test_agent_harness_reports_a_wrong_selection_without_running_a_tool() -> None:
    matrix = load_agent_harness_matrix(MATRIX)
    one_case = copy.deepcopy(matrix)
    one_case["selectionCases"] = [one_case["selectionCases"][0]]
    one_case["completionCases"] = [one_case["completionCases"][0]]

    report = evaluate_agent_harness(
        one_case,
        select_tool=lambda _prompt, _exposure_layer: "vrcforge_health",
    )

    assert report["accepted"] is False
    assert report["selection"]["cases"] == [
        {
            "id": "selection-compile-errors",
            "passed": False,
            "selectedTool": "vrcforge_health",
            "expectedTool": "vrcforge_get_compile_errors",
            "selectedActionKind": "skill",
            "expectedActionKind": "skill",
            "exposureLayer": "planning",
            "providerEvidenceValid": False,
        }
    ]
    assert report["completion"]["passed"] == 1


def test_agent_harness_rejects_the_right_tool_with_the_wrong_action_kind() -> None:
    matrix = load_agent_harness_matrix(MATRIX)
    one_case = copy.deepcopy(matrix)
    one_case["selectionCases"] = [one_case["selectionCases"][0]]
    one_case["completionCases"] = [one_case["completionCases"][0]]

    report = evaluate_agent_harness(
        one_case,
        select_tool=lambda _prompt, _exposure_layer: {
            "toolCalls": ["vrcforge_get_compile_errors"],
            "actionKind": "write",
        },
    )

    assert report["accepted"] is False
    assert report["selection"]["cases"][0]["selectedTool"] == "vrcforge_get_compile_errors"
    assert report["selection"]["cases"][0]["selectedActionKind"] == "write"
    assert report["selection"]["cases"][0]["expectedActionKind"] == "skill"
    assert report["selection"]["cases"][0]["passed"] is False


def test_all_selection_cases_reject_a_dynamically_rotated_wrong_tool() -> None:
    matrix = load_agent_harness_matrix(MATRIX)
    cases = matrix["selectionCases"]
    expected_tools = [case["expectedTool"] for case in cases]
    wrong_by_prompt: dict[str, str] = {}
    for index, case in enumerate(cases):
        candidates = (
            expected_tools[(index + offset) % len(expected_tools)]
            for offset in range(1, len(expected_tools))
        )
        wrong_by_prompt[case["prompt"]] = next(
            candidate for candidate in candidates if candidate != case["expectedTool"]
        )

    report = evaluate_agent_harness(
        matrix,
        select_tool=lambda prompt, _layer: wrong_by_prompt[prompt],
    )

    assert report["selection"]["total"] == len(cases) == 16
    assert report["selection"]["passed"] == 0
    assert all(not case["passed"] for case in report["selection"]["cases"])


def test_failure_completion_cases_are_never_projected_as_done() -> None:
    matrix = load_agent_harness_matrix(MATRIX)
    expected_by_id = {
        case["id"]: case
        for case in matrix["completionCases"]
        if case["expectedStatus"] != "ok"
    }
    selected_by_prompt = {
        case["prompt"]: case["expectedTool"] for case in matrix["selectionCases"]
    }

    report = evaluate_agent_harness(
        matrix,
        select_tool=lambda prompt, _layer: selected_by_prompt[prompt],
    )
    reported_by_id = {case["id"]: case for case in report["completion"]["cases"]}

    assert expected_by_id
    for case_id, expected in expected_by_id.items():
        actual = reported_by_id[case_id]
        assert actual["outcomeStatus"] == expected["expectedStatus"]
        assert actual["nextStep"] == expected["expectedNextStep"]
        assert actual["nextStep"] != "done"


def test_production_harness_guards_do_not_embed_fixture_answers() -> None:
    matrix = load_agent_harness_matrix(MATRIX)
    production_sources = "\n".join(
        (ROOT / path).read_text(encoding="utf-8")
        for path in (
            "agent_gateway.py",
            "agent_harness.py",
            "agent_harness_journey.py",
            "agent_task_loop.py",
            "agent_tool_result_contract.py",
            "runtime_planner_service.py",
        )
    )

    assert "tests/fixtures/agent_harness_matrix.json" not in production_sources
    for case in matrix["selectionCases"]:
        assert case["id"] not in production_sources
        assert case["prompt"] not in production_sources


def test_agent_harness_does_not_treat_selection_receipts_as_end_to_end_release_evidence() -> None:
    matrix = load_agent_harness_matrix(MATRIX)
    one_case = copy.deepcopy(matrix)
    one_case["selectionCases"] = [one_case["selectionCases"][0]]
    one_case["completionCases"] = [one_case["completionCases"][0]]
    one_case["loopCases"] = [one_case["loopCases"][0]]

    report = evaluate_agent_harness(
        one_case,
        select_tool=lambda _prompt, _layer: {
            "toolCalls": ["vrcforge_get_compile_errors"],
            "actionKind": "skill",
            "providerEvidence": {"receiptId": "one-use"},
        },
        verify_selection=lambda _prompt, _result, _layer: True,
        selection_source="app-backend:http://127.0.0.1:8757",
        trusted_selection_receipts=True,
    )

    assert report["accepted"] is True
    assert report["releaseAccepted"] is False
    assert report["selectionReceiptAccepted"] is True
    assert report["runtimeJourneyAccepted"] is False
    assert report["externalVerificationAccepted"] is False
    assert report["toolsExecuted"] is False
    assert report["providerEvidenceValid"] is True
    assert report["trustedSelectionReceipts"] is True


def test_agent_harness_release_requires_a_verified_real_gateway_journey() -> None:
    matrix = load_agent_harness_matrix(MATRIX)
    one_case = copy.deepcopy(matrix)
    one_case["selectionCases"] = [one_case["selectionCases"][0]]
    one_case["completionCases"] = [one_case["completionCases"][0]]
    one_case["loopCases"] = [one_case["loopCases"][0]]
    action_id = "action_0123456789abcdef"
    task_id = "task_0123456789abcdef"
    session_id = "session-live-unity-write"
    write_transaction = {
        "schema": "vrcforge.approved_write_transaction.v1",
        "status": "applied",
        "approvalId": "approval-live-unity-write",
        "checkpointId": "checkpoint-live-unity-write",
        "checkpointVerified": True,
        "taskId": task_id,
        "sessionId": session_id,
        "actionId": action_id,
        "kind": "write",
        "tool": "fixture_unity_write",
    }
    journey = {
        "schema": "vrcforge.agent_harness_journey.v1",
        "id": "live-unity-write",
        "sessionId": session_id,
        "taskId": task_id,
        "toolExecutions": 1,
        "providerRequestCount": 2,
        "resultRefeedCount": 1,
        "nextStep": "done",
        "taskStatus": "completed",
        "completedActionIds": [action_id],
        "evidenceActionIds": [action_id],
        "verificationProfiles": ["persisted_scene_write_console"],
        "verificationStates": ["passed"],
        "completedActions": [
            {
                "actionId": action_id,
                "kind": "write",
                "tool": "fixture_unity_write",
                "verificationProfiles": ["persisted_scene_write_console"],
                "verificationStates": ["passed"],
                "writeTransaction": write_transaction,
            }
        ],
        "writeTransactionCount": 1,
        "receipt": {"id": "one-use-runtime-journey"},
    }

    report = evaluate_agent_harness(
        one_case,
        select_tool=lambda _prompt, _layer: {
            "toolCalls": ["vrcforge_get_compile_errors"],
            "actionKind": "skill",
            "providerEvidence": {"receiptId": "one-use-selection"},
        },
        verify_selection=lambda _prompt, _result, _layer: True,
        trusted_selection_receipts=True,
        runtime_journeys=[journey],
        verify_runtime_journey=lambda value: journey if value.get("receipt") == journey["receipt"] else {},
        trusted_runtime_journey_receipts=True,
    )

    assert report["releaseAccepted"] is True
    assert report["selectionReceiptAccepted"] is True
    assert report["runtimeJourneyAccepted"] is True
    assert report["externalVerificationAccepted"] is True
    assert report["toolsExecuted"] is True


@pytest.mark.parametrize(
    "mutation",
    [
        {"toolExecutions": 0},
        {"providerRequestCount": 1},
        {"resultRefeedCount": 0},
        {"nextStep": "tool_failed"},
        {"taskStatus": "failed"},
        {"evidenceActionIds": ["action_other"]},
        {"verificationProfiles": ["canonical_tool_result"]},
        {"completedActions": []},
        {"writeTransactionCount": 0},
    ],
)
def test_agent_harness_runtime_journey_fails_closed(mutation: dict[str, object]) -> None:
    matrix = load_agent_harness_matrix(MATRIX)
    action_id = "action_0123456789abcdef"
    journey: dict[str, object] = {
        "schema": "vrcforge.agent_harness_journey.v1",
        "id": "live-unity-write",
        "sessionId": "session-live-unity-write",
        "taskId": "task-live-unity-write",
        "toolExecutions": 1,
        "providerRequestCount": 2,
        "resultRefeedCount": 1,
        "nextStep": "done",
        "taskStatus": "completed",
        "completedActionIds": [action_id],
        "evidenceActionIds": [action_id],
        "verificationProfiles": ["persisted_scene_write_console"],
        "verificationStates": ["passed"],
        "completedActions": [
            {
                "actionId": action_id,
                "kind": "write",
                "tool": "fixture_unity_write",
                "verificationProfiles": ["persisted_scene_write_console"],
                "verificationStates": ["passed"],
                "writeTransaction": {
                    "schema": "vrcforge.approved_write_transaction.v1",
                    "status": "applied",
                    "approvalId": "approval-live-unity-write",
                    "checkpointId": "checkpoint-live-unity-write",
                    "checkpointVerified": True,
                    "taskId": "task-live-unity-write",
                    "sessionId": "session-live-unity-write",
                    "actionId": action_id,
                    "kind": "write",
                    "tool": "fixture_unity_write",
                },
            }
        ],
        "writeTransactionCount": 1,
    }
    journey.update(mutation)

    report = evaluate_agent_harness(
        matrix,
        select_tool=lambda _prompt, _layer: "vrcforge_get_compile_errors",
        runtime_journeys=[journey],
        verify_runtime_journey=lambda _value: journey,
        trusted_runtime_journey_receipts=True,
    )

    assert report["runtimeJourneyAccepted"] is False
    assert report["releaseAccepted"] is False


def test_agent_harness_evaluates_only_the_authenticated_journey_projection() -> None:
    matrix = load_agent_harness_matrix(MATRIX)
    forged_receipt = {
        "schema": "vrcforge.agent_harness_journey.v1",
        "id": "forged",
        "toolExecutions": 99,
        "providerRequestCount": 100,
        "resultRefeedCount": 99,
        "nextStep": "done",
        "taskStatus": "completed",
        "completedActionIds": ["action_forged"],
        "evidenceActionIds": ["action_forged"],
        "verificationProfiles": ["multi_angle_visual"],
        "verificationStates": ["passed"],
    }
    authenticated = {
        **forged_receipt,
        "toolExecutions": 0,
        "providerRequestCount": 0,
        "resultRefeedCount": 0,
        "completedActionIds": [],
        "evidenceActionIds": [],
    }

    report = evaluate_agent_harness(
        matrix,
        select_tool=lambda _prompt, _layer: "vrcforge_get_compile_errors",
        runtime_journeys=[forged_receipt],
        verify_runtime_journey=lambda _receipt: authenticated,
        trusted_runtime_journey_receipts=True,
    )

    assert report["runtimeJourneyAccepted"] is False
    assert report["toolsExecuted"] is False


def test_agent_harness_rejects_legacy_boolean_journey_verification() -> None:
    matrix = load_agent_harness_matrix(MATRIX)
    report = evaluate_agent_harness(
        matrix,
        select_tool=lambda _prompt, _layer: "vrcforge_get_compile_errors",
        runtime_journeys=[{"receipt": "forged"}],
        verify_runtime_journey=lambda _receipt: True,  # type: ignore[return-value]
        trusted_runtime_journey_receipts=True,
    )

    assert report["runtimeJourneyAccepted"] is False


def test_agent_harness_does_not_accept_unverified_provider_output_as_release_evidence() -> None:
    matrix = load_agent_harness_matrix(MATRIX)
    one_case = copy.deepcopy(matrix)
    one_case["selectionCases"] = [one_case["selectionCases"][0]]
    one_case["completionCases"] = [one_case["completionCases"][0]]
    one_case["loopCases"] = [one_case["loopCases"][0]]

    report = evaluate_agent_harness(
        one_case,
        select_tool=lambda _prompt, _layer: {
            "toolCalls": ["vrcforge_get_compile_errors"],
            "actionKind": "skill",
            "providerEvidence": {"receiptId": "forged"},
        },
        verify_selection=lambda _prompt, _result, _layer: False,
        trusted_selection_receipts=True,
    )

    assert report["accepted"] is True
    assert report["releaseAccepted"] is False
    assert report["providerEvidenceValid"] is False
