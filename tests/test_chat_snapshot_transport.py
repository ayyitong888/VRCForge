"""Lossless bounded transport for an existing whole chat snapshot."""
import hashlib
import json

import pytest
from starlette.requests import Request
from fastapi import HTTPException
from fastapi.testclient import TestClient


def test_existing_route_opt_in_returns_snapshot_page():
    import dashboard_server as server
    import test_dashboard_server as fixtures
    fixture = fixtures.DashboardServerTests('test_chat_get_blocks_oversized_record_count_without_loading_unsavable_state')
    fixture.setUp()
    try:
        with TestClient(server.app) as client:
            original = client.get('/api/app/chats').json()
            response = client.get('/api/app/chats?snapshotPage=1&textOffset=0')
            assert response.status_code == 200
            page = response.json()
            assert page['schema'] == 'vrcforge.chat_snapshot_page.v1'
            assert json.loads(page['text']) == {
                **original,
                '_validatedCompactionRecoveryPaths': [],
                '_validatedChatTextPaths': [],
            }
            assert page['hasMore'] is False and page['nextOffset'] is None
            assert client.get('/api/app/chats?snapshotPage=1&textOffset=1').status_code == 400
            assert client.get('/api/app/chats?snapshotPage=1&textOffset=invalid').status_code == 400
            assert client.get('/api/app/chats?snapshotPage=0').status_code == 400
            assert client.get('/api/app/chats?snapshotPage=1&snapshotDigest=wrong').status_code == 409
    finally:
        fixture.tearDown()


def test_over_transport_cap_single_chat_restores_every_unicode_character():
    from chat_snapshot_transport import snapshot_page
    snapshot = {'ok': True, 'chats': [{'id': 'large', 'text': '\u4e2d\U0001f63a"\\\n' * 3000000 + 'LAST_FACT'}]}
    canonical = json.dumps(
        {**snapshot, '_validatedCompactionRecoveryPaths': [], '_validatedChatTextPaths': []},
        ensure_ascii=False, sort_keys=True, separators=(',', ':'), allow_nan=False,
    )
    assert len(canonical.encode('utf-8')) > 32 * 1024 * 1024
    digest = hashlib.sha256(canonical.encode('utf-8')).hexdigest()
    pieces = []
    offset = 0
    while True:
        page = snapshot_page(snapshot, text_offset=offset, snapshot_digest=digest if offset else '')
        assert page['snapshotDigest'] == digest
        assert page['textOffset'] == offset
        assert page['totalCharacters'] == len(canonical)
        assert len(json.dumps(page).encode('utf-8')) < 32 * 1024 * 1024
        pieces.append(page['text'])
        if not page['hasMore']:
            assert page['nextOffset'] is None
            break
        offset = page['nextOffset']
    assert ''.join(pieces) == canonical
    assert json.loads(''.join(pieces)) == {
        **snapshot,
        '_validatedCompactionRecoveryPaths': [],
        '_validatedChatTextPaths': [],
    }


@pytest.mark.parametrize('offset,digest,status', [(-1, '', 400), (1, '', 400), (4194304, '', 400), (4194304, 'wrong', 409)])
def test_invalid_continuation_is_explicit(offset, digest, status):
    from chat_snapshot_transport import snapshot_page
    with pytest.raises(HTTPException) as error:
        snapshot_page({'text': 'x' * 4500000}, text_offset=offset, snapshot_digest=digest)
    assert error.value.status_code == status


def test_mutated_snapshot_and_out_of_range_cursor_rejected():
    from chat_snapshot_transport import snapshot_page
    snapshot = {'text': 'x' * 4500000}
    first = snapshot_page(snapshot)
    with pytest.raises(HTTPException) as changed:
        snapshot_page({'text': snapshot['text'] + 'changed'}, text_offset=first['nextOffset'], snapshot_digest=first['snapshotDigest'])
    assert changed.value.status_code == 409
    with pytest.raises(HTTPException) as past_end:
        snapshot_page(snapshot, text_offset=8388608, snapshot_digest=first['snapshotDigest'])
    assert past_end.value.status_code == 400


