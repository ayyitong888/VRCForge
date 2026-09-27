import json

from test_native_runtime_gateway import (
    budgeted_request_history,
    call,
    run,
    setup_gateway,
)


def test_native_rejected_completion_call_requests_host_bound_reply_contract(tmp_path):
    gateway, model, invoked = setup_gateway(
        tmp_path,
        [
            call("read-1", "vrcforge_read_text_file", {"path": "a.txt"}),
            call(
                "unbound",
                "vrcforge_runtime_action",
                {
                    "action": "reply",
                    "reply": "Incorrect success",
                    "completion_claim": {
                        "satisfied": True,
                        "evidence_action_ids": ["not-executed"],
                    },
                },
            ),
            call("recovered", "vrcforge_runtime_action", {
                "action": "reply", "reply": "Read complete.",
                "completion_claim": {"satisfied": True},
            }),
        ],
    )

    result = run(gateway, tmp_path)

    assert len(invoked) == 1
    assert len(model.requests) == 3
    correction_request = budgeted_request_history(model.requests[2])
    rejected_call = next(
        message for message in correction_request
        if message.get("role") == "tool" and message.get("tool_call_id") == "unbound"
    )
    correction_text = rejected_call["content"]
    assert "action=reply" in correction_text
    assert "omit optional evidence_action_ids" in correction_text
    assert "action=correct" in correction_text
    assert "reply_completion_claim" in correction_text
    assert "requiredEvidenceActionIds" in correction_text
    assert "not-executed" not in correction_text

    assert result["plan"]["completionClaim"] == {"satisfied": True}
    expected = [
        action["actionId"]
        for action in result["plan"]["task"]["actions"]
        if action["status"] == "completed"
    ]
    assert expected
    assert result["plan"]["taskCompletion"]["evidenceActionIds"] == expected
    assert json.dumps(result, ensure_ascii=False).find("not-executed") == -1
