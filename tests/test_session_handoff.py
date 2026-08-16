import os
import stat
import threading
from pathlib import Path

import pytest

import session_handoff
from session_handoff import (
    SESSION_HANDOFF_AUDIT_SCHEMA,
    SessionHandoffError,
    SessionHandoffStore,
)


class Clock:
    def __init__(self, value: float = 10.0) -> None:
        self.value = value

    def __call__(self) -> float:
        return self.value

    def advance(self, seconds: float) -> None:
        self.value += seconds


def make_payload(goal: str = "handoff goal", **overrides: object) -> dict[str, object]:
    payload: dict[str, object] = {
        "goal": goal,
        "completed": False,
        "decisions": ["decision-a"],
        "blockers": [],
        "nextAction": "continue work",
        "question": "what should happen next?",
    }
    payload.update(overrides)
    return payload


def store(
    path: Path,
    clock: Clock | None = None,
    *,
    handoff_ttl_seconds: float = 300.0,
    claim_ttl_seconds: float = 300.0,
) -> SessionHandoffStore:
    return SessionHandoffStore(
        db_path=path / "handoffs.db",
        metadata_audit_path=path / "handoff_audit.jsonl",
        clock=clock or Clock(),
        handoff_ttl_seconds=handoff_ttl_seconds,
        claim_ttl_seconds=claim_ttl_seconds,
        lock=threading.RLock(),
    )


def create_store_entry(
    root: Path,
    clock: Clock,
    owner: str = "owner-a",
    source_session: str = "src-a",
    target_session: str = "dst-b",
    scope: str = "",
    payload: dict[str, object] | None = None,
) -> tuple[SessionHandoffStore, dict[str, object]]:
    db = store(root, clock)
    row = db.create(
        owner_id=owner,
        source_session_id=source_session,
        target_session_id=target_session,
        source_chat_id=f"{source_session}-chat",
        target_chat_id=f"{target_session}-chat",
        source_revision=1,
        target_revision=1,
        source_scope=scope,
        target_scope=scope,
        payload=payload or make_payload(),
        reply_to=None,
        kind="handoff",
    )
    return db, row


def create_row(
    db: SessionHandoffStore,
    *,
    owner_id: str = "owner-a",
    source_session: str = "src-a",
    target_session: str = "dst-b",
    scope: str = "",
    payload: dict[str, object] | None = None,
) -> dict[str, object]:
    return db.create(
        owner_id=owner_id,
        source_session_id=source_session,
        target_session_id=target_session,
        source_chat_id=f"{source_session}-chat",
        target_chat_id=f"{target_session}-chat",
        source_revision=1,
        target_revision=1,
        source_scope=scope,
        target_scope=scope,
        payload=payload or make_payload(),
        reply_to=None,
        kind="handoff",
    )


def read(
    db: SessionHandoffStore,
    row: dict[str, object],
    *,
    session_id: str | None = None,
    scope: str | None = None,
) -> dict[str, object]:
    return db.get(
        handoff_id=str(row["id"]),
        owner_id=str(row["owner_id"]),
        session_id=session_id or str(row["source_session_id"]),
        scope=scope if scope is not None else str(row["source_scope"]),
    )


@pytest.mark.parametrize(
    ("override", "error_type"),
    [
        ({"owner_id": "owner-b"}, PermissionError),
        ({"target_session_id": "src-a"}, PermissionError),
        ({"scope": "other"}, PermissionError),
        ({"expected_revision": 99}, SessionHandoffError),
    ],
)
def test_target_action_authority_rejects_cross_binding_and_stale_cas_without_row_mutation(
    tmp_path: Path,
    override: dict[str, object],
    error_type: type[Exception],
) -> None:
    db, row = create_store_entry(tmp_path, Clock(), scope="project-a")
    before = db.binding(handoff_id=str(row["id"]), owner_id="owner-a")
    request: dict[str, object] = {
        "handoff_id": row["id"],
        "owner_id": "owner-a",
        "target_session_id": "dst-b",
        "scope": "project-a",
        "expected_revision": row["revision"],
        **override,
    }

    with pytest.raises(error_type):
        db.authorize_target_action(**request)

    assert db.binding(handoff_id=str(row["id"]), owner_id="owner-a") == before


