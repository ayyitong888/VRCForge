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
    assert report["schema"] == "vrcforge.agent_harness_report.v2"
    assert report["accepted"] is True
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

    report = evaluate_agent_harness(one_case, select_tool=lambda _prompt: "vrcforge_health")

    assert report["accepted"] is False
    assert report["selection"]["cases"] == [
        {
            "id": "selection-compile-errors",
            "passed": False,
            "selectedTool": "vrcforge_health",
            "expectedTool": "vrcforge_get_compile_errors",
        }
    ]
    assert report["completion"]["passed"] == 1
