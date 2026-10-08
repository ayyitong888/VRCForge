"""Regression coverage for completed items omitted from the final stream frame."""
from copy import deepcopy
import pytest

from tests.test_chatgpt_responses_adapter import make_adapter, completed

TOOLS = [{'type': 'function', 'function': {'name': 'read', 'parameters': {}}}]
CALL = {'type': 'function_call', 'id': 'fc_1', 'call_id': 'call_1',
        'namespace': 'vrcforge', 'name': 'read', 'arguments': '{}', 'status': 'completed'}
USAGE = {'input_tokens': 65, 'output_tokens': 19, 'total_tokens': 84,
         'input_tokens_details': {'cached_tokens': 32}}

def send(events):
    adapter, client, _ = make_adapter(events)
    try:
        return adapter.send_native_request(model='test', messages=[{'role': 'user', 'content': 'read'}], tools=TOOLS)
    finally:
        assert client.closed == 1

def done(item, index=0):
    return {'type': 'response.output_item.done', 'output_index': index, 'item': item}

def test_sparse_terminal_uses_completed_items_and_preserves_usage():
    events = [done(CALL), completed(usage=USAGE)]
    before = deepcopy(events)
    response = send(events)
    assert response.assistant_message['tool_calls'][0]['id'] == 'call_1'
    assert response.usage['input_tokens_details']['cached_tokens'] == 32
    assert events == before

def test_final_encrypted_reasoning_and_semantic_order_preserved():
    reasoning = {'id': 'rs_1', 'type': 'reasoning', 'encrypted_content': 'final-cipher', 'summary': []}
    response = send([{'type': 'response.output_item.added', 'output_index': 0,
                      'item': {**reasoning, 'encrypted_content': 'partial'}},
                     done(CALL, 1), done(reasoning), completed(usage=USAGE)])
    assert response.assistant_message['responses_output'] == [reasoning, CALL]

@pytest.mark.parametrize('index', [-1, True, '0', 2])
def test_invalid_or_missing_stream_indices_rejected(index):
    with pytest.raises(RuntimeError):
        send([done(CALL, index), completed(usage=USAGE)])

def test_conflicting_duplicate_done_rejected():
    with pytest.raises(RuntimeError):
        send([done(CALL), done({**CALL, 'arguments': '{"different":true}'}), completed([CALL])])

def test_full_terminal_does_not_duplicate_items():
    result = send([done(CALL), completed([CALL], usage=USAGE)])
    assert len(result.assistant_message['tool_calls']) == 1

def test_disagreeing_full_terminal_rejected():
    with pytest.raises(RuntimeError):
        send([done(CALL), completed([{**CALL, 'arguments': '{"different":true}'}], usage=USAGE)])

def test_no_terminal_never_executes_completed_items():
    with pytest.raises(RuntimeError):
        send([done(CALL)])

def test_empty_native_response_failure_keeps_known_usage():
    with pytest.raises(RuntimeError) as error:
        send([completed(usage=USAGE)])
    assert error.value.usage['input_tokens'] == 65
    assert error.value.usage['input_tokens_details']['cached_tokens'] == 32

def test_failed_terminal_keeps_usage_and_never_admits_calls():
    with pytest.raises(RuntimeError) as error:
        send([done(CALL), {'type': 'response.failed', 'response': {
            'status': 'failed', 'output': [], 'usage': USAGE}}])
    assert error.value.usage['output_tokens'] == 19

def test_failed_oauth_usage_reaches_runtime_receipt(tmp_path, monkeypatch):
    from chatgpt_integration.adapter import ChatGPTResponsesAdapter
    from tests.test_native_runtime_gateway import setup_gateway, run
    from dataclasses import replace
    from tests.test_provider_protocol_clients import _native_chat_settings
    from vrchat_blendshape_agent import request_llm_plan_with_metadata
    settings = replace(_native_chat_settings('responses'), llm_provider='chatgpt',
                       llm_api_key='fixture-account', llm_base_url='https://api.openai.com/v1')
    monkeypatch.setattr(ChatGPTResponsesAdapter, '_stream_response',
                        lambda *args, **kwargs: {'output': [], 'status': 'completed', 'usage': USAGE})
    def fail(request):
        request_llm_plan_with_metadata(settings, '', native_messages=request['messages'], native_tools=request['tools'])
    gateway, model, invoked = setup_gateway(tmp_path, [fail])
    result = run(gateway, tmp_path)
    assert not invoked and len(model.requests) == 1
    assert result['task']['status'] == 'planner_failed'
    usage = result['contextUsage']
    assert usage['requestCount'] == 1
    assert usage['inputTokens'] == 65 and usage['outputTokens'] == 19
    assert usage['cacheReadTokens'] == 32 and usage['exact'] is True