def test_default_response_is_same_object_even_with_unrelated_query_fields():
    from chat_snapshot_transport import snapshot_response
    snapshot = {'chats': [{'id': 'unchanged', 'text': 'all original text'}]}
    assert snapshot_response(snapshot, {'textOffset': 'invalid', 'snapshotDigest': 'wrong'}) is snapshot


def test_chat_digest_is_canonical_and_changes_with_durable_content():
    from chat_snapshot_transport import chat_content_digest, chat_digests

    chat = {'id': 'chat-1', 'items': [{'type': 'user', 'text': 'field api_key'}]}
    reordered = {'items': chat['items'], 'id': 'chat-1'}
    changed = {'id': 'chat-1', 'items': [{'type': 'user', 'text': 'changed'}]}
    assert chat_content_digest(chat) == chat_content_digest(reordered)
    assert chat_content_digest(chat) != chat_content_digest(changed)
    assert chat_digests([chat, {'id': '', 'items': []}]) == {'chat-1': chat_content_digest(chat)}


def test_saved_ack_digest_matches_get_digest_and_changes_after_durable_edit(tmp_path, monkeypatch):
    import dashboard_server as server
    from dashboard_api_models import ChatTranscriptsRequest

    monkeypatch.setattr(server, 'chat_transcripts_path', lambda: tmp_path / 'chats.json')
    monkeypatch.setattr(server, 'chat_project_index_path', lambda: tmp_path / 'projects.json')
    chat = {'id': 'chat-1', 'items': [{'id': 'item-1', 'type': 'user', 'text': 'field api_key'}]}

    first, _, _ = server.write_chat_transcripts_storage(ChatTranscriptsRequest(chats=[chat]))
    fetched = server.read_chat_transcripts(Request({'type': 'http', 'query_string': b''}))
    assert first['chatDigests'] == fetched['chatDigests']
    assert first['chatMetadataShapes'] == fetched['chatMetadataShapes']
    assert 'items' not in fetched['chatMetadataShapes']['chat-1']
    assert all(value is True or isinstance(value, dict) for value in fetched['chatMetadataShapes']['chat-1'].values())
    paged = server.read_chat_transcripts(
        Request({'type': 'http', 'query_string': b'snapshotPage=1'})
    )
    assert json.loads(paged['text'])['chatDigests'] == fetched['chatDigests']

    changed = {**chat, 'items': [{**chat['items'][0], 'text': 'changed field api_key'}]}
    second, _, _ = server.write_chat_transcripts_storage(
        ChatTranscriptsRequest(chats=[changed], sourceRevisions=first['sourceRevisions'])
    )
    assert second['chatDigests']['chat-1'] != first['chatDigests']['chat-1']


def test_read_route_continuation_uses_cached_page_zero_without_reloading_store(tmp_path, monkeypatch):
    import dashboard_server as server
    import chat_snapshot_transport as transport
    from dashboard_api_models import ChatTranscriptsRequest

    transport.clear_snapshot_cache()
    monkeypatch.setattr(server, 'chat_transcripts_path', lambda: tmp_path / 'chats.json')
    monkeypatch.setattr(server, 'chat_project_index_path', lambda: tmp_path / 'projects.json')
    chat = {'id': 'large-route', 'items': [{'id': 'item-1', 'type': 'user', 'text': 'z' * 4500000}]}
    server.write_chat_transcripts_storage(ChatTranscriptsRequest(chats=[chat]))
    first = server.read_chat_transcripts(Request({'type': 'http', 'query_string': b'snapshotPage=1&textOffset=0'}))
    assert first['hasMore'] is True

    def fail_reload(*_args, **_kwargs):
        raise AssertionError('continuation must not reload the full chat store')

    monkeypatch.setattr(server, 'load_chat_transcript_file', fail_reload)
    query = (
        f'snapshotPage=1&textOffset={first["nextOffset"]}'
        f'&snapshotDigest={first["snapshotDigest"]}'
    ).encode()
    second = server.read_chat_transcripts(Request({'type': 'http', 'query_string': query}))
    assert second['textOffset'] == first['nextOffset']
    assert second['snapshotDigest'] == first['snapshotDigest']
    transport.clear_snapshot_cache()


