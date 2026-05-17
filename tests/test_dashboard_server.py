import json
import os
import tempfile
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

from fastapi.testclient import TestClient

import dashboard_server
from vrchat_blendshape_agent import BlendshapeAdjustment, BlendshapePlan


def make_shader_inventory() -> dict:
    return {
        "type": "material_inventory_snapshot",
        "version": "0.2",
        "materials": [
            {
                "material_id": "mat_skin",
                "avatar_name": "HeroAvatar",
                "avatar_path": "Scene/HeroAvatar",
                "item_path": "Scene/HeroAvatar/Body",
                "renderer_id": "renderer_body",
                "renderer_name": "Body",
                "renderer_path": "Scene/HeroAvatar/Body",
                "mesh_name": "BodyMesh",
                "slot_index": 0,
                "material_name": "Face_Skin",
                "shader_name": "lilToon",
                "shader_family": "lilToon",
                "category": "skin",
                "supported_properties": {
                    "base_color": {"type": "color", "value": "#FFD6C8FF", "writable": True},
                    "smoothness": {"type": "float", "value": 0.2, "writable": True},
                    "outline_width": {"type": "float", "value": 0.01, "writable": True},
                },
            },
            {
                "material_id": "mat_unsupported",
                "avatar_name": "HeroAvatar",
                "avatar_path": "Scene/HeroAvatar",
                "material_name": "Legacy",
                "shader_family": "Unsupported",
                "category": "unknown",
                "supported_properties": {},
            },
        ],
        "summary": {"materialCount": 2},
    }


