"""Large complete tool evidence must survive the actual chat storage boundary."""
import json
from dataclasses import replace
from pathlib import Path
from unittest.mock import patch

import pytest
import dashboard_server as server
import test_dashboard_server as _dashboard_fixture


@pytest.fixture
def storage():
    fixture = _dashboard_fixture.DashboardServerTests('test_chat_get_blocks_oversized_record_count_without_loading_unsavable_state')
    fixture.setUp()
    try:
        yield Path(fixture.tuning_store_dir.name)
    finally:
        fixture.tearDown()


def large_chat(project=''):
    result = {'ok': True, 'text': 'complete evidence ' * 160000 + 'LAST_FACT'}
    steps = [{'tool': 'read_fixture', 'status': 'executed', 'result': result,
              'modelInformation': {'payload': {'text': json.dumps(result)}}}]
    response = {'ok': True, 'plan': {'reply': 'Done', 'summary': 'Done',
                'planner': 'llm', 'shellNeeded': False, 'steps': steps}, 'steps': steps}
    return {'id': 'large-chat', 'projectPath': project,
            'items': [{'id': f'agent-{i}', 'type': 'agent', 'response': response} for i in range(2)]}


@pytest.mark.parametrize('project_owned', [False, True])
def test_complete_agent_items_over_index_limit_round_trip(storage, project_owned):
    project = storage / 'Project'
    project.mkdir()
    chat = large_chat(str(project) if project_owned else '')
    assert len(json.dumps(chat).encode()) > server.CHAT_TRANSCRIPTS_MAX_BYTES
    saved, _, _ = server.write_chat_transcripts_storage(server.ChatTranscriptsRequest(chats=[chat]))
    assert saved['ok']
    path = server.project_chat_transcripts_path(str(project)) if project_owned else server.chat_transcripts_path()
    target = server.chat_store_target(path, scope='project' if project_owned else 'app', project_path=str(project))
    loaded, source, recovery = server.load_chat_transcript_file(target, scope='project' if project_owned else 'app')
    assert recovery is None and source['status'] == 'ok'
    assert loaded == [chat]
    assert path.stat().st_size < server.CHAT_TRANSCRIPTS_MAX_BYTES
    # Old readers must identify an unsupported document, not quarantine new references.
    assert json.loads(path.read_text(encoding='utf-8'))['version'] == 2
    old_scan = server.scan_session_store(replace(target, known_document_versions=(1,)))
    assert old_scan['status'] == 'unsupported'


def test_archive_transaction_failure_leaves_no_new_payloads(storage):
    chat = large_chat()
    original_write = server.atomic_write_bytes
    index = server.chat_project_index_path()
    def fail_index(path, data):
        if path == index:
            raise OSError('injected index write failure')
        return original_write(path, data)
    with patch.object(server, 'atomic_write_bytes', side_effect=fail_index):
        with pytest.raises(server.HTTPException) as error:
            server.write_chat_transcripts_storage(server.ChatTranscriptsRequest(chats=[chat]))
    assert error.value.status_code == 500
    assert not server.chat_transcripts_path().exists()
    assert not list(storage.rglob('*.gz'))


def test_missing_archive_blocks_read_and_preserves_source(storage):
    from chat_transcript_archive import blob_path, REF_FIELD
    server.write_chat_transcripts_storage(server.ChatTranscriptsRequest(chats=[large_chat()]))
    path = server.chat_transcripts_path()
    snapshot = path.read_bytes()
    item = json.loads(snapshot)['chats'][0]['items'][0]
    blob_path(path, item[REF_FIELD]['digest']).unlink()
    loaded, source, recovery = server.load_chat_transcript_file(server.chat_store_target(path, scope='app'), scope='app')
    assert loaded == [] and source['status'] == 'needs_repair'
    assert recovery['reason'] == 'chat_archive_unavailable'
    assert path.read_bytes() == snapshot


def test_api_cannot_submit_stored_references(storage):
    from chat_transcript_archive import encode_chats
    packed, _ = encode_chats([large_chat()])
    with pytest.raises(server.HTTPException) as error:
        server.write_chat_transcripts_storage(server.ChatTranscriptsRequest(chats=packed))
    assert error.value.status_code == 422
    assert not server.chat_transcripts_path().exists()
