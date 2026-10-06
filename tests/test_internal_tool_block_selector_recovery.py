from copy import deepcopy

import pytest

from agent_task_loop import (
    AgentTaskLoop,
    approval_completion,
    approval_task_context,
)


LOADER = "vrcforge_load_internal_tool_block"


def ok_outcome():
    return {"status": "ok", "summary": "discovery completed",
            "verification": {"state": "not_required", "checks": []}}


@pytest.mark.parametrize("code", [
    "internal_tool_selection_invalid",
    "internal_tool_block_selector_invalid",
])
def test_corrected_discovery_rebinds_obligation_without_erasing_failed_receipt(code):
    loop = AgentTaskLoop("Inspect an Avatar after corrected tool discovery")
    arguments = {"block": "unity", "tools": []}
    loop.require_action(kind="skill", tool=LOADER, arguments=arguments)
    rejected = loop.record_action(kind="skill", tool=LOADER, arguments=arguments,
        raw_result={"ok": False}, outcome={"status": "failed", "summary": "invalid discovery",
                                         "error": {"code": code}})
    failed_outcome = deepcopy(rejected["outcome"])
    corrected = loop.record_action(kind="skill", tool=LOADER,
        arguments={"block": "avatar", "tools": []}, raw_result={"ok": True}, outcome=ok_outcome())

    old = next(row for row in loop.planner_projection()["actions"]
               if row["actionId"] == rejected["actionId"])
    assert old["status"] == "superseded"
    assert old["supersededBy"] == corrected["actionId"]
    assert old["outcome"] == failed_outcome
    assert rejected["actionId"] in corrected["correctedActionIds"]
    assert loop.gate_terminal({"planner": "llm", "nextStep": "done", "reply": "discovery inspected",
        "completionClaim": {"satisfied": True, "evidenceActionIds": [corrected["actionId"]]}})["nextStep"] == "done"


def test_two_rejected_selectors_remain_corrected_after_approval_continuation():
    loop = AgentTaskLoop("Inspect after corrected discovery")
    rejected = []
    for selector in ("unity", "unity_avatar"):
        arguments = {"block": selector, "tools": []}
        loop.require_action(kind="skill", tool=LOADER, arguments=arguments)
        rejected.append(loop.record_action(kind="skill", tool=LOADER, arguments=arguments,
            raw_result={"ok": False}, outcome={"status": "failed", "summary": "unknown selector",
                "error": {"code": "internal_tool_block_selector_invalid"}}))
    corrected = loop.record_action(kind="skill", tool=LOADER, arguments={"tools": []},
        raw_result={"ok": True}, outcome=ok_outcome())
    write_tool = "vrcforge_create_gameobject"
    arguments = {"name": "ApprovalFixture"}
    context = approval_task_context(
        loop.approval_seed(requested_tool=write_tool, requested_arguments=arguments),
        tool=write_tool, arguments=arguments)
    completion = approval_completion(context, raw_result={"ok": True}, outcome=ok_outcome())
    assert completion["status"] == "needs_user_action"
    assert completion["outcome"]["verification"]["state"] == "needs_user_action"
    assert completion["outcome"]["verification"]["checks"] == [
        {"kind": "persistedReadback", "state": "failed"},
        {"kind": "sceneSaved", "state": "failed"},
        {"kind": "consoleVerified", "state": "failed"},
    ]
    resumed = AgentTaskLoop.from_approval_context(context, completion)
    actions = resumed.planner_projection()["actions"]
    for failed in rejected:
        old = next(row for row in actions if row["actionId"] == failed["actionId"])
        assert old["status"] == "superseded"
        assert old["outcome"]["status"] == "failed"
        assert old["supersededBy"] == corrected["actionId"]
    # Discovery correction cannot convert an unverified approved write to done.
    assert resumed.gate_terminal({"planner": "llm", "nextStep": "done", "reply": "done", "completionClaim": {
        "satisfied": True, "evidenceActionIds": [corrected["actionId"], completion["actionId"]]}})["nextStep"] == "needs_user_action"


@pytest.mark.parametrize("tool,code,status", [
    (LOADER, "connection_failed", "failed"),
    (LOADER, "internal_tool_block_selector_invalid", "needs_user_action"),
    ("vrcforge_read_asset", "asset_not_found", "failed"),
    ("vrcforge_write_asset", "internal_tool_block_selector_invalid", "failed"),
    ("vrcforge_write_asset", "approval_required", "needs_user_action"),
])
def test_successful_discovery_keeps_unrelated_or_pending_obligations(tool, code, status):
    loop = AgentTaskLoop("Inspect without concealing other failures")
    failed = loop.record_action(kind="skill", tool=tool, arguments={}, raw_result={"ok": False},
        outcome={"status": status, "summary": "must remain", "error": {"code": code}})
    corrected = loop.record_action(kind="skill", tool=LOADER, arguments={"tools": []},
        raw_result={"ok": True}, outcome=ok_outcome())
    old = next(row for row in loop.planner_projection()["actions"]
               if row["actionId"] == failed["actionId"])
    assert old["status"] == status
    assert old["outcome"]["status"] == status
    assert failed["actionId"] not in corrected.get("correctedActionIds", [])
    assert loop.gate_terminal({"nextStep": "done", "reply": "done"})["nextStep"] != "done"


@pytest.mark.parametrize("tool,status", [(LOADER, "failed"), ("vrcforge_read_asset", "ok")])
def test_only_a_successful_loader_can_supersede_a_rejected_selector(tool, status):
    loop = AgentTaskLoop("Inspect after an incomplete correction")
    rejected = loop.record_action(kind="skill", tool=LOADER, arguments={"block": "unity"},
        raw_result={"ok": False}, outcome={"status": "failed", "summary": "unknown selector",
            "error": {"code": "internal_tool_block_selector_invalid"}})
    outcome = ok_outcome() if status == "ok" else {"status": "failed", "summary": "not corrected",
        "error": {"code": "internal_tool_block_selector_invalid"}}
    loop.record_action(kind="skill", tool=tool, arguments={}, raw_result={}, outcome=outcome)
    old = next(row for row in loop.planner_projection()["actions"]
               if row["actionId"] == rejected["actionId"])
    assert old["status"] == "failed"
    assert loop.gate_terminal({"nextStep": "done", "reply": "done"})["nextStep"] != "done"
