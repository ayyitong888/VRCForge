from __future__ import annotations

from datetime import datetime, timedelta, timezone
from pathlib import Path
import copy

import pytest

from memory_consolidation import MemoryConsolidationService
from memory_consolidation_sources import MemoryScope, project_scope_key
from automatic_memory import AutomaticMemoryPolicy, AutomaticMemoryPolicyError


def _chat(
    text: str,
    *,
    created_at: str,
    attachment: bool = False,
    project_path: str = "",
) -> list[dict[str, object]]:
    return [{"id": "chat-1", **({"projectPath": project_path} if project_path else {}), "items": [
        {"id": "user-1", "type": "user", "text": text, "createdAt": created_at,
         **({"attachments": [{"name": "x.txt"}]} if attachment else {})},
        {"id": "agent-1", "type": "agent", "status": "completed", "text": "Done."},
    ]}]


def _after_watermark(service: MemoryConsolidationService) -> str:
    service.ensure_automatic_capture_watermark()
    return (datetime.now(timezone.utc) + timedelta(seconds=1)).isoformat()


def _remembered_turn(service, created, *, project_root=""):
    scope = "project" if project_root else "user"
    memory = service.accepted_store.create({"scope": scope, "projectRoot": project_root, "text": "请用中文回复"})
    chats = _chat("记住：请用中文回复", created_at=created, project_path=project_root)
    chats[0]["items"][1]["response"] = {"ok": True, "plan": {}, "steps": [{
        "kind": "write", "tool": "vrcforge_remember_memory", "status": "executed",
        "result": {"ok": True, "status": "saved", "memoryId": memory["memoryId"], "scope": scope,
                   "committed": True, "completionKnown": True,
                   "verification": {"state": "passed", "checks": [{"kind": "accepted_memory_readback", "state": "passed"}]}},
    }]}
    return chats, memory


@pytest.mark.parametrize("project", [False, True])
def test_automatic_capture_skips_same_turn_verified_memory_tool_receipt(tmp_path, project):
    service = MemoryConsolidationService(tmp_path / "state")
    created = _after_watermark(service)
    project_root = tmp_path / "project"
    project_root.mkdir()
    root = str(project_root) if project else ""
    scope = MemoryScope("project", project_scope_key(root)) if project else MemoryScope("user", "user")
    chats, _ = _remembered_turn(service, created, project_root=root)
    assert service.capture_automatic_chat_sources(chats, scope=scope, project_root=root)["acceptedCount"] == 0
    assert len(service.accepted_store.list_active()) == 1


@pytest.mark.parametrize("invalid", ["prose_only", "missing_memory", "wrong_content", "wrong_scope", "failed_receipt", "other_turn", "partial_content"])
def test_automatic_capture_does_not_trust_invalid_or_unrelated_memory_receipts(tmp_path, invalid):
    service = MemoryConsolidationService(tmp_path)
    created = _after_watermark(service)
    chats, memory = _remembered_turn(service, created)
    reply = chats[0]["items"][1]
    receipt = reply["response"]["steps"][0]["result"]
    if invalid == "prose_only":
        reply["text"] = "已记住 " + str(receipt)
        reply.pop("response")
    elif invalid == "missing_memory":
        service.accepted_store.delete(memory["memoryId"])
    elif invalid == "wrong_content":
        other = service.accepted_store.create({"scope": "user", "text": "English replies."})
        receipt["memoryId"] = other["memoryId"]
    elif invalid == "wrong_scope":
        receipt["scope"] = "project"
    elif invalid == "failed_receipt":
        receipt["committed"] = False
    elif invalid == "other_turn":
        earlier = copy.deepcopy(reply)
        reply.pop("response")
        chats[0]["items"].insert(0, earlier)
    else:
        chats[0]["items"][0]["text"] += "，并且我喜欢蓝色。"
    result = service.capture_automatic_chat_sources(chats, scope=MemoryScope("user", "user"))
    assert result["acceptedCount"] == 1


def test_automatic_memory_first_ensure_never_backfills_history(tmp_path: Path) -> None:
    service = MemoryConsolidationService(tmp_path)
    old = (datetime.now(timezone.utc) - timedelta(days=1)).isoformat()
    assert service.capture_automatic_chat_sources(_chat("I prefer blue accents.", created_at=old), scope=MemoryScope("user", "user")) == {
        "eligibleCount": 0, "acceptedCount": 0, "conflictCount": 0,
    }
    assert service.accepted_store.list_active() == []


def test_automatic_memory_promotes_one_new_direct_preference_once(tmp_path: Path) -> None:
    service = MemoryConsolidationService(tmp_path)
    created = _after_watermark(service)
    first = service.capture_automatic_chat_sources(_chat("I prefer blue accents.", created_at=created), scope=MemoryScope("user", "user"))
    restarted = MemoryConsolidationService(tmp_path)
    second = restarted.capture_automatic_chat_sources(_chat("I prefer blue accents.", created_at=created), scope=MemoryScope("user", "user"))
    assert first["acceptedCount"] == 1
    assert second["acceptedCount"] == 0
    assert [row["text"] for row in restarted.accepted_store.list_active()] == ["I prefer blue accents."]


