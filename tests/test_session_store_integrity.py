from __future__ import annotations

import hashlib
import json
import math
import os
from pathlib import Path

import pytest

from session_store_integrity import (
    SESSION_STORE_INTEGRITY_SCHEMA,
    SessionStoreTarget,
    is_valid_chat_record,
    repair_session_store,
    scan_session_store,
    scan_session_stores,
)


def target(
    path: Path,
    *,
    store_id: str = "chat.app",
    scope: str = "app_owned",
    format: str = "json",
    known_schemas: tuple[str, ...] = (),
    required_list_field: str = "",
    required_list_item_kind: str = "any",
    max_list_items: int = 0,
) -> SessionStoreTarget:
    return SessionStoreTarget(
        store_id=store_id,
        path=path,
        scope=scope,  # type: ignore[arg-type]
        format=format,  # type: ignore[arg-type]
        known_schemas=known_schemas,
        required_list_field=required_list_field,
        required_list_item_kind=required_list_item_kind,  # type: ignore[arg-type]
        max_list_items=max_list_items,
    )


def sidecar(path: Path, kind: str, digest: str) -> Path:
    return path.with_name(f"{path.name}.vrcforge-{kind}-{digest[:16]}")


def assert_public_result(payload: dict, root: Path, secret: str = "") -> None:
    encoded = json.dumps(payload, ensure_ascii=False)
    assert str(root) not in encoded
    if secret:
        assert secret not in encoded
    assert "path" not in {str(key).lower() for key in payload}


def test_scan_whole_json_is_read_only_and_redacted(tmp_path: Path) -> None:
    private_root = tmp_path / "private" / "machine" / "path"
    private_root.mkdir(parents=True)
    path = private_root / "chat-transcripts.json"
    secret = "do-not-return-this-bad-content"
    original = f'{{"chats":[{{"text":"{secret}"}}]'.encode()
    path.write_bytes(original)
    before = path.stat()

    result = scan_session_store(target(path))

    after = path.stat()
    assert result["status"] == "needs_repair"
    assert result["reason"] == "invalid_json"
    assert result["invalidCount"] == 1
    assert result["digest"]
    assert path.read_bytes() == original
    assert after.st_mtime_ns == before.st_mtime_ns
    assert list(path.parent.iterdir()) == [path]
    assert_public_result(result, tmp_path, secret)


def test_whole_json_repair_never_creates_partial_or_empty_replacement(tmp_path: Path) -> None:
    path = tmp_path / "chat-transcripts.json"
    original = b'{"version":1,"chats":[{"id":"good"}]} trailing-corruption'
    path.write_bytes(original)
    store = target(path)
    scan = scan_session_store(store)

    repaired = repair_session_store(store, scan)

    assert repaired["status"] == "quarantined"
    assert repaired["changed"] is True
    assert not path.exists()
    backup = sidecar(path, "backup", scan["digest"])
    quarantine = sidecar(path, "quarantine", scan["digest"])
    assert backup.read_bytes() == original
    assert quarantine.read_bytes() == original
    assert not any(item.name.endswith(".tmp") for item in tmp_path.iterdir())
    assert_public_result(repaired, tmp_path, "good")

    mtimes = {item.name: item.stat().st_mtime_ns for item in tmp_path.iterdir()}
    repeated = repair_session_store(store, scan)
    assert repeated["status"] == "already_repaired"
    assert repeated["changed"] is False
    assert {item.name: item.stat().st_mtime_ns for item in tmp_path.iterdir()} == mtimes


def test_required_list_shape_is_repairable_as_one_atomic_document(tmp_path: Path) -> None:
    path = tmp_path / "chat-transcripts.json"
    original = b'{"version":1,"chats":{}}'
    path.write_bytes(original)
    store = target(path, required_list_field="chats")

    scan = scan_session_store(store)
    repaired = repair_session_store(store, scan)

    assert scan["status"] == "needs_repair"
    assert scan["reason"] == "invalid_record_shape"
    assert scan["invalidCount"] == 1
    assert scan["semanticIssueCount"] == 1
    assert repaired["status"] == "quarantined"
    assert not path.exists()
    assert sidecar(path, "backup", scan["digest"]).read_bytes() == original
    assert sidecar(path, "quarantine", scan["digest"]).read_bytes() == original


