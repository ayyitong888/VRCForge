from agent_runtime_event_projection import project_runtime_turn_event


def test_question_terminal_exposes_only_bounded_confirmed_steer_ids():
    projected = project_runtime_turn_event({
        "continuationSource": "question_answered", "sessionId": "session", "turnId": "turn",
        "consumedSteerInputIds": ["turn-confirmed:input-1", "", {"message": "private"}, "bad value", "x" * 241],
        "plan": {"reply": "Selected file read", "nextStep": "done"},
    })
    assert projected["consumedSteerInputIds"] == ["turn-confirmed:input-1"]
    assert projected["plan"]["reply"] == "Selected file read"


def test_absent_consumption_receipt_never_claims_steer_delivery():
    projected = project_runtime_turn_event({
        "continuationSource": "question_answered", "sessionId": "session", "turnId": "turn",
        "plan": {"nextStep": "done"},
    })
    assert "consumedSteerInputIds" not in projected
