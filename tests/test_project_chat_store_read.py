from __future__ import annotations

import hashlib
import tempfile
from pathlib import Path

import dashboard_server
from project_chat_store_read_service import inspect_project_chat_store


def _target(root: Path):
    return dashboard_server.project_chat_repair_target(root)


def _project(parent: Path) -> Path:
    root = parent / "cost-project"
    (root / "Assets").mkdir(parents=True)
    (root / "Packages").mkdir()
    (root / "ProjectSettings").mkdir()
    (root / ".vrcforge").mkdir()
    return root


def test_inspection_returns_digest_store_binding_without_mutation() -> None:
    with tempfile.TemporaryDirectory() as tmp:
        root = _project(Path(tmp))
        store = root / ".vrcforge" / "chat-transcripts.json"
        original = b'{"chats":['
        store.write_bytes(original)
        before_entries = sorted(str(p.relative_to(root)) for p in root.rglob("*"))
        before_stat = store.stat()
        result = inspect_project_chat_store(
            {"projectPath": str(root)},
            resolve_project_root=dashboard_server.resolve_chat_project_root,
            target_factory=_target,
        )
        assert result["ok"] is True
        assert result["status"] == "needs_repair"
        assert result["digest"] == hashlib.sha256(original).hexdigest()
        assert result["storeId"] == _target(root).store_id
        assert result["repairHint"]["expectedDigest"] == result["digest"]
        assert result["repairHint"]["storeId"] == result["storeId"]
        assert store.read_bytes() == original
        after_stat = store.stat()
        assert after_stat.st_mtime_ns == before_stat.st_mtime_ns
        assert sorted(str(p.relative_to(root)) for p in root.rglob("*")) == before_entries


def test_inspection_distinguishes_healthy_and_missing_store() -> None:
    with tempfile.TemporaryDirectory() as tmp:
        root = _project(Path(tmp))
        store = root / ".vrcforge" / "chat-transcripts.json"
        store.write_text('{"version":1,"chats":[]}', encoding="utf-8")
        healthy = inspect_project_chat_store({"projectPath": str(root)}, resolve_project_root=dashboard_server.resolve_chat_project_root, target_factory=_target)
        assert healthy["status"] in {"healthy", "ok"}
        assert healthy["repairRequired"] is False
        store.unlink()
        missing = inspect_project_chat_store({"projectPath": str(root)}, resolve_project_root=dashboard_server.resolve_chat_project_root, target_factory=_target)
        assert missing["ok"] is True and missing["exists"] is False
        assert missing["repairHint"] is None


def test_inspection_rejects_unknown_project_and_is_planning_discoverable() -> None:
    rejected = inspect_project_chat_store({"projectPath": "C:/does/not/exist"}, resolve_project_root=dashboard_server.resolve_chat_project_root, target_factory=_target)
    assert rejected == {"ok": False, "status": "invalid_project_root", "changed": False}
    descriptor = next(item for item in dashboard_server.AGENT_GATEWAY.build_external_mcp_tools("planning", ["assets"]) if item["name"] == "vrcforge_inspect_project_chat_store")
    assert descriptor["write"] is False
    assert descriptor["inputSchema"]["required"] == ["projectPath"]
    assert "chat" not in str(descriptor.get("outputSchema") or {}).lower()


def test_public_inspection_hint_is_accepted_by_real_repair_handler(tmp_path):
    root = _project(tmp_path)
    store = root / ".vrcforge" / "chat-transcripts.json"
    store.write_bytes(b'{"version":1,"chats":[broken')
    handler = dashboard_server.AGENT_GATEWAY._tools["vrcforge_inspect_project_chat_store"]
    inspected = handler.handler({"projectPath": str(root)})
    hint = dict(inspected["repairHint"])
    hint.pop("tool")
    hint.pop("requiresApproval")
    repaired = dashboard_server.repair_project_chat_store_sync(hint)
    assert repaired["ok"] is True, repaired
    assert repaired["verified"] is True
    assert repaired["readback"]["state"] == "passed"