def test_strict_json_rejects_exponent_overflow_in_whole_json_and_jsonl(tmp_path: Path) -> None:
    for index, number in enumerate(("1e999", "-1e999")):
        path = tmp_path / f"overflow-{index}.json"
        path.write_text(f'{{"version":1,"future":{number}}}', encoding="utf-8")
        scan = scan_session_store(target(path, store_id=f"overflow.whole.{index}"))
        assert scan["status"] == "needs_repair"
        assert scan["reason"] == "invalid_json"

    jsonl_path = tmp_path / "overflow.jsonl"
    good = b'{"schema":"known.v1","value":1}\n'
    invalid = b'{"schema":"known.v1","value":-1e999}\n'
    jsonl_path.write_bytes(good + invalid)
    store = target(
        jsonl_path,
        store_id="overflow.jsonl",
        format="jsonl",
        known_schemas=("known.v1",),
    )
    scan = scan_session_store(store)
    repaired = repair_session_store(store, scan)

    assert scan["invalidCount"] == 1
    assert repaired["status"] == "repaired"
    assert jsonl_path.read_bytes() == good
    assert sidecar(jsonl_path, "quarantine", scan["digest"]).read_bytes() == invalid


@pytest.mark.parametrize("literal", ("NaN", "Infinity", "-Infinity"))
def test_strict_json_rejects_non_standard_numeric_literals(tmp_path: Path, literal: str) -> None:
    path = tmp_path / f"invalid-{literal.replace('-', 'negative-')}.json"
    original = f'{{"version":1,"value":{literal}}}'.encode()
    path.write_bytes(original)

    scan = scan_session_store(target(path, store_id="strict.literal"))

    assert scan["status"] == "needs_repair"
    assert scan["reason"] == "invalid_json"
    assert path.read_bytes() == original


def test_strict_json_rejects_depth_beyond_product_limit(tmp_path: Path) -> None:
    path = tmp_path / "deep.json"
    original = (b'{"nested":' * 65) + b"0" + (b"}" * 65)
    path.write_bytes(original)

    scan = scan_session_store(target(path, store_id="strict.depth"))

    assert scan["status"] == "needs_repair"
    assert scan["reason"] == "invalid_json"
    assert path.read_bytes() == original


def test_required_list_limit_is_enforced_without_guessing_which_records_to_drop(tmp_path: Path) -> None:
    path = tmp_path / "chat-transcripts.json"
    store = target(
        path,
        required_list_field="chats",
        required_list_item_kind="chat",
        max_list_items=100,
    )

    def chats(count: int) -> list[dict]:
        return [
            {
                "id": f"chat-{index}",
                "items": [{"id": f"item-{index}", "type": "user", "text": "keep"}],
            }
            for index in range(count)
        ]

    path.write_text(json.dumps({"version": 1, "chats": chats(100)}), encoding="utf-8")
    assert scan_session_store(store)["status"] == "ok"

    path.write_text(json.dumps({"version": 1, "chats": chats(101)}), encoding="utf-8")
    overflow = scan_session_store(store)
    unchanged = repair_session_store(store, overflow)

    assert overflow["status"] == "unsupported"
    assert overflow["reason"] == "record_limit_exceeded"
    assert overflow["recordCount"] == 101
    assert unchanged["status"] == "no_change"
    assert path.exists()


