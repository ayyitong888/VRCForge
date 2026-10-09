"""A rejected provider batch is evidence, never an executable proposal queue."""
import json

import pytest

from provider_runtime_adapters import ProviderResponseError
from tests.test_deepseek_native_transport import CALL, REASONING, TOOLS, make_adapter
from tests.test_native_runtime_gateway import call, finish, run, setup_gateway


def reject_response(request):
    output = [REASONING, dict(CALL, call_id='allowed', name='load_internal_tool_block'),
              dict(CALL, id='f2', call_id='denied', name='find_files')]
    adapter, _, _ = make_adapter([output])
    try:
        adapter.send_native_request(model='deepseek-flash', messages=request['messages'], tools=request['tools'])
    except ProviderResponseError as exc:
        exc.provider_usage = {'inputTokens': 10000, 'outputTokens': 3,
                              'cacheReadTokens': 8192, 'exact': True}
        raise


def test_rejected_batch_reaches_correction_without_executing_valid_sibling(tmp_path):
    gateway, model, invoked = setup_gateway(tmp_path, [reject_response,
        call('corrected', 'vrcforge_read_text_file', {'path': 'a.txt'}), finish])
    result = run(gateway, tmp_path)
    assert len(model.requests) == 3
    assert len(invoked) == 1
    assert not any(step.get('tool') == 'vrcforge_load_internal_tool_block'
                   and step.get('status') == 'executed' for step in result.get('steps', []))
    feedback = [m for m in model.requests[1]['messages'] if m['role'] == 'tool']
    assert {m['tool_call_id'] for m in feedback} == {'allowed', 'denied'}
    assert all('tool_not_visible' in m['content'] for m in feedback)
    assert model.requests[0]['tools'] == model.requests[1]['tools']
    assert result['contextUsage']['requestCount'] == 3
    assert result['contextUsage']['inputTokens'] == 10000
    assert result['contextUsage']['cacheReadTokens'] == 8192
    assert 'fixture reasoning' not in json.dumps(result)


def test_repeated_rejected_receipts_stop_under_existing_budget(tmp_path):
    gateway, model, invoked = setup_gateway(tmp_path, [reject_response] * 4)
    result = run(gateway, tmp_path)
    assert len(model.requests) == 2
    assert not invoked
    assert result['plan']['nextStep'] == 'planner_failed'


@pytest.mark.parametrize('invalid', [dict(CALL, call_id=''), {'type': 'unsupported'},
    dict(CALL, id='f2', name='call_tool', call_id='c1')])
def test_unknown_name_does_not_hide_other_protocol_errors(invalid):
    adapter, _, _ = make_adapter([[dict(CALL, name='find_files'), invalid]])
    with pytest.raises(ProviderResponseError) as caught:
        adapter.send_native_request(model='deepseek-flash', messages=[], tools=TOOLS)
    assert not hasattr(caught.value, 'rejected_native_receipt')
