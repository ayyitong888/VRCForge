import json
from urllib.parse import urlencode
from unittest.mock import patch as mock_patch
import pytest
from fastapi import HTTPException
from starlette.requests import Request
from chat_snapshot_transport import chat_digests, snapshot_response


def fixture_snapshot():
    chats = [{'id': 'old', 'items': [{'id': 'item-1', 'type': 'user', 'text': 'x' * 1000000}]}, {'id': 'edited', 'items': [{'id': 'item-1', 'type': 'user', 'text': 'new'}]}]
    return {'ok': True, 'chats': chats, 'chatDigests': chat_digests(chats), 'chatMetadataShapes': {'old': {}, 'edited': {}}, 'count': 2, 'sources': [{'digest': 'revision'}], 'writeBlocked': False}


def test_incremental_refresh_transmits_changed_only_and_complete_manifest():
    snapshot = fixture_snapshot()
    query = {'knownChatDigests': json.dumps({'old': snapshot['chatDigests']['old'], 'deleted': 'a' * 64})}
    result = snapshot_response(snapshot, query)
    assert result['incremental'] is True
    assert result['chatManifest']['ids'] == ['old', 'edited']
    assert result['chatManifest']['complete'] is True
    assert result['chatManifest']['schema'] == 'vrcforge.chat_manifest.v1'
    assert [c['id'] for c in result['chats']] == ['edited']
    assert result['count'] == 2
    assert result['chatDigests'] == snapshot['chatDigests']
    assert result['sources'] == snapshot['sources']
    assert len(json.dumps(result)) < 2000
    assert len(snapshot['chats']) == 2


@pytest.mark.parametrize('known', ['[]', 'null', '{bad', '{"old":"not-a-digest"}'])
def test_invalid_known_digests_fail_closed(known):
    with pytest.raises(HTTPException) as error:
        snapshot_response(fixture_snapshot(), {'knownChatDigests': known})
    assert error.value.status_code == 400


def test_incomplete_server_manifest_is_never_announced_complete():
    snapshot = fixture_snapshot()
    del snapshot['chatDigests']['old']
    with pytest.raises(HTTPException):
        snapshot_response(snapshot, {'knownChatDigests': '{}'})


def test_incremental_paged_projection_validates_paths_after_filtering():
    snapshot = fixture_snapshot()
    result = json.loads(snapshot_response(snapshot, {'snapshotPage': '1', 'knownChatDigests': json.dumps({'old': snapshot['chatDigests']['old']})})['text'])
    assert result['chatManifest']['ids'] == ['old', 'edited']
    assert result['_validatedChatTextPaths'] == ['/chats/0/items/0/text']
    assert result['chats'][0]['id'] == 'edited'


def test_read_route_incremental_preserves_full_sources_and_deletion_manifest(tmp_path, monkeypatch):
    import dashboard_server as server
    from dashboard_api_models import ChatTranscriptsRequest
    monkeypatch.setattr(server, 'chat_transcripts_path', lambda: tmp_path / 'chats.json')
    monkeypatch.setattr(server, 'chat_project_index_path', lambda: tmp_path / 'projects.json')
    server.write_chat_transcripts_storage(ChatTranscriptsRequest(chats=fixture_snapshot()['chats']))
    first = server.read_chat_transcripts(Request({'type': 'http', 'query_string': b''}))
    query = urlencode({'knownChatDigests': json.dumps(first['chatDigests'])}).encode()
    result = server.read_chat_transcripts(Request({'type': 'http', 'query_string': query}))
    assert result['chats'] == []
    assert result['chatManifest']['ids'] == [c['id'] for c in first['chats']]
    assert result['sources'] == first['sources']