def test_agent_steps_require_ui_safe_field_shapes(tmp_path: Path) -> None:
    valid = {
        "id": "chat-agent",
        "items": [
            {
                "id": "agent-item",
                "type": "agent",
                "response": {
                    "plan": {"summary": "done", "planner": "test", "shellNeeded": False},
                    "steps": [
                        {
                            "index": 1,
                            "kind": "plan",
                            "tool": "vrcforge_health",
                            "status": "done",
                            "source": "runtime",
                            "usage": {},
                            "imageCount": 0,
                        }
                    ],
                },
            }
        ],
    }
    assert is_valid_chat_record(valid)
    for field in ("kind", "tool", "summary", "status", "provider", "providerLabel", "model", "source"):
        malformed = json.loads(json.dumps(valid))
        malformed["items"][0]["response"]["steps"][0][field] = 1
        assert not is_valid_chat_record(malformed), field
    malformed_index = json.loads(json.dumps(valid))
    malformed_index["items"][0]["response"]["steps"][0]["index"] = math.inf
    assert not is_valid_chat_record(malformed_index)

    path = tmp_path / "malformed-agent-step.json"
    malformed = json.loads(json.dumps(valid))
    malformed["items"][0]["response"]["steps"][0]["kind"] = 1
    path.write_text(json.dumps({"version": 1, "chats": [malformed]}), encoding="utf-8")
    scan = scan_session_store(
        target(
            path,
            required_list_field="chats",
            required_list_item_kind="chat",
        )
    )
    assert scan["status"] == "needs_repair"
    assert scan["reason"] == "invalid_list_records"


def test_attachment_vault_and_compacted_references_require_ui_safe_shapes() -> None:
    body = "attachment body"
    payload_hash = hashlib.sha256(body.encode()).hexdigest()
    valid = {
        "id": "chat-attachment",
        "attachmentPayloads": {
            payload_hash: {"payloadHash": payload_hash, "payloadKind": "text", "text": body},
        },
        "compactedAttachmentRefs": [
            {
                "id": "attachment-1",
                "name": "notes.txt",
                "size": len(body),
                "type": "text/plain",
                "payloadKind": "text",
                "payloadHash": payload_hash,
            }
        ],
        "items": [
            {
                "id": "user-item",
                "type": "user",
                "text": "inspect it",
                "attachments": [
                    {
                        "id": "attachment-1",
                        "name": "notes.txt",
                        "size": len(body),
                        "type": "text/plain",
                        "payloadKind": "text",
                        "payloadHash": payload_hash,
                    }
                ],
            }
        ],
    }
    assert is_valid_chat_record(valid)

    mutations = (
        ("negative attachment size", lambda item: item["items"][0]["attachments"][0].__setitem__("size", -1)),
        ("fractional attachment size", lambda item: item["items"][0]["attachments"][0].__setitem__("size", 1.5)),
        ("invalid payload hash", lambda item: item["items"][0]["attachments"][0].__setitem__("payloadHash", "bad")),
        ("mismatched vault body", lambda item: item["attachmentPayloads"][payload_hash].__setitem__("text", "changed")),
        ("compacted body leak", lambda item: item["compactedAttachmentRefs"][0].__setitem__("text", body)),
        ("non-list compacted refs", lambda item: item.__setitem__("compactedAttachmentRefs", {})),
    )
    for label, mutate in mutations:
        malformed = json.loads(json.dumps(valid))
        mutate(malformed)
        assert not is_valid_chat_record(malformed), label


