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

def test_runtime_snapshot_does_not_invert_chat_and_gateway_lock_order():
    chats={"c":{"id":"c","sessionId":"s","projectPath":"p","revision":1,"items":[]}}
    chat_lock=threading.Lock()
    gateway_lock=threading.Lock()
    gateway_held=threading.Event()
    runtime_waiting=threading.Event()
    commit_may_enter_chat=threading.Event()
    commit_acquired_chat=threading.Event()
    snapshot_result=[]

    def runtime_snapshot(*_args):
        runtime_waiting.set()
        with gateway_lock:
            return {"active_stream":False}

    port=SessionHandoffChatPort(principal_digest="p",lock=chat_lock,load_chat=lambda cid,scope: chats.get(cid),save_chat=lambda *a:a[2],runtime_snapshot=runtime_snapshot,enqueue_context=lambda *a: None)

    def memory_review_commit():
        with gateway_lock:
            gateway_held.set()
            commit_may_enter_chat.wait(3)
            acquired=chat_lock.acquire(timeout=0.5)
            if acquired:
                commit_acquired_chat.set()
                chat_lock.release()

    commit=threading.Thread(target=memory_review_commit)
    commit.start()
    assert gateway_held.wait(3)
    snapshot=threading.Thread(target=lambda: snapshot_result.append(
        port.get_snapshot(owner_id="p",chat_id="c",session_id="s",scope="p")
    ))
    snapshot.start()
    assert runtime_waiting.wait(3)
    commit_may_enter_chat.set()
    commit.join(3)
    snapshot.join(3)

    assert not commit.is_alive() and not snapshot.is_alive()
    assert commit_acquired_chat.is_set()
    assert snapshot_result[0]["active_stream"] is False