class DashboardServerTests(unittest.TestCase):
    def setUp(self) -> None:
        dashboard_server.DASHBOARD_RUNTIME.manual_undo_stack.clear()
        dashboard_server.DASHBOARD_RUNTIME.current_avatar_path = ""
        dashboard_server.DASHBOARD_RUNTIME.current_avatar_name = ""
        self.tuning_store_dir = tempfile.TemporaryDirectory()
        self.original_tuning_paths = (
            dashboard_server.TUNING_HISTORY_PATH,
            dashboard_server.TUNING_PRESETS_PATH,
            dashboard_server.TUNING_LOCKS_PATH,
            dashboard_server.SHADER_TUNING_HISTORY_PATH,
            dashboard_server.SHADER_TUNING_PRESETS_PATH,
            dashboard_server.SHADER_TUNING_LOCKS_PATH,
        )
        tuning_root = Path(self.tuning_store_dir.name)
        dashboard_server.TUNING_HISTORY_PATH = tuning_root / "tuning_history.json"
        dashboard_server.TUNING_PRESETS_PATH = tuning_root / "tuning_presets.json"
        dashboard_server.TUNING_LOCKS_PATH = tuning_root / "tuning_locks.json"
        dashboard_server.SHADER_TUNING_HISTORY_PATH = tuning_root / "shader_tuning_history.json"
        dashboard_server.SHADER_TUNING_PRESETS_PATH = tuning_root / "shader_tuning_presets.json"
        dashboard_server.SHADER_TUNING_LOCKS_PATH = tuning_root / "shader_tuning_locks.json"
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
        (
            dashboard_server.TUNING_HISTORY_PATH,
            dashboard_server.TUNING_PRESETS_PATH,
            dashboard_server.TUNING_LOCKS_PATH,
            dashboard_server.SHADER_TUNING_HISTORY_PATH,
            dashboard_server.SHADER_TUNING_PRESETS_PATH,
            dashboard_server.SHADER_TUNING_LOCKS_PATH,
        ) = self.original_tuning_paths
        self.tuning_store_dir.cleanup()

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
            self.assertIn("VRCForge 控制台", response.text)
            self.assertIn("识图分析", response.text)
            self.assertIn("原图 / 当前脸", response.text)
            self.assertIn("目标参考图", response.text)
            self.assertIn("粘贴图片", response.text)
            self.assertIn("选择本地图片", response.text)

    def test_phase2_unity_tools_are_registered_without_roslyn(self) -> None:
        editor_dir = Path(__file__).resolve().parents[1] / "Assets" / "VRCAutoRig" / "Editor"
        expected_tools = {
            "GameObjectTools.cs": "vrc_scan_avatar_items",
            "ComponentTools.cs": "vrc_scan_fx_animator",
            "AssetTools.cs": "vrc_scan_animation_bindings",
            "ConsoleTools.cs": "vrc_create_safe_backup",
            "PrefabTools.cs": "vrc_restore_safe_backup",
        }
        phase2_text = []

        for filename, tool_name in expected_tools.items():
            source = (editor_dir / filename).read_text(encoding="utf-8")
            phase2_text.append(source)
            self.assertIn("[McpForUnityTool(", source)
            self.assertIn(f'name: "{tool_name}"', source)
            self.assertIn("public static object HandleCommand(JObject @params)", source)

        combined = "\n".join(phase2_text)
        self.assertNotIn("vrc_execute_roslyn", combined)
        self.assertNotIn("CSharpScript", combined)

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
            self.assertNotIn("recentLogs", payload)
            self.assertEqual(payload["logRetentionHours"], 24)

    def test_recent_log_snapshot_keeps_only_last_24_hours(self) -> None:
        dashboard_server.RECENT_LOGS.clear()
        old_entry = {
            "timestamp": (datetime.now(timezone.utc) - timedelta(hours=25)).isoformat(),
            "level": "info",
            "scope": "test",
            "message": "old",
            "data": {},
        }
        fresh_entry = {
            "timestamp": (datetime.now(timezone.utc) - timedelta(hours=1)).isoformat(),
            "level": "info",
            "scope": "test",
            "message": "fresh",
            "data": {},
        }
        dashboard_server.RECENT_LOGS.extend([old_entry, fresh_entry])

        self.assertEqual(dashboard_server.recent_log_snapshot(), [fresh_entry])

    def test_local_log_file_keeps_only_last_24_hours(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            original_log_path = dashboard_server.LOCAL_LOG_PATH
            dashboard_server.LOCAL_LOG_PATH = Path(temp_dir) / "dashboard.log"
            try:
                old_entry = {
                    "timestamp": (datetime.now(timezone.utc) - timedelta(hours=25)).isoformat(),
                    "message": "old",
                }
                fresh_entry = {
                    "timestamp": datetime.now(timezone.utc).isoformat(),
                    "message": "fresh",
                }
                dashboard_server.LOCAL_LOG_PATH.write_text(
                    json.dumps(old_entry, ensure_ascii=False) + "\n"
                    + json.dumps(fresh_entry, ensure_ascii=False) + "\n",
                    encoding="utf-8",
                )

                dashboard_server.prune_local_log_file()

                lines = dashboard_server.LOCAL_LOG_PATH.read_text(encoding="utf-8").splitlines()
                self.assertEqual(len(lines), 1)
                self.assertEqual(json.loads(lines[0])["message"], "fresh")
            finally:
                dashboard_server.LOCAL_LOG_PATH = original_log_path

    def test_prune_stale_dashboard_log_files_removes_old_sidecar_logs(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            original_artifacts_dir = dashboard_server.DASHBOARD_ARTIFACTS_DIR
            original_log_path = dashboard_server.LOCAL_LOG_PATH
            temp_path = Path(temp_dir)
            dashboard_server.DASHBOARD_ARTIFACTS_DIR = temp_path
            dashboard_server.LOCAL_LOG_PATH = temp_path / "dashboard.log"
            try:
                stale_log = temp_path / "dashboard_stdout.log"
                fresh_log = temp_path / "dashboard_stderr.log"
                current_log = dashboard_server.LOCAL_LOG_PATH
                stale_log.write_text("old", encoding="utf-8")
                fresh_log.write_text("fresh", encoding="utf-8")
                current_log.write_text("current", encoding="utf-8")
                old_time = (datetime.now(timezone.utc) - timedelta(hours=25)).timestamp()
                os.utime(stale_log, (old_time, old_time))

                dashboard_server.prune_stale_dashboard_log_files()

                self.assertFalse(stale_log.exists())
                self.assertTrue(fresh_log.exists())
                self.assertTrue(current_log.exists())
            finally:
                dashboard_server.DASHBOARD_ARTIFACTS_DIR = original_artifacts_dir
                dashboard_server.LOCAL_LOG_PATH = original_log_path

    def test_reference_image_context_supports_optional_source_and_target_images(self) -> None:
        data_url = "data:image/png;base64,aW1hZ2U="
        with tempfile.TemporaryDirectory() as temp_dir:
            original_artifacts_dir = dashboard_server.ARTIFACTS_DIR
            original_dashboard_artifacts_dir = dashboard_server.DASHBOARD_ARTIFACTS_DIR
            temp_artifacts = Path(temp_dir) / "artifacts"
            dashboard_server.ARTIFACTS_DIR = temp_artifacts
            dashboard_server.DASHBOARD_ARTIFACTS_DIR = temp_artifacts / "dashboard"
            try:
                request = dashboard_server.DashboardRequest(
                    instruction="match target",
                    source_reference_image_data_urls=[data_url],
                    target_reference_image_data_urls=[data_url, data_url],
                )

                context = dashboard_server.build_reference_image_context(request)

                self.assertIsNotNone(context)
                self.assertEqual(context["count"], 3)
                self.assertEqual(len(context["imagePaths"]), 3)
                self.assertEqual([group["role"] for group in context["groups"]], ["source", "target"])
                self.assertEqual(len(context["groups"][0]["images"]), 1)
                self.assertEqual(len(context["groups"][1]["images"]), 2)
                self.assertIn("原图", context["imageLabels"][0])
                self.assertIn("目标参考图", context["imageLabels"][1])
            finally:
                dashboard_server.ARTIFACTS_DIR = original_artifacts_dir
                dashboard_server.DASHBOARD_ARTIFACTS_DIR = original_dashboard_artifacts_dir

    def test_extract_tool_result_payload_falls_back_to_flat_stdout(self) -> None:
        result = dashboard_server.McpResult(
            exit_code=0,
            stdout=(
                "objectPath: Avatar/Hood\n"
                "active: False\n"
                "createdCount: 1\n"
                "skipped: [0 items]\n"
                "assetDir: Assets/VRCAutoRig/Generated/FX\n"
                "✅ Executed custom tool: vrc_toggle_scene_object"
            ),
            stderr="",
            payload=[0],
        )

        payload = dashboard_server.extract_tool_result_payload(result)

        self.assertEqual(payload["objectPath"], "Avatar/Hood")
        self.assertFalse(payload["active"])
        self.assertEqual(payload["createdCount"], 1)
        self.assertEqual(payload["skipped"], [])
        self.assertEqual(payload["assetDir"], "Assets/VRCAutoRig/Generated/FX")

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

    @patch("dashboard_server.fetch_provider_models")
    def test_api_models_endpoint_reads_models_from_draft_config(self, mock_fetch_provider_models) -> None:
        mock_fetch_provider_models.return_value = [
            {"id": "gemini-2.5-flash", "label": "gemini-2.5-flash"},
            {"id": "gemini-2.5-pro", "label": "gemini-2.5-pro"},
        ]

        with TestClient(dashboard_server.app) as client:
            response = client.post(
                "/api/models",
                json={
                    "provider": "gemini",
                    "api_key": "draft-secret",
                    "base_url": "https://generativelanguage.googleapis.com/v1beta/openai/",
                    "model": "gemini-2.5-pro",
                },
            )
            self.assertEqual(response.status_code, 200)

            payload = response.json()
            self.assertEqual(payload["provider"], "gemini")
            self.assertEqual(payload["modelCount"], 2)
            self.assertEqual(payload["selectedModel"], "gemini-2.5-pro")
            self.assertEqual(payload["models"][1]["id"], "gemini-2.5-pro")

            config = mock_fetch_provider_models.call_args.args[0]
            self.assertEqual(config.api_key, "draft-secret")
            self.assertEqual(config.model, "gemini-2.5-pro")

    def test_api_models_endpoint_requires_api_key(self) -> None:
        with TestClient(dashboard_server.app) as client:
            response = client.post(
                "/api/models",
                json={
                    "provider": "openai",
                    "api_key": "",
                    "base_url": "https://api.openai.com/v1",
                    "model": "gpt-4.1-mini",
                },
            )
            self.assertEqual(response.status_code, 400)
            self.assertIn("API key is empty", response.json()["detail"])

    @patch("dashboard_server.fetch_openai_compatible_models")
    def test_api_models_endpoint_allows_ollama_without_api_key(self, mock_fetch_openai_compatible_models) -> None:
        mock_fetch_openai_compatible_models.return_value = [{"id": "llama3.2", "label": "llama3.2"}]

        with TestClient(dashboard_server.app) as client:
            response = client.post(
                "/api/models",
                json={
                    "provider": "ollama",
                    "api_key": "",
                    "base_url": "http://127.0.0.1:11434/v1",
                    "model": "llama3.2",
                },
            )

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()["provider"], "ollama")
        self.assertEqual(response.json()["models"][0]["id"], "llama3.2")

    @patch("dashboard_server.export_blendshapes")
    @patch("dashboard_server.load_dashboard_settings")
    def test_scene_avatar_scan_endpoint_returns_vrchat_avatars_from_export(
        self,
        mock_load_settings,
        mock_export_blendshapes,
    ) -> None:
        mock_load_settings.return_value = SimpleNamespace(
            unity_mcp_timeout_seconds=30,
            unity_mcp_host="127.0.0.1",
            unity_mcp_port=8080,
            unity_mcp_instance="",
        )
        mock_export_blendshapes.return_value = {
            "summary": {"avatarCount": 2, "rendererCount": 2, "blendshapeCount": 3},
            "avatars": [
                {
                    "avatarName": "HeroAvatar",
                    "avatarPath": "Scene/HeroAvatar",
                    "sceneName": "AvatarScene",
                    "isVrChatAvatar": True,
                    "renderers": [{"blendshapes": [{"name": "Smile"}, {"name": "Blink"}]}],
                },
                {
                    "avatarName": "PreviewProxy",
                    "avatarPath": "PreviewProxy",
                    "sceneName": "___NDMF Preview___",
                    "isVrChatAvatar": False,
                    "renderers": [{"blendshapes": [{"name": "Proxy"}]}],
                },
            ],
        }

        with TestClient(dashboard_server.app) as client:
            response = client.post("/api/scene/avatars", json={"unity_host": "127.0.0.1", "unity_port": 8080})

        self.assertEqual(response.status_code, 200)
        payload = response.json()
        self.assertEqual(payload["avatarCount"], 1)
        self.assertEqual(payload["avatars"][0]["avatarName"], "HeroAvatar")
        self.assertEqual(payload["avatars"][0]["blendshapeCount"], 2)

    @patch("dashboard_server.load_dashboard_export_payload")
    @patch("dashboard_server.load_dashboard_settings")
    @patch("dashboard_server.invoke_unity_mcp")
    def test_manual_blendshape_apply_uses_direct_unity_tool(
        self,
        mock_invoke_unity_mcp,
        mock_load_settings,
        mock_load_dashboard_export_payload,
    ) -> None:
        mock_load_settings.return_value = SimpleNamespace()
        mock_load_dashboard_export_payload.return_value = (
            {
                "avatars": [
                    {
                        "avatarName": "HeroAvatar",
                        "avatarPath": "Scene/HeroAvatar",
                        "sceneName": "AvatarScene",
                        "renderers": [
                            {
                                "rendererPath": "Scene/HeroAvatar/Face",
                                "blendshapes": [{"name": "Smile", "currentWeight": 10.0}],
                            }
                        ],
                    }
                ]
            },
            "test-export",
            False,
        )
        mock_invoke_unity_mcp.return_value = dashboard_server.McpResult(
            exit_code=0,
            stdout="ok",
            stderr="",
            payload={"ok": True, "appliedCount": 1},
        )

        with TestClient(dashboard_server.app) as client:
            response = client.post(
                "/api/blendshapes/apply",
                json={
                    "source_mode": "unity_live_export",
                    "mock_execute": False,
                    "avatar": "Scene/HeroAvatar",
                    "adjustments": [
                        {
                            "renderer_path": "Scene/HeroAvatar/Face",
                            "blendshape_name": "Smile",
                            "target_weight": 42.0,
                        }
                    ],
                },
            )

        self.assertEqual(response.status_code, 200)
        mock_invoke_unity_mcp.assert_called_once()
        _settings, tool_name, params = mock_invoke_unity_mcp.call_args.args
        self.assertEqual(tool_name, "vrc_apply_blendshapes")
        self.assertEqual(params["avatarPath"], "Scene/HeroAvatar")
        self.assertEqual(params["adjustments"][0]["targetWeight"], 42.0)

    @patch("dashboard_server.capture_blendshape_visual_proof")
    @patch("dashboard_server.verify_live_blendshape_changes")
    @patch("dashboard_server.invoke_unity_mcp")
    @patch("dashboard_server.load_dashboard_settings")
    @patch("dashboard_server.load_dashboard_export_payload")
    @patch("dashboard_server.create_blendshape_plan")
    def test_pipeline_run_live_uses_direct_apply_and_returns_change_preview(
        self,
        mock_create_blendshape_plan,
        mock_load_dashboard_export_payload,
        mock_load_settings,
        mock_invoke_unity_mcp,
        mock_verify_live_blendshape_changes,
        mock_capture_blendshape_visual_proof,
    ) -> None:
        mock_load_settings.return_value = SimpleNamespace(
            llm_provider="gemini",
            llm_model="gemini-test",
            min_confidence=0.65,
        )
        mock_load_dashboard_export_payload.return_value = (
            {
                "avatars": [
                    {
                        "avatarName": "HeroAvatar",
                        "avatarPath": "Scene/HeroAvatar",
                        "sceneName": "AvatarScene",
                        "renderers": [
                            {
                                "rendererPath": "Scene/HeroAvatar/Face",
                                "blendshapes": [{"name": "Smile", "currentWeight": 10.0}],
                            }
                        ],
                    }
                ]
            },
            "test-export",
            False,
        )
        mock_create_blendshape_plan.return_value = BlendshapePlan(
            summary="Make the face rounder.",
            adjustments=[
                BlendshapeAdjustment(
                    avatar_path="Scene/HeroAvatar",
                    renderer_path="Scene/HeroAvatar/Face",
                    blendshape_name="Smile",
                    target_weight=55.0,
                    reason="Smile softens the face.",
                    confidence=0.9,
                )
            ],
        )
        mock_invoke_unity_mcp.return_value = dashboard_server.McpResult(
            exit_code=0,
            stdout="ok",
            stderr="",
            payload={"ok": True, "appliedCount": 1},
        )
        mock_verify_live_blendshape_changes.return_value = [
            {
                "rendererPath": "Scene/HeroAvatar/Face",
                "blendshapeName": "Smile",
                "targetWeight": 55.0,
                "actualWeight": 55.0,
                "verified": True,
                "verificationStatus": "verified",
            }
        ]

        def capture_proof_side_effect(*, stage, current_proof, **_kwargs):
            proof = dict(current_proof or {})
            proof[stage] = {
                "imagePath": f"artifacts/dashboard/latest/blendshape_{stage}.png",
                "imageUrl": f"/artifacts/dashboard/latest/blendshape_{stage}.png",
            }
            return proof

        mock_capture_blendshape_visual_proof.side_effect = capture_proof_side_effect

        with TestClient(dashboard_server.app) as client:
            response = client.post(
                "/api/pipeline/run",
                json={
                    "source_mode": "unity_live_export",
                    "mock_execute": False,
                    "avatar": "Scene/HeroAvatar",
                    "instruction": "把脸变得更圆润一些",
                    "allow_low_confidence": True,
                },
            )

        self.assertEqual(response.status_code, 200)
        payload = response.json()
        self.assertEqual(payload["changePreview"][0]["previousWeight"], 10.0)
        self.assertEqual(payload["changePreview"][0]["targetWeight"], 55.0)
        self.assertTrue(payload["verifiedChanges"][0]["verified"])
        self.assertIn("before", payload["visualProof"])
        self.assertIn("after", payload["visualProof"])
        self.assertEqual(payload["undoDepth"], 1)
        _settings, tool_name, params = mock_invoke_unity_mcp.call_args.args
        self.assertEqual(tool_name, "vrc_apply_blendshapes")
        self.assertEqual(params["adjustments"][0]["blendshapeName"], "Smile")

    @patch("dashboard_server.load_dashboard_settings")
    @patch("dashboard_server.load_dashboard_export_payload")
    @patch("dashboard_server.create_blendshape_plan")
    def test_pipeline_plan_saves_history_and_excludes_locked_blendshapes(
        self,
        mock_create_blendshape_plan,
        mock_load_dashboard_export_payload,
        mock_load_settings,
    ) -> None:
        mock_load_settings.return_value = SimpleNamespace(
            llm_provider="gemini",
            llm_model="gemini-test",
            min_confidence=0.65,
        )
        mock_load_dashboard_export_payload.return_value = (
            {
                "avatars": [
                    {
                        "avatarName": "HeroAvatar",
                        "avatarPath": "Scene/HeroAvatar",
                        "sceneName": "AvatarScene",
                        "renderers": [
                            {
                                "rendererPath": "Scene/HeroAvatar/Face",
                                "blendshapes": [
                                    {"name": "Smile", "currentWeight": 10.0},
                                    {"name": "eye_morph_narrow", "currentWeight": 5.0},
                                ],
                            }
                        ],
                    }
                ]
            },
            "test-export",
            True,
        )
        dashboard_server.save_tuning_store(
            dashboard_server.TUNING_LOCKS_PATH,
            {
                "type": "blendshape_tuning_locks",
                "version": "0.1",
                "avatars": {
                    "Scene/HeroAvatar": [
                        {"rendererPath": "Scene/HeroAvatar/Face", "blendshapeName": "Smile"}
                    ]
                },
            },
        )

        def create_plan_side_effect(_settings, planning_payload, *_args, **_kwargs):
            blendshape_names = [
                blendshape["name"]
                for avatar in planning_payload["avatars"]
                for renderer in avatar["renderers"]
                for blendshape in renderer["blendshapes"]
            ]
            self.assertEqual(blendshape_names, ["eye_morph_narrow"])
            return BlendshapePlan(
                summary="Reroll unlocked eye shape.",
                adjustments=[
                    BlendshapeAdjustment(
                        avatar_path="Scene/HeroAvatar",
                        renderer_path="Scene/HeroAvatar/Face",
                        blendshape_name="eye_morph_narrow",
                        target_weight=30.0,
                        reason="Only unlocked Blendshape is available.",
                        confidence=0.95,
                    )
                ],
            )

        mock_create_blendshape_plan.side_effect = create_plan_side_effect

        with TestClient(dashboard_server.app) as client:
            response = client.post(
                "/api/pipeline/plan",
                json={
                    "source_mode": "unity_live_export",
                    "mock_execute": True,
                    "avatar": "Scene/HeroAvatar",
                    "instruction": "保留嘴巴，只重抽眼睛",
                    "allow_low_confidence": True,
                    "save_artifacts": False,
                },
            )

        self.assertEqual(response.status_code, 200)
        payload = response.json()
        self.assertEqual(payload["changePreview"][0]["blendshapeName"], "eye_morph_narrow")
        self.assertEqual(payload["lockedBlendshapes"][0]["blendshapeName"], "Smile")
        self.assertFalse(payload["historyRecord"]["applied"])
        self.assertEqual(payload["historyRecord"]["changes"][0]["blendshape"], "eye_morph_narrow")

        history = dashboard_server.load_tuning_history_store()
        self.assertEqual(len(history["records"]), 1)
        self.assertEqual(history["records"][0]["locked_blendshapes"][0]["blendshapeName"], "Smile")

    @patch("dashboard_server.load_dashboard_settings")
    @patch("dashboard_server.load_dashboard_export_payload")
    def test_preset_apply_uses_saved_after_values_without_delta_stacking(
        self,
        mock_load_dashboard_export_payload,
        mock_load_settings,
    ) -> None:
        mock_load_settings.return_value = SimpleNamespace()
        mock_load_dashboard_export_payload.return_value = (
            {
                "avatars": [
                    {
                        "avatarName": "HeroAvatar",
                        "avatarPath": "Scene/HeroAvatar",
                        "sceneName": "AvatarScene",
                        "renderers": [
                            {
                                "rendererPath": "Scene/HeroAvatar/Face",
                                "blendshapes": [{"name": "Smile", "currentWeight": 30.0}],
                            }
                        ],
                    }
                ]
            },
            "test-export",
            True,
        )
        dashboard_server.save_tuning_store(
            dashboard_server.TUNING_HISTORY_PATH,
            {
                "type": "blendshape_tuning_history",
                "version": "0.1",
                "records": [
                    {
                        "id": "hist_test",
                        "created_at": "2026-05-16T00:00:00+00:00",
                        "avatar_name": "HeroAvatar",
                        "avatar_path": "Scene/HeroAvatar",
                        "user_prompt": "make a soft smile",
                        "provider": "Gemini",
                        "model": "gemini-test",
                        "reference_image_count": 1,
                        "applied": False,
                        "changes": [
                            {
                                "renderer_path": "Scene/HeroAvatar/Face",
                                "blendshape": "Smile",
                                "before": 10.0,
                                "after": 55.0,
                                "delta": 45.0,
                                "confidence": 0.95,
                            }
                        ],
                        "locked_blendshapes": [],
                    }
                ],
            },
        )

        with TestClient(dashboard_server.app) as client:
            create_response = client.post(
                "/api/tuning/presets",
                json={"history_id": "hist_test", "name": "soft_smile_face", "tags": ["mouth"]},
            )
            self.assertEqual(create_response.status_code, 200)
            preset_id = create_response.json()["preset"]["id"]
            apply_response = client.post(
                f"/api/tuning/presets/{preset_id}/apply",
                json={
                    "source_mode": "unity_live_export",
                    "mock_execute": True,
                    "avatar": "Scene/HeroAvatar",
                },
            )

        self.assertEqual(apply_response.status_code, 200)
        payload = apply_response.json()
        self.assertEqual(payload["appliedAdjustments"][0]["targetWeight"], 55.0)
        self.assertEqual(payload["changePreview"][0]["previousWeight"], 30.0)
        self.assertEqual(payload["changePreview"][0]["delta"], 25.0)
        self.assertEqual(payload["undoDepth"], 1)

    @patch("dashboard_server.load_dashboard_settings")
    @patch("dashboard_server.load_dashboard_export_payload")
    def test_preset_apply_skips_locked_and_missing_blendshapes_without_crash(
        self,
        mock_load_dashboard_export_payload,
        mock_load_settings,
    ) -> None:
        mock_load_settings.return_value = SimpleNamespace()
        mock_load_dashboard_export_payload.return_value = (
            {
                "avatars": [
                    {
                        "avatarName": "HeroAvatar",
                        "avatarPath": "Scene/HeroAvatar",
                        "sceneName": "AvatarScene",
                        "renderers": [
                            {
                                "rendererPath": "Scene/HeroAvatar/Face",
                                "blendshapes": [{"name": "Smile", "currentWeight": 30.0}],
                            }
                        ],
                    }
                ]
            },
            "test-export",
            True,
        )
        dashboard_server.save_tuning_store(
            dashboard_server.TUNING_PRESETS_PATH,
            {
                "type": "blendshape_tuning_presets",
                "version": "0.1",
                "presets": [
                    {
                        "id": "preset_test",
                        "name": "mixed_targets",
                        "avatar_name": "HeroAvatar",
                        "avatar_path": "Scene/HeroAvatar",
                        "apply_mode": "after_values",
                        "changes": [
                            {
                                "renderer_path": "Scene/HeroAvatar/Face",
                                "blendshape": "Smile",
                                "after": 55.0,
                            },
                            {
                                "renderer_path": "Scene/HeroAvatar/Face",
                                "blendshape": "Missing",
                                "after": 80.0,
                            },
                        ],
                    }
                ],
            },
        )
        dashboard_server.save_tuning_store(
            dashboard_server.TUNING_LOCKS_PATH,
            {
                "type": "blendshape_tuning_locks",
                "version": "0.1",
                "avatars": {
                    "Scene/HeroAvatar": [
                        {"rendererPath": "Scene/HeroAvatar/Face", "blendshapeName": "Smile"}
                    ]
                },
            },
        )

        with TestClient(dashboard_server.app) as client:
            response = client.post(
                "/api/tuning/presets/preset_test/apply",
                json={
                    "source_mode": "unity_live_export",
                    "mock_execute": True,
                    "avatar": "Scene/HeroAvatar",
                },
            )

        self.assertEqual(response.status_code, 200)
        payload = response.json()
        self.assertEqual(payload["appliedAdjustments"], [])
        self.assertEqual({item["reason"] for item in payload["skippedAdjustments"]}, {"locked", "missing_blendshape"})
        self.assertEqual(payload["undoDepth"], 0)

    def test_shader_plan_validation_rejects_arbitrary_shader_property_names(self) -> None:
        validation = dashboard_server.validate_shader_material_tuning_plan(
            plan={
                "type": "material_tuning_plan",
                "version": "0.2",
                "changes": [
                    {
                        "material_id": "mat_skin",
                        "shader_property": "_Color",
                        "semantic_property": "_Color",
                        "after": "#FFFFFF",
                    }
                ],
            },
            inventory=make_shader_inventory(),
        )

        self.assertEqual(validation["validatedChanges"], [])
        self.assertEqual(validation["skippedChanges"][0]["validation_status"], "skipped")
        self.assertIn("Real shader property names", validation["skippedChanges"][0]["warning"])

    def test_shader_plan_validation_clamps_and_skips_unsupported_targets(self) -> None:
        validation = dashboard_server.validate_shader_material_tuning_plan(
            plan={
                "type": "material_tuning_plan",
                "version": "0.2",
                "changes": [
                    {"material_id": "mat_skin", "semantic_property": "outline_width", "after": 9.0},
                    {"material_id": "mat_unsupported", "semantic_property": "smoothness", "after": 0.5},
                    {"material_id": "missing", "semantic_property": "smoothness", "after": 0.5},
                ],
            },
            inventory=make_shader_inventory(),
        )

        self.assertEqual(validation["validatedChanges"][0]["after"], 0.25)
        self.assertEqual(len(validation["skippedChanges"]), 2)
        self.assertTrue(any("Unsupported shader family" in item["warning"] for item in validation["skippedChanges"]))
        self.assertTrue(any("Unknown material_id" in item["warning"] for item in validation["skippedChanges"]))

    def test_shader_plan_validation_respects_locked_materials_and_properties(self) -> None:
        validation = dashboard_server.validate_shader_material_tuning_plan(
            plan={
                "type": "material_tuning_plan",
                "version": "0.2",
                "changes": [
                    {"material_id": "mat_skin", "semantic_property": "smoothness", "after": 0.6},
                    {"material_id": "mat_skin", "semantic_property": "base_color", "after": "#FFFFFF"},
                ],
            },
            inventory=make_shader_inventory(),
            locked_materials=set(),
            locked_properties={"mat_skin::smoothness"},
        )

        self.assertEqual([item["semantic_property"] for item in validation["validatedChanges"]], ["base_color"])
        self.assertEqual(validation["skippedChanges"][0]["warning"], "Semantic property is locked: smoothness")

    @patch("dashboard_server.apply_shader_material_tuning_direct")
    @patch("dashboard_server.scan_shader_materials_direct")
    @patch("dashboard_server.load_dashboard_settings")
    def test_shader_preset_apply_uses_saved_after_values(
        self,
        mock_load_settings,
        mock_scan_shader_materials_direct,
        mock_apply_shader_material_tuning_direct,
    ) -> None:
        mock_load_settings.return_value = SimpleNamespace()
        mock_scan_shader_materials_direct.return_value = make_shader_inventory()

        def apply_side_effect(_settings, _avatar_path, changes):
            self.assertEqual(changes[0]["after"], 0.8)
            return {
                "applied": [
                    {
                        "material_id": "mat_skin",
                        "semantic_property": "smoothness",
                        "before": 0.2,
                        "after": 0.8,
                    }
                ],
                "skipped": [],
            }

        mock_apply_shader_material_tuning_direct.side_effect = apply_side_effect
        dashboard_server.save_tuning_store(
            dashboard_server.SHADER_TUNING_PRESETS_PATH,
            {
                "type": "shader_tuning_presets",
                "version": "0.2",
                "presets": [
                    {
                        "id": "shader_preset_test",
                        "name": "soft_skin",
                        "avatar_path": "Scene/HeroAvatar",
                        "apply_mode": "after_values",
                        "changes": [
                            {
                                "material_id": "mat_skin",
                                "material_name": "Face_Skin",
                                "shader_family": "lilToon",
                                "category": "skin",
                                "semantic_property": "smoothness",
                                "before": 0.2,
                                "after": 0.8,
                            }
                        ],
                    }
                ],
            },
        )

        with TestClient(dashboard_server.app) as client:
            response = client.post(
                "/api/shader/presets/shader_preset_test/apply",
                json={"avatar": "Scene/HeroAvatar", "source_mode": "unity_live_export"},
            )

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()["appliedChanges"][0]["after"], 0.8)

    @patch("dashboard_server.invoke_unity_mcp")
    @patch("dashboard_server.load_dashboard_settings")
    def test_clothes_scan_reads_avatar_menu_and_parameters(
        self,
        mock_load_settings,
        mock_invoke_unity_mcp,
    ) -> None:
        mock_load_settings.return_value = SimpleNamespace()
        mock_invoke_unity_mcp.return_value = dashboard_server.McpResult(
            exit_code=0,
            stdout="ok",
            stderr="",
            payload={
                "data": {
                    "items": [
                        {
                            "displayName": "Jacket",
                            "source": "menu_control",
                            "menuPath": "Clothes/Jacket",
                            "parameterName": "Cloth_Jacket",
                            "active": True,
                            "canToggleSceneObject": False,
                        }
                    ]
                }
            },
        )

        with TestClient(dashboard_server.app) as client:
            response = client.post("/api/clothes/scan", json={"avatar_path": "Scene/HeroAvatar"})

        self.assertEqual(response.status_code, 200)
        payload = response.json()
        self.assertEqual(payload["count"], 1)
        self.assertEqual(payload["clothes"][0]["parameterName"], "Cloth_Jacket")
        _settings, tool_name, params = mock_invoke_unity_mcp.call_args.args
        self.assertEqual(tool_name, "vrc_scan_avatar_controls")
        self.assertEqual(params["avatarPath"], "Scene/HeroAvatar")

    @patch("dashboard_server.invoke_unity_mcp")
    @patch("dashboard_server.load_dashboard_settings")
    def test_parameter_scan_uses_direct_unity_tool(
        self,
        mock_load_settings,
        mock_invoke_unity_mcp,
    ) -> None:
        mock_load_settings.return_value = SimpleNamespace()
        mock_invoke_unity_mcp.return_value = dashboard_server.McpResult(
            exit_code=0,
            stdout="ok",
            stderr="",
            payload={"data": {"boolCount": 2, "intCount": 1, "floatCount": 3, "suggestions": []}},
        )

        with TestClient(dashboard_server.app) as client:
            response = client.post("/api/parameters/scan", json={"avatar_path": "Scene/HeroAvatar"})

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()["stats"]["boolCount"], 2)
        _settings, tool_name, _params = mock_invoke_unity_mcp.call_args.args
        self.assertEqual(tool_name, "vrc_scan_avatar_parameters")

    @patch("dashboard_server.invoke_unity_mcp")
    @patch("dashboard_server.load_dashboard_settings")
    def test_vision_capture_uses_direct_scene_view_tool(
        self,
        mock_load_settings,
        mock_invoke_unity_mcp,
    ) -> None:
        mock_load_settings.return_value = SimpleNamespace()
        image_path = str((dashboard_server.ARTIFACTS_DIR / "dashboard" / "latest" / "vision_capture.png").resolve())
        mock_invoke_unity_mcp.return_value = dashboard_server.McpResult(
            exit_code=0,
            stdout="ok",
            stderr="",
            payload={"data": {"imagePath": image_path, "width": 960, "height": 960}},
        )

        with TestClient(dashboard_server.app) as client:
            response = client.post("/api/vision/capture", json={"avatar_path": "Scene/HeroAvatar"})

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()["imageUrl"], "/artifacts/dashboard/latest/vision_capture.png")
        _settings, tool_name, params = mock_invoke_unity_mcp.call_args.args
        self.assertEqual(tool_name, "vrc_capture_scene_view")
        self.assertFalse(params["setRotation"])
        self.assertEqual(params["avatarPath"], "Scene/HeroAvatar")

    @patch("dashboard_server.load_dashboard_export_payload")
    @patch("dashboard_server.load_dashboard_settings")
    def test_avatar_blendshape_list_is_limited_to_face_scope(
        self,
        mock_load_settings,
        mock_load_dashboard_export_payload,
    ) -> None:
        mock_load_settings.return_value = SimpleNamespace()
        mock_load_dashboard_export_payload.return_value = (
            {
                "avatars": [
                    {
                        "avatarName": "HeroAvatar",
                        "avatarPath": "Scene/HeroAvatar",
                        "sceneName": "Scene",
                        "renderers": [
                            {
                                "rendererName": "Body",
                                "rendererPath": "Scene/HeroAvatar/Body",
                                "meshName": "Body",
                                "blendshapes": [
                                    {"name": "Smile", "currentWeight": 0},
                                    {"name": "Breast_big", "currentWeight": 0},
                                ],
                            }
                        ],
                    }
                ]
            },
            "test-export",
            False,
        )

        with TestClient(dashboard_server.app) as client:
            response = client.post(
                "/api/avatar/blendshapes",
                json={
                    "source_mode": "unity_live_export",
                    "mock_execute": False,
                    "avatar": "Scene/HeroAvatar",
                },
            )

        self.assertEqual(response.status_code, 200)
        payload = response.json()
        self.assertEqual([item["blendshapeName"] for item in payload["blendshapes"]], ["Smile"])
        self.assertEqual(payload["filterScope"], "face")

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

    def test_has_unity_mcp_dependency_accepts_utf8_bom_manifest(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            manifest_path = Path(temp_dir) / "manifest.json"
            manifest_path.write_text(
                json.dumps(
                    {
                        "dependencies": {
                            "com.coplaydev.unity-mcp": "https://github.com/CoplayDev/unity-mcp.git?path=/MCPForUnity#main"
                        }
                    }
                ),
                encoding="utf-8-sig",
            )

            self.assertTrue(dashboard_server.has_unity_mcp_dependency(manifest_path))

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

    def test_parameter_rollback_code_accepts_scanner_parameter_names(self) -> None:
        code = dashboard_server.build_parameter_rollback_code(
            "MyAvatar",
            {
                "parameterNames": [
                    {
                        "name": "DPS",
                        "valueType": "Int",
                        "defaultValue": 0.0,
                        "saved": True,
                        "networkSynced": True,
                    }
                ]
            },
        )

        self.assertIn('name = "DPS"', code)
        self.assertIn('valueType = "Int"', code)
        self.assertIn("Enum.TryParse", code)

    def test_apply_parameter_optimization_non_dry_run_saves_snapshot_first(self) -> None:
        suggestions = [
            {"name": "IsWearing", "currentType": "Int", "suggestedType": "Bool", "reason": "heuristic"},
        ]
        original_snapshot_dir = dashboard_server.PARAMETER_SNAPSHOT_DIR
        original_latest_snapshot = dashboard_server.DASHBOARD_RUNTIME.latest_parameter_snapshot_path

        with tempfile.TemporaryDirectory() as temp_dir:
            dashboard_server.PARAMETER_SNAPSHOT_DIR = Path(temp_dir) / "parameter_snapshots"
            dashboard_server.DASHBOARD_RUNTIME.latest_parameter_snapshot_path = ""

            calls: list[str] = []

            def invoke_side_effect(_settings, tool_name, _params):
                calls.append(tool_name)
                if tool_name == "vrc_scan_avatar_parameters":
                    return dashboard_server.McpResult(
                        exit_code=0,
                        stdout="ok",
                        stderr="",
                        payload={
                            "data": {
                                "ok": True,
                                "avatarPath": "MyAvatar",
                                "parameterCount": 1,
                                "parameterNames": [
                                    {
                                        "name": "IsWearing",
                                        "valueType": "Int",
                                        "defaultValue": 0.0,
                                        "saved": False,
                                        "networkSynced": True,
                                    }
                                ],
                            }
                        },
                    )
                return dashboard_server.McpResult(
                    exit_code=0,
                    stdout="ok",
                    stderr="",
                    payload={"data": {"ok": True, "appliedCount": 1}},
                )

            try:
                with patch("dashboard_server.load_dashboard_settings", return_value=SimpleNamespace()), patch(
                    "dashboard_server.invoke_unity_mcp",
                    side_effect=invoke_side_effect,
                ):
                    with TestClient(dashboard_server.app) as client:
                        response = client.post(
                            "/api/parameters/apply-optimization",
                            json={"avatar_path": "MyAvatar", "suggestions": suggestions, "dry_run": False},
                        )

                self.assertEqual(response.status_code, 200)
                payload = response.json()
                self.assertTrue(payload["ok"])
                self.assertFalse(payload["dryRun"])
                self.assertIn("snapshotPath", payload)
                self.assertTrue(Path(payload["snapshotPath"]).exists())
                self.assertEqual(len(calls), 2)
                self.assertEqual(calls[0], "vrc_scan_avatar_parameters")
                self.assertEqual(calls[1], "vrc_apply_parameter_optimization")
            finally:
                dashboard_server.PARAMETER_SNAPSHOT_DIR = original_snapshot_dir
                dashboard_server.DASHBOARD_RUNTIME.latest_parameter_snapshot_path = original_latest_snapshot

    def test_parameter_rollback_restores_explicit_snapshot(self) -> None:
        original_snapshot_dir = dashboard_server.PARAMETER_SNAPSHOT_DIR
        original_latest_snapshot = dashboard_server.DASHBOARD_RUNTIME.latest_parameter_snapshot_path

        with tempfile.TemporaryDirectory() as temp_dir:
            dashboard_server.PARAMETER_SNAPSHOT_DIR = Path(temp_dir) / "parameter_snapshots"
            dashboard_server.PARAMETER_SNAPSHOT_DIR.mkdir(parents=True)
            snapshot_path = dashboard_server.PARAMETER_SNAPSHOT_DIR / "snapshot.json"
            snapshot_path.write_text(
                json.dumps(
                    {
                        "ok": True,
                        "avatarPath": "MyAvatar",
                        "parameterCount": 1,
                        "parameters": [
                            {
                                "name": "IsWearing",
                                "valueType": "Int",
                                "defaultValue": 0.0,
                                "saved": False,
                                "networkSynced": True,
                            }
                        ],
                    }
                ),
                encoding="utf-8",
            )

            try:
                with patch("dashboard_server.load_dashboard_settings", return_value=SimpleNamespace()), patch(
                    "dashboard_server.invoke_unity_mcp",
                    return_value=dashboard_server.McpResult(
                        exit_code=0,
                        stdout="ok",
                        stderr="",
                        payload={"data": {"ok": True, "restoredCount": 1}},
                    ),
                ) as mock_invoke:
                    with TestClient(dashboard_server.app) as client:
                        response = client.post(
                            "/api/parameters/rollback",
                            json={"avatar_path": "MyAvatar", "snapshot_path": str(snapshot_path)},
                        )

                self.assertEqual(response.status_code, 200)
                payload = response.json()
                self.assertTrue(payload["ok"])
                self.assertEqual(payload["restoredCount"], 1)
                _settings, tool_name, params = mock_invoke.call_args.args
                self.assertEqual(tool_name, "vrc_rollback_avatar_parameters")
                self.assertEqual(params["parameterNames"][0]["name"], "IsWearing")
            finally:
                dashboard_server.PARAMETER_SNAPSHOT_DIR = original_snapshot_dir
                dashboard_server.DASHBOARD_RUNTIME.latest_parameter_snapshot_path = original_latest_snapshot

    # ------------------------------------------------------------------
    # /api/vision/capture-multi (needs Unity — verify endpoint exists + 503)
    # ------------------------------------------------------------------
    @patch("dashboard_server.invoke_unity_mcp", side_effect=dashboard_server.UnityMcpError("not connected"))
    @patch("dashboard_server.load_dashboard_settings")
    def test_capture_multi_endpoint_fails_gracefully_when_unity_offline(
        self, mock_load_settings, _mock_invoke
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

    def test_normalize_vision_audit_payload_keeps_position_annotations(self) -> None:
        payload = dashboard_server.normalize_vision_audit_payload(
            {
                "status": "clipping",
                "summary": "Hair clips through hood",
                "issues": [{"summary": "hair/hood intersection"}],
                "annotations": [
                    {
                        "label": "hood edge",
                        "reason": "hair intersects the hood",
                        "severity": "high",
                        "box": {"x": 10, "y": 20, "width": 30, "height": 40},
                    }
                ],
            }
        )
        self.assertEqual(payload["status"], "clipping")
        self.assertEqual(payload["issues"], ["hair/hood intersection"])
        self.assertEqual(payload["annotations"][0]["severity"], "high")
        self.assertAlmostEqual(payload["annotations"][0]["box"]["x"], 0.1)
        self.assertAlmostEqual(payload["annotations"][0]["box"]["width"], 0.3)

    def test_normalize_vision_box_accepts_gemini_1000_scale(self) -> None:
        box = dashboard_server.normalize_vision_box({"x_min": 100, "y_min": 200, "x_max": 500, "y_max": 650})
        self.assertIsNotNone(box)
        self.assertAlmostEqual(box["x"], 0.1)
        self.assertAlmostEqual(box["y"], 0.2)
        self.assertAlmostEqual(box["width"], 0.4)
        self.assertAlmostEqual(box["height"], 0.45)

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

    def test_build_parameter_snapshot_and_rollback_code_contains_key_tokens(self) -> None:
        snapshot_code = dashboard_server.build_parameter_snapshot_code("Avatar")
        self.assertIn("capturedAtUtc", snapshot_code)
        self.assertIn("parameterCount", snapshot_code)

        rollback_code = dashboard_server.build_parameter_rollback_code(
            "Avatar",
            {
                "parameters": [
                    {
                        "name": "IsWearing",
                        "valueType": "Int",
                        "defaultValue": 0.0,
                        "saved": False,
                        "networkSynced": True,
                    }
                ],
            },
        )
        self.assertIn("ParameterSnapshotItem", rollback_code)
        self.assertIn("VRCExpressionParameters.ValueType.Float", rollback_code)
        self.assertIn("IsWearing", rollback_code)

    def test_build_screenshot_multi_capture_code_contains_rotation(self) -> None:
        from pathlib import Path
        code = dashboard_server.build_screenshot_multi_capture_code(Path("out/front.png"), 15.0, 0.0, 0.0, 960, 960)
        self.assertIn("Quaternion.Euler", code)
        self.assertIn("15.0000f", code)
        self.assertIn("front.png", code)

    @patch("dashboard_server.export_blendshapes")
    def test_verify_live_blendshape_changes_reports_actual_weight(self, mock_export_blendshapes) -> None:
        selected_avatar = dashboard_server.SelectedAvatar(
            avatar_name="HeroAvatar",
            avatar_path="Scene/HeroAvatar",
            scene_name="Scene",
            renderer_count=1,
            blendshape_count=1,
        )
        mock_export_blendshapes.return_value = {
            "avatars": [
                {
                    "avatarPath": "Scene/HeroAvatar",
                    "renderers": [
                        {
                            "rendererPath": "Scene/HeroAvatar/Face",
                            "blendshapes": [{"name": "Smile", "currentWeight": 55.0}],
                        }
                    ],
                }
            ]
        }

        verified = dashboard_server.verify_live_blendshape_changes(
            SimpleNamespace(),
            selected_avatar,
            [
                {
                    "rendererPath": "Scene/HeroAvatar/Face",
                    "blendshapeName": "Smile",
                    "targetWeight": 55.0,
                    "previousWeight": 10.0,
                }
            ],
        )

        self.assertTrue(verified[0]["verified"])
        self.assertEqual(verified[0]["actualWeight"], 55.0)
        self.assertEqual(verified[0]["verificationStatus"], "verified")


if __name__ == "__main__":
    unittest.main()
