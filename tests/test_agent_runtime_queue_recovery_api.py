import tempfile
import unittest
from pathlib import Path

from fastapi.testclient import TestClient

import dashboard_server
from agent_gateway import AgentGateway


class AgentRuntimeQueueRecoveryApiTests(unittest.TestCase):
    def test_queue_list_without_lane_returns_recoverable_followups(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            gateway = AgentGateway(Path(tmp) / "config.json", Path(tmp) / "audit")
            original_gateway = dashboard_server.AGENT_GATEWAY
            dashboard_server.AGENT_GATEWAY = gateway
            try:
                with TestClient(dashboard_server.app) as client:
                    queued = client.post(
                        "/api/app/agent/runs/queue",
                        json={
                            "sessionId": "runtime-session-recovered",
                            "laneId": "chat-lane-recovered",
                            "clientTurnId": "client-turn-recovered",
                            "message": "recover the pending turn",
                            "provider": "fixture",
                            "providerLabel": "Fixture",
                            "model": "fixture-model",
                        },
                    )
                    self.assertEqual(queued.status_code, 200)
                    self.assertTrue(queued.json()["accepted"])

                    recovered = client.get(
                        "/api/app/agent/runs/queue",
                        params={"includeTerminal": False},
                    )
                self.assertEqual(recovered.status_code, 200)
                items = recovered.json()["items"]
                self.assertEqual(len(items), 1)
                self.assertEqual(items[0]["clientTurnId"], "client-turn-recovered")
                self.assertEqual(items[0]["sessionId"], "chat-lane-recovered")
                self.assertEqual(items[0]["runtimeSessionId"], "runtime-session-recovered")
            finally:
                dashboard_server.AGENT_GATEWAY = original_gateway

