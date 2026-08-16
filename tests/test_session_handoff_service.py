from pathlib import Path
import pytest
from session_handoff import SessionHandoffError, SessionHandoffStore
from session_handoff_chat_port import ChatPortConflict
from session_handoff_service import SessionHandoffService

class Chat:
    def __init__(self): self.revs={}; self.cards={}; self.context=[]; self.calls=0; self.busy=False
    def get_snapshot(self, *, owner_id, chat_id, session_id, scope): return {"owner_id":owner_id,"chat_id":chat_id,"session_id":session_id,"scope":scope,"revision":self.revs.get(chat_id,1),"active_stream":self.busy}
    def append_inbox_card(self, *, card_id, payload_digest, card, expected_revision):
        self.calls+=1
        if card_id in self.cards: return self.cards[card_id]
        if expected_revision != self.revs.get(card["sourceChatId"].replace("src","dst"),1):
            raise RuntimeError("CAS")
        self.cards[card_id]=dict(card); self.cards[card_id]["revision"] = expected_revision + 1; return self.cards[card_id]
    def get_inbox_card(self, *, card_id, chat_id): return self.cards.get(card_id)
    def enqueue_next_turn_context(self, *, chat_id, context):
        if any(existing[1].get("contextId") == context.get("contextId") for existing in self.context): return
        self.context.append((chat_id,context))

def payload(goal="do thing"): return {"goal":goal,"completed":False,"decisions":[],"blockers":[],"nextAction":"","question":""}
def setup(tmp_path):
    s=SessionHandoffStore(tmp_path/"h.db",tmp_path/"a.log")
    c=Chat(); svc=SessionHandoffService(s,c,state_path=tmp_path/"state.json"); return s,c,svc

def test_send_reads_both_and_deliver_exactly_once(tmp_path):
    s,c,svc=setup(tmp_path)
    row=svc.send(owner_id="o",source_session_id="s",source_chat_id="src",target_session_id="t",target_chat_id="dst",scope="p",payload=payload())
    out=svc.deliver(handoff_id=row["id"],owner_id="o",target_session_id="t",scope="p")
    assert out["status"]=="materialized" and len(c.cards)==1 and len(c.context)==1
    again=svc.deliver(handoff_id=row["id"],owner_id="o",target_session_id="t",scope="p")
    assert again["cardId"]==out["cardId"] and len(c.cards)==1 and len(c.context)==1
    assert "claim_token" not in out["handoff"]

def test_busy_queues_without_claim_or_chat_activity(tmp_path):
    s,c,svc=setup(tmp_path); c.busy=True
    row=svc.send(owner_id="o",source_session_id="s",source_chat_id="src",target_session_id="t",target_chat_id="dst",scope="",payload=payload())
    out=svc.deliver(handoff_id=row["id"],owner_id="o",target_session_id="t",scope="")
    assert out["status"]=="queued" and not c.cards
    assert s.get(handoff_id=row["id"],owner_id="o",session_id="t",scope="")["status"]=="pending_review"

def test_pause_is_restart_safe(tmp_path):
    s,c,svc=setup(tmp_path)
    row=svc.send(owner_id="o",source_session_id="s",source_chat_id="src",target_session_id="t",target_chat_id="dst",scope="p",payload=payload())
    svc.pause(handoff_id=row["id"], owner_id="o", target_session_id="t", scope="p", expected_revision=row["revision"])
    svc2=SessionHandoffService(s,c,state_path=tmp_path/"state.json")
    assert svc2.deliver(handoff_id=row["id"],owner_id="o",target_session_id="t",scope="p")["status"]=="paused"
    svc2.resume(handoff_id=row["id"], owner_id="o", target_session_id="t", scope="p", expected_revision=row["revision"])
    assert svc2.deliver(handoff_id=row["id"],owner_id="o",target_session_id="t",scope="p")["status"]=="materialized"