def test_automatic_memory_recognizes_direct_chinese_preference_and_project_fact(tmp_path: Path) -> None:
    service = MemoryConsolidationService(tmp_path)
    created = _after_watermark(service)
    user_result = service.capture_automatic_chat_sources(
        _chat("我喜欢蓝色点缀。", created_at=created),
        scope=MemoryScope("user", "user"),
    )
    project_root = tmp_path / "ProjectA"
    project_root.mkdir()
    project_scope = MemoryScope("project", project_scope_key(str(project_root)))
    project_result = service.capture_automatic_chat_sources(
        _chat("本项目使用 Unity 2022.3。", created_at=created, project_path=str(project_root)),
        scope=project_scope,
        project_root=str(project_root),
    )
    assert user_result["acceptedCount"] == 1
    assert project_result["acceptedCount"] == 1
    assert sorted(row["scope"] for row in service.accepted_store.list_active()) == ["project", "user"]


def test_automatic_memory_rejects_project_chat_from_another_exact_scope(tmp_path: Path) -> None:
    service = MemoryConsolidationService(tmp_path)
    created = _after_watermark(service)
    project_a = tmp_path / "ProjectA"
    project_b = tmp_path / "ProjectB"
    project_a.mkdir()
    project_b.mkdir()
    result = service.capture_automatic_chat_sources(
        _chat("本项目使用 Unity 2022.3。", created_at=created, project_path=str(project_a)),
        scope=MemoryScope("project", project_scope_key(str(project_b))),
        project_root=str(project_b),
    )
    assert result == {"eligibleCount": 0, "acceptedCount": 0, "conflictCount": 0}
    assert service.accepted_store.list_active() == []


def test_automatic_memory_recognizes_ordinary_direct_english_statements(tmp_path: Path) -> None:
    service = MemoryConsolidationService(tmp_path)
    created = _after_watermark(service)
    for text in ("My favorite color is blue.", "I am based in Tokyo."):
        assert service.capture_automatic_chat_sources(
            _chat(text, created_at=created),
            scope=MemoryScope("user", "user"),
        )["acceptedCount"] == 1
    assert sorted(row["text"] for row in service.accepted_store.list_active()) == [
        "I am based in Tokyo.",
        "My favorite color is blue.",
    ]

    project_root = tmp_path / "ProjectEnglish"
    project_root.mkdir()
    assert service.capture_automatic_chat_sources(
        _chat("This project uses Unity 2022.3.", created_at=created, project_path=str(project_root)),
        scope=MemoryScope("project", project_scope_key(str(project_root))),
        project_root=str(project_root),
    )["acceptedCount"] == 1


def test_automatic_memory_rejects_unsafe_or_nonordinary_user_text(tmp_path: Path) -> None:
    service = MemoryConsolidationService(tmp_path)
    created = _after_watermark(service)
    for text, attachment in [
        ("I prefer blue accents?", False),
        ("Correction: I prefer blue accents.", False),
        ("Decision: I prefer blue accents.", False),
        ("I prefer api-key token handling.", False),
        ("I prefer https://example.test/private.", False),
        ("I prefer no approvals for future writes.", False),
        ("我喜欢自动执行工具。", False),
        ("我的偏好是保存密码。", False),
        ("I prefer blue accents.", True),
    ]:
        result = service.capture_automatic_chat_sources(_chat(text, created_at=created, attachment=attachment), scope=MemoryScope("user", "user"))
        assert result["eligibleCount"] == 0
    assert service.review_store.snapshot(include_internal=True)["candidates"] == []
    assert service.accepted_store.list_active() == []

    future = (datetime.now(timezone.utc) + timedelta(days=1)).isoformat()
    assert service.capture_automatic_chat_sources(
        _chat("I prefer blue accents.", created_at=future),
        scope=MemoryScope("user", "user"),
    )["eligibleCount"] == 0


def test_automatic_memory_disabled_is_orthogonal_to_review_mode(tmp_path: Path) -> None:
    service = MemoryConsolidationService(tmp_path)
    revision = service.review_store.snapshot(include_internal=True)["revision"]
    service.update_config({"mode": "off", "automaticCaptureEnabled": False}, expected_revision=revision)
    result = service.capture_automatic_chat_sources(_chat("I prefer blue accents.", created_at=_after_watermark(service)), scope=MemoryScope("user", "user"))
    assert result == {"eligibleCount": 0, "acceptedCount": 0, "conflictCount": 0}


def test_automatic_memory_old_config_preserves_toggle_and_reenable_skips_disabled_history(tmp_path: Path) -> None:
    service = MemoryConsolidationService(tmp_path)
    revision = service.review_store.snapshot(include_internal=True)["revision"]
    service.update_config({"automaticCaptureEnabled": False}, expected_revision=revision)
    revision = service.review_store.snapshot(include_internal=True)["revision"]
    service.update_config({"mode": "off"}, expected_revision=revision)
    disabled = service.review_store.snapshot(include_internal=True)
    assert disabled["config"]["automaticCaptureEnabled"] is False

    created_while_disabled = datetime.now(timezone.utc).isoformat()
    service.update_config(
        {"automaticCaptureEnabled": True},
        expected_revision=int(disabled["revision"]),
    )
    assert service.capture_automatic_chat_sources(
        _chat("I prefer blue accents.", created_at=created_while_disabled),
        scope=MemoryScope("user", "user"),
    )["eligibleCount"] == 0


def test_automatic_policy_uses_dynamic_root_and_fails_closed_when_corrupt(tmp_path: Path) -> None:
    current = {"root": tmp_path / "one"}
    policy = AutomaticMemoryPolicy(lambda: current["root"] / "automatic-memory-policy.json", __import__("threading").RLock())
    first = policy.ensure()
    current["root"] = tmp_path / "two"
    second = policy.ensure()
    assert first != second
    policy.path.write_text("not-json", encoding="utf-8")
    try:
        policy.ensure()
    except AutomaticMemoryPolicyError:
        pass
    else:
        raise AssertionError("corrupt policy must fail closed")