def _claim_and_materialize(
    db: SessionHandoffStore,
    *,
    owner_id: str,
    row: dict[str, object],
) -> tuple[dict[str, object], dict[str, object]]:
    claimed = db.claim(
        handoff_id=str(row["id"]),
        owner_id=owner_id,
        session_id=str(row["source_session_id"]),
        scope=str(row["source_scope"]),
        expected_revision=row["revision"],
    )
    materialized = db.materialize(
        handoff_id=str(row["id"]),
        owner_id=owner_id,
        session_id=str(row["source_session_id"]),
        scope=str(row["source_scope"]),
        expected_revision=claimed["revision"],
        claim_token=str(claimed["claim_token"]),
    )
    return claimed, materialized


def _symlink_or_skip(link: Path, target: Path) -> None:
    try:
        link.symlink_to(target, target_is_directory=True)
    except (NotImplementedError, OSError) as exc:
        pytest.skip(f"directory symlink unavailable: {exc}")


def test_reject_unknown_fields_and_sensitive_content(tmp_path: Path) -> None:
    clock = Clock()
    db, _ = create_store_entry(tmp_path, clock)
    with pytest.raises(SessionHandoffError, match="unsupported fields"):
        db.create(
            owner_id="owner-a",
            source_session_id="src-a",
            target_session_id="dst-b",
            source_chat_id="src-a-chat", target_chat_id="dst-b-chat", source_revision=1, target_revision=1, kind="handoff", reply_to=None,
            source_scope="",
            target_scope="",
            payload={
                "goal": "x",
                "completed": True,
                "decisions": [],
                "blockers": [],
                "nextAction": "",
                "question": "",
                "tool": "vrc",
            },
        )
    with pytest.raises(SessionHandoffError, match="forbidden"):
        db.create(
            owner_id="owner-a",
            source_session_id="src-a",
            target_session_id="dst-b",
            source_chat_id="src-a-chat", target_chat_id="dst-b-chat", source_revision=1, target_revision=1, kind="handoff", reply_to=None,
            source_scope="",
            target_scope="",
            payload=make_payload(goal="Contains API_KEY-like text"),
        )
    with pytest.raises(SessionHandoffError, match="forbidden"):
        db.create(
            owner_id="owner-a",
            source_session_id="src-a",
            target_session_id="dst-b",
            source_chat_id="src-a-chat", target_chat_id="dst-b-chat", source_revision=1, target_revision=1, kind="handoff", reply_to=None,
            source_scope="",
            target_scope="",
            payload=make_payload(question="C:\\\\tmp\\\\secret"),
        )


def test_authorized_get_and_list_require_owner_session_scope(tmp_path: Path) -> None:
    clock = Clock()
    db, created = create_store_entry(tmp_path, clock)

    with pytest.raises(PermissionError, match="owner_id"):
        db.get(
            handoff_id=str(created["id"]),
            owner_id="other-owner",
            session_id=str(created["source_session_id"]),
            scope=str(created["source_scope"]),
        )
    with pytest.raises(PermissionError, match="session_id"):
        db.get(
            handoff_id=str(created["id"]),
            owner_id=str(created["owner_id"]),
            session_id="other-session",
            scope=str(created["source_scope"]),
        )
    with pytest.raises(PermissionError, match="scope"):
        db.get(
            handoff_id=str(created["id"]),
            owner_id=str(created["owner_id"]),
            session_id=str(created["source_session_id"]),
            scope="other-scope",
        )

    visible = db.get(
        handoff_id=str(created["id"]),
        owner_id=str(created["owner_id"]),
        session_id=str(created["source_session_id"]),
        scope=str(created["source_scope"]),
    )
    assert visible["id"] == created["id"]

    same_session = db.list(owner_id=str(created["owner_id"]), session_id=str(created["source_session_id"]), scope=str(created["source_scope"]))
    assert same_session == [visible]

    out_of_session = db.list(owner_id=str(created["owner_id"]), session_id="other-session", scope=str(created["source_scope"]))
    assert out_of_session == []


def test_source_and_target_sessions_must_differ(tmp_path: Path) -> None:
    db = store(tmp_path, Clock())
    with pytest.raises(SessionHandoffError, match="must differ"):
        db.create(
            owner_id="owner-a",
            source_session_id="same",
            target_session_id="same",
            source_chat_id="same-src-chat", target_chat_id="same-dst-chat", source_revision=1, target_revision=1, kind="handoff", reply_to=None,
            source_scope="",
            target_scope="",
            payload=make_payload(),
        )