@pytest.mark.parametrize(
    ("override", "error_type"),
    [
        ({"owner_id": "other"}, PermissionError),
        ({"target_session_id": "s"}, PermissionError),
        ({"scope": "other"}, PermissionError),
        ({"expected_revision": 99}, SessionHandoffError),
    ],
)
def test_pause_rejects_cross_binding_and_stale_revision_without_mutation(tmp_path, override, error_type):
    _s,c,svc=setup(tmp_path)
    row=svc.send(owner_id="o",source_session_id="s",source_chat_id="src",target_session_id="t",target_chat_id="dst",scope="p",payload=payload())
    request={"handoff_id":row["id"],"owner_id":"o","target_session_id":"t","scope":"p","expected_revision":row["revision"],**override}

    with pytest.raises(error_type):
        svc.pause(**request)

    assert svc._paused == set()
    assert not (tmp_path/"state.json").exists()
    assert c.calls == 0


def test_resume_rejects_cross_binding_and_stale_revision_without_mutation(tmp_path):
    _s,_c,svc=setup(tmp_path)
    row=svc.send(owner_id="o",source_session_id="s",source_chat_id="src",target_session_id="t",target_chat_id="dst",scope="p",payload=payload())
    svc.pause(handoff_id=row["id"], owner_id="o", target_session_id="t", scope="p", expected_revision=row["revision"])
    before=(tmp_path/"state.json").read_bytes()

    cases = (
        ({"owner_id":"other"}, PermissionError),
        ({"target_session_id":"s"}, PermissionError),
        ({"scope":"other"}, PermissionError),
        ({"expected_revision":99}, SessionHandoffError),
    )
    for override, error_type in cases:
        request={"handoff_id":row["id"],"owner_id":"o","target_session_id":"t","scope":"p","expected_revision":row["revision"],**override}
        with pytest.raises(error_type):
            svc.resume(**request)
        assert (tmp_path/"state.json").read_bytes() == before
        assert row["id"] in svc._paused


def test_pause_cross_checks_authoritative_target_chat_snapshot(tmp_path):
    _s,c,svc=setup(tmp_path)
    row=svc.send(owner_id="o",source_session_id="s",source_chat_id="src",target_session_id="t",target_chat_id="dst",scope="p",payload=payload())
    original=c.get_snapshot
    def mismatched(**kwargs):
        snapshot=original(**kwargs); snapshot["session_id"]="foreign-session"; return snapshot
    c.get_snapshot=mismatched

    with pytest.raises(PermissionError, match="binding mismatch"):
        svc.pause(handoff_id=row["id"], owner_id="o", target_session_id="t", scope="p", expected_revision=row["revision"])
    assert svc._paused == set()
    assert not (tmp_path/"state.json").exists()

def test_owner_and_scope_boundaries(tmp_path):
    s,c,svc=setup(tmp_path)
    class Foreign(Chat):
        def get_snapshot(self, **kwargs):
            snap = super().get_snapshot(**kwargs); snap["owner_id"] = "other"; return snap
    svc.chat = Foreign()
    with pytest.raises(PermissionError):
        svc.send(owner_id="o",source_session_id="s",source_chat_id="src",target_session_id="t",target_chat_id="dst",scope="a",payload=payload())

def test_snapshot_missing_binding_and_relative_state_fail_closed(tmp_path):
    s,c,svc=setup(tmp_path)
    class Incomplete(Chat):
        def get_snapshot(self, **kwargs): return {"revision": 1}
    svc.chat = Incomplete()
    with pytest.raises(PermissionError, match="incomplete"):
        svc.send(owner_id="o",source_session_id="s",source_chat_id="src",target_session_id="t",target_chat_id="dst",scope="a",payload=payload())
    with pytest.raises(ValueError, match="absolute"):
        SessionHandoffService(s, c, state_path="relative.json")

def test_changed_target_revision_rejects_without_matching_existing_card(tmp_path):
    s,c,svc=setup(tmp_path)
    row=svc.send(owner_id="o",source_session_id="s",source_chat_id="src",target_session_id="t",target_chat_id="dst",scope="p",payload=payload())
    c.revs["dst"] = 2
    with pytest.raises(RuntimeError, match="revision changed"):
        svc.deliver(handoff_id=row["id"],owner_id="o",target_session_id="t",scope="p")
    assert s.get(handoff_id=row["id"],owner_id="o",session_id="t",scope="p")["status"] == "pending_review"

