from types import SimpleNamespace

import pytest

from provider_runtime_adapters import DeepSeekResponsesAdapter, OpenAIResponsesAdapter
from vrchat_blendshape_agent import extract_llm_token_usage


@pytest.mark.parametrize("adapter_type", [DeepSeekResponsesAdapter, OpenAIResponsesAdapter])
@pytest.mark.parametrize("streaming", [False, True])
@pytest.mark.parametrize("count", [None, 0, 8832])
def test_responses_preserves_cache_details(adapter_type, streaming, count):
    usage = {"input_tokens": 10000, "output_tokens": 2, "total_tokens": 10002}
    if count is not None:
        usage["input_tokens_details"] = {"cached_tokens": count, "cache_write_tokens": 512}
    response = {"output_text": "OK", "output": [], "usage": usage}
    adapter = adapter_type(api_key="fixture", base_url="https://provider.invalid")
    if streaming:
        result = adapter._consume_stream(
            iter([{"type": "response.completed", "response": response}]),
            lambda text: None, planner_mode=False,
        )
    else:
        result = adapter.parse_response(response, planner_mode=False)
    trace = extract_llm_token_usage(result, SimpleNamespace(llm_provider="deepseek", llm_model="deepseek-flash"))
    if count is None:
        assert "cacheReadTokens" not in trace
    else:
        assert trace["cacheReadTokens"] == count
        assert result.usage["input_tokens_details"] == usage["input_tokens_details"]


def test_responses_sdk_detail_objects_preserve_usage():
    from openai.types.responses.response_usage import InputTokensDetails, OutputTokensDetails, ResponseUsage

    usage = ResponseUsage(input_tokens=10000, output_tokens=2, total_tokens=10002,
        input_tokens_details=InputTokensDetails(cached_tokens=8832),
        output_tokens_details=OutputTokensDetails(reasoning_tokens=1))
    adapter = DeepSeekResponsesAdapter(api_key="fixture", base_url="https://provider.invalid")
    result = adapter.parse_response(SimpleNamespace(output_text="OK", output=[], usage=usage), planner_mode=False)
    assert result.usage["input_tokens_details"]["cached_tokens"] == 8832
    assert result.usage["output_tokens_details"]["reasoning_tokens"] == 1


@pytest.mark.parametrize("streaming", [False, True])
def test_rejected_tool_preserves_usage_without_accepting_action(streaming):
    response = {"output": [{"type": "function_call", "name": "not_exposed",
        "arguments": '{"action":"reply","reply":"OK"}'}],
        "usage": {"input_tokens": 10000, "output_tokens": 2,
            "input_tokens_details": {"cached_tokens": 8832}}}
    adapter = DeepSeekResponsesAdapter(api_key="fixture", base_url="https://provider.invalid")
    with pytest.raises(RuntimeError, match="unknown planner tool") as caught:
        if streaming:
            adapter._consume_stream(iter([{"type": "response.completed", "response": response}]),
                lambda text: None, planner_mode=True)
        else:
            adapter.parse_response(response)
    assert caught.value.usage["input_tokens_details"]["cached_tokens"] == 8832
    assert not hasattr(caught.value, "response")


def test_runtime_failure_exposes_accountable_usage(monkeypatch):
    import vrchat_blendshape_agent as agent
    from provider_runtime_adapters import ProviderResponseError

    failure = ProviderResponseError("unknown planner tool", {"usage": {
        "input_tokens": 10000, "output_tokens": 2,
        "input_tokens_details": {"cached_tokens": 8832}}})
    calls = []
    def reject(**kwargs):
        calls.append(kwargs)
        raise failure
    monkeypatch.setattr(agent, "execute_provider_protocol_negotiation", reject)
    settings = SimpleNamespace(llm_provider="deepseek", llm_model="deepseek-flash", llm_api_type="responses")
    with pytest.raises(ProviderResponseError) as caught:
        agent.request_llm_plan_with_metadata(settings, "fixture")
    assert len(calls) == 1
    assert caught.value.provider_usage["cacheReadTokens"] == 8832
    assert caught.value.provider_usage["inputTokens"] == 10000