def test_claim_expires_to_pending_before_total_ttl_then_expired_after_total_ttl(tmp_path: Path) -> None:
    db_clock = Clock(10.0)
    db = store(tmp_path, db_clock, handoff_ttl_seconds=120.0, claim_ttl_seconds=5.0)
    created = db.create(
        owner_id="owner-a",
        source_session_id="src-a",
        target_session_id="dst-b",
        source_chat_id="src-a-chat", target_chat_id="dst-b-chat", source_revision=1, target_revision=1, kind="handoff", reply_to=None,
        source_scope="",
        target_scope="",
        payload=make_payload(),
    )
    claimed = db.claim(
        handoff_id=str(created["id"]),
        owner_id=str(created["owner_id"]),
        session_id=str(created["source_session_id"]),
        scope=str(created["source_scope"]),
        expected_revision=created["revision"],
    )
    db_clock.advance(10.0)
    pending = read(db, created)
    assert pending["status"] == "pending_review"
    assert pending["revision"] == claimed["revision"] + 1
    reclaimed = db.claim(
        handoff_id=str(created["id"]),
        owner_id=str(created["owner_id"]),
        session_id=str(created["source_session_id"]),
        scope=str(created["source_scope"]),
        expected_revision=pending["revision"],
    )
    assert reclaimed["status"] == "claimed"

    db_clock.advance(120.0)
    with pytest.raises(SessionHandoffError, match="expected revision"):
        db.claim(
            handoff_id=str(created["id"]),
            owner_id=str(created["owner_id"]),
            session_id=str(created["source_session_id"]),
            scope=str(created["source_scope"]),
            expected_revision=reclaimed["revision"],
        )


def test_claim_and_total_ttl_expire_same_moment_converges_in_single_read(tmp_path: Path) -> None:
    db_clock = Clock(10.0)
    db = store(tmp_path, db_clock, handoff_ttl_seconds=10.0, claim_ttl_seconds=10.0)
    created = db.create(
        owner_id="owner-a",
        source_session_id="src-a",
        target_session_id="dst-b",
        source_chat_id="src-a-chat", target_chat_id="dst-b-chat", source_revision=1, target_revision=1, kind="handoff", reply_to=None,
        source_scope="",
        target_scope="",
        payload=make_payload(),
    )
    claimed = db.claim(
        handoff_id=str(created["id"]),
        owner_id=str(created["owner_id"]),
        session_id=str(created["source_session_id"]),
        scope=str(created["source_scope"]),
        expected_revision=created["revision"],
    )
    db_clock.advance(10.0)

    expired = read(db, created)
    assert expired["status"] == "expired"
    assert expired["revision"] == claimed["revision"] + 1

    listed = db.list(owner_id=str(created["owner_id"]), session_id=str(created["source_session_id"]), scope="", include_terminal=True)
    assert len(listed) == 1
    assert listed[0]["id"] == created["id"]
    assert listed[0]["status"] == "expired"
    assert listed[0]["revision"] == expired["revision"]


def test_scope_and_owner_session_guard_on_mutation(tmp_path: Path) -> None:
    clock = Clock()
    db, created = create_store_entry(tmp_path, clock, scope="")
    with pytest.raises(SessionHandoffError, match="must be equal"):
        db.create(
            owner_id="owner-a",
            source_session_id="src-a",
            target_session_id="dst-b",
            source_chat_id="src-a-chat", target_chat_id="dst-b-chat", source_revision=1, target_revision=1, kind="handoff", reply_to=None,
            source_scope="project-a",
            target_scope="project-b",
            payload=make_payload(),
        )

    with pytest.raises(PermissionError, match="scope"):
        db.claim(
            handoff_id=str(created["id"]),
            owner_id=str(created["owner_id"]),
            session_id=str(created["source_session_id"]),
            scope="wrong-scope",
            expected_revision=created["revision"],
        )
    with pytest.raises(PermissionError, match="owner_id"):
        db.claim(
            handoff_id=str(created["id"]),
            owner_id="other-owner",
            session_id=str(created["source_session_id"]),
            scope="",
            expected_revision=created["revision"],
        )


