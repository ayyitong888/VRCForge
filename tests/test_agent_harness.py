from __future__ import annotations

import copy
import json
from pathlib import Path
import subprocess
import sys

import pytest

from agent_harness import AgentHarnessError, evaluate_agent_harness, load_agent_harness_matrix


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
    assert report["schema"] == "vrcforge.agent_harness_report.v3"
    assert report["accepted"] is True
    assert report["releaseAccepted"] is False
    assert report["toolsExecuted"] is False
    assert report["selection"]["passed"] == report["selection"]["total"] == 13
    assert report["completion"]["passed"] == report["completion"]["total"] == 7
    assert report["loop"]["passed"] == report["loop"]["total"] == 9


def test_agent_harness_rejects_duplicate_case_ids(tmp_path: Path) -> None:
    matrix = json.loads(MATRIX.read_text(encoding="utf-8"))
    matrix["completionCases"][0]["id"] = matrix["selectionCases"][0]["id"]
    duplicate = tmp_path / "agent_harness_matrix.duplicate.json"
    duplicate.write_text(json.dumps(matrix), encoding="utf-8")

    with pytest.raises(AgentHarnessError, match="duplicate matrix case id"):
        load_agent_harness_matrix(duplicate)


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
            "exposureLayer": "planning",
            "providerEvidenceValid": False,
        }
    ]
    assert report["completion"]["passed"] == 1


def test_agent_harness_requires_one_use_provider_receipts_for_release_acceptance() -> None:
    matrix = load_agent_harness_matrix(MATRIX)
    one_case = copy.deepcopy(matrix)
    one_case["selectionCases"] = [one_case["selectionCases"][0]]
    one_case["completionCases"] = [one_case["completionCases"][0]]
    one_case["loopCases"] = [one_case["loopCases"][0]]

    report = evaluate_agent_harness(
        one_case,
        select_tool=lambda _prompt, _layer: {
            "toolCalls": ["vrcforge_get_compile_errors"],
            "providerEvidence": {"receiptId": "one-use"},
        },
        verify_selection=lambda _prompt, _result, _layer: True,
        selection_source="app-backend:http://127.0.0.1:8757",
        trusted_selection_receipts=True,
    )

    assert report["accepted"] is True
    assert report["releaseAccepted"] is True
    assert report["providerEvidenceValid"] is True
    assert report["trustedSelectionReceipts"] is True


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
            "providerEvidence": {"receiptId": "forged"},
        },
        verify_selection=lambda _prompt, _result, _layer: False,
        trusted_selection_receipts=True,
    )

    assert report["accepted"] is True
    assert report["releaseAccepted"] is False
    assert report["providerEvidenceValid"] is False
