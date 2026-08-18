from __future__ import annotations

from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

from memory_consolidation import MemoryConsolidationError, MemoryConsolidationService
from memory_consolidation_sources import MemoryScope


def _chat(text: str, created_at: str) -> list[dict[str, object]]:
    return [{
        "id": "chat-memory-preferences",
        "items": [
            {
                "id": "user-memory-preferences",
                "type": "user",
                "text": text,
                "createdAt": created_at,
            },
            {
                "id": "agent-memory-preferences",
                "type": "agent",
                "status": "completed",
                "text": "Done.",
            },
        ],
    }]


def _update_preferences(
    service: MemoryConsolidationService,
    *,
    memory_enabled: bool,
    cross_session_enabled: bool,
) -> None:
    revision = int(service.review_store.snapshot(include_internal=True)["revision"])
    service.update_config(
        {
            "memoryEnabled": memory_enabled,
            "crossSessionEnabled": cross_session_enabled,
        },
        expected_revision=revision,
    )


def test_memory_preferences_gate_capture_without_deleting_existing_memory(tmp_path: Path) -> None:
    service = MemoryConsolidationService(tmp_path)
    existing = service.accepted_store.create({
        "scope": "user",
        "kind": "preference",
        "text": "Keep compact status updates.",
    })
    service.ensure_automatic_capture_watermark()
    created_at = (datetime.now(timezone.utc) + timedelta(seconds=1)).isoformat()

    _update_preferences(service, memory_enabled=True, cross_session_enabled=False)
    assert service.memory_preferences() == {
        "memoryEnabled": True,
        "crossSessionEnabled": False,
    }
    assert service.capture_automatic_chat_sources(
        _chat("I prefer blue accents.", created_at),
        scope=MemoryScope("user", "user"),
    ) == {"eligibleCount": 0, "acceptedCount": 0, "conflictCount": 0}
    assert service.accepted_store.get(str(existing["memoryId"])) is not None

    _update_preferences(service, memory_enabled=False, cross_session_enabled=True)
    assert service.memory_preferences() == {
        "memoryEnabled": False,
        "crossSessionEnabled": False,
    }
    assert service.accepted_store.get(str(existing["memoryId"])) is not None


def test_dreaming_requires_second_model_review_before_deduplicating_saved_memory(
    tmp_path: Path,
) -> None:
    service = MemoryConsolidationService(tmp_path)
    first = service.accepted_store.create({
        "scope": "user",
        "kind": "preference",
        "text": "Keep compact status updates.",
    })
    duplicate = service.accepted_store.create({
        "scope": "user",
        "kind": "preference",
        "text": "Status updates should stay compact.",
    })
    false_positive_keep = service.accepted_store.create({
        "scope": "user",
        "kind": "fact",
        "text": "The project uses Unity 2022.3.",
    })
    false_positive_remove = service.accepted_store.create({
        "scope": "user",
        "kind": "fact",
        "text": "The preferred editor language is English.",
    })
    missed_keep = service.accepted_store.create({
        "scope": "user",
        "kind": "fact",
        "text": "Status reports should include validation results.",
    })
    missed_remove = service.accepted_store.create({
        "scope": "user",
        "kind": "fact",
        "text": "Include validation results in status reports.",
    })
    extra_one = service.accepted_store.create({
        "scope": "user",
        "kind": "fact",
        "text": "The release branch is main.",
    })
    extra_two = service.accepted_store.create({
        "scope": "user",
        "kind": "preference",
        "text": "Use concise release notes.",
    })
    run_at = datetime(2026, 8, 18, 6, 0, tzinfo=timezone.utc)

    prepared = service.prepare_dreaming(run_at)
    assert prepared["prepared"] is True
    assert prepared["request"]["schema"] == "vrcforge.memory_dreaming_plan_request.v1"
    assert all("projectRoot" not in memory for memory in prepared["memories"])
    first_pass = {
        "duplicateGroups": [
            {"keepId": first["memoryId"], "removeIds": [duplicate["memoryId"]]},
            {
                "keepId": false_positive_keep["memoryId"],
                "removeIds": [false_positive_remove["memoryId"]],
            },
        ]
    }
    review_request = service.build_dreaming_review_request(prepared, first_pass)
    assert review_request["schema"] == "vrcforge.memory_dreaming_review_request.v1"
    assert review_request["proposal"] == first_pass["duplicateGroups"]

    with pytest.raises(MemoryConsolidationError, match="second model review"):
        service.commit_dreaming(prepared, first_pass, run_at)

    reviewed = {
        "reviewed": True,
        "duplicateGroups": [
            {"keepId": first["memoryId"], "removeIds": [duplicate["memoryId"]]},
            {"keepId": missed_keep["memoryId"], "removeIds": [missed_remove["memoryId"]]},
        ],
    }
    result = service.commit_dreaming(prepared, reviewed, run_at)

    assert result["reason"] == "completed"
    assert result["deduplicatedCount"] == 2
    active_ids = {str(row["memoryId"]) for row in service.accepted_store.list_active()}
    assert str(first["memoryId"]) in active_ids
    assert str(duplicate["memoryId"]) not in active_ids
    assert str(false_positive_keep["memoryId"]) in active_ids
    assert str(false_positive_remove["memoryId"]) in active_ids
    assert str(missed_keep["memoryId"]) in active_ids
    assert str(missed_remove["memoryId"]) not in active_ids
    assert len(active_ids) == 6

    cadence = service.prepare_dreaming(run_at + timedelta(hours=1))
    assert cadence["due"] is False
    assert cadence["reason"] == "cadence"

    rollback = service.rollback_last_dreaming()
    assert rollback["rolledBack"] is True
    assert rollback["restoredCount"] == 2
    restored_ids = {str(row["memoryId"]) for row in service.accepted_store.list_active()}
    assert restored_ids == {
        str(first["memoryId"]),
        str(duplicate["memoryId"]),
        str(false_positive_keep["memoryId"]),
        str(false_positive_remove["memoryId"]),
        str(missed_keep["memoryId"]),
        str(missed_remove["memoryId"]),
        str(extra_one["memoryId"]),
        str(extra_two["memoryId"]),
    }


def test_dreaming_uses_saved_memory_even_when_cross_session_recall_is_disabled(
    tmp_path: Path,
) -> None:
    service = MemoryConsolidationService(tmp_path)
    _update_preferences(service, memory_enabled=True, cross_session_enabled=False)
    for index in range(5):
        service.accepted_store.create({
            "scope": "user",
            "kind": "fact",
            "text": f"Saved Memory {index}.",
        })
    result = service.prepare_dreaming(datetime.now(timezone.utc))
    assert result["due"] is True
    assert result["prepared"] is True

    _update_preferences(service, memory_enabled=False, cross_session_enabled=False)
    result = service.prepare_dreaming(datetime.now(timezone.utc))
    assert result == {
        "due": False,
        "reason": "memory_disabled",
        "revision": result["revision"],
        "prepared": False,
    }
