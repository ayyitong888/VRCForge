"""Lossless bounded transport for an existing whole chat snapshot."""
import hashlib
import json

import pytest
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
            assert json.loads(page['text']) == original
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
    canonical = json.dumps(snapshot, ensure_ascii=False, sort_keys=True, separators=(',', ':'), allow_nan=False)
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
    assert json.loads(''.join(pieces)) == snapshot


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
