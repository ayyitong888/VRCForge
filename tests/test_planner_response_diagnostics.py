import json

import pytest

from runtime_planner_service import PlannerModelResult, parse_llm_plan_response
from test_runtime_planner_service import FakeModel, service


@pytest.mark.parametrize("raw", [
    '{"action":"reply","reply":"first\nsecond","completion_claim":{"satisfied":true}}',
    '{"broken":, "nested":{"action":"reply","reply":"must not run"}}',
    '[{"action":"reply","reply":"must not run"}]',
])
def test_malformed_or_nonobject_outer_response_cannot_select_nested_object(raw):
    assert parse_llm_plan_response(raw) is None


@pytest.mark.parametrize("wrapper", ["{}", "Here is the plan:\n{}\nDone.", "```json\n{}\n```"])
def test_valid_outer_envelope_remains_supported(wrapper):
    payload = {"action": "reply", "reply": "line one\nline two", "completion_claim": {"satisfied": True}}
    assert parse_llm_plan_response(wrapper.format(json.dumps(payload))) == payload


def failure(raw):
    return service(model=FakeModel(PlannerModelResult(raw)))._llm_plan_agent_turn("continue", {}, [])["plannerFailure"]["invalidResponse"]


def test_invalid_response_preserves_whitespace_and_exact_json_error_location():
    raw = ' \n```json\n{"action":"reply","reply":"first\n\tsecond","completion_claim":{"satisfied":true}}\n```'
    result = failure(raw)
    assert result["stage"] == "json_object_parse"
    assert result["preview"] == raw
    try:
        json.JSONDecoder().raw_decode(raw, raw.index("{"))
    except json.JSONDecodeError as error:
        assert result["jsonError"] == {"reason": error.msg, "position": error.pos, "line": error.lineno, "column": error.colno}
    assert result["selectedKeys"] == []


def test_diagnostics_redact_secrets_and_bound_preview_and_selected_keys():
    secret = "sk-" + "a" * 40
    raw = json.dumps({"action": "unknown", secret: "private", "reply": "api_key=" + secret + "\n" + "x" * 2000})
    result = failure(raw)
    assert result["stage"] == "action_validation"
    assert len(result["preview"]) <= 1200
    assert secret not in json.dumps(result)
    assert result["selectedKeys"] == ["action", "<redacted>", "reply"]
    assert "jsonError" not in result


def test_fenced_native_call_with_json_arguments_remains_supported():
    raw = ('```xml\n<tool_call><function=skill_tool_selector>'
           '<parameter=skill_tool>load_internal_tool_block</parameter>'
           '<parameter=skill_params>{"block":"unity/diagnostics"}</parameter>'
           '</function></tool_call>\n```')
    parsed = parse_llm_plan_response(raw)
    assert parsed["action"] == "skill"
    assert parsed["skill_params"] == {"block": "unity/diagnostics"}


class SequenceModel:
    def __init__(self, *responses):
        self.responses = iter(responses)
        self.prompts = []

    def plan(self, prompt):
        self.prompts.append(prompt)
        response = next(self.responses)
        if isinstance(response, Exception):
            raise response
        return response


def response(text, tokens=11):
    return PlannerModelResult(text, usage={"exact": True, "inputTokens": tokens, "outputTokens": 2, "totalTokens": tokens + 2})


BAD_JSON = '{"action":"reply","reply":"private_marker\nbroken","completion_claim":{"satisfied":true}}'


def test_one_format_correction_reuses_prompt_and_accumulates_usage():
    model = SequenceModel(response(BAD_JSON), response('{"action":"reply","reply":"verified"}', 22))
    usage = {}
    plan = service(model=model)._llm_plan_agent_turn("continue", {}, [], context_usage=usage)
    assert plan["nextStep"] == "done"
    assert plan["reply"] == "verified"
    assert len(model.prompts) == 2
    assert model.prompts[1].startswith(model.prompts[0] + "\n\n")
    assert "private_marker" not in model.prompts[1]
    assert "Invalid control character" in model.prompts[1]
    assert usage["requestCount"] == 2
    assert usage["cumulativeTotalTokens"] == 37
    assert plan["formatCorrection"]["attemptCount"] == 1
    assert plan["formatCorrection"]["parseRecovered"] is True


def test_second_bad_json_remains_terminal_without_a_third_request():
    model = SequenceModel(response(BAD_JSON), response(BAD_JSON))
    plan = service(model=model)._llm_plan_agent_turn("continue", {}, [])
    assert len(model.prompts) == 2
    assert plan["nextStep"] == "planner_failed"
    assert plan["formatCorrection"]["attemptCount"] == 1
    assert plan["formatCorrection"]["parseRecovered"] is False
    assert plan["plannerFailure"]["invalidResponse"]["stage"] == "json_object_parse"


@pytest.mark.parametrize("raw", ["", "not json", "[]", '{"action":"unknown"}', '{"action":"skill","skill_params":[]}'])
def test_other_invalid_responses_do_not_trigger_format_retry(raw):
    model = SequenceModel(response(raw))
    plan = service(model=model)._llm_plan_agent_turn("continue", {}, [])
    assert len(model.prompts) == 1
    assert "formatCorrection" not in plan


def test_cancellation_in_correction_uses_original_error_propagation_and_keeps_usage():
    from dashboard_server import RuntimePlannerProviderCancelledError
    model = SequenceModel(response(BAD_JSON), RuntimePlannerProviderCancelledError("cancelled"))
    usage = {}
    with pytest.raises(RuntimePlannerProviderCancelledError):
        service(model=model)._llm_plan_agent_turn("continue", {}, [], context_usage=usage, propagate_provider_errors=True)
    assert len(model.prompts) == 2
    assert usage["requestCount"] == 1


def test_provider_error_is_not_automatically_retried():
    model = SequenceModel(TimeoutError("timeout"))
    plan = service(model=model)._llm_plan_agent_turn("continue", {}, [])
    assert len(model.prompts) == 1
    assert plan["plannerFailure"]["code"] == "provider_timeout"


def test_timeout_during_correction_is_terminal_and_retains_first_usage():
    model = SequenceModel(response(BAD_JSON), TimeoutError("timeout"))
    usage = {}
    plan = service(model=model)._llm_plan_agent_turn("continue", {}, [], context_usage=usage)
    assert len(model.prompts) == 2
    assert plan["nextStep"] == "planner_failed"
    assert plan["plannerFailure"]["code"] == "provider_timeout"
    assert plan["formatCorrection"]["attemptCount"] == 1
    assert usage["requestCount"] == 1
    assert usage["cumulativeTotalTokens"] == 13