def test_cas_mismatch_blocking_keeps_state_stable(tmp_path: Path) -> None:
    clock = Clock()
    db, created = create_store_entry(tmp_path, clock)
    with pytest.raises(SessionHandoffError, match="expected revision"):
        db.claim(
            handoff_id=str(created["id"]),
            owner_id=str(created["owner_id"]),
            session_id=str(created["source_session_id"]),
            scope="",
            expected_revision=created["revision"] + 1,
        )
    first = read(db, created)
    assert first["revision"] == created["revision"]

    claimed = db.claim(
        handoff_id=str(created["id"]),
        owner_id=str(created["owner_id"]),
        session_id=str(created["source_session_id"]),
        scope="",
        expected_revision=created["revision"],
    )
    with pytest.raises(SessionHandoffError, match="expected revision"):
        db.materialize(
            handoff_id=str(created["id"]),
            owner_id=str(created["owner_id"]),
            session_id=str(created["source_session_id"]),
            scope="",
            expected_revision=created["revision"],
            claim_token=str(claimed["claim_token"]),
        )
    current = read(db, created)
    assert current["status"] == "claimed"
    assert current["revision"] == claimed["revision"]


def test_concurrent_claim_cas_once_only(tmp_path: Path) -> None:
    clock = Clock()
    db, created = create_store_entry(tmp_path, clock)
    expect = created["revision"]

    outcomes = {"ok": 0, "fail": 0}
    lock = threading.Lock()

    def worker(owner: str) -> None:
        local = store(tmp_path, Clock(clock.value))
        try:
            local.claim(
                handoff_id=str(created["id"]),
                owner_id=owner,
                session_id=str(created["source_session_id"]),
                scope="",
                expected_revision=expect,
            )
            with lock:
                outcomes["ok"] += 1
        except SessionHandoffError:
            with lock:
                outcomes["fail"] += 1

    first = threading.Thread(target=worker, args=("owner-a",))
    second = threading.Thread(target=worker, args=("owner-a",))
    first.start()
    second.start()
    first.join()
    second.join()

    assert outcomes["ok"] == 1
    assert outcomes["fail"] == 1
    current = read(db, created)
    assert current["revision"] == expect + 1


def test_expiry_transition_from_pending_requires_restart_compatible(tmp_path: Path) -> None:
    clock = Clock(1.0)
    db, created = create_store_entry(tmp_path, clock, source_session="src", target_session="dst", scope="project-scope")
    assert created["status"] == "pending_review"
    clock.advance(400.0)
    expired = read(db, created, scope=str(created["source_scope"]))
    assert expired["status"] == "expired"
    with pytest.raises(SessionHandoffError, match="expected revision"):
        db.claim(
            handoff_id=str(created["id"]),
            owner_id=str(created["owner_id"]),
            session_id=str(created["source_session_id"]),
            scope="project-scope",
            expected_revision=created["revision"],
        )


def test_restart_load_and_materialize_receipt_idempotent(tmp_path: Path) -> None:
    clock = Clock(20.0)
    first, created = create_store_entry(tmp_path, clock, source_session="src", target_session="dst", scope="project-a")
    claimed = first.claim(
        handoff_id=str(created["id"]),
        owner_id=str(created["owner_id"]),
        session_id=str(created["source_session_id"]),
        scope="project-a",
        expected_revision=created["revision"],
    )
    materialized = first.materialize(
        handoff_id=str(created["id"]),
        owner_id=str(created["owner_id"]),
        session_id=str(created["source_session_id"]),
        scope="project-a",
        expected_revision=claimed["revision"],
        claim_token=str(claimed["claim_token"]),
    )
    first.close()

    second = store(tmp_path, Clock(clock.value))
    refreshed = second.get(
        handoff_id=str(created["id"]),
        owner_id=str(created["owner_id"]),
        session_id=str(created["source_session_id"]),
        scope="project-a",
    )
    assert refreshed["status"] == "materialized"
    assert refreshed["materializeReceipt"] == materialized["materializeReceipt"]

    with pytest.raises(SessionHandoffError, match="already materialized"):
        second.materialize(
            handoff_id=str(created["id"]),
            owner_id=str(created["owner_id"]),
            session_id=str(created["source_session_id"]),
            scope="project-a",
            expected_revision=refreshed["revision"],
            claim_token="ignored",
        )
    other_owner, other_row = create_store_entry(tmp_path, Clock(), owner="owner-b", source_session="other-src", target_session="other-dst")
    other_claimed = other_owner.claim(
        handoff_id=str(other_row["id"]),
        owner_id=str(other_row["owner_id"]),
        session_id=str(other_row["source_session_id"]),
        scope=str(other_row["source_scope"]),
        expected_revision=other_row["revision"],
    )
    other_materialized = other_owner.materialize(
        handoff_id=str(other_row["id"]),
        owner_id=str(other_row["owner_id"]),
        session_id=str(other_row["source_session_id"]),
        scope=str(other_row["source_scope"]),
        expected_revision=other_claimed["revision"],
        claim_token=str(other_claimed["claim_token"]),
    )
    with pytest.raises(SessionHandoffError, match="already materialized"):
        second.materialize(
            handoff_id=str(created["id"]),
            owner_id=str(created["owner_id"]),
            session_id=str(created["source_session_id"]),
            scope="project-a",
            expected_revision=refreshed["revision"],
            claim_token=str(other_materialized["materializeReceipt"]),
        )
    with pytest.raises(SessionHandoffError, match="expected revision"):
        second.materialize(
            handoff_id=str(created["id"]),
            owner_id=str(created["owner_id"]),
            session_id=str(created["source_session_id"]),
            scope="project-a",
            expected_revision=refreshed["revision"] - 1,
            claim_token=str(refreshed["materializeReceipt"]),
        )

    with pytest.raises(PermissionError, match="session_id"):
        second.materialize(
            handoff_id=str(created["id"]),
            owner_id=str(created["owner_id"]),
            session_id="unbound-session",
            scope="project-a",
            expected_revision=refreshed["revision"],
            claim_token=str(refreshed["materializeReceipt"]),
        )

    replay = second.materialize(
        handoff_id=str(created["id"]),
        owner_id=str(created["owner_id"]),
        session_id=str(created["source_session_id"]),
        scope="project-a",
        expected_revision=refreshed["revision"],
        claim_token=str(refreshed["materializeReceipt"]),
    )
    assert replay["revision"] == refreshed["revision"]
    assert replay["materializeReceipt"] == refreshed["materializeReceipt"]