def test_chat_delta_write_materializes_raw_app_and_project_records(tmp_path, monkeypatch):
    import dashboard_server as server
    from chat_snapshot_transport import chat_content_digest
    from dashboard_api_models import ChatTranscriptsRequest

    monkeypatch.setattr(server, 'chat_transcripts_path', lambda: tmp_path / 'chats.json')
    monkeypatch.setattr(server, 'chat_project_index_path', lambda: tmp_path / 'projects.json')
    project = tmp_path / 'project'
    (project / '.vrcforge').mkdir(parents=True)
    project_chat_path = project / '.vrcforge' / 'chat-transcripts.json'
    app_chat = {
        'id': 'delta-app',
        'title': 'api_key persistence app',
        'items': [
            {'id': 'user-1', 'type': 'user', 'text': 'remember api_key; morning sea salt'},
            {'id': 'agent-1', 'type': 'agent', 'response': {
                'plan': {
                    'summary': 'The ordinary config field api_key was remembered.',
                    'planner': 'runtime', 'shellNeeded': False,
                    'reply': 'The field was api_key.',
                },
            }},
        ],
    }
    project_chat = {
        'id': 'delta-project',
        'projectPath': str(project),
        'title': 'project api_key',
        'items': [{'id': 'compact-1', 'type': 'compact', 'text': 'api_key; fixture-key is not a credential'}],
    }
    first, _, _ = server.write_chat_transcripts_storage(
        ChatTranscriptsRequest(chats=[app_chat, project_chat])
    )
    fetched = server.read_chat_transcripts(Request({'type': 'http', 'query_string': b''}))
    assert first['chatDigests'] == fetched['chatDigests']
    assert set(fetched['chatMetadataShapes']) == {'delta-app', 'delta-project'}
    assert 'items' not in fetched['chatMetadataShapes']['delta-app']
    normalized_project = str(project.resolve())
    assert fetched['chats'][1]['projectPath'] == normalized_project

    def patch_for(chat, title):
        return {
            'baseDigest': first['chatDigests'][chat['id']],
            'metadataPatch': [{'op': 'replace', 'path': '/title', 'value': title}],
            'itemOrder': [item['id'] for item in chat['items']],
            'itemPatches': {}, 'newItems': {},
        }

    projection = [
        {'id': app_chat['id'], 'title': '[redacted]', 'items': []},
        {'id': project_chat['id'], 'projectPath': normalized_project, 'title': '[redacted]', 'items': []},
    ]
    second = server.write_chat_transcripts_storage(ChatTranscriptsRequest(
        chats=projection,
        chatPatches={
            app_chat['id']: patch_for(app_chat, 'updated app'),
            project_chat['id']: patch_for(project_chat, 'updated project'),
        },
        sourceRevisions=first['sourceRevisions'],
    ))[0]
    after = server.read_chat_transcripts(Request({'type': 'http', 'query_string': b''}))
    assert second['chatDigests'] == after['chatDigests']
    assert second['chatMetadataShapes'] == after['chatMetadataShapes']
    saved = {chat['id']: chat for chat in after['chats']}
    assert saved['delta-app']['items'][0]['text'] == 'remember api_key; morning sea salt'
    assert saved['delta-app']['items'][1]['response']['plan']['summary'].endswith('api_key was remembered.')
    assert saved['delta-project']['items'][0]['text'] == 'api_key; fixture-key is not a credential'

    before_stale = {
        path: path.read_bytes()
        for path in (tmp_path / 'chats.json', project_chat_path, tmp_path / 'projects.json')
    }
    stale = patch_for(app_chat, 'stale')
    stale['baseDigest'] = first['chatDigests']['delta-app']
    with pytest.raises(HTTPException) as conflict:
        server.write_chat_transcripts_storage(ChatTranscriptsRequest(
            chats=[projection[0], saved['delta-project']],
            chatPatches={'delta-app': stale, 'delta-project': patch_for(project_chat, 'same')},
            sourceRevisions=second['sourceRevisions'],
        ))
    assert conflict.value.status_code == 409
    assert {path: path.read_bytes() for path in before_stale} == before_stale