def test_incremental_continuation_remains_bound_to_original_manifest(monkeypatch):
    from starlette.datastructures import QueryParams
    import chat_snapshot_transport as transport
    monkeypatch.setattr(transport, 'PAGE_CHARACTERS', 120)
    transport.clear_snapshot_cache()
    snapshot = fixture_snapshot()
    query = QueryParams({'snapshotPage': '1', 'projectPath': 'C:/project', 'knownChatDigests': json.dumps({'old': snapshot['chatDigests']['old']})})
    page = transport.snapshot_response(snapshot, query, cache_context=('principal', ['C:/project']))
    pieces = [page['text']]
    while page['hasMore']:
        page = transport.cached_snapshot_page(text_offset=page['nextOffset'], snapshot_digest=page['snapshotDigest'], principal_hash='principal', project_paths=['C:/project'])
        assert page is not None
        pieces.append(page['text'])
    assembled = json.loads(''.join(pieces))
    assert assembled['chatManifest']['ids'] == ['old', 'edited']
    assert [c['id'] for c in assembled['chats']] == ['edited']
    assert transport.cached_snapshot_page(text_offset=120, snapshot_digest=page['snapshotDigest'], principal_hash='other', project_paths=['C:/project']) is None
    assert transport.cached_snapshot_page(text_offset=120, snapshot_digest=page['snapshotDigest'], principal_hash='principal', project_paths=['C:/other']) is None
    transport.clear_snapshot_cache()


def test_id_only_save_patch_preserves_raw_record_and_cas(tmp_path, monkeypatch):
    import dashboard_server as server
    from dashboard_api_models import ChatTranscriptsRequest
    monkeypatch.setattr(server, 'chat_transcripts_path', lambda: tmp_path / 'chats.json')
    monkeypatch.setattr(server, 'chat_project_index_path', lambda: tmp_path / 'projects.json')
    chat = {'id': 'chat', 'title': 'before', 'privateMetadata': {'preserve': 'value'}, 'items': [{'id': 'item', 'type': 'user', 'text': 'preserve raw history'}]}
    ack, _, _ = server.write_chat_transcripts_storage(ChatTranscriptsRequest(chats=[chat]))
    patch = {'baseDigest': ack['chatDigests']['chat'], 'metadataPatch': [{'op':'replace','path':'/title','value':'after'}], 'itemOrder':['item'], 'itemPatches':{}, 'newItems':{}}
    request = ChatTranscriptsRequest(chats=[{'id': 'chat'}], sourceRevisions=ack['sourceRevisions'], chatPatches={'chat':patch})
    server.write_chat_transcripts_storage(request)
    loaded = server.read_chat_transcripts(Request({'type':'http','query_string':b''}))['chats'][0]
    assert loaded['title'] == 'after'
    assert loaded['items'] == chat['items']
    assert loaded['privateMetadata'] == chat['privateMetadata']
    with pytest.raises(HTTPException) as stale:
        server.write_chat_transcripts_storage(request)
    assert stale.value.status_code == 409


