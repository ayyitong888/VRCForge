import json
import threading
from pathlib import Path

from agent_runtime_followup_queue import AgentRuntimeFollowupQueue, FollowupQueuePorts


class Clock:
    def __init__(self, value: float = 100.0) -> None:
        self.value = value

    def __call__(self) -> float:
        return self.value


def make_queue(path: Path, clock: Clock | None = None) -> AgentRuntimeFollowupQueue:
    return AgentRuntimeFollowupQueue(
        FollowupQueuePorts(path=path, lock=threading.RLock(), now=clock or Clock())
    )


def test_followups_are_durable_fifo_and_not_capped_at_eight(tmp_path: Path) -> None:
    path = tmp_path / "followups.json"
    queue = make_queue(path)
    for index in range(32):
        result = queue.enqueue(
            session_id="session-a",
            client_turn_id=f"turn-{index:02d}",
            message=f"message {index}",
        )
        assert result["accepted"] is True
        assert result["sequence"] == index + 1

    restarted = make_queue(path)
    delivered = []
    for _index in range(32):
        claimed = restarted.claim(session_id="session-a", owner_id="desktop-a", limit=64)
        assert len(claimed) == 1
        item = claimed[0]
        delivered.append(item["clientTurnId"])
        assert restarted.ack(queue_id=item["queueId"], session_id="session-a", claim_token=item["claimToken"])
    assert delivered == [f"turn-{index:02d}" for index in range(32)]


def test_followup_idempotency_restart_and_claim_token_binding(tmp_path: Path) -> None:
    path = tmp_path / "followups.json"
    first = make_queue(path)
    accepted = first.enqueue(session_id="session-a", client_turn_id="turn-a", message="hello")
    duplicate = make_queue(path).enqueue(session_id="session-a", client_turn_id="turn-a", message="hello")
    assert duplicate["accepted"] is True
    assert duplicate["deduped"] is True
    assert duplicate["queueId"] == accepted["queueId"]

    claimed = make_queue(path).claim(session_id="session-a", owner_id="desktop-a", limit=1)[0]
    queue = make_queue(path)
    assert queue.ack(queue_id=claimed["queueId"], session_id="wrong", claim_token=claimed["claimToken"]) is False
    assert queue.ack(queue_id=claimed["queueId"], session_id="session-a", claim_token="wrong") is False
    assert queue.ack(queue_id=claimed["queueId"], session_id="session-a", claim_token=claimed["claimToken"]) is True


def test_expired_claim_is_reclaimable_without_reordering(tmp_path: Path) -> None:
    clock = Clock()
    queue = make_queue(tmp_path / "followups.json", clock)
    queue.enqueue(session_id="session-a", client_turn_id="turn-a", message="a")
    queue.enqueue(session_id="session-a", client_turn_id="turn-b", message="b")
    first = queue.claim(session_id="session-a", owner_id="old", limit=1)[0]
    clock.value += queue.LEASE_SECONDS + 1
    reclaimed = queue.claim(session_id="session-a", owner_id="new", limit=1)[0]
    assert reclaimed["queueId"] == first["queueId"]
    assert reclaimed["claimToken"] != first["claimToken"]


def test_attachment_only_followup_keeps_safe_references_not_inline_payloads(tmp_path: Path) -> None:
    path = tmp_path / "followups.json"
    result = make_queue(path).enqueue(
        session_id="session-a",
        client_turn_id="turn-a",
        message="",
        attachments=[{
            "id": "attachment-a",
            "name": "scene.png",
            "type": "image/png",
            "size": 10,
            "payloadHash": "sha256:abc",
            "dataUrl": "data:image/png;base64,secret",
            "token": "secret-token",
        }],
    )
    assert result["accepted"] is True
    raw = path.read_text(encoding="utf-8")
    assert "data:image" not in raw
    assert "secret-token" not in raw
    item = json.loads(raw)[0]
    assert item["attachments"] == [{
        "id": "attachment-a",
        "name": "scene.png",
        "payloadHash": "sha256:abc",
        "size": 10,
        "type": "image/png",
    }]


def test_corrupt_store_is_preserved_and_new_input_gets_backpressure(tmp_path: Path) -> None:
    path = tmp_path / "followups.json"
    original = b"{not-json"
    path.write_bytes(original)
    result = make_queue(path).enqueue(session_id="session-a", client_turn_id="turn-a", message="hello")
    assert result == {
        "accepted": False,
        "mode": "followup",
        "reason": "durable_store_unavailable",
        "status": "backpressure",
    }
    assert path.read_bytes() == original


def test_persist_failures_roll_back_in_memory_claim_ack_and_cancel(tmp_path: Path, monkeypatch) -> None:
    queue = make_queue(tmp_path / "followups.json")
    queue.enqueue(session_id="session-a", client_turn_id="turn-a", message="a")
    second = queue.enqueue(session_id="session-a", client_turn_id="turn-b", message="b")
    persist = queue._persist

    def fail_persist() -> None:
        raise OSError("disk unavailable")

    monkeypatch.setattr(queue, "_persist", fail_persist)
    try:
        queue.claim(session_id="session-a", owner_id="desktop-a", limit=1)
    except OSError:
        pass
    else:
        raise AssertionError("claim must report a durable persistence failure")
    assert queue.list(session_id="session-a", include_terminal=True)[0]["status"] == "pending"

    monkeypatch.setattr(queue, "_persist", persist)
    claimed = queue.claim(session_id="session-a", owner_id="desktop-a", limit=1)[0]
    monkeypatch.setattr(queue, "_persist", fail_persist)
    try:
        queue.ack(queue_id=claimed["queueId"], session_id="session-a", claim_token=claimed["claimToken"])
    except OSError:
        pass
    else:
        raise AssertionError("ack must report a durable persistence failure")
    assert queue.list(session_id="session-a", include_terminal=True)[0]["status"] == "claimed"

    monkeypatch.setattr(queue, "_persist", persist)
    assert queue.ack(queue_id=claimed["queueId"], session_id="session-a", claim_token=claimed["claimToken"])
    monkeypatch.setattr(queue, "_persist", fail_persist)
    try:
        queue.cancel(queue_id=second["queueId"], session_id="session-a")
    except OSError:
        pass
    else:
        raise AssertionError("cancel must report a durable persistence failure")
    statuses = {item["clientTurnId"]: item["status"] for item in queue.list(session_id="session-a", include_terminal=True)}
    assert statuses == {"turn-a": "acked", "turn-b": "pending"}


def test_resource_backpressure_is_byte_based_and_terminal_content_is_compacted(tmp_path: Path) -> None:
    path = tmp_path / "followups.json"
    queue = make_queue(path)
    accepted = queue.enqueue(session_id="session-a", client_turn_id="turn-a", message="private follow-up")
    claimed = queue.claim(session_id="session-a", owner_id="desktop-a", limit=1)[0]
    assert queue.ack(queue_id=accepted["queueId"], session_id="session-a", claim_token=claimed["claimToken"])
    assert "private follow-up" not in path.read_text(encoding="utf-8")

    queue.MAX_STORE_BYTES = len(path.read_bytes()) + 32
    blocked = queue.enqueue(session_id="session-a", client_turn_id="turn-b", message="x" * 200)
    assert blocked == {
        "accepted": False,
        "mode": "followup",
        "reason": "durable_store_capacity",
        "status": "backpressure",
    }
    assert [item["clientTurnId"] for item in queue.list(session_id="session-a")] == ["turn-a"]