def test_nested_agent_response_fields_require_react_safe_shapes(tmp_path: Path) -> None:
    valid = {
        "id": "chat-agent-nested",
        "items": [
            {
                "id": "agent-item",
                "type": "agent",
                "response": {
                    "plan": {"summary": "done", "planner": "test", "shellNeeded": True},
                    "shell": {
                        "ok": True,
                        "status": "pending_approval",
                        "approval": {
                            "id": "approval-1",
                            "status": "pending",
                            "targetTool": "vrcforge_write",
                            "preview": {"command": "safe", "riskReasons": ["write"]},
                        },
                    },
                    "reasoning": {
                        "provider": "test",
                        "itemCount": 1,
                        "items": [{"title": "Reason", "kind": "summary", "text": "hidden", "opaque": False}],
                    },
                    "skill": {"ok": True, "status": "executed", "tool": "vrcforge_health", "write": False},
                },
            }
        ],
    }
    assert is_valid_chat_record(valid)

    mutations = (
        ("shell error object", lambda response: response["shell"].__setitem__("error", {"bad": True})),
        ("approval tool object", lambda response: response["shell"]["approval"].__setitem__("targetTool", {"bad": True})),
        ("approval reasons object", lambda response: response["shell"]["approval"]["preview"].__setitem__("riskReasons", [{}])),
        ("reasoning title object", lambda response: response["reasoning"]["items"][0].__setitem__("title", {"bad": True})),
        ("reasoning opaque string", lambda response: response["reasoning"]["items"][0].__setitem__("opaque", "yes")),
        ("skill write string", lambda response: response["skill"].__setitem__("write", "yes")),
    )
    malformed_for_store = None
    for label, mutate in mutations:
        malformed = json.loads(json.dumps(valid))
        mutate(malformed["items"][0]["response"])
        assert not is_valid_chat_record(malformed), label
        malformed_for_store = malformed

    path = tmp_path / "mixed-agent-records.json"
    path.write_text(json.dumps({"version": 1, "chats": [valid, malformed_for_store]}), encoding="utf-8")
    store = target(path, required_list_field="chats", required_list_item_kind="chat")
    scan = scan_session_store(store)
    repaired = repair_session_store(store, scan)

    assert scan["status"] == "needs_repair"
    assert scan["invalidCount"] == 1
    assert repaired["status"] == "repaired"
    assert json.loads(path.read_text(encoding="utf-8"))["chats"] == [valid]
    assert scan_session_store(store)["status"] == "ok"


def test_pending_supervised_write_result_is_valid_non_shell_history() -> None:
    approval = {
        "id": "approval-write-1",
        "status": "pending",
        "targetTool": "vrcforge_apply_change",
        "requiresExplicitApproval": True,
    }
    pending = {
        "ok": True,
        "status": "pending",
        "approval": approval,
        "message": "Apply request requires explicit user approval.",
    }
    chat = {
        "id": "chat-pending-write",
        "items": [
            {
                "id": "agent-pending-write",
                "type": "agent",
                "response": {
                    "plan": {"summary": "approval requested", "planner": "test", "shellNeeded": False},
                    "write": {
                        "ok": True,
                        "status": "approval_pending",
                        "tool": "vrcforge_apply_change",
                        "approvalId": approval["id"],
                        "result": pending,
                    },
                    "approvalId": approval["id"],
                    "result": pending,
                },
            }
        ],
    }

    assert is_valid_chat_record(chat)
    malformed = json.loads(json.dumps(chat))
    malformed["items"][0]["response"]["result"]["ratio"] = math.inf
    assert not is_valid_chat_record(malformed)


def test_project_owned_repair_requires_supervision_and_writes_nothing(tmp_path: Path) -> None:
    project = tmp_path / "AvatarProject"
    path = project / ".vrcforge" / "chat-transcripts.json"
    path.parent.mkdir(parents=True)
    original = b'{"chats":['
    path.write_bytes(original)
    before = {item.name: item.read_bytes() for item in path.parent.iterdir()}
    store = target(path, store_id="chat.project", scope="project_owned")
    scan = scan_session_store(store)

    result = repair_session_store(store, scan)

    assert result["status"] == "approval_required"
    assert result["reason"] == "project_write_supervision_required"
    assert result["requiresApproval"] is True
    assert result["changed"] is False
    assert {item.name: item.read_bytes() for item in path.parent.iterdir()} == before
    assert_public_result(result, tmp_path)


def test_authorized_project_owned_repair_creates_verified_recovery_artifacts(tmp_path: Path) -> None:
    project = tmp_path / "AvatarProject"
    path = project / ".vrcforge" / "chat-transcripts.json"
    path.parent.mkdir(parents=True)
    original = b'{"chats":['
    path.write_bytes(original)
    store = target(path, store_id="chat.project", scope="project_owned")
    scan = scan_session_store(store)

    result = repair_session_store(store, scan, project_write_authorized=True)

    assert result["status"] == "quarantined"
    assert result["changed"] is True
    assert not path.exists()
    assert sidecar(path, "backup", scan["digest"]).read_bytes() == original
    assert sidecar(path, "quarantine", scan["digest"]).read_bytes() == original
    assert_public_result(result, tmp_path)


