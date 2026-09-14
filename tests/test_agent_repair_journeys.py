from __future__ import annotations

import hashlib
import json
import tempfile
import unittest
from pathlib import Path

import dashboard_server
from agent_gateway import AgentGateway


def unity_fixture(root: Path) -> Path:
    project = root / "UnityProject"
    for name in ("Assets", "Packages", "ProjectSettings", ".vrcforge"):
        (project / name).mkdir(parents=True)
    (project / "Packages" / "manifest.json").write_text("{}", encoding="utf-8")
    return project


class AgentRepairJourneyTests(unittest.TestCase):
    def test_corrupt_chatstore_routes_through_approval_and_independent_readback(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            project = unity_fixture(root)
            store = project / ".vrcforge" / "chat-transcripts.json"
            original = b'{"chats":['
            store.write_bytes(original)
            gateway = AgentGateway(root / "config" / "gateway.json", root / "audit")
            gateway.approval_transactions.register_write_handler(
                "vrcforge_repair_project_chat_store",
                "Repair project chat store.",
                "medium",
                dashboard_server.repair_project_chat_store_sync,
            )
            key = dashboard_server.normalize_chat_project_key(str(project))
            store_id = "session.chat.project." + hashlib.sha256(key.encode()).hexdigest()[:16]
            args = {
                "projectRoot": str(project),
                "projectPath": str(project),
                "expectedDigest": hashlib.sha256(original).hexdigest(),
                "storeId": store_id,
            }
            request = gateway.approval_transactions.create_apply_request(
                {"target_tool": "vrcforge_repair_project_chat_store", "arguments": args},
                internal_wrapper=True,
            )
            approval_id = request["approval"]["id"]
            gateway.approval_transactions.approve(approval_id)
            applied = gateway.approval_transactions.apply_approved({"approvalId": approval_id})
            self.assertTrue(applied["ok"], applied)
            self.assertEqual(applied["result"]["status"], "quarantined")
            self.assertFalse(store.exists())
            backup = next(project.glob(".vrcforge/chat-transcripts.json.vrcforge-backup-*"))
            self.assertEqual(backup.read_bytes(), original)
            self.assertTrue(gateway.build_health()["ok"])

    def test_interrupted_recovery_routes_through_approval_and_unblocks_readback(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            project = unity_fixture(root)
            gateway = AgentGateway(root / "config" / "gateway.json", root / "audit")
            checkpoint = gateway.approval_transactions._create_pre_write_checkpoint(
                {"id": "approval-crash", "targetTool": "vrcforge_test_write"},
                {"projectRoot": str(project)},
            )
            self.assertTrue(checkpoint["ok"])
            recovery = gateway.approval_transactions._start_apply_recovery(
                {"id": "approval-crash", "targetTool": "vrcforge_test_write"},
                {"projectRoot": str(project)},
                checkpoint,
            )
            before = gateway.checkpoint_recovery.list_interrupted_apply_recoveries()
            self.assertTrue(before["blockingWrites"])
            gateway.approval_transactions.register_write_handler(
                "vrcforge_resolve_interrupted_apply_recovery",
                "Resolve recovery.",
                "medium",
                lambda args: gateway.checkpoint_recovery.resolve_interrupted_apply_recovery(args),
            )
            request = gateway.approval_transactions.create_apply_request(
                {
                    "target_tool": "vrcforge_resolve_interrupted_apply_recovery",
                    "arguments": {"recoveryId": recovery["id"], "confirmResolved": True},
                },
                internal_wrapper=True,
            )
            approval_id = request["approval"]["id"]
            gateway.approval_transactions.approve(approval_id)
            applied = gateway.approval_transactions.apply_approved({"approvalId": approval_id})
            self.assertTrue(applied["ok"], applied)
            after = gateway.checkpoint_recovery.list_interrupted_apply_recoveries()
            self.assertFalse(after["blockingWrites"])
            self.assertTrue(gateway.build_health()["ok"])


if __name__ == "__main__":
    unittest.main()
