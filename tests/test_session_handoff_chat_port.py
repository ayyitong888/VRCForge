import threading
import pytest
from session_handoff_chat_port import SessionHandoffChatPort, ChatPortConflict

def test_snapshot_is_server_bound_and_revision_explicit():
    chats={"dst":{"id":"dst","sessionId":"s","projectPath":"p","revision":3,"items":[]}}
    port=SessionHandoffChatPort(principal_digest="principal",lock=threading.RLock(),load_chat=lambda cid,scope: chats.get(cid),save_chat=lambda *a: a[2],runtime_snapshot=lambda *a: {},enqueue_context=lambda *a: None)
    snap=port.get_snapshot(owner_id="principal",chat_id="dst",session_id="s",scope="p")
    assert snap["revision"]==3 and snap["owner_id"]=="principal"
    with pytest.raises(PermissionError): port.get_snapshot(owner_id="client",chat_id="dst",session_id="s",scope="p")

def test_missing_revision_fails_closed():
    port=SessionHandoffChatPort(principal_digest="p",lock=threading.RLock(),load_chat=lambda *_: {"id":"c","sessionId":"s","projectPath":"","items":[]},save_chat=lambda *a: a[2],runtime_snapshot=lambda *a: {},enqueue_context=lambda *a: None)
    with pytest.raises(ChatPortConflict): port.get_snapshot(owner_id="p",chat_id="c",session_id="s",scope="")

def test_card_append_cas_and_idempotent_digest():
    chats={"dst":{"id":"dst","sessionId":"s","projectPath":"p","revision":2,"items":[]}}
    def save(cid,scope,updated,expected): chats[cid]=updated; return updated
    port=SessionHandoffChatPort(principal_digest="p",lock=threading.RLock(),load_chat=lambda cid,scope: chats.get(cid),save_chat=save,runtime_snapshot=lambda *a: {},enqueue_context=lambda *a: None)
    card={"cardId":"card","handoffId":"h","kind":"handoff","payload":{"goal":"summary"},"targetChatId":"dst","scope":"p"}
    result=port.append_inbox_card(card_id="card",payload_digest="d",card=card,expected_revision=2)
    assert result["revision"]==3
    replay=port.append_inbox_card(card_id="card",payload_digest="d",card=card,expected_revision=2)
    assert replay["revision"]==3 and len(chats["dst"]["items"])==1
    with pytest.raises(ChatPortConflict): port.append_inbox_card(card_id="card",payload_digest="other",card=card,expected_revision=2)

def test_runtime_flags_are_server_owned():
    chats={"c":{"id":"c","sessionId":"s","projectPath":"","revision":1,"items":[]}}
    port=SessionHandoffChatPort(principal_digest="p",lock=threading.RLock(),load_chat=lambda cid,scope: chats.get(cid),save_chat=lambda *a:a[2],runtime_snapshot=lambda *a:{"active_stream":True},enqueue_context=lambda *a: None)
    assert port.get_snapshot(owner_id="p",chat_id="c",session_id="s",scope="")["active_stream"] is True