def test_jsonl_repair_preserves_valid_lines_and_future_schema_bytes(tmp_path: Path) -> None:
    path = tmp_path / "runtime-runs.jsonl"
    first = b'{ "schema": "known.v1", "id": 1 }\r\n'
    invalid = b'{"schema":"known.v1","id":\n'
    future = b'  {"schema":"future.v9", "opaque":{"keep":true}}  \n'
    blank = b"\n"
    last = b'{"schema":"known.v1","id":2}'
    original = first + invalid + future + blank + last
    path.write_bytes(original)
    store = target(
        path,
        store_id="runtime.runs",
        format="jsonl",
        known_schemas=("known.v1",),
    )
    before = path.stat().st_mtime_ns

    scan = scan_session_store(store)

    assert path.read_bytes() == original
    assert path.stat().st_mtime_ns == before
    assert scan["status"] == "needs_repair"
    assert scan["invalidCount"] == 1
    assert scan["recordCount"] == 3
    assert scan["unknownSchemaCount"] == 1

    repaired = repair_session_store(store, scan)

    assert repaired["status"] == "repaired"
    assert path.read_bytes() == first + future + blank + last
    assert sidecar(path, "backup", scan["digest"]).read_bytes() == original
    assert sidecar(path, "quarantine", scan["digest"]).read_bytes() == invalid
    assert future in path.read_bytes()
    assert_public_result(repaired, tmp_path, "opaque")

    after_scan = scan_session_store(store)
    assert after_scan["status"] == "unsupported"
    assert after_scan["reason"] == "unknown_schema"
    assert after_scan["invalidCount"] == 0
    assert after_scan["unknownSchemaCount"] == 1


def test_jsonl_bom_is_allowed_only_at_document_start_and_bytes_are_preserved(tmp_path: Path) -> None:
    path = tmp_path / "bom.jsonl"
    first = b'\xef\xbb\xbf{"schema":"known.v1","id":"first"}\r\n'
    invalid_second_bom = b'\xef\xbb\xbf{"schema":"known.v1","id":"second"}\n'
    last = b'{"schema":"known.v1","id":"last"}'
    original = first + invalid_second_bom + last
    path.write_bytes(original)
    store = target(path, store_id="bom.jsonl", format="jsonl", known_schemas=("known.v1",))

    scan = scan_session_store(store)
    repaired = repair_session_store(store, scan)

    assert scan["invalidCount"] == 1
    assert repaired["status"] == "repaired"
    assert path.read_bytes() == first + last
    assert sidecar(path, "quarantine", scan["digest"]).read_bytes() == invalid_second_bom


def test_agent_goal_repair_preserves_v1_and_v2_records_exactly(tmp_path: Path) -> None:
    path = tmp_path / "agent-goals.jsonl"
    v1 = b'{ "schema": "vrcforge.agent_goal.v1", "id": "legacy" }\r\n'
    invalid = b'{"schema":"vrcforge.agent_goal.v2","id":\n'
    v2 = b'{"schema":"vrcforge.agent_goal.v2","id":"current"}'
    original = v1 + invalid + v2
    path.write_bytes(original)
    store = target(
        path,
        store_id="agent.goals.mixed",
        format="jsonl",
        known_schemas=("vrcforge.agent_goal.v1", "vrcforge.agent_goal.v2"),
    )

    scan = scan_session_store(store)
    repaired = repair_session_store(store, scan)

    assert repaired["status"] == "repaired"
    assert path.read_bytes() == v1 + v2
    assert sidecar(path, "quarantine", scan["digest"]).read_bytes() == invalid
    assert scan_session_store(store)["status"] == "ok"