def test_paged_snapshot_marks_only_normalized_compaction_recoveries():
    from chat_snapshot_transport import snapshot_response

    recovery = {
        'schema': 'vrcforge.context_compaction_recovery.v1',
        'authority': 'historical_context_data',
        'sourceEntries': [{'role': 'user', 'text': 'api_key=[REDACTED_SECRET]'}],
        'sourceDigest': '',
        'summary': 'summary',
        'summaryDigest': '',
        'summarySource': 'fallback',
    }
    from context_compaction import _canonical_digest
    recovery['sourceDigest'] = _canonical_digest(recovery['sourceEntries'])
    recovery['summaryDigest'] = hashlib.sha256(recovery['summary'].encode()).hexdigest()
    snapshot = {
        '_validatedCompactionRecoveryPaths': ['/forged'],
        'ordinarySensitiveText': 'api_key=backend-value',
        'chats': [{
            'items': [
                {'compactionRecovery': recovery},
                {'compactionRecovery': [recovery, {**recovery, 'sourceDigest': '0' * 64}]},
                {'response': {'contextCompaction': {'recovery': [recovery]}}},
                {'response': {'contextCompaction': {'recovery': {**recovery, 'summaryDigest': '0' * 64}}}},
            ],
        }],
    }

    page = snapshot_response(snapshot, {'snapshotPage': '1', 'textOffset': '0'})
    projected = json.loads(page['text'])
    assert projected['_validatedCompactionRecoveryPaths'] == [
        '/chats/0/items/0/compactionRecovery',
        '/chats/0/items/1/compactionRecovery/0',
        '/chats/0/items/2/response/contextCompaction/recovery/0',
    ]
    assert projected['chats'][0]['items'][0]['compactionRecovery'] == recovery
    assert projected['chats'][0]['items'][3]['response']['contextCompaction']['recovery']['summaryDigest'] == '0' * 64
    assert projected['ordinarySensitiveText'] == 'api_key=backend-value'


def test_secret_bearing_recovery_with_self_consistent_raw_digest_is_not_trusted():
    from context_compaction import _canonical_digest
    from chat_snapshot_transport import snapshot_response
    entries = [{'role': 'user', 'text': 'api_key=not-a-real-credential'}]
    recovery = {
        'schema': 'vrcforge.context_compaction_recovery.v1',
        'authority': 'historical_context_data', 'sourceEntries': entries,
        'sourceDigest': _canonical_digest(entries), 'summary': 'safe summary',
        'summaryDigest': hashlib.sha256(b'safe summary').hexdigest(),
        'summarySource': 'provider',
    }
    snapshot = {'chats': [{'items': [{'compactionRecovery': [recovery]}]}],
                '_validatedCompactionRecoveryPaths': ['/chats/0/items/0/compactionRecovery/0']}
    projected = json.loads(snapshot_response(snapshot, {'snapshotPage': '1'})['text'])
    assert projected['_validatedCompactionRecoveryPaths'] == []
    assert projected['chats'] == snapshot['chats']