def test_one_bounded_reply_and_no_payload_in_metadata_audit(tmp_path: Path) -> None:
    clock = Clock()
    db, target = create_store_entry(tmp_path, clock, source_session="target-src", target_session="target-dst", scope="scope")
    _, _ = _claim_and_materialize(db, owner_id=str(target["owner_id"]), row=target)
    _, replyer = create_store_entry(tmp_path, clock, source_session="target-dst", target_session="target-src", scope="scope", owner="owner-a")
    _, replyer_materialized = _claim_and_materialize(db, owner_id=str(replyer["owner_id"]), row=replyer)

    replied = db.create(
        owner_id="owner-a", source_session_id="target-dst", target_session_id="target-src",
        source_chat_id="target-dst-chat", target_chat_id="target-src-chat",
        source_revision=1, target_revision=1, source_scope="scope", target_scope="scope",
        kind="reply", reply_to=str(target["id"]), payload=make_payload(goal="reply"),
    )
    assert replied["reply_to"] == str(target["id"])
    assert replied["status"] == "pending_review"
    with pytest.raises(SessionHandoffError, match="already replied"):
        db.create(
            owner_id="owner-a", source_session_id="target-dst", target_session_id="target-src",
            source_chat_id="target-dst-chat", target_chat_id="target-src-chat",
            source_revision=1, target_revision=1, source_scope="scope", target_scope="scope",
            kind="reply", reply_to=str(target["id"]), payload=make_payload(goal="reply2"),
        )

    pending_target = create_row(
        db,
        owner_id="owner-a",
        source_session="pending-a",
        target_session="pending-b",
        scope="scope",
    )
    pending_replyer = create_row(
        db,
        owner_id="owner-a",
        source_session="reply-src",
        target_session="reply-dst",
        scope="scope",
    )
    _, pending_replyer_materialized = _claim_and_materialize(db, owner_id=str(pending_replyer["owner_id"]), row=pending_replyer)
    with pytest.raises(SessionHandoffError, match="must be materialized"):
        db.create(
            owner_id="owner-a", source_session_id="pending-b", target_session_id="pending-a",
            source_chat_id="pending-b-chat", target_chat_id="pending-a-chat", source_revision=1, target_revision=1,
            source_scope="scope", target_scope="scope", kind="reply", reply_to=str(pending_target["id"]), payload=make_payload(),
        )

    wrong_direction_target = create_row(
        db,
        source_session="wrong-a",
        target_session="wrong-b",
        scope="scope",
    )
    _, _ = _claim_and_materialize(
        db,
        owner_id=str(wrong_direction_target["owner_id"]),
        row=wrong_direction_target,
    )
    wrong_direction_replyer = create_row(
        db,
        source_session="wrong-c",
        target_session="wrong-d",
        scope="scope",
    )
    _, wrong_direction_replyer_materialized = _claim_and_materialize(
        db,
        owner_id=str(wrong_direction_replyer["owner_id"]),
        row=wrong_direction_replyer,
    )
    with pytest.raises(SessionHandoffError, match="session direction is invalid"):
        db.create(
            owner_id="owner-a", source_session_id="wrong-c", target_session_id="wrong-d",
            source_chat_id="wrong-c-chat", target_chat_id="wrong-d-chat", source_revision=1, target_revision=1,
            source_scope="scope", target_scope="scope", kind="reply", reply_to=str(wrong_direction_target["id"]), payload=make_payload(),
        )

    mismatch_owner_target = create_row(
        db,
        owner_id="owner-b",
        source_session="owner-mismatch-d",
        target_session="owner-mismatch-c",
        scope="scope",
    )
    _, _ = _claim_and_materialize(
        db,
        owner_id=str(mismatch_owner_target["owner_id"]),
        row=mismatch_owner_target,
    )
    mismatch_replyer = create_row(
        db,
        owner_id="owner-a",
        source_session="owner-mismatch-c",
        target_session="owner-mismatch-d",
        scope="scope",
    )
    _, mismatch_replyer_materialized = _claim_and_materialize(
        db,
        owner_id=str(mismatch_replyer["owner_id"]),
        row=mismatch_replyer,
    )
    with pytest.raises(SessionHandoffError, match="owner mismatch"):
        db.create(
            owner_id="owner-a", source_session_id="owner-mismatch-c", target_session_id="owner-mismatch-d",
            source_chat_id="owner-mismatch-c-chat", target_chat_id="owner-mismatch-d-chat", source_revision=1, target_revision=1,
            source_scope="scope", target_scope="scope", kind="reply", reply_to=str(mismatch_owner_target["id"]), payload=make_payload(),
        )

    fake_lookup_target = create_row(
        db,
        owner_id="owner-a",
        source_session="fake-lookup-src",
        target_session="fake-lookup-dst",
        scope="scope",
    )
    _, fake_lookup_materialized = _claim_and_materialize(
        db,
        owner_id=str(fake_lookup_target["owner_id"]),
        row=fake_lookup_target,
    )
    with pytest.raises(KeyError):
        db.create(
            owner_id="owner-a", source_session_id="fake-lookup-dst", target_session_id="fake-lookup-src",
            source_chat_id="fake-lookup-dst-chat", target_chat_id="fake-lookup-src-chat", source_revision=1, target_revision=1,
            source_scope="scope", target_scope="scope", kind="reply", reply_to="fake-not-a-handoff-id", payload=make_payload(),
        )

    audit = (tmp_path / "handoff_audit.jsonl").read_text(encoding="utf-8")
    assert "goal" not in audit
    assert "provider" not in audit
    assert "tool" not in audit
    assert SESSION_HANDOFF_AUDIT_SCHEMA in audit


