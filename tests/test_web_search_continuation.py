import pytest
from general_agent_tools import web_search
from tests.test_general_agent_web_tools import FakeClient, FakeResponse


def client():
    html=''.join(f'<a class="result__a" href="https://example.test/{i}">Result {i}</a>' for i in range(13))
    return FakeClient(FakeResponse(html.encode(),headers={'content-type':'text/html'}))


def test_all_admitted_search_results_have_continuation():
    http=client()
    page=web_search('sample',client=http,max_results=5)
    rows=list(page['results'])
    while page['hasMore']:
        request=page['nextRequest']
        page=web_search(request['query'],client=http,max_results=request['maxResults'],
                        offset=request['offset'],snapshot_digest=request['snapshotDigest'])
        rows.extend(page['results'])
    assert [row['title'] for row in rows]==[f'Result {i}' for i in range(13)]
    assert page['nextRequest'] is None


def test_continuation_reads_the_original_snapshot_without_a_new_search():
    http = client()
    first = web_search('dynamic-sample', client=http, max_results=5)
    http.response.content += b'<a class="result__a" href="https://example.test/new">New result</a>'

    request = first['nextRequest']
    page = web_search(request['query'], client=http, max_results=request['maxResults'],
                      offset=request['offset'], snapshot_digest=request['snapshotDigest'])

    assert [row['title'] for row in page['results']] == ['Result 5', 'Result 6', 'Result 7', 'Result 8', 'Result 9']
    assert len(http.calls) == 1


def test_search_continuation_rejects_changed_query_and_uses_cached_source():
    http=client()
    first=web_search('sample',client=http,max_results=5)
    with pytest.raises(ValueError,match='snapshot query changed'):
        web_search('different',client=http,offset=5,snapshot_digest=first['snapshotDigest'])
    http.response.content+=b'<a class="result__a" href="https://example.test/new">New</a>'
    request=first['nextRequest']
    page=web_search(request['query'],client=http,max_results=request['maxResults'],
                    offset=request['offset'],snapshot_digest=request['snapshotDigest'])
    assert [row['title'] for row in page['results']]==['Result 5','Result 6','Result 7','Result 8','Result 9']
    assert len(http.calls)==1


def test_expired_or_unknown_search_snapshot_fails_without_network():
    http=client()
    with pytest.raises(ValueError,match='snapshot expired or unavailable'):
        web_search('unknown-snapshot',client=http,max_results=5,offset=5,snapshot_digest='a'*64)
    assert http.calls==[]

from tests.test_planner_web_evidence import web_adapters, projected


def test_registered_search_adapter_forwards_continuation(web_adapters):
    html=''.join(f'<a class="result__a" href="https://example.test/{i}">Result {i}</a>' for i in range(7))
    first, _, _=projected(web_adapters,'web_search',html,{'query':'sample','maxResults':5})
    second, _, _=projected(web_adapters,'web_search',html,first['nextRequest'])
    assert [row['title'] for row in second['results']]==['Result 5','Result 6']
    assert not second['hasMore']
