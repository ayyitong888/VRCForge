import json
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

from fastapi.testclient import TestClient

import dashboard_server


class DashboardServerTests(unittest.TestCase):
    def setUp(self) -> None:
        self.status_snapshot_patcher = patch(
            "dashboard_server.build_unity_status_snapshot",
            return_value={
                "connected": False,
                "host": "127.0.0.1",
                "port": 8080,
                "instance": "",
                "projectPath": "",
                "output": "",
                "parsed": None,
                "error": "mocked in tests",
            },
        )
        self.status_snapshot_patcher.start()

    def tearDown(self) -> None:
        self.status_snapshot_patcher.stop()

    def test_websocket_sends_bootstrap_payload(self) -> None:
        with TestClient(dashboard_server.app) as client:
            with client.websocket_connect("/ws") as websocket:
                message = websocket.receive_json()
                self.assertEqual(message["type"], "hello")
                self.assertIn("projects", message["payload"])
                self.assertIn("unityStatus", message["payload"])

    def test_root_serves_dashboard_page(self) -> None:
        with TestClient(dashboard_server.app) as client:
            response = client.get("/")
            self.assertEqual(response.status_code, 200)
            self.assertIn("VRCAutoRig 深色控制台", response.text)
            self.assertIn("Gemini Vision 审核", response.text)

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
            self.assertEqual(payload["defaults"]["sourceMode"], "unity_live_export")
            self.assertFalse(payload["defaults"]["mockExecute"])

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

    @patch("dashboard_server.execute_dashboard_code")
    @patch("dashboard_server.load_dashboard_settings")
    def test_scene_avatar_scan_endpoint_returns_unity_descriptor_list(
        self,
        mock_load_settings,
        mock_execute_dashboard_code,
    ) -> None:
        mock_load_settings.return_value = SimpleNamespace(
            unity_mcp_timeout_seconds=30,
            unity_mcp_host="127.0.0.1",
            unity_mcp_port=8080,
            unity_mcp_instance="",
        )
        mock_execute_dashboard_code.return_value = [
            {"avatarName": "HeroAvatar", "avatarPath": "Scene/HeroAvatar", "sceneName": "AvatarScene"},
            {"avatarName": "VillainAvatar", "avatarPath": "Scene/VillainAvatar", "sceneName": "AvatarScene"},
        ]

        with TestClient(dashboard_server.app) as client:
            response = client.post("/api/scene/avatars", json={"unity_host": "127.0.0.1", "unity_port": 8080})

        self.assertEqual(response.status_code, 200)
        payload = response.json()
        self.assertEqual(payload["avatarCount"], 2)
        self.assertEqual(payload["avatars"][0]["avatarName"], "HeroAvatar")

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

    def test_to_artifact_url_maps_local_artifacts_path(self) -> None:
        path = str((dashboard_server.ARTIFACTS_DIR / "dashboard" / "latest" / "vision_capture.png").resolve())
        url = dashboard_server.to_artifact_url(path)
        self.assertEqual(url, "/artifacts/dashboard/latest/vision_capture.png")

    # ------------------------------------------------------------------
    # /api/clothes/apply-fx (dry_run=True — no Unity needed)
    # ------------------------------------------------------------------
    def test_apply_clothing_fx_dry_run_returns_generated_csharp(self) -> None:
        items = [
            {
                "displayName": "Jacket",
                "parameterName": "Cloth_Jacket",
                "sampleObjectPath": "MyAvatar/Body/Jacket",
                "animationClipName": "FX_Jacket_Toggle",
            }
        ]
        with TestClient(dashboard_server.app) as client:
            response = client.post(
                "/api/clothes/apply-fx",
                json={"avatar_path": "MyAvatar", "items": items, "dry_run": True},
            )
        self.assertEqual(response.status_code, 200)
        payload = response.json()
        self.assertTrue(payload["ok"])
        self.assertTrue(payload["dryRun"])
        self.assertIn("generatedCsharp", payload)
        self.assertIn("Cloth_Jacket", payload["generatedCsharp"])
        self.assertIn("AnimationClip", payload["generatedCsharp"])

    def test_apply_clothing_fx_dry_run_no_items_raises_400(self) -> None:
        with TestClient(dashboard_server.app) as client:
            response = client.post(
                "/api/clothes/apply-fx",
                json={"avatar_path": "MyAvatar", "items": [], "dry_run": True},
            )
        self.assertEqual(response.status_code, 400)

    # ------------------------------------------------------------------
    # /api/parameters/apply-optimization (dry_run=True)
    # ------------------------------------------------------------------
    def test_apply_parameter_optimization_dry_run_returns_diff_and_csharp(self) -> None:
        suggestions = [
            {"name": "IsWearing", "currentType": "Int", "suggestedType": "Bool", "reason": "heuristic"},
        ]
        with TestClient(dashboard_server.app) as client:
            response = client.post(
                "/api/parameters/apply-optimization",
                json={"avatar_path": "MyAvatar", "suggestions": suggestions, "dry_run": True},
            )
        self.assertEqual(response.status_code, 200)
        payload = response.json()
        self.assertTrue(payload["ok"])
        self.assertTrue(payload["dryRun"])
        self.assertEqual(payload["appliedCount"], 1)
        self.assertEqual(payload["diff"][0]["name"], "IsWearing")
        self.assertEqual(payload["diff"][0]["from"], "Int")
        self.assertEqual(payload["diff"][0]["to"], "Bool")
        self.assertIn("IsWearing", payload["generatedCsharp"])

    def test_apply_parameter_optimization_no_suggestions_raises_400(self) -> None:
        with TestClient(dashboard_server.app) as client:
            response = client.post(
                "/api/parameters/apply-optimization",
                json={"avatar_path": "MyAvatar", "suggestions": [], "dry_run": True},
            )
        self.assertEqual(response.status_code, 400)

    # ------------------------------------------------------------------
    # /api/vision/capture-multi (needs Unity — verify endpoint exists + 503)
    # ------------------------------------------------------------------
    @patch("dashboard_server.execute_dashboard_code", side_effect=dashboard_server.UnityMcpError("not connected"))
    @patch("dashboard_server.load_dashboard_settings")
    def test_capture_multi_endpoint_fails_gracefully_when_unity_offline(
        self, mock_load_settings, _mock_execute
    ) -> None:
        mock_load_settings.return_value = SimpleNamespace(
            unity_mcp_timeout_seconds=5,
            unity_mcp_host="127.0.0.1",
            unity_mcp_port=8080,
            unity_mcp_instance="",
            execute_tool_name="execute_csharp",
        )
        with TestClient(dashboard_server.app) as client:
            response = client.post(
                "/api/vision/capture-multi",
                json={"angles": ["front", "back"], "width": 512, "height": 512},
            )
        self.assertIn(response.status_code, (400, 503))

    # ------------------------------------------------------------------
    # /api/vision/audit-multi — validates multi-path logic
    # ------------------------------------------------------------------
    def test_audit_multi_requires_image_paths(self) -> None:
        with TestClient(dashboard_server.app) as client:
            response = client.post("/api/vision/audit-multi", json={"image_paths": []})
        self.assertEqual(response.status_code, 400)

    # ------------------------------------------------------------------
    # Code generator unit tests (no server)
    # ------------------------------------------------------------------
    def test_build_clothes_fx_apply_code_contains_key_tokens(self) -> None:
        items = [{"displayName": "Hat", "parameterName": "Cloth_Hat", "sampleObjectPath": "Avatar/Hat", "animationClipName": "FX_Hat_Toggle"}]
        code = dashboard_server.build_clothes_fx_apply_code("Avatar", items)
        self.assertIn("AnimationClip", code)
        self.assertIn("VRCExpressionParameters", code)
        self.assertIn("VRCExpressionsMenu", code)
        self.assertIn("Cloth_Hat", code)

    def test_build_parameter_apply_optimization_code_contains_key_tokens(self) -> None:
        suggestions = [{"name": "IsWearing"}]
        code = dashboard_server.build_parameter_apply_optimization_code("Avatar", suggestions)
        self.assertIn("IsWearing", code)
        self.assertIn("VRCExpressionParameters.ValueType.Bool", code)
        self.assertIn("changedCount", code)

    def test_build_screenshot_multi_capture_code_contains_rotation(self) -> None:
        from pathlib import Path
        code = dashboard_server.build_screenshot_multi_capture_code(Path("out/front.png"), 15.0, 0.0, 0.0, 960, 960)
        self.assertIn("Quaternion.Euler", code)
        self.assertIn("15.0000f", code)
        self.assertIn("front.png", code)


if __name__ == "__main__":
    unittest.main()
