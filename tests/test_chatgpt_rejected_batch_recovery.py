"""OAuth denials must reach correction without admitting any sibling calls."""
from dataclasses import replace

import pytest

from chatgpt_integration.adapter import ChatGPTResponsesAdapter
from tests.test_native_runtime_gateway import call, finish, run, setup_gateway
from tests.test_provider_protocol_clients import _native_chat_settings
from vrchat_blendshape_agent import request_llm_plan_with_metadata


@pytest.fixture
def rejected_oauth(monkeypatch):
    settings = replace(_native_chat_settings('responses'), llm_provider='chatgpt',
                       llm_api_key='fixture-account', llm_base_url='https://api.openai.com/v1')
    output = [{'type': 'function_call', 'namespace': 'vrcforge',
               'call_id': identifier, 'name': name, 'arguments': '{}'}
              for identifier, name in [('allowed', 'load_internal_tool_block'),
                                       ('denied', 'find_files')]]
    monkeypatch.setattr(ChatGPTResponsesAdapter, '_stream_response',
        lambda *args, **kwargs: {'status': 'completed', 'output': output,
            'usage': {'input_tokens': 10000, 'output_tokens': 3,
                      'input_tokens_details': {'cached_tokens': 8192}}})

    def request(payload):
        return request_llm_plan_with_metadata(settings, '',
            native_messages=payload['messages'], native_tools=payload['tools'])

    return request, output


def test_oauth_denial_corrects_whole_batch_and_counts_usage_once(tmp_path, rejected_oauth):
    request, _ = rejected_oauth
    gateway, model, invoked = setup_gateway(tmp_path, [request,
        call('corrected', 'vrcforge_read_text_file', {'path': 'a.txt'}), finish])
    result = run(gateway, tmp_path)
    assert len(model.requests) == 3
    assert len(invoked) == 1
    feedback = [m for m in model.requests[1]['messages'] if m['role'] == 'tool']
    assert {m['tool_call_id'] for m in feedback} == {'allowed', 'denied'}
    assert all('tool_not_visible' in m['content'] for m in feedback)
    assert model.requests[0]['tools'] == model.requests[1]['tools']
    assert result['contextUsage']['requestCount'] == 3
    assert result['contextUsage']['inputTokens'] == 10000
    assert result['contextUsage']['cacheReadTokens'] == 8192


def test_oauth_repeated_denials_keep_existing_stop_budget(tmp_path, rejected_oauth):
    request, _ = rejected_oauth
    gateway, model, invoked = setup_gateway(tmp_path, [request] * 4)
    result = run(gateway, tmp_path)
    assert len(model.requests) == 2
    assert not invoked
    assert result['plan']['nextStep'] == 'planner_failed'


@pytest.mark.parametrize('invalid', [
    {'type': 'unsupported'},
    {'type': 'function_call', 'namespace': 'other', 'name': 'call_tool',
     'call_id': 'bad', 'arguments': '{}'},
    {'type': 'reasoning', 'summary': []},
])
def test_oauth_other_protocol_errors_remain_fatal(tmp_path, rejected_oauth, invalid):
    request, output = rejected_oauth
    output.append(invalid)
    gateway, model, invoked = setup_gateway(tmp_path, [request] * 4)
    result = run(gateway, tmp_path)
    assert len(model.requests) == 1
    assert not invoked
    assert result['plan']['nextStep'] == 'planner_failed'
