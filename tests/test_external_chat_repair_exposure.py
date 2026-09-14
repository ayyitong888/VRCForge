from __future__ import annotations

import hashlib
import json
import tempfile
from pathlib import Path
import pytest

import dashboard_server
from agent_gateway import AgentGateway, EXPOSURE_LAYER_EXECUTION, EXPOSURE_LAYER_PLANNING
from session_store_integrity import SessionStoreTarget, verify_session_store_repair


@pytest.mark.parametrize("partial", [False, True])
@pytest.mark.parametrize("damage", ["missing", "mismatch"])
def test_repair_readback_preserves_records_and_rejects_damaged_backup(tmp_path, partial, damage):
    project = _project(tmp_path)
    path = project / ".vrcforge" / "chat-transcripts.json"
    valid = {"id": "retained", "items": []}
    original = json.dumps({"version": 1, "chats": [valid, 17]}).encode() if partial else b'{"chats":['
    path.write_bytes(original)
    digest = hashlib.sha256(original).hexdigest()
    key = dashboard_server.normalize_chat_project_key(str(project))
    store_id = "session.chat.project." + hashlib.sha256(key.encode()).hexdigest()[:16]
    args = {"projectPath": str(project), "storeId": store_id, "expectedDigest": digest}
    result = dashboard_server.repair_project_chat_store_sync(args)
    assert result["ok"] and result["verified"]
    assert result["status"] == ("repaired" if partial else "quarantined")
    if partial:
        assert json.loads(path.read_text())["chats"] == [valid]
    else:
        assert not path.exists()
    repeated = dashboard_server.repair_project_chat_store_sync(args)
    assert repeated["status"] == "already_repaired" and repeated["verified"]
    backup = path.parent / result["backupBasename"]
    if damage == "missing":
        backup.unlink()
    else:
        backup.write_bytes(b"unexpected content")
    target = SessionStoreTarget(store_id, path, "project_owned", "json", required_list_field="chats", required_list_item_kind="chat")
    assert verify_session_store_repair(target, digest)["state"] == "failed"
    assert not dashboard_server.repair_project_chat_store_sync(args)["ok"]


def _project(root: Path) -> Path:
    project = root / "UnityProject"
    for name in ("Assets", "Packages", "ProjectSettings", ".vrcforge"):
        (project / name).mkdir(parents=True)
    (project / "Packages" / "manifest.json").write_text("{}", encoding="utf-8")
    return project


def test_external_chat_repair_is_execution_only_and_keeps_real_approval_boundary() -> None:
    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp)
        project = _project(root)
        store = project / ".vrcforge" / "chat-transcripts.json"
        original = b'{"chats":['
        store.write_bytes(original)
        gateway = AgentGateway(root / "config" / "gateway.json", root / "audit")
        config = gateway.ensure_config()
        config.enabled = True
        config.allow_write_requests = True
        config.execution_mode = "approval"
        gateway.save_config(config)
        gateway.approval_transactions.register_write_handler(
            "vrcforge_repair_project_chat_store",
            "Repair a digest-bound project chat transcript store after explicit approval.",
            "medium",
            dashboard_server.repair_project_chat_store_sync,
            external_mcp_capability="digest_bound_chat_store_repair_v1",
        )
        name = "vrcforge_repair_project_chat_store"
        assert name not in {item["name"] for item in gateway.build_external_mcp_tools(EXPOSURE_LAYER_PLANNING, ["checkpoint"])}
        execution = gateway.build_external_mcp_tools(EXPOSURE_LAYER_EXECUTION, ["checkpoint"])
        descriptor = next(item for item in execution if item["name"] == name)
        assert descriptor["write"] is True
        assert descriptor["_meta"]["toolBlock"] == "checkpoint"
        assert set(descriptor["inputSchema"]["required"]) == {"projectPath", "expectedDigest", "storeId", "executionTarget"}
        assert {"projectPath", "projectRoot", "expectedDigest", "storeId"} <= set(descriptor["inputSchema"]["properties"])

        key = dashboard_server.normalize_chat_project_key(str(project))
        store_id = "session.chat.project." + hashlib.sha256(key.encode()).hexdigest()[:16]
        arguments = {
            "projectRoot": str(project),
            "projectPath": str(project),
            "expectedDigest": hashlib.sha256(original).hexdigest(),
            "storeId": store_id,
        }
        pending = gateway.call_external_mcp_tool(name, arguments, agent_name="external-test")
        assert pending["status"] == "user_confirmation_required"
        assert pending["confirmation"]["requiredDecision"] == ["approve", "reject"]
        assert pending["mutationStarted"] is False
        assert store.read_bytes() == original
        assert list(project.glob(".vrcforge/chat-transcripts.json.vrcforge-backup-*")) == []

        confirmation = dict(pending["confirmation"])
        confirmation["decision"] = "approve"
        applied = gateway.call_external_mcp_tool(
            name,
            {**arguments, "confirmation": confirmation},
            agent_name="external-test",
        )
        assert applied["ok"] is True, applied
        assert applied["status"] == "executed"
        assert not store.exists()
        backup = next(project.glob(".vrcforge/chat-transcripts.json.vrcforge-backup-*"))
        assert backup.read_bytes() == original
        assert gateway.build_health()["ok"] is True