def test_checkpoint_jsonl_repair_preserves_line_endings_and_future_schema(tmp_path: Path) -> None:
    path = tmp_path / "checkpoints.jsonl"
    known = b'{"schema":"vrcforge.checkpoint.v1","id":"known"}\r\n'
    invalid = b'{"schema":"vrcforge.checkpoint.v1","id":\n'
    future = b'{"schema":"vrcforge.checkpoint.v9","id":"future"}\n'
    last = b'{"schema":"vrcforge.checkpoint.v1","id":"last"}'
    original = known + invalid + future + last
    path.write_bytes(original)
    store = target(
        path,
        store_id="checkpoint.mixed",
        format="jsonl",
        known_schemas=("vrcforge.checkpoint.v1",),
    )

    scan = scan_session_store(store)
    repaired = repair_session_store(store, scan)

    assert repaired["status"] == "repaired"
    assert path.read_bytes() == known + future + last
    assert sidecar(path, "quarantine", scan["digest"]).read_bytes() == invalid
    after = scan_session_store(store)
    assert after["status"] == "unsupported"
    assert after["reason"] == "unknown_schema"


def test_session_store_refuses_target_and_parent_links_without_writing(tmp_path: Path) -> None:
    real_parent = tmp_path / "real"
    real_parent.mkdir()
    real_file = real_parent / "events.jsonl"
    original = b'{"schema":"known.v1","id":"safe"}\n'
    real_file.write_bytes(original)
    file_link = tmp_path / "file-link.jsonl"
    parent_link = tmp_path / "parent-link"
    try:
        os.symlink(real_file, file_link)
        os.symlink(real_parent, parent_link, target_is_directory=True)
    except OSError as exc:
        pytest.skip(f"symlink creation is unavailable: {exc}")

    for store_id, path in (("link.file", file_link), ("link.parent", parent_link / real_file.name)):
        store = SessionStoreTarget(
            store_id=store_id,
            path=path,
            scope="app_owned",
            format="jsonl",
            known_schemas=("known.v1",),
            guard_root=tmp_path,
        )
        scan = scan_session_store(store)
        repaired = repair_session_store(store, scan)
        assert scan["status"] == "error"
        assert scan["reason"] == "symlink_refused"
        assert repaired["status"] == "conflict"
        assert repaired["changed"] is False
        assert real_file.read_bytes() == original
    assert file_link.is_symlink()
    assert parent_link.is_symlink()


def test_jsonl_repair_is_idempotent_for_original_snapshot(tmp_path: Path) -> None:
    path = tmp_path / "agent-goals.jsonl"
    original = b'{"schema":"goal.v1","id":"a"}\nnot-json\n{"schema":"goal.v1","id":"b"}\n'
    path.write_bytes(original)
    store = target(
        path,
        store_id="agent.goals",
        format="jsonl",
        known_schemas=("goal.v1",),
    )
    scan = scan_session_store(store)
    first = repair_session_store(store, scan)
    assert first["status"] == "repaired"
    mtimes = {item.name: item.stat().st_mtime_ns for item in tmp_path.iterdir()}

    second = repair_session_store(store, scan)

    assert second["status"] == "already_repaired"
    assert second["changed"] is False
    assert {item.name: item.stat().st_mtime_ns for item in tmp_path.iterdir()} == mtimes


def test_repair_rejects_a_stale_scan_before_creating_artifacts(tmp_path: Path) -> None:
    path = tmp_path / "sub-agent-events.jsonl"
    path.write_bytes(b'{"schema":"known.v1"}\nbad-one\n')
    store = target(
        path,
        store_id="subagent.events",
        format="jsonl",
        known_schemas=("known.v1",),
    )
    scan = scan_session_store(store)
    replacement = b'{"schema":"known.v1"}\nbad-two\n'
    path.write_bytes(replacement)

    result = repair_session_store(store, scan)

    assert result["status"] == "conflict"
    assert result["reason"] == "snapshot_changed"
    assert path.read_bytes() == replacement
    assert list(tmp_path.iterdir()) == [path]


def test_unknown_future_schema_is_reported_but_never_isolated(tmp_path: Path) -> None:
    path = tmp_path / "goal-result.json"
    original = b'{"schema":"goal.result.v99","response":{"future":true}}\n'
    path.write_bytes(original)
    store = target(
        path,
        store_id="goal.result",
        known_schemas=("goal.result.v1",),
    )
    scan = scan_session_store(store)
    before = path.stat().st_mtime_ns

    result = repair_session_store(store, scan)

    assert scan["status"] == "unsupported"
    assert scan["reason"] == "unknown_schema"
    assert result["status"] == "no_change"
    assert result["reason"] == "unknown_schema"
    assert result["changed"] is False
    assert path.read_bytes() == original
    assert path.stat().st_mtime_ns == before
    assert list(tmp_path.iterdir()) == [path]


