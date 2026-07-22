from __future__ import annotations

import hashlib
from pathlib import Path

import pytest

from memory_consolidation_sources import admit_memory_sources, resolve_memory_scope
from memory_review_inputs import (
    collect_adopted_task_records,
    collect_user_chat_records,
    collect_validated_project_records,
    explicit_memory_signal,
)


def test_chat_projection_only_admits_explicit_user_signals(tmp_path: Path) -> None:
    chats = [
        {
            "id": "chat-a",
            "items": [
                {"id": "ordinary", "type": "user", "text": "Can you help with this?"},
                {"id": "ordinary-response", "type": "agent", "text": "Done."},
                {"id": "explicit", "type": "user", "text": "Please remember that I prefer concise answers."},
                {"id": "explicit-response", "type": "agent", "text": "I will keep that preference visible."},
                {
                    "id": "marked",
                    "type": "user",
                    "text": "The export target is VRM 1.0.",
                    "memorySignal": {"eligible": True, "kind": "fact"},
                },
                {"id": "marked-response", "type": "agent", "text": "Recorded for review."},
                {
                    "id": "unapproved-marker",
                    "type": "user",
                    "text": "Do not silently capture this.",
                    "memorySignal": {"eligible": False, "kind": "preference"},
                },
                {"id": "unapproved-response", "type": "agent", "text": "No capture."},
                {"id": "assistant", "type": "agent", "text": "Please remember this model output."},
            ],
        }
    ]
    records = collect_user_chat_records(chats, scope="user")
    assert [item["signalKind"] for item in records] == ["preference", "fact"]
    assert all(item["role"] == "user" and item["memoryScope"] == "user" for item in records)
    assert all("projectRoot" not in item for item in records)

    scope = resolve_memory_scope("user")
    admitted, counts = admit_memory_sources(records, scope=scope)
    assert len(admitted) == 2
    assert counts == {"admitted": 2, "excluded": 0, "invalid": 0}


@pytest.mark.parametrize(
    ("text", "kind"),
    [
        ("更正：这个决定只适用于当前项目", "correction"),
        ("決定：保留现有材质", "decision"),
        ("覚えておいて、この設定を優先する", "preference"),
        ("事实是：目标版本为 1.3.6", "fact"),
    ],
)
def test_explicit_multilingual_signal_phrases(text: str, kind: str) -> None:
    assert explicit_memory_signal({"text": text}) == kind


def test_project_chat_projection_uses_authoritative_root_and_changes_revision_on_edit(tmp_path: Path) -> None:
    project = tmp_path / "Project"
    project.mkdir()
    chat = {
        "id": "chat-project",
        "projectPath": "untrusted-embedded-path",
        "items": [
            {"id": "u1", "type": "user", "text": "Decision: keep the current shader."},
            {"id": "a1", "type": "agent", "text": "Decision acknowledged."},
        ],
    }
    first = collect_user_chat_records([chat], scope="project", project_root=str(project))
    chat["items"][0]["text"] = "Decision: replace the shader after review."
    second = collect_user_chat_records([chat], scope="project", project_root=str(project))
    assert first[0]["projectRoot"] == str(project)
    assert first[0]["sourceId"] == second[0]["sourceId"]
    assert first[0]["sourceRevision"] != second[0]["sourceRevision"]


def test_user_scope_rejects_project_material(tmp_path: Path) -> None:
    project = tmp_path / "Project"
    project.mkdir()
    chats = [
        {
            "id": "project-chat",
            "projectPath": str(project),
            "items": [
                {"id": "u1", "type": "user", "text": "Remember that this is private."},
                {"id": "a1", "type": "agent", "text": "Acknowledged."},
            ],
        }
    ]
    assert collect_user_chat_records(chats, scope="user") == []
    with pytest.raises(ValueError):
        collect_user_chat_records(chats, scope="user", project_root=str(project))