def test_delta_save_reuses_validated_large_archive_without_reinflate_or_recompress(tmp_path, monkeypatch):
    import chat_transcript_archive as archive
    import dashboard_server as server
    from dashboard_api_models import ChatTranscriptsRequest

    monkeypatch.setattr(server, 'chat_transcripts_path', lambda: tmp_path / 'chats.json')
    monkeypatch.setattr(server, 'chat_project_index_path', lambda: tmp_path / 'projects.json')
    result = {'ok': True, 'text': 'large durable result ' * 7000}
    steps = [{'tool': 'read_fixture', 'status': 'executed', 'result': result}]
    response = {'ok': True, 'plan': {'reply': 'Done', 'summary': 'Done', 'planner': 'llm', 'shellNeeded': False, 'steps': steps}, 'steps': steps}
    chat = {'id': 'large', 'title': 'before', 'items': [{'id': 'agent', 'type': 'agent', 'response': response}]}
    first, _, _ = server.write_chat_transcripts_storage(ChatTranscriptsRequest(chats=[chat]))
    delta = {
        'baseDigest': first['chatDigests']['large'],
        'metadataPatch': [{'op': 'replace', 'path': '/title', 'value': 'after'}],
        'itemOrder': ['agent'], 'itemPatches': {}, 'newItems': {},
    }
    request = ChatTranscriptsRequest(
        chats=[{'id': 'large'}], sourceRevisions=first['sourceRevisions'], chatPatches={'large': delta}
    )
    original_read = archive._read_blob
    original_compress = archive.gzip.compress
    original_atomic_write_bytes = server.atomic_write_bytes
    reads = []
    compressions = []
    blob_writes = []

    def counted_read(*args, **kwargs):
        reads.append(1)
        return original_read(*args, **kwargs)

    def counted_compress(*args, **kwargs):
        compressions.append(1)
        return original_compress(*args, **kwargs)

    def counted_atomic_write(path, data):
        if str(path).endswith('.json.gz'):
            blob_writes.append(1)
        return original_atomic_write_bytes(path, data)

    with mock_patch.object(archive, '_read_blob', side_effect=counted_read), \
         mock_patch.object(archive.gzip, 'compress', side_effect=counted_compress), \
         mock_patch.object(server, 'atomic_write_bytes', side_effect=counted_atomic_write):
        updated, _, _ = server.write_chat_transcripts_storage(request)

    assert len(reads) == 1  # Initial source validation only.
    assert compressions == []
    assert blob_writes == []
    assert updated['chatDigests']['large'] != first['chatDigests']['large']
    stored = json.loads((tmp_path / 'chats.json').read_text(encoding='utf-8'))
    assert stored['version'] == 2
    assert stored['chats'][0]['items'][0]['__vrcforge_chat_transcript_archive__']['digest']
    loaded, _source, recovery = server.load_chat_transcript_file(
        server.chat_store_target(tmp_path / 'chats.json', scope='app'), scope='app'
    )
    assert recovery is None and loaded[0]['title'] == 'after'

    with pytest.raises(HTTPException) as stale:
        server.write_chat_transcripts_storage(request)
    assert stale.value.status_code == 409


def test_delta_archive_reuse_write_failure_restores_exact_storage_snapshot(tmp_path, monkeypatch):
    import dashboard_server as server
    from dashboard_api_models import ChatTranscriptsRequest

    monkeypatch.setattr(server, 'chat_transcripts_path', lambda: tmp_path / 'chats.json')
    monkeypatch.setattr(server, 'chat_project_index_path', lambda: tmp_path / 'projects.json')
    result = {'ok': True, 'text': 'large durable result ' * 7000}
    steps = [{'tool': 'read_fixture', 'status': 'executed', 'result': result}]
    response = {'ok': True, 'plan': {'reply': 'Done', 'summary': 'Done', 'planner': 'llm', 'shellNeeded': False, 'steps': steps}, 'steps': steps}
    chat = {'id': 'large', 'title': 'before', 'items': [{'id': 'agent', 'type': 'agent', 'response': response}]}
    first, _, _ = server.write_chat_transcripts_storage(ChatTranscriptsRequest(chats=[chat]))
    transcript = tmp_path / 'chats.json'
    original_transcript = transcript.read_bytes()
    archives = {path.relative_to(tmp_path): path.read_bytes() for path in tmp_path.rglob('*.gz')}
    delta = {
        'baseDigest': first['chatDigests']['large'],
        'metadataPatch': [{'op': 'replace', 'path': '/title', 'value': 'after'}],
        'itemOrder': ['agent'], 'itemPatches': {}, 'newItems': {},
    }
    request = ChatTranscriptsRequest(chats=[{'id': 'large'}], sourceRevisions=first['sourceRevisions'], chatPatches={'large': delta})
    original_write_json = server.atomic_write_json

    def fail_project_index(path, value):
        if path == server.chat_project_index_path():
            raise OSError('injected delta index write failure')
        return original_write_json(path, value)

    with mock_patch.object(server, 'atomic_write_json', side_effect=fail_project_index):
        with pytest.raises(HTTPException) as failure:
            server.write_chat_transcripts_storage(request)
    assert failure.value.status_code == 500
    assert transcript.read_bytes() == original_transcript
    assert {path.relative_to(tmp_path): path.read_bytes() for path in tmp_path.rglob('*.gz')} == archives