def test_list_is_session_bound(tmp_path: Path) -> None:
    clock = Clock()
    db = store(tmp_path, clock)
    a = db.create(
        owner_id="owner-a",
        source_session_id="session-a",
        target_session_id="session-b",
        source_chat_id="session-a-chat", target_chat_id="session-b-chat", source_revision=1, target_revision=1, kind="handoff", reply_to=None,
        source_scope="scope-a",
        target_scope="scope-a",
        payload=make_payload(),
    )
    b = db.create(
        owner_id="owner-a",
        source_session_id="session-c",
        target_session_id="session-d",
        source_chat_id="session-c-chat", target_chat_id="session-d-chat", source_revision=1, target_revision=1, kind="handoff", reply_to=None,
        source_scope="scope-a",
        target_scope="scope-a",
        payload=make_payload(),
    )

    source_matches = db.list(owner_id="owner-a", session_id="session-a", scope="scope-a")
    assert len(source_matches) == 1
    assert source_matches[0]["id"] == a["id"]

    target_matches = db.list(owner_id="owner-a", session_id="session-b", scope="scope-a")
    assert len(target_matches) == 1
    assert target_matches[0]["id"] == a["id"]

    unrelated = db.list(owner_id="owner-a", session_id="session-c", scope="scope-a")
    assert len(unrelated) == 1
    assert unrelated[0]["id"] == b["id"]


