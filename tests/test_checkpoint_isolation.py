from __future__ import annotations

import tempfile
import threading
import unittest
from pathlib import Path

from agent_gateway import AgentGateway, AgentGatewayError


def create_unity_project(root: Path, name: str = "UnityProject") -> Path:
    project = root / name
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


def create_gateway(root: Path) -> AgentGateway:
    gateway = AgentGateway(root / "config" / "agent_gateway.json", root / "audit")
    gateway.checkpoint_prepare_handler = lambda _path: {"ok": True}
    return gateway


def approved_apply_request(gateway: AgentGateway, target_tool: str, project: Path) -> str:
    request = gateway.create_apply_request(
        {
            "target_tool": target_tool,
            "arguments": {"projectRoot": str(project)},
        }
    )
    approval_id = request["approval"]["id"]
    gateway.approve(approval_id)
    return approval_id


class ConcurrentApplyWriteGuardTests(unittest.TestCase):
    def test_second_apply_is_blocked_while_first_write_is_in_flight(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            project = create_unity_project(root)
            gateway = create_gateway(root)

            entered_write = threading.Event()
            release_write = threading.Event()
            slow_calls: list[dict] = []
            fast_calls: list[dict] = []

            def slow_write(args: dict) -> dict:
                slow_calls.append(args)
                Path(args["projectRoot"], "Assets", "slow-write.txt").write_text("slow", encoding="utf-8")
                entered_write.set()
                if not release_write.wait(timeout=30):
                    raise RuntimeError("Test release event was never set.")
                return {"ok": True}

            def fast_write(args: dict) -> dict:
                fast_calls.append(args)
                Path(args["projectRoot"], "Assets", "fast-write.txt").write_text("fast", encoding="utf-8")
                return {"ok": True}

            gateway.register_write_handler("vrcforge_test_slow_write", "Slow write", "high", slow_write)
            gateway.register_write_handler("vrcforge_test_fast_write", "Fast write", "high", fast_write)

            slow_id = approved_apply_request(gateway, "vrcforge_test_slow_write", project)
            fast_id = approved_apply_request(gateway, "vrcforge_test_fast_write", project)

            slow_result: dict = {}

            def run_slow_apply() -> None:
                slow_result.update(gateway.apply_approved({"approval_id": slow_id}))

            worker = threading.Thread(target=run_slow_apply, daemon=True)
            worker.start()
            self.assertTrue(entered_write.wait(timeout=30), "slow write handler never started")

            blocked = gateway.apply_approved({"approval_id": fast_id})
            self.assertFalse(blocked["ok"])
            self.assertEqual(blocked["status"], "blocked_concurrent_write")
            self.assertEqual(fast_calls, [])
            in_flight = blocked["inFlightWrites"]
            self.assertEqual(len(in_flight), 1)
            self.assertEqual(in_flight[0]["approvalId"], slow_id)
            self.assertEqual(in_flight[0]["targetTool"], "vrcforge_test_slow_write")

            # 只允许第一笔写产生检查点：并发写被拒之门外，不会追加第二条记录。
            checkpoints = gateway.list_checkpoints({"projectRoot": str(project)})
            self.assertEqual(checkpoints["count"], 1)
            self.assertEqual(checkpoints["checkpoints"][0]["approvalId"], slow_id)

            release_write.set()
            worker.join(timeout=30)
            self.assertFalse(worker.is_alive())
            self.assertTrue(slow_result["ok"])
            self.assertEqual(slow_result["status"], "applied")

            # 第一笔写收尾后，登记表已清空，被挡下的写可以重新执行。
            unblocked = gateway.apply_approved({"approval_id": fast_id})
            self.assertTrue(unblocked["ok"])
            self.assertEqual(unblocked["status"], "applied")
            self.assertEqual(len(fast_calls), 1)
            checkpoints = gateway.list_checkpoints({"projectRoot": str(project)})
            self.assertEqual(checkpoints["count"], 2)

    def test_in_flight_registration_is_cleared_when_write_fails(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            project = create_unity_project(root)
            gateway = create_gateway(root)

            def failing_write(args: dict) -> dict:
                raise RuntimeError("Write handler crashed mid-apply.")

            gateway.register_write_handler("vrcforge_test_failing_write", "Failing write", "high", failing_write)

            approval_id = approved_apply_request(gateway, "vrcforge_test_failing_write", project)
            failed = gateway.apply_approved({"approval_id": approval_id})
            self.assertFalse(failed["ok"])
            self.assertEqual(failed["status"], "failed")

            # finally 分支必须清空登记表；后续写只会被 recovery 记录挡下
            # （blocked_recovery），而不是被幽灵在飞写挡下。
            self.assertEqual(gateway._in_flight_apply_writes, {})

            retry_id = approved_apply_request(gateway, "vrcforge_test_failing_write", project)
            blocked = gateway.apply_approved({"approval_id": retry_id})
            self.assertFalse(blocked["ok"])
            self.assertEqual(blocked["status"], "blocked_recovery")

    def test_apply_transition_io_failure_restores_retryable_approval(self) -> None:
        for failing_method in ("append_audit", "_append_runtime_run"):
            with self.subTest(failing_method=failing_method), tempfile.TemporaryDirectory() as tmp:
                root = Path(tmp)
                project = create_unity_project(root)
                gateway = create_gateway(root)
                calls: list[dict] = []

                gateway.register_write_handler(
                    "vrcforge_test_write",
                    "Test write",
                    "high",
                    lambda args: calls.append(args) or {"ok": True},
                )
                approval_id = approved_apply_request(gateway, "vrcforge_test_write", project)
                original = getattr(gateway, failing_method)

                def fail_transition(*_args: object, **_kwargs: object) -> None:
                    raise OSError(f"simulated {failing_method} failure")

                setattr(gateway, failing_method, fail_transition)
                with self.assertRaises(AgentGatewayError) as raised:
                    gateway.apply_approved({"approval_id": approval_id})
                self.assertEqual(raised.exception.status_code, 500)
                self.assertEqual(gateway._approvals[approval_id]["status"], "approved")
                self.assertEqual(gateway._in_flight_apply_writes, {})
                self.assertEqual(calls, [])

                setattr(gateway, failing_method, original)
                retried = gateway.apply_approved({"approval_id": approval_id})
                self.assertTrue(retried["ok"])
                self.assertEqual(retried["status"], "applied")
                self.assertEqual(len(calls), 1)

    def test_recovery_writes_wait_for_the_live_write_to_finish(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            project = create_unity_project(root)
            gateway = create_gateway(root)

            entered_write = threading.Event()
            release_write = threading.Event()
            resolve_calls: list[dict] = []

            def slow_write(args: dict) -> dict:
                entered_write.set()
                if not release_write.wait(timeout=30):
                    raise RuntimeError("Test release event was never set.")
                return {"ok": True}

            gateway.register_write_handler("vrcforge_test_slow_write", "Slow write", "high", slow_write)
            gateway.register_write_handler(
                "vrcforge_resolve_interrupted_apply_recovery",
                "Resolve recovery",
                "medium",
                lambda args: resolve_calls.append(args) or {"ok": True},
            )

            slow_id = approved_apply_request(gateway, "vrcforge_test_slow_write", project)
            exempt_id = approved_apply_request(
                gateway, "vrcforge_resolve_interrupted_apply_recovery", project
            )

            worker = threading.Thread(
                target=lambda: gateway.apply_approved({"approval_id": slow_id}), daemon=True
            )
            worker.start()
            self.assertTrue(entered_write.wait(timeout=30), "slow write handler never started")

            blocked = gateway.apply_approved({"approval_id": exempt_id})
            self.assertFalse(blocked["ok"])
            self.assertEqual(blocked["status"], "blocked_concurrent_write")
            self.assertEqual(resolve_calls, [])

            release_write.set()
            worker.join(timeout=30)
            self.assertFalse(worker.is_alive())

            exempt = gateway.apply_approved({"approval_id": exempt_id})
            self.assertTrue(exempt["ok"])
            self.assertEqual(len(resolve_calls), 1)

    def test_global_write_lane_also_serializes_different_projects(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            project_a = create_unity_project(root, "UnityProjectA")
            project_b = create_unity_project(root, "UnityProjectB")
            gateway = create_gateway(root)
            entered_write = threading.Event()
            release_write = threading.Event()

            def slow_write(_args: dict) -> dict:
                entered_write.set()
                if not release_write.wait(timeout=30):
                    raise RuntimeError("Test release event was never set.")
                return {"ok": True}

            gateway.register_write_handler("vrcforge_test_slow_write", "Slow write", "high", slow_write)
            gateway.register_write_handler("vrcforge_test_fast_write", "Fast write", "high", lambda _args: {"ok": True})
            slow_id = approved_apply_request(gateway, "vrcforge_test_slow_write", project_a)
            fast_id = approved_apply_request(gateway, "vrcforge_test_fast_write", project_b)

            worker = threading.Thread(
                target=lambda: gateway.apply_approved({"approval_id": slow_id}),
                daemon=True,
            )
            worker.start()
            self.assertTrue(entered_write.wait(timeout=30), "slow write handler never started")

            blocked = gateway.apply_approved({"approval_id": fast_id})
            self.assertFalse(blocked["ok"])
            self.assertEqual(blocked["status"], "blocked_concurrent_write")
            self.assertEqual(gateway.list_checkpoints({"projectRoot": str(project_b)})["count"], 0)

            release_write.set()
            worker.join(timeout=30)
            self.assertFalse(worker.is_alive())
            self.assertTrue(gateway.apply_approved({"approval_id": fast_id})["ok"])


class CheckpointStorageIsolationTests(unittest.TestCase):
    def test_relocate_waits_for_checkpoint_registration_and_then_fails_closed(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            project = create_unity_project(root)
            gateway = create_gateway(root)
            append_entered = threading.Event()
            release_append = threading.Event()
            write_entered = threading.Event()
            release_write = threading.Event()
            relocate_finished = threading.Event()
            apply_result: dict = {}
            relocate_result: dict = {}

            def slow_write(_args: dict) -> dict:
                write_entered.set()
                if not release_write.wait(timeout=30):
                    raise RuntimeError("Test release event was never set.")
                return {"ok": True}

            gateway.register_write_handler("vrcforge_test_slow_write", "Slow write", "high", slow_write)
            approval_id = approved_apply_request(gateway, "vrcforge_test_slow_write", project)
            original_append = gateway._append_checkpoint

            def pausing_append(record: dict) -> None:
                if record.get("ok") and record.get("strategy") == "archive":
                    append_entered.set()
                    if not release_append.wait(timeout=30):
                        raise RuntimeError("Test checkpoint append was never released.")
                original_append(record)

            gateway._append_checkpoint = pausing_append  # type: ignore[method-assign]

            apply_worker = threading.Thread(
                target=lambda: apply_result.update(gateway.apply_approved({"approval_id": approval_id})),
                daemon=True,
            )
            apply_worker.start()
            self.assertTrue(append_entered.wait(timeout=30), "archive was not created before registration")

            target_dir = root / "relocated-checkpoints"

            def relocate() -> None:
                relocate_result.update(gateway.relocate_checkpoint_archives(str(target_dir)))
                relocate_finished.set()

            relocate_worker = threading.Thread(target=relocate, daemon=True)
            relocate_worker.start()
            self.assertFalse(
                relocate_finished.wait(timeout=0.2),
                "relocation must not pass a checkpoint that has not reached its durable projection",
            )

            release_append.set()
            self.assertTrue(write_entered.wait(timeout=30), "write handler never started")
            self.assertTrue(relocate_finished.wait(timeout=30), "relocation did not resume")
            self.assertFalse(relocate_result["ok"])
            self.assertEqual(relocate_result["code"], "active_recovery")

            release_write.set()
            apply_worker.join(timeout=30)
            relocate_worker.join(timeout=30)
            self.assertFalse(apply_worker.is_alive())
            self.assertFalse(relocate_worker.is_alive())
            self.assertTrue(apply_result["ok"])
            checkpoint_id = apply_result["checkpoint"]["id"]
            self.assertTrue(gateway.preview_restore_checkpoint({"checkpointId": checkpoint_id})["ok"])


class CrossProjectCheckpointIsolationTests(unittest.TestCase):
    def test_checkpoints_are_scoped_per_project_and_restore_does_not_touch_other_project(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            project_a = create_unity_project(root, "UnityProjectA")
            project_b = create_unity_project(root, "UnityProjectB")
            gateway = create_gateway(root)

            def write_marker(args: dict) -> dict:
                Path(args["projectRoot"], "Assets", "marker.txt").write_text("written", encoding="utf-8")
                return {"ok": True}

            gateway.register_write_handler("vrcforge_test_marker_write", "Marker write", "high", write_marker)

            approval_a = approved_apply_request(gateway, "vrcforge_test_marker_write", project_a)
            applied_a = gateway.apply_approved({"approval_id": approval_a})
            self.assertTrue(applied_a["ok"])
            checkpoint_a = applied_a["checkpoint"]

            approval_b = approved_apply_request(gateway, "vrcforge_test_marker_write", project_b)
            applied_b = gateway.apply_approved({"approval_id": approval_b})
            self.assertTrue(applied_b["ok"])
            checkpoint_b = applied_b["checkpoint"]

            # 检查点记录各自归属自己的项目根，互不混淆。
            self.assertEqual(checkpoint_a["projectRoot"], str(project_a.resolve()))
            self.assertEqual(checkpoint_b["projectRoot"], str(project_b.resolve()))
            self.assertNotEqual(checkpoint_a["id"], checkpoint_b["id"])
            if checkpoint_a.get("strategy") == "archive":
                self.assertNotEqual(checkpoint_a["archivePath"], checkpoint_b["archivePath"])

            only_a = gateway.list_checkpoints({"projectRoot": str(project_a)})
            self.assertEqual(only_a["count"], 1)
            self.assertEqual(only_a["checkpoints"][0]["id"], checkpoint_a["id"])
            only_b = gateway.list_checkpoints({"projectRoot": str(project_b)})
            self.assertEqual(only_b["count"], 1)
            self.assertEqual(only_b["checkpoints"][0]["id"], checkpoint_b["id"])

            # 恢复 A 的检查点：A 回到写前状态，B 的写入结果原封不动。
            (project_b / "Assets" / "post-checkpoint.txt").write_text("keep-me", encoding="utf-8")
            restored = gateway.restore_checkpoint(
                {"checkpointId": checkpoint_a["id"], "confirmRestore": True}
            )
            self.assertTrue(restored["ok"])
            self.assertFalse((project_a / "Assets" / "marker.txt").exists())
            self.assertEqual(
                (project_a / "Assets" / "existing.txt").read_text(encoding="utf-8"), "before"
            )
            self.assertTrue((project_b / "Assets" / "marker.txt").exists())
            self.assertEqual(
                (project_b / "Assets" / "post-checkpoint.txt").read_text(encoding="utf-8"),
                "keep-me",
            )


if __name__ == "__main__":
    unittest.main()
