import copy
import json
import os
from pathlib import Path
from types import SimpleNamespace

import pytest

from agent_runtime_run_ledger import AgentRuntimeRunLedger, _terminal_continuation
from chat_resource_image import read_chat_scene_image_resource
from mcp_resource_registry import McpResourceRegistry


def ledger(path):
    return AgentRuntimeRunLedger(SimpleNamespace(log_path=lambda: path, redact=lambda value: value,
        error_factory=lambda message, status: ValueError(message)))


def fixture():
    identity = dict(sessionId='session', clientTurnId='client', turnId='turn', projectRoot='D:/project')
    previews = []
    for index in (1, 2):
        capture = f'00000000-0000-4000-8000-{index:012d}'
        previews.append({'angle': 'front', 'preview': {'schema': 'vrcforge.scene_capture_preview.v1',
            'mimeType': 'image/png', 'captureId': capture,
            'resourceUri': f'vrcforge://scene-capture-preview/{capture}?context=runtime%3Asession&revision=1&store=' + '1'*32},
            'imagePath': 'private.png'})
    timeline = [{'id': f'result-{index}', 'sequence': index, 'kind': 'tool_result',
        'payload': {'actionId': 'reused', 'tool': 'capture'}} for index in (1, 2)]
    return dict(identity, event='runtime_turn_completed', status='completed',
        steps=[{'actionId': 'reused', 'result': {'captures': [preview]}} for preview in previews],
        continuationEvent=dict(identity, continuationSource='foreground_completed', timeline=timeline, plan={}))


def test_old_terminal_capture_projection_preserves_occurrences_and_ownership(tmp_path):
    source = fixture()
    before = copy.deepcopy(source)
    rows = [dict(source, event='runtime_turn_timeline_event', timelineEvent=event)
            for event in source['continuationEvent']['timeline']] + [source]
    path = tmp_path / 'runs.jsonl'
    path.write_text(''.join(json.dumps(row)+'\n' for row in rows), encoding='utf-8')
    instance = ledger(path)
    restored = instance.read_terminal_continuation(session_id='session', client_turn_id='client', turn_id='turn')
    captures = [event['payload']['captures'][0] for event in restored['timeline']]
    assert captures[0]['preview']['captureId'] != captures[1]['preview']['captureId']
    assert 'imagePath' not in json.dumps(restored)
    assert 'steps' not in restored
    page = instance.read_timeline(session_id='session', client_turn_id='client', after_sequence=2, limit=1)
    assert page['events'][0]['payload']['captures'] == [captures[1]]
    assert source == before
    missing = copy.deepcopy(source)
    missing['steps'][0].pop('result')
    missing_timeline = _terminal_continuation(missing)['timeline']
    assert 'captures' not in missing_timeline[0]['payload']
    assert missing_timeline[1]['payload']['captures'] == [captures[1]]
    assert instance.read_timeline(session_id='foreign', client_turn_id='client')['events'] == []
    for field in ('sessionId', 'clientTurnId', 'turnId', 'projectRoot'):
        foreign = copy.deepcopy(source)
        foreign['continuationEvent'][field] = 'foreign'
        assert all('captures' not in event['payload'] for event in _terminal_continuation(foreign)['timeline'])


def test_original_persisted_capture_restore_and_image_authorization(monkeypatch):
    root = os.environ.get('VRCFORGE_CAPTURE_RUNTIME_PROFILE')
    chat_path = os.environ.get('VRCFORGE_CAPTURE_CHAT_REPLAY')
    if not root or not chat_path:
        pytest.skip('optional original persistent ledger and registry readback')
    audit = Path(root) / 'artifacts/dashboard/agent_gateway'
    instance = ledger(audit / 'runtime-runs.jsonl')
    chats = json.loads(Path(chat_path).read_text(encoding='utf-8'))['chats']
    chat = next(chat for chat in chats if chat['id'] == 'chat-fe96541de57a43efa2ed424b17530f31')
    client = 'turn-7087ed919aef4ff68ef78370a912ef1c'
    response = next(item['response'] for item in chat['items'] if item.get('response', {}).get('clientTurnId') == client)
    restored = instance.read_terminal_continuation(session_id=chat['sessionId'], client_turn_id=client, turn_id=response['turnId'])
    uris = {capture['preview']['resourceUri'] for event in restored['timeline']
            for capture in event.get('payload', {}).get('captures', [])}
    assert len(uris) == 12
    assert 'scene-capture-preview' not in json.dumps(response)
    registry = McpResourceRegistry(audit / 'mcp-resources')
    import dashboard_server as server
    monkeypatch.setattr(server, 'AGENT_GATEWAY', SimpleNamespace(_mcp_resources=registry, runtime_runs=instance))
    monkeypatch.setattr(server, '_load_handoff_chat', lambda chat_id, scope: chat
                        if chat_id == chat['id'] and scope == chat['projectPath'] else None)
    for uri in uris:
        args = dict(uri=uri, session_id=chat['sessionId'], client_turn_id=client,
                    project_path=chat['projectPath'], chat=chat)
        with pytest.raises(ValueError, match='not present'):
            read_chat_scene_image_resource(registry, **args)
        image = server.app_agent_resource_image(server.ChatResourceImageRequest(
            uri=uri, sessionId=chat['sessionId'], clientTurnId=client,
            chatId=chat['id'], projectPath=chat['projectPath']))
        assert image['ok'] is True
        assert image['pngBase64'] and len(image['sha256']) == 64
