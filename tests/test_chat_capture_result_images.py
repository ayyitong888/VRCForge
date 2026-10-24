import copy
import json
import os
from pathlib import Path

import pytest
from PIL import Image
from chat_resource_image import _matching_chat_response, public_capture_previews, read_chat_scene_image_resource
from mcp_resource_registry import McpResourceRegistry
from scene_capture_preview import publish_scene_capture_preview


def test_capture_result_authorizes_exact_turn_and_projects_only_safe_metadata(tmp_path):
    image = tmp_path / 'capture.png'
    Image.new('RGB', (8, 8), 'red').save(image)
    registry = McpResourceRegistry(tmp_path / 'registry')
    preview = publish_scene_capture_preview(registry, image, str(tmp_path), {'sessionId': 'session', 'approvalId': 'approval'}, 'runtime:session')
    result = {'captures': [{'angle': 'front', 'preview': preview, 'imagePath': 'private/path', 'secret': 'not-public'}]}
    public = public_capture_previews(result)
    assert len(public) == 1
    assert set(public[0]) == {'angle', 'preview'}
    assert set(public[0]['preview']) == {'schema', 'mimeType', 'captureId', 'resourceUri'}
    response = {'sessionId': 'session', 'clientTurnId': 'turn', 'plan': {'reply': 'Review the captured result.'}, 'steps': [{'result': result}]}
    chat = {'sessionId': 'session', 'projectPath': str(tmp_path), 'items': [{'type': 'agent', 'response': response}]}
    params = dict(uri=preview['resourceUri'], session_id='session', client_turn_id='turn', project_path=str(tmp_path), chat=chat)
    assert read_chat_scene_image_resource(registry, **params)['pngBase64']
    chat['items'] = [{'type': 'streaming', 'clientTurnId': 'turn', 'timeline': [{'kind': 'tool_result', 'payload': {'captures': public}}]}]
    assert read_chat_scene_image_resource(registry, **params)['pngBase64']
    with pytest.raises(ValueError):
        read_chat_scene_image_resource(registry, **{**params, 'client_turn_id': 'other'})
    chat['items'][0]['timeline'][0]['kind'] = 'assistant'
    with pytest.raises(ValueError):
        read_chat_scene_image_resource(registry, **params)
    assert public_capture_previews({'preview': {**preview, 'schema': 'untrusted'}}) == []
    assert public_capture_previews({'preview': {**preview, 'captureId': 'other'}}) == []
    from agent_runtime_event_projection import project_runtime_turn_event
    event = {'id': 'capture', 'kind': 'tool_result', 'payload': {'captures': public, 'private': 'excluded'}}
    projected = project_runtime_turn_event({'continuationSource': 'foreground_completed', 'sessionId': 'session', 'turnId': 'backend', 'timeline': [event]})
    assert projected['timeline'][0]['payload']['captures'] == public
    assert 'private' not in projected['timeline'][0]['payload']
    chat['items'] = []
    def ledger(**kwargs):
        return {'run': {'sessionId': 'session', 'clientTurnId': 'turn', 'projectRoot': str(tmp_path)}, 'events': projected['timeline'], 'hasMore': False}
    assert read_chat_scene_image_resource(registry, **params, runtime_timeline_reader=ledger)['pngBase64']
    def foreign_ledger(**kwargs):
        page = ledger(**kwargs)
        page['run']['projectRoot'] = str(tmp_path / 'foreign')
        return page
    with pytest.raises(ValueError):
        read_chat_scene_image_resource(registry, **params, runtime_timeline_reader=foreign_ledger)



def test_original_capture_receipt_projects_and_authorizes_without_model_markdown():
    source = os.environ.get('VRCFORGE_CAPTURE_RECEIPT')
    if not source:
        pytest.skip('optional original receipt replay')
    response = json.loads(Path(source).read_text(encoding='utf-8'))['response']
    projected = [capture for step in response['steps'] for capture in public_capture_previews(step.get('result'))]
    uris = {capture['preview']['resourceUri'] for capture in projected}
    assert len(uris) == 12
    assert all(uri not in response['plan']['reply'] for uri in uris)
    chat = {'items': [{'type': 'agent', 'response': response}]}
    assert all(_matching_chat_response(chat, response['sessionId'], response['clientTurnId'], uri) is response for uri in uris)
    wrong = copy.deepcopy(chat)
    wrong['items'][0]['response']['clientTurnId'] = 'other'
    assert all(_matching_chat_response(wrong, response['sessionId'], response['clientTurnId'], uri) is None for uri in uris)

def test_file_route_requires_app_auth_and_exact_stored_tool_result(tmp_path, monkeypatch):
    import dashboard_server as server
    from fastapi.testclient import TestClient
    project = tmp_path / 'project'
    project.mkdir()
    report = project / 'report.txt'
    report.write_text('inspection result', encoding='utf-8')
    chat = {'id': 'chat', 'sessionId': 'session', 'projectPath': str(project), 'items': [{'type': 'agent', 'response': {
        'sessionId': 'session', 'clientTurnId': 'turn', 'steps': [{'result': {'filePath': str(report)}}], 'plan': {'reply': 'Report ready.'},
    }}]}
    monkeypatch.setattr(server, 'APP_AUTH_REQUIRED', True)
    monkeypatch.setattr(server, 'APP_SESSION_TOKEN', 'fixture-file-token')
    monkeypatch.setattr(server, 'ARTIFACTS_DIR', tmp_path / 'artifacts')
    monkeypatch.setattr(server, '_load_handoff_chat', lambda chat_id, scope: chat if chat_id == 'chat' and scope == str(project) else None)
    body = {'path': str(report), 'chatId': 'chat', 'projectPath': str(project), 'sessionId': 'session', 'clientTurnId': 'turn'}
    with TestClient(server.app, client=('127.0.0.1', 1234)) as client:
        assert client.post('/api/app/agent/resource-file', json=body).status_code == 401
        headers = {'Authorization': 'Bearer fixture-file-token'}
        response = client.post('/api/app/agent/resource-file', json=body, headers=headers)
        assert response.status_code == 200
        assert Path(response.json()['path']) == report.resolve()
        assert client.post('/api/app/agent/resource-file', json={**body, 'clientTurnId': 'other'}, headers=headers).status_code == 404
