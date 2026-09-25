from types import SimpleNamespace

import pytest

from agent_task_loop import AgentTaskLoop, approval_task_context, _bounded_provider_usage, merge_provider_usage
from runtime_planner_service import RuntimePlannerService
from vrchat_blendshape_agent import extract_llm_token_usage


def extracted(usage):
    return extract_llm_token_usage({'usage': usage}, SimpleNamespace(llm_provider='custom', llm_model='fixture'))


def recorded(*caches):
    current = {}
    for cache in caches:
        usage = {'exact': True, 'inputTokens': 100, 'outputTokens': 10, 'totalTokens': 110}
        if cache is not None:
            usage['cacheReadTokens'] = cache
        RuntimePlannerService.record_context_usage(None, current, 'fixture', [], usage)
    return current


@pytest.mark.parametrize('field', ['prompt_tokens_details', 'input_tokens_details'])
def test_extract_nested_cache_without_changing_primary_counts(field):
    usage = extracted({'prompt_tokens': 100, 'completion_tokens': 10, field: {'cached_tokens': 30}})
    assert usage['cacheReadTokens'] == 30
    assert usage['inputTokens'] == 100
    assert usage['totalTokens'] == 110


def test_cache_aliases_not_added_and_explicit_top_level_zero_wins():
    usage = extracted({'prompt_tokens': 100, 'completion_tokens': 10, 'cached_tokens': 0,
                       'prompt_tokens_details': {'cached_tokens': 30}, 'input_tokens_details': {'cached_tokens': 40}})
    assert usage['cacheReadTokens'] == 0


def test_missing_cache_not_zero_and_coverage_is_incomplete():
    usage = recorded(None)
    assert 'cacheReadTokens' not in usage
    assert usage['cacheUsageComplete'] is False
    assert usage['cacheUsageRequestCount'] == 0
    assert usage['exact'] is True


def test_explicit_zero_complete_but_one_missing_request_stays_incomplete():
    assert recorded(0)['cacheUsageComplete'] is True
    usage = recorded(30, None, 0)
    assert usage['cacheUsageComplete'] is False
    assert usage['cacheUsageRequestCount'] == 2
    assert usage['cacheReadTokens'] == 30


def test_approval_seed_preserves_cache_coverage_and_merges_adjacent_phases():
    loop = AgentTaskLoop('fixture', session_id='cache-test')
    seed = loop.approval_seed(requested_tool='vrcforge_apply_shader_tuning', requested_arguments={},
                              provider_request_count=2, provider_usage=recorded(30, 0))
    context = approval_task_context(seed, tool='vrcforge_apply_shader_tuning', arguments={})
    saved = context['providerUsage']
    assert saved['cacheUsageRequestCount'] == 2
    merged = merge_provider_usage(saved, recorded(20))
    assert merged['cacheUsageComplete'] is True
    assert merged['cacheUsageRequestCount'] == 3
    assert merged['cacheReadTokens'] == 50
    assert merged['inputTokens'] == 300


def test_historic_seed_without_coverage_never_implies_complete():
    historical = {'exact': True, 'inputTokens': 100, 'outputTokens': 10, 'totalTokens': 110, 'cacheReadTokens': 0}
    merged = merge_provider_usage(_bounded_provider_usage(historical), recorded(20))
    assert merged['cacheUsageComplete'] is False
    assert merged['cacheUsageRequestCount'] == 1
    assert merge_provider_usage(recorded(30), {})['cacheUsageComplete'] is False


def test_old_in_memory_context_with_cache_zero_is_not_upgraded_to_complete():
    usage = {'exact': True, 'requestCount': 3, 'inputTokens': 300, 'outputTokens': 30, 'totalTokens': 330, 'cacheReadTokens': 0}
    RuntimePlannerService.record_context_usage(None, usage, 'fixture', [], extracted({'prompt_tokens':100,'completion_tokens':10,'cached_tokens':20}))
    assert usage['cacheUsageComplete'] is False
    assert usage['cacheUsageRequestCount'] == 1
