import asyncio
import time
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock, patch

import dashboard_server
from agent_gateway import AgentGateway
from agent_runtime_run_ledger import AgentRuntimeRunLedger


class DashboardRuntimeLoopTests(unittest.TestCase):
    def test_wrong_followup_owner_releases_claim_without_dispatch(self) -> None:
        for session_id, client_turn_id in [("wrong-session", "right-client"), ("right-session", "wrong-client")]:
            with self.subTest(session_id=session_id, client_turn_id=client_turn_id), TemporaryDirectory() as tmp:
                root = Path(tmp)
                queue = AgentGateway(root / "config.json", root / "audit")
                inserted = queue.enqueue_runtime_followup({"sessionId": "lane", "runtimeSessionId": "right-session",
                                                           "clientTurnId": "right-client", "message": "fixture"})
                with (patch.object(dashboard_server, "RUNTIME_QUEUE_PORT", queue),
                      patch.object(dashboard_server.AGENT_GATEWAY, "runtime_message") as run):
                    with self.assertRaises(dashboard_server.HTTPException) as failure:
                        asyncio.run(dashboard_server._execute_app_agent_runtime_message(
                            dashboard_server.AgentRuntimeMessageRequest(message="fixture", session_id=session_id,
                                client_turn_id=client_turn_id, followup_queue_id=inserted["queueId"], followup_lane_id="lane"),
                            human_user_message=False,
                        ))
                self.assertEqual(failure.exception.status_code, 409)
                run.assert_not_called()
                self.assertEqual(queue.list_runtime_followups(session_id="lane")[0]["status"], "pending")
                self.assertEqual(len(queue.claim_runtime_followups(session_id="lane", owner_id="retry", queue_id=inserted["queueId"])), 1)

    def test_runtime_message_ledger_projection_keeps_event_loop_responsive(self) -> None:
        async def exercise() -> tuple[int, int]:
            heartbeat_count = 0

            async def heartbeat() -> None:
                nonlocal heartbeat_count
                while True:
                    heartbeat_count += 1
                    await asyncio.sleep(0.01)

            def slow_list_runs(*, limit: int = 50, session_id: str = "", **_kwargs: object) -> dict:
                time.sleep(0.15)
                return {
                    "ok": True,
                    "schema": "vrcforge.runtime_runs.v1",
                    "runs": [],
                    "events": [],
                    "count": 0,
                    "limit": limit,
                    "sessionId": session_id,
                }

            heartbeat_task = asyncio.create_task(heartbeat())
            try:
                await asyncio.sleep(0.03)
                before_projection = heartbeat_count
                with (
                    patch.object(
                        dashboard_server.AGENT_GATEWAY,
                        "runtime_message",
                        return_value={"sessionId": "test-session", "turnId": "test-turn"},
                    ),
                    patch.object(
                        AgentRuntimeRunLedger,
                        "read_terminal_continuation",
                        return_value=None,
                    ),
                    patch.object(AgentRuntimeRunLedger, "list_runs", side_effect=slow_list_runs) as list_runs,
                    patch.object(dashboard_server.EVENT_BUS, "broadcast", new=AsyncMock()),
                    patch.object(dashboard_server.TELEGRAM_SERVICE, "notify_foreground_result"),
                    patch.object(
                        dashboard_server,
                        "attach_agent_harness_journey_receipt",
                        side_effect=lambda payload: payload,
                    ),
                    patch.object(
                        type(dashboard_server.AGENT_GATEWAY.approval_transactions),
                        "list_approvals",
                        return_value=[],
                    ),
                ):
                    payload = await dashboard_server._execute_app_agent_runtime_message(
                        dashboard_server.AgentRuntimeMessageRequest(
                            message="synthetic event-loop regression",
                            session_id="test-session",
                        ),
                        human_user_message=False,
                    )
                during_projection = heartbeat_count
                self.assertEqual(payload["turnId"], "test-turn")
                self.assertEqual(list_runs.call_count, 1)
                return during_projection - before_projection, heartbeat_count
            finally:
                heartbeat_task.cancel()
                try:
                    await heartbeat_task
                except asyncio.CancelledError:
                    pass

        heartbeat_ticks_during_projection, _ = asyncio.run(exercise())
        self.assertGreaterEqual(heartbeat_ticks_during_projection, 3)

    def test_followup_finally_recovery_read_keeps_event_loop_responsive(self) -> None:
        async def exercise() -> int:
            heartbeat_count = 0

            async def heartbeat() -> None:
                nonlocal heartbeat_count
                while True:
                    heartbeat_count += 1
                    await asyncio.sleep(0.01)

            def fail_runtime(*_args, **_kwargs):
                raise RuntimeError("synthetic runtime failure")

            def slow_get_runtime_session(*_args, **_kwargs):
                time.sleep(0.15)
                return {"status": "completed"}

            queue = SimpleNamespace(
                claim_runtime_followups=Mock(return_value=[{"clientTurnId": "client-turn", "claimToken": "claim"}]),
                ack_runtime_followup=Mock(return_value=True),
                release_runtime_followup=Mock(return_value=True),
            )
            heartbeat_task = asyncio.create_task(heartbeat())
            try:
                await asyncio.sleep(0.03)
                before_recovery = heartbeat_count
                with (
                    patch.object(dashboard_server.AGENT_GATEWAY, "runtime_message", side_effect=fail_runtime),
                    patch.object(
                        dashboard_server.AGENT_GATEWAY,
                        "get_runtime_session",
                        side_effect=slow_get_runtime_session,
                    ) as get_runtime_session,
                    patch.object(dashboard_server, "RUNTIME_QUEUE_PORT", queue),
                ):
                    with self.assertRaisesRegex(RuntimeError, "synthetic runtime failure"):
                        await dashboard_server._execute_app_agent_runtime_message(
                            dashboard_server.AgentRuntimeMessageRequest(
                                message="synthetic followup failure",
                                session_id="followup-session",
                                client_turn_id="client-turn",
                                followup_queue_id="followup-queue",
                                followup_lane_id="followup-lane",
                            ),
                            human_user_message=False,
                        )
                ticks_during_recovery = heartbeat_count - before_recovery
                get_runtime_session.assert_called_once_with("followup-session", client_turn_id="client-turn")
                queue.ack_runtime_followup.assert_called_once_with("followup-queue", "followup-lane", "claim")
                return ticks_during_recovery
            finally:
                heartbeat_task.cancel()
                try:
                    await heartbeat_task
                except asyncio.CancelledError:
                    pass

        self.assertGreaterEqual(asyncio.run(exercise()), 3)


if __name__ == "__main__":
    unittest.main()