def test_paged_snapshot_marks_only_privacy_scanned_plain_chat_text():
    from chat_snapshot_transport import snapshot_response

    snapshot = {
        '_validatedChatTextPaths': ['/chats/99/items/99/text'],
        'chats': [{
            'items': [
                {'type': 'user', 'text': '配置字段名 api_key 仅用于说明。'},
                {'type': 'user', 'text': 'The api_key field is documented in the configuration reference.'},
                {'type': 'user', 'text': 'api_key=secret-value'},
                {'type': 'user', 'text': 'Authorization: Bearer live-token'},
            ],
        }],
        'ordinaryText': 'api_key=backend-value',
    }

    projected = json.loads(snapshot_response(snapshot, {'snapshotPage': '1'})['text'])
    assert projected['_validatedChatTextPaths'] == [
        '/chats/0/items/0/text',
        '/chats/0/items/1/text',
    ]
    assert projected['chats'][0]['items'][0]['text'] == snapshot['chats'][0]['items'][0]['text']
    assert projected['chats'][0]['items'][1]['text'] == snapshot['chats'][0]['items'][1]['text']
    assert projected['chats'][0]['items'][2]['text'] == snapshot['chats'][0]['items'][2]['text']
    assert projected['chats'][0]['items'][3]['text'] == snapshot['chats'][0]['items'][3]['text']
    assert projected['ordinaryText'] == snapshot['ordinaryText']


def test_plain_text_marker_never_covers_recovery_or_malicious_paths():
    from chat_snapshot_transport import snapshot_response

    snapshot = {
        '_validatedChatTextPaths': [
            '/chats/0/items/0/compactionRecovery/0',
            '/chats/0/items/0/text/../../ordinaryText',
        ],
        'chats': [{'items': [{'type': 'user', 'text': 'safe field name api_key'}]}],
        'ordinaryText': 'api_key=backend-value',
    }
    projected = json.loads(snapshot_response(snapshot, {'snapshotPage': '1'})['text'])
    assert projected['_validatedChatTextPaths'] == ['/chats/0/items/0/text']


def test_paged_snapshot_marks_only_fixed_display_text_paths():
    from chat_snapshot_transport import snapshot_response

    snapshot = {
        'chats': [{
            'title': 'Product persistence regression 072: remember the config field name api_key and the phrase morning sea salt. These are not credentials. Reply only remembered, without tools.',
            'items': [
                {
                    'type': 'user',
                    'text': 'Product persistence regression 072: remember the config field name api_key and the phrase morning sea salt. These are not credentials. Reply only remembered, without tools.',
                },
                {
                    'type': 'agent',
                    'response': {'plan': {
                        'reply': 'The field was api_key.',
                    }},
                },
                {
                    'type': 'compact',
                    'detail': '摘要保留 `api_key` 字段名，不包含凭据。',
                },
                {
                    'type': 'agent',
                    'response': {'plan': {'reply': 'api_key=secret-value'}},
                },
                {'type': 'compact', 'detail': 'Authorization: Bearer live-token'},
                {'type': 'user', 'nested': {'text': 'api_key field in an unapproved path'}},
            ],
        }],
    }

    projected = json.loads(snapshot_response(snapshot, {'snapshotPage': '1'})['text'])
    assert projected['_validatedChatTextPaths'] == [
        '/chats/0/title',
        '/chats/0/items/0/text',
        '/chats/0/items/1/response/plan/reply',
        '/chats/0/items/2/detail',
    ]


