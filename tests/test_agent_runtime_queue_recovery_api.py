import tempfile
import unittest
from pathlib import Path

from fastapi.testclient import TestClient

import dashboard_server
from agent_gateway import AgentGateway


class AgentRuntimeQueueRecoveryApiTests(unittest.TestCase):
    def test_steer_delivers_quote_image_and_file_to_exact_active_turn(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            gateway = AgentGateway(Path(tmp) / "config.json", Path(tmp) / "audit")
            gateway.runtime_sessions.begin_turn(
                session_id="session", turn_id="running-turn", client_turn_id="active-input",
            )
            original_gateway = dashboard_server.AGENT_GATEWAY
            dashboard_server.AGENT_GATEWAY = gateway
            try:
                attachments = [
                    {"id": "selection", "name": "Selected text", "type": "text/plain",
                     "payloadKind": "text", "text": "执行前会创建检查点。"},
                    {"id": "image", "name": "layout.png", "type": "image/png",
                     "payloadKind": "data_url", "dataUrl": "data:image/png;base64,AAAA"},
                    {"id": "file", "name": "materials.zip", "type": "application/zip",
                     "payloadKind": "vault_file", "payloadHash": "a" * 64},
                ]
                with TestClient(dashboard_server.app) as client:
                    response = client.post("/api/app/agent/runs/queue", json={
                        "sessionId": "session", "laneId": "chat", "clientTurnId": "steer-input",
                        "targetClientTurnId": "active-input", "message": "不用创建", "attachments": attachments,
                    })
                self.assertEqual(response.status_code, 200, response.text)
                self.assertEqual(response.json()["mode"], "steer")
                [steer] = gateway.runtime_sessions.drain_steer(session_id="session", client_turn_id="active-input")
                self.assertEqual(steer["message"], "不用创建")
                self.assertEqual(steer["attachments"][0]["text"], attachments[0]["text"])
                self.assertEqual(steer["attachments"][1]["dataUrl"], attachments[1]["dataUrl"])
                self.assertEqual(steer["attachments"][2]["payloadHash"], attachments[2]["payloadHash"])
                self.assertEqual(gateway.list_runtime_followups(session_id="chat"), [])
            finally:
                dashboard_server.AGENT_GATEWAY = original_gateway

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