def test_parseable_jsonl_shape_is_preserved_and_not_repaired(tmp_path: Path) -> None:
    path = tmp_path / "events.jsonl"
    original = b'["future", "shape"]\n{"schema":"known.v1"}\n'
    path.write_bytes(original)
    store = target(
        path,
        store_id="events.shape",
        format="jsonl",
        known_schemas=("known.v1",),
    )
    scan = scan_session_store(store)

    result = repair_session_store(store, scan)

    assert scan["status"] == "unsupported"
    assert scan["reason"] == "invalid_record_shape"
    assert scan["invalidCount"] == 0
    assert scan["semanticIssueCount"] == 1
    assert result["status"] == "no_change"
    assert path.read_bytes() == original
    assert list(tmp_path.iterdir()) == [path]


def test_invalid_utf8_jsonl_line_is_the_only_quarantined_bytes(tmp_path: Path) -> None:
    path = tmp_path / "events.jsonl"
    first = b'{"schema":"known.v1","id":1}\n'
    invalid = b"\xff\xfe\n"
    last = b'{"schema":"known.v1","id":2}\n'
    path.write_bytes(first + invalid + last)
    store = target(
        path,
        store_id="events.encoding",
        format="jsonl",
        known_schemas=("known.v1",),
    )
    scan = scan_session_store(store)

    result = repair_session_store(store, scan)

    assert result["status"] == "repaired"
    assert path.read_bytes() == first + last
    assert sidecar(path, "quarantine", scan["digest"]).read_bytes() == invalid


def test_all_invalid_jsonl_is_renamed_aside_without_empty_replacement(tmp_path: Path) -> None:
    path = tmp_path / "runtime-runs.jsonl"
    original = b"not-json\n{still-not-json\n"
    path.write_bytes(original)
    store = target(path, store_id="runtime.empty_guard", format="jsonl")
    scan = scan_session_store(store)

    result = repair_session_store(store, scan)

    assert result["status"] == "quarantined"
    assert result["reason"] == "no_valid_records"
    assert not path.exists()
    assert sidecar(path, "backup", scan["digest"]).read_bytes() == original
    assert sidecar(path, "quarantine", scan["digest"]).read_bytes() == original

    repeated = repair_session_store(store, scan)
    assert repeated["status"] == "already_repaired"
    assert repeated["changed"] is False


def test_jsonl_does_not_infer_record_boundaries_from_control_bytes(tmp_path: Path) -> None:
    path = tmp_path / "events.jsonl"
    original = b'{"schema":"known.v1"}\v{"schema":"known.v1"}\n'
    path.write_bytes(original)
    store = target(
        path,
        store_id="events.strict_lines",
        format="jsonl",
        known_schemas=("known.v1",),
    )

    scan = scan_session_store(store)
    result = repair_session_store(store, scan)

    assert scan["recordCount"] == 0
    assert scan["invalidCount"] == 1
    assert result["status"] == "quarantined"
    assert not path.exists()
    assert sidecar(path, "quarantine", scan["digest"]).read_bytes() == original


def test_missing_store_and_read_only_batch_are_stable(tmp_path: Path) -> None:
    missing = target(tmp_path / "missing.json", store_id="chat.missing")
    valid_path = tmp_path / "valid.json"
    valid_path.write_text('{"version":1,"chats":[]}', encoding="utf-8")
    valid = target(valid_path, store_id="chat.valid")

    report = scan_session_stores([missing, valid])
    repair = repair_session_store(missing, report["stores"][0])

    assert report["schema"] == SESSION_STORE_INTEGRITY_SCHEMA
    assert report["status"] == "ok"
    assert report["storeCount"] == 2
    assert report["invalidCount"] == 0
    assert repair["status"] == "no_change"
    assert repair["reason"] == "missing"
    assert list(tmp_path.iterdir()) == [valid_path]
    assert_public_result(report["stores"][0], tmp_path)