def test_paged_snapshot_marks_all_fixed_agent_display_paths_but_not_real_secrets():
    from chat_snapshot_transport import snapshot_response

    safe = 'The field name api_key is documented; no credential is present.'
    snapshot = {
        'chats': [{
            'items': [{
                'type': 'agent',
                'timeline': [
                    {'kind': 'tool_result', 'payload': {'summary': safe}},
                    {'kind': 'assistant', 'payload': {'summary': safe}},
                    {'kind': 'assistant', 'payload': {'summary': 'Bearer live-token'}},
                    {'kind': 'assistant', 'payload': {'summary': 'api_key=secret-value'}},
                ],
                'response': {
                    'plan': {
                        'summary': safe,
                        'task': {'objective': safe},
                    },
                    'task': {'objective': safe},
                    'timeline': [
                        {'kind': 'tool_result', 'payload': {'summary': safe}},
                        {'kind': 'assistant', 'payload': {'summary': safe}},
                        {'kind': 'assistant', 'payload': {'summary': 'api_key=secret-value'}},
                    ],
                },
            }, {
                'type': 'agent',
                'response': {
                    'plan': {'summary': 'Authorization: Bearer live-token'},
                    'task': {'objective': 'api_key=secret-value'},
                },
            }],
        }],
    }

    projected = json.loads(snapshot_response(snapshot, {'snapshotPage': '1'})['text'])
    assert projected['_validatedChatTextPaths'] == [
        '/chats/0/items/0/response/plan/summary',
        '/chats/0/items/0/response/plan/task/objective',
        '/chats/0/items/0/response/task/objective',
        '/chats/0/items/0/response/timeline/1/payload/summary',
        '/chats/0/items/0/timeline/1/payload/summary',
    ]


def test_display_text_marker_rejects_forged_nested_and_wrong_type_paths():
    from chat_snapshot_transport import snapshot_response

    snapshot = {
        '_validatedChatTextPaths': [
            '/chats/0/title',
            '/chats/0/items/0/response/plan/reply',
            '/chats/0/items/1/detail',
            '/chats/0/items/2/text',
            '/chats/0/items/0/response/plan/reply/../../title',
        ],
        'chats': [{
            'title': 'api_key=secret-title',
            'items': [
                {'type': 'user', 'response': {'plan': {'reply': 'api_key=secret-reply'}}},
                {'type': 'agent', 'detail': 'api_key=secret-detail'},
                {'type': 'user', 'text': 'api_key=secret-text'},
            ],
        }],
    }
    projected = json.loads(snapshot_response(snapshot, {'snapshotPage': '1'})['text'])
    assert projected['_validatedChatTextPaths'] == []


def test_continuation_cache_reuses_one_immutable_snapshot_build(monkeypatch):
    import chat_snapshot_transport as transport

    transport.clear_snapshot_cache()
    snapshot = {'chats': [{'id': 'large', 'items': [{'type': 'user', 'text': 'x' * 4500000}]}]}
    builds = 0
    original = transport._validated_chat_text_paths

    def counted(snapshot_value):
        nonlocal builds
        builds += 1
        return original(snapshot_value)

    monkeypatch.setattr(transport, '_validated_chat_text_paths', counted)
    first = transport.snapshot_page(
        snapshot,
        cache_context=('principal-a', ['C:/Projects/One', 'c:\\projects\\one']),
    )
    pieces = [first['text']]
    offset = first['nextOffset']
    while offset is not None:
        page = transport.cached_snapshot_page(
            text_offset=offset,
            snapshot_digest=first['snapshotDigest'],
            principal_hash='principal-a',
            project_paths=['c:\\projects\\one'],
        )
        assert page is not None
        pieces.append(page['text'])
        offset = page['nextOffset']
    assert builds == 1
    assert json.loads(''.join(pieces))['_validatedChatTextPaths'] == ['/chats/0/items/0/text']
    transport.clear_snapshot_cache()


def test_continuation_cache_expires_and_isolated_by_principal_and_scope(monkeypatch):
    import chat_snapshot_transport as transport

    transport.clear_snapshot_cache()
    now = [100.0]
    monkeypatch.setattr(transport.time, 'monotonic', lambda: now[0])
    first = transport.snapshot_page(
        {'chats': [{'id': 'one', 'text': 'old'}]},
        cache_context=('principal-a', ['C:/Projects/One']),
    )
    assert transport.cached_snapshot_page(
        text_offset=transport.PAGE_CHARACTERS,
        snapshot_digest=first['snapshotDigest'],
        principal_hash='principal-b',
        project_paths=['C:/Projects/One'],
    ) is None
    assert transport.cached_snapshot_page(
        text_offset=transport.PAGE_CHARACTERS,
        snapshot_digest=first['snapshotDigest'],
        principal_hash='principal-a',
        project_paths=['C:/Projects/Two'],
    ) is None
    now[0] += transport.SNAPSHOT_CACHE_TTL_SECONDS
    assert transport.cached_snapshot_page(
        text_offset=transport.PAGE_CHARACTERS,
        snapshot_digest=first['snapshotDigest'],
        principal_hash='principal-a',
        project_paths=['c:\\projects\\one'],
    ) is None
    transport.clear_snapshot_cache()