def test_adopted_task_projection_requires_completed_exact_project_and_summary(tmp_path: Path) -> None:
    project_a = tmp_path / "A"
    project_b = tmp_path / "B"
    project_a.mkdir()
    project_b.mkdir()
    base = {
        "status": "completed",
        "mergeDecision": "adopted",
        "parentChatId": "parent",
        "projectPath": str(project_a),
        "summary": "Use the validated material plan.",
        "revision": 4,
    }
    tasks = [
        {**base, "id": "accepted"},
        {**base, "id": "other-project", "projectPath": str(project_b)},
        {**base, "id": "not-adopted", "mergeDecision": "dismissed"},
        {**base, "id": "failed", "status": "failed"},
        {**base, "id": "no-parent", "parentChatId": ""},
    ]
    records = collect_adopted_task_records(tasks, project_root=str(project_a))
    assert len(records) == 1
    assert records[0]["sourceType"] == "adopted_task"
    assert records[0]["summary"] == base["summary"]


def test_validated_project_projection_requires_narrow_envelope(tmp_path: Path) -> None:
    project = tmp_path / "Project"
    other = tmp_path / "Other"
    project.mkdir()
    other.mkdir()
    summary = "The static validation passed."
    digest = hashlib.sha256(summary.encode("utf-8")).hexdigest()
    valid = {
        "id": "event-a",
        "memoryEvidence": {
            "schema": "vrcforge.memory_evidence.v1",
            "applied": True,
            "validated": True,
            "projectRoot": str(project),
            "summary": summary,
            "summaryDigest": digest,
        },
        "rawResult": "must not be traversed",
    }
    events = [
        valid,
        {"event": "approval_applied", "summary": "generic apply is insufficient"},
        {**valid, "id": "other", "memoryEvidence": {**valid["memoryEvidence"], "projectRoot": str(other)}},
        {**valid, "id": "not-validated", "memoryEvidence": {**valid["memoryEvidence"], "validated": False}},
    ]
    records = collect_validated_project_records(events, project_root=str(project))
    assert len(records) == 1
    assert records[0]["summary"] == "The static validation passed."
    assert "rawResult" not in records[0]

    forged = {
        **valid,
        "id": "forged-digest",
        "memoryEvidence": {**valid["memoryEvidence"], "summaryDigest": "0" * 64},
    }
    assert collect_validated_project_records([forged], project_root=str(project)) == []


def test_collectors_never_emit_secret_from_unadmitted_records(tmp_path: Path) -> None:
    marker = "sk" + "-example-not-a-real-key"
    records = collect_user_chat_records(
        [{
            "id": "chat",
            "items": [
                {"id": "u", "type": "user", "text": f"ordinary {marker}"},
                {"id": "a", "type": "agent", "text": "Done."},
            ],
        }],
        scope="user",
    )
    assert records == []


def test_signal_waits_for_a_successful_durable_reply() -> None:
    pending = [
        {
            "id": "chat",
            "items": [{"id": "u", "type": "user", "text": "Remember that I prefer short answers."}],
        }
    ]
    failed = [
        {
            "id": "chat",
            "items": [
                {"id": "u", "type": "user", "text": "Remember that I prefer short answers."},
                {"id": "a", "type": "agent", "status": "failed", "text": "Provider failed."},
            ],
        }
    ]
    assert collect_user_chat_records(pending, scope="user") == []
    assert collect_user_chat_records(failed, scope="user") == []


def test_chat_projection_accepts_the_real_durable_agent_response_shape() -> None:
    records = collect_user_chat_records(
        [
            {
                "id": "chat-real-shape",
                "items": [
                    {
                        "id": "u",
                        "type": "user",
                        "text": "Please remember that I prefer compact status updates.",
                    },
                    {
                        "id": "a",
                        "type": "agent",
                        "response": {
                            "ok": True,
                            "plan": {"summary": "Acknowledged.", "planner": "llm"},
                        },
                    },
                ],
            }
        ],
        scope="user",
    )
    assert len(records) == 1
    assert records[0]["signalKind"] == "preference"

    failed_response = {
        "id": "chat-failed-shape",
        "items": [
            {"id": "u", "type": "user", "text": "Please remember this."},
            {"id": "a", "type": "agent", "response": {"ok": False, "plan": {}}},
        ],
    }
    assert collect_user_chat_records([failed_response], scope="user") == []
