"""A real approved transaction must fail coherently after a writer returned."""
from copy import deepcopy

import pytest

from agent_gateway import AgentGateway
from agent_task_loop import TASK_LOOP_SCHEMA


@pytest.fixture(params=["checkpoint", "observer"])
def failed_finalization(tmp_path, monkeypatch, request):
    gateway = AgentGateway(tmp_path / "config.json", tmp_path / "audit")
    config = gateway.ensure_config()
    config.enabled = config.allow_write_requests = True
    gateway.save_config(config)
    project = tmp_path / "Project"
    for directory in ("Assets", "Packages", "ProjectSettings"):
        (project / directory).mkdir(parents=True)
    service = gateway.approval_transactions
    written = []
    # Same missing-proof shape as the retained real outfit result. In particular,
    # an ordinary ok payload must not be promoted to verified by its consumer.
    raw = {"ok": True, "preview": False, "action": "add_wardrobe_outfit",
           "assignedValue": 1, "menuToggleAdded": True,
           "previousWardrobeFingerprint": "", "wardrobeFingerprint": "saved"}
    def writer(arguments):
        written.append(deepcopy(arguments))
        return deepcopy(raw)
    tool = "vrcforge_fixture_finalize"
    service.register_write_handler(tool, "Write fixture assets", "high", writer,
                                   pre_write_checkpoint_required=request.param == "checkpoint")
    if request.param == "checkpoint":
        monkeypatch.setattr(type(service), "_create_pre_write_checkpoint", lambda *_args: {
            "id": "checkpoint-fixture", "ok": True, "status": "ready",
            "projectRoot": str(project),
            "assetFootprint": {"schema": "vrcforge.asset_checkpoint_footprint.v1"}})
        expected = "Created-asset checkpoint requires verified terminal write completion."
        layer = "checkpoint_finalize"
    else:
        expected = "Lifecycle observer failed after saved asset write."
        layer = "apply_lifecycle_observer"
        def observer(_self, phase, *_args, **_kwargs):
            if phase == "handler_returned":
                raise RuntimeError(expected)
        monkeypatch.setattr(type(service), "_observe_apply_lifecycle", observer)
    requested = service.create_apply_request(
        {"target_tool": tool, "arguments": {"projectRoot": str(project)}},
        task_context={"schema": TASK_LOOP_SCHEMA, "taskId": "task-finalize",
                      "objective": "Save and verify the fixture assets"})
    approval_id = requested["approval"]["id"]
    service.approve(approval_id)
    result = service.apply_approved({"approval_id": approval_id})
    assert len(written) == 1, result
    assert result["approval"]["error"] == expected
    assert result["outcome"]["status"] == "failed"
    return result, expected, layer


def test_post_writer_failure_never_preserves_a_completed_task(failed_finalization):
    result, _, _ = failed_finalization
    for owner in (result, result["approval"]):
        assert owner["taskCompletion"]["status"] == "failed"
        assert owner["taskCompletion"]["outcome"]["status"] == "failed"


def test_post_writer_failure_preserves_its_actual_cause(failed_finalization):
    result, expected, layer = failed_finalization
    failure = result["writeFailure"]
    assert failure["failureLayer"] == layer
    assert expected in failure["rootCause"]["message"]
    assert failure.get("mutationStarted") is not False
    assert failure.get("commitState") != "not_started"
    assert failure["safeToRetry"] is False
