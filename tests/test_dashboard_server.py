import json
import tempfile
import unittest
from pathlib import Path

from fastapi.testclient import TestClient

import dashboard_server


class DashboardServerTests(unittest.TestCase):
    def test_websocket_sends_bootstrap_payload(self) -> None:
        with TestClient(dashboard_server.app) as client:
            with client.websocket_connect("/ws") as websocket:
                messages = [websocket.receive_json() for _ in range(3)]
                hello_messages = [message for message in messages if message["type"] == "hello"]
                self.assertTrue(hello_messages)
                self.assertIn("projects", hello_messages[0]["payload"])
                self.assertIn("unityStatus", hello_messages[0]["payload"])

    def test_root_serves_dashboard_page(self) -> None:
        with TestClient(dashboard_server.app) as client:
            response = client.get("/")
            self.assertEqual(response.status_code, 200)
            self.assertIn("Avatar Control Deck", response.text)
            self.assertIn("Provider 与模型", response.text)

    def test_health_returns_defaults_and_state(self) -> None:
        with TestClient(dashboard_server.app) as client:
            response = client.get("/api/health")
            self.assertEqual(response.status_code, 200)

            payload = response.json()
            self.assertTrue(payload["ok"])
            self.assertIn("defaults", payload)
            self.assertIn("state", payload)
            self.assertIn("projects", payload)
            self.assertIn("apiConfig", payload)
            self.assertIn("configPath", payload)

    def test_api_config_endpoint_persists_and_returns_effective_provider(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            original_config_path = dashboard_server.CONFIG_PATH
            original_api_config = dashboard_server.DASHBOARD_API_CONFIG
            dashboard_server.CONFIG_PATH = Path(temp_dir) / "config.json"
            dashboard_server.DASHBOARD_API_CONFIG = dashboard_server.DashboardApiConfig(
                provider="gemini",
                api_key="",
                base_url="https://generativelanguage.googleapis.com/v1beta/openai",
                model="gemini-2.5-flash",
            )

            try:
                with TestClient(dashboard_server.app) as client:
                    response = client.post(
                        "/api/config",
                        json={
                            "provider": "anthropic",
                            "api_key": "anthropic-secret",
                            "base_url": "https://ignored.example.com",
                            "model": "claude-opus-4-6",
                        },
                    )
                    self.assertEqual(response.status_code, 200)
                    payload = response.json()
                    self.assertEqual(payload["apiConfig"]["provider"], "anthropic")
                    self.assertEqual(payload["apiConfig"]["base_url"], "")
                    self.assertEqual(payload["effective"]["model"], "claude-opus-4-6")
                    self.assertTrue(dashboard_server.CONFIG_PATH.exists())

                    saved_payload = json.loads(dashboard_server.CONFIG_PATH.read_text(encoding="utf-8"))
                    self.assertEqual(saved_payload["api"]["provider"], "anthropic")
                    self.assertEqual(saved_payload["api"]["base_url"], "")
            finally:
                dashboard_server.CONFIG_PATH = original_config_path
                dashboard_server.DASHBOARD_API_CONFIG = original_api_config

    def test_discover_projects_reads_unity_project_metadata(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            project_dir = root / "Sample Avatar Project"
            (project_dir / "ProjectSettings").mkdir(parents=True)
            (project_dir / "Packages").mkdir(parents=True)
            (project_dir / "Assets" / "VRCAutoRig" / "Editor").mkdir(parents=True)

            (project_dir / "ProjectSettings" / "ProjectVersion.txt").write_text(
                "m_EditorVersion: 2022.3.22f1\n",
                encoding="utf-8",
            )
            (project_dir / "Packages" / "manifest.json").write_text(
                json.dumps(
                    {
                        "dependencies": {
                            "com.coplaydev.unity-mcp": "https://github.com/CoplayDev/unity-mcp.git?path=/MCPForUnity#main"
                        }
                    }
                ),
                encoding="utf-8",
            )
            (project_dir / "Assets" / "VRCAutoRig" / "Editor" / "BlendshapeExporter.cs").write_text(
                "// test",
                encoding="utf-8",
            )

            original_selected = dashboard_server.DASHBOARD_STATE.selected_project_path
            dashboard_server.DASHBOARD_STATE.selected_project_path = dashboard_server.normalize_path_string(str(project_dir))
            try:
                projects = dashboard_server.discover_projects([root])
            finally:
                dashboard_server.DASHBOARD_STATE.selected_project_path = original_selected

            self.assertEqual(len(projects), 1)
            self.assertEqual(projects[0]["name"], "Sample Avatar Project")
            self.assertEqual(projects[0]["editorVersion"], "2022.3.22f1")
            self.assertTrue(projects[0]["hasVrcAutoRig"])
            self.assertTrue(projects[0]["hasUnityMcpPackage"])
            self.assertTrue(projects[0]["selected"])


if __name__ == "__main__":
    unittest.main()
