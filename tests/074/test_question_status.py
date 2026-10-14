"""074 regression matrix for question-state status precedence."""

import json
import sys
from pathlib import Path

import pytest


REPO_ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO_ROOT))

from agent_runtime_run_ledger import AgentRuntimeRunLedger  # noqa: E402


FIXTURE_PATH = Path(__file__).with_name("question-status-fixture.json")
CASES = json.loads(FIXTURE_PATH.read_text(encoding="utf-8"))


@pytest.mark.parametrize("case", CASES, ids=[case["name"] for case in CASES])
def test_question_candidate_preserves_failure_and_stop_precedence(case):
    actual = AgentRuntimeRunLedger.turn_run_status(
        top_plan=case["top_plan"],
        shell_payload=case.get("shell_payload"),
        skill_payload=case.get("skill_payload"),
        write_payload=case.get("write_payload"),
        approval_id=case.get("approval_id", ""),
    )

    assert actual == case["expected"]
