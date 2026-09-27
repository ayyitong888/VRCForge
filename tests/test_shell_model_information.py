"""Execution receipts must survive the shared model outlet without tail loss."""
import json

import pytest

from tests.test_runtime_planner_service import service, _assert_complete_model_pages


@pytest.mark.parametrize("tool", ["shell", "unity_shell"])
def test_complete_execution_receipt_is_redacted_then_paged(tool):
    command = "Write-Output 'inspect'; " * 1200 + "Write-Output 'COMMAND_TAIL'; $api_key='fixture-secret'"
    cwd = "D:/" + "nested/" * 80 + "DIRECTORY_TAIL"
    step = {"tool": tool, "kind": "shell", "status": "executed",
            "executedInput": {"command": command, "cwd": cwd},
            "result": {"exitCode": 0, "stdout": "inspected"}}
    observation = service()._llm_loop_step_observation(step)
    receipt = json.loads(observation.split("; executedInput=", 1)[1].split("; readEvidence=", 1)[0])
    assert receipt["cwd"] == cwd
    assert "COMMAND_TAIL" in receipt["command"]
    assert "fixture-secret" not in observation
    assert receipt["command"].count("Write-Output 'inspect'") == 1200
    assert len(_assert_complete_model_pages(step, observation)) > 1