def test_card_return_mismatch_keeps_handoff_pending(tmp_path):
    s,c,svc=setup(tmp_path)
    row=svc.send(owner_id="o",source_session_id="s",source_chat_id="src",target_session_id="t",target_chat_id="dst",scope="p",payload=payload())
    c.append_inbox_card=lambda **kwargs: {"cardId":"wrong","payloadDigest":"wrong","revision":2}
    with pytest.raises(RuntimeError, match="identity mismatch"):
        svc.deliver(handoff_id=row["id"],owner_id="o",target_session_id="t",scope="p")
    assert s.get(handoff_id=row["id"],owner_id="o",session_id="t",scope="p")["status"] == "pending_review"

def test_enqueue_restart_replay_is_port_idempotent(tmp_path):
    s,c,svc=setup(tmp_path)
    row=svc.send(owner_id="o",source_session_id="s",source_chat_id="src",target_session_id="t",target_chat_id="dst",scope="p",payload=payload())
    original_save=svc._save_state
    svc._save_state=lambda: (_ for _ in ()).throw(RuntimeError("crash after enqueue"))
    with pytest.raises(RuntimeError): svc.deliver(handoff_id=row["id"],owner_id="o",target_session_id="t",scope="p")
    svc2=SessionHandoffService(s,c,state_path=tmp_path/"state.json")
    svc2.deliver(handoff_id=row["id"],owner_id="o",target_session_id="t",scope="p")
    assert len(c.context) == 1
def test_crash_window_card_retry_converges(tmp_path):
    s,c,svc=setup(tmp_path)
    row=svc.send(owner_id="o",source_session_id="s",source_chat_id="src",target_session_id="t",target_chat_id="dst",scope="p",payload=payload())
    original=s.materialize; s.materialize=lambda **kw: (_ for _ in ()).throw(RuntimeError("crash"))
    with pytest.raises(RuntimeError): svc.deliver(handoff_id=row["id"],owner_id="o",target_session_id="t",scope="p")
    s.materialize=original
    out=svc.deliver(handoff_id=row["id"],owner_id="o",target_session_id="t",scope="p")
    assert out["status"]=="materialized" and len(c.cards)==1


def test_post_commit_chat_conflict_converges_only_after_exact_card_readback(tmp_path):
    s,c,svc=setup(tmp_path)
    row=svc.send(owner_id="o",source_session_id="s",source_chat_id="src",target_session_id="t",target_chat_id="dst",scope="p",payload=payload())
    original=c.append_inbox_card

    def persist_then_conflict(**kwargs):
        original(**kwargs)
        raise ChatPortConflict("post-commit readback was interrupted")

    c.append_inbox_card=persist_then_conflict
    out=svc.deliver(handoff_id=row["id"],owner_id="o",target_session_id="t",scope="p")

    assert out["status"] == "materialized"
    assert len(c.cards) == 1
    assert len(c.context) == 1


def test_post_commit_runtime_error_converges_only_after_exact_card_readback(tmp_path):
    s,c,svc=setup(tmp_path)
    row=svc.send(owner_id="o",source_session_id="s",source_chat_id="src",target_session_id="t",target_chat_id="dst",scope="p",payload=payload())
    original=c.append_inbox_card

    def persist_then_runtime_error(**kwargs):
        original(**kwargs)
        raise RuntimeError("post-commit adapter readback changed")

    c.append_inbox_card=persist_then_runtime_error
    out=svc.deliver(handoff_id=row["id"],owner_id="o",target_session_id="t",scope="p")

    assert out["status"] == "materialized"
    assert len(c.cards) == 1
    assert len(c.context) == 1


def test_post_commit_malformed_receipt_converges_only_after_exact_card_readback(tmp_path):
    s,c,svc=setup(tmp_path)
    row=svc.send(owner_id="o",source_session_id="s",source_chat_id="src",target_session_id="t",target_chat_id="dst",scope="p",payload=payload())
    original=c.append_inbox_card

    def persist_then_return_stale_receipt(**kwargs):
        original(**kwargs)
        return {"cardId":"stale","payloadDigest":"stale","revision":1}

    c.append_inbox_card=persist_then_return_stale_receipt
    out=svc.deliver(handoff_id=row["id"],owner_id="o",target_session_id="t",scope="p")

    assert out["status"] == "materialized"
    assert len(c.cards) == 1
    assert len(c.context) == 1
