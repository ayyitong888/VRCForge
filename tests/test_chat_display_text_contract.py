"""Local authenticated chat history is content, not diagnostic output."""
import json
import os
from pathlib import Path

from chat_snapshot_transport import snapshot_page


def test_local_chat_display_roundtrip_keeps_original_text_in_typed_slots():
    texts = [
        '事件桥通过 WebSocket + Bearer 会话令牌连接。',
        '```json\n{"authorization": "Bearer <session token>"}\n```',
        '用户粘贴的本地原文: api_key=local-synthetic-value; Bearer local-synthetic-token',
    ]
    fixture = os.environ.get('VRCFORGE_CHAT_DISPLAY_REPLAY')
    if fixture:
        texts.extend(row['text'] for row in json.loads(Path(fixture).read_text(encoding='utf-8-sig')))
    for text in texts:
        snapshot = {'chats': [{'title': text, 'items': [
            {'type': 'user', 'text': text},
            {'type': 'agent', 'response': {'plan': {'reply': text, 'publicMessages': [{'text': text}]}}},
        ]}], '_validatedChatTextPaths': ['/private/config/api_key'], 'private': {'config': {'api_key': 'not-a-chat-slot'}}}
        projected = json.loads(snapshot_page(snapshot)['text'])
        assert projected['chats'] == snapshot['chats']
        assert projected['_validatedChatTextPaths'] == [
            '/chats/0/title', '/chats/0/items/0/text',
            '/chats/0/items/1/response/plan/reply',
            '/chats/0/items/1/response/plan/publicMessages/0/text',
        ]
