from __future__ import annotations

import json
from pathlib import Path

import pytest

from agent_gateway import parse_skill_markdown

ROOT = Path(__file__).resolve().parents[1] / "artifacts/skills/vrcforge-avatar-wardrobe"
CONTROLS = {
    "vrcforge_gesture_manager_enter_play_mode",
    "vrcforge_gesture_manager_set_parameter",
    "vrcforge_start_runtime_observation",
    "vrcforge_set_play_mode",
}


def test_wardrobe_declares_existing_runtime_start_read_and_exit_tools() -> None:
    skill = parse_skill_markdown(ROOT / "SKILL.md")
    assert CONTROLS | {"vrcforge_get_runtime_observation"} <= set(skill["allowedTools"])


@pytest.mark.parametrize("control", sorted(CONTROLS))
def test_runtime_controls_are_execution_steps_with_approval(control: str) -> None:
    workflow = json.loads((ROOT / "workflows/wardrobe-authoring.json").read_text(encoding="utf-8"))
    owning_steps = [s for s in workflow["steps"] if control in {s["tool"], *s.get("tools", [])}]
    assert owning_steps
    assert all(s["writes"] and s["runtimePermissionGateRequired"] and s["runtimeApprovalRequired"] for s in owning_steps)


def test_observation_workflow_reads_original_job_then_restores_approved_play_state() -> None:
    workflow = json.loads((ROOT / "workflows/wardrobe-authoring.json").read_text(encoding="utf-8"))
    steps = workflow["steps"]
    index = lambda name: next(i for i, s in enumerate(steps) if name in {s["tool"], *s.get("tools", [])})
    started = index("vrcforge_start_runtime_observation")
    # Recognition may read an existing job before any newly approved capture.
    readback = next(i for i, s in enumerate(steps) if i > started and "vrcforge_get_runtime_observation" in {s["tool"], *s.get("tools", [])})
    assert started < readback < index("vrcforge_set_play_mode")
    lifecycle = workflow["communityWardrobeContract"]["runtimeEvidence"]["observationLifecycle"]
    assert lifecycle["pendingAction"] == "read_original_job_id_without_restart"
    assert lifecycle["requiresFiniteDurationAndFrameBudget"] is True
    assert lifecycle["completedDoesNotMeanVisualAcceptance"] is True
    assert lifecycle["cleanup"] == "restore_user_approved_play_state_and_read_back"
    assert workflow["recognitionLoop"]["writesAllowed"] is False
