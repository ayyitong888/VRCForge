"""Production wardrobe service with primitive-call doubles; no Unity execution."""
import ast
import os
import subprocess

import pytest
import wardrobe_outfit_workflow_service as wardrobe


def service_class():
    ref = os.environ.get("VRCFORGE_WARDROBE_RECEIPT_GIT_REF")
    if not ref:
        return wardrobe.CreateWardrobeApprovedWriteService
    source = subprocess.check_output(["git", "show", f"{ref}:wardrobe_outfit_workflow_service.py"], text=True)
    node = next(item for item in ast.parse(source).body if isinstance(item, ast.ClassDef) and item.name == "CreateWardrobeApprovedWriteService")
    scope = vars(wardrobe).copy()
    exec(compile(ast.Module(body=[node], type_ignores=[]), "old-wardrobe-service", "exec"), scope)
    return scope[node.name]


def receipt(index=0):
    return {"ok": True, "verified": True, "persistedReadback": True,
            "committed": True, "commitState": "committed", "readback": {"asset": index}}


def run(results):
    calls = []
    pending = iter(results)

    def invoke(arguments):
        calls.append(arguments)
        result = next(pending)
        if isinstance(result, Exception):
            raise result
        return result

    service = service_class()(wardrobe.CreateWardrobeApprovedWritePorts(
        build_request=wardrobe.build_create_wardrobe_request,
        build_calls=wardrobe.build_create_wardrobe_core_calls,
        ensure_parameter=invoke, ensure_animator=invoke, ensure_menu=invoke,
        log=lambda *_args: None,
    ))
    return service.execute({"parameterName": "Clothes"}), calls


def test_three_verified_steps_have_an_aggregate_persisted_receipt():
    result, calls = run([receipt(index) for index in range(3)])
    assert len(calls) == 3
    assert result["ok"] and result["verified"] and result["persistedReadback"]
    assert result["commitState"] == "committed"
    assert [step["readback"]["asset"] for step in result["readback"]["steps"]] == [0, 1, 2]


@pytest.mark.parametrize("change", [
    {"verified": False}, {"persistedReadback": False}, {"readback": {}},
    {"status": "pending"}, {"preview": True}, {"committed": False},
    {"commitState": "unknown"}, {"checkpointRecoveryRequired": True},
])
def test_unverified_or_nonterminal_step_stops_following_writes(change):
    result, calls = run([{**receipt(), **change}, receipt(1), receipt(2)])
    assert len(calls) == 1
    assert result["ok"] is False and result["verified"] is False
    assert result["checkpointRecoveryRequired"] is True


@pytest.mark.parametrize("failure", [
    {"ok": False, "mutationStarted": False, "commitState": "not_started", "error": "rejected"},
    RuntimeError("transport interrupted"),
])
def test_later_failure_preserves_previous_committed_step(failure):
    result, calls = run([receipt(), failure, receipt(2)])
    assert len(calls) == 2
    assert result["ok"] is False and result["commitState"] == "partial"
    assert result["mutationStarted"] is True and result["mutationApplied"] is True
    assert result["checkpointRecoveryRequired"] is True
    assert result["steps"][0]["result"] == receipt()


def test_explicit_first_step_rejection_is_not_a_partial_write():
    result, calls = run([{"ok": False, "mutationStarted": False, "commitState": "not_started"}])
    assert len(calls) == 1 and result["commitState"] == "not_started"
    assert result["checkpointRecoveryRequired"] is False


def test_unknown_first_step_does_not_claim_no_write():
    result, _ = run([RuntimeError("lost response")])
    assert result["commitState"] == "unknown" and result["checkpointRecoveryRequired"] is True