def test_ancestor_rejects_db_and_audit_path_ancestor_reparse_or_symlink(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    real_lstat = session_handoff.os.lstat
    db_path = tmp_path / "handoffs.db"
    audit_path = tmp_path / "handoff_audit.jsonl"
    db_parent = tmp_path / "db-parent"
    audit_parent = tmp_path / "audit-parent"
    db_parent.mkdir()
    audit_parent.mkdir()

    class BadMetadata:
        def __init__(self, wrapped: os.stat_result, *, on_path: Path) -> None:
            self._wrapped = wrapped
            self._on_path = on_path

        def __getattr__(self, name: str) -> object:
            return getattr(self._wrapped, name)

    class SymlinkMetadata(BadMetadata):
        def __init__(self, wrapped: os.stat_result, *, on_path: Path) -> None:
            super().__init__(wrapped, on_path=on_path)
            self.st_mode = (wrapped.st_mode & 0o777) | stat.S_IFLNK

    class ReparseMetadata(BadMetadata):
        def __init__(self, wrapped: os.stat_result, *, on_path: Path) -> None:
            super().__init__(wrapped, on_path=on_path)
            self.st_file_attributes = int(getattr(wrapped, "st_file_attributes", 0) or 0) | session_handoff.FILE_ATTRIBUTE_REPARSE_POINT
            self.st_mode = wrapped.st_mode

    def injected_lstat(path: os.PathLike[str] | str, *args: object, **kwargs: object) -> os.stat_result:
        resolved = Path(path)
        if resolved == db_parent:
            return ReparseMetadata(real_lstat(path, *args, **kwargs), on_path=resolved)  # type: ignore[return-value]
        if resolved == audit_parent:
            return SymlinkMetadata(real_lstat(path, *args, **kwargs), on_path=resolved)  # type: ignore[return-value]
        return real_lstat(path, *args, **kwargs)

    monkeypatch.setattr(session_handoff.os, "lstat", injected_lstat)

    with pytest.raises(OSError, match="link or reparse point"):
        SessionHandoffStore(db_path=db_parent / "handoffs.db", metadata_audit_path=audit_path, clock=Clock()).close()
    with pytest.raises(OSError, match="link or reparse point"):
        SessionHandoffStore(db_path=db_path, metadata_audit_path=audit_parent / "handoff_audit.jsonl", clock=Clock()).close()


def test_cancel_only_before_claim_and_dismiss_from_any_non_terminal_state(tmp_path: Path) -> None:
    clock = Clock()
    db, created = create_store_entry(tmp_path, clock)
    cancelled = db.cancel(
        handoff_id=str(created["id"]),
        owner_id=str(created["owner_id"]),
        session_id=str(created["source_session_id"]),
        scope="",
        expected_revision=created["revision"],
    )
    assert cancelled["status"] == "cancelled"

    db2, pending = create_store_entry(tmp_path / "second", Clock(), source_session="src", target_session="dst")
    claimed = db2.claim(
        handoff_id=str(pending["id"]),
        owner_id=str(pending["owner_id"]),
        session_id=str(pending["source_session_id"]),
        scope="",
        expected_revision=pending["revision"],
    )
    with pytest.raises(SessionHandoffError, match="only pending_review"):
        db2.cancel(
            handoff_id=str(pending["id"]),
            owner_id=str(pending["owner_id"]),
            session_id=str(pending["source_session_id"]),
            scope="",
            expected_revision=claimed["revision"],
        )
    dismissed = db2.dismiss(
        handoff_id=str(pending["id"]),
        owner_id=str(pending["owner_id"]),
        session_id=str(pending["source_session_id"]),
        scope="",
        expected_revision=claimed["revision"],
    )
    assert dismissed["status"] == "dismissed"


def test_chat_revision_kind_and_payload_digest_are_immutable_projection(tmp_path: Path) -> None:
    db = store(tmp_path, Clock())
    row = db.create(
        owner_id="owner-a",
        source_session_id="src-session",
        target_session_id="dst-session",
        source_chat_id="src-chat",
        target_chat_id="dst-chat",
        source_revision=7,
        target_revision=9,
        kind="question",
        reply_to=None,
        source_scope="scope",
        target_scope="scope",
        payload=make_payload(goal="bounded question"),
    )
    assert row["source_chat_id"] == "src-chat"
    assert row["target_chat_id"] == "dst-chat"
    assert row["source_revision"] == 7
    assert row["target_revision"] == 9
    assert row["kind"] == "question"
    assert row["payloadDigest"] == session_handoff._payload_digest(
        session_handoff._normalize_payload(make_payload(goal="bounded question"))
    )
    refreshed = db.get(
        handoff_id=str(row["id"]), owner_id="owner-a", session_id="src-session", scope="scope"
    )
    for key in ("source_chat_id", "target_chat_id", "source_revision", "target_revision", "kind", "payloadDigest"):
        assert refreshed[key] == row[key]

    with pytest.raises(SessionHandoffError, match="source_chat_id is required"):
        db.create(
            owner_id="owner-a", source_session_id="src-session", target_session_id="dst-session",
            source_chat_id="", target_chat_id="dst-chat", source_revision=1, target_revision=1,
            source_scope="scope", target_scope="scope", kind="handoff", reply_to=None,
            payload=make_payload(),
        )
    with pytest.raises(SessionHandoffError, match="target_chat_id is required"):
        db.create(
            owner_id="owner-a", source_session_id="src-session", target_session_id="dst-session",
            source_chat_id="src-chat", target_chat_id="", source_revision=1, target_revision=1,
            source_scope="scope", target_scope="scope", kind="handoff", reply_to=None,
            payload=make_payload(),
        )

    with pytest.raises(SessionHandoffError, match="chat_id.*must differ"):
        db.create(
            owner_id="owner-a",
            source_session_id="src-session",
            target_session_id="dst-session",
            source_chat_id="same-chat",
            target_chat_id="same-chat",
            source_revision=1, target_revision=1, kind="handoff", reply_to=None,
            source_scope="scope",
            target_scope="scope",
            payload=make_payload(),
        )
    with pytest.raises(SessionHandoffError, match="only kind=reply"):
        db.create(
            owner_id="owner-a",
            source_session_id="src-session",
            target_session_id="dst-session",
            source_chat_id="src-session-chat", target_chat_id="dst-session-chat", source_revision=1, target_revision=1,
            source_scope="scope",
            target_scope="scope",
            kind="question",
            reply_to=str(row["id"]),
            payload=make_payload(),
        )


def test_reply_kind_binds_opposite_chat_direction_and_materialized_target(tmp_path: Path) -> None:
    clock = Clock()
    db, target = create_store_entry(tmp_path, clock, source_session="src", target_session="dst")
    _, target_materialized = _claim_and_materialize(db, owner_id="owner-a", row=target)
    replyer = db.create(
        owner_id="owner-a",
        source_session_id="dst",
        target_session_id="src",
        source_chat_id="dst",
        target_chat_id="src",
        source_revision=1,
        target_revision=1,
        source_scope="",
        target_scope="",
        kind="question",
        reply_to=None,
        payload=make_payload(goal="reply"),
    )
    _, materialized = _claim_and_materialize(db, owner_id="owner-a", row=replyer)
    replied = db.create(
        owner_id="owner-a", source_session_id="dst", target_session_id="src",
        source_chat_id="dst-chat", target_chat_id="src-chat", source_revision=1, target_revision=1,
        source_scope="", target_scope="", kind="reply", reply_to=str(target["id"]),
        payload=make_payload(goal="reply-record"),
    )
    assert replied["kind"] == "reply"
    assert replied["reply_to"] == target["id"]
    unchanged_target = db.get(handoff_id=str(target["id"]), owner_id="owner-a", session_id="src", scope="")
    assert unchanged_target["kind"] == "handoff"
    assert unchanged_target["status"] == "materialized"

    wrong_chat = db.create(
        owner_id="owner-a", source_session_id="dst", target_session_id="src",
        source_chat_id="other-chat", target_chat_id="src", source_scope="", target_scope="",
        source_revision=1, target_revision=1, kind="handoff", reply_to=None,
        payload=make_payload(),
    )
    _, wrong_materialized = _claim_and_materialize(db, owner_id="owner-a", row=wrong_chat)
    with pytest.raises(SessionHandoffError, match="direction is invalid"):
        db.create(
            owner_id="owner-a", source_session_id="dst", target_session_id="src",
            source_chat_id="other-chat", target_chat_id="src-chat", source_revision=1, target_revision=1,
            source_scope="", target_scope="", kind="reply", reply_to=str(target["id"]),
            payload=make_payload(),
        )