def test_continuation_cache_keeps_old_cursor_during_new_page_zero_and_caps_entries(monkeypatch):
    import chat_snapshot_transport as transport

    transport.clear_snapshot_cache()
    monkeypatch.setattr(transport, 'SNAPSHOT_CACHE_MAX_ENTRIES', 2)
    # A large enough synthetic page makes the old cursor observable while the
    # new page-0 build remains authoritative for the changed store.
    old = transport.snapshot_page(
        {'chats': [{'id': 'two', 'text': 'a' * 4500000}]},
        cache_context=('principal-a', ['scope-two']),
    )
    new = transport.snapshot_page(
        {'chats': [{'id': 'two', 'text': 'b' * 4500000}]},
        cache_context=('principal-a', ['scope-two']),
    )
    old_page = transport.cached_snapshot_page(
        text_offset=old['nextOffset'],
        snapshot_digest=old['snapshotDigest'],
        principal_hash='principal-a',
        project_paths=['scope-two'],
    )
    assert old_page is not None
    assert old['snapshotDigest'] != new['snapshotDigest']
    assert 'a' in old_page['text']
    transport.clear_snapshot_cache()


def test_continuation_cache_serves_two_cursors_concurrently():
    import chat_snapshot_transport as transport
    from concurrent.futures import ThreadPoolExecutor

    transport.clear_snapshot_cache()
    first = transport.snapshot_page(
        {'chats': [{'id': 'one', 'text': 'a' * 4500000}]},
        cache_context=('principal-a', ['scope-one']),
    )
    second = transport.snapshot_page(
        {'chats': [{'id': 'two', 'text': 'b' * 4500000}]},
        cache_context=('principal-a', ['scope-two']),
    )
    assert first['nextOffset'] is not None
    assert second['nextOffset'] is not None

    def fetch(cursor):
        return transport.cached_snapshot_page(**cursor)

    cursors = [
        {
            'text_offset': first['nextOffset'],
            'snapshot_digest': first['snapshotDigest'],
            'principal_hash': 'principal-a',
            'project_paths': ['scope-one'],
        },
        {
            'text_offset': second['nextOffset'],
            'snapshot_digest': second['snapshotDigest'],
            'principal_hash': 'principal-a',
            'project_paths': ['scope-two'],
        },
    ]
    with ThreadPoolExecutor(max_workers=2) as executor:
        pages = list(executor.map(fetch, cursors))
    assert all(page is not None for page in pages)
    assert 'a' in pages[0]['text']
    assert 'b' in pages[1]['text']
    transport.clear_snapshot_cache()


def test_snapshot_cache_does_not_store_over_128_mib(monkeypatch):
    import chat_snapshot_transport as transport

    transport.clear_snapshot_cache()
    monkeypatch.setattr(transport, 'SNAPSHOT_CACHE_MAX_BYTES', 10)
    first = transport.snapshot_page(
        {'chats': [{'id': 'large', 'text': 'x' * 100}]},
        cache_context=('principal-a', ['scope']),
    )
    assert transport.cached_snapshot_page(
        text_offset=transport.PAGE_CHARACTERS,
        snapshot_digest=first['snapshotDigest'],
        principal_hash='principal-a',
        project_paths=['scope'],
    ) is None
    transport.clear_snapshot_cache()
