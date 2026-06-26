from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from agent_gateway import AgentGateway


def create_unity_project(root: Path) -> Path:
    project = root / "UnityProject"
    (project / "Assets").mkdir(parents=True)
    (project / "Packages").mkdir()
    (project / "ProjectSettings").mkdir()
    (project / "Assets" / "existing.txt").write_text("before", encoding="utf-8")
    (project / "Packages" / "manifest.json").write_text("{}", encoding="utf-8")
    (project / "ProjectSettings" / "ProjectVersion.txt").write_text(
        "m_EditorVersion: 2022.3",
        encoding="utf-8",
    )
    return project


class InterruptedApplyRecoveryTests(unittest.TestCase):
    def test_failed_write_creates_blocking_recovery_and_restore_clears_it(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            project = create_unity_project(root)
            gateway = AgentGateway(root / "config" / "agent_gateway.json", root / "audit")
            gateway.checkpoint_prepare_handler = lambda _path: {"ok": True}
            successful_calls: list[dict] = []

            def failing_write(args: dict) -> dict:
                project_root = Path(args["projectRoot"])
                (project_root / "Assets" / "generated-before-fail.txt").write_text("generated", encoding="utf-8")
                raise RuntimeError("Unity MCP disconnected after checkpoint")

            def successful_write(args: dict) -> dict:
                successful_calls.append(args)
                Path(args["projectRoot"], "Assets", "after-recovery.txt").write_text("ok", encoding="utf-8")
                return {"ok": True}

            gateway.register_write_handler("vrcforge_test_failing_write", "Failing write", "high", failing_write)
            gateway.register_write_handler("vrcforge_test_successful_write", "Successful write", "high", successful_write)

            request = gateway.create_apply_request(
                {
                    "target_tool": "vrcforge_test_failing_write",
                    "arguments": {"projectRoot": str(project)},
                }
            )
            approval_id = request["approval"]["id"]
            gateway.approve(approval_id)
            applied = gateway.apply_approved({"approval_id": approval_id})

            self.assertFalse(applied["ok"])
            self.assertEqual(applied["status"], "failed")
            checkpoint_id = applied["checkpoint"]["id"]
            recoveries = gateway.list_interrupted_apply_recoveries()
            self.assertTrue(recoveries["blockingWrites"])
            self.assertEqual(recoveries["activeCount"], 1)
            recovery = recoveries["recoveries"][0]
            self.assertEqual(recovery["status"], "needs_recovery")
            self.assertEqual(recovery["checkpointId"], checkpoint_id)

            preview = gateway.preview_interrupted_apply_recovery({"recoveryId": recovery["id"]})
            self.assertTrue(preview["ok"])
            self.assertTrue(preview["checkpointPreview"]["ok"])

            bundle = gateway.export_interrupted_apply_incident_bundle({"recoveryId": recovery["id"]})
            self.assertTrue(bundle["ok"])
            self.assertTrue(Path(bundle["path"]).is_file())

            blocked_request = gateway.create_apply_request(
                {
                    "target_tool": "vrcforge_test_successful_write",
                    "arguments": {"projectRoot": str(project)},
                }
            )
            blocked_id = blocked_request["approval"]["id"]
            gateway.approve(blocked_id)
            blocked = gateway.apply_approved({"approval_id": blocked_id})
            self.assertFalse(blocked["ok"])
            self.assertEqual(blocked["status"], "blocked_recovery")
            self.assertEqual(successful_calls, [])

            restored = gateway.restore_checkpoint({"checkpointId": checkpoint_id, "confirmRestore": True})
            self.assertTrue(restored["ok"])
            self.assertIn("resolvedApplyRecoveries", restored)
            self.assertFalse((project / "Assets" / "generated-before-fail.txt").exists())
            self.assertFalse(gateway.list_interrupted_apply_recoveries()["blockingWrites"])

            unblocked = gateway.apply_approved({"approval_id": blocked_id})
            self.assertTrue(unblocked["ok"])
            self.assertEqual(len(successful_calls), 1)

    def test_applying_recovery_survives_restart_and_can_be_manually_resolved(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            project = create_unity_project(root)
            first_gateway = AgentGateway(root / "config" / "agent_gateway.json", root / "audit")
            first_gateway.checkpoint_prepare_handler = lambda _path: {"ok": True}
            approval = {"id": "approval-crash", "targetTool": "vrcforge_test_hanging_write", "riskLevel": "high"}
            arguments = {"projectRoot": str(project)}
            checkpoint = first_gateway._create_pre_write_checkpoint(approval, arguments)
            self.assertIsNotNone(checkpoint)
            self.assertTrue(checkpoint["ok"])
            recovery = first_gateway._start_apply_recovery(approval, arguments, checkpoint)

            restarted_gateway = AgentGateway(root / "config" / "agent_gateway.json", root / "audit")
            calls: list[dict] = []
            restarted_gateway.register_write_handler(
                "vrcforge_test_after_restart",
                "Write after restart",
                "high",
                lambda args: calls.append(args) or {"ok": True},
            )
            restarted_gateway.register_write_handler(
                "vrcforge_resolve_interrupted_apply_recovery",
                "Resolve recovery",
                "medium",
                lambda args: restarted_gateway.resolve_interrupted_apply_recovery(args),
            )

            recoveries = restarted_gateway.list_interrupted_apply_recoveries()
            self.assertTrue(recoveries["blockingWrites"])
            self.assertEqual(recoveries["recoveries"][0]["id"], recovery["id"])
            self.assertEqual(recoveries["recoveries"][0]["status"], "applying")

            request = restarted_gateway.create_apply_request(
                {
                    "target_tool": "vrcforge_test_after_restart",
                    "arguments": {"projectRoot": str(project)},
                }
            )
            approval_id = request["approval"]["id"]
            restarted_gateway.approve(approval_id)
            blocked = restarted_gateway.apply_approved({"approval_id": approval_id})
            self.assertFalse(blocked["ok"])
            self.assertEqual(blocked["status"], "blocked_recovery")
            self.assertEqual(calls, [])

            resolve_request = restarted_gateway.create_apply_request(
                {
                    "target_tool": "vrcforge_resolve_interrupted_apply_recovery",
                    "arguments": {"recoveryId": recovery["id"], "confirmResolved": True},
                }
            )
            resolve_id = resolve_request["approval"]["id"]
            restarted_gateway.approve(resolve_id)
            resolved = restarted_gateway.apply_approved({"approval_id": resolve_id})
            self.assertTrue(resolved["ok"])
            self.assertFalse(restarted_gateway.list_interrupted_apply_recoveries()["blockingWrites"])

            unblocked = restarted_gateway.apply_approved({"approval_id": approval_id})
            self.assertTrue(unblocked["ok"])
            self.assertEqual(len(calls), 1)

    def test_unity_prepare_failure_falls_back_to_file_checkpoint(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            project = create_unity_project(root)
            gateway = AgentGateway(root / "config" / "agent_gateway.json", root / "audit")
            gateway.checkpoint_prepare_handler = lambda _path: {"ok": False, "error": "Unity MCP unavailable"}

            def failing_write(args: dict) -> dict:
                project_root = Path(args["projectRoot"])
                (project_root / "Assets" / "generated-after-prepare-warning.txt").write_text("generated", encoding="utf-8")
                raise RuntimeError("Unity crashed after file checkpoint")

            gateway.register_write_handler("vrcforge_test_prepare_fallback", "Prepare fallback write", "high", failing_write)
            request = gateway.create_apply_request(
                {
                    "target_tool": "vrcforge_test_prepare_fallback",
                    "arguments": {"projectRoot": str(project)},
                }
            )
            approval_id = request["approval"]["id"]
            gateway.approve(approval_id)
            applied = gateway.apply_approved({"approval_id": approval_id})

            self.assertFalse(applied["ok"])
            checkpoint = applied["checkpoint"]
            self.assertTrue(checkpoint["ok"])
            self.assertEqual(checkpoint["strategy"], "archive")
            self.assertEqual(checkpoint["unityPrepare"]["error"], "Unity MCP unavailable")
            self.assertIn("Unity prepare checkpoint failed", " ".join(checkpoint.get("warnings") or []))

            recoveries = gateway.list_interrupted_apply_recoveries()
            self.assertTrue(recoveries["blockingWrites"])
            restored = gateway.restore_checkpoint({"checkpointId": checkpoint["id"], "confirmRestore": True})
            self.assertTrue(restored["ok"])
            self.assertFalse((project / "Assets" / "generated-after-prepare-warning.txt").exists())


if __name__ == "__main__":
    unittest.main()
