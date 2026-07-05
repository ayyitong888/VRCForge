import asyncio
import json
import os
import subprocess
import tempfile
import threading
import time
import unittest
import zipfile
from datetime import datetime, timedelta, timezone
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import ANY, patch

from fastapi.testclient import TestClient

import dashboard_server
from agent_gateway import (
    AgentGateway,
    AgentGatewayError,
    CHECKPOINT_ARCHIVE_DEFAULT_MAX_SIZE_MB,
    SHELL_RUNNER_NATIVE,
    SHELL_RUNNER_POWERSHELL,
    native_shell_argv,
    resolve_powershell_executable,
)
from skill_packages import SkillPackageService
from vrchat_blendshape_agent import BlendshapeAdjustment, BlendshapePlan, LlmPlanResponse


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
        self.original_agent_paths = (
            dashboard_server.AGENT_GATEWAY.config_path,
            dashboard_server.AGENT_GATEWAY.audit_dir,
        )
        tuning_root = Path(self.tuning_store_dir.name)
        dashboard_server.TUNING_HISTORY_PATH = tuning_root / "tuning_history.json"
        dashboard_server.TUNING_PRESETS_PATH = tuning_root / "tuning_presets.json"
        dashboard_server.TUNING_LOCKS_PATH = tuning_root / "tuning_locks.json"
        dashboard_server.SHADER_TUNING_HISTORY_PATH = tuning_root / "shader_tuning_history.json"
        dashboard_server.SHADER_TUNING_PRESETS_PATH = tuning_root / "shader_tuning_presets.json"
        dashboard_server.SHADER_TUNING_LOCKS_PATH = tuning_root / "shader_tuning_locks.json"
        dashboard_server.AGENT_GATEWAY.configure_paths(
            tuning_root / "agent_gateway.json",
            tuning_root / "agent_gateway",
        )
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
        dashboard_server.AGENT_GATEWAY.configure_paths(*self.original_agent_paths)
        self.tuning_store_dir.cleanup()

    def test_websocket_sends_bootstrap_payload(self) -> None:
        with TestClient(dashboard_server.app) as client:
            with client.websocket_connect("/ws") as websocket:
                message = websocket.receive_json()
                self.assertEqual(message["type"], "hello")
                self.assertIn("projects", message["payload"])
                self.assertIn("unityStatus", message["payload"])
                self.assertNotIn("api_key", json.dumps(message["payload"]).lower())

    def test_websocket_uses_header_or_cookie_instead_of_query_token(self) -> None:
        original_required = dashboard_server.APP_AUTH_REQUIRED
        original_token = dashboard_server.APP_SESSION_TOKEN
        dashboard_server.APP_AUTH_REQUIRED = True
        dashboard_server.APP_SESSION_TOKEN = "test-app-session-token"
        headers = {"Authorization": "Bearer test-app-session-token"}
        try:
            with TestClient(dashboard_server.app) as client:
                with self.assertRaises(dashboard_server.WebSocketDisconnect):
                    with client.websocket_connect("/ws?ws_ticket=test-app-session-token") as websocket:
                        websocket.receive_json()

                with self.assertRaises(dashboard_server.WebSocketDisconnect):
                    with client.websocket_connect("/ws?app_token=test-app-session-token") as websocket:
                        websocket.receive_json()

                with client.websocket_connect("/ws", headers=headers) as websocket:
                    message = websocket.receive_json()
                    self.assertEqual(message["type"], "hello")

                with client.websocket_connect(
                    "/ws",
                    headers={
                        "Authorization": "Bearer test-app-session-token",
                        "Origin": "http://127.0.0.1:8757",
                    },
                ) as websocket:
                    message = websocket.receive_json()
                    self.assertEqual(message["type"], "hello")

                dashboard_response = client.get("/")
                self.assertEqual(dashboard_response.status_code, 200)
                self.assertIn("httponly", dashboard_response.headers.get("set-cookie", "").lower())
                with client.websocket_connect("/ws") as websocket:
                    message = websocket.receive_json()
                    self.assertEqual(message["type"], "hello")

                with self.assertRaises(dashboard_server.WebSocketDisconnect):
                    with client.websocket_connect(
                        "/ws",
                        headers={"Authorization": "Bearer test-app-session-token", "Origin": "https://example.invalid"},
                    ) as websocket:
                        websocket.receive_json()
        finally:
            dashboard_server.APP_AUTH_REQUIRED = original_required
            dashboard_server.APP_SESSION_TOKEN = original_token

    def test_legacy_dashboard_websocket_avoids_query_tokens(self) -> None:
        app_js = (Path(__file__).resolve().parents[1] / "dashboard" / "app.js").read_text(encoding="utf-8")
        self.assertNotIn("/api/app/ws-ticket", app_js)
        self.assertNotIn("ws_ticket", app_js)
        self.assertIn("new WebSocket(`${scheme}://${window.location.host}/ws`)", app_js)

    def test_chat_transcripts_split_temporary_and_project_storage(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            project = Path(temp_dir) / "AvatarProject"
            project.mkdir()
            chats = [
                {"id": "temp-chat", "projectPath": "", "items": [{"id": "u1", "type": "user", "text": "temporary"}]},
                {"id": "project-chat", "projectPath": str(project), "items": [{"id": "u2", "type": "user", "text": "project"}]},
            ]

            with TestClient(dashboard_server.app) as client:
                response = client.post("/api/app/chats", json={"chats": chats})
                self.assertEqual(response.status_code, 200)
                payload = response.json()
                self.assertEqual(payload["appCount"], 1)
                self.assertEqual(len(payload["projectPaths"]), 1)

                app_path = Path(payload["path"])
                project_path = project / ".vrcforge" / "chat-transcripts.json"
                index_path = app_path.parent / "chat-projects.json"
                self.assertTrue(app_path.is_file())
                self.assertTrue(project_path.is_file())
                self.assertTrue(index_path.is_file())
                self.assertEqual(json.loads(app_path.read_text(encoding="utf-8"))["chats"][0]["id"], "temp-chat")
                self.assertEqual(json.loads(project_path.read_text(encoding="utf-8"))["chats"][0]["id"], "project-chat")
                self.assertIn(str(project.resolve()), json.loads(index_path.read_text(encoding="utf-8"))["projectPaths"])

                read_response = client.get("/api/app/chats", params=[("projectPath", str(project))])
                self.assertEqual(read_response.status_code, 200)
                self.assertEqual({chat["id"] for chat in read_response.json()["chats"]}, {"temp-chat", "project-chat"})

                response = client.post("/api/app/chats", json={"chats": [chats[0]]})
                self.assertEqual(response.status_code, 200)
                self.assertFalse(project_path.exists())
                read_response = client.get("/api/app/chats", params=[("projectPath", str(project))])
                self.assertEqual(read_response.status_code, 200)
                self.assertEqual([chat["id"] for chat in read_response.json()["chats"]], ["temp-chat"])

    def test_project_prefs_accepts_only_unity_project_roots(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            valid = root / "ValidAvatarProject"
            (valid / "Assets").mkdir(parents=True)
            (valid / "Packages").mkdir()
            (valid / "ProjectSettings").mkdir()
            (valid / "ProjectSettings" / "ProjectVersion.txt").write_text(
                "m_EditorVersion: 2022.3.22f1",
                encoding="utf-8",
            )
            plain_dir = root / "Start Menu Shortcut Folder"
            plain_dir.mkdir()

            with TestClient(dashboard_server.app) as client:
                response = client.post(
                    "/api/app/projects/prefs",
                    json={"customPaths": [str(valid), str(plain_dir)], "hiddenPaths": []},
                )

            self.assertEqual(response.status_code, 200)
            payload = response.json()
            self.assertEqual(payload["customPaths"], [str(valid).replace("\\", "/")])

    def test_project_prefs_rejects_parent_directory_without_project_version(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            parent = Path(temp_dir)
            project = parent / "ChildUnityProject"
            (project / "Assets").mkdir(parents=True)
            (project / "Packages").mkdir()
            (project / "ProjectSettings").mkdir()
            (project / "ProjectSettings" / "ProjectVersion.txt").write_text(
                "m_EditorVersion: 2022.3.22f1",
                encoding="utf-8",
            )

            with TestClient(dashboard_server.app) as client:
                response = client.post(
                    "/api/app/projects/prefs",
                    json={"customPaths": [str(parent)], "hiddenPaths": []},
                )

            self.assertEqual(response.status_code, 200)
            self.assertEqual(response.json()["customPaths"], [])

    def test_app_bootstrap_does_not_wait_for_full_health_diagnostics(self) -> None:
        async def idle_status_monitor() -> None:
            await asyncio.sleep(60)

        with (
            patch("dashboard_server.build_full_health_payload", side_effect=AssertionError("bootstrap waited for full health")),
            patch("dashboard_server.build_unity_status_snapshot", side_effect=AssertionError("bootstrap waited for Unity diagnostics")),
            patch("dashboard_server.status_monitor_loop", side_effect=idle_status_monitor),
        ):
            with TestClient(dashboard_server.app) as client:
                response = client.get("/api/app/bootstrap")

        self.assertEqual(response.status_code, 200)
        payload = response.json()
        self.assertTrue(payload["ok"])
        self.assertTrue(payload["health"]["deferredDiagnostics"])
        self.assertEqual(payload["health"]["components"]["backend"]["status"], "ok")
        self.assertIn(payload["health"]["components"]["unityMcpBridgeReachable"]["status"], {"unknown", "warning"})

    def test_app_unity_readiness_refresh_updates_cached_status_without_project_scan(self) -> None:
        async def idle_status_monitor() -> None:
            await asyncio.sleep(60)

        previous_status = dashboard_server.CURRENT_UNITY_STATUS
        previous_fingerprint = dashboard_server.LAST_STATUS_FINGERPRINT
        previous_connected = dashboard_server.LAST_STATUS_CONNECTED
        snapshot = {
            "connected": True,
            "mcpServerReachable": True,
            "unityInstanceRegistered": True,
            "selectedInstanceMatched": True,
            "activeInstanceCount": 1,
            "vrcForgeToolsRegistered": True,
            "missingRequiredVrcForgeTools": [],
            "tools": {"totalTools": 80, "vrcForgeToolsCount": 42},
            "error": "",
        }
        try:
            dashboard_server.CURRENT_UNITY_STATUS = {"connected": False}
            with (
                patch("dashboard_server.build_unity_status_snapshot", return_value=snapshot) as mock_status,
                patch("dashboard_server.status_monitor_loop", side_effect=idle_status_monitor),
                patch("dashboard_server.bootstrap_project_snapshot_payload", side_effect=AssertionError("Unity refresh triggered project scan")),
            ):
                with TestClient(dashboard_server.app) as client:
                    response = client.post("/api/app/unity/readiness/refresh")

            self.assertEqual(response.status_code, 200)
            payload = response.json()
            self.assertEqual(payload["schema"], "vrcforge.unity_readiness_refresh.v1")
            self.assertEqual(payload["unityStatus"], snapshot)
            self.assertEqual(dashboard_server.CURRENT_UNITY_STATUS, snapshot)
            self.assertEqual(payload["health"]["components"]["unityMcpBridgeReachable"]["status"], "ok")
            self.assertEqual(payload["health"]["components"]["vrcForgeUnityTools"]["status"], "ok")
            mock_status.assert_called_once_with()
        finally:
            dashboard_server.CURRENT_UNITY_STATUS = previous_status
            dashboard_server.LAST_STATUS_FINGERPRINT = previous_fingerprint
            dashboard_server.LAST_STATUS_CONNECTED = previous_connected

    def test_app_bootstrap_degrades_when_agent_surfaces_fail(self) -> None:
        with (
            patch.object(dashboard_server.AGENT_GATEWAY, "build_manifest", side_effect=RuntimeError("manifest broken")),
            patch.object(dashboard_server.AGENT_GATEWAY, "build_health", side_effect=RuntimeError("health broken")),
            patch.object(dashboard_server.AGENT_GATEWAY, "permission_state", side_effect=RuntimeError("permission broken")),
            patch.object(dashboard_server.AGENT_GATEWAY, "list_approvals", side_effect=RuntimeError("approvals broken")),
        ):
            with TestClient(dashboard_server.app) as client:
                response = client.get("/api/app/bootstrap")

        self.assertEqual(response.status_code, 200)
        payload = response.json()
        self.assertTrue(payload["ok"])
        self.assertFalse(payload["agentManifest"]["ok"])
        self.assertIn("manifest broken", payload["agentManifest"]["error"])
        self.assertFalse(payload["agentHealth"]["ok"])
        self.assertEqual(payload["permission"]["executionMode"], "approval")
        self.assertEqual(payload["approvals"], [])

    def test_mcp_startup_failure_does_not_block_app_bootstrap(self) -> None:
        with patch("dashboard_server.create_agent_mcp_app", side_effect=RuntimeError("mcp broken")):
            with TestClient(dashboard_server.app) as client:
                response = client.get("/api/app/bootstrap")

        self.assertEqual(response.status_code, 200)
        self.assertTrue(response.json()["ok"])

    def test_startup_schedules_mcp_init_without_waiting_for_it(self) -> None:
        async def slow_mcp_init() -> None:
            await asyncio.sleep(60)

        async def slow_status_loop() -> None:
            await asyncio.sleep(60)

        async def exercise() -> None:
            original_mcp_task = dashboard_server.AGENT_MCP_INIT_TASK
            original_status_task = dashboard_server.STATUS_MONITOR_TASK
            dashboard_server.AGENT_MCP_INIT_TASK = None
            dashboard_server.STATUS_MONITOR_TASK = None
            try:
                with (
                    patch("dashboard_server.initialize_agent_mcp_mount", side_effect=slow_mcp_init),
                    patch("dashboard_server.status_monitor_loop", side_effect=slow_status_loop),
                ):
                    await dashboard_server.on_startup()
                    self.assertIsNotNone(dashboard_server.AGENT_MCP_INIT_TASK)
                    self.assertFalse(dashboard_server.AGENT_MCP_INIT_TASK.done())
            finally:
                for task in (dashboard_server.AGENT_MCP_INIT_TASK, dashboard_server.STATUS_MONITOR_TASK):
                    if task is not None and not task.done():
                        task.cancel()
                        try:
                            await task
                        except asyncio.CancelledError:
                            pass
                dashboard_server.AGENT_MCP_INIT_TASK = original_mcp_task
                dashboard_server.STATUS_MONITOR_TASK = original_status_task

        asyncio.run(exercise())

    def test_app_bootstrap_uses_cached_project_snapshot_without_waiting(self) -> None:
        originals = {
            "cache": dashboard_server.PROJECT_SNAPSHOT_CACHE,
            "refreshing": dashboard_server.PROJECT_SNAPSHOT_REFRESHING,
            "updated": dashboard_server.PROJECT_SNAPSHOT_UPDATED_AT,
            "started": dashboard_server.PROJECT_SNAPSHOT_STARTED_AT,
            "error": dashboard_server.PROJECT_SNAPSHOT_LAST_ERROR,
            "duration": dashboard_server.PROJECT_SNAPSHOT_LAST_DURATION_MS,
            "changes": dashboard_server.PROJECT_SNAPSHOT_LAST_CHANGES,
            "cache_monotonic": dashboard_server.PROJECT_SNAPSHOT_CACHE_MONOTONIC,
            "started_monotonic": dashboard_server.PROJECT_SNAPSHOT_REFRESH_STARTED_MONOTONIC,
            "loaded": dashboard_server.PROJECT_SNAPSHOT_CACHE_LOADED,
        }
        dashboard_server.PROJECT_SNAPSHOT_CACHE = None
        dashboard_server.PROJECT_SNAPSHOT_REFRESHING = False
        dashboard_server.PROJECT_SNAPSHOT_UPDATED_AT = ""
        dashboard_server.PROJECT_SNAPSHOT_STARTED_AT = ""
        dashboard_server.PROJECT_SNAPSHOT_LAST_ERROR = ""
        dashboard_server.PROJECT_SNAPSHOT_LAST_DURATION_MS = 0
        dashboard_server.PROJECT_SNAPSHOT_LAST_CHANGES = {}
        dashboard_server.PROJECT_SNAPSHOT_CACHE_MONOTONIC = 0.0
        dashboard_server.PROJECT_SNAPSHOT_REFRESH_STARTED_MONOTONIC = 0.0
        dashboard_server.PROJECT_SNAPSHOT_CACHE_LOADED = True
        try:
            with (
                patch("dashboard_server.schedule_project_snapshot_refresh", return_value=True) as schedule_refresh,
                patch("dashboard_server.build_project_snapshot_payload", side_effect=AssertionError("bootstrap waited for project discovery")),
                TestClient(dashboard_server.app) as client,
            ):
                normal_response = client.get("/api/app/bootstrap")
                response = client.get("/api/app/bootstrap", params={"refreshProjects": "true"})
                second_response = client.get("/api/app/bootstrap")

            self.assertEqual(normal_response.status_code, 200)
            self.assertEqual(response.status_code, 200)
            self.assertEqual(second_response.status_code, 200)
            payload = response.json()
            projects = payload["health"]["projects"]
            self.assertEqual(projects["projects"], [])
            self.assertIn(projects["scan"]["status"], {"pending", "refreshing"})
            self.assertEqual(schedule_refresh.call_count, 1)
            schedule_refresh.assert_called_once_with(force=True)
        finally:
            dashboard_server.PROJECT_SNAPSHOT_CACHE = originals["cache"]
            dashboard_server.PROJECT_SNAPSHOT_REFRESHING = originals["refreshing"]
            dashboard_server.PROJECT_SNAPSHOT_UPDATED_AT = originals["updated"]
            dashboard_server.PROJECT_SNAPSHOT_STARTED_AT = originals["started"]
            dashboard_server.PROJECT_SNAPSHOT_LAST_ERROR = originals["error"]
            dashboard_server.PROJECT_SNAPSHOT_LAST_DURATION_MS = originals["duration"]
            dashboard_server.PROJECT_SNAPSHOT_LAST_CHANGES = originals["changes"]
            dashboard_server.PROJECT_SNAPSHOT_CACHE_MONOTONIC = originals["cache_monotonic"]
            dashboard_server.PROJECT_SNAPSHOT_REFRESH_STARTED_MONOTONIC = originals["started_monotonic"]
            dashboard_server.PROJECT_SNAPSHOT_CACHE_LOADED = originals["loaded"]

    def test_projects_get_reads_cache_without_scheduling_refresh(self) -> None:
        originals = {
            "cache": dashboard_server.PROJECT_SNAPSHOT_CACHE,
            "refreshing": dashboard_server.PROJECT_SNAPSHOT_REFRESHING,
            "updated": dashboard_server.PROJECT_SNAPSHOT_UPDATED_AT,
            "started": dashboard_server.PROJECT_SNAPSHOT_STARTED_AT,
            "error": dashboard_server.PROJECT_SNAPSHOT_LAST_ERROR,
            "duration": dashboard_server.PROJECT_SNAPSHOT_LAST_DURATION_MS,
            "changes": dashboard_server.PROJECT_SNAPSHOT_LAST_CHANGES,
            "cache_monotonic": dashboard_server.PROJECT_SNAPSHOT_CACHE_MONOTONIC,
            "started_monotonic": dashboard_server.PROJECT_SNAPSHOT_REFRESH_STARTED_MONOTONIC,
            "loaded": dashboard_server.PROJECT_SNAPSHOT_CACHE_LOADED,
        }
        dashboard_server.PROJECT_SNAPSHOT_CACHE = {
            "selectedProjectPath": "",
            "unityEditorPath": "",
            "projects": [{"name": "Cached Project", "path": "", "sources": ["test"]}],
        }
        dashboard_server.PROJECT_SNAPSHOT_REFRESHING = False
        dashboard_server.PROJECT_SNAPSHOT_UPDATED_AT = ""
        dashboard_server.PROJECT_SNAPSHOT_STARTED_AT = ""
        dashboard_server.PROJECT_SNAPSHOT_LAST_ERROR = ""
        dashboard_server.PROJECT_SNAPSHOT_LAST_DURATION_MS = 0
        dashboard_server.PROJECT_SNAPSHOT_LAST_CHANGES = {}
        dashboard_server.PROJECT_SNAPSHOT_CACHE_MONOTONIC = 0.0
        dashboard_server.PROJECT_SNAPSHOT_REFRESH_STARTED_MONOTONIC = 0.0
        dashboard_server.PROJECT_SNAPSHOT_CACHE_LOADED = True
        try:
            with (
                patch("dashboard_server.schedule_project_snapshot_refresh", return_value=True) as schedule_refresh,
                patch("dashboard_server.build_project_snapshot_payload", side_effect=AssertionError("GET /api/projects scanned project roots")),
                TestClient(dashboard_server.app) as client,
            ):
                response = client.get("/api/projects")

            self.assertEqual(response.status_code, 200)
            self.assertEqual(response.json()["projects"][0]["name"], "Cached Project")
            self.assertFalse(schedule_refresh.called)
        finally:
            dashboard_server.PROJECT_SNAPSHOT_CACHE = originals["cache"]
            dashboard_server.PROJECT_SNAPSHOT_REFRESHING = originals["refreshing"]
            dashboard_server.PROJECT_SNAPSHOT_UPDATED_AT = originals["updated"]
            dashboard_server.PROJECT_SNAPSHOT_STARTED_AT = originals["started"]
            dashboard_server.PROJECT_SNAPSHOT_LAST_ERROR = originals["error"]
            dashboard_server.PROJECT_SNAPSHOT_LAST_DURATION_MS = originals["duration"]
            dashboard_server.PROJECT_SNAPSHOT_LAST_CHANGES = originals["changes"]
            dashboard_server.PROJECT_SNAPSHOT_CACHE_MONOTONIC = originals["cache_monotonic"]
            dashboard_server.PROJECT_SNAPSHOT_REFRESH_STARTED_MONOTONIC = originals["started_monotonic"]
            dashboard_server.PROJECT_SNAPSHOT_CACHE_LOADED = originals["loaded"]

    def test_full_health_reads_project_cache_without_scheduling_refresh(self) -> None:
        originals = {
            "cache": dashboard_server.PROJECT_SNAPSHOT_CACHE,
            "refreshing": dashboard_server.PROJECT_SNAPSHOT_REFRESHING,
            "updated": dashboard_server.PROJECT_SNAPSHOT_UPDATED_AT,
            "started": dashboard_server.PROJECT_SNAPSHOT_STARTED_AT,
            "error": dashboard_server.PROJECT_SNAPSHOT_LAST_ERROR,
            "duration": dashboard_server.PROJECT_SNAPSHOT_LAST_DURATION_MS,
            "changes": dashboard_server.PROJECT_SNAPSHOT_LAST_CHANGES,
            "cache_monotonic": dashboard_server.PROJECT_SNAPSHOT_CACHE_MONOTONIC,
            "started_monotonic": dashboard_server.PROJECT_SNAPSHOT_REFRESH_STARTED_MONOTONIC,
            "loaded": dashboard_server.PROJECT_SNAPSHOT_CACHE_LOADED,
        }
        dashboard_server.PROJECT_SNAPSHOT_CACHE = {
            "selectedProjectPath": "",
            "unityEditorPath": "",
            "projects": [{"name": "Cached Project", "path": "", "sources": ["test"]}],
        }
        dashboard_server.PROJECT_SNAPSHOT_REFRESHING = False
        dashboard_server.PROJECT_SNAPSHOT_UPDATED_AT = ""
        dashboard_server.PROJECT_SNAPSHOT_STARTED_AT = ""
        dashboard_server.PROJECT_SNAPSHOT_LAST_ERROR = ""
        dashboard_server.PROJECT_SNAPSHOT_LAST_DURATION_MS = 0
        dashboard_server.PROJECT_SNAPSHOT_LAST_CHANGES = {}
        dashboard_server.PROJECT_SNAPSHOT_CACHE_MONOTONIC = 0.0
        dashboard_server.PROJECT_SNAPSHOT_REFRESH_STARTED_MONOTONIC = 0.0
        dashboard_server.PROJECT_SNAPSHOT_CACHE_LOADED = True
        try:
            with (
                patch("dashboard_server.schedule_project_snapshot_refresh", return_value=True) as schedule_refresh,
                patch("dashboard_server.build_project_snapshot_payload", side_effect=AssertionError("/api/health scanned project roots")),
                TestClient(dashboard_server.app) as client,
            ):
                response = client.get("/api/health")

            self.assertEqual(response.status_code, 200)
            self.assertEqual(response.json()["projects"]["projects"][0]["name"], "Cached Project")
            self.assertFalse(schedule_refresh.called)
        finally:
            dashboard_server.PROJECT_SNAPSHOT_CACHE = originals["cache"]
            dashboard_server.PROJECT_SNAPSHOT_REFRESHING = originals["refreshing"]
            dashboard_server.PROJECT_SNAPSHOT_UPDATED_AT = originals["updated"]
            dashboard_server.PROJECT_SNAPSHOT_STARTED_AT = originals["started"]
            dashboard_server.PROJECT_SNAPSHOT_LAST_ERROR = originals["error"]
            dashboard_server.PROJECT_SNAPSHOT_LAST_DURATION_MS = originals["duration"]
            dashboard_server.PROJECT_SNAPSHOT_LAST_CHANGES = originals["changes"]
            dashboard_server.PROJECT_SNAPSHOT_CACHE_MONOTONIC = originals["cache_monotonic"]
            dashboard_server.PROJECT_SNAPSHOT_REFRESH_STARTED_MONOTONIC = originals["started_monotonic"]
            dashboard_server.PROJECT_SNAPSHOT_CACHE_LOADED = originals["loaded"]

    def test_root_serves_dashboard_page(self) -> None:
        with TestClient(dashboard_server.app) as client:
            response = client.get("/")
            self.assertEqual(response.status_code, 200)
            self.assertIn("VRCForge 控制台", response.text)
            self.assertIn("识图分析", response.text)
            self.assertIn("Gesture Manager Play Mode screenshots", response.text)
            self.assertIn("原图 / 当前脸", response.text)
            self.assertIn("目标参考图", response.text)
            self.assertIn("粘贴图片", response.text)
            self.assertIn("选择本地图片", response.text)

    def test_workspace_diff_summary_reads_git_status(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            subprocess.run(["git", "init"], cwd=root, check=True, capture_output=True, text=True)
            subprocess.run(["git", "config", "user.email", "test@example.com"], cwd=root, check=True, capture_output=True, text=True)
            subprocess.run(["git", "config", "user.name", "VRCForge Test"], cwd=root, check=True, capture_output=True, text=True)
            tracked = root / "tracked.txt"
            tracked.write_text("one\n", encoding="utf-8")
            subprocess.run(["git", "add", "tracked.txt"], cwd=root, check=True, capture_output=True, text=True)
            subprocess.run(["git", "commit", "-m", "initial"], cwd=root, check=True, capture_output=True, text=True)

            tracked.write_text("one\ntwo\n", encoding="utf-8")
            (root / "new.txt").write_text("new\n", encoding="utf-8")

            with TestClient(dashboard_server.app) as client:
                response = client.get("/api/app/workspace/diff", params={"root": str(root)})
                patch_response = client.get("/api/app/workspace/diff", params={"root": str(root), "includePatch": "true"})

        self.assertEqual(response.status_code, 200)
        payload = response.json()
        self.assertTrue(payload["ok"])
        self.assertEqual(payload["schema"], "vrcforge.workspace_diff.v1")
        self.assertEqual(payload["status"], "changed")
        self.assertGreaterEqual(payload["fileCount"], 2)
        self.assertGreaterEqual(payload["additions"], 1)
        self.assertTrue(any("tracked.txt" in item["path"] for item in payload["files"]))
        self.assertTrue(any("new.txt" in item["path"] for item in payload["files"]))
        self.assertEqual(payload.get("patch", ""), "")

        self.assertEqual(patch_response.status_code, 200)
        patch_payload = patch_response.json()
        self.assertIn("tracked.txt", patch_payload.get("patch", ""))
        self.assertFalse(patch_payload.get("patchTruncated", False))

    def test_runtime_snapshot_combines_workspace_and_runtime_ledgers(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            project_root = Path(tmp) / "ProjectA"
            project_root.mkdir()
            with TestClient(dashboard_server.app) as client:
                response = client.get(
                    "/api/app/runtime/snapshot",
                    params={"sessionId": "session-a", "projectRoot": str(project_root), "globalOnly": "false"},
                )

        self.assertEqual(response.status_code, 200)
        payload = response.json()
        self.assertTrue(payload["ok"])
        self.assertEqual(payload["schema"], "vrcforge.desktop_runtime_snapshot.v1")
        self.assertEqual(payload["workspaceDiff"].get("fallbackFromProjectRoot"), str(project_root))
        self.assertIn("approvals", payload)
        self.assertIn("runs", payload)
        self.assertIn("desktopActions", payload)
        self.assertIn("goals", payload)
        self.assertIn("memory", payload)

    def test_runtime_snapshot_without_scope_does_not_return_ledgers(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            gateway = AgentGateway(root / "config" / "agent_gateway.json", root / "audit")
            gateway.record_runtime_queue_event({"clientTurnId": "turn-a", "sessionId": "session-a", "message": "queued"})
            gateway.request_desktop_action({"action": "browser", "sessionId": "session-a", "message": "open"})
            gateway.create_agent_goal({"title": "Scoped goal", "sessionId": "session-a"})
            gateway.create_agent_memory({"scope": "user", "text": "user memory", "kind": "preference"})
            original_gateway = dashboard_server.AGENT_GATEWAY
            try:
                dashboard_server.AGENT_GATEWAY = gateway
                with TestClient(dashboard_server.app) as client:
                    response = client.get("/api/app/runtime/snapshot")
            finally:
                dashboard_server.AGENT_GATEWAY = original_gateway

        self.assertEqual(response.status_code, 200)
        payload = response.json()
        self.assertEqual(payload["runs"]["count"], 0)
        self.assertEqual(payload["desktopActions"]["count"], 0)
        self.assertEqual(payload["goals"]["count"], 0)
        self.assertEqual(payload["memory"]["count"], 0)

    def test_agent_runtime_message_preserves_bounded_attachments(self) -> None:
        with TestClient(dashboard_server.app) as client:
            response = client.post(
                "/api/app/agent/message",
                json={
                    "message": "read the attached note",
                    "attachments": [
                        {
                            "id": "att-1",
                            "name": "note.txt",
                            "type": "text/plain",
                            "size": 11,
                            "text": "hello world",
                            "payloadKind": "text",
                        }
                    ],
                },
            )

        self.assertEqual(response.status_code, 200)
        payload = response.json()
        self.assertEqual(payload["attachments"][0]["name"], "note.txt")
        self.assertEqual(payload["attachments"][0]["payloadKind"], "text")
        self.assertEqual(payload["attachments"][0]["text"], "hello world")
        self.assertTrue(payload["attachments"][0]["replayable"])
        self.assertTrue(payload["attachments"][0]["payloadHash"])

    def test_agent_runtime_message_runs_off_event_loop(self) -> None:
        with patch("dashboard_server.asyncio.to_thread", wraps=dashboard_server.asyncio.to_thread) as to_thread:
            with TestClient(dashboard_server.app) as client:
                response = client.post("/api/app/agent/message", json={"message": "check repository status"})

        self.assertEqual(response.status_code, 200)
        self.assertTrue(
            any(call.args and call.args[0] == dashboard_server.AGENT_GATEWAY.runtime_message for call in to_thread.call_args_list)
        )

    def test_agent_desktop_action_is_explicit_and_audited(self) -> None:
        with patch("dashboard_server.asyncio.to_thread", wraps=dashboard_server.asyncio.to_thread) as to_thread:
            with TestClient(dashboard_server.app) as client:
                response = client.post(
                    "/api/app/agent/desktop-actions",
                    json={
                        "action": "computer_use",
                        "prompt": "diagnose a desktop issue",
                        "sessionId": "sess-test",
                        "clientTurnId": "turn-test",
                        "projectRoot": "ProjectA",
                    },
                )
                listing = client.get("/api/app/agent/desktop-actions", params={"sessionId": "sess-test"})

        self.assertEqual(response.status_code, 200)
        payload = response.json()
        self.assertEqual(payload["status"], "unavailable")
        self.assertIn("Desktop control bridge", payload["error"])
        self.assertEqual(listing.status_code, 200)
        actions = listing.json()["actions"]
        self.assertEqual(actions[0]["action"], "computer_use")
        self.assertEqual(actions[0]["clientTurnId"], "turn-test")
        self.assertTrue(
            any(call.args and call.args[0] == dashboard_server.AGENT_GATEWAY.request_desktop_action for call in to_thread.call_args_list)
        )

    def test_agent_goals_are_durable_and_statused(self) -> None:
        with TestClient(dashboard_server.app) as client:
            created = client.post(
                "/api/app/agent/goals",
                json={"title": "Finish avatar QA", "summary": "Long task", "sessionId": "sess-goal", "projectRoot": "ProjectA"},
            )
            goal_id = created.json()["goal"]["goalId"]
            paused = client.post(f"/api/app/agent/goals/{goal_id}", json={"status": "paused", "summary": "Waiting on user"})
            completed = client.post(f"/api/app/agent/goals/{goal_id}", json={"status": "completed", "summary": "Done"})
            listing = client.get("/api/app/agent/goals", params={"sessionId": "sess-goal"})

        self.assertEqual(created.status_code, 200)
        self.assertEqual(paused.status_code, 200)
        self.assertEqual(completed.status_code, 200)
        goals = listing.json()["goals"]
        self.assertEqual(goals[0]["goalId"], goal_id)
        self.assertEqual(goals[0]["status"], "completed")
        self.assertEqual(goals[0]["title"], "Finish avatar QA")

    def test_agent_memory_can_be_inspected_deleted_and_cleared(self) -> None:
        with TestClient(dashboard_server.app) as client:
            first = client.post(
                "/api/app/agent/memory",
                json={"scope": "project", "kind": "style", "text": "Prefer soft lilToon outlines.", "projectRoot": "ProjectA"},
            )
            second = client.post(
                "/api/app/agent/memory",
                json={"scope": "user", "kind": "preference", "text": "Show approval summaries inline."},
            )
            memory_id = first.json()["memory"]["memoryId"]
            listed = client.get("/api/app/agent/memory", params={"projectRoot": "ProjectA"})
            deleted = client.request("DELETE", f"/api/app/agent/memory/{memory_id}", json={"reason": "test"})
            after_delete = client.get("/api/app/agent/memory", params={"projectRoot": "ProjectA"})
            cleared = client.post("/api/app/agent/memory/clear", json={"scope": "user", "reason": "test"})
            after_clear = client.get("/api/app/agent/memory")

        self.assertEqual(first.status_code, 200)
        self.assertEqual(second.status_code, 200)
        self.assertEqual(listed.status_code, 200)
        self.assertGreaterEqual(listed.json()["count"], 2)
        self.assertEqual(deleted.status_code, 200)
        self.assertTrue(all(item["memoryId"] != memory_id for item in after_delete.json()["memories"]))
        self.assertEqual(cleared.status_code, 200)
        self.assertGreaterEqual(cleared.json()["cleared"], 1)
        self.assertEqual(after_clear.json()["count"], 0)

    def test_agent_project_memory_requires_project_root(self) -> None:
        with TestClient(dashboard_server.app) as client:
            missing_project = client.post(
                "/api/app/agent/memory",
                json={"scope": "project", "kind": "style", "text": "Project-specific preference."},
            )
            user_memory = client.post(
                "/api/app/agent/memory",
                json={"scope": "user", "kind": "preference", "text": "User-wide preference."},
            )

        self.assertEqual(missing_project.status_code, 400)
        self.assertIn("projectRoot", missing_project.json()["detail"])
        self.assertEqual(user_memory.status_code, 200)
        self.assertEqual(user_memory.json()["memory"]["scope"], "user")

    def test_agent_memory_no_project_list_only_returns_user_scope(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            gateway = AgentGateway(root / "config.json", root / "audit")
            gateway.create_agent_memory(
                {"scope": "project", "kind": "note", "text": "Project A private memory.", "projectRoot": str(root / "ProjectA")}
            )
            gateway.create_agent_memory(
                {"scope": "project", "kind": "note", "text": "Project B private memory.", "projectRoot": str(root / "ProjectB")}
            )
            gateway.create_agent_memory({"scope": "user", "kind": "preference", "text": "User-wide memory."})

            no_project = gateway.list_agent_memory(limit=10)
            project_a = gateway.list_agent_memory(limit=10, project_root=str(root / "ProjectA"))

        self.assertEqual([item["text"] for item in no_project["memories"]], ["User-wide memory."])
        self.assertEqual(
            {item["text"] for item in project_a["memories"]},
            {"Project A private memory.", "User-wide memory."},
        )

    def test_agent_project_filters_normalize_windows_style_paths(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            gateway = AgentGateway(root / "config.json", root / "audit")
            project = root / "AvatarProject"
            project.mkdir()
            stored_project = str(project).replace("/", "\\")
            query_project = str(project).replace("\\", "/")
            gateway.record_runtime_queue_event({"clientTurnId": "turn-path", "message": "queued", "projectRoot": stored_project})
            gateway.request_desktop_action({"action": "computer_use", "prompt": "inspect", "projectRoot": stored_project})
            gateway.create_agent_goal({"title": "Path scoped goal", "projectRoot": stored_project})
            gateway.create_agent_memory({"scope": "project", "text": "Path scoped memory", "projectRoot": stored_project})

            runs = gateway.list_runtime_runs(project_root=query_project, limit=10)
            actions = gateway.list_desktop_actions(project_root=query_project, limit=10)
            goals = gateway.list_agent_goals(project_root=query_project, limit=10)
            memories = gateway.list_agent_memory(project_root=query_project, limit=10)

        self.assertEqual(runs["count"], 1)
        self.assertEqual(actions["count"], 1)
        self.assertEqual(goals["count"], 1)
        self.assertEqual({item["text"] for item in memories["memories"]}, {"Path scoped memory"})

    def test_agent_memory_active_state_keeps_entries_before_last_4000_events(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            gateway = AgentGateway(root / "config.json", root / "audit")
            first_id = gateway.create_agent_memory({"scope": "user", "text": "Oldest active memory."})["memory"]["memoryId"]
            for index in range(4001):
                gateway.create_agent_memory({"scope": "user", "text": f"Active memory {index}"})

            active_memory_ids = set(gateway._project_agent_memory())  # noqa: SLF001

        self.assertIn(first_id, active_memory_ids)

    def test_agent_approval_list_filters_by_normalized_project_root(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            gateway = AgentGateway(root / "config.json", root / "audit")
            project_a = root / "ProjectA"
            project_b = root / "ProjectB"
            project_a.mkdir()
            project_b.mkdir()
            gateway.register_write_handler("tool_a", "Tool A", "medium", lambda _args: {"ok": True})
            gateway.register_write_handler("tool_b", "Tool B", "medium", lambda _args: {"ok": True})
            approval_a = gateway.create_apply_request(
                {"target_tool": "tool_a", "arguments": {"projectRoot": str(project_a).replace("/", "\\")}, "reason": "A"}
            )
            gateway.create_apply_request(
                {"target_tool": "tool_b", "arguments": {"projectRoot": str(project_b)}, "reason": "B"}
            )

            filtered = gateway.list_approvals(include_expired=False, project_root=str(project_a).replace("\\", "/"))

        self.assertEqual([item["id"] for item in filtered], [approval_a["approval"]["id"]])

    def test_agent_approval_scope_checks_project_aliases_on_execution(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            gateway = AgentGateway(root / "config.json", root / "audit")
            project_a = root / "ProjectA"
            project_b = root / "ProjectB"
            project_a.mkdir()
            project_b.mkdir()
            gateway.register_write_handler("tool_a", "Tool A", "medium", lambda _args: {"ok": True})
            gateway.register_write_handler("tool_b", "Tool B", "medium", lambda _args: {"ok": True})
            approval_a = gateway.create_apply_request(
                {"target_tool": "tool_a", "arguments": {"projectPath": str(project_a)}, "reason": "A"}
            )["approval"]
            approval_b = gateway.create_apply_request(
                {"target_tool": "tool_b", "arguments": {"project_path": str(project_b)}, "reason": "B"}
            )["approval"]

            project_a_approvals = gateway.list_approvals(include_expired=False, project_root=str(project_a))
            global_approvals = gateway.list_approvals(include_expired=False, global_only=True)

            with self.assertRaises(AgentGatewayError):
                gateway.approve(approval_b["id"], expected_project_root=str(project_a))
            with self.assertRaises(AgentGatewayError):
                gateway.reject(approval_a["id"], global_only=True)

            approved = gateway.approve(approval_a["id"], expected_project_root=str(project_a))

        self.assertEqual([item["id"] for item in project_a_approvals], [approval_a["id"]])
        self.assertEqual(global_approvals, [])
        self.assertTrue(approved["ok"])

    def test_agent_runtime_observe_filters_goals_by_project_root(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            gateway = AgentGateway(root / "config.json", root / "audit")
            project_a = root / "ProjectA"
            project_b = root / "ProjectB"
            project_a.mkdir()
            project_b.mkdir()
            gateway.create_agent_goal({"title": "Goal A", "projectRoot": str(project_a)})
            gateway.create_agent_goal({"title": "Goal B private", "projectRoot": str(project_b)})

            observe = gateway.runtime_observe(project_root=str(project_a))
            titles = {item["title"] for item in observe["goals"]["items"]}

        self.assertEqual(titles, {"Goal A"})

    def test_agent_runtime_context_usage_is_turn_local_for_concurrent_requests(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            gateway = AgentGateway(root / "config.json", root / "audit")

            def fake_llm(prompt: str) -> dict[str, object]:
                if "alpha request" in prompt:
                    time.sleep(0.05)
                    tokens = 111
                    reply = "alpha done"
                else:
                    tokens = 222
                    reply = "beta done"
                return {
                    "text": json.dumps({"action": "reply", "summary": reply, "reply": reply}),
                    "usage": {
                        "exact": True,
                        "inputTokens": tokens,
                        "outputTokens": 1,
                        "totalTokens": tokens + 1,
                        "provider": "test",
                        "model": "model",
                    },
                }

            gateway.llm_plan_fn = fake_llm
            results: dict[str, dict[str, object]] = {}

            def run_turn(name: str, message: str) -> None:
                results[name] = gateway.runtime_message({"message": message, "sessionId": f"sess-{name}"})

            threads = [
                threading.Thread(target=run_turn, args=("alpha", "alpha request")),
                threading.Thread(target=run_turn, args=("beta", "beta request")),
            ]
            for thread in threads:
                thread.start()
            for thread in threads:
                thread.join()

        self.assertEqual(results["alpha"]["contextUsage"]["inputTokens"], 111)
        self.assertEqual(results["beta"]["contextUsage"]["inputTokens"], 222)

    def test_agent_gateway_requires_token_and_is_disabled_by_default(self) -> None:
        config = dashboard_server.AGENT_GATEWAY.ensure_config()
        with TestClient(dashboard_server.app) as client:
            missing_token = client.get("/api/agent/manifest")
            self.assertEqual(missing_token.status_code, 401)

            headers = {"Authorization": f"Bearer {config.token}"}
            manifest = client.get("/api/agent/manifest", headers=headers)
            self.assertEqual(manifest.status_code, 200)
            payload = manifest.json()
            self.assertFalse(payload["enabled"])
            self.assertTrue(payload["requiresToken"])
            self.assertNotIn("vrcforge_request_roslyn_advanced", {tool["name"] for tool in payload["tools"]})

            blocked_tool = client.post("/api/agent/tool/vrcforge_health", headers=headers, json={"params": {}})
            self.assertEqual(blocked_tool.status_code, 403)
            blocked_mcp = client.post("/mcp", json={})
            self.assertEqual(blocked_mcp.status_code, 401)

    def test_agentic_app_bootstrap_is_local_desktop_surface(self) -> None:
        with TestClient(dashboard_server.app) as client:
            response = client.get("/api/app/bootstrap")

        self.assertEqual(response.status_code, 200)
        payload = response.json()
        self.assertTrue(payload["ok"])
        self.assertEqual(payload["app"]["surface"], "tauri-agentic-desktop")
        self.assertFalse(payload["app"]["browserRequired"])
        self.assertEqual(payload["permission"]["executionMode"], "approval")
        self.assertIn("vrcforge_health", {tool["name"] for tool in payload["agentManifest"]["tools"]})
        serialized = json.dumps(payload).lower()
        self.assertNotIn("approval_token", serialized)
        self.assertNotIn("api_key", serialized)

    def test_app_doctor_report_is_read_only_and_redacted(self) -> None:
        config = dashboard_server.AGENT_GATEWAY.ensure_config()
        config.token = "doctor-secret-token"
        config.approval_token = "doctor-approval-secret"
        dashboard_server.AGENT_GATEWAY.save_config(config)
        original_project_path = dashboard_server.DASHBOARD_STATE.selected_project_path
        private_project_path = r"C:\Users\xiao123\PrivateAvatarProjects\DoctorLeakTest"
        dashboard_server.DASHBOARD_STATE.selected_project_path = private_project_path

        try:
            with TestClient(dashboard_server.app) as client:
                response = client.get("/api/app/doctor")
        finally:
            dashboard_server.DASHBOARD_STATE.selected_project_path = original_project_path

        self.assertEqual(response.status_code, 200)
        payload = response.json()
        self.assertEqual(payload["schema"], "vrcforge.doctor.v1")
        self.assertEqual(payload["scope"], "vrcforge.environment.v1")
        self.assertFalse(payload["projectContentInspected"])
        self.assertNotIn("selectedProjectPath", payload)
        self.assertEqual(payload["selectedUnityEnvironment"]["label"], ".../DoctorLeakTest")
        self.assertIn("checks", payload)
        check_ids = {item["id"] for item in payload["checks"]}
        self.assertIn("desktop.runtime", check_ids)
        self.assertIn("backend.online", check_ids)
        self.assertIn("unity.project_root", check_ids)
        self.assertIn("provider.test", check_ids)
        self.assertIn("checkpoint.backend", check_ids)
        self.assertIn("external.security_contract", check_ids)
        self.assertTrue(payload["sections"])
        provider_check = next(item for item in payload["checks"] if item["id"] == "provider.test")
        self.assertEqual(provider_check["section"], "Providers")
        self.assertIn("Settings", provider_check["fixCommand"])
        self.assertFalse(provider_check["fixable"])
        serialized = json.dumps(payload).lower()
        self.assertNotIn("doctor-secret-token", serialized)
        self.assertNotIn("doctor-approval-secret", serialized)
        self.assertNotIn(private_project_path.lower(), serialized)
        self.assertNotIn("privateavatarprojects", serialized)
        self.assertNotIn("approval_token", serialized)
        self.assertNotIn("api_key", serialized)

    def test_app_doctor_degrades_when_diagnostics_fail(self) -> None:
        with patch("dashboard_server.build_app_doctor_report", side_effect=RuntimeError("doctor exploded")):
            with TestClient(dashboard_server.app) as client:
                response = client.get("/api/app/doctor")

        self.assertEqual(response.status_code, 200)
        payload = response.json()
        self.assertFalse(payload["ok"])
        self.assertEqual(payload["schema"], "vrcforge.doctor.v1")
        self.assertEqual(payload["scope"], "vrcforge.environment.v1")
        self.assertFalse(payload["projectContentInspected"])
        check_by_id = {item["id"]: item for item in payload["checks"]}
        self.assertEqual(check_by_id["desktop.runtime"]["status"], "ok")
        self.assertEqual(check_by_id["doctor.degraded"]["status"], "warning")
        self.assertIn("doctor exploded", check_by_id["doctor.degraded"]["message"])

    def test_debug_diagnostics_toggle_records_interaction_log(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            temp_path = Path(temp_dir)
            original_config_path = dashboard_server.DIAGNOSTICS_CONFIG_PATH
            original_interaction_log_path = dashboard_server.INTERACTION_LOG_PATH
            dashboard_server.DIAGNOSTICS_CONFIG_PATH = temp_path / "diagnostics.json"
            dashboard_server.INTERACTION_LOG_PATH = temp_path / "interactions.jsonl"
            try:
                with TestClient(dashboard_server.app) as client:
                    update = client.post("/api/app/diagnostics", json={"debugLogging": True})
                    self.assertEqual(update.status_code, 200)
                    self.assertTrue(update.json()["debugLogging"])

                    bootstrap = client.get("/api/app/bootstrap?app_token=query-secret&artifact_sig=artifact-secret")
                    self.assertEqual(bootstrap.status_code, 200)

                entries = [
                    json.loads(line)
                    for line in dashboard_server.INTERACTION_LOG_PATH.read_text(encoding="utf-8").splitlines()
                    if line.strip()
                ]
                paths = {entry.get("path") for entry in entries}
                self.assertIn("/api/app/diagnostics", paths)
                self.assertIn("/api/app/bootstrap", paths)
                serialized = json.dumps(entries).lower()
                self.assertNotIn("approval_token", serialized)
                self.assertNotIn("api_key", serialized)
                self.assertNotIn("query-secret", serialized)
                self.assertNotIn("artifact-secret", serialized)
            finally:
                dashboard_server.DIAGNOSTICS_CONFIG_PATH = original_config_path
                dashboard_server.INTERACTION_LOG_PATH = original_interaction_log_path

    def test_support_bundle_exports_redacted_diagnostics(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            temp_path = Path(temp_dir)
            original_config_path = dashboard_server.DIAGNOSTICS_CONFIG_PATH
            original_support_bundle_dir = dashboard_server.SUPPORT_BUNDLE_DIR
            original_log_path = dashboard_server.LOCAL_LOG_PATH
            original_interaction_log_path = dashboard_server.INTERACTION_LOG_PATH
            dashboard_server.DIAGNOSTICS_CONFIG_PATH = temp_path / "diagnostics.json"
            dashboard_server.SUPPORT_BUNDLE_DIR = temp_path / "support-bundles"
            dashboard_server.LOCAL_LOG_PATH = temp_path / "dashboard.log"
            dashboard_server.INTERACTION_LOG_PATH = temp_path / "interactions.jsonl"
            private_path = r"C:\Users\xiao123\PrivateAvatarProjects\PaidAvatar"
            try:
                dashboard_server.save_diagnostics_config({"debugLogging": True})
                dashboard_server.LOCAL_LOG_PATH.write_text(
                    json.dumps(
                        {
                            "timestamp": datetime.now(timezone.utc).isoformat(),
                            "level": "error",
                            "scope": "test",
                            "message": "failure",
                            "data": {"api_key": "provider-secret", "projectPath": private_path},
                        },
                        ensure_ascii=False,
                    )
                    + "\n",
                    encoding="utf-8",
                )
                dashboard_server.INTERACTION_LOG_PATH.write_text(
                    json.dumps(
                        {
                            "timestamp": datetime.now(timezone.utc).isoformat(),
                            "path": "/api/test",
                            "authorization": "Bearer secret-token",
                            "query": {"app_token": "query-secret", "artifact_sig": "artifact-secret"},
                            "cwd": private_path,
                        },
                        ensure_ascii=False,
                    )
                    + "\n",
                    encoding="utf-8",
                )

                with TestClient(dashboard_server.app) as client:
                    response = client.post("/api/app/support-bundle", json={"logLimit": 20})

                self.assertEqual(response.status_code, 200)
                payload = response.json()
                self.assertTrue(payload["ok"])
                bundle_path = Path(payload["bundlePath"])
                self.assertTrue(bundle_path.exists())
                with zipfile.ZipFile(bundle_path) as bundle:
                    names = set(bundle.namelist())
                    self.assertIn("metadata.json", names)
                    self.assertIn("dashboard-log.json", names)
                    self.assertIn("interaction-log.json", names)
                    content = "\n".join(bundle.read(name).decode("utf-8") for name in names)
                lowered = content.lower()
                self.assertNotIn("provider-secret", lowered)
                self.assertNotIn("secret-token", lowered)
                self.assertNotIn("query-secret", lowered)
                self.assertNotIn("artifact-secret", lowered)
                self.assertNotIn(private_path.lower(), lowered)
                self.assertNotIn("privateavatarprojects", lowered)
                self.assertIn(".../paidavatar", lowered)
            finally:
                dashboard_server.DIAGNOSTICS_CONFIG_PATH = original_config_path
                dashboard_server.SUPPORT_BUNDLE_DIR = original_support_bundle_dir
                dashboard_server.LOCAL_LOG_PATH = original_log_path
                dashboard_server.INTERACTION_LOG_PATH = original_interaction_log_path

    def test_validation_report_mvp_is_read_only_and_registered(self) -> None:
        with (
            patch(
                "dashboard_server.read_agent_compile_errors",
                return_value={"ok": True, "result": {"exitCode": 0, "stdout": "hasErrors: False\nerrorCount: 0"}},
            ),
            patch("dashboard_server.scan_avatar_parameters_gateway_sync", return_value={"ok": True, "warningCount": 1, "suggestions": [{"id": "compress"}]}),
            patch("dashboard_server.scan_avatar_controls_sync", return_value={"ok": True, "missingReferences": [{"path": "Menu/Missing"}]}),
            patch("dashboard_server.scan_fx_animator_sync", return_value={"ok": True, "parameterTypeMismatches": []}),
            patch("dashboard_server.scan_animation_bindings_sync", return_value={"ok": True, "brokenBindings": [{"clip": "BadClip"}]}),
            patch("dashboard_server.scan_shader_materials_sync", return_value={"ok": True, "summary": {"unsupportedShaderCount": 1}}),
            patch("dashboard_server.scan_wardrobe_sync", return_value={"ok": True, "wardrobeCandidateCount": 1}),
            patch("dashboard_server.scan_avatar_performance_sync", side_effect=[
                {"ok": True, "rank": "Poor"},
                {"ok": True, "rank": "Excellent"},
            ]),
            patch(
                "dashboard_server.validation_dependency_status_sync",
                return_value={
                    "ok": True,
                    "projectConfigured": True,
                    "projectReadable": True,
                    "packages": {
                        "vrchat_sdk": {"installed": True, "packageId": "com.vrchat.avatars", "version": "3.0.0"},
                        "modular_avatar": {"installed": True, "packageId": "nadena.dev.modular-avatar", "version": "1.0.0"},
                        "vrcfury": {"installed": False},
                    },
                },
            ),
            patch(
                "dashboard_server.validation_environment_status_sync",
                return_value={
                    "ok": True,
                    "components": {
                        "unityPluginInstalled": {"status": "ok"},
                        "mcpPackageConfigured": {"status": "ok"},
                        "unityMcpBridgeReachable": {"status": "ok"},
                        "unityMcpInstance": {"status": "ok"},
                        "vrcForgeUnityTools": {"status": "ok"},
                    },
                },
            ),
            patch("dashboard_server.package_manager_status_sync", return_value={"ok": True, "preferredCli": {"name": "vrc-get"}, "managers": [{"name": "vrc-get"}]}),
            patch("dashboard_server.scan_avatar_items_sync", return_value={"ok": True, "itemCount": 4}),
            patch("dashboard_server.scan_generated_asset_residue_sync", return_value={"ok": True, "projectReadable": True, "residueCount": 0}),
        ):
            with TestClient(dashboard_server.app) as client:
                response = client.post("/api/app/validation/report", json={"avatarPath": "Scene/Avatar", "projectPath": r"C:\Private\UnityProject"})

        self.assertEqual(response.status_code, 200)
        payload = response.json()
        self.assertEqual(payload["schema"], "vrcforge.validation.v1")
        self.assertTrue(payload["readOnly"])
        self.assertFalse(payload["autoFix"])
        self.assertTrue(payload["ok"])
        self.assertTrue(payload["rules"]["validationNeverFixes"])
        self.assertGreaterEqual(payload["summary"]["severityCounts"]["Warning"], 4)
        self.assertGreaterEqual(payload["summary"]["severityCounts"]["Suggestion"], 1)
        self.assertIn("sections", payload)
        self.assertIn("findings", payload)
        section_names = {section["name"] for section in payload["sections"]}
        self.assertIn("VRChat SDK", section_names)
        self.assertIn("MCP bridge", section_names)
        self.assertIn("Generated asset residue", section_names)
        self.assertEqual(payload["gate"]["status"], "pass")
        self.assertEqual(payload["summary"]["gateStatus"], "pass")
        self.assertNotIn(r"C:\Private\UnityProject".lower(), json.dumps(payload).lower())

        manifest = dashboard_server.AGENT_GATEWAY.build_manifest()
        tool_names = {tool["name"] for tool in manifest["tools"]}
        write_targets = {target["name"] for target in manifest["writeTargets"]}
        self.assertIn("vrcforge_run_validation_report", tool_names)
        self.assertIn("vrcforge_build_test_readiness", tool_names)
        self.assertNotIn("vrcforge_run_validation_report", write_targets)
        self.assertNotIn("vrcforge_build_test_readiness", write_targets)

    def test_validation_report_records_scanner_failures_as_findings(self) -> None:
        with (
            patch(
                "dashboard_server.read_agent_compile_errors",
                return_value={"ok": True, "result": {"exitCode": 0, "stdout": "hasErrors: False\nerrorCount: 0"}},
            ),
            patch("dashboard_server.scan_avatar_parameters_gateway_sync", side_effect=RuntimeError("parameter scanner down")),
            patch("dashboard_server.scan_avatar_controls_sync", return_value={"ok": True}),
            patch("dashboard_server.scan_fx_animator_sync", return_value={"ok": True}),
            patch("dashboard_server.scan_animation_bindings_sync", return_value={"ok": True}),
            patch("dashboard_server.scan_shader_materials_sync", return_value={"ok": True}),
            patch("dashboard_server.scan_wardrobe_sync", return_value={"ok": True}),
            patch("dashboard_server.scan_avatar_performance_sync", return_value={"ok": True, "rank": "Good"}),
        ):
            with TestClient(dashboard_server.app) as client:
                response = client.post("/api/app/validation/report", json={"includeQuest": False, "includeReadiness": False})

        self.assertEqual(response.status_code, 200)
        payload = response.json()
        self.assertTrue(payload["ok"])
        self.assertEqual(payload["summary"]["failedSourceCount"], 1)
        warnings = [finding for finding in payload["findings"] if finding["severity"] == "Warning"]
        self.assertTrue(any("parameter scanner down" in finding["message"] for finding in warnings))
        self.assertEqual(payload["gate"]["status"], "pass")

    def test_build_test_readiness_is_read_only_gate(self) -> None:
        validation = {
            "ok": False,
            "schema": "vrcforge.validation.v1",
            "summary": {"severityCounts": {"Error": 1, "Warning": 0, "Suggestion": 0, "Info": 2, "Ignored": 0}},
            "sections": [
                {"name": "Unity compile", "status": "error", "findingIds": ["compile.1"], "counts": {"Error": 1}},
                {"name": "VRChat SDK", "status": "info", "findingIds": ["dependencies.2"], "counts": {"Info": 1}},
                {"name": "Selected avatar", "status": "info", "findingIds": ["selected_avatar.3"], "counts": {"Info": 1}},
                {"name": "MCP bridge", "status": "info", "findingIds": [], "counts": {"Info": 1}},
                {"name": "Package manager", "status": "info", "findingIds": [], "counts": {"Info": 1}},
            ],
            "gate": {"enabled": True, "status": "blocked", "blockingFindingIds": ["compile.1"]},
        }
        diagnostics = {
            "ok": True,
            "schema": "vrcforge.package_install_diagnostics.v1",
            "symptoms": [{"code": "compile"}],
            "suggestedFixPlans": [{"id": "explain_compile_errors_request", "title": "Explain compile errors"}],
        }
        with (
            patch("dashboard_server.build_validation_report_sync", return_value=validation),
            patch("dashboard_server.diagnose_package_install_errors_sync", return_value=diagnostics),
        ):
            with TestClient(dashboard_server.app) as client:
                response = client.post("/api/app/build-test/readiness", json={"avatarPath": "Scene/Avatar", "projectPath": r"C:\Private\UnityProject"})

        self.assertEqual(response.status_code, 200)
        payload = response.json()
        self.assertEqual(payload["schema"], "vrcforge.build_test_readiness.v1")
        self.assertTrue(payload["readOnly"])
        self.assertFalse(payload["autoBuild"])
        self.assertFalse(payload["autoPublish"])
        self.assertFalse(payload["ok"])
        self.assertEqual(payload["status"], "blocked")
        self.assertTrue(payload["rules"]["noUnattendedVrchatSdkPublish"])
        self.assertTrue(all(item.get("requiresPreviewApprovalCheckpointValidationRollback") for item in payload["suggestedFixPlans"]))
        self.assertNotIn(r"C:\Private\UnityProject".lower(), json.dumps(payload).lower())

    def test_provider_test_vision_is_explicit_skip_without_project_upload(self) -> None:
        with TestClient(dashboard_server.app) as client:
            response = client.post(
                "/api/app/provider/test",
                json={"provider": "ollama", "api_key": "", "base_url": "http://127.0.0.1:11434/v1", "model": "llama3.2", "capability": "vision"},
            )

        self.assertEqual(response.status_code, 200)
        payload = response.json()
        self.assertTrue(payload["ok"])
        self.assertEqual(payload["status"], "skipped")
        self.assertTrue(payload["skipped"])
        self.assertIn("no Unity screenshot", payload["message"])
        self.assertNotIn("api_key", json.dumps(payload).lower())

    def test_provider_test_structured_uses_probe_without_secret_leak(self) -> None:
        with patch("dashboard_server._run_provider_text_probe", return_value='{"ok":true,"name":"vrcforge"}') as probe:
            with TestClient(dashboard_server.app) as client:
                response = client.post(
                    "/api/app/provider/test",
                    json={"provider": "openai", "api_key": "provider-secret", "base_url": "", "model": "gpt-4.1-mini", "capability": "structured"},
                )

        self.assertEqual(response.status_code, 200)
        payload = response.json()
        self.assertTrue(payload["ok"])
        self.assertEqual(payload["status"], "ok")
        probe.assert_called_once()
        self.assertNotIn("provider-secret", json.dumps(payload))

    def test_read_avatars_sync_reports_execution_mode_without_name_error(self) -> None:
        export_payload = {"summary": {"avatarCount": 1}}
        with (
            patch("dashboard_server.load_dashboard_settings", return_value=SimpleNamespace()),
            patch("dashboard_server.load_dashboard_export_payload", return_value=(export_payload, "unit-test", False)),
            patch("dashboard_server.serialize_avatar_list", return_value=[{"name": "Avatar", "path": "Scene/Avatar"}]),
        ):
            payload = dashboard_server.read_avatars_sync(dashboard_server.DashboardRequest())

        self.assertTrue(payload["ok"])
        self.assertTrue(payload["executed"])
        self.assertEqual(payload["executionMode"], "live-unity")
        self.assertEqual(payload["avatarCount"], 1)

    def test_app_avatars_endpoint_uses_live_avatar_builder(self) -> None:
        export_payload = {"summary": {"avatarCount": 1}}
        seen: dict[str, object] = {}

        def fake_export(_settings: object, request: dashboard_server.DashboardRequest) -> tuple[dict[str, object], str, bool]:
            seen["source_mode"] = request.source_mode
            seen["mock_execute"] = request.mock_execute
            seen["save_artifacts"] = request.save_artifacts
            return export_payload, "unit-test", False

        with (
            patch("dashboard_server.load_dashboard_settings", return_value=SimpleNamespace()),
            patch("dashboard_server.load_dashboard_export_payload", side_effect=fake_export),
            patch(
                "dashboard_server.serialize_avatar_list",
                return_value=[
                    {
                        "avatarName": "Hero",
                        "avatarPath": "Scene/Hero",
                        "sceneName": "Scene",
                        "rendererCount": 3,
                        "blendshapeCount": 8,
                    }
                ],
            ),
            TestClient(dashboard_server.app) as client,
        ):
            response = client.post("/api/app/avatars", json={"projectPath": r"C:\Unity\Hero"})

        self.assertEqual(response.status_code, 200)
        payload = response.json()
        self.assertTrue(payload["ok"])
        self.assertTrue(payload["executed"])
        self.assertEqual(payload["executionMode"], "live-unity")
        self.assertEqual(payload["avatars"][0]["avatarPath"], "Scene/Hero")
        self.assertEqual(payload["avatarCount"], 1)
        self.assertEqual(seen["source_mode"], "unity_live_export")
        self.assertIs(seen["mock_execute"], False)
        self.assertIs(seen["save_artifacts"], True)

    def test_app_auth_validation_checks_loopback_origin_and_token(self) -> None:
        original_required = dashboard_server.APP_AUTH_REQUIRED
        original_token = dashboard_server.APP_SESSION_TOKEN
        dashboard_server.APP_AUTH_REQUIRED = True
        dashboard_server.APP_SESSION_TOKEN = "test-app-session-token"
        try:
            with self.assertRaises(dashboard_server.HTTPException) as non_loopback:
                dashboard_server.validate_app_request_auth("192.0.2.10", "", "test-app-session-token")
            self.assertEqual(non_loopback.exception.status_code, 403)

            with self.assertRaises(dashboard_server.HTTPException) as bad_origin:
                dashboard_server.validate_app_request_auth(
                    "127.0.0.1",
                    "https://example.invalid",
                    "test-app-session-token",
                )
            self.assertEqual(bad_origin.exception.status_code, 403)

            with self.assertRaises(dashboard_server.HTTPException) as bad_token:
                dashboard_server.validate_app_request_auth("127.0.0.1", "", "wrong-token")
            self.assertEqual(bad_token.exception.status_code, 401)

            dashboard_server.validate_app_request_auth("127.0.0.1", "tauri://localhost", "test-app-session-token")
        finally:
            dashboard_server.APP_AUTH_REQUIRED = original_required
            dashboard_server.APP_SESSION_TOKEN = original_token

    def test_source_mode_app_session_handshake_is_local_and_lightweight(self) -> None:
        original_portable = dashboard_server.PORTABLE_MODE
        dashboard_server.PORTABLE_MODE = False
        try:
            with TestClient(dashboard_server.app) as client:
                missing_origin = client.get("/api/app/session")
                bad_origin = client.get("/api/app/session", headers={"Origin": "https://example.invalid"})
                dev_session = client.get("/api/app/session", headers={"Origin": "http://127.0.0.1:1420"})
                challenge = client.get(
                    "/api/app/session-challenge",
                    params={"nonce": "startup-nonce-1"},
                    headers={"Origin": "tauri://localhost"},
                )

            self.assertEqual(missing_origin.status_code, 403)
            self.assertEqual(bad_origin.status_code, 403)
            self.assertEqual(dev_session.status_code, 200)
            self.assertGreaterEqual(len(dev_session.json()["appSessionToken"]), 32)
            self.assertEqual(challenge.status_code, 200)
            self.assertEqual(
                challenge.json()["signature"],
                dashboard_server.app_session_challenge_signature("startup-nonce-1"),
            )
            self.assertNotIn("appSessionToken", challenge.json())
        finally:
            dashboard_server.PORTABLE_MODE = original_portable

    def test_app_cors_preflight_is_not_blocked_by_session_auth(self) -> None:
        original_required = dashboard_server.APP_AUTH_REQUIRED
        original_token = dashboard_server.APP_SESSION_TOKEN
        dashboard_server.APP_AUTH_REQUIRED = True
        dashboard_server.APP_SESSION_TOKEN = "test-app-session-token"
        try:
            with TestClient(dashboard_server.app) as client:
                preflight = client.options(
                    "/api/app/bootstrap",
                    headers={
                        "Origin": "tauri://localhost",
                        "Access-Control-Request-Method": "GET",
                        "Access-Control-Request-Headers": "authorization",
                    },
                )
                missing_token_get = client.get("/api/app/bootstrap", headers={"Origin": "tauri://localhost"})

            self.assertEqual(preflight.status_code, 200)
            self.assertEqual(preflight.headers.get("access-control-allow-origin"), "tauri://localhost")
            self.assertIn("authorization", preflight.headers.get("access-control-allow-headers", "").lower())
            self.assertEqual(missing_token_get.status_code, 401)
        finally:
            dashboard_server.APP_AUTH_REQUIRED = original_required
            dashboard_server.APP_SESSION_TOKEN = original_token

    def test_app_auth_covers_legacy_api_routes_but_keeps_health_public(self) -> None:
        original_required = dashboard_server.APP_AUTH_REQUIRED
        original_token = dashboard_server.APP_SESSION_TOKEN
        dashboard_server.APP_AUTH_REQUIRED = True
        dashboard_server.APP_SESSION_TOKEN = "test-app-session-token"
        headers = {"Authorization": "Bearer test-app-session-token"}
        try:
            with TestClient(dashboard_server.app) as client:
                health = client.get("/api/health")
                authorized_health = client.get("/api/health", headers=headers)
                missing_token_get = client.get("/api/projects")
                query_token_get = client.get("/api/projects?app_token=test-app-session-token")
                missing_token_post = client.post("/api/projects/install", json={})
                authorized_get = client.get("/api/projects", headers=headers)

            self.assertEqual(health.status_code, 200)
            health_payload = health.json()
            self.assertEqual(health_payload["schema"], "vrcforge.public_health.v1")
            self.assertNotIn("paths", health_payload)
            self.assertNotIn("projects", health_payload)
            self.assertNotIn("components", health_payload)
            self.assertNotIn("configPath", health_payload)
            self.assertEqual(authorized_health.status_code, 200)
            self.assertIn("components", authorized_health.json())
            self.assertEqual(missing_token_get.status_code, 401)
            self.assertEqual(query_token_get.status_code, 401)
            self.assertEqual(missing_token_post.status_code, 401)
            self.assertEqual(authorized_get.status_code, 200)
        finally:
            dashboard_server.APP_AUTH_REQUIRED = original_required
            dashboard_server.APP_SESSION_TOKEN = original_token

    def test_artifact_urls_require_scoped_signature(self) -> None:
        original_required = dashboard_server.APP_AUTH_REQUIRED
        original_token = dashboard_server.APP_SESSION_TOKEN
        dashboard_server.APP_AUTH_REQUIRED = True
        dashboard_server.APP_SESSION_TOKEN = "test-app-session-token"
        artifact_path = dashboard_server.DASHBOARD_ARTIFACTS_DIR / "latest" / "signed-artifact-test.txt"
        runtime_path = dashboard_server.ARTIFACTS_DIR / "optimizer-apply-smoke" / "runtime-signed-artifact-test.txt"
        artifact_path.parent.mkdir(parents=True, exist_ok=True)
        artifact_path.write_text("artifact-ok", encoding="utf-8")
        try:
            signed_url = dashboard_server.to_artifact_url(str(artifact_path))
            repeated_url = dashboard_server.to_artifact_url(str(artifact_path))
            self.assertTrue(signed_url.startswith("/artifacts/latest/signed-artifact-test.txt?"))
            self.assertIn("artifact_sig=", signed_url)
            self.assertIn("artifact_v=", signed_url)
            self.assertNotIn("app_token=", signed_url)
            self.assertEqual(repeated_url, signed_url)

            with TestClient(dashboard_server.app) as client:
                unsigned = client.get("/artifacts/latest/signed-artifact-test.txt")
                signed = client.get(signed_url)

            self.assertEqual(unsigned.status_code, 401)
            self.assertEqual(signed.status_code, 200)
            self.assertEqual(signed.text, "artifact-ok")

            runtime_path.parent.mkdir(parents=True, exist_ok=True)
            runtime_path.write_text("runtime-artifact-ok", encoding="utf-8")
            runtime_url = dashboard_server.to_runtime_artifact_url(str(runtime_path))
            self.assertTrue(runtime_url.startswith("/runtime-artifacts/optimizer-apply-smoke/runtime-signed-artifact-test.txt?"))
            self.assertIn("artifact_sig=", runtime_url)
            with TestClient(dashboard_server.app) as client:
                runtime_unsigned = client.get("/runtime-artifacts/optimizer-apply-smoke/runtime-signed-artifact-test.txt")
                runtime_signed = client.get(runtime_url)
            self.assertEqual(runtime_unsigned.status_code, 401)
            self.assertEqual(runtime_signed.status_code, 200)
            self.assertEqual(runtime_signed.text, "runtime-artifact-ok")
        finally:
            artifact_path.unlink(missing_ok=True)
            runtime_path.unlink(missing_ok=True)
            dashboard_server.APP_AUTH_REQUIRED = original_required
            dashboard_server.APP_SESSION_TOKEN = original_token

    def test_packaged_backend_exe_resolves_payload_root(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            payload_root = Path(tmp) / "VRCForge_Windows_x64"
            backend_exe = payload_root / "backend" / "vrcforge_backend.exe"
            with (
                patch.object(dashboard_server.sys, "frozen", True, create=True),
                patch.object(dashboard_server.sys, "executable", str(backend_exe)),
            ):
                self.assertEqual(dashboard_server.default_runtime_root(), payload_root.resolve())

    def test_packaged_backend_defaults_to_user_data_root(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            with patch.dict(os.environ, {"LOCALAPPDATA": tmp}):
                expected = Path(tmp) / "VRCForge" / "agentic-app"
                self.assertEqual(dashboard_server.default_user_data_root(), expected)

    def test_agentic_permission_full_auto_does_not_wait_for_unity_acknowledgement(self) -> None:
        with TestClient(dashboard_server.app) as client:
            blocked = client.post("/api/app/permission", json={"execution_mode": "roslyn_full_auto"})
            self.assertEqual(blocked.status_code, 200)
            self.assertEqual(blocked.json()["permission"]["executionMode"], "roslyn_full_auto")
            self.assertTrue(blocked.json()["permission"]["fullPermission"])
            self.assertFalse(blocked.json()["permission"]["allowRoslynAdvanced"])
            self.assertNotIn("unityAcknowledgement", blocked.json())

            enabled = client.post(
                "/api/app/permission",
                json={
                    "execution_mode": "roslyn_full_auto",
                    "acknowledge_roslyn_risk": True,
                },
            )
            self.assertEqual(enabled.status_code, 200)
            permission = enabled.json()["permission"]
            self.assertEqual(permission["executionMode"], "roslyn_full_auto")
            self.assertTrue(permission["roslynRiskAcknowledged"])
            self.assertFalse(permission["allowRoslynAdvanced"])
            self.assertTrue(enabled.json()["permission"]["fullPermission"])
            self.assertNotIn("unityAcknowledgement", enabled.json())

            approval = client.post("/api/app/permission", json={"execution_mode": "approval"})
            self.assertEqual(approval.status_code, 200)
            self.assertEqual(approval.json()["permission"]["executionMode"], "approval")
            self.assertTrue(approval.json()["permission"]["roslynRiskAcknowledged"])

            restored = client.post("/api/app/permission", json={"execution_mode": "roslyn_full_auto"})
            self.assertEqual(restored.status_code, 200)
            self.assertEqual(restored.json()["permission"]["executionMode"], "roslyn_full_auto")
            self.assertTrue(restored.json()["permission"]["roslynRiskAcknowledged"])
            self.assertNotIn("unityAcknowledgement", restored.json())

    def test_auto_permission_shell_delete_and_outside_read_require_manual_until_full_auto(self) -> None:
        def ps_quote(path: Path) -> str:
            return "'" + str(path).replace("'", "''") + "'"

        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            workspace = root / "workspace"
            workspace.mkdir()
            outside = root / "outside.txt"
            outside.write_text("outside-ok", encoding="utf-8")
            victim = workspace / "victim.txt"
            victim.write_text("delete-me", encoding="utf-8")
            gateway = AgentGateway(root / "config" / "agent_gateway.json", root / "audit")

            config = gateway.ensure_config()
            config.enabled = True
            config.execution_mode = "auto"
            gateway.save_config(config)

            outside_read = gateway.execute_shell(
                {
                    "command": f"Get-Content -LiteralPath {ps_quote(outside)}",
                    "workspace_root": str(workspace),
                    "cwd": str(workspace),
                    "timeout_seconds": 5,
                }
            )
            self.assertEqual(outside_read["status"], "pending_approval")
            self.assertTrue(outside_read["approval"]["requiresExplicitApproval"])
            self.assertIn("outside", outside_read["approval"]["explicitApprovalReason"].lower())

            delete_request = gateway.execute_shell(
                {
                    "command": f"Remove-Item -LiteralPath {ps_quote(victim)}",
                    "workspace_root": str(workspace),
                    "cwd": str(workspace),
                    "timeout_seconds": 5,
                }
            )
            self.assertEqual(delete_request["status"], "pending_approval")
            self.assertTrue(delete_request["approval"]["requiresExplicitApproval"])
            self.assertTrue(victim.exists())

            config = gateway.ensure_config()
            config.execution_mode = "roslyn_full_auto"
            config.roslyn_risk_acknowledged = True
            config.allow_roslyn_advanced = True
            gateway.save_config(config)

            full_read = gateway.execute_shell(
                {
                    "command": f"Get-Content -LiteralPath {ps_quote(outside)}",
                    "workspace_root": str(workspace),
                    "cwd": str(workspace),
                    "timeout_seconds": 5,
                }
            )
            self.assertEqual(full_read["status"], "executed")
            self.assertIn("outside-ok", full_read["result"]["stdout"])

            full_delete = gateway.execute_shell(
                {
                    "command": f"Remove-Item -LiteralPath {ps_quote(victim)}",
                    "workspace_root": str(workspace),
                    "cwd": str(workspace),
                    "timeout_seconds": 5,
                }
            )
            self.assertEqual(full_delete["status"], "executed")
            self.assertFalse(victim.exists())

    def test_auto_permission_delete_write_requires_manual_until_full_auto(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            project = root / "UnityProject"
            (project / "Assets").mkdir(parents=True)
            (project / "Packages").mkdir()
            (project / "ProjectSettings").mkdir()
            (project / "Assets" / "target.txt").write_text("delete-me", encoding="utf-8")
            (project / "Packages" / "manifest.json").write_text("{}", encoding="utf-8")
            (project / "ProjectSettings" / "ProjectVersion.txt").write_text("m_EditorVersion: 2022.3", encoding="utf-8")
            gateway = AgentGateway(root / "config" / "agent_gateway.json", root / "audit")
            gateway.checkpoint_prepare_handler = lambda _root: {"ok": True}

            def delete_handler(args: dict) -> dict:
                target = Path(args["assetPath"])
                target.unlink(missing_ok=True)
                return {"ok": True, "deleted": target.name}

            gateway.register_write_handler("vrcforge_test_delete_asset", "Delete test asset.", "high", delete_handler)
            config = gateway.ensure_config()
            config.enabled = True
            config.execution_mode = "auto"
            gateway.save_config(config)

            auto_request = gateway.create_apply_request(
                {
                    "target_tool": "vrcforge_test_delete_asset",
                    "arguments": {
                        "projectRoot": str(project),
                        "assetPath": str(project / "Assets" / "target.txt"),
                        "delete": True,
                    },
                }
            )
            self.assertEqual(auto_request["status"], "pending")
            self.assertTrue(auto_request["approval"]["requiresExplicitApproval"])
            self.assertTrue((project / "Assets" / "target.txt").exists())

            config = gateway.ensure_config()
            config.execution_mode = "roslyn_full_auto"
            config.roslyn_risk_acknowledged = True
            config.allow_roslyn_advanced = True
            gateway.save_config(config)
            full_request = gateway.create_apply_request(
                {
                    "target_tool": "vrcforge_test_delete_asset",
                    "arguments": {
                        "projectRoot": str(project),
                        "assetPath": str(project / "Assets" / "target.txt"),
                        "delete": True,
                    },
                }
            )
            self.assertEqual(full_request["status"], "executed")
            self.assertTrue(full_request["autoApproved"])
            self.assertTrue(full_request["fullPermission"])
            self.assertEqual(full_request["permissionLabel"], "full permission")
            self.assertTrue(full_request["approval"]["fullPermission"])
            self.assertEqual(full_request["approval"]["permissionMode"], "roslyn_full_auto")
            self.assertFalse((project / "Assets" / "target.txt").exists())
            audit_logs = gateway.recent_audit_logs(limit=30)
            self.assertTrue(
                any(
                    event.get("event") == "approval_auto_approved"
                    and event.get("fullPermission") is True
                    and event.get("permissionLabel") == "full permission"
                    and event.get("targetTool") == "vrcforge_test_delete_asset"
                    for event in audit_logs
                )
            )
            runtime_events = gateway.list_runtime_runs(limit=30)["events"]
            self.assertTrue(
                any(
                    event.get("event") == "approval_applied"
                    and event.get("fullPermission") is True
                    and event.get("permissionLabel") == "full permission"
                    for event in runtime_events
                )
            )

    def test_agent_runtime_message_observes_and_plans_without_unity(self) -> None:
        with TestClient(dashboard_server.app) as client:
            response = client.post("/api/app/agent/message", json={"message": "检查仓库状态"})

        self.assertEqual(response.status_code, 200)
        payload = response.json()
        self.assertTrue(payload["ok"])
        self.assertTrue(payload["observe"]["ok"])
        self.assertEqual(payload["plan"]["planner"], "deterministic-local")
        self.assertIn("session_id", payload)
        self.assertIn("turn_id", payload)

    def test_agent_runtime_run_ledger_records_message_turn(self) -> None:
        with TestClient(dashboard_server.app) as client:
            response = client.post(
                "/api/app/agent/message",
                json={
                    "message": "hello ledger",
                    "clientTurnId": "client-turn-1",
                    "provider": "deepseek",
                    "providerLabel": "DeepSeek",
                    "model": "deepseek-v4-pro",
                    "projectRoot": "D:/AvatarProject",
                },
            )
            self.assertEqual(response.status_code, 200)
            turn_payload = response.json()

            dashboard_server.AGENT_GATEWAY._runtime_sessions.clear()
            ledger_response = client.get(
                "/api/app/agent/runs",
                params={"sessionId": turn_payload["sessionId"], "limit": "10"},
            )

        self.assertEqual(ledger_response.status_code, 200)
        ledger = ledger_response.json()
        self.assertTrue(ledger["ok"])
        runs = ledger["runs"]
        self.assertGreaterEqual(len(runs), 1)
        run = next(item for item in runs if item.get("turnId") == turn_payload["turnId"])
        self.assertEqual(run["status"], "completed")
        self.assertEqual(run["clientTurnId"], "client-turn-1")
        self.assertEqual(run["providerLabel"], "DeepSeek")
        self.assertEqual(run["model"], "deepseek-v4-pro")
        self.assertIn("stepCount", run)

    def test_agent_runtime_cancel_records_request(self) -> None:
        with TestClient(dashboard_server.app) as client:
            response = client.post(
                "/api/app/agent/runs/cancel",
                json={"sessionId": "sess-cancel", "clientTurnId": "client-cancel-1", "reason": "user_stop"},
            )
            self.assertEqual(response.status_code, 200)
            self.assertEqual(response.json()["status"], "cancel_requested")

            ledger_response = client.get(
                "/api/app/agent/runs",
                params={"sessionId": "sess-cancel", "clientTurnId": "client-cancel-1"},
            )

        self.assertEqual(ledger_response.status_code, 200)
        runs = ledger_response.json()["runs"]
        self.assertEqual(len(runs), 1)
        self.assertEqual(runs[0]["status"], "cancel_requested")
        self.assertEqual(runs[0]["clientTurnId"], "client-cancel-1")

    def test_agent_runtime_cancel_by_session_is_observed_by_turn(self) -> None:
        session_id = "sess-cancel-observed"
        dashboard_server.AGENT_GATEWAY.request_runtime_cancel({"session_id": session_id, "reason": "user_stop"})
        try:
            payload = dashboard_server.AGENT_GATEWAY.runtime_message(
                {"message": "hello after cancel", "session_id": session_id},
            )
        finally:
            dashboard_server.AGENT_GATEWAY._cancelled_runtime_turns.discard(session_id)

        self.assertEqual(payload["plan"]["nextStep"], "cancelled")

    def test_agent_runtime_queue_records_request(self) -> None:
        with TestClient(dashboard_server.app) as client:
            response = client.post(
                "/api/app/agent/runs/queue",
                json={
                    "sessionId": "sess-queue",
                    "clientTurnId": "client-queue-1",
                    "message": "queued follow-up",
                    "providerLabel": "DeepSeek",
                    "model": "deepseek-v4-pro",
                },
            )
            self.assertEqual(response.status_code, 200)
            self.assertEqual(response.json()["status"], "queued")

            ledger_response = client.get(
                "/api/app/agent/runs",
                params={"sessionId": "sess-queue", "clientTurnId": "client-queue-1"},
            )

        self.assertEqual(ledger_response.status_code, 200)
        runs = ledger_response.json()["runs"]
        self.assertEqual(len(runs), 1)
        self.assertEqual(runs[0]["status"], "queued")
        self.assertEqual(runs[0]["messageSummary"], "queued follow-up")
        self.assertEqual(runs[0]["providerLabel"], "DeepSeek")

    @patch("dashboard_server.request_llm_plan_with_metadata")
    @patch("dashboard_server.load_dashboard_settings")
    def test_agent_runtime_message_includes_provider_reasoning_trace(
        self,
        mock_load_settings,
        mock_request_llm_plan,
    ) -> None:
        mock_load_settings.return_value = SimpleNamespace(
            llm_provider="ollama",
            llm_api_key="",
            llm_model="qwen3",
        )
        mock_request_llm_plan.return_value = LlmPlanResponse(
            text=json.dumps({"action": "reply", "reply": "ready"}),
            reasoning={
                "schema": "vrcforge.llm_reasoning.v1",
                "provider": "ollama",
                "providerLabel": "Ollama",
                "model": "qwen3",
                "collapsedDefault": True,
                "itemCount": 1,
                "items": [{"title": "thinking", "kind": "thinking", "text": "visible model thinking"}],
            },
        )

        with TestClient(dashboard_server.app) as client:
            response = client.post("/api/app/agent/message", json={"message": "hello model planner"})

        self.assertEqual(response.status_code, 200)
        payload = response.json()
        self.assertEqual(payload["plan"]["planner"], "llm")
        self.assertEqual(payload["reasoning"]["provider"], "ollama")
        self.assertTrue(payload["reasoning"]["collapsedDefault"])
        self.assertEqual(payload["reasoning"]["items"][0]["text"], "visible model thinking")

    @patch("dashboard_server.request_llm_plan_with_metadata")
    @patch("dashboard_server.load_dashboard_settings")
    def test_agent_runtime_message_reports_provider_context_usage(
        self,
        mock_load_settings,
        mock_request_llm_plan,
    ) -> None:
        mock_load_settings.return_value = SimpleNamespace(
            llm_provider="ollama",
            llm_api_key="",
            llm_model="deepseek-v4-pro",
        )
        mock_request_llm_plan.return_value = LlmPlanResponse(
            text=json.dumps({"action": "reply", "reply": "ready"}),
            reasoning={},
            usage={
                "schema": "vrcforge.provider_usage.v1",
                "provider": "deepseek",
                "providerLabel": "DeepSeek",
                "model": "deepseek-v4-pro",
                "source": "openai-compatible",
                "exact": True,
                "inputTokens": 1234,
                "outputTokens": 56,
                "totalTokens": 1290,
            },
        )

        history = [{"role": "user", "text": "first visible request"}]
        with TestClient(dashboard_server.app) as client:
            response = client.post(
                "/api/app/agent/message",
                json={"message": "hello model planner", "history": history},
            )

        self.assertEqual(response.status_code, 200)
        usage = response.json()["contextUsage"]
        self.assertTrue(usage["exact"])
        self.assertEqual(usage["inputTokens"], 1234)
        self.assertEqual(usage["outputTokens"], 56)
        self.assertEqual(usage["totalTokens"], 1290)
        self.assertEqual(usage["sentHistoryEntryCount"], 1)
        self.assertGreater(usage["promptCharacterCount"], 0)

    def test_agent_runtime_prompt_uses_full_visible_dialogue_only(self) -> None:
        captured: dict[str, str] = {}
        previous_fn = dashboard_server.AGENT_GATEWAY.llm_plan_fn
        previous_label = dashboard_server.AGENT_GATEWAY.llm_planner_label
        try:
            dashboard_server.AGENT_GATEWAY.llm_planner_label = "TestProvider / model"

            def plan_fn(prompt: str) -> dict:
                captured["prompt"] = prompt
                return {
                    "text": json.dumps({"action": "reply", "reply": "done"}),
                    "usage": {"exact": True, "inputTokens": 77, "outputTokens": 5, "totalTokens": 82},
                }

            dashboard_server.AGENT_GATEWAY.llm_plan_fn = plan_fn
            long_tail = "visible-long-history-" + ("x" * 700) + "-tail-kept"
            history = [
                {"role": "user", "text": "first turn is still present", "tool": "DO_NOT_SEND_TOOL_FIELD"},
                {"role": "agent", "text": "assistant visible reply one", "reasoning": "DO_NOT_SEND_COT_FIELD"},
            ]
            for index in range(13):
                history.append({"role": "user", "text": f"middle user {index}"})
            history.append({"role": "agent", "text": long_tail, "shell": {"stdout": "DO_NOT_SEND_STDOUT_FIELD"}})

            payload = dashboard_server.AGENT_GATEWAY.runtime_message(
                {
                    "message": "latest user message",
                    "session_id": "sess-full-visible-history",
                    "history": history,
                }
            )
        finally:
            dashboard_server.AGENT_GATEWAY.llm_plan_fn = previous_fn
            dashboard_server.AGENT_GATEWAY.llm_planner_label = previous_label
            dashboard_server.AGENT_GATEWAY._runtime_sessions.pop("sess-full-visible-history", None)

        self.assertTrue(payload["ok"])
        prompt = captured["prompt"]
        self.assertIn("first turn is still present", prompt)
        self.assertIn("middle user 0", prompt)
        self.assertIn("middle user 12", prompt)
        self.assertIn("-tail-kept", prompt)
        self.assertIn("latest user message", prompt)
        self.assertNotIn("DO_NOT_SEND_TOOL_FIELD", prompt)
        self.assertNotIn("DO_NOT_SEND_COT_FIELD", prompt)
        self.assertNotIn("DO_NOT_SEND_STDOUT_FIELD", prompt)

    def test_agent_runtime_prompt_filters_project_memory_by_project_root(self) -> None:
        captured: list[str] = []
        previous_fn = dashboard_server.AGENT_GATEWAY.llm_plan_fn
        previous_label = dashboard_server.AGENT_GATEWAY.llm_planner_label
        try:
            dashboard_server.AGENT_GATEWAY.create_agent_memory(
                {"scope": "project", "kind": "style", "text": "ProjectA private memory", "projectRoot": "ProjectA"}
            )
            dashboard_server.AGENT_GATEWAY.create_agent_memory(
                {"scope": "project", "kind": "style", "text": "ProjectB private memory", "projectRoot": "ProjectB"}
            )
            dashboard_server.AGENT_GATEWAY.create_agent_memory(
                {"scope": "user", "kind": "preference", "text": "Global user memory"}
            )
            dashboard_server.AGENT_GATEWAY.llm_planner_label = "TestProvider / model"

            def plan_fn(prompt: str) -> dict:
                captured.append(prompt)
                return {"text": json.dumps({"action": "reply", "reply": "done"})}

            dashboard_server.AGENT_GATEWAY.llm_plan_fn = plan_fn
            dashboard_server.AGENT_GATEWAY.runtime_message(
                {"message": "use scoped memory", "session_id": "sess-memory-project-a", "projectRoot": "ProjectA"}
            )
            dashboard_server.AGENT_GATEWAY.runtime_message(
                {"message": "use only user memory", "session_id": "sess-memory-no-project"}
            )
        finally:
            dashboard_server.AGENT_GATEWAY.llm_plan_fn = previous_fn
            dashboard_server.AGENT_GATEWAY.llm_planner_label = previous_label
            dashboard_server.AGENT_GATEWAY._runtime_sessions.pop("sess-memory-project-a", None)
            dashboard_server.AGENT_GATEWAY._runtime_sessions.pop("sess-memory-no-project", None)

        self.assertEqual(len(captured), 2)
        project_prompt, no_project_prompt = captured
        self.assertIn("ProjectA private memory", project_prompt)
        self.assertIn("Global user memory", project_prompt)
        self.assertNotIn("ProjectB private memory", project_prompt)
        self.assertIn("Global user memory", no_project_prompt)
        self.assertNotIn("ProjectA private memory", no_project_prompt)
        self.assertNotIn("ProjectB private memory", no_project_prompt)

    def test_llm_planner_prompt_uses_thin_loop_observations(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            gateway = AgentGateway(root / "config" / "agent_gateway.json", root / "audit")
            prompt = gateway._build_llm_plan_prompt(
                "continue after tool execution",
                history=[],
                loop_state=[
                    {
                        "tool": "shell",
                        "kind": "shell",
                        "status": "executed",
                        "result": {
                            "ok": True,
                            "exitCode": 0,
                            "timedOut": False,
                            "stdoutSummary": "DO_NOT_SEND_STDOUT_SUMMARY",
                            "stderrSummary": "DO_NOT_SEND_STDERR_SUMMARY",
                            "payload": {"raw": "DO_NOT_SEND_PAYLOAD"},
                        },
                    },
                    {
                        "tool": "vrcforge_test_tool",
                        "kind": "skill",
                        "status": "failed",
                        "result": {
                            "ok": False,
                            "code": "missing_parameter",
                            "error": "missing avatar path",
                            "data": {"raw": "DO_NOT_SEND_DATA"},
                        },
                    },
                ],
            )

        self.assertIn("shell", prompt)
        self.assertIn("exitCode=0", prompt)
        self.assertIn("missing_parameter", prompt)
        self.assertIn("missing avatar path", prompt)
        self.assertNotIn("DO_NOT_SEND_STDOUT_SUMMARY", prompt)
        self.assertNotIn("DO_NOT_SEND_STDERR_SUMMARY", prompt)
        self.assertNotIn("DO_NOT_SEND_PAYLOAD", prompt)
        self.assertNotIn("DO_NOT_SEND_DATA", prompt)

    def test_agent_runtime_routes_read_skill_without_shell(self) -> None:
        with TestClient(dashboard_server.app) as client:
            response = client.post("/api/app/agent/message", json={"message": "检查 Unity MCP 状态"})

        self.assertEqual(response.status_code, 200)
        payload = response.json()
        self.assertTrue(payload["ok"])
        self.assertFalse(payload["plan"]["shellNeeded"])
        self.assertTrue(payload["plan"]["skillNeeded"])
        self.assertEqual(payload["plan"]["skillTool"], "vrcforge_unity_status")
        self.assertEqual(payload["plan"]["nextStep"], "call_skill")
        self.assertEqual(payload["skill"]["tool"], "vrcforge_unity_status")
        self.assertEqual(payload["skill"]["status"], "executed")
        self.assertIn("result", payload["skill"])

    def test_agent_runtime_routes_skill_manifest_request(self) -> None:
        with TestClient(dashboard_server.app) as client:
            response = client.post("/api/app/agent/message", json={"message": "列一下 skills"})

        self.assertEqual(response.status_code, 200)
        payload = response.json()
        self.assertTrue(payload["ok"])
        self.assertEqual(payload["plan"]["skillTool"], "vrcforge_skill_manifest")
        self.assertEqual(payload["skill"]["status"], "executed")
        self.assertGreater(payload["skill"]["result"]["toolCount"], 10)
        self.assertNotIn("token", payload["skill"]["result"])

    def test_app_skill_registry_crud_uses_local_skill_markdown(self) -> None:
        with TestClient(dashboard_server.app) as client:
            initial = client.get("/api/app/skills")
            self.assertEqual(initial.status_code, 200)
            initial_payload = initial.json()
            self.assertEqual(initial_payload["schema"], "vrcforge.skills.v1")
            builtin_names = {skill["name"] for skill in initial_payload["skills"] if skill["source"] == "builtin"}
            self.assertNotIn("vrcforge_roslyn_status", builtin_names)
            self.assertNotIn("roslyn-advanced-power", builtin_names)
            self.assertIn("runtime-diagnostics", builtin_names)
            runtime_skill = next(skill for skill in initial_payload["skills"] if skill["name"] == "runtime-diagnostics")
            self.assertEqual(runtime_skill["skillType"], "group")
            self.assertIn("vrcforge_skill_check", runtime_skill["allowedTools"])

            created = client.post(
                "/api/app/skills",
                json={
                    "name": "avatar-review",
                    "title": "Avatar Review",
                    "description": "Check avatar state before edits.",
                    "whenToUse": "avatar review",
                    "inputs": ["Unity project context"],
                    "outputs": ["Review notes"],
                    "allowedTools": ["vrcforge_unity_status", "vrcforge_list_avatars"],
                    "disallowedTools": ["vrcforge_execute_shell"],
                    "entrypointTool": "vrcforge_unity_status",
                    "argumentHint": "avatar path",
                    "instructions": "Inspect Unity status before suggesting writes.",
                },
            )
            self.assertEqual(created.status_code, 200)
            created_payload = created.json()
            self.assertEqual(created_payload["skill"]["name"], "avatar-review")
            skill_file = dashboard_server.AGENT_GATEWAY.user_skills_dir / "avatar-review" / "SKILL.md"
            self.assertTrue(skill_file.exists())
            skill_text = skill_file.read_text(encoding="utf-8")
            self.assertIn("allowed-tools:", skill_text)
            self.assertIn("disallowed-tools:", skill_text)
            self.assertIn("entrypoint-tool: vrcforge_unity_status", skill_text)
            self.assertIn("Inspect Unity status", skill_text)

            check = client.get("/api/app/skills/check")
            self.assertEqual(check.status_code, 200)
            self.assertGreaterEqual(check.json()["count"], initial_payload["count"])

            turn = client.post(
                "/api/app/agent/message",
                json={"message": "/avatar-review Scene/Hero"},
            )
            self.assertEqual(turn.status_code, 200)
            self.assertEqual(turn.json()["skill"]["status"], "executed")
            self.assertEqual(turn.json()["skill"]["result"]["name"], "avatar-review")
            self.assertEqual(turn.json()["skill"]["result"]["arguments"], "Scene/Hero")
            self.assertEqual(turn.json()["skill"]["entrypointTool"], "vrcforge_unity_status")

            updated = client.put("/api/app/skills/avatar-review", json={"title": "Avatar Review Updated"})
            self.assertEqual(updated.status_code, 200)
            self.assertEqual(updated.json()["skill"]["title"], "Avatar Review Updated")

            deleted = client.delete("/api/app/skills/avatar-review")
            self.assertEqual(deleted.status_code, 200)
            self.assertFalse(skill_file.exists())

    def test_skill_markdown_hyphen_frontmatter_and_dependency_check(self) -> None:
        skill_dir = dashboard_server.AGENT_GATEWAY.user_skills_dir / "hyphen-skill"
        skill_dir.mkdir(parents=True, exist_ok=True)
        (skill_dir / "SKILL.md").write_text(
            "\n".join(
                [
                    "---",
                    "name: hyphen-skill",
                    "title: Hyphen Skill",
                    "permission-mode: read-only",
                    "risk-level: low",
                    "when-to-use: hyphen skill",
                    "allowed-tools:",
                    "  - vrcforge_health",
                    "entrypoint-tool: vrcforge_health",
                    "argument-hint: target",
                    "requires-env:",
                    "  - VRCFORGE_TEST_MISSING_ENV",
                    "supported-os:",
                    "  - windows",
                    "disable-model-invocation: true",
                    "---",
                    "Use $ARGUMENTS safely.",
                ]
            ),
            encoding="utf-8",
        )
        with TestClient(dashboard_server.app) as client:
            skills = client.get("/api/app/skills").json()["skills"]
            skill = next(item for item in skills if item["name"] == "hyphen-skill")
            self.assertEqual(skill["permissionMode"], "read_only")
            self.assertEqual(skill["entrypointTool"], "vrcforge_health")
            self.assertTrue(skill["disableModelInvocation"])
            self.assertEqual(skill["argumentHint"], "target")
            self.assertEqual(skill["validation"]["status"], "error")
            self.assertIn("missing env", "; ".join(skill["validation"]["reasons"]))

            check = client.get("/api/app/skills/check")
            self.assertEqual(check.status_code, 200)
            check_skill = next(item for item in check.json()["checks"] if item["name"] == "hyphen-skill")
            self.assertEqual(check_skill["status"], "error")

    def test_shell_classifier_low_high_and_reject_cases(self) -> None:
        workspace_root = str(Path(__file__).resolve().parents[1])

        low = dashboard_server.AGENT_GATEWAY.classify_shell(
            {"command": "git --no-pager status --short", "workspace_root": workspace_root}
        )
        self.assertEqual(low["risk"], "low")

        rg_low = dashboard_server.AGENT_GATEWAY.classify_shell(
            {"command": "rg TODO .", "workspace_root": workspace_root}
        )
        self.assertEqual(rg_low["risk"], "low")

        git_show_low = dashboard_server.AGENT_GATEWAY.classify_shell(
            {"command": "git show --stat HEAD", "workspace_root": workspace_root}
        )
        self.assertEqual(git_show_low["risk"], "low")

        high = dashboard_server.AGENT_GATEWAY.classify_shell(
            {"command": "Set-Content test.txt hi", "workspace_root": workspace_root}
        )
        self.assertEqual(high["risk"], "high")

        home_path = dashboard_server.AGENT_GATEWAY.classify_shell(
            {"command": "Get-Content ~\\.codex\\auth.json", "workspace_root": workspace_root}
        )
        self.assertEqual(home_path["risk"], "high")

        root_relative = dashboard_server.AGENT_GATEWAY.classify_shell(
            {"command": "Get-Content \\Windows\\win.ini", "workspace_root": workspace_root}
        )
        self.assertEqual(root_relative["risk"], "high")

        rg_preprocessor = dashboard_server.AGENT_GATEWAY.classify_shell(
            {"command": "rg --pre powershell TODO .", "workspace_root": workspace_root}
        )
        self.assertEqual(rg_preprocessor["risk"], "high")

        git_show_output = dashboard_server.AGENT_GATEWAY.classify_shell(
            {"command": "git show --stat --output=leak.txt HEAD", "workspace_root": workspace_root}
        )
        self.assertEqual(git_show_output["risk"], "high")

        redirected = dashboard_server.AGENT_GATEWAY.classify_shell(
            {"command": "Get-Content a.txt > b.txt", "workspace_root": workspace_root}
        )
        self.assertEqual(redirected["risk"], "high")

        chained = dashboard_server.AGENT_GATEWAY.classify_shell(
            {"command": "Get-ChildItem; Remove-Item test.txt", "workspace_root": workspace_root}
        )
        self.assertEqual(chained["risk"], "high")

        rejected = dashboard_server.AGENT_GATEWAY.classify_shell({"command": "", "workspace_root": workspace_root})
        self.assertEqual(rejected["risk"], "reject")

    def test_shell_command_can_be_cancelled_before_timeout(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            cancel_id = "sess-shell-cancel"
            dashboard_server.AGENT_GATEWAY._cancelled_runtime_turns.add(cancel_id)
            try:
                result = dashboard_server.AGENT_GATEWAY._run_shell_command(
                    "Start-Sleep -Seconds 30",
                    Path(temp_dir),
                    timeout_seconds=30,
                    cancel_ids=[cancel_id],
                )
            finally:
                dashboard_server.AGENT_GATEWAY._cancelled_runtime_turns.discard(cancel_id)

        self.assertFalse(result["ok"])
        self.assertTrue(result["cancelled"])
        self.assertFalse(result["timedOut"])
        self.assertLess(result["durationSeconds"], 5)

    def test_native_shell_argv_accepts_plain_commands_only(self) -> None:
        argv = native_shell_argv("git --no-pager status --short")
        self.assertIsNotNone(argv)
        assert argv is not None
        self.assertEqual(argv[1:], ["--no-pager", "status", "--short"])
        self.assertIn("git", Path(argv[0]).name.lower())

        # PowerShell cmdlets/aliases never resolve to a real executable.
        self.assertIsNone(native_shell_argv("Get-ChildItem"))
        # Pipeline, redirection, variables, formats stay on PowerShell semantics.
        self.assertIsNone(native_shell_argv("git status | Out-Null"))
        self.assertIsNone(native_shell_argv("Get-Content a.txt > b.txt"))
        self.assertIsNone(native_shell_argv("echo $env:PATH"))
        self.assertIsNone(native_shell_argv("git log --format=%h"))
        self.assertIsNone(native_shell_argv("git status; git log"))
        self.assertIsNone(native_shell_argv("git status\ngit log"))

    def test_shell_classification_reports_planned_runner(self) -> None:
        workspace_root = str(Path(__file__).resolve().parents[1])
        native = dashboard_server.AGENT_GATEWAY.classify_shell(
            {"command": "git --no-pager status --short", "workspace_root": workspace_root}
        )
        self.assertEqual(native["plannedRunner"], SHELL_RUNNER_NATIVE)

        cmdlet = dashboard_server.AGENT_GATEWAY.classify_shell(
            {"command": "Get-ChildItem", "workspace_root": workspace_root}
        )
        self.assertEqual(cmdlet["plannedRunner"], SHELL_RUNNER_POWERSHELL)

    def test_run_shell_command_uses_native_runner_for_plain_git(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            result = dashboard_server.AGENT_GATEWAY._run_shell_command(
                "git --version",
                Path(temp_dir),
                timeout_seconds=30,
            )
        self.assertEqual(result["runner"], SHELL_RUNNER_NATIVE)
        self.assertTrue(result["ok"])
        self.assertIn("git version", result["stdout"].lower())

    def test_resolve_powershell_executable_returns_nonempty_path(self) -> None:
        resolved = resolve_powershell_executable()
        self.assertIsInstance(resolved, str)
        self.assertTrue(resolved)

    def test_agent_runtime_shell_direct_and_approval_execution(self) -> None:
        with tempfile.TemporaryDirectory() as workspace:
            target = Path(workspace) / "agent-loop.txt"
            with TestClient(dashboard_server.app) as client:
                low = client.post(
                    "/api/app/agent/message",
                    json={
                        "message": "列目录",
                        "workspace_root": workspace,
                        "cwd": workspace,
                    },
                )
                self.assertEqual(low.status_code, 200)
                self.assertEqual(low.json()["shell"]["status"], "executed")
                self.assertEqual(low.json()["shell"]["classification"]["risk"], "low")

                high = client.post(
                    "/api/app/agent/message",
                    json={
                        "message": "写入测试文件",
                        "shell_command": "Set-Content -Path agent-loop.txt -Value hi -Encoding utf8",
                        "workspace_root": workspace,
                        "cwd": workspace,
                    },
                )
                self.assertEqual(high.status_code, 200)
                high_payload = high.json()
                self.assertEqual(high_payload["shell"]["status"], "pending_approval")
                self.assertFalse(target.exists())

                approval_id = high_payload["shell"]["approval_id"]
                with patch("dashboard_server.asyncio.to_thread", wraps=dashboard_server.asyncio.to_thread) as to_thread:
                    approved = client.post(f"/api/app/agent/approvals/{approval_id}/approve")
                self.assertEqual(approved.status_code, 200)
                approved_payload = approved.json()
                self.assertTrue(approved_payload["ok"])
                self.assertEqual(approved_payload["execution"]["status"], "applied")
                self.assertTrue(target.exists())
                self.assertEqual(target.read_text(encoding="utf-8-sig").strip(), "hi")
                self.assertTrue(
                    any(
                        call.args and getattr(call.args[0], "__name__", "") == "approve_and_execute"
                        for call in to_thread.call_args_list
                    )
                )

                replay = client.post(f"/api/app/agent/approvals/{approval_id}/approve")
                self.assertEqual(replay.status_code, 200)
                self.assertFalse(replay.json()["ok"])

    def test_app_approval_revision_supersedes_pending_approval(self) -> None:
        with tempfile.TemporaryDirectory() as workspace:
            target = Path(workspace) / "revision.txt"
            with TestClient(dashboard_server.app) as client:
                high = client.post(
                    "/api/app/agent/message",
                    json={
                        "message": "write test file",
                        "shell_command": "Set-Content -Path revision.txt -Value hi -Encoding utf8",
                        "workspace_root": workspace,
                        "cwd": workspace,
                    },
                )
                self.assertEqual(high.status_code, 200)
                approval_id = high.json()["shell"]["approval_id"]

                revision = client.post(
                    f"/api/app/agent/approvals/{approval_id}/revision",
                    json={"reason": "change request", "note": "use another name"},
                )
                self.assertEqual(revision.status_code, 200)
                revision_payload = revision.json()
                self.assertTrue(revision_payload["ok"])
                self.assertEqual(revision_payload["approval"]["status"], "revision_requested")
                self.assertEqual(revision_payload["approval"]["revisionReason"], "change request")
                self.assertFalse(target.exists())

                stale_approval = client.post(f"/api/app/agent/approvals/{approval_id}/approve")
                self.assertEqual(stale_approval.status_code, 200)
                self.assertFalse(stale_approval.json()["ok"])
                self.assertFalse(target.exists())

    def test_app_approval_reject_does_not_rewrite_terminal_status(self) -> None:
        with tempfile.TemporaryDirectory() as workspace:
            target = Path(workspace) / "terminal.txt"
            with TestClient(dashboard_server.app) as client:
                high = client.post(
                    "/api/app/agent/message",
                    json={
                        "message": "write test file",
                        "shell_command": "Set-Content -Path terminal.txt -Value hi -Encoding utf8",
                        "workspace_root": workspace,
                        "cwd": workspace,
                    },
                )
                self.assertEqual(high.status_code, 200)
                approval_id = high.json()["shell"]["approval_id"]
                approved = client.post(f"/api/app/agent/approvals/{approval_id}/approve")
                self.assertEqual(approved.status_code, 200)
                self.assertTrue(approved.json()["ok"])
                self.assertTrue(target.exists())

                rejected = client.post(f"/api/app/agent/approvals/{approval_id}/reject")
                self.assertEqual(rejected.status_code, 200)
                rejected_payload = rejected.json()
                self.assertFalse(rejected_payload["ok"])
                self.assertEqual(rejected_payload["approval"]["status"], "applied")

    def test_agent_gateway_preview_and_supervised_apply_flow(self) -> None:
        temp_project = tempfile.TemporaryDirectory()
        self.addCleanup(temp_project.cleanup)
        project = Path(temp_project.name) / "UnityProject"
        (project / "Assets").mkdir(parents=True)
        (project / "Packages").mkdir()
        (project / "ProjectSettings").mkdir()
        (project / "Packages" / "manifest.json").write_text("{}", encoding="utf-8")
        (project / "ProjectSettings" / "ProjectVersion.txt").write_text("m_EditorVersion: 2022.3", encoding="utf-8")
        original_prepare = dashboard_server.AGENT_GATEWAY.checkpoint_prepare_handler
        dashboard_server.AGENT_GATEWAY.checkpoint_prepare_handler = lambda _root: {"ok": True}
        self.addCleanup(setattr, dashboard_server.AGENT_GATEWAY, "checkpoint_prepare_handler", original_prepare)

        config = dashboard_server.AGENT_GATEWAY.ensure_config()
        config.enabled = True
        dashboard_server.AGENT_GATEWAY.save_config(config)
        headers = {"Authorization": f"Bearer {config.token}"}

        with TestClient(dashboard_server.app) as client:
            preview = client.post(
                "/api/agent/tool/vrcforge_preview_blendshape_apply",
                headers=headers,
                json={
                    "agent_name": "codex-test",
                    "params": {
                        "avatar_path": "Scene/Avatar",
                        "adjustments": [
                            {"rendererPath": "Scene/Avatar/Face", "blendshapeName": "Smile", "targetWeight": 42}
                        ],
                    },
                },
            )
            self.assertEqual(preview.status_code, 200)
            self.assertTrue(preview.json()["ok"])
            self.assertIn("vrc_apply_blendshapes", preview.json()["result"]["applyPayload"])

            request_apply = client.post(
                "/api/agent/tool/vrcforge_request_apply",
                headers=headers,
                json={
                    "agent_name": "codex-test",
                    "params": {
                        "target_tool": "vrcforge_apply_blendshapes",
                        "arguments": {"projectRoot": str(project), "adjustments": []},
                        "reason": "test supervised loop",
                    },
                },
            )
            self.assertEqual(request_apply.status_code, 200)
            approval = request_apply.json()["result"]["approval"]
            self.assertEqual(approval["status"], "pending")

            approvals = client.get("/api/agent/approvals", headers=headers)
            self.assertEqual(approvals.status_code, 200)
            self.assertEqual(approvals.json()["count"], 1)

            agent_cannot_approve = client.post(f"/api/agent/approvals/{approval['id']}/approve", headers=headers)
            self.assertEqual(agent_cannot_approve.status_code, 401)

            external_cannot_apply = client.post(
                "/api/agent/tool/vrcforge_apply_approved",
                headers=headers,
                json={"agent_name": "codex-test", "params": {"approval_id": approval["id"]}},
            )
            self.assertEqual(external_cannot_apply.status_code, 404)

            with patch("dashboard_server.apply_manual_blendshapes_sync", return_value={"ok": True, "appliedAdjustments": []}) as mock_apply:
                applied = client.post(
                    f"/api/app/agent/approvals/{approval['id']}/approve",
                    json={"expectedProjectRoot": str(project)},
                )
            self.assertEqual(applied.status_code, 200)
            self.assertTrue(applied.json()["ok"])
            self.assertEqual(applied.json()["execution"]["status"], "applied")
            mock_apply.assert_called_once()

    def test_agent_gateway_manifest_describes_codex_debug_loop_tools(self) -> None:
        config = dashboard_server.AGENT_GATEWAY.ensure_config()
        config.enabled = True
        dashboard_server.AGENT_GATEWAY.save_config(config)
        headers = {"Authorization": f"Bearer {config.token}"}

        with TestClient(dashboard_server.app) as client:
            payload = client.get("/api/agent/manifest", headers=headers).json()

        tool_names = {tool["name"] for tool in payload["tools"]}
        self.assertIn("vrcforge_agent_observe", tool_names)
        self.assertIn("vrcforge_agent_message", tool_names)
        self.assertIn("vrcforge_classify_shell", tool_names)
        self.assertIn("vrcforge_execute_shell", tool_names)
        self.assertNotIn("vrcforge_execute_approved_shell", tool_names)
        self.assertIn("vrcforge_skill_manifest", tool_names)
        self.assertIn("vrcforge_tool_registry", tool_names)
        self.assertIn("vrcforge_external_agent_connectors", tool_names)
        self.assertIn("vrcforge_list_skill_packages", tool_names)
        self.assertIn("vrcforge_preflight_skill_package", tool_names)
        self.assertIn("vrcforge_capture_screenshot", tool_names)
        self.assertIn("vrcforge_vision_audit", tool_names)
        self.assertNotIn("vrcforge_roslyn_status", tool_names)
        self.assertIn("vrcforge_get_compile_errors", tool_names)
        self.assertIn("vrcforge_request_apply", tool_names)
        self.assertIn("vrcforge_tool_registry", tool_names)
        self.assertNotIn("vrcforge_apply_approved", tool_names)
        self.assertNotIn("vrcforge_execute_approved_shell", tool_names)
        self.assertIn("vrcforge_read_recent_logs", tool_names)
        write_targets = {item["name"] for item in payload["writeTargets"]}
        self.assertIn("vrcforge_apply_blendshapes", write_targets)
        self.assertIn("vrcforge_import_skill_package", write_targets)
        self.assertIn("vrcforge_export_skill_package", write_targets)
        self.assertIn("vrcforge_set_skill_package_enabled", write_targets)
        self.assertIn("vrcforge_uninstall_skill_package", write_targets)
        self.assertNotIn("vrcforge_import_skill_package", tool_names)
        self.assertNotIn("vrcforge_export_skill_package", tool_names)
        self.assertNotIn("vrcforge_set_skill_package_enabled", tool_names)
        self.assertNotIn("vrcforge_uninstall_skill_package", tool_names)
        self.assertNotIn("api_key", json.dumps(payload).lower())
        self.assertNotIn("approval_token", json.dumps(payload).lower())

    def test_external_agent_connector_endpoint_uses_env_placeholder(self) -> None:
        with TestClient(dashboard_server.app) as client:
            response = client.post(
                "/api/app/external-agent/connectors",
                json={
                    "serverName": "vrcforge_local",
                    "tokenEnvVar": "CUSTOM_VRCFORGE_TOKEN",
                    "mcpUrl": "http://127.0.0.1:8757/mcp",
                },
            )

        self.assertEqual(response.status_code, 200)
        payload = response.json()
        rendered = json.dumps(payload)
        self.assertTrue(payload["ok"])
        self.assertEqual(payload["mcp"]["url"], "http://127.0.0.1:8757/mcp")
        stdio = payload["clientConfigs"]["codexStdio"]["config"]["mcp_servers"]["vrcforge_local"]
        self.assertEqual(Path(stdio["cwd"]), dashboard_server.ROOT_DIR)
        self.assertEqual(Path(stdio["args"][0]), dashboard_server.ROOT_DIR / "tools" / "vrcforge_agent_mcp_stdio.py")
        self.assertIn("--no-start", stdio["args"])
        self.assertIn("CUSTOM_VRCFORGE_TOKEN", rendered)
        self.assertNotIn("real-token", rendered)

    def test_external_agent_connector_prefers_packaged_backend_stdio(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            backend_dir = root / "backend"
            backend_dir.mkdir()
            backend_exe = backend_dir / "vrcforge_backend.exe"
            backend_exe.write_text("", encoding="utf-8")

            with patch("dashboard_server.ROOT_DIR", root):
                payload = dashboard_server.connector_bundle_sync({})

        stdio = payload["clientConfigs"]["codexStdio"]["config"]["mcp_servers"]["vrcforge"]
        self.assertEqual(Path(stdio["command"]), backend_exe)
        self.assertEqual(stdio["args"], ["--agent-mcp-stdio", "--no-start"])
        self.assertEqual(Path(stdio["cwd"]), root)

    def test_external_agent_connector_status_uses_project_query_for_claude_code(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            project = Path(tmp) / "Unity Project"
            project.mkdir()
            (project / ".mcp.json").write_text(
                json.dumps(
                    {
                        "mcpServers": {
                            "vrcforge": {
                                "command": "vrcforge_backend.exe",
                                "args": ["--agent-mcp-stdio"],
                                "env": {},
                            }
                        }
                    }
                ),
                encoding="utf-8",
            )

            with TestClient(dashboard_server.app) as client:
                without_project = client.get("/api/app/external-agent/connectors")
                with_project = client.get(
                    "/api/app/external-agent/connectors",
                    params={"projectPath": str(project)},
                )

        self.assertEqual(without_project.status_code, 200)
        self.assertEqual(with_project.status_code, 200)
        self.assertFalse(without_project.json()["clients"]["claudeCode"]["installed"])
        self.assertTrue(with_project.json()["clients"]["claudeCode"]["installed"])
        self.assertTrue(with_project.json()["clients"]["claudeCode"]["installable"])
        self.assertEqual(Path(with_project.json()["clients"]["claudeCode"]["configPath"]), project / ".mcp.json")

    def test_external_agent_gateway_settings_update_and_revoke_token(self) -> None:
        config = dashboard_server.AGENT_GATEWAY.ensure_config()
        original_token = config.token

        with TestClient(dashboard_server.app) as client:
            enabled = client.post(
                "/api/app/external-agent/gateway",
                json={"enabled": True, "allowWriteRequests": False},
            )
            revoked = client.post("/api/app/external-agent/gateway", json={"revokeToken": True})
            old_token_manifest = client.get(
                "/api/agent/manifest",
                headers={"Authorization": f"Bearer {original_token}"},
            )

        self.assertEqual(enabled.status_code, 200)
        self.assertTrue(enabled.json()["gateway"]["enabled"])
        self.assertFalse(enabled.json()["gateway"]["allowWriteRequests"])
        self.assertEqual(revoked.status_code, 200)
        self.assertTrue(revoked.json()["gateway"]["tokenConfigured"])
        self.assertEqual(old_token_manifest.status_code, 401)
        serialized = json.dumps(revoked.json()).lower()
        self.assertNotIn("approval_token", serialized)
        self.assertNotIn(original_token.lower(), serialized)

    def test_external_agent_gateway_checkpoint_archive_limit_prunes_old_archives(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            gateway = AgentGateway(root / "config" / "agent_gateway.json", root / "audit")
            archive_dir = gateway.checkpoint_store_dir / "project"
            archive_dir.mkdir(parents=True)
            base_time = datetime(2026, 1, 1, tzinfo=timezone.utc).timestamp()
            paths: list[Path] = []
            for index in range(3):
                path = archive_dir / f"ckpt_{index}.zip"
                path.write_bytes(b"x" * 700_000)
                os.utime(path, (base_time + index, base_time + index))
                paths.append(path)

            original_gateway = dashboard_server.AGENT_GATEWAY
            try:
                dashboard_server.AGENT_GATEWAY = gateway
                with TestClient(dashboard_server.app) as client:
                    response = client.post("/api/app/external-agent/gateway", json={"checkpointArchiveMaxSizeMb": 1})
            finally:
                dashboard_server.AGENT_GATEWAY = original_gateway

            payload = response.json()
            self.assertEqual(response.status_code, 200)
            self.assertEqual(payload["gateway"]["checkpointArchiveMaxSizeMb"], 1)
            self.assertEqual(payload["gateway"]["checkpointArchivePrune"]["deletedCount"], 1)
            self.assertFalse(paths[0].exists())
            self.assertTrue(paths[1].exists())
            self.assertTrue(paths[2].exists())
            self.assertGreater(payload["gateway"]["checkpointArchiveUsage"]["sizeBytes"], 1_048_576)

    def test_external_agent_gateway_checkpoint_archive_default_limit_is_bounded(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            gateway = AgentGateway(root / "config" / "agent_gateway.json", root / "audit")
            config = gateway.ensure_config()

            self.assertEqual(config.checkpoint_archive_max_size_mb, CHECKPOINT_ARCHIVE_DEFAULT_MAX_SIZE_MB)
            self.assertEqual(gateway.checkpoint_archive_usage(config)["maxSizeMb"], CHECKPOINT_ARCHIVE_DEFAULT_MAX_SIZE_MB)

    def test_external_agent_gateway_checkpoint_ledgers_are_fsynced(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            gateway = AgentGateway(root / "config" / "agent_gateway.json", root / "audit")

            with patch("agent_gateway.os.fsync") as fsync:
                gateway._append_checkpoint({"id": "ckpt_fsync", "ok": True})
                gateway._append_apply_recovery_entry({"id": "rec_fsync", "checkpointId": "ckpt_fsync", "status": "applying"})

            self.assertGreaterEqual(fsync.call_count, 2)

    def test_external_agent_gateway_checkpoint_archive_limit_zero_keeps_archives(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            gateway = AgentGateway(root / "config" / "agent_gateway.json", root / "audit")
            archive_dir = gateway.checkpoint_store_dir / "project"
            archive_dir.mkdir(parents=True)
            archive_path = archive_dir / "ckpt_keep.zip"
            archive_path.write_bytes(b"x" * 700_000)

            original_gateway = dashboard_server.AGENT_GATEWAY
            try:
                dashboard_server.AGENT_GATEWAY = gateway
                with TestClient(dashboard_server.app) as client:
                    response = client.post("/api/app/external-agent/gateway", json={"checkpointArchiveMaxSizeMb": 0})
            finally:
                dashboard_server.AGENT_GATEWAY = original_gateway

            payload = response.json()
            self.assertEqual(response.status_code, 200)
            self.assertEqual(payload["gateway"]["checkpointArchiveMaxSizeMb"], 0)
            self.assertEqual(payload["gateway"]["checkpointArchivePrune"]["deletedCount"], 0)
            self.assertTrue(archive_path.exists())

    def test_external_agent_gateway_relocate_checkpoint_archives_rewrites_paths(self) -> None:
        # 安全关键：检查点把绝对 archivePath 持久化在 checkpoints.jsonl，迁移必须复制 ZIP
        # 并改写记录，否则回滚会找不到存档。这里证明改写后的路径指向新目录里真实存在的文件。
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            gateway = AgentGateway(root / "config" / "agent_gateway.json", root / "audit")
            old_dir = gateway.checkpoint_store_dir / "project"
            old_dir.mkdir(parents=True)
            old_zip = old_dir / "ckpt_relocate.zip"
            old_zip.write_bytes(b"x" * 1024)
            gateway.checkpoint_log_path.parent.mkdir(parents=True, exist_ok=True)
            gateway.checkpoint_log_path.write_text(
                json.dumps(
                    {"id": "ckpt_relocate", "archivePath": str(old_zip), "strategy": "archive"}
                )
                + "\n",
                encoding="utf-8",
            )
            new_root = root / "moved-archives"

            original_gateway = dashboard_server.AGENT_GATEWAY
            try:
                dashboard_server.AGENT_GATEWAY = gateway
                with TestClient(dashboard_server.app) as client:
                    response = client.post(
                        "/api/app/external-agent/gateway",
                        json={"checkpointArchiveDirectory": str(new_root)},
                    )
            finally:
                dashboard_server.AGENT_GATEWAY = original_gateway

            payload = response.json()
            self.assertEqual(response.status_code, 200)
            relocate = payload["gateway"]["checkpointArchiveRelocate"]
            self.assertTrue(relocate["ok"])
            self.assertEqual(relocate["copiedCount"], 1)
            self.assertEqual(relocate["rewrittenCount"], 1)

            new_zip = new_root / "project" / "ckpt_relocate.zip"
            self.assertTrue(new_zip.exists())
            self.assertFalse(old_zip.exists())
            self.assertEqual(gateway.checkpoint_store_dir.resolve(), new_root.resolve())

            record = json.loads(gateway.checkpoint_log_path.read_text(encoding="utf-8").splitlines()[0])
            self.assertEqual(Path(record["archivePath"]).resolve(), new_zip.resolve())

    def test_external_agent_gateway_relocate_blocked_by_active_recovery(self) -> None:
        # 有未结的写入恢复时迁移必须被安全闸拒绝，否则迁移途中回滚会找不到旧存档。
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            gateway = AgentGateway(root / "config" / "agent_gateway.json", root / "audit")
            old_dir = gateway.checkpoint_store_dir / "project"
            old_dir.mkdir(parents=True)
            old_zip = old_dir / "ckpt_guard.zip"
            old_zip.write_bytes(b"x" * 1024)
            gateway._append_apply_recovery_entry(
                {"id": "rec_1", "checkpointId": "ckpt_guard", "status": "needs_recovery"}
            )
            new_root = root / "moved-archives"

            result = gateway.relocate_checkpoint_archives(str(new_root))

            self.assertFalse(result["ok"])
            self.assertEqual(result["code"], "active_recovery")
            self.assertTrue(old_zip.exists())
            self.assertFalse((new_root / "project" / "ckpt_guard.zip").exists())

    def test_external_agent_gateway_delete_checkpoint_archives_protects_active_recovery(self) -> None:
        # 多选清理时，活跃恢复检查点对应的存档必须被强制保护、跳过不删。
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            gateway = AgentGateway(root / "config" / "agent_gateway.json", root / "audit")
            archive_dir = gateway.checkpoint_store_dir / "project"
            archive_dir.mkdir(parents=True)
            base_time = datetime(2026, 1, 1, tzinfo=timezone.utc).timestamp()
            keep = archive_dir / "ckpt_protected.zip"
            drop = archive_dir / "ckpt_free.zip"
            recent = archive_dir / "ckpt_recent.zip"
            keep.write_bytes(b"x" * 1024)
            drop.write_bytes(b"x" * 1024)
            recent.write_bytes(b"x" * 1024)
            os.utime(drop, (base_time, base_time))
            os.utime(keep, (base_time + 1, base_time + 1))
            os.utime(recent, (base_time + 2, base_time + 2))
            gateway._append_apply_recovery_entry(
                {"id": "rec_2", "checkpointId": "ckpt_protected", "status": "applying"}
            )

            original_gateway = dashboard_server.AGENT_GATEWAY
            try:
                dashboard_server.AGENT_GATEWAY = gateway
                with TestClient(dashboard_server.app) as client:
                    response = client.post(
                        "/api/app/external-agent/gateway",
                        json={"deleteCheckpointArchiveIds": ["ckpt_protected", "ckpt_free"]},
                    )
            finally:
                dashboard_server.AGENT_GATEWAY = original_gateway

            payload = response.json()
            self.assertEqual(response.status_code, 200)
            delete = payload["gateway"]["checkpointArchiveDelete"]
            self.assertTrue(delete["ok"])
            self.assertEqual(delete["deletedCount"], 1)
            self.assertIn("ckpt_protected", delete["protectedSkipped"])
            self.assertTrue(keep.exists())
            self.assertTrue(recent.exists())
            self.assertFalse(drop.exists())

    def test_skill_package_import_projects_skill_and_export_endpoint_builds_vsk(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            source = root / "package-source"
            source.mkdir()
            manifest = {
                "id": "community.avatar-review",
                "name": "Avatar Review Package",
                "skill_name": "avatar-review",
                "version": "1.0.0",
                "author": "Unit Test",
                "description": "Dashboard skill package fixture.",
                "min_vrcforge_version": "0.5.0",
                "permissions": ["read_project"],
                "entrypoints": {"skill": "SKILL.md"},
            }
            (source / "manifest.json").write_text(json.dumps(manifest), encoding="utf-8")
            (source / "SKILL.md").write_text(
                "---\n"
                "name: avatar-review\n"
                "title: Avatar Review\n"
                "description: Imported package skill.\n"
                "allowed-tools:\n"
                "  - vrcforge_health\n"
                "entrypoint-tool: vrcforge_health\n"
                "---\n"
                "Inspect project state before edits.\n",
                encoding="utf-8",
            )
            package = SkillPackageService(root / "store", vrcforge_version="0.5.1").export_dev(
                source,
                root / "avatar-review.vsk",
            ).package_path

            with TestClient(dashboard_server.app) as client:
                preflight = client.post("/api/app/skill-packages/preflight", json={"packagePath": str(package)})
                imported = client.post("/api/app/skill-packages/import", json={"packagePath": str(package)})
                skills = client.get("/api/app/skills").json()["skills"]
                exported_path = root / "exported-avatar-review.vsk"
                exported = client.post(
                    "/api/app/skill-packages/export",
                    json={"skillName": "avatar-review", "outputPath": str(exported_path)},
                )
                disabled = client.put(
                    "/api/app/skill-packages/community.avatar-review",
                    json={"enabled": False},
                )
                skills_after_disable = client.get("/api/app/skills").json()["skills"]
                uninstalled = client.request(
                    "DELETE",
                    "/api/app/skill-packages/community.avatar-review",
                    json={"removeProjectedSkill": True},
                )
                packages_after_uninstall = client.get("/api/app/skill-packages").json()["installed"]
                skills_after_uninstall = client.get("/api/app/skills").json()["skills"]

            self.assertEqual(preflight.status_code, 200)
            self.assertEqual(preflight.json()["preview"]["manifest"]["id"], "community.avatar-review")
            self.assertEqual(imported.status_code, 200)
            self.assertEqual(imported.json()["projectedSkill"]["name"], "avatar-review")
            self.assertFalse(imported.json()["projectedSkill"]["enabled"])
            self.assertTrue(any(skill["name"] == "avatar-review" and skill["source"] == "user" and not skill["enabled"] for skill in skills))
            self.assertEqual(exported.status_code, 200)
            self.assertTrue(exported_path.is_file())
            self.assertEqual(exported.json()["exported"]["signature_status"], "dev")
            self.assertEqual(disabled.status_code, 200)
            self.assertFalse(disabled.json()["state"]["registry_entry"]["enabled"])
            self.assertTrue(any(skill["name"] == "avatar-review" and not skill["enabled"] for skill in skills_after_disable))
            self.assertEqual(uninstalled.status_code, 200)
            self.assertEqual(uninstalled.json()["uninstalled"]["skill_id"], "community.avatar-review")
            self.assertEqual(packages_after_uninstall, [])
            self.assertFalse(any(skill["name"] == "avatar-review" for skill in skills_after_uninstall))

    def test_skill_package_safe_mode_import_dry_run_and_projection_disable(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            source = root / "package-source"
            source.mkdir()
            manifest = {
                "id": "community.risky-review",
                "name": "Risky Review Package",
                "skill_name": "risky-review",
                "version": "1.0.0",
                "author": "Unit Test",
                "description": "Dashboard risky skill package fixture.",
                "min_vrcforge_version": "0.5.0",
                "permissions": ["read_project", "unity_modify_materials"],
                "entrypoints": {"skill": "SKILL.md"},
            }
            (source / "manifest.json").write_text(json.dumps(manifest), encoding="utf-8")
            (source / "SKILL.md").write_text(
                "---\n"
                "name: risky-review\n"
                "title: Risky Review\n"
                "description: Imported risky package skill.\n"
                "allowed-tools:\n"
                "  - vrcforge_health\n"
                "entrypoint-tool: vrcforge_health\n"
                "---\n"
                "Inspect project state before edits.\n",
                encoding="utf-8",
            )
            package = SkillPackageService(root / "build-store", vrcforge_version="0.5.1").export_dev(
                source,
                root / "risky-review.vsk",
            ).package_path

            with TestClient(dashboard_server.app) as client:
                safe_mode = client.post("/api/app/skill-packages/safe-mode", json={"enabled": True})
                dry_run = client.post(
                    "/api/app/skill-packages/import",
                    json={"packagePath": str(package), "dryRun": True},
                )
                installed_after_dry_run = client.get("/api/app/skill-packages").json()["installed"]
                imported = client.post("/api/app/skill-packages/import", json={"packagePath": str(package)})
                skills = client.get("/api/app/skills").json()["skills"]
                enable_blocked = client.put(
                    "/api/app/skill-packages/community.risky-review",
                    json={"enabled": True},
                )

            self.assertEqual(safe_mode.status_code, 200)
            self.assertEqual(dry_run.status_code, 200)
            self.assertTrue(dry_run.json()["dryRun"])
            self.assertFalse(dry_run.json()["preview"]["dryRun"]["willWrite"])
            self.assertFalse(dry_run.json()["preview"]["governance"]["safeMode"]["defaultEnabled"])
            self.assertEqual(installed_after_dry_run, [])
            self.assertEqual(imported.status_code, 200)
            self.assertFalse(imported.json()["imported"]["registry_entry"]["enabled"])
            self.assertFalse(imported.json()["projectedSkill"]["enabled"])
            self.assertTrue(any(skill["name"] == "risky-review" and not skill["enabled"] for skill in skills))
            self.assertEqual(enable_blocked.status_code, 400)
            self.assertIn("safe mode", enable_blocked.json()["detail"])

    def test_skill_package_revoked_signer_preflight_explains_and_import_blocks(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            source = root / "package-source"
            source.mkdir()
            manifest = {
                "id": "community.signed-review",
                "name": "Signed Review Package",
                "skill_name": "signed-review",
                "version": "1.0.0",
                "author": "Unit Test",
                "description": "Dashboard signed skill package fixture.",
                "min_vrcforge_version": "0.5.0",
                "permissions": ["read_project"],
                "entrypoints": {"skill": "SKILL.md"},
            }
            (source / "manifest.json").write_text(json.dumps(manifest), encoding="utf-8")
            (source / "SKILL.md").write_text("---\nname: signed-review\n---\nInspect safely.\n", encoding="utf-8")
            service = SkillPackageService(root / "build-store", vrcforge_version="0.5.1")
            key_pair = service.generate_signing_keypair()
            package = service.export_release(source, root / "signed-review.vsk", key_pair.private_key_pem).package_path

            with TestClient(dashboard_server.app) as client:
                untrusted = client.post("/api/app/skill-packages/preflight", json={"packagePath": str(package)})
                revoked = client.post(
                    "/api/app/skill-packages/revoke-signer",
                    json={"signerFingerprint": key_pair.fingerprint, "reason": "test compromise"},
                )
                blocked_preflight = client.post("/api/app/skill-packages/preflight", json={"packagePath": str(package)})
                blocked_import = client.post("/api/app/skill-packages/import", json={"packagePath": str(package)})

            self.assertEqual(untrusted.status_code, 200)
            self.assertTrue(untrusted.json()["preview"]["governance"]["signatureVerified"])
            self.assertFalse(untrusted.json()["preview"]["governance"]["verified"])
            self.assertEqual(untrusted.json()["preview"]["governance"]["signerTrustStatus"], "untrusted")
            self.assertEqual(revoked.status_code, 200)
            self.assertEqual(blocked_preflight.status_code, 200)
            self.assertEqual(blocked_preflight.json()["preview"]["governance"]["signerTrustStatus"], "revoked")
            self.assertFalse(blocked_preflight.json()["preview"]["governance"]["importAllowed"])
            self.assertEqual(blocked_import.status_code, 400)
            self.assertIn("revoked", blocked_import.json()["detail"])

    def test_path_to_skill_preview_write_and_export_endpoints(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            preview_dir = root / "preview-should-not-write"
            source_dir = root / "captured-source"
            export_source_dir = root / "captured-export-source"
            package_path = root / "shader-preset.vsk"
            summary = {
                "status": "passed",
                "workflow": "shader_adapter_semantic_tuning",
                "projectPath": "C:\\Users\\xiao123\\AvatarProject",
                "avatarPath": "AvatarRoot",
                "steps": [
                    {
                        "name": "shader.apply",
                        "tool": "vrcforge_apply_shader_tuning",
                        "params": {
                            "projectRoot": "C:\\Users\\xiao123\\AvatarProject",
                            "artifactPath": "C:\\Users\\xiao123\\AvatarProject\\Assets\\VRCForge\\proof.json",
                            "rendererPath": "AvatarRoot/Hat",
                        },
                    }
                ],
                "validation": {
                    "requiresApproval": True,
                    "requiresCheckpoint": True,
                    "requiresRollback": True,
                },
            }

            with TestClient(dashboard_server.app) as client:
                preview = client.post(
                    "/api/app/path-to-skill/preview",
                    json={
                        "summary": summary,
                        "packageId": "community.path-to-skill.shader-preset",
                        "skillName": "shader-preset",
                        "title": "Shader Preset",
                        "outputPath": str(preview_dir),
                        "writeSource": True,
                        "exportVsk": True,
                    },
                )
                written = client.post(
                    "/api/app/path-to-skill/write",
                    json={
                        "summary": summary,
                        "packageId": "community.path-to-skill.shader-preset",
                        "skillName": "shader-preset",
                        "title": "Shader Preset",
                        "outputPath": str(source_dir),
                        "writeSource": True,
                    },
                )
                exported = client.post(
                    "/api/app/path-to-skill/write",
                    json={
                        "summary": summary,
                        "packageId": "community.path-to-skill.shader-preset",
                        "skillName": "shader-preset",
                        "title": "Shader Preset",
                        "outputPath": str(export_source_dir),
                        "exportVsk": True,
                        "confirmExport": True,
                        "packageOutputPath": str(package_path),
                    },
                )

            self.assertEqual(preview.status_code, 200, preview.text)
            preview_payload = preview.json()
            self.assertTrue(preview_payload["dryRun"])
            self.assertTrue(preview_payload["writeSuppressed"])
            self.assertFalse(preview_dir.exists())
            serialized_source = json.dumps(preview_payload["sourceFiles"], ensure_ascii=False)
            self.assertIn("vrcforge.path_to_skill.v1", serialized_source)
            self.assertIn("{{projectPath}}", serialized_source)
            self.assertNotIn("C:\\Users", serialized_source)

            self.assertEqual(written.status_code, 200, written.text)
            self.assertFalse(written.json()["dryRun"])
            self.assertTrue((source_dir / "manifest.json").is_file())
            self.assertTrue((source_dir / "SKILL.md").is_file())
            self.assertTrue((source_dir / "workflows" / "captured-path.json").is_file())

            self.assertEqual(exported.status_code, 200, exported.text)
            self.assertTrue(package_path.is_file())
            self.assertEqual(exported.json()["exported"]["signature_status"], "dev")
            package_preview = SkillPackageService(root / "inspect-store", vrcforge_version="0.9.0-beta").inspect_package(package_path)
            self.assertEqual(package_preview.manifest["id"], "community.path-to-skill.shader-preset")
            self.assertEqual(package_preview.manifest["entrypoints"]["workflow"], "workflows/captured-path.json")

    def test_path_to_skill_write_requires_explicit_export_confirmation(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            package_path = root / "blocked.vsk"
            with TestClient(dashboard_server.app) as client:
                response = client.post(
                    "/api/app/path-to-skill/write",
                    json={
                        "summary": {"status": "passed", "workflow": "optimizer_conservative_profile"},
                        "packageId": "community.path-to-skill.optimizer-profile",
                        "exportVsk": True,
                        "packageOutputPath": str(package_path),
                    },
                )

            self.assertEqual(response.status_code, 400)
            self.assertIn("confirmExport=true", response.json()["detail"])
            self.assertFalse(package_path.exists())

    def test_path_to_skill_endpoint_rejects_secrets_and_embedded_private_paths(self) -> None:
        with TestClient(dashboard_server.app) as client:
            secret = client.post(
                "/api/app/path-to-skill/preview",
                json={
                    "summary": {"workflow": "bad", "gatewayToken": "test-token-123456789"},
                    "packageId": "community.path-to-skill.bad-secret",
                },
            )
            private_path = client.post(
                "/api/app/path-to-skill/preview",
                json={
                    "summary": {
                        "workflow": "bad",
                        "notes": "Proof file was C:\\Users\\xiao123\\Desktop\\private-proof.json",
                    },
                    "packageId": "community.path-to-skill.bad-path",
                },
            )

        self.assertEqual(secret.status_code, 400)
        self.assertIn("secret", secret.json()["detail"].lower())
        self.assertEqual(private_path.status_code, 400)
        self.assertIn("absolute path", private_path.json()["detail"].lower())

    def test_roslyn_advanced_skill_requires_full_auto_mode_and_confirmation(self) -> None:
        config = dashboard_server.AGENT_GATEWAY.ensure_config()
        config.enabled = True
        dashboard_server.AGENT_GATEWAY.save_config(config)
        headers = {"Authorization": f"Bearer {config.token}"}

        with TestClient(dashboard_server.app) as client:
            initial = client.get("/api/agent/manifest", headers=headers).json()
            initial_tool_names = {tool["name"] for tool in initial["tools"]}
            self.assertNotIn("vrcforge_roslyn_status", initial_tool_names)
            self.assertNotIn("vrcforge_request_roslyn_advanced", initial_tool_names)
            self.assertNotIn("roslyn-advanced-power", {skill["name"] for skill in initial["skills"]})

            dashboard_server.AGENT_GATEWAY.update_permission_state("roslyn_full_auto", acknowledge_roslyn_risk=True)
            payload = client.get("/api/agent/manifest", headers=headers).json()
            tool_names = {tool["name"] for tool in payload["tools"]}
            self.assertNotIn("vrcforge_request_roslyn_advanced", tool_names)
            self.assertNotIn("vrcforge_roslyn_advanced", {item["name"] for item in payload["writeTargets"]})
            self.assertNotIn("roslyn-advanced-power", {skill["name"] for skill in payload["skills"]})

    @patch("dashboard_server.invoke_unity_mcp")
    def test_generic_unity_write_cannot_bypass_roslyn_gate(self, mock_invoke) -> None:
        result = dashboard_server.unity_mcp_write_sync({"toolName": "vrc_execute_roslyn", "arguments": {"code": "return 42;"}})
        self.assertFalse(result["ok"])
        self.assertIn("Dynamic code execution is not supported", result["error"])
        mock_invoke.assert_not_called()

    @patch("dashboard_server.invoke_unity_mcp")
    def test_generic_unity_write_rejects_non_allowlisted_tools(self, mock_invoke) -> None:
        result = dashboard_server.unity_mcp_write_sync({"toolName": "vrc_import_unitypackage", "arguments": {"packagePath": "Assets/test.unitypackage"}})
        self.assertFalse(result["ok"])
        self.assertIn("static write allowlist", result["error"])
        mock_invoke.assert_not_called()

    @patch("dashboard_server.invoke_unity_mcp")
    @patch("dashboard_server.load_dashboard_settings")
    def test_generic_unity_write_allows_static_vrcforge_tool(self, mock_load_settings, mock_invoke) -> None:
        mock_load_settings.return_value = SimpleNamespace(unity_mcp_timeout_seconds=30)
        mock_invoke.return_value = dashboard_server.McpResult(exit_code=0, stdout="ok", stderr="", payload={"ok": True})

        result = dashboard_server.unity_mcp_write_sync(
            {
                "toolName": "vrc_set_material_shader",
                "arguments": {
                    "materialAssetPath": "Assets/Avatar/Test.mat",
                    "shaderName": "Standard",
                },
            }
        )

        self.assertTrue(result["ok"])
        self.assertEqual(mock_invoke.call_args.args[1], "vrc_set_material_shader")
        self.assertEqual(mock_invoke.call_args.args[2]["shaderName"], "Standard")

    def test_agent_gateway_mcp_lists_codex_debug_loop_tools(self) -> None:
        config = dashboard_server.AGENT_GATEWAY.ensure_config()
        config.enabled = True
        dashboard_server.AGENT_GATEWAY.save_config(config)
        headers = {
            "Authorization": f"Bearer {config.token}",
            "Accept": "application/json, text/event-stream",
            "Content-Type": "application/json",
        }

        with TestClient(dashboard_server.app) as client:
            initialize = client.post(
                "/mcp",
                headers=headers,
                json={
                    "jsonrpc": "2.0",
                    "id": 1,
                    "method": "initialize",
                    "params": {
                        "protocolVersion": "2025-06-18",
                        "capabilities": {},
                        "clientInfo": {"name": "codex-test", "version": "0"},
                    },
                },
            )
            self.assertEqual(initialize.status_code, 200)

            listed = client.post(
                "/mcp",
                headers=headers,
                json={"jsonrpc": "2.0", "id": 2, "method": "tools/list", "params": {}},
            )
            self.assertEqual(listed.status_code, 200)

        tool_names = {tool["name"] for tool in listed.json()["result"]["tools"]}
        self.assertIn("vrcforge_agent_message", tool_names)
        self.assertIn("vrcforge_execute_shell", tool_names)
        self.assertIn("vrcforge_capture_screenshot", tool_names)
        self.assertIn("vrcforge_vision_audit", tool_names)
        self.assertNotIn("vrcforge_roslyn_status", tool_names)
        self.assertIn("vrcforge_get_compile_errors", tool_names)
        self.assertIn("vrcforge_request_apply", tool_names)
        self.assertNotIn("vrcforge_apply_approved", tool_names)
        self.assertNotIn("vrcforge_execute_approved_shell", tool_names)
        self.assertIn("vrcforge_preview_ensure_expression_parameter", tool_names)
        self.assertIn("vrcforge_preview_ensure_expression_menu_control", tool_names)
        self.assertIn("vrcforge_preview_ensure_animator_state", tool_names)
        self.assertIn("vrcforge_preview_create_wardrobe", tool_names)
        self.assertIn("vrcforge_preview_manage_wardrobe", tool_names)
        self.assertIn("vrcforge_preview_add_outfit_part", tool_names)
        self.assertIn("vrcforge_preview_add_modular_avatar_component", tool_names)
        self.assertIn("vrcforge_scan_project_index", tool_names)
        self.assertIn("vrcforge_inspect_outfit_package", tool_names)
        self.assertIn("vrcforge_avatar_encryption_research_report", tool_names)
        self.assertIn("vrcforge_avatar_encryption_scan", tool_names)
        self.assertIn("vrcforge_avatar_encryption_plan", tool_names)
        self.assertIn("vrcforge_avatar_encryption_preview", tool_names)
        self.assertIn("vrcforge_avatar_encryption_addon_status", tool_names)
        self.assertIn("vrcforge_avatar_encryption_liltoon_apply_request", tool_names)
        self.assertIn("vrcforge_avatar_encryption_poiyomi_apply_request", tool_names)
        self.assertIn("vrcforge_avatar_encryption_remove_request", tool_names)
        self.assertNotIn("vrcforge_ensure_expression_parameter", tool_names)
        self.assertNotIn("vrcforge_ensure_expression_menu_control", tool_names)
        self.assertNotIn("vrcforge_ensure_animator_state", tool_names)
        self.assertNotIn("vrcforge_create_wardrobe", tool_names)
        self.assertNotIn("vrcforge_manage_wardrobe", tool_names)
        self.assertNotIn("vrcforge_avatar_encryption_addon_apply", tool_names)
        self.assertNotIn("vrcforge_avatar_encryption_addon_remove", tool_names)

    def test_phase2_unity_tools_are_registered_without_roslyn(self) -> None:
        editor_dir = Path(__file__).resolve().parents[1] / "Assets" / "VRCForge" / "Editor"
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
        old_dynamic_tool = "vrc_" + "execute_" + "roslyn"
        old_dynamic_type = "CSharp" + "Script"
        self.assertNotIn(old_dynamic_tool, combined)
        self.assertNotIn(old_dynamic_type, combined)

    def test_vrcforge_distribution_disables_third_party_execute_code_tool(self) -> None:
        discovery_source = Path("third_party/com.coplaydev.unity-mcp/Editor/Services/ToolDiscoveryService.cs").read_text(encoding="utf-8-sig")
        dispatcher_source = Path("third_party/com.coplaydev.unity-mcp/Editor/Services/Transport/TransportCommandDispatcher.cs").read_text(encoding="utf-8-sig")
        execute_code_source = Path("third_party/com.coplaydev.unity-mcp/Editor/Tools/ExecuteCode.cs").read_text(encoding="utf-8-sig")

        self.assertIn("VrcForgeDisabledToolNames", discovery_source)
        self.assertIn("IsVrcForgeDisabledToolName", discovery_source)
        self.assertIn('"execute_code"', discovery_source)
        self.assertIn('"manage_script"', discovery_source)
        self.assertIn("is disabled in the VRCForge distribution", discovery_source)
        self.assertIn("continue;", discovery_source)
        self.assertIn("ToolDiscoveryService.IsVrcForgeDisabledToolName", dispatcher_source)
        batch_source = Path("third_party/com.coplaydev.unity-mcp/Editor/Tools/BatchExecute.cs").read_text(encoding="utf-8-sig")
        self.assertIn("ToolDiscoveryService.IsVrcForgeDisabledToolName", batch_source)
        self.assertIn("fail closed even when a tool is excluded from discovery metadata", batch_source)
        self.assertIn('[McpForUnityTool("execute_code", AutoRegister = false', execute_code_source)

    def test_safe_backup_restore_source_constrains_manifest_paths(self) -> None:
        source = Path("Assets/VRCForge/Editor/PrefabTools.cs").read_text(encoding="utf-8-sig")
        create_source = Path("Assets/VRCForge/Editor/ConsoleTools.cs").read_text(encoding="utf-8-sig")

        self.assertIn("TryNormalizeManifestRelativePath", source)
        self.assertIn("ResolveContainedPath", source)
        self.assertIn("Path.GetFullPath", source)
        self.assertIn("Manifest path must be relative", source)
        self.assertIn("Manifest path is not a safe relative path", source)
        self.assertIn("ResolveManagedProjectPath", source)
        self.assertIn("ResolveManagedProjectPath", create_source)
        self.assertIn("Library/VRCForge/Backups", create_source)
        self.assertNotIn("Path.Combine(backupPath, backupRelativePath)", source)

    def test_unity_scan_outputs_use_managed_project_path_guard(self) -> None:
        editor_dir = Path("Assets/VRCForge/Editor")
        guard_source = (editor_dir / "VRCForgeOutputPathGuard.cs").read_text(encoding="utf-8-sig")
        self.assertIn("ResolveManagedProjectOutputPath", guard_source)
        self.assertIn("Assets/VRCForge", guard_source)
        for filename in (
            "GameObjectTools.cs",
            "ComponentTools.cs",
            "AssetTools.cs",
            "ShaderMaterialScanner.cs",
            "BlendshapeExporter.cs",
            "AvatarParameterScanner.cs",
            "AvatarControlScanner.cs",
            "WardrobeScanner.cs",
            "AvatarPerformanceTool.cs",
        ):
            source = (editor_dir / filename).read_text(encoding="utf-8-sig")
            self.assertIn("VRCForgeOutputPathGuard.ResolveManagedProjectOutputPath", source)

    def test_wardrobe_scanner_source_exists(self) -> None:
        editor_dir = Path(__file__).resolve().parents[1] / "Assets" / "VRCForge" / "Editor"
        source = (editor_dir / "WardrobeScanner.cs").read_text(encoding="utf-8")
        # Declares the read-only int-exclusive wardrobe detection tool.
        self.assertIn("[McpForUnityTool(", source)
        self.assertIn('name: "vrc_scan_wardrobe"', source)
        self.assertIn("public static object HandleCommand(JObject @params)", source)
        # Captures the menu toggle's int value (the gap the older scanners lacked).
        self.assertIn("control.value", source)
        # Reads per-state Write Defaults, which exclusivity in this style relies on.
        self.assertIn("writeDefaultValues", source)
        # Reconciles the FX layer via Any-State "Equals N" transitions.
        self.assertIn("AnimatorConditionMode.Equals", source)
        self.assertIn("anyStateTransitions", source)
        # Reads which objects each clip turns on vs off.
        self.assertIn("m_IsActive", source)
        # Strict wardrobes must have a selectable outfit object that turns on.
        # Off-only naked-base toggles stay in wardrobeCandidates, not wardrobes.
        self.assertIn("hasSelectableOutfitObject", source)
        self.assertIn("no FX clip turns an outfit object on; off-only toggles are not wardrobes", source)
        self.assertIn("wardrobeCandidateCount", source)
        self.assertIn("wardrobeCandidates", source)
        self.assertIn("looseControlCount", source)
        self.assertIn("looseControls", source)
        self.assertIn("LooksLikeDisableOnlyControl", source)
        self.assertIn("animatorEvidence", source)
        self.assertIn("fxTransitionCount", source)
        self.assertIn("clipWithOnObjectCount", source)
        self.assertIn("menu controls look like disable/off toggles", source)
        # Recurses into SubMenus when toggles overflow the 8-control cap.
        self.assertIn("ControlType.SubMenu", source)
        # Read-only: must NOT mutate the avatar (no FX/menu/param writes, no Undo).
        self.assertNotIn("Undo.", source)
        self.assertNotIn("SetDirty", source)
        self.assertNotIn(".AddLayer(", source)
        self.assertNotIn(".AddState(", source)
        self.assertNotIn("AssetDatabase.CreateAsset", source)

    def test_wardrobe_scan_registered_in_gateway(self) -> None:
        config = dashboard_server.AGENT_GATEWAY.ensure_config()
        config.enabled = True
        dashboard_server.AGENT_GATEWAY.save_config(config)
        headers = {"Authorization": f"Bearer {config.token}"}

        with TestClient(dashboard_server.app) as client:
            payload = client.get("/api/agent/manifest", headers=headers).json()

        tool_names = {tool["name"] for tool in payload["tools"]}
        write_targets = {item["name"] for item in payload["writeTargets"]}
        # Read tool is directly callable, never an approval-gated write target.
        self.assertIn("vrcforge_scan_wardrobe", tool_names)
        self.assertNotIn("vrcforge_scan_wardrobe", write_targets)

    @patch("dashboard_server.invoke_unity_mcp")
    @patch("dashboard_server.load_dashboard_settings")
    def test_wardrobe_scan_forwards_to_unity_tool(self, mock_load_settings, mock_invoke) -> None:
        mock_load_settings.return_value = SimpleNamespace()
        mock_invoke.return_value = dashboard_server.McpResult(
            exit_code=0,
            stdout="ok",
            stderr="",
            payload={"data": {"wardrobeCount": 1, "wardrobes": [{"parameterName": "Clothes"}]}},
        )
        result = dashboard_server.scan_wardrobe_sync({"avatar_path": "Scene/HeroAvatar"})
        self.assertTrue(result["ok"])
        _settings, tool_name, params = mock_invoke.call_args.args
        self.assertEqual(tool_name, "vrc_scan_wardrobe")
        self.assertEqual(params["avatarPath"], "Scene/HeroAvatar")

    @patch("dashboard_server.invoke_unity_mcp")
    @patch("dashboard_server.load_dashboard_settings")
    def test_wardrobe_scan_does_not_reuse_stale_artifact(self, mock_load_settings, mock_invoke) -> None:
        mock_load_settings.return_value = SimpleNamespace()
        mock_invoke.return_value = dashboard_server.McpResult(
            exit_code=0,
            stdout="ok",
            stderr="",
            payload={"data": {"wardrobeCount": 0, "wardrobes": []}},
        )
        with tempfile.TemporaryDirectory() as temp_dir:
            original_artifacts_dir = dashboard_server.DASHBOARD_ARTIFACTS_DIR
            dashboard_server.DASHBOARD_ARTIFACTS_DIR = Path(temp_dir)
            try:
                stale_path = dashboard_server.build_dashboard_artifact_path(
                    "wardrobe",
                    "Scene/HeroAvatar",
                    "json",
                )
                stale_path.write_text(
                    json.dumps({"wardrobeCount": 7, "wardrobes": [{"parameterName": "Stale"}]}),
                    encoding="utf-8",
                )

                result = dashboard_server.scan_wardrobe_sync({"avatar_path": "Scene/HeroAvatar"})

                self.assertEqual(result["wardrobeCount"], 0)
                self.assertEqual(result["wardrobes"], [])
                self.assertTrue(stale_path.exists())
                saved = json.loads(stale_path.read_text(encoding="utf-8"))
                self.assertEqual(saved["wardrobeCount"], 0)
            finally:
                dashboard_server.DASHBOARD_ARTIFACTS_DIR = original_artifacts_dir

    def test_wardrobe_outfit_writer_source_exists(self) -> None:
        editor_dir = Path(__file__).resolve().parents[1] / "Assets" / "VRCForge" / "Editor"
        source = (editor_dir / "WardrobeOutfitWriter.cs").read_text(encoding="utf-8")
        # Declares the int-exclusive add-outfit write tool.
        self.assertIn("[McpForUnityTool(", source)
        self.assertIn('name: "vrc_add_wardrobe_outfit"', source)
        self.assertIn("public static object HandleCommand(JObject @params)", source)
        # Assigns the next free int value and gates an Any-State Equals N transition.
        self.assertIn("AnimatorConditionMode.Equals", source)
        self.assertIn("AddAnyStateTransition", source)
        self.assertIn("AddState", source)
        # Matches the wardrobe's Write Defaults convention, which exclusivity relies on.
        self.assertIn("writeDefaultValues", source)
        # Authors a clip toggling objects on/off and adds a menu toggle (SubMenu overflow).
        self.assertIn("m_IsActive", source)
        self.assertIn("AssetDatabase.CreateAsset", source)
        self.assertIn("CreateOverflowSubMenu", source)
        self.assertIn("owner.controls.RemoveAt", source)
        self.assertIn("VRCExpressionsMenu.MAX_CONTROLS", source)
        self.assertIn("ControlType.Toggle", source)
        self.assertIn("VRCExpressionsMenu.MAX_CONTROLS", source)
        # Full wardrobe menus overflow inside the existing wardrobe menu tree,
        # not onto the avatar root menu.
        self.assertIn("FindBestMenuRef", source)
        self.assertIn("CreateOverflowSubMenu(existingHome.menu", source)
        self.assertIn("FindLastControlIndex", source)
        self.assertIn("var existingClip = st.motion as AnimationClip", source)
        self.assertNotIn("var clip = st.motion as AnimationClip", source)
        # Write tool: must register Undo entries so the checkpoint timeline can roll it back.
        self.assertIn("Undo.", source)
        # Supports a non-mutating preview path.
        self.assertIn("preview", source)

    def test_wardrobe_manager_writer_source_exists(self) -> None:
        editor_dir = Path(__file__).resolve().parents[1] / "Assets" / "VRCForge" / "Editor"
        source = (editor_dir / "WardrobeManagerWriter.cs").read_text(encoding="utf-8")
        self.assertIn("[McpForUnityTool(", source)
        self.assertIn('name: "vrc_manage_wardrobe"', source)
        self.assertIn("public static object HandleCommand(JObject @params)", source)
        for action in (
            "remove_outfit",
            "rename_outfit",
            "reorder_outfits",
            "set_default",
            "delete_wardrobe",
        ):
            self.assertIn(action, source)
        # WD-style wardrobe management must edit the same triangle the scanner reads.
        self.assertIn("RemoveAnyStateTransition", source)
        self.assertIn("RemoveState", source)
        self.assertIn("RemoveLayer", source)
        self.assertIn("VRCExpressionParameters.ValueType.Int", source)
        self.assertIn("ControlType.SubMenu", source)
        self.assertIn("m_IsActive", source)
        # Destructive object/asset removal is supported but opt-in and Undo/checkpoint friendly.
        self.assertIn("deleteObjects", source)
        self.assertIn("DestroyObjectImmediate", source)
        self.assertIn("DeleteAsset", source)
        self.assertIn("Undo.", source)
        self.assertIn("preview", source)
        self.assertIn("private static WardrobeManagePlan BuildPlan", source)
        self.assertIn("private class WardrobeManagePlan", source)
        self.assertNotIn("private static object BuildPlan", source)

    def test_avatar_authoring_primitives_source_exists(self) -> None:
        editor_dir = Path(__file__).resolve().parents[1] / "Assets" / "VRCForge" / "Editor" / "Generic"
        source = (editor_dir / "UnityAvatarAuthoringCrud.cs").read_text(encoding="utf-8")
        self.assertIn("[McpForUnityTool(", source)
        self.assertIn('name: "vrc_ensure_expression_parameter"', source)
        self.assertIn('name: "vrc_ensure_expression_menu_control"', source)
        self.assertIn('name: "vrc_ensure_animator_state"', source)
        self.assertEqual(source.count("public static object HandleCommand(JObject @params)"), 3)
        # Generic primitives cover the scan-detectable int-exclusive wardrobe triangle.
        self.assertIn("VRCExpressionParameters.ValueType.Int", source)
        self.assertIn("AnimatorControllerParameterType.Int", source)
        self.assertIn("AddAnyStateTransition", source)
        self.assertIn("AnimatorConditionMode.Equals", source)
        self.assertIn("ControlType.SubMenu", source)
        self.assertIn("ControlType.Toggle", source)
        self.assertIn("controlValue", source)
        # Can bootstrap missing avatar assets and still uses Undo/preview.
        self.assertIn("CreateAnimatorControllerAtPath", source)
        self.assertIn("descriptor.expressionParameters = asset", source)
        self.assertIn("descriptor.expressionsMenu = asset", source)
        self.assertIn("EnsureMenuHasRoom", source)
        self.assertIn('name = "More"', source)
        self.assertIn("Undo.", source)
        self.assertIn("preview", source)

    def test_add_wardrobe_outfit_registered_in_gateway(self) -> None:
        config = dashboard_server.AGENT_GATEWAY.ensure_config()
        config.enabled = True
        dashboard_server.AGENT_GATEWAY.save_config(config)
        headers = {"Authorization": f"Bearer {config.token}"}

        with TestClient(dashboard_server.app) as client:
            payload = client.get("/api/agent/manifest", headers=headers).json()

        tool_names = {tool["name"] for tool in payload["tools"]}
        write_targets = {item["name"] for item in payload["writeTargets"]}
        # Preview is a directly callable read/plan tool, never an approval-gated write target.
        self.assertIn("vrcforge_preview_add_wardrobe_outfit", tool_names)
        self.assertNotIn("vrcforge_preview_add_wardrobe_outfit", write_targets)
        self.assertIn("vrcforge_preview_manage_wardrobe", tool_names)
        self.assertNotIn("vrcforge_preview_manage_wardrobe", write_targets)
        self.assertIn("vrcforge_preview_ensure_expression_parameter", tool_names)
        self.assertIn("vrcforge_preview_ensure_expression_menu_control", tool_names)
        self.assertIn("vrcforge_preview_ensure_animator_state", tool_names)
        self.assertNotIn("vrcforge_preview_ensure_expression_parameter", write_targets)
        self.assertNotIn("vrcforge_preview_ensure_expression_menu_control", write_targets)
        self.assertNotIn("vrcforge_preview_ensure_animator_state", write_targets)
        self.assertIn("vrcforge_preview_create_wardrobe", tool_names)
        self.assertNotIn("vrcforge_preview_create_wardrobe", write_targets)
        # The write is approval-gated: a writeTarget, never a direct read tool.
        self.assertIn("vrcforge_add_wardrobe_outfit", write_targets)
        self.assertNotIn("vrcforge_add_wardrobe_outfit", tool_names)
        self.assertIn("vrcforge_manage_wardrobe", write_targets)
        self.assertNotIn("vrcforge_manage_wardrobe", tool_names)
        self.assertIn("vrcforge_ensure_expression_parameter", write_targets)
        self.assertIn("vrcforge_ensure_expression_menu_control", write_targets)
        self.assertIn("vrcforge_ensure_animator_state", write_targets)
        self.assertNotIn("vrcforge_ensure_expression_parameter", tool_names)
        self.assertNotIn("vrcforge_ensure_expression_menu_control", tool_names)
        self.assertNotIn("vrcforge_ensure_animator_state", tool_names)
        self.assertIn("vrcforge_create_wardrobe", write_targets)
        self.assertNotIn("vrcforge_create_wardrobe", tool_names)

    def test_authoring_wrappers_parse_string_booleans(self) -> None:
        wardrobe = dashboard_server.build_create_wardrobe_request(
            {"parameterName": "Clothes", "writeDefaults": "false", "saved": "false", "networkSynced": "false"},
            preview=False,
        )
        parameter = dashboard_server.build_ensure_expression_parameter_request(
            {"parameterName": "Clothes", "saved": "false", "networkSynced": "false"},
            preview=False,
        )
        animator = dashboard_server.build_ensure_animator_state_request(
            {"layerName": "Clothes", "stateName": "Default", "parameterName": "Clothes", "writeDefaults": "false"},
            preview=False,
        )

        self.assertFalse(wardrobe["writeDefaults"])
        self.assertFalse(wardrobe["saved"])
        self.assertFalse(wardrobe["networkSynced"])
        self.assertFalse(parameter["saved"])
        self.assertFalse(parameter["networkSynced"])
        self.assertFalse(animator["writeDefaults"])

    @patch("dashboard_server.invoke_unity_mcp")
    @patch("dashboard_server.load_dashboard_settings")
    def test_create_wardrobe_preview_forwards_with_flag(self, mock_load_settings, mock_invoke) -> None:
        mock_load_settings.return_value = SimpleNamespace()
        mock_invoke.return_value = dashboard_server.McpResult(
            exit_code=0,
            stdout="ok",
            stderr="",
            payload={"data": {"preview": True, "plan": {"parameterName": "Clothes"}}},
        )
        result = dashboard_server.preview_create_wardrobe_sync({
            "avatar_path": "Scene/HeroAvatar",
            "parameter_name": "Clothes",
            "menu_name": "Wardrobe",
        })
        self.assertTrue(result["ok"])
        self.assertEqual([call.args[1] for call in mock_invoke.call_args_list], [
            "vrc_ensure_expression_parameter",
            "vrc_ensure_animator_state",
            "vrc_ensure_expression_menu_control",
        ])
        params_by_tool = {call.args[1]: call.args[2] for call in mock_invoke.call_args_list}
        self.assertEqual(params_by_tool["vrc_ensure_expression_parameter"]["avatarPath"], "Scene/HeroAvatar")
        self.assertEqual(params_by_tool["vrc_ensure_expression_parameter"]["parameterName"], "Clothes")
        self.assertEqual(params_by_tool["vrc_ensure_animator_state"]["layerName"], "Clothes")
        self.assertEqual(params_by_tool["vrc_ensure_expression_menu_control"]["menuPath"], "Wardrobe")
        self.assertTrue(all(call.args[2]["preview"] for call in mock_invoke.call_args_list))

    @patch("dashboard_server.invoke_unity_mcp")
    @patch("dashboard_server.load_dashboard_settings")
    def test_create_wardrobe_apply_forwards_without_preview(self, mock_load_settings, mock_invoke) -> None:
        mock_load_settings.return_value = SimpleNamespace()
        mock_invoke.return_value = dashboard_server.McpResult(
            exit_code=0,
            stdout="ok",
            stderr="",
            payload={"data": {"ok": True, "parameterName": "Clothes", "fxLayerName": "Clothes"}},
        )
        result = dashboard_server.create_wardrobe_sync({
            "avatarPath": "Scene/HeroAvatar",
            "parameterName": "Clothes",
        })
        self.assertTrue(result["ok"])
        self.assertEqual([call.args[1] for call in mock_invoke.call_args_list], [
            "vrc_ensure_expression_parameter",
            "vrc_ensure_animator_state",
            "vrc_ensure_expression_menu_control",
        ])
        self.assertFalse(any(call.args[2]["preview"] for call in mock_invoke.call_args_list))

    @patch("dashboard_server.invoke_unity_mcp")
    @patch("dashboard_server.load_dashboard_settings")
    def test_add_wardrobe_outfit_preview_forwards_with_flag(self, mock_load_settings, mock_invoke) -> None:
        mock_load_settings.return_value = SimpleNamespace()
        mock_invoke.return_value = dashboard_server.McpResult(
            exit_code=0,
            stdout="ok",
            stderr="",
            payload={"data": {"preview": True, "plan": {"value": 3}}},
        )
        result = dashboard_server.preview_add_wardrobe_outfit_sync({
            "avatar_path": "Scene/HeroAvatar",
            "parameter_name": "Clothes",
            "outfit_name": "Hoodie",
            "object_paths": ["Outfits/Hoodie"],
        })
        self.assertTrue(result["ok"])
        _settings, tool_name, params = mock_invoke.call_args.args
        self.assertEqual(tool_name, "vrc_add_wardrobe_outfit")
        self.assertEqual(params["avatarPath"], "Scene/HeroAvatar")
        self.assertEqual(params["parameterName"], "Clothes")
        self.assertEqual(params["outfitName"], "Hoodie")
        self.assertEqual(params["objectPaths"], ["Outfits/Hoodie"])
        self.assertTrue(params["preview"])

    @patch("dashboard_server.invoke_unity_mcp")
    @patch("dashboard_server.load_dashboard_settings")
    def test_add_wardrobe_outfit_apply_forwards_without_preview(self, mock_load_settings, mock_invoke) -> None:
        mock_load_settings.return_value = SimpleNamespace()
        mock_invoke.return_value = dashboard_server.McpResult(
            exit_code=0,
            stdout="ok",
            stderr="",
            payload={"data": {"ok": True, "assignedValue": 3, "fxStateName": "Hoodie"}},
        )
        result = dashboard_server.add_wardrobe_outfit_sync({
            "avatarPath": "Scene/HeroAvatar",
            "parameterName": "Clothes",
            "outfitName": "Hoodie",
            "objectPaths": ["Outfits/Hoodie"],
        })
        self.assertTrue(result["ok"])
        self.assertEqual(result["assignedValue"], 3)
        _settings, tool_name, params = mock_invoke.call_args.args
        self.assertEqual(tool_name, "vrc_add_wardrobe_outfit")
        self.assertFalse(params["preview"])

    def test_add_wardrobe_outfit_requires_parameter_and_objects(self) -> None:
        missing_param = dashboard_server.add_wardrobe_outfit_sync({
            "outfit_name": "Hoodie",
            "object_paths": ["Outfits/Hoodie"],
        })
        self.assertFalse(missing_param["ok"])
        missing_objects = dashboard_server.add_wardrobe_outfit_sync({
            "parameter_name": "Clothes",
            "outfit_name": "Hoodie",
        })
        self.assertFalse(missing_objects["ok"])

    def test_outfit_part_writer_source_exists(self) -> None:
        editor_dir = Path(__file__).resolve().parents[1] / "Assets" / "VRCForge" / "Editor"
        source = (editor_dir / "WardrobeOutfitPartWriter.cs").read_text(encoding="utf-8")
        # Declares the int-gated part toggle write tool.
        self.assertIn("[McpForUnityTool(", source)
        self.assertIn('name: "vrc_add_outfit_part"', source)
        self.assertIn("public static object HandleCommand(JObject @params)", source)
        # Off -> On requires (int Equals N) AND (bool true); On -> Off fires on
        # (bool false) OR (int != N), so the toggle is inert unless outfit N is worn.
        self.assertIn("AnimatorConditionMode.Equals", source)
        self.assertIn("AnimatorConditionMode.If", source)
        self.assertIn("AnimatorConditionMode.IfNot", source)
        self.assertIn("AnimatorConditionMode.NotEqual", source)
        # Dedicated FX layer with explicit on/off clips, matching WD convention.
        self.assertIn("AddLayer", source)
        self.assertIn("writeDefaultValues", source)
        self.assertIn("m_IsActive", source)
        self.assertIn("AssetDatabase.CreateAsset", source)
        # Creates the Bool expression parameter and a bound menu toggle.
        self.assertIn("VRCExpressionParameters.ValueType.Bool", source)
        self.assertIn("ControlType.Toggle", source)
        # Write tool: Undo-registered for the checkpoint timeline, with preview path.
        self.assertIn("Undo.", source)
        self.assertIn("preview", source)
        # Apply payload must avoid the gateway unwrap-trap top-level keys.
        for trap_key in ('"data"', '"result"', '"payload"'):
            self.assertNotIn(trap_key + " =", source)

    def test_ma_component_writer_source_exists(self) -> None:
        editor_dir = Path(__file__).resolve().parents[1] / "Assets" / "VRCForge" / "Editor"
        source = (editor_dir / "MAComponentWriter.cs").read_text(encoding="utf-8")
        self.assertIn("[McpForUnityTool(", source)
        self.assertIn('name: "vrc_add_modular_avatar_component"', source)
        self.assertIn("public static object HandleCommand(JObject @params)", source)
        # Reflection-only MA access: no hard compile-time dependency on the package.
        self.assertIn("nadena.dev.modular_avatar.core.", source)
        self.assertNotIn("using nadena", source)
        # Common component aliases are supported.
        for alias in ("MergeArmature", "BoneProxy", "MenuInstaller", "MergeAnimator", "Parameters"):
            self.assertIn(alias, source)
        # Resolves MA's AvatarObjectReference fields via its Set(GameObject) method.
        self.assertIn("AvatarObjectReference", source)
        self.assertIn("referencePath", source)
        # Adds the component with Undo and supports a preview path.
        self.assertIn("Undo.AddComponent", source)
        self.assertIn("TryResolveReference", source)
        self.assertIn("Undo.RevertAllDownToGroup", source)
        self.assertIn("preview", source)

    def test_add_outfit_part_and_ma_registered_in_gateway(self) -> None:
        config = dashboard_server.AGENT_GATEWAY.ensure_config()
        config.enabled = True
        dashboard_server.AGENT_GATEWAY.save_config(config)
        headers = {"Authorization": f"Bearer {config.token}"}

        with TestClient(dashboard_server.app) as client:
            payload = client.get("/api/agent/manifest", headers=headers).json()

        tool_names = {tool["name"] for tool in payload["tools"]}
        write_targets = {item["name"] for item in payload["writeTargets"]}
        wardrobe_skill = next(skill for skill in payload["skills"] if skill["name"] == "wardrobe-control")
        allowed_tools = set(wardrobe_skill["allowedTools"])
        # Previews are directly callable read/plan tools, never approval-gated writes.
        self.assertIn("vrcforge_preview_add_outfit_part", tool_names)
        self.assertNotIn("vrcforge_preview_add_outfit_part", write_targets)
        self.assertIn("vrcforge_preview_add_modular_avatar_component", tool_names)
        self.assertNotIn("vrcforge_preview_add_modular_avatar_component", write_targets)
        # The writes are approval-gated write targets, never direct read tools.
        self.assertIn("vrcforge_add_outfit_part", write_targets)
        self.assertNotIn("vrcforge_add_outfit_part", tool_names)
        self.assertIn("vrcforge_add_modular_avatar_component", write_targets)
        self.assertNotIn("vrcforge_add_modular_avatar_component", tool_names)
        self.assertIn("vrcforge_preview_add_outfit_part", allowed_tools)
        self.assertIn("vrcforge_add_outfit_part", allowed_tools)
        self.assertIn("vrcforge_preview_add_modular_avatar_component", allowed_tools)
        self.assertIn("vrcforge_add_modular_avatar_component", allowed_tools)

    @patch("dashboard_server.invoke_unity_mcp")
    @patch("dashboard_server.load_dashboard_settings")
    def test_add_outfit_part_preview_forwards_with_flag(self, mock_load_settings, mock_invoke) -> None:
        mock_load_settings.return_value = SimpleNamespace()
        mock_invoke.return_value = dashboard_server.McpResult(
            exit_code=0,
            stdout="ok",
            stderr="",
            payload={"data": {"preview": True, "plan": {"partParameterName": "Hat"}}},
        )
        result = dashboard_server.preview_add_outfit_part_sync({
            "avatar_path": "Scene/HeroAvatar",
            "parameter_name": "Clothes",
            "part_name": "Hat",
            "value": 2,
            "object_paths": ["Outfits/Hoodie/Hat"],
        })
        self.assertTrue(result["ok"])
        _settings, tool_name, params = mock_invoke.call_args.args
        self.assertEqual(tool_name, "vrc_add_outfit_part")
        self.assertEqual(params["parameterName"], "Clothes")
        self.assertEqual(params["partName"], "Hat")
        self.assertEqual(params["value"], 2)
        self.assertEqual(params["objectPaths"], ["Outfits/Hoodie/Hat"])
        self.assertTrue(params["preview"])

    @patch("dashboard_server.invoke_unity_mcp")
    @patch("dashboard_server.load_dashboard_settings")
    def test_add_outfit_part_apply_forwards_without_preview(self, mock_load_settings, mock_invoke) -> None:
        mock_load_settings.return_value = SimpleNamespace()
        mock_invoke.return_value = dashboard_server.McpResult(
            exit_code=0,
            stdout="ok",
            stderr="",
            payload={"data": {"ok": True, "assignedPartParameter": "Hat", "fxLayerName": "Hat (part)"}},
        )
        result = dashboard_server.add_outfit_part_sync({
            "avatarPath": "Scene/HeroAvatar",
            "parameterName": "Clothes",
            "partName": "Hat",
            "outfitValue": 2,
            "objectPaths": ["Outfits/Hoodie/Hat"],
        })
        self.assertTrue(result["ok"])
        self.assertEqual(result["assignedPartParameter"], "Hat")
        _settings, tool_name, params = mock_invoke.call_args.args
        self.assertEqual(tool_name, "vrc_add_outfit_part")
        self.assertEqual(params["value"], 2)
        self.assertFalse(params["preview"])

    def test_add_outfit_part_requires_parameter_value_and_objects(self) -> None:
        missing_value = dashboard_server.add_outfit_part_sync({
            "parameter_name": "Clothes",
            "part_name": "Hat",
            "object_paths": ["Outfits/Hoodie/Hat"],
        })
        self.assertFalse(missing_value["ok"])
        missing_objects = dashboard_server.add_outfit_part_sync({
            "parameter_name": "Clothes",
            "part_name": "Hat",
            "value": 2,
        })
        self.assertFalse(missing_objects["ok"])

    @patch("dashboard_server.invoke_unity_mcp")
    @patch("dashboard_server.load_dashboard_settings")
    def test_add_modular_avatar_component_forwards_references_and_fields(self, mock_load_settings, mock_invoke) -> None:
        mock_load_settings.return_value = SimpleNamespace()
        mock_invoke.return_value = dashboard_server.McpResult(
            exit_code=0,
            stdout="ok",
            stderr="",
            payload={"data": {"ok": True, "addedComponent": True}},
        )
        result = dashboard_server.add_modular_avatar_component_sync({
            "game_object_path": "HeroAvatar/Outfits/Hoodie",
            "component_type": "MergeArmature",
            "avatar_path": "Scene/HeroAvatar",
            "references": {"mergeTarget": "Armature"},
            "fields": {"prefix": "", "suffix": ""},
        })
        self.assertTrue(result["ok"])
        _settings, tool_name, params = mock_invoke.call_args.args
        self.assertEqual(tool_name, "vrc_add_modular_avatar_component")
        self.assertEqual(params["componentType"], "MergeArmature")
        self.assertEqual(params["references"], {"mergeTarget": "Armature"})
        self.assertEqual(params["fields"], {"prefix": "", "suffix": ""})
        self.assertFalse(params["preview"])

    def test_add_modular_avatar_component_requires_target_and_type(self) -> None:
        missing_type = dashboard_server.add_modular_avatar_component_sync({
            "game_object_path": "HeroAvatar/Outfits/Hoodie",
        })
        self.assertFalse(missing_type["ok"])
        missing_target = dashboard_server.add_modular_avatar_component_sync({
            "component_type": "MergeArmature",
        })
        self.assertFalse(missing_target["ok"])

    def test_avatar_primitive_crud_source_exists(self) -> None:
        editor_dir = Path(__file__).resolve().parents[1] / "Assets" / "VRCForge" / "Editor" / "Generic"
        source = (editor_dir / "UnityAvatarPrimitiveCrud.cs").read_text(encoding="utf-8")
        for tool_name in (
            "vrc_read_avatar_descriptor",
            "vrc_write_avatar_descriptor",
            "vrc_write_animation_curve",
            "vrc_manage_expression_parameters",
            "vrc_manage_expression_menu",
            "vrc_manage_fx_animator",
        ):
            self.assertIn(f'name: "{tool_name}"', source)
        self.assertIn("Undo.RegisterCompleteObjectUndo", source)
        self.assertIn("AnimationUtility.SetEditorCurve", source)
        self.assertIn("VRCAvatarDescriptor", source)
        self.assertIn("VRCExpressionsMenu", source)
        self.assertIn("AnimatorController", source)

    def test_avatar_primitive_tools_registered_with_safe_exposure(self) -> None:
        config = dashboard_server.AGENT_GATEWAY.ensure_config()
        config.enabled = True
        dashboard_server.AGENT_GATEWAY.save_config(config)
        headers = {"Authorization": f"Bearer {config.token}"}

        with TestClient(dashboard_server.app) as client:
            payload = client.get("/api/agent/manifest", headers=headers).json()

        tool_names = {tool["name"] for tool in payload["tools"]}
        write_targets = {item["name"] for item in payload["writeTargets"]}
        authoring_skill = next(skill for skill in payload["skills"] if skill["name"] == "avatar-authoring-primitives")
        allowed_tools = set(authoring_skill["allowedTools"])
        for name in (
            "vrcforge_read_avatar_descriptor",
            "vrcforge_preview_write_avatar_descriptor",
            "vrcforge_preview_write_animation_curve",
            "vrcforge_preview_manage_expression_parameters",
            "vrcforge_preview_manage_expression_menu",
            "vrcforge_preview_manage_fx_animator",
        ):
            self.assertIn(name, tool_names)
            self.assertNotIn(name, write_targets)
            self.assertIn(name, allowed_tools)
        for name in (
            "vrcforge_write_avatar_descriptor",
            "vrcforge_write_animation_curve",
            "vrcforge_manage_expression_parameters",
            "vrcforge_manage_expression_menu",
            "vrcforge_manage_fx_animator",
        ):
            self.assertIn(name, write_targets)
            self.assertNotIn(name, tool_names)
            self.assertIn(name, allowed_tools)

    @patch("dashboard_server.invoke_unity_mcp")
    @patch("dashboard_server.load_dashboard_settings")
    def test_avatar_primitive_wrappers_forward_to_unity_tools(self, mock_load_settings, mock_invoke) -> None:
        mock_load_settings.return_value = SimpleNamespace()
        mock_invoke.return_value = dashboard_server.McpResult(
            exit_code=0,
            stdout="ok",
            stderr="",
            payload={"data": {"ok": True, "preview": True}},
        )

        calls = [
            (dashboard_server.read_avatar_descriptor_sync, {"avatar_path": "Avatar"}, "vrc_read_avatar_descriptor"),
            (lambda params: dashboard_server.write_avatar_descriptor_sync(params, preview=True), {"avatar_path": "Avatar", "view_position": {"x": 0, "y": 1.5, "z": 0}}, "vrc_write_avatar_descriptor"),
            (lambda params: dashboard_server.write_animation_curve_sync(params, preview=True), {"clip_path": "Assets/Test.anim", "binding_path": "Hat", "component_type": "GameObject", "property_name": "m_IsActive", "constant_float": 1}, "vrc_write_animation_curve"),
            (lambda params: dashboard_server.manage_expression_parameters_sync(params, preview=True), {"avatar_path": "Avatar", "action": "delete", "parameter_name": "Old"}, "vrc_manage_expression_parameters"),
            (lambda params: dashboard_server.manage_expression_menu_sync(params, preview=True), {"avatar_path": "Avatar", "action": "delete", "control_name": "Old"}, "vrc_manage_expression_menu"),
            (lambda params: dashboard_server.manage_fx_animator_sync(params, preview=True), {"avatar_path": "Avatar", "action": "delete_state", "layer_name": "FX", "state_name": "Old"}, "vrc_manage_fx_animator"),
        ]

        for func, params, expected_tool in calls:
            mock_invoke.reset_mock()
            result = func(params)
            self.assertTrue(result["ok"])
            _settings, tool_name, forwarded = mock_invoke.call_args.args
            self.assertEqual(tool_name, expected_tool)
            if expected_tool != "vrc_read_avatar_descriptor":
                self.assertTrue(forwarded["preview"])

        _, _tool_name, curve_params = mock_invoke.call_args.args
        self.assertIn("action", curve_params)

    def test_manage_wardrobe_request_parses_actions_values_and_flags(self) -> None:
        request = dashboard_server.build_manage_wardrobe_request(
            {
                "action": "reorder_outfits",
                "avatarPath": "Scene/HeroAvatar",
                "parameterName": "Clothes",
                "orderValues": "3, 1, 2",
                "deleteObjects": "true",
                "deleteGeneratedAssets": "false",
                "confirmDeleteWardrobe": "true",
            },
            preview=False,
        )
        self.assertEqual(request["action"], "reorder_outfits")
        self.assertEqual(request["avatarPath"], "Scene/HeroAvatar")
        self.assertEqual(request["parameterName"], "Clothes")
        self.assertEqual(request["orderValues"], [3, 1, 2])
        self.assertTrue(request["deleteObjects"])
        self.assertFalse(request["deleteGeneratedAssets"])
        self.assertTrue(request["confirmDeleteWardrobe"])
        self.assertFalse(request["preview"])

    @patch("dashboard_server.invoke_unity_mcp")
    @patch("dashboard_server.load_dashboard_settings")
    def test_manage_wardrobe_preview_forwards_with_flag(self, mock_load_settings, mock_invoke) -> None:
        mock_load_settings.return_value = SimpleNamespace()
        mock_invoke.return_value = dashboard_server.McpResult(
            exit_code=0,
            stdout="ok",
            stderr="",
            payload={"data": {"preview": True, "plan": {"action": "remove_outfit", "targetValues": [3]}}},
        )
        result = dashboard_server.preview_manage_wardrobe_sync({
            "avatar_path": "Scene/HeroAvatar",
            "parameter_name": "Clothes",
            "action": "remove_outfit",
            "target_value": 3,
        })
        self.assertTrue(result["ok"])
        _settings, tool_name, params = mock_invoke.call_args.args
        self.assertEqual(tool_name, "vrc_manage_wardrobe")
        self.assertEqual(params["avatarPath"], "Scene/HeroAvatar")
        self.assertEqual(params["parameterName"], "Clothes")
        self.assertEqual(params["action"], "remove_outfit")
        self.assertEqual(params["targetValue"], 3)
        self.assertTrue(params["preview"])

    @patch("dashboard_server.invoke_unity_mcp")
    @patch("dashboard_server.load_dashboard_settings")
    def test_manage_wardrobe_apply_forwards_without_preview(self, mock_load_settings, mock_invoke) -> None:
        mock_load_settings.return_value = SimpleNamespace()
        mock_invoke.return_value = dashboard_server.McpResult(
            exit_code=0,
            stdout="ok",
            stderr="",
            payload={"data": {"ok": True, "action": "rename_outfit", "targetValues": [3], "newName": "Coat"}},
        )
        result = dashboard_server.manage_wardrobe_sync({
            "avatarPath": "Scene/HeroAvatar",
            "parameterName": "Clothes",
            "action": "rename_outfit",
            "value": 3,
            "newName": "Coat",
        })
        self.assertTrue(result["ok"])
        self.assertEqual(result["action"], "rename_outfit")
        _settings, tool_name, params = mock_invoke.call_args.args
        self.assertEqual(tool_name, "vrc_manage_wardrobe")
        self.assertFalse(params["preview"])
        self.assertEqual(params["value"], 3)
        self.assertEqual(params["newName"], "Coat")

    def test_manage_wardrobe_requires_action_and_parameter(self) -> None:
        missing_action = dashboard_server.manage_wardrobe_sync({"parameterName": "Clothes"})
        self.assertFalse(missing_action["ok"])
        missing_parameter = dashboard_server.manage_wardrobe_sync({"action": "remove_outfit", "targetValue": 3})
        self.assertFalse(missing_parameter["ok"])

    def test_checkpoint_timeline_wraps_approved_write_and_restores(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            project = Path(tmp) / "UnityProject"
            (project / "Assets").mkdir(parents=True)
            (project / "Packages").mkdir()
            (project / "ProjectSettings").mkdir()
            (project / "Assets" / "existing.txt").write_text("before", encoding="utf-8")
            (project / "Packages" / "manifest.json").write_text("{}", encoding="utf-8")
            (project / "ProjectSettings" / "ProjectVersion.txt").write_text("m_EditorVersion: 2022.3", encoding="utf-8")

            for args in (
                ["init"],
                ["config", "user.email", "test@example.invalid"],
                ["config", "user.name", "Test User"],
                ["add", "-A"],
                ["commit", "-m", "initial"],
            ):
                proc = subprocess.run(["git", *args], cwd=str(project), capture_output=True, text=True)
                self.assertEqual(proc.returncode, 0, proc.stderr)

            def write_handler(args: dict) -> dict:
                (Path(args["projectRoot"]) / "Assets" / "generated.txt").write_text("after", encoding="utf-8")
                return {"ok": True, "wrote": "Assets/generated.txt"}

            original_handlers = dict(dashboard_server.AGENT_GATEWAY._write_handlers)
            original_prepare = dashboard_server.AGENT_GATEWAY.checkpoint_prepare_handler
            original_reload = dashboard_server.AGENT_GATEWAY.checkpoint_restore_handler
            try:
                dashboard_server.AGENT_GATEWAY.checkpoint_prepare_handler = lambda _root: {"ok": True}
                dashboard_server.AGENT_GATEWAY.checkpoint_restore_handler = lambda _root: {"ok": True}
                dashboard_server.AGENT_GATEWAY.register_write_handler(
                    "vrcforge_test_checkpoint_write",
                    "Test checkpoint write.",
                    "high",
                    write_handler,
                )
                request = dashboard_server.AGENT_GATEWAY.create_apply_request({
                    "target_tool": "vrcforge_test_checkpoint_write",
                    "arguments": {"projectRoot": str(project)},
                })
                approval_id = request["approval"]["id"]
                dashboard_server.AGENT_GATEWAY.approve(approval_id)
                applied = dashboard_server.AGENT_GATEWAY.apply_approved({"approval_id": approval_id})

                self.assertTrue(applied["ok"])
                self.assertTrue(applied["checkpoint"]["ok"])
                self.assertTrue((project / "Assets" / "generated.txt").exists())

                listed = dashboard_server.AGENT_GATEWAY.list_checkpoints({"projectRoot": str(project)})
                self.assertEqual(listed["count"], 1)
                checkpoint_id = listed["checkpoints"][0]["id"]
                preview = dashboard_server.AGENT_GATEWAY.preview_restore_checkpoint({"checkpointId": checkpoint_id})
                self.assertTrue(preview["ok"])
                self.assertTrue(any("generated.txt" in item for item in preview["workingTreeStatus"] + preview["changedFiles"]))

                restored = dashboard_server.AGENT_GATEWAY.restore_checkpoint({
                    "checkpointId": checkpoint_id,
                    "confirmRestore": True,
                })
                self.assertTrue(restored["ok"])
                self.assertFalse((project / "Assets" / "generated.txt").exists())
            finally:
                dashboard_server.AGENT_GATEWAY._write_handlers = original_handlers
                dashboard_server.AGENT_GATEWAY.checkpoint_prepare_handler = original_prepare
                dashboard_server.AGENT_GATEWAY.checkpoint_restore_handler = original_reload

    def test_archive_checkpoint_restores_non_git_unity_project(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            project = root / "UnityProject"
            (project / "Assets").mkdir(parents=True)
            (project / "Packages").mkdir()
            (project / "ProjectSettings").mkdir()
            existing = project / "Assets" / "existing.txt"
            existing.write_text("before", encoding="utf-8")
            (project / "Packages" / "manifest.json").write_text("{}", encoding="utf-8")
            (project / "ProjectSettings" / "ProjectVersion.txt").write_text("m_EditorVersion: 2022.3", encoding="utf-8")

            gateway = AgentGateway(root / "config" / "agent_gateway.json", root / "audit")
            prepared: list[Path] = []
            reloaded: list[Path] = []
            gateway.checkpoint_prepare_handler = lambda path: prepared.append(path) or {"ok": True}
            gateway.checkpoint_restore_handler = lambda path: reloaded.append(path) or {"ok": True}

            def write_handler(args: dict) -> dict:
                project_root = Path(args["projectRoot"])
                (project_root / "Assets" / "existing.txt").write_text("after", encoding="utf-8")
                (project_root / "Assets" / "generated.txt").write_text("generated", encoding="utf-8")
                return {"ok": True}

            gateway.register_write_handler("vrcforge_test_archive_write", "Archive write", "high", write_handler)
            request = gateway.create_apply_request({
                "target_tool": "vrcforge_test_archive_write",
                "arguments": {"projectRoot": str(project)},
            })
            approval_id = request["approval"]["id"]
            gateway.approve(approval_id)
            applied = gateway.apply_approved({"approval_id": approval_id})

            self.assertTrue(applied["ok"])
            self.assertEqual(applied["checkpoint"]["strategy"], "archive")
            self.assertTrue(Path(applied["checkpoint"]["archivePath"]).is_file())
            self.assertEqual(existing.read_text(encoding="utf-8"), "after")
            self.assertEqual(prepared, [project.resolve()])
            bee_cache = project / "Library" / "Bee"
            script_cache = project / "Library" / "ScriptAssemblies"
            package_cache = project / "Library" / "PackageCache"
            bee_cache.mkdir(parents=True)
            script_cache.mkdir(parents=True)
            package_cache.mkdir(parents=True)
            (bee_cache / "stale-inputdata.json").write_text("Packages/com.deleted.shader", encoding="utf-8")
            (script_cache / "stale.dll").write_text("stale", encoding="utf-8")
            (package_cache / "stale-package").write_text("stale", encoding="utf-8")

            checkpoint_id = applied["checkpoint"]["id"]
            preview = gateway.preview_restore_checkpoint({"checkpointId": checkpoint_id})
            self.assertTrue(preview["ok"])
            self.assertTrue(any("existing.txt" in item for item in preview["changedFiles"]))
            self.assertTrue(any("generated.txt" in item for item in preview["changedFiles"]))

            restored = gateway.restore_checkpoint({"checkpointId": checkpoint_id, "confirmRestore": True})
            self.assertTrue(restored["ok"])
            self.assertEqual(existing.read_text(encoding="utf-8"), "before")
            self.assertFalse((project / "Assets" / "generated.txt").exists())
            self.assertFalse(bee_cache.exists())
            self.assertFalse(script_cache.exists())
            self.assertFalse(package_cache.exists())
            self.assertFalse(restored["unityCacheCleanup"]["skipped"])
            self.assertIn(str(bee_cache.resolve()), restored["unityCacheCleanup"]["deleted"])
            self.assertIn(str(script_cache.resolve()), restored["unityCacheCleanup"]["deleted"])
            self.assertIn(str(package_cache.resolve()), restored["unityCacheCleanup"]["deleted"])
            self.assertEqual(reloaded, [project.resolve()])

    def test_archive_checkpoint_rejects_mutable_archive_path_outside_store(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            project = root / "UnityProject"
            (project / "Assets").mkdir(parents=True)
            (project / "Packages").mkdir()
            (project / "ProjectSettings").mkdir()
            (project / "Assets" / "existing.txt").write_text("before", encoding="utf-8")
            (project / "Packages" / "manifest.json").write_text("{}", encoding="utf-8")
            (project / "ProjectSettings" / "ProjectVersion.txt").write_text("m_EditorVersion: 2022.3", encoding="utf-8")

            gateway = AgentGateway(root / "config" / "agent_gateway.json", root / "audit")
            checkpoint = gateway._create_pre_write_checkpoint(  # noqa: SLF001 - regression covers checkpoint metadata handling.
                {"id": "approval-test", "targetTool": "vrcforge_test_archive_write"},
                {"projectRoot": str(project)},
            )
            self.assertIsNotNone(checkpoint)
            self.assertTrue(checkpoint["ok"])

            external_zip = root / "outside.zip"
            with zipfile.ZipFile(external_zip, "w") as archive:
                archive.writestr("Assets/existing.txt", "outside")
            records = [json.loads(line) for line in gateway.checkpoint_log_path.read_text(encoding="utf-8").splitlines()]
            records[-1]["archivePath"] = str(external_zip)
            gateway.checkpoint_log_path.write_text(
                "\n".join(json.dumps(record, ensure_ascii=False) for record in records) + "\n",
                encoding="utf-8",
            )

            preview = gateway.preview_restore_checkpoint({"checkpointId": checkpoint["id"]})
            restored = gateway.restore_checkpoint({"checkpointId": checkpoint["id"], "confirmRestore": True})

            self.assertFalse(preview["ok"])
            self.assertIn("outside configured storage", preview["error"])
            self.assertFalse(restored["ok"])
            self.assertEqual((project / "Assets" / "existing.txt").read_text(encoding="utf-8"), "before")

    def test_local_state_checkpoint_rejects_mutable_archive_path_outside_store(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            gateway = AgentGateway(root / "config" / "agent_gateway.json", root / "audit")
            skill_dir = gateway.user_skills_dir / "avatar-review"
            skill_dir.mkdir(parents=True)
            (skill_dir / "SKILL.md").write_text("before", encoding="utf-8")

            checkpoint = gateway._create_pre_write_checkpoint(  # noqa: SLF001 - regression covers checkpoint metadata handling.
                {"id": "approval-local", "targetTool": "vrcforge_import_skill_package"},
                {},
            )
            self.assertIsNotNone(checkpoint)
            self.assertTrue(checkpoint["ok"])

            external_zip = root / "outside-local.zip"
            with zipfile.ZipFile(external_zip, "w") as archive:
                archive.writestr("skills/avatar-review/SKILL.md", "outside")
            records = [json.loads(line) for line in gateway.checkpoint_log_path.read_text(encoding="utf-8").splitlines()]
            records[-1]["archivePath"] = str(external_zip)
            gateway.checkpoint_log_path.write_text(
                "\n".join(json.dumps(record, ensure_ascii=False) for record in records) + "\n",
                encoding="utf-8",
            )

            preview = gateway.preview_restore_checkpoint({"checkpointId": checkpoint["id"]})
            restored = gateway.restore_checkpoint({"checkpointId": checkpoint["id"], "confirmRestore": True})

            self.assertFalse(preview["ok"])
            self.assertIn("outside configured storage", preview["error"])
            self.assertFalse(restored["ok"])
            self.assertEqual((skill_dir / "SKILL.md").read_text(encoding="utf-8"), "before")

    def test_checkpoint_rollback_coverage_audit_tracks_ma_vrcf_ndmf_state(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            project = root / "UnityProject"
            (project / "Assets" / "Scenes").mkdir(parents=True)
            (project / "Assets" / "Prefabs").mkdir()
            (project / "Packages").mkdir()
            (project / "ProjectSettings").mkdir()
            scene = project / "Assets" / "Scenes" / "Avatar.unity"
            prefab = project / "Assets" / "Prefabs" / "Outfit.prefab"
            generated = project / "Assets" / "VRCForge" / "Generated" / "RollbackAudit" / "generated.anim"
            manifest = project / "Packages" / "manifest.json"
            lock = project / "Packages" / "packages-lock.json"
            scene.write_text("before scene with MA component", encoding="utf-8")
            prefab.write_text("before prefab with VRCF component", encoding="utf-8")
            manifest.write_text(
                json.dumps(
                    {
                        "dependencies": {
                            "nadena.dev.modular-avatar": "1.17.1",
                            "com.vrcfury.vrcfury": "1.1334.0",
                            "nadena.dev.ndmf": "1.13.1",
                        }
                    }
                ),
                encoding="utf-8",
            )
            lock.write_text(
                json.dumps(
                    {
                        "dependencies": {
                            "nadena.dev.modular-avatar": {"version": "1.17.1"},
                            "com.vrcfury.vrcfury": {"version": "1.1334.0"},
                            "nadena.dev.ndmf": {"version": "1.13.1"},
                        }
                    }
                ),
                encoding="utf-8",
            )
            (project / "ProjectSettings" / "ProjectVersion.txt").write_text("m_EditorVersion: 2022.3", encoding="utf-8")

            gateway = AgentGateway(root / "config" / "agent_gateway.json", root / "audit")
            gateway.checkpoint_prepare_handler = lambda _path: {"ok": True}
            gateway.checkpoint_restore_handler = lambda _path: {"ok": True}

            def write_handler(args: dict) -> dict:
                project_root = Path(args["projectRoot"])
                (project_root / "Assets" / "Scenes" / "Avatar.unity").write_text("after scene", encoding="utf-8")
                (project_root / "Assets" / "Prefabs" / "Outfit.prefab").write_text("after prefab", encoding="utf-8")
                generated.parent.mkdir(parents=True)
                generated.write_text("generated", encoding="utf-8")
                manifest.write_text(
                    json.dumps({"dependencies": {"nadena.dev.modular-avatar": "1.17.1"}}),
                    encoding="utf-8",
                )
                package_cache = project_root / "Library" / "PackageCache"
                bee_cache = project_root / "Library" / "Bee"
                script_cache = project_root / "Library" / "ScriptAssemblies"
                package_cache.mkdir(parents=True)
                bee_cache.mkdir(parents=True)
                script_cache.mkdir(parents=True)
                (package_cache / "com.vrcfury.vrcfury@1.1334.0").write_text("stale", encoding="utf-8")
                (bee_cache / "inputdata.json").write_text("stale", encoding="utf-8")
                (script_cache / "Assembly-CSharp.dll").write_text("stale", encoding="utf-8")
                return {"ok": True}

            gateway.register_write_handler("vrcforge_test_ma_vrcf_rollback", "MA/VRCF rollback", "high", write_handler)
            request = gateway.create_apply_request({
                "target_tool": "vrcforge_test_ma_vrcf_rollback",
                "arguments": {"projectRoot": str(project)},
            })
            approval_id = request["approval"]["id"]
            gateway.approve(approval_id)
            applied = gateway.apply_approved({"approval_id": approval_id})

            self.assertTrue(applied["ok"])
            checkpoint_audit = applied["checkpoint"]["rollbackCoverageAudit"]
            self.assertEqual(checkpoint_audit["schema"], "vrcforge.rollback_coverage_audit.v1")
            checkpoint_checks = {item["id"]: item for item in checkpoint_audit["checks"]}
            self.assertEqual(checkpoint_checks["scene_prefab_component_state"]["status"], "covered")
            self.assertEqual(checkpoint_checks["packages_manifest"]["status"], "covered")
            frameworks = checkpoint_checks["packages_manifest"]["frameworkPackages"]["packages"]
            self.assertTrue(frameworks["modular_avatar"]["detected"])
            self.assertTrue(frameworks["vrcfury"]["detected"])
            self.assertTrue(frameworks["ndmf"]["detected"])

            preview = gateway.preview_restore_checkpoint({"checkpointId": applied["checkpoint"]["id"]})
            self.assertTrue(preview["ok"])
            self.assertEqual(preview["rollbackCoverageAudit"]["phase"], "preview")
            preview_checks = {item["id"]: item for item in preview["rollbackCoverageAudit"]["checks"]}
            preview_frameworks = preview_checks["packages_manifest"]["frameworkPackages"]["packages"]
            self.assertTrue(preview_frameworks["vrcfury"]["detected"])

            restored = gateway.restore_checkpoint({"checkpointId": applied["checkpoint"]["id"], "confirmRestore": True})

            self.assertTrue(restored["ok"])
            self.assertEqual(scene.read_text(encoding="utf-8"), "before scene with MA component")
            self.assertEqual(prefab.read_text(encoding="utf-8"), "before prefab with VRCF component")
            self.assertFalse(generated.exists())
            restored_manifest = json.loads(manifest.read_text(encoding="utf-8"))
            self.assertIn("com.vrcfury.vrcfury", restored_manifest["dependencies"])
            self.assertFalse((project / "Library" / "PackageCache").exists())
            self.assertFalse((project / "Library" / "Bee").exists())
            self.assertFalse((project / "Library" / "ScriptAssemblies").exists())
            restore_audit = restored["rollbackCoverageAudit"]
            restore_checks = {item["id"]: item for item in restore_audit["checks"]}
            self.assertEqual(restore_audit["gateStatus"], "todo")
            self.assertEqual(restore_checks["package_cache_generated_folders"]["status"], "passed")
            self.assertEqual(restore_checks["unity_reload_after_restore"]["status"], "passed")
            self.assertEqual(restore_checks["validation_after_restore"]["status"], "todo")
            self.assertTrue(any(item["id"] == "run_post_restore_validation" for item in restore_audit["todos"]))

    def test_skill_package_write_uses_local_state_checkpoint_and_restore(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            package = root / "avatar-review.vsk"
            source = root / "source"
            source.mkdir()
            (source / "manifest.json").write_text(
                json.dumps(
                    {
                        "id": "community.avatar-review",
                        "name": "Avatar Review Package",
                        "skill_name": "avatar-review",
                        "version": "1.0.0",
                        "author": "Unit Test",
                        "description": "Dashboard skill package fixture.",
                        "min_vrcforge_version": "0.5.0",
                        "permissions": ["read_project"],
                        "entrypoints": {"skill": "SKILL.md"},
                    }
                ),
                encoding="utf-8",
            )
            (source / "SKILL.md").write_text(
                "---\n"
                "name: avatar-review\n"
                "title: Avatar Review\n"
                "description: Imported package skill.\n"
                "allowed-tools:\n"
                "  - vrcforge_health\n"
                "entrypoint-tool: vrcforge_health\n"
                "---\n"
                "Inspect project state before edits.\n",
                encoding="utf-8",
            )
            SkillPackageService(root / "build-store", vrcforge_version="0.5.1").export_dev(source, package)

            gateway = AgentGateway(root / "app" / "config" / "agent_gateway.json", root / "audit")
            original_gateway = dashboard_server.AGENT_GATEWAY
            try:
                dashboard_server.AGENT_GATEWAY = gateway
                dashboard_server.register_agent_gateway_tools()
                request = gateway.create_apply_request(
                    {
                        "target_tool": "vrcforge_import_skill_package",
                        "arguments": {"packagePath": str(package)},
                    }
                )
                gateway.approve(request["approval"]["id"])
                applied = gateway.apply_approved({"approval_id": request["approval"]["id"]})

                self.assertTrue(applied["ok"])
                checkpoint = applied["checkpoint"]
                self.assertEqual(checkpoint["strategy"], "local_state_archive")
                self.assertEqual(checkpoint["pathspecs"], ["skill-packages", "skills"])
                self.assertTrue((gateway.user_skills_dir / "avatar-review" / "SKILL.md").is_file())
                self.assertTrue((gateway.user_constraints_path.parent / "skill-packages" / "community.avatar-review").is_dir())
                preview = gateway.preview_restore_checkpoint({"checkpointId": checkpoint["id"]})
                self.assertTrue(preview["ok"])
                self.assertTrue(any("avatar-review" in item for item in preview["workingTreeStatus"] + preview["changedFiles"]))

                restored = gateway.restore_checkpoint({"checkpointId": checkpoint["id"], "confirmRestore": True})

                self.assertTrue(restored["ok"])
                self.assertEqual(restored["status"], "restored")
                self.assertFalse((gateway.user_skills_dir / "avatar-review").exists())
                self.assertFalse((gateway.user_constraints_path.parent / "skill-packages").exists())
                audit = restored["rollbackCoverageAudit"]
                checks = {item["id"]: item for item in audit["checks"]}
                self.assertEqual(checks["local_skill_package_store"]["status"], "covered")
                self.assertEqual(checks["local_projected_user_skills"]["status"], "covered")
            finally:
                dashboard_server.AGENT_GATEWAY = original_gateway
                dashboard_server.register_agent_gateway_tools()

    def test_failed_write_after_checkpoint_returns_checkpoint_for_rollback(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            project = root / "UnityProject"
            (project / "Assets").mkdir(parents=True)
            (project / "Packages").mkdir()
            (project / "ProjectSettings").mkdir()
            (project / "Assets" / "existing.txt").write_text("before", encoding="utf-8")
            (project / "Packages" / "manifest.json").write_text("{}", encoding="utf-8")
            (project / "ProjectSettings" / "ProjectVersion.txt").write_text("m_EditorVersion: 2022.3", encoding="utf-8")

            gateway = AgentGateway(root / "config" / "agent_gateway.json", root / "audit")
            gateway.checkpoint_prepare_handler = lambda _path: {"ok": True}

            def failing_write(args: dict) -> dict:
                project_root = Path(args["projectRoot"])
                (project_root / "Assets" / "generated-before-fail.txt").write_text("generated", encoding="utf-8")
                raise RuntimeError("Unity MCP disconnected after checkpoint")

            gateway.register_write_handler("vrcforge_test_failing_write", "Failing write", "high", failing_write)
            request = gateway.create_apply_request({
                "target_tool": "vrcforge_test_failing_write",
                "arguments": {"projectRoot": str(project)},
            })
            approval_id = request["approval"]["id"]
            gateway.approve(approval_id)
            applied = gateway.apply_approved({"approval_id": approval_id})

            self.assertFalse(applied["ok"])
            self.assertEqual(applied["status"], "failed")
            self.assertIn("Unity MCP disconnected", applied["error"])
            self.assertTrue(applied["checkpoint"]["ok"])
            self.assertEqual(applied["approval"]["checkpoint"]["id"], applied["checkpoint"]["id"])

            restored = gateway.restore_checkpoint({"checkpointId": applied["checkpoint"]["id"], "confirmRestore": True})
            self.assertTrue(restored["ok"])
            self.assertFalse((project / "Assets" / "generated-before-fail.txt").exists())

    def test_audit_log_approval_is_not_executable_after_memory_loss(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            gateway = AgentGateway(root / "config" / "agent_gateway.json", root / "audit")
            called: list[dict] = []

            def write_handler(args: dict) -> dict:
                called.append(args)
                return {"ok": True}

            gateway.register_write_handler("vrcforge_test_audit_write", "Audit write", "high", write_handler)
            request = gateway.create_apply_request({
                "target_tool": "vrcforge_test_audit_write",
                "arguments": {
                    "projectRoot": str(root / "UnityProject"),
                    "repository": "https://example.com/vpm/index.json",
                    "nested": {"key": "value"},
                },
            })
            approval_id = request["approval"]["id"]

            gateway._approvals.clear()

            with self.assertRaises(AgentGatewayError) as approve_error:
                gateway.approve(approval_id)
            self.assertEqual(approve_error.exception.status_code, 404)

            with self.assertRaises(AgentGatewayError) as apply_error:
                gateway.apply_approved({"approval_id": approval_id})
            self.assertEqual(apply_error.exception.status_code, 404)
            self.assertEqual(called, [])

    def test_checkpoint_blocks_write_when_project_root_is_missing(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            gateway = AgentGateway(root / "config" / "agent_gateway.json", root / "audit")
            called: list[dict] = []

            def write_handler(args: dict) -> dict:
                called.append(args)
                return {"ok": True}

            gateway.register_write_handler("vrcforge_test_missing_root_write", "Missing root write", "high", write_handler)
            request = gateway.create_apply_request({
                "target_tool": "vrcforge_test_missing_root_write",
                "arguments": {"avatar_path": "Scene/Avatar"},
            })
            approval_id = request["approval"]["id"]
            gateway.approve(approval_id)
            applied = gateway.apply_approved({"approval_id": approval_id})

            self.assertFalse(applied["ok"])
            self.assertEqual(applied["status"], "failed")
            self.assertIn("No Unity project root", applied["error"])
            self.assertTrue(applied["checkpoint"]["blocking"])
            self.assertEqual(called, [])

    def test_checkpoint_archive_restore_succeeds_when_unity_reload_fails(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            project = root / "UnityProject"
            (project / "Assets").mkdir(parents=True)
            (project / "Packages").mkdir()
            (project / "ProjectSettings").mkdir()
            existing = project / "Assets" / "existing.txt"
            existing.write_text("before", encoding="utf-8")
            (project / "Packages" / "manifest.json").write_text("{}", encoding="utf-8")
            (project / "ProjectSettings" / "ProjectVersion.txt").write_text("m_EditorVersion: 2022.3", encoding="utf-8")

            gateway = AgentGateway(root / "config" / "agent_gateway.json", root / "audit")
            gateway.checkpoint_prepare_handler = lambda _path: {"ok": True}
            gateway.checkpoint_restore_handler = lambda _path: {"ok": False, "error": "Unity bridge unavailable"}

            def write_handler(args: dict) -> dict:
                project_root = Path(args["projectRoot"])
                (project_root / "Assets" / "existing.txt").write_text("after", encoding="utf-8")
                (project_root / "Assets" / "generated.txt").write_text("generated", encoding="utf-8")
                return {"ok": True}

            gateway.register_write_handler("vrcforge_test_reload_warning", "Reload warning write", "high", write_handler)
            request = gateway.create_apply_request({
                "target_tool": "vrcforge_test_reload_warning",
                "arguments": {"projectRoot": str(project)},
            })
            approval_id = request["approval"]["id"]
            gateway.approve(approval_id)
            applied = gateway.apply_approved({"approval_id": approval_id})

            self.assertTrue(applied["ok"])
            restored = gateway.restore_checkpoint({"checkpointId": applied["checkpoint"]["id"], "confirmRestore": True})

            self.assertTrue(restored["ok"])
            self.assertEqual(restored["status"], "restored_with_unity_reload_warning")
            self.assertIn("Unity bridge unavailable", restored["unityReloadWarning"])
            self.assertEqual(existing.read_text(encoding="utf-8"), "before")
            self.assertFalse((project / "Assets" / "generated.txt").exists())

    def test_checkpoint_tools_registered_with_restore_as_write_target(self) -> None:
        config = dashboard_server.AGENT_GATEWAY.ensure_config()
        config.enabled = True
        dashboard_server.AGENT_GATEWAY.save_config(config)
        headers = {"Authorization": f"Bearer {config.token}"}

        with TestClient(dashboard_server.app) as client:
            payload = client.get("/api/agent/manifest", headers=headers).json()

        tool_names = {tool["name"] for tool in payload["tools"]}
        write_targets = {item["name"] for item in payload["writeTargets"]}
        self.assertIn("vrcforge_list_checkpoints", tool_names)
        self.assertIn("vrcforge_preview_restore_checkpoint", tool_names)
        self.assertNotIn("vrcforge_restore_checkpoint", tool_names)
        self.assertIn("vrcforge_restore_checkpoint", write_targets)
        self.assertIn("vrcforge_unity_mcp_write", write_targets)

    def test_write_targets_publish_rollback_policy(self) -> None:
        config = dashboard_server.AGENT_GATEWAY.ensure_config()
        config.enabled = True
        dashboard_server.AGENT_GATEWAY.save_config(config)
        headers = {"Authorization": f"Bearer {config.token}"}

        with TestClient(dashboard_server.app) as client:
            manifest = client.get("/api/agent/manifest", headers=headers).json()
            registry = client.get("/api/app/tools/registry").json()

        targets = {item["name"]: item for item in manifest["writeTargets"]}
        self.assertGreater(len(targets), 10)
        for name, target in targets.items():
            policy = target.get("rollbackPolicy")
            self.assertIsInstance(policy, dict, name)
            self.assertEqual(policy["schema"], "vrcforge.write_rollback_policy.v1")
            self.assertTrue(policy["required"], name)
            self.assertEqual(policy["restoreTool"], "vrcforge_restore_checkpoint")
            self.assertEqual(policy["coverageAudit"], "vrcforge.rollback_coverage_audit.v1")

        unity_policy = targets["vrcforge_add_modular_avatar_component"]["rollbackPolicy"]
        self.assertEqual(unity_policy["kind"], "unity_project_checkpoint")
        self.assertEqual(unity_policy["checkpointScope"], ["Assets", "Packages", "ProjectSettings"])
        self.assertIn("Modular Avatar", unity_policy["ecosystemCoverageRequired"])
        self.assertIn("VRCFury", unity_policy["ecosystemCoverageRequired"])
        self.assertIn("NDMF", unity_policy["ecosystemCoverageRequired"])

        package_policy = targets["vrcforge_import_skill_package"]["rollbackPolicy"]
        self.assertEqual(package_policy["kind"], "local_state_archive")
        self.assertEqual(package_policy["checkpointScope"], ["skill-packages", "skills"])

        restore_policy = targets["vrcforge_restore_checkpoint"]["rollbackPolicy"]
        self.assertEqual(restore_policy["kind"], "checkpoint_restore")
        self.assertFalse(restore_policy["preWriteCheckpointRequired"])

        registry_targets = {item["name"]: item for item in registry["tools"] if item.get("source") == "write-target"}
        self.assertEqual(
            registry_targets["vrcforge_import_skill_package"]["rollbackPolicy"],
            package_policy,
        )

    def test_checkpoint_recovery_unity_tools_save_and_reload_scenes(self) -> None:
        source = (
            Path(__file__).resolve().parents[1]
            / "Assets"
            / "VRCForge"
            / "Editor"
            / "CheckpointRecoveryTool.cs"
        ).read_text(encoding="utf-8")
        self.assertIn('name: "vrc_prepare_checkpoint"', source)
        self.assertIn('name: "vrc_reload_after_checkpoint_restore"', source)
        self.assertIn("EditorSceneManager.SaveOpenScenes", source)
        self.assertIn("EditorSceneManager.OpenScene", source)
        self.assertIn("NewSceneSetup.EmptyScene", source)
        self.assertIn("EditorSceneManager.CloseScene(scene, true)", source)
        self.assertIn("ForceSynchronousImport", source)
        self.assertIn("AssetDatabase.Refresh", source)

    def test_refresh_asset_database_tool_can_resolve_packages(self) -> None:
        source = (
            Path(__file__).resolve().parents[1]
            / "Assets"
            / "VRCForge"
            / "Editor"
            / "OutfitPackageImporter.cs"
        ).read_text(encoding="utf-8")

        self.assertIn("UnityEditor.PackageManager", source)
        self.assertIn("resolvePackages", source)
        self.assertIn("Client.Resolve()", source)
        self.assertIn("packageResolve", source)
        self.assertIn('status = "started"', source)

    def test_setup_outfit_uses_modular_avatar_public_api(self) -> None:
        source = (
            Path(__file__).resolve().parents[1]
            / "Assets"
            / "VRCForge"
            / "Editor"
            / "SetupOutfitTool.cs"
        ).read_text(encoding="utf-8")
        self.assertIn("nadena.dev.modular_avatar.core.editor.SetupOutfit", source)
        self.assertIn('"SetupOutfitUI"', source)
        self.assertIn("method.Invoke(null, new object[] { outfit })", source)
        self.assertIn("ESOErrorWindow", source)
        self.assertIn("suppressField?.SetValue(null, true)", source)
        self.assertNotIn("EditorApplication.ExecuteMenuItem(", source)

    def test_setup_outfit_saves_target_scene_only(self) -> None:
        source = (
            Path(__file__).resolve().parents[1]
            / "Assets"
            / "VRCForge"
            / "Editor"
            / "SetupOutfitTool.cs"
        ).read_text(encoding="utf-8")
        self.assertIn("SaveTargetScene(outfit.gameObject.scene)", source)
        self.assertIn("EditorSceneManager.SaveScene(scene)", source)
        self.assertNotIn("EditorSceneManager.SaveOpenScenes", source)

    def test_setup_outfit_write_uses_job_polling(self) -> None:
        source = (Path(__file__).resolve().parents[1] / "dashboard_server.py").read_text(encoding="utf-8")
        start = source.index("def setup_outfit_sync")
        end = source.index("\ndef _coerce_path_list", start)
        setup_source = source[start:end]

        self.assertIn("wait_for_setup_outfit_job(settings, params, payload)", setup_source)
        self.assertNotIn("unity_mcp_timeout_seconds = max(settings.unity_mcp_timeout_seconds, 120)", setup_source)

    @patch("dashboard_server.invoke_unity_mcp")
    @patch("dashboard_server.load_dashboard_settings")
    def test_setup_outfit_sync_polls_pending_job_to_completion(self, mock_load_settings, mock_invoke) -> None:
        mock_load_settings.return_value = SimpleNamespace(unity_mcp_timeout_seconds=30)
        mock_invoke.side_effect = [
            dashboard_server.McpResult(
                exit_code=0,
                stdout="ok",
                stderr="",
                payload={"data": {"ok": True, "pending": True, "status": "pending", "jobId": "job-1"}},
            ),
            dashboard_server.McpResult(
                exit_code=0,
                stdout="ok",
                stderr="",
                payload={"data": {"ok": True, "pending": True, "status": "running", "jobId": "job-1"}},
            ),
            dashboard_server.McpResult(
                exit_code=0,
                stdout="ok",
                stderr="",
                payload={"data": {"ok": True, "pending": False, "status": "completed", "jobId": "job-1", "sceneSaved": True}},
            ),
        ]

        result = dashboard_server.setup_outfit_sync(
            {
                "avatarPath": "Avatar",
                "outfitPath": "Avatar/Hoodie",
                "setupOutfitPollIntervalSeconds": 0,
                "setupOutfitPollTimeoutSeconds": 1,
            }
        )

        self.assertTrue(result["ok"])
        self.assertEqual(result["status"], "completed")
        self.assertEqual([call.args[2] for call in mock_invoke.call_args_list], [
            {"avatarPath": "Avatar", "outfitPath": "Avatar/Hoodie", "confirmSetup": True, "saveScene": True},
            {"jobId": "job-1"},
            {"jobId": "job-1"},
        ])

    @patch("dashboard_server.invoke_unity_mcp")
    @patch("dashboard_server.load_dashboard_settings")
    def test_setup_outfit_sync_returns_job_error(self, mock_load_settings, mock_invoke) -> None:
        mock_load_settings.return_value = SimpleNamespace(unity_mcp_timeout_seconds=30)
        mock_invoke.side_effect = [
            dashboard_server.McpResult(
                exit_code=0,
                stdout="ok",
                stderr="",
                payload={"data": {"ok": True, "pending": True, "status": "pending", "jobId": "job-2"}},
            ),
            dashboard_server.McpResult(
                exit_code=0,
                stdout="ok",
                stderr="",
                payload={"data": {"ok": False, "pending": False, "status": "error", "jobId": "job-2", "error": "MA failed"}},
            ),
        ]

        result = dashboard_server.setup_outfit_sync(
            {
                "avatarPath": "Avatar",
                "outfitPath": "Avatar/Hoodie",
                "setupOutfitPollIntervalSeconds": 0,
                "setupOutfitPollTimeoutSeconds": 1,
            }
        )

        self.assertFalse(result["ok"])
        self.assertEqual(result["status"], "error")
        self.assertEqual(result["error"], "MA failed")
        self.assertEqual(mock_invoke.call_count, 2)

    @patch("dashboard_server.invoke_unity_mcp")
    @patch("dashboard_server.load_dashboard_settings")
    def test_setup_outfit_sync_returns_timeout_for_unfinished_job(self, mock_load_settings, mock_invoke) -> None:
        mock_load_settings.return_value = SimpleNamespace(unity_mcp_timeout_seconds=30)
        mock_invoke.return_value = dashboard_server.McpResult(
            exit_code=0,
            stdout="ok",
            stderr="",
            payload={"data": {"ok": True, "pending": True, "status": "pending", "jobId": "job-3"}},
        )

        result = dashboard_server.setup_outfit_sync(
            {
                "avatarPath": "Avatar",
                "outfitPath": "Avatar/Hoodie",
                "setupOutfitPollTimeoutSeconds": 0,
            }
        )

        self.assertFalse(result["ok"])
        self.assertEqual(result["status"], "timeout")
        self.assertEqual(result["jobId"], "job-3")
        self.assertEqual(mock_invoke.call_count, 1)

    def test_app_approval_executes_non_shell_write_handler(self) -> None:
        temp_project = tempfile.TemporaryDirectory()
        self.addCleanup(temp_project.cleanup)
        project = Path(temp_project.name) / "UnityProject"
        (project / "Assets").mkdir(parents=True)
        (project / "Packages").mkdir()
        (project / "ProjectSettings").mkdir()
        (project / "Packages" / "manifest.json").write_text("{}", encoding="utf-8")
        (project / "ProjectSettings" / "ProjectVersion.txt").write_text("m_EditorVersion: 2022.3", encoding="utf-8")

        original_handlers = dict(dashboard_server.AGENT_GATEWAY._write_handlers)
        original_prepare = dashboard_server.AGENT_GATEWAY.checkpoint_prepare_handler
        dashboard_server.AGENT_GATEWAY.checkpoint_prepare_handler = lambda _root: {"ok": True}
        calls: list[dict] = []

        def write_handler(args: dict) -> dict:
            calls.append(args)
            return {"ok": True, "wrote": args.get("name")}

        try:
            dashboard_server.AGENT_GATEWAY.register_write_handler(
                "vrcforge_test_app_write",
                "Test app write.",
                "high",
                write_handler,
            )
            request = dashboard_server.AGENT_GATEWAY.create_apply_request({
                "target_tool": "vrcforge_test_app_write",
                "arguments": {"projectRoot": str(project), "name": "value"},
            })
            approval_id = request["approval"]["id"]

            with TestClient(dashboard_server.app) as client:
                payload = client.post(
                    f"/api/app/agent/approvals/{approval_id}/approve",
                    json={"expectedProjectRoot": str(project)},
                ).json()

            self.assertTrue(payload["ok"])
            self.assertEqual(payload["execution"]["status"], "applied")
            self.assertEqual(payload["execution"]["result"]["wrote"], "value")
            self.assertEqual(calls, [{"projectRoot": str(project), "name": "value"}])
        finally:
            dashboard_server.AGENT_GATEWAY._write_handlers = original_handlers
            dashboard_server.AGENT_GATEWAY.checkpoint_prepare_handler = original_prepare

    def test_app_checkpoint_restore_request_uses_approval_path(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            project = Path(tmp) / "UnityProject"
            (project / "Assets").mkdir(parents=True)
            (project / "Packages").mkdir()
            (project / "ProjectSettings").mkdir()
            (project / "Assets" / "existing.txt").write_text("before", encoding="utf-8")
            (project / "Packages" / "manifest.json").write_text("{}", encoding="utf-8")
            (project / "ProjectSettings" / "ProjectVersion.txt").write_text("m_EditorVersion: 2022.3", encoding="utf-8")

            for args in (
                ["init"],
                ["config", "user.email", "test@example.invalid"],
                ["config", "user.name", "Test User"],
                ["add", "-A"],
                ["commit", "-m", "initial"],
            ):
                proc = subprocess.run(["git", *args], cwd=str(project), capture_output=True, text=True)
                self.assertEqual(proc.returncode, 0, proc.stderr)

            original_handlers = dict(dashboard_server.AGENT_GATEWAY._write_handlers)
            original_prepare = dashboard_server.AGENT_GATEWAY.checkpoint_prepare_handler
            original_reload = dashboard_server.AGENT_GATEWAY.checkpoint_restore_handler

            def write_handler(args: dict) -> dict:
                (Path(args["projectRoot"]) / "Assets" / "generated.txt").write_text("after", encoding="utf-8")
                return {"ok": True}

            try:
                dashboard_server.AGENT_GATEWAY.checkpoint_prepare_handler = lambda _root: {"ok": True}
                dashboard_server.AGENT_GATEWAY.checkpoint_restore_handler = lambda _root: {"ok": True}
                dashboard_server.AGENT_GATEWAY.register_write_handler(
                    "vrcforge_test_checkpoint_write",
                    "Test checkpoint write.",
                    "high",
                    write_handler,
                )
                request = dashboard_server.AGENT_GATEWAY.create_apply_request({
                    "target_tool": "vrcforge_test_checkpoint_write",
                    "arguments": {"projectRoot": str(project)},
                })
                approval_id = request["approval"]["id"]
                dashboard_server.AGENT_GATEWAY.approve(approval_id)
                dashboard_server.AGENT_GATEWAY.apply_approved({"approval_id": approval_id})

                with TestClient(dashboard_server.app) as client:
                    listed = client.get("/api/app/checkpoints", params={"projectRoot": str(project)}).json()
                    checkpoint_id = listed["checkpoints"][0]["id"]
                    preview = client.post(f"/api/app/checkpoints/{checkpoint_id}/preview").json()
                    restore_request = client.post(f"/api/app/checkpoints/{checkpoint_id}/restore").json()
                    restore_approval_id = restore_request["approval"]["id"]
                    applied = client.post(
                        f"/api/app/agent/approvals/{restore_approval_id}/approve",
                        json={"expectedProjectRoot": str(project.resolve())},
                    ).json()

                self.assertTrue(preview["ok"])
                self.assertEqual(restore_request["status"], "pending")
                self.assertEqual(restore_request["approval"]["targetTool"], "vrcforge_restore_checkpoint")
                stored_restore = dashboard_server.AGENT_GATEWAY._approvals[restore_approval_id]  # noqa: SLF001 - verify executable approval payload.
                self.assertEqual(stored_restore["arguments"]["projectRoot"], str(project.resolve()))
                self.assertTrue(applied["execution"]["ok"])
                self.assertFalse((project / "Assets" / "generated.txt").exists())
            finally:
                dashboard_server.AGENT_GATEWAY._write_handlers = original_handlers
                dashboard_server.AGENT_GATEWAY.checkpoint_prepare_handler = original_prepare
                dashboard_server.AGENT_GATEWAY.checkpoint_restore_handler = original_reload

    def test_adjustment_checkpoint_timeline_supports_crud_select_apply_and_overwrite(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            project = Path(tmp) / "UnityProject"
            (project / "Assets").mkdir(parents=True)
            (project / "Packages").mkdir()
            (project / "ProjectSettings").mkdir()
            (project / "Assets" / "face.txt").write_text("current", encoding="utf-8")
            (project / "Packages" / "manifest.json").write_text("{}", encoding="utf-8")
            (project / "ProjectSettings" / "ProjectVersion.txt").write_text("m_EditorVersion: 2022.3", encoding="utf-8")

            original_prepare = dashboard_server.AGENT_GATEWAY.checkpoint_prepare_handler
            original_reload = dashboard_server.AGENT_GATEWAY.checkpoint_restore_handler
            try:
                dashboard_server.AGENT_GATEWAY.checkpoint_prepare_handler = lambda _root: {"ok": True}
                dashboard_server.AGENT_GATEWAY.checkpoint_restore_handler = lambda _root: {"ok": True}
                with TestClient(dashboard_server.app) as client:
                    created = client.post(
                        "/api/app/adjustment-checkpoints",
                        json={
                            "kind": "face",
                            "projectRoot": str(project),
                            "avatarPath": "Scene/HeroAvatar",
                            "label": "Face A",
                            "tags": ["nose", "smile"],
                        },
                    ).json()
                    entry_id = created["checkpoint"]["id"]

                    listed = client.get("/api/app/adjustment-checkpoints", params={"kind": "face"}).json()
                    fetched = client.get(f"/api/app/adjustment-checkpoints/{entry_id}").json()
                    updated = client.put(
                        f"/api/app/adjustment-checkpoints/{entry_id}",
                        json={"label": "Face A tuned", "compareGroup": "smile-test"},
                    ).json()
                    selected = client.post(
                        f"/api/app/adjustment-checkpoints/{entry_id}/select",
                        json={"slot": "A", "compareGroup": "smile-test"},
                    ).json()
                    selections = client.get(
                        "/api/app/adjustment-checkpoints/selection",
                        params={"kind": "face", "compareGroup": "smile-test"},
                    ).json()

                    (project / "Assets" / "face.txt").write_text("variant b", encoding="utf-8")
                    overwritten = client.post(
                        f"/api/app/adjustment-checkpoints/{entry_id}/overwrite",
                        json={"label": "Face B", "projectRoot": str(project), "compareGroup": "smile-test"},
                    ).json()
                    preview = client.post(f"/api/app/adjustment-checkpoints/{entry_id}/preview").json()
                    apply_request = client.post(f"/api/app/adjustment-checkpoints/{entry_id}/apply").json()
                    deleted = client.delete(f"/api/app/adjustment-checkpoints/{entry_id}").json()
                    listed_after_delete = client.get("/api/app/adjustment-checkpoints", params={"kind": "face"}).json()
                    listed_with_deleted = client.get(
                        "/api/app/adjustment-checkpoints",
                        params={"kind": "face", "includeDeleted": "true"},
                    ).json()

                self.assertTrue(created["ok"])
                self.assertEqual(created["checkpoint"]["label"], "Face A")
                self.assertTrue(created["checkpoint"]["checkpointId"])
                self.assertEqual(listed["count"], 1)
                self.assertEqual(fetched["checkpoint"]["id"], entry_id)
                self.assertEqual(updated["checkpoint"]["label"], "Face A tuned")
                self.assertEqual(selected["selection"]["slot"], "A")
                self.assertIn("face:smile-test:A", selections["selections"])
                self.assertEqual(overwritten["checkpoint"]["label"], "Face B")
                self.assertEqual(overwritten["checkpoint"]["overwriteCount"], 1)
                self.assertTrue(preview["ok"])
                self.assertEqual(apply_request["approval"]["targetTool"], "vrcforge_restore_checkpoint")
                self.assertTrue(deleted["checkpoint"]["deletedAt"])
                self.assertEqual(listed_after_delete["count"], 0)
                self.assertEqual(listed_with_deleted["count"], 1)
            finally:
                dashboard_server.AGENT_GATEWAY.checkpoint_prepare_handler = original_prepare
                dashboard_server.AGENT_GATEWAY.checkpoint_restore_handler = original_reload

    def test_face_shader_checkpoint_records_auto_adjustment_timeline_entries(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            gateway = AgentGateway(Path(tmp) / "config" / "agent_gateway.json", Path(tmp) / "audit")

            gateway._append_checkpoint(  # noqa: SLF001 - unit test verifies checkpoint index side effect.
                {
                    "id": "ckpt_face_auto",
                    "createdAt": "2026-06-24T00:00:00+00:00",
                    "ok": True,
                    "status": "ready",
                    "targetTool": "vrcforge_run_face_tuning",
                    "projectRoot": "D:/AvatarProject",
                }
            )
            gateway._append_checkpoint(  # noqa: SLF001 - unit test verifies checkpoint index side effect.
                {
                    "id": "ckpt_shader_auto",
                    "createdAt": "2026-06-24T00:00:01+00:00",
                    "ok": True,
                    "status": "ready",
                    "targetTool": "vrcforge_apply_shader_tuning",
                    "projectRoot": "D:/AvatarProject",
                }
            )

            face = gateway.list_adjustment_checkpoints({"kind": "face"})
            shader = gateway.list_adjustment_checkpoints({"kind": "shader"})

            self.assertEqual(face["count"], 1)
            self.assertEqual(face["checkpoints"][0]["checkpointId"], "ckpt_face_auto")
            self.assertEqual(face["checkpoints"][0]["source"], "automatic")
            self.assertEqual(shader["count"], 1)
            self.assertEqual(shader["checkpoints"][0]["checkpointId"], "ckpt_shader_auto")

    def test_add_outfit_workflow_registered_in_gateway(self) -> None:
        config = dashboard_server.AGENT_GATEWAY.ensure_config()
        config.enabled = True
        dashboard_server.AGENT_GATEWAY.save_config(config)
        headers = {"Authorization": f"Bearer {config.token}"}

        with TestClient(dashboard_server.app) as client:
            payload = client.get("/api/agent/manifest", headers=headers).json()

        tool_names = {tool["name"] for tool in payload["tools"]}
        write_targets = {item["name"] for item in payload["writeTargets"]}
        self.assertIn("vrcforge_preview_add_outfit", tool_names)
        self.assertNotIn("vrcforge_add_outfit", tool_names)
        self.assertIn("vrcforge_add_outfit", write_targets)

    @patch("dashboard_server.invoke_unity_mcp")
    @patch("dashboard_server.load_dashboard_settings")
    def test_add_outfit_workflow_preview_matches_apply_order(self, mock_load_settings, mock_invoke) -> None:
        mock_load_settings.return_value = SimpleNamespace()
        mock_invoke.return_value = dashboard_server.McpResult(
            exit_code=0,
            stdout="ok",
            stderr="",
            payload={"data": {"ok": True, "assets": [{"assetPath": "Assets/Outfits/Hoodie.prefab", "guid": "abc", "name": "Hoodie"}]}},
        )

        result = dashboard_server.preview_add_outfit_workflow_sync({
            "avatarPath": "Avatar",
            "assetQuery": "hoodie",
            "outfitName": "Hoodie",
        })

        self.assertTrue(result["ok"])
        self.assertEqual([step["tool"] for step in result["plan"]["steps"]], [
            "vrc_find_assets",
            "vrc_scan_wardrobe",
            "vrc_create_wardrobe",
            "vrc_instantiate_prefab",
            "vrc_setup_outfit",
            "vrc_add_wardrobe_outfit",
        ])

    @patch("dashboard_server.invoke_unity_mcp")
    @patch("dashboard_server.load_dashboard_settings")
    def test_add_outfit_workflow_resolves_prefab_and_runs_ordered_steps(self, mock_load_settings, mock_invoke) -> None:
        mock_load_settings.return_value = SimpleNamespace()

        def fake_invoke(_settings, tool_name, params):
            if tool_name == "vrc_find_assets":
                return dashboard_server.McpResult(
                    exit_code=0,
                    stdout="ok",
                    stderr="",
                    payload={"data": {"ok": True, "assets": [{"assetPath": "Assets/Outfits/Hoodie.prefab", "guid": "abc", "name": "Hoodie"}]}},
                )
            if tool_name == "vrc_scan_wardrobe":
                return dashboard_server.McpResult(
                    exit_code=0,
                    stdout="ok",
                    stderr="",
                    payload={"data": {"ok": True, "wardrobeCount": 1, "wardrobes": [{"parameterName": "Clothes"}]}},
                )
            if tool_name == "vrc_instantiate_prefab":
                return dashboard_server.McpResult(
                    exit_code=0,
                    stdout="ok",
                    stderr="",
                    payload={"data": {"ok": True, "gameObjectPath": "Avatar/Hoodie"}},
                )
            if tool_name == "vrc_setup_outfit":
                return dashboard_server.McpResult(exit_code=0, stdout="ok", stderr="", payload={"data": {"ok": True, "confirmed": True}})
            if tool_name == "vrc_add_wardrobe_outfit":
                return dashboard_server.McpResult(exit_code=0, stdout="ok", stderr="", payload={"data": {"ok": True, "assignedValue": 4}})
            raise AssertionError(tool_name)

        mock_invoke.side_effect = fake_invoke
        result = dashboard_server.add_outfit_workflow_sync({
            "avatarPath": "Avatar",
            "assetQuery": "hoodie",
            "outfitName": "Hoodie",
            "parameterName": "Clothes",
        })

        self.assertTrue(result["ok"])
        self.assertEqual(result["outfitPath"], "Avatar/Hoodie")
        self.assertEqual([call.args[1] for call in mock_invoke.call_args_list], [
            "vrc_find_assets",
            "vrc_scan_wardrobe",
            "vrc_instantiate_prefab",
            "vrc_setup_outfit",
            "vrc_add_wardrobe_outfit",
        ])
        wardrobe_params = mock_invoke.call_args_list[-1].args[2]
        self.assertEqual(wardrobe_params["objectPaths"], ["Avatar/Hoodie"])
        self.assertEqual(wardrobe_params["parameterName"], "Clothes")

    @patch("dashboard_server.invoke_unity_mcp")
    @patch("dashboard_server.load_dashboard_settings")
    def test_add_outfit_workflow_creates_missing_wardrobe_before_binding(self, mock_load_settings, mock_invoke) -> None:
        mock_load_settings.return_value = SimpleNamespace()

        def fake_invoke(_settings, tool_name, params):
            if tool_name == "vrc_find_assets":
                return dashboard_server.McpResult(
                    exit_code=0,
                    stdout="ok",
                    stderr="",
                    payload={"data": {"ok": True, "assets": [{"assetPath": "Assets/Outfits/Hoodie.prefab", "guid": "abc", "name": "Hoodie"}]}},
                )
            if tool_name == "vrc_scan_wardrobe":
                return dashboard_server.McpResult(
                    exit_code=0,
                    stdout="ok",
                    stderr="",
                    payload={
                        "data": {
                            "ok": True,
                            "wardrobeCount": 0,
                            "wardrobeCandidateCount": 0,
                            "wardrobes": [],
                            "wardrobeCandidates": [],
                            "looseControlCount": 2,
                            "looseControls": [{"parameterName": "sock"}, {"parameterName": "hat"}],
                        }
                    },
                )
            if tool_name in {"vrc_ensure_expression_parameter", "vrc_ensure_animator_state", "vrc_ensure_expression_menu_control"}:
                return dashboard_server.McpResult(
                    exit_code=0,
                    stdout="ok",
                    stderr="",
                    payload={"data": {"ok": True, "tool": tool_name, "parameterName": params.get("parameterName")}},
                )
            if tool_name == "vrc_instantiate_prefab":
                return dashboard_server.McpResult(
                    exit_code=0,
                    stdout="ok",
                    stderr="",
                    payload={"data": {"ok": True, "gameObjectPath": "Avatar/Hoodie"}},
                )
            if tool_name == "vrc_setup_outfit":
                return dashboard_server.McpResult(exit_code=0, stdout="ok", stderr="", payload={"data": {"ok": True, "confirmed": True}})
            if tool_name == "vrc_add_wardrobe_outfit":
                return dashboard_server.McpResult(exit_code=0, stdout="ok", stderr="", payload={"data": {"ok": True, "assignedValue": 1}})
            raise AssertionError(tool_name)

        mock_invoke.side_effect = fake_invoke
        result = dashboard_server.add_outfit_workflow_sync({
            "avatarPath": "Avatar",
            "assetQuery": "hoodie",
            "outfitName": "Hoodie",
        })

        self.assertTrue(result["ok"])
        self.assertEqual([call.args[1] for call in mock_invoke.call_args_list], [
            "vrc_find_assets",
            "vrc_scan_wardrobe",
            "vrc_ensure_expression_parameter",
            "vrc_ensure_animator_state",
            "vrc_ensure_expression_menu_control",
            "vrc_instantiate_prefab",
            "vrc_setup_outfit",
            "vrc_add_wardrobe_outfit",
        ])
        create_params = mock_invoke.call_args_list[2].args[2]
        wardrobe_params = mock_invoke.call_args_list[-1].args[2]
        self.assertEqual(create_params["parameterName"], "Clothes")
        self.assertEqual(mock_invoke.call_args_list[3].args[2]["layerName"], "Clothes")
        self.assertEqual(mock_invoke.call_args_list[4].args[2]["menuPath"], "Wardrobe")
        self.assertEqual(wardrobe_params["parameterName"], "Clothes")

    @patch("dashboard_server.invoke_unity_mcp")
    @patch("dashboard_server.load_dashboard_settings")
    def test_add_outfit_workflow_does_not_auto_use_candidate_wardrobe(self, mock_load_settings, mock_invoke) -> None:
        mock_load_settings.return_value = SimpleNamespace()

        def fake_invoke(_settings, tool_name, _params):
            if tool_name == "vrc_find_assets":
                return dashboard_server.McpResult(
                    exit_code=0,
                    stdout="ok",
                    stderr="",
                    payload={"data": {"ok": True, "assets": [{"assetPath": "Assets/Outfits/Hoodie.prefab", "guid": "abc", "name": "Hoodie"}]}},
                )
            if tool_name == "vrc_scan_wardrobe":
                return dashboard_server.McpResult(
                    exit_code=0,
                    stdout="ok",
                    stderr="",
                    payload={
                        "data": {
                            "ok": True,
                            "wardrobeCount": 0,
                            "wardrobeCandidateCount": 1,
                            "wardrobes": [],
                            "wardrobeCandidates": [{"parameterName": "MaybeClothes"}],
                            "looseControlCount": 0,
                            "looseControls": [],
                        }
                    },
                )
            raise AssertionError(tool_name)

        mock_invoke.side_effect = fake_invoke
        result = dashboard_server.add_outfit_workflow_sync({
            "avatarPath": "Avatar",
            "assetQuery": "hoodie",
            "outfitName": "Hoodie",
        })

        self.assertFalse(result["ok"])
        self.assertIn("No high-confidence wardrobe was found", result["error"])
        self.assertEqual(result["wardrobeCandidates"], ["MaybeClothes"])
        self.assertEqual([call.args[1] for call in mock_invoke.call_args_list], [
            "vrc_find_assets",
            "vrc_scan_wardrobe",
        ])

    @patch("dashboard_server.invoke_unity_mcp")
    @patch("dashboard_server.load_dashboard_settings")
    def test_add_outfit_workflow_allows_explicit_candidate_wardrobe(self, mock_load_settings, mock_invoke) -> None:
        mock_load_settings.return_value = SimpleNamespace()

        def fake_invoke(_settings, tool_name, params):
            if tool_name == "vrc_find_assets":
                return dashboard_server.McpResult(
                    exit_code=0,
                    stdout="ok",
                    stderr="",
                    payload={"data": {"ok": True, "assets": [{"assetPath": "Assets/Outfits/Hoodie.prefab", "guid": "abc", "name": "Hoodie"}]}},
                )
            if tool_name == "vrc_scan_wardrobe":
                return dashboard_server.McpResult(
                    exit_code=0,
                    stdout="ok",
                    stderr="",
                    payload={
                        "data": {
                            "ok": True,
                            "wardrobeCount": 0,
                            "wardrobeCandidateCount": 1,
                            "wardrobes": [],
                            "wardrobeCandidates": [{"parameterName": "MaybeClothes"}],
                            "looseControlCount": 0,
                            "looseControls": [],
                        }
                    },
                )
            if tool_name == "vrc_instantiate_prefab":
                return dashboard_server.McpResult(
                    exit_code=0,
                    stdout="ok",
                    stderr="",
                    payload={"data": {"ok": True, "gameObjectPath": "Avatar/Hoodie"}},
                )
            if tool_name == "vrc_setup_outfit":
                return dashboard_server.McpResult(exit_code=0, stdout="ok", stderr="", payload={"data": {"ok": True}})
            if tool_name == "vrc_add_wardrobe_outfit":
                return dashboard_server.McpResult(exit_code=0, stdout="ok", stderr="", payload={"data": {"ok": True, "assignedValue": 2}})
            raise AssertionError(tool_name)

        mock_invoke.side_effect = fake_invoke
        result = dashboard_server.add_outfit_workflow_sync({
            "avatarPath": "Avatar",
            "assetQuery": "hoodie",
            "outfitName": "Hoodie",
            "parameterName": "MaybeClothes",
        })

        self.assertTrue(result["ok"])
        self.assertEqual([call.args[1] for call in mock_invoke.call_args_list], [
            "vrc_find_assets",
            "vrc_scan_wardrobe",
            "vrc_instantiate_prefab",
            "vrc_setup_outfit",
            "vrc_add_wardrobe_outfit",
        ])
        self.assertEqual(mock_invoke.call_args_list[-1].args[2]["parameterName"], "MaybeClothes")

    def test_generic_component_crud_tool_source_exists(self) -> None:
        editor_dir = Path(__file__).resolve().parents[1] / "Assets" / "VRCForge" / "Editor" / "Generic"
        source = (editor_dir / "UnityComponentCrud.cs").read_text(encoding="utf-8")
        for tool_name in (
            "vrc_get_property",
            "vrc_add_component",
            "vrc_remove_component",
            "vrc_set_property",
        ):
            self.assertIn(f'name: "{tool_name}"', source)
        self.assertIn("[McpForUnityTool(", source)
        self.assertEqual(source.count("public static object HandleCommand(JObject @params)"), 4)
        # Write tools must register Undo entries so the checkpoint timeline can roll them back.
        self.assertIn("Undo.AddComponent", source)
        self.assertIn("Undo.DestroyObjectImmediate", source)
        self.assertIn("Undo.RecordObject", source)

    def test_component_crud_tools_registered_in_gateway(self) -> None:
        config = dashboard_server.AGENT_GATEWAY.ensure_config()
        config.enabled = True
        dashboard_server.AGENT_GATEWAY.save_config(config)
        headers = {"Authorization": f"Bearer {config.token}"}

        with TestClient(dashboard_server.app) as client:
            payload = client.get("/api/agent/manifest", headers=headers).json()

        tool_names = {tool["name"] for tool in payload["tools"]}
        write_targets = {item["name"] for item in payload["writeTargets"]}
        # Read tool is directly callable.
        self.assertIn("vrcforge_get_property", tool_names)
        # Write tools are approval-gated: present as writeTargets, never as direct read tools.
        self.assertNotIn("vrcforge_add_component", tool_names)
        self.assertNotIn("vrcforge_remove_component", tool_names)
        self.assertNotIn("vrcforge_set_property", tool_names)
        self.assertIn("vrcforge_add_component", write_targets)
        self.assertIn("vrcforge_remove_component", write_targets)
        self.assertIn("vrcforge_set_property", write_targets)

    @patch("dashboard_server.invoke_unity_mcp")
    @patch("dashboard_server.load_dashboard_settings")
    def test_get_property_forwards_to_unity_tool(self, mock_load_settings, mock_invoke) -> None:
        mock_load_settings.return_value = SimpleNamespace()
        mock_invoke.return_value = dashboard_server.McpResult(
            exit_code=0,
            stdout="ok",
            stderr="",
            payload={"data": {
                "componentType": "UnityEngine.SkinnedMeshRenderer",
                "propertyPath": "enabled",
                "valueType": "System.Boolean",
                "propertyValue": True,
            }},
        )
        result = dashboard_server.read_component_property_sync({
            "game_object_path": "Scene/Avatar/Body",
            "component_type": "SkinnedMeshRenderer",
            "property_path": "enabled",
        })
        self.assertTrue(result["ok"])
        _settings, tool_name, params = mock_invoke.call_args.args
        self.assertEqual(tool_name, "vrc_get_property")
        self.assertEqual(params["gameObjectPath"], "Scene/Avatar/Body")
        self.assertEqual(params["componentType"], "SkinnedMeshRenderer")
        self.assertEqual(params["propertyPath"], "enabled")

    def test_get_property_requires_target_fields(self) -> None:
        self.assertFalse(dashboard_server.read_component_property_sync({})["ok"])
        self.assertFalse(
            dashboard_server.read_component_property_sync(
                {"game_object_path": "A", "component_type": "C"}
            )["ok"]
        )

    @patch("dashboard_server.invoke_unity_mcp")
    @patch("dashboard_server.load_dashboard_settings")
    def test_add_component_forwards_with_preview_flag(self, mock_load_settings, mock_invoke) -> None:
        mock_load_settings.return_value = SimpleNamespace()
        mock_invoke.return_value = dashboard_server.McpResult(
            exit_code=0,
            stdout="ok",
            stderr="",
            payload={"data": {"action": "add_component", "preview": True, "componentType": "X"}},
        )
        result = dashboard_server.add_component_sync({
            "game_object_path": "Scene/Avatar/Outfit",
            "component_type": "nadena.dev.modular_avatar.core.ModularAvatarMergeArmature",
            "preview": True,
        })
        self.assertTrue(result["ok"])
        _settings, tool_name, params = mock_invoke.call_args.args
        self.assertEqual(tool_name, "vrc_add_component")
        self.assertTrue(params["preview"])
        self.assertEqual(
            params["componentType"],
            "nadena.dev.modular_avatar.core.ModularAvatarMergeArmature",
        )

    @patch("dashboard_server.invoke_unity_mcp")
    @patch("dashboard_server.load_dashboard_settings")
    def test_remove_component_forwards_index(self, mock_load_settings, mock_invoke) -> None:
        mock_load_settings.return_value = SimpleNamespace()
        mock_invoke.return_value = dashboard_server.McpResult(
            exit_code=0,
            stdout="ok",
            stderr="",
            payload={"data": {"action": "remove_component"}},
        )
        result = dashboard_server.remove_component_sync({
            "gameObjectPath": "Scene/Avatar/Body",
            "componentType": "BoxCollider",
            "componentIndex": 2,
        })
        self.assertTrue(result["ok"])
        _settings, tool_name, params = mock_invoke.call_args.args
        self.assertEqual(tool_name, "vrc_remove_component")
        self.assertEqual(params["componentIndex"], 2)

    @patch("dashboard_server.invoke_unity_mcp")
    @patch("dashboard_server.load_dashboard_settings")
    def test_set_property_requires_value_and_forwards(self, mock_load_settings, mock_invoke) -> None:
        mock_load_settings.return_value = SimpleNamespace()
        mock_invoke.return_value = dashboard_server.McpResult(
            exit_code=0,
            stdout="ok",
            stderr="",
            payload={"data": {"action": "set_property", "newValue": False}},
        )
        missing = dashboard_server.set_component_property_sync({
            "game_object_path": "Scene/Avatar/Body",
            "component_type": "SkinnedMeshRenderer",
            "property_path": "enabled",
        })
        self.assertFalse(missing["ok"])
        mock_invoke.assert_not_called()
        result = dashboard_server.set_component_property_sync({
            "game_object_path": "Scene/Avatar/Body",
            "component_type": "SkinnedMeshRenderer",
            "property_path": "enabled",
            "value": False,
        })
        self.assertTrue(result["ok"])
        _settings, tool_name, params = mock_invoke.call_args.args
        self.assertEqual(tool_name, "vrc_set_property")
        self.assertEqual(params["propertyPath"], "enabled")
        self.assertIn("value", params)
        self.assertEqual(params["value"], False)

    def test_generic_gameobject_crud_tool_source_exists(self) -> None:
        editor_dir = Path(__file__).resolve().parents[1] / "Assets" / "VRCForge" / "Editor" / "Generic"
        source = (editor_dir / "UnityGameObjectCrud.cs").read_text(encoding="utf-8")
        for tool_name in (
            "vrc_get_gameobject",
            "vrc_create_gameobject",
            "vrc_rename_gameobject",
            "vrc_reparent_gameobject",
            "vrc_delete_gameobject",
            "vrc_set_gameobject_active",
        ):
            self.assertIn(f'name: "{tool_name}"', source)
        self.assertIn("[McpForUnityTool(", source)
        self.assertEqual(source.count("public static object HandleCommand(JObject @params)"), 6)
        # Reuses the shared reflection core rather than hard-referencing MA/VRC SDK assemblies.
        self.assertIn("ComponentCrudCore.ResolveGameObject", source)
        # Every write tool registers a Unity Undo entry for the checkpoint timeline.
        self.assertIn("Undo.RegisterCreatedObjectUndo", source)
        self.assertIn("Undo.SetTransformParent", source)
        self.assertIn("Undo.DestroyObjectImmediate", source)
        self.assertIn("Undo.RecordObject", source)
        # read payload must avoid auto-unwrap keys (data/result/payload/value).
        self.assertNotIn("value =", source)

    def test_gameobject_crud_tools_registered_in_gateway(self) -> None:
        config = dashboard_server.AGENT_GATEWAY.ensure_config()
        config.enabled = True
        dashboard_server.AGENT_GATEWAY.save_config(config)
        headers = {"Authorization": f"Bearer {config.token}"}

        with TestClient(dashboard_server.app) as client:
            payload = client.get("/api/agent/manifest", headers=headers).json()

        tool_names = {tool["name"] for tool in payload["tools"]}
        write_targets = {item["name"] for item in payload["writeTargets"]}
        # Read tool is directly callable.
        self.assertIn("vrcforge_get_gameobject", tool_names)
        # Write tools are approval-gated: present as writeTargets, never as direct read tools.
        for write_name in (
            "vrcforge_create_gameobject",
            "vrcforge_rename_gameobject",
            "vrcforge_reparent_gameobject",
            "vrcforge_delete_gameobject",
            "vrcforge_set_gameobject_active",
        ):
            self.assertNotIn(write_name, tool_names)
            self.assertIn(write_name, write_targets)

    @patch("dashboard_server.invoke_unity_mcp")
    @patch("dashboard_server.load_dashboard_settings")
    def test_get_gameobject_forwards_to_unity_tool(self, mock_load_settings, mock_invoke) -> None:
        mock_load_settings.return_value = SimpleNamespace()
        mock_invoke.return_value = dashboard_server.McpResult(
            exit_code=0,
            stdout="ok",
            stderr="",
            payload={"data": {
                "gameObjectPath": "Avatar/Body",
                "name": "Body",
                "activeSelf": True,
                "childCount": 0,
                "componentCount": 2,
            }},
        )
        result = dashboard_server.get_gameobject_sync({
            "game_object_path": "Avatar/Body",
        })
        self.assertTrue(result["ok"])
        _settings, tool_name, params = mock_invoke.call_args.args
        self.assertEqual(tool_name, "vrc_get_gameobject")
        self.assertEqual(params["gameObjectPath"], "Avatar/Body")

    def test_get_gameobject_requires_path(self) -> None:
        self.assertFalse(dashboard_server.get_gameobject_sync({})["ok"])

    @patch("dashboard_server.invoke_unity_mcp")
    @patch("dashboard_server.load_dashboard_settings")
    def test_create_gameobject_forwards_with_preview_flag(self, mock_load_settings, mock_invoke) -> None:
        mock_load_settings.return_value = SimpleNamespace()
        mock_invoke.return_value = dashboard_server.McpResult(
            exit_code=0,
            stdout="ok",
            stderr="",
            payload={"data": {"action": "create_gameobject", "preview": True, "name": "Outfit"}},
        )
        result = dashboard_server.create_gameobject_sync({
            "name": "Outfit",
            "parent_path": "Avatar",
            "preview": True,
        })
        self.assertTrue(result["ok"])
        _settings, tool_name, params = mock_invoke.call_args.args
        self.assertEqual(tool_name, "vrc_create_gameobject")
        self.assertTrue(params["preview"])
        self.assertEqual(params["name"], "Outfit")
        self.assertEqual(params["parentPath"], "Avatar")

    @patch("dashboard_server.invoke_unity_mcp")
    @patch("dashboard_server.load_dashboard_settings")
    def test_rename_gameobject_requires_new_name_and_forwards(self, mock_load_settings, mock_invoke) -> None:
        mock_load_settings.return_value = SimpleNamespace()
        mock_invoke.return_value = dashboard_server.McpResult(
            exit_code=0,
            stdout="ok",
            stderr="",
            payload={"data": {"action": "rename_gameobject", "newName": "Hips"}},
        )
        missing = dashboard_server.rename_gameobject_sync({"game_object_path": "Avatar/Armature/Hip"})
        self.assertFalse(missing["ok"])
        mock_invoke.assert_not_called()
        result = dashboard_server.rename_gameobject_sync({
            "game_object_path": "Avatar/Armature/Hip",
            "new_name": "Hips",
        })
        self.assertTrue(result["ok"])
        _settings, tool_name, params = mock_invoke.call_args.args
        self.assertEqual(tool_name, "vrc_rename_gameobject")
        self.assertEqual(params["newName"], "Hips")

    @patch("dashboard_server.invoke_unity_mcp")
    @patch("dashboard_server.load_dashboard_settings")
    def test_reparent_gameobject_forwards_world_position_stays(self, mock_load_settings, mock_invoke) -> None:
        mock_load_settings.return_value = SimpleNamespace()
        mock_invoke.return_value = dashboard_server.McpResult(
            exit_code=0,
            stdout="ok",
            stderr="",
            payload={"data": {"action": "reparent_gameobject"}},
        )
        result = dashboard_server.reparent_gameobject_sync({
            "game_object_path": "Avatar/Outfit",
            "new_parent_path": "Avatar/Armature/Hips",
            "world_position_stays": False,
        })
        self.assertTrue(result["ok"])
        _settings, tool_name, params = mock_invoke.call_args.args
        self.assertEqual(tool_name, "vrc_reparent_gameobject")
        self.assertEqual(params["newParentPath"], "Avatar/Armature/Hips")
        self.assertFalse(params["worldPositionStays"])

    @patch("dashboard_server.invoke_unity_mcp")
    @patch("dashboard_server.load_dashboard_settings")
    def test_delete_gameobject_forwards(self, mock_load_settings, mock_invoke) -> None:
        mock_load_settings.return_value = SimpleNamespace()
        mock_invoke.return_value = dashboard_server.McpResult(
            exit_code=0,
            stdout="ok",
            stderr="",
            payload={"data": {"action": "delete_gameobject", "preview": True}},
        )
        missing = dashboard_server.delete_gameobject_sync({})
        self.assertFalse(missing["ok"])
        result = dashboard_server.delete_gameobject_sync({
            "game_object_path": "Avatar/OldOutfit",
            "preview": True,
        })
        self.assertTrue(result["ok"])
        _settings, tool_name, params = mock_invoke.call_args.args
        self.assertEqual(tool_name, "vrc_delete_gameobject")
        self.assertEqual(params["gameObjectPath"], "Avatar/OldOutfit")

    @patch("dashboard_server.invoke_unity_mcp")
    @patch("dashboard_server.load_dashboard_settings")
    def test_set_gameobject_active_requires_active_and_forwards(self, mock_load_settings, mock_invoke) -> None:
        mock_load_settings.return_value = SimpleNamespace()
        mock_invoke.return_value = dashboard_server.McpResult(
            exit_code=0,
            stdout="ok",
            stderr="",
            payload={"data": {"action": "set_gameobject_active", "newActive": False}},
        )
        missing = dashboard_server.set_gameobject_active_sync({"game_object_path": "Avatar/Hat"})
        self.assertFalse(missing["ok"])
        mock_invoke.assert_not_called()
        result = dashboard_server.set_gameobject_active_sync({
            "game_object_path": "Avatar/Hat",
            "active": False,
        })
        self.assertTrue(result["ok"])
        _settings, tool_name, params = mock_invoke.call_args.args
        self.assertEqual(tool_name, "vrc_set_gameobject_active")
        self.assertIn("active", params)
        self.assertFalse(params["active"])

    def test_asset_prefab_crud_tool_source_exists(self) -> None:
        editor_dir = Path(__file__).resolve().parents[1] / "Assets" / "VRCForge" / "Editor" / "Generic"
        source = (editor_dir / "UnityAssetPrefabCrud.cs").read_text(encoding="utf-8")
        for tool_name in (
            "vrc_find_assets",
            "vrc_get_asset_info",
            "vrc_instantiate_prefab",
            "vrc_unpack_prefab",
        ):
            self.assertIn(f'name: "{tool_name}"', source)
        self.assertIn("[McpForUnityTool(", source)
        self.assertEqual(source.count("public static object HandleCommand(JObject @params)"), 4)
        # Reuses the shared reflection core rather than hard-referencing MA/VRC SDK assemblies.
        self.assertIn("ComponentCrudCore.ResolveGameObject", source)
        # Reads sit on stable AssetDatabase APIs.
        self.assertIn("AssetDatabase.FindAssets", source)
        # Both write tools register a Unity Undo entry for the checkpoint timeline.
        self.assertIn("Undo.RegisterCreatedObjectUndo", source)
        self.assertIn("PrefabUtility.InstantiatePrefab", source)
        self.assertIn("PrefabUtility.UnpackPrefabInstance", source)
        # payload must avoid auto-unwrap keys (data/result/payload/value).
        self.assertNotIn("value =", source)

    def test_asset_prefab_tools_registered_in_gateway(self) -> None:
        config = dashboard_server.AGENT_GATEWAY.ensure_config()
        config.enabled = True
        dashboard_server.AGENT_GATEWAY.save_config(config)
        headers = {"Authorization": f"Bearer {config.token}"}

        with TestClient(dashboard_server.app) as client:
            payload = client.get("/api/agent/manifest", headers=headers).json()

        tool_names = {tool["name"] for tool in payload["tools"]}
        write_targets = {item["name"] for item in payload["writeTargets"]}
        # Read tools are directly callable.
        self.assertIn("vrcforge_find_assets", tool_names)
        self.assertIn("vrcforge_get_asset_info", tool_names)
        # Write tools are approval-gated: present as writeTargets, never as direct read tools.
        for write_name in (
            "vrcforge_instantiate_prefab",
            "vrcforge_unpack_prefab",
        ):
            self.assertNotIn(write_name, tool_names)
            self.assertIn(write_name, write_targets)

    @patch("dashboard_server.invoke_unity_mcp")
    @patch("dashboard_server.load_dashboard_settings")
    def test_find_assets_forwards_query_and_type(self, mock_load_settings, mock_invoke) -> None:
        mock_load_settings.return_value = SimpleNamespace()
        mock_invoke.return_value = dashboard_server.McpResult(
            exit_code=0,
            stdout="ok",
            stderr="",
            payload={"data": {"filter": "t:Prefab outfit", "count": 1, "assets": []}},
        )
        result = dashboard_server.find_assets_sync({
            "query": "outfit",
            "type_name": "Prefab",
            "folder": "Assets/Outfits",
        })
        self.assertTrue(result["ok"])
        _settings, tool_name, params = mock_invoke.call_args.args
        self.assertEqual(tool_name, "vrc_find_assets")
        self.assertEqual(params["query"], "outfit")
        self.assertEqual(params["typeName"], "Prefab")
        self.assertEqual(params["folder"], "Assets/Outfits")

    def test_get_asset_info_requires_path_or_guid(self) -> None:
        self.assertFalse(dashboard_server.get_asset_info_sync({})["ok"])

    @patch("dashboard_server.invoke_unity_mcp")
    @patch("dashboard_server.load_dashboard_settings")
    def test_get_asset_info_forwards(self, mock_load_settings, mock_invoke) -> None:
        mock_load_settings.return_value = SimpleNamespace()
        mock_invoke.return_value = dashboard_server.McpResult(
            exit_code=0,
            stdout="ok",
            stderr="",
            payload={"data": {"assetPath": "Assets/Outfits/Dress.prefab", "isPrefab": True}},
        )
        result = dashboard_server.get_asset_info_sync({
            "asset_path": "Assets/Outfits/Dress.prefab",
        })
        self.assertTrue(result["ok"])
        _settings, tool_name, params = mock_invoke.call_args.args
        self.assertEqual(tool_name, "vrc_get_asset_info")
        self.assertEqual(params["assetPath"], "Assets/Outfits/Dress.prefab")

    def test_instantiate_prefab_requires_asset(self) -> None:
        self.assertFalse(dashboard_server.instantiate_prefab_sync({})["ok"])

    @patch("dashboard_server.invoke_unity_mcp")
    @patch("dashboard_server.load_dashboard_settings")
    def test_instantiate_prefab_forwards_with_preview(self, mock_load_settings, mock_invoke) -> None:
        mock_load_settings.return_value = SimpleNamespace()
        mock_invoke.return_value = dashboard_server.McpResult(
            exit_code=0,
            stdout="ok",
            stderr="",
            payload={"data": {"action": "instantiate_prefab", "preview": True, "name": "Dress"}},
        )
        result = dashboard_server.instantiate_prefab_sync({
            "asset_path": "Assets/Outfits/Dress.prefab",
            "parent_path": "Avatar",
            "preview": True,
        })
        self.assertTrue(result["ok"])
        _settings, tool_name, params = mock_invoke.call_args.args
        self.assertEqual(tool_name, "vrc_instantiate_prefab")
        self.assertTrue(params["preview"])
        self.assertEqual(params["assetPath"], "Assets/Outfits/Dress.prefab")
        self.assertEqual(params["parentPath"], "Avatar")

    def test_unpack_prefab_requires_path(self) -> None:
        self.assertFalse(dashboard_server.unpack_prefab_sync({})["ok"])

    @patch("dashboard_server.invoke_unity_mcp")
    @patch("dashboard_server.load_dashboard_settings")
    def test_unpack_prefab_forwards_mode(self, mock_load_settings, mock_invoke) -> None:
        mock_load_settings.return_value = SimpleNamespace()
        mock_invoke.return_value = dashboard_server.McpResult(
            exit_code=0,
            stdout="ok",
            stderr="",
            payload={"data": {"action": "unpack_prefab", "unpackMode": "completely"}},
        )
        result = dashboard_server.unpack_prefab_sync({
            "game_object_path": "Avatar/Dress",
            "mode": "completely",
        })
        self.assertTrue(result["ok"])
        _settings, tool_name, params = mock_invoke.call_args.args
        self.assertEqual(tool_name, "vrc_unpack_prefab")
        self.assertEqual(params["gameObjectPath"], "Avatar/Dress")
        self.assertEqual(params["mode"], "completely")

    def test_dynamic_code_execution_files_are_not_distributed(self) -> None:
        editor_dir = Path(__file__).resolve().parents[1] / "Assets" / "VRCForge" / "Editor"
        self.assertFalse((editor_dir / "RoslynExecutor.cs").exists())
        self.assertFalse((editor_dir / "RoslynSupportBootstrap.cs").exists())
        self.assertFalse((Path(__file__).resolve().parents[1] / "tools" / "install-roslyn-support.ps1").exists())

    def test_release_payload_excludes_dynamic_unity_mcp_execution_files(self) -> None:
        script = (Path(__file__).resolve().parents[1] / "packaging" / "build_release.ps1").read_text(encoding="utf-8")
        self.assertIn("Editor\\Tools\\ExecuteCode.cs", script)
        self.assertIn("Editor\\Setup\\RoslynInstaller.cs", script)
        self.assertIn("$relativePath.meta", script)
        self.assertIn("Remove-Item", script)

    def test_unity_editor_branding_uses_vrcforge_menu_and_paths(self) -> None:
        editor_dir = Path(__file__).resolve().parents[1] / "Assets" / "VRCForge" / "Editor"
        combined = "\n".join(path.read_text(encoding="utf-8") for path in editor_dir.glob("*.cs"))

        self.assertIn('MenuItem("VRCForge/MCP/Start Bridge Now")', combined)
        self.assertIn('private const string MenuPath = "VRCForge/Uninstall VRCForge Unity Plugin"', combined)
        self.assertIn("Assets/VRCForge/blendshapes_export.json", combined)
        self.assertIn('".vrcforge", "backups"', combined)
        old_brand = "VRC" + "AutoRig"
        self.assertNotIn(f'MenuItem("{old_brand}', combined)
        self.assertNotIn(f"[{old_brand}", combined)
        self.assertNotIn(f"Assets/{old_brand}", combined)

        self.assertTrue(callable(dashboard_server.install_vrcforge_into_unity_project))

    def test_unity_instance_session_id_is_resolved_to_cli_hash(self) -> None:
        settings = SimpleNamespace(
            unity_mcp_host="127.0.0.1",
            unity_mcp_port=8080,
            unity_mcp_timeout_seconds=5,
            unity_mcp_instance="session-123",
        )
        previous_instance = dashboard_server.DASHBOARD_STATE.unity_instance
        dashboard_server.DASHBOARD_STATE.unity_instance = ""
        try:
            with patch(
                "dashboard_server.fetch_unity_http_json",
                return_value=(
                    True,
                    {
                        "instances": [
                            {
                                "session_id": "session-123",
                                "project": "milltina",
                                "hash": "5d8ae8a25423705c",
                                "unity_version": "2022.3.22f1",
                            }
                        ]
                    },
                    "",
                    200,
                ),
            ):
                dashboard_server.resolve_unity_cli_instance_selector(settings)

            self.assertEqual(settings.unity_mcp_instance, "5d8ae8a25423705c")
            self.assertEqual(dashboard_server.DASHBOARD_STATE.unity_instance, "5d8ae8a25423705c")
        finally:
            dashboard_server.DASHBOARD_STATE.unity_instance = previous_instance

    def test_doctor_marks_unity_bridge_checks_repairable(self) -> None:
        with TestClient(dashboard_server.app) as client:
            response = client.get("/api/app/doctor")

        self.assertEqual(response.status_code, 200)
        checks = {item["id"]: item for item in response.json()["checks"]}
        self.assertTrue(checks["unity.mcp.bridge"]["fixable"])
        self.assertIn("repair_unity_bridge", checks["unity.mcp.bridge"]["actions"])
        self.assertTrue(checks["unity.mcp.instance"]["fixable"])
        self.assertIn("repair_unity_bridge", checks["unity.mcp.instance"]["actions"])

    def test_extract_unity_project_path_from_command_line(self) -> None:
        command_line = r'"E:\unity\Unity 2022.3.22f1\Editor\Unity.exe" -projectPath "E:\unity\milltina"'

        self.assertEqual(
            dashboard_server.extract_unity_project_path_from_command_line(command_line),
            "E:/unity/milltina",
        )

    def test_discover_projects_includes_running_unity_project_path(self) -> None:
        previous_selected = dashboard_server.DASHBOARD_STATE.selected_project_path
        previous_status = dashboard_server.CURRENT_UNITY_STATUS
        try:
            dashboard_server.DASHBOARD_STATE.selected_project_path = ""
            dashboard_server.CURRENT_UNITY_STATUS = {"instances": []}
            with tempfile.TemporaryDirectory() as temp_dir:
                project = Path(temp_dir) / "Running Avatar"
                (project / "Assets").mkdir(parents=True)
                (project / "Packages").mkdir()
                (project / "ProjectSettings").mkdir()
                (project / "ProjectSettings" / "ProjectVersion.txt").write_text("m_EditorVersion: 2022.3.22f1\n", encoding="utf-8")
                with (
                    patch("dashboard_server.discover_vcc_projects", return_value=[]),
                    patch("dashboard_server.discover_alcom_projects", return_value=[]),
                    patch("dashboard_server.discover_unity_hub_projects", return_value=[]),
                    patch("dashboard_server.load_project_prefs", return_value={"customPaths": [], "hiddenPaths": []}),
                    patch(
                        "dashboard_server.list_running_unity_processes",
                        return_value=[
                            {
                                "processId": 123,
                                "executablePath": r"E:\unity\Unity 2022.3.22f1\Editor\Unity.exe",
                                "commandLine": f'"E:\\unity\\Unity 2022.3.22f1\\Editor\\Unity.exe" -projectPath "{project}"',
                            }
                        ],
                    ),
                ):
                    projects = dashboard_server.discover_projects([], include_external=True)

            self.assertEqual(len(projects), 1)
            self.assertEqual(projects[0]["path"], dashboard_server.normalize_path_string(str(project)))
            self.assertIn("running-unity", projects[0]["sources"])
        finally:
            dashboard_server.DASHBOARD_STATE.selected_project_path = previous_selected
            dashboard_server.CURRENT_UNITY_STATUS = previous_status

    def test_repair_unity_mcp_bridge_already_healthy_noop(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            project = Path(temp_dir) / "AvatarProject"
            (project / "Assets").mkdir(parents=True)
            (project / "Packages").mkdir()
            (project / "ProjectSettings").mkdir()
            (project / "ProjectSettings" / "ProjectVersion.txt").write_text("m_EditorVersion: 2022.3.22f1\n", encoding="utf-8")
            healthy = {
                "connected": True,
                "mcpServerReachable": True,
                "unityInstanceRegistered": True,
                "selectedInstanceMatched": True,
                "activeInstanceCount": 1,
                "vrcForgeToolsRegistered": True,
                "missingRequiredVrcForgeTools": [],
                "tools": {"totalTools": 78, "vrcForgeToolsCount": 48},
                "error": "",
            }
            with (
                patch("dashboard_server.build_unity_status_snapshot", return_value=healthy),
                patch("dashboard_server.verify_unity_mcp_execution_connection", return_value=(True, {"tool": "vrc_check_roslyn_status"})),
                patch("dashboard_server.subprocess.Popen") as mock_popen,
            ):
                result = dashboard_server.repair_unity_mcp_bridge_sync(
                    dashboard_server.UnityMcpRepairRequest(projectPath=str(project), allowUnityRelaunch=True)
                )

        self.assertTrue(result["ok"])
        self.assertEqual(result["status"], "healthy")
        mock_popen.assert_not_called()

    def test_repair_unity_mcp_bridge_returns_busy_when_repair_running(self) -> None:
        acquired = dashboard_server.UNITY_MCP_REPAIR_LOCK.acquire(blocking=False)
        self.assertTrue(acquired)
        try:
            result = dashboard_server.repair_unity_mcp_bridge_sync(
                dashboard_server.UnityMcpRepairRequest(projectPath=r"C:\Unity\AvatarProject")
            )
        finally:
            dashboard_server.UNITY_MCP_REPAIR_LOCK.release()

        self.assertFalse(result["ok"])
        self.assertEqual(result["status"], "busy")
        self.assertIn("repair_lock", {phase["id"] for phase in result["phases"]})

    def test_repair_unity_mcp_bridge_refuses_to_close_unmatched_unity(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            project = root / "AvatarProject"
            other = root / "OtherProject"
            editor = root / "Unity.exe"
            for candidate in (project, other):
                (candidate / "Assets").mkdir(parents=True)
                (candidate / "Packages").mkdir()
                (candidate / "ProjectSettings").mkdir()
                (candidate / "ProjectSettings" / "ProjectVersion.txt").write_text("m_EditorVersion: 2022.3.22f1\n", encoding="utf-8")
            editor.write_text("", encoding="utf-8")
            offline = {
                "connected": True,
                "mcpServerReachable": True,
                "unityInstanceRegistered": False,
                "selectedInstanceMatched": False,
                "activeInstanceCount": 0,
                "vrcForgeToolsRegistered": False,
                "missingRequiredVrcForgeTools": [],
                "tools": {"totalTools": 0, "vrcForgeToolsCount": 0},
                "error": "",
            }
            with (
                patch("dashboard_server.build_unity_status_snapshot", return_value=offline),
                patch("dashboard_server.ensure_unity_mcp_server_running", return_value=True),
                patch("dashboard_server.wait_for_unity_project_registration", return_value=(False, {"instances": []})),
                patch(
                    "dashboard_server.list_running_unity_processes",
                    return_value=[
                        {
                            "processId": 123,
                            "executablePath": str(editor),
                            "commandLine": f'"{editor}" -projectPath "{other}"',
                        }
                    ],
                ),
                patch("dashboard_server.launch_unity_project") as mock_launch,
            ):
                result = dashboard_server.repair_unity_mcp_bridge_sync(
                    dashboard_server.UnityMcpRepairRequest(projectPath=str(project), unityEditorPath=str(editor), allowUnityRelaunch=True)
                )

        self.assertFalse(result["ok"])
        self.assertEqual(result["status"], "needs_user_action")
        self.assertIn("did not close any editor", json.dumps(result["phases"]))
        mock_launch.assert_not_called()

    def test_repair_unity_mcp_bridge_registered_without_tools_needs_action(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            project = Path(temp_dir) / "AvatarProject"
            (project / "Assets").mkdir(parents=True)
            (project / "Packages").mkdir()
            (project / "ProjectSettings").mkdir()
            (project / "ProjectSettings" / "ProjectVersion.txt").write_text("m_EditorVersion: 2022.3.22f1\n", encoding="utf-8")
            registered_without_tools = {
                "connected": True,
                "mcpServerReachable": True,
                "unityInstanceRegistered": True,
                "selectedInstanceMatched": True,
                "activeInstanceCount": 1,
                "vrcForgeToolsRegistered": False,
                "missingRequiredVrcForgeTools": ["vrc_export_blendshapes"],
                "tools": {"totalTools": 0, "vrcForgeToolsCount": 0},
                "error": "",
            }
            with (
                patch("dashboard_server.build_unity_status_snapshot", return_value=registered_without_tools),
                patch("dashboard_server.ensure_unity_mcp_server_running", return_value=True),
                patch("dashboard_server.wait_for_unity_project_registration", return_value=(True, {"instances": [{"project": project.name}]})),
                patch("dashboard_server.restart_unity_mcp_server", return_value=False),
                patch("dashboard_server.recent_unity_mcp_execution_error", return_value={}),
                patch("dashboard_server.close_unity_project_gracefully") as mock_close,
            ):
                result = dashboard_server.repair_unity_mcp_bridge_sync(
                    dashboard_server.UnityMcpRepairRequest(projectPath=str(project), allowUnityRelaunch=False, waitSeconds=5)
                )

        self.assertFalse(result["ok"])
        self.assertEqual(result["status"], "needs_user_action")
        self.assertIn("unity_tools", {phase["id"] for phase in result["phases"]})
        self.assertFalse(result["after"]["vrcForgeToolsRegistered"])
        mock_close.assert_not_called()

    def test_launch_unity_project_uses_editor_directory_as_cwd(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            editor_dir = root / "Editor"
            project = root / "AvatarProject"
            editor_dir.mkdir()
            project.mkdir()
            editor = editor_dir / "Unity.exe"
            editor.write_text("", encoding="utf-8")

            internal_dir = str(dashboard_server.ROOT_DIR / "backend" / "_internal")
            with (
                patch.dict(dashboard_server.os.environ, {"PATH": internal_dir + os.pathsep + r"C:\Windows"}),
                patch("dashboard_server.pyinstaller_internal_dir", return_value=Path(internal_dir)),
                patch("dashboard_server.set_windows_dll_directory") as mock_set_dll_directory,
                patch("dashboard_server.subprocess.Popen") as mock_popen,
            ):
                ok, error = dashboard_server.launch_unity_project(editor, project)

            self.assertTrue(ok)
            self.assertEqual(error, "")
            mock_popen.assert_called_once_with(
                [
                    str(editor),
                    "-projectPath",
                    str(project),
                    "-executeMethod",
                    "VRCForge.Editor.McpBridgeBootstrap.StartBridgeNow",
                ],
                cwd=str(editor_dir),
                env=ANY,
            )
            self.assertNotIn(internal_dir, mock_popen.call_args.kwargs["env"]["PATH"])
            self.assertEqual([call.args[0] for call in mock_set_dll_directory.call_args_list], [None, internal_dir])

    def test_open_project_route_accepts_project_path_alias_and_uses_editor_cwd(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            project = root / "AvatarProject"
            editor_dir = root / "Editor"
            editor = editor_dir / "Unity.exe"
            (project / "Assets").mkdir(parents=True)
            (project / "Packages").mkdir()
            (project / "ProjectSettings").mkdir()
            (project / "ProjectSettings" / "ProjectVersion.txt").write_text("m_EditorVersion: 2022.3.22f1\n", encoding="utf-8")
            editor_dir.mkdir()
            editor.write_text("", encoding="utf-8")
            previous_editor = dashboard_server.DASHBOARD_STATE.unity_editor_path
            previous_selected = dashboard_server.DASHBOARD_STATE.selected_project_path
            dashboard_server.DASHBOARD_STATE.unity_editor_path = str(editor)
            try:
                for payload in ({"projectPath": str(project)}, {"project_path": str(project)}):
                    with patch("dashboard_server.subprocess.Popen") as mock_popen:
                        with TestClient(dashboard_server.app) as client:
                            response = client.post("/api/projects/open", json=payload)
                    self.assertEqual(response.status_code, 200)
                    command = mock_popen.call_args.args[0]
                    self.assertEqual(command[0], str(editor))
                    self.assertEqual(command[1], "-projectPath")
                    self.assertEqual(Path(command[2]).resolve(), project.resolve())
                    self.assertEqual(mock_popen.call_args.kwargs["cwd"], str(editor_dir))
                    self.assertNotIn(str(dashboard_server.ROOT_DIR / "backend" / "_internal"), mock_popen.call_args.kwargs["env"]["PATH"])
            finally:
                dashboard_server.DASHBOARD_STATE.unity_editor_path = previous_editor
                dashboard_server.DASHBOARD_STATE.selected_project_path = previous_selected

    def test_discover_vrcforge_unity_tool_definitions_reads_mcp_attributes(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            project = Path(temp_dir) / "AvatarProject"
            editor = project / "Assets" / "VRCForge" / "Editor"
            editor.mkdir(parents=True)
            (editor / "SampleTool.cs").write_text(
                """
using MCPForUnity.Editor.Tools;

namespace VRCForge.Editor
{
    [McpForUnityTool(
        name: "vrc_sample_tool",
        Description = "Sample VRCForge tool."
    )]
    public static class SampleTool {}
}
""",
                encoding="utf-8",
            )

            definitions = dashboard_server.discover_vrcforge_unity_tool_definitions(project)

        self.assertEqual([item["name"] for item in definitions], ["vrc_sample_tool"])
        self.assertEqual(definitions[0]["description"], "Sample VRCForge tool.")
        self.assertTrue(definitions[0]["structured_output"])

    def test_repair_unity_mcp_bridge_reregisters_empty_tool_list(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            project = Path(temp_dir) / "AvatarProject"
            editor = project / "Assets" / "VRCForge" / "Editor"
            editor.mkdir(parents=True)
            (project / "Packages").mkdir()
            (project / "ProjectSettings").mkdir()
            (project / "ProjectSettings" / "ProjectVersion.txt").write_text("m_EditorVersion: 2022.3.22f1\n", encoding="utf-8")
            (editor / "SampleTool.cs").write_text(
                """
using MCPForUnity.Editor.Tools;

namespace VRCForge.Editor
{
    [McpForUnityTool(name: "vrc_export_blendshapes", Description = "Export blendshapes.")]
    public static class SampleTool {}
}
""",
                encoding="utf-8",
            )
            registered_without_tools = {
                "connected": True,
                "mcpServerReachable": True,
                "unityInstanceRegistered": True,
                "selectedInstanceMatched": True,
                "activeInstanceCount": 1,
                "activeInstance": {"project": project.name, "hash": "abc123", "cliInstanceId": "abc123"},
                "instances": [{"project": project.name, "hash": "abc123", "cliInstanceId": "abc123"}],
                "vrcForgeToolsRegistered": False,
                "missingRequiredVrcForgeTools": ["vrc_export_blendshapes"],
                "tools": {"totalTools": 0, "vrcForgeToolsCount": 0},
                "error": "",
            }
            healthy_summary = {
                "connected": True,
                "mcpServerReachable": True,
                "unityInstanceRegistered": True,
                "selectedInstanceMatched": True,
                "activeInstanceCount": 1,
                "vrcForgeToolsRegistered": True,
                "totalTools": 78,
                "vrcForgeToolsCount": 48,
                "missingRequiredVrcForgeTools": [],
                "toolsError": "",
                "error": "",
            }
            with (
                patch("dashboard_server.build_unity_status_snapshot", return_value=registered_without_tools),
                patch("dashboard_server.ensure_unity_mcp_server_running", return_value=True),
                patch("dashboard_server.wait_for_unity_project_registration", return_value=(True, {"instances": [{"project": project.name}]})),
                patch("dashboard_server.wait_for_unity_tools_ready", side_effect=[(False, dashboard_server._unity_repair_status_summary(registered_without_tools)), (True, healthy_summary)]),
                patch("dashboard_server.post_unity_http_json", return_value=(True, {"ok": True}, "", 200)) as mock_post,
                patch("dashboard_server.verify_unity_mcp_execution_connection", return_value=(True, {"tool": "vrc_check_roslyn_status"})),
                patch("dashboard_server.recent_unity_mcp_execution_error", return_value={}),
                patch("dashboard_server.restart_unity_mcp_server") as mock_restart,
                patch("dashboard_server.close_unity_project_gracefully") as mock_close,
            ):
                result = dashboard_server.repair_unity_mcp_bridge_sync(
                    dashboard_server.UnityMcpRepairRequest(projectPath=str(project), allowUnityRelaunch=False, waitSeconds=5)
                )

        self.assertTrue(result["ok"])
        self.assertEqual(result["status"], "recovered")
        self.assertIn("unity_tool_registration", {phase["id"] for phase in result["phases"]})
        self.assertEqual(mock_post.call_args.args[1], "/register-tools")
        self.assertEqual(mock_post.call_args.args[2]["project_id"], "abc123")
        self.assertEqual(mock_post.call_args.args[2]["tools"][0]["name"], "vrc_export_blendshapes")
        mock_restart.assert_not_called()
        mock_close.assert_not_called()

    def test_unity_repair_tools_message_distinguishes_execution_disconnect(self) -> None:
        message = dashboard_server.unity_repair_tools_message(
            {
                "unityInstanceRegistered": True,
                "totalTools": 0,
                "vrcForgeToolsRegistered": False,
                "toolsError": "HTTP 503: No Unity instances connected.",
            }
        )

        self.assertIn("execution connection", message)

    def test_repair_unity_mcp_bridge_restart_recovers_empty_tool_list(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            project = Path(temp_dir) / "AvatarProject"
            (project / "Assets").mkdir(parents=True)
            (project / "Packages").mkdir()
            (project / "ProjectSettings").mkdir()
            (project / "ProjectSettings" / "ProjectVersion.txt").write_text("m_EditorVersion: 2022.3.22f1\n", encoding="utf-8")
            registered_without_tools = {
                "connected": True,
                "mcpServerReachable": True,
                "unityInstanceRegistered": True,
                "selectedInstanceMatched": True,
                "activeInstanceCount": 1,
                "vrcForgeToolsRegistered": False,
                "missingRequiredVrcForgeTools": ["vrc_export_blendshapes"],
                "tools": {"totalTools": 0, "vrcForgeToolsCount": 0},
                "error": "",
            }
            healthy_summary = {
                "connected": True,
                "mcpServerReachable": True,
                "unityInstanceRegistered": True,
                "selectedInstanceMatched": True,
                "activeInstanceCount": 1,
                "vrcForgeToolsRegistered": True,
                "totalTools": 78,
                "vrcForgeToolsCount": 48,
                "missingRequiredVrcForgeTools": [],
                "error": "",
            }
            with (
                patch("dashboard_server.build_unity_status_snapshot", return_value=registered_without_tools),
                patch("dashboard_server.ensure_unity_mcp_server_running", return_value=True),
                patch(
                    "dashboard_server.wait_for_unity_project_registration",
                    side_effect=[
                        (True, {"instances": [{"project": project.name}]}),
                        (True, {"instances": [{"project": project.name}]}),
                    ],
                ),
            patch("dashboard_server.wait_for_unity_tools_ready", side_effect=[(False, dashboard_server._unity_repair_status_summary(registered_without_tools)), (True, healthy_summary)]),
            patch("dashboard_server.restart_unity_mcp_server", return_value=True) as mock_restart,
            patch("dashboard_server.verify_unity_mcp_execution_connection", return_value=(True, {"tool": "vrc_check_roslyn_status"})),
            patch("dashboard_server.recent_unity_mcp_execution_error", return_value={}),
            patch("dashboard_server.close_unity_project_gracefully") as mock_close,
        ):
                result = dashboard_server.repair_unity_mcp_bridge_sync(
                    dashboard_server.UnityMcpRepairRequest(projectPath=str(project), allowUnityRelaunch=False, waitSeconds=5)
                )

        self.assertTrue(result["ok"])
        self.assertEqual(result["status"], "recovered")
        self.assertEqual(result["after"]["totalTools"], 78)
        mock_restart.assert_called_once()
        mock_close.assert_not_called()

    def test_repair_unity_mcp_bridge_relaunches_and_reconnects(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            project = root / "AvatarProject"
            editor = root / "Unity.exe"
            (project / "Assets").mkdir(parents=True)
            (project / "Packages").mkdir()
            (project / "ProjectSettings").mkdir()
            (project / "ProjectSettings" / "ProjectVersion.txt").write_text("m_EditorVersion: 2022.3.22f1\n", encoding="utf-8")
            editor.write_text("", encoding="utf-8")
            offline = {
                "connected": True,
                "mcpServerReachable": True,
                "unityInstanceRegistered": False,
                "selectedInstanceMatched": False,
                "activeInstanceCount": 0,
                "vrcForgeToolsRegistered": False,
                "missingRequiredVrcForgeTools": [],
                "tools": {"totalTools": 0, "vrcForgeToolsCount": 0},
                "error": "",
            }
            healthy = {
                "connected": True,
                "mcpServerReachable": True,
                "unityInstanceRegistered": True,
                "selectedInstanceMatched": True,
                "activeInstanceCount": 1,
                "vrcForgeToolsRegistered": True,
                "missingRequiredVrcForgeTools": [],
                "tools": {"totalTools": 78, "vrcForgeToolsCount": 48},
                "error": "",
            }
            with (
                patch("dashboard_server.build_unity_status_snapshot", side_effect=[offline, healthy]),
                patch("dashboard_server.ensure_unity_mcp_server_running", return_value=True),
                patch(
                    "dashboard_server.wait_for_unity_project_registration",
                    side_effect=[
                        (False, {"instances": []}),
                        (True, {"instances": [{"project": project.name, "hash": "abc123"}]}),
                    ],
                ) as mock_wait,
                patch("dashboard_server.verify_unity_mcp_execution_connection", return_value=(True, {"tool": "vrc_check_roslyn_status"})),
                patch("dashboard_server.close_unity_project_gracefully", return_value=(True, "Unity closed cleanly.", {})) as mock_close,
                patch("dashboard_server.launch_unity_project", return_value=(True, "")) as mock_launch,
            ):
                result = dashboard_server.repair_unity_mcp_bridge_sync(
                    dashboard_server.UnityMcpRepairRequest(projectPath=str(project), unityEditorPath=str(editor), allowUnityRelaunch=True)
                )

        self.assertTrue(result["ok"])
        self.assertEqual(result["status"], "recovered")
        self.assertEqual(mock_wait.call_count, 2)
        mock_close.assert_called_once()
        mock_launch.assert_called_once()

    def test_repair_unity_mcp_bridge_relaunch_recovers_after_slow_tool_list_registration(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            project = root / "AvatarProject"
            editor = root / "Unity.exe"
            (project / "Assets").mkdir(parents=True)
            (project / "Packages").mkdir()
            (project / "ProjectSettings").mkdir()
            (project / "ProjectSettings" / "ProjectVersion.txt").write_text("m_EditorVersion: 2022.3.22f1\n", encoding="utf-8")
            editor.write_text("", encoding="utf-8")
            settings = SimpleNamespace(unity_mcp_timeout_seconds=30, unity_mcp_retries=3, unity_mcp_retry_backoff_seconds=1.0)
            offline = {
                "connected": True,
                "mcpServerReachable": True,
                "unityInstanceRegistered": False,
                "selectedInstanceMatched": False,
                "activeInstanceCount": 0,
                "vrcForgeToolsRegistered": False,
                "missingRequiredVrcForgeTools": [],
                "tools": {"totalTools": 0, "vrcForgeToolsCount": 0},
                "error": "",
            }
            tool_list_timeout = {
                "connected": True,
                "mcpServerReachable": True,
                "unityInstanceRegistered": True,
                "selectedInstanceMatched": True,
                "activeInstanceCount": 1,
                "activeInstance": {"project": project.name, "hash": "abc123"},
                "vrcForgeToolsRegistered": False,
                "missingRequiredVrcForgeTools": ["vrc_export_blendshapes"],
                "tools": {"totalTools": 0, "vrcForgeToolsCount": 0, "error": "tool list timed out"},
                "error": "tool list timed out",
            }
            healthy = {
                "connected": True,
                "mcpServerReachable": True,
                "unityInstanceRegistered": True,
                "selectedInstanceMatched": True,
                "activeInstanceCount": 1,
                "activeInstance": {"project": project.name, "hash": "abc123"},
                "vrcForgeToolsRegistered": True,
                "missingRequiredVrcForgeTools": [],
                "tools": {"totalTools": 78, "vrcForgeToolsCount": 48},
                "error": "",
            }
            status_snapshots = [offline, tool_list_timeout, healthy]
            observed_timeouts: list[int] = []

            def fake_status_snapshot(snapshot_settings: SimpleNamespace) -> dict[str, object]:
                observed_timeouts.append(snapshot_settings.unity_mcp_timeout_seconds)
                return status_snapshots.pop(0)

            with (
                patch("dashboard_server.load_dashboard_settings", return_value=settings),
                patch("dashboard_server.build_unity_status_snapshot", side_effect=fake_status_snapshot),
                patch("dashboard_server.ensure_unity_mcp_server_running", return_value=True),
                patch(
                    "dashboard_server.wait_for_unity_project_registration",
                    side_effect=[
                        (False, {"instances": []}),
                        (True, {"instances": [{"project": project.name, "hash": "abc123"}]}),
                    ],
                ),
                patch("dashboard_server.verify_unity_mcp_execution_connection", return_value=(True, {"tool": "vrc_check_roslyn_status"})),
                patch("dashboard_server.recent_unity_mcp_execution_error", return_value={}),
                patch("dashboard_server.close_unity_project_gracefully", return_value=(True, "Unity closed cleanly.", {})),
                patch("dashboard_server.launch_unity_project", return_value=(True, "")),
                patch("dashboard_server.time.sleep"),
            ):
                result = dashboard_server.repair_unity_mcp_bridge_sync(
                    dashboard_server.UnityMcpRepairRequest(
                        projectPath=str(project),
                        unityEditorPath=str(editor),
                        allowUnityRelaunch=True,
                        waitSeconds=12,
                    )
                )

        self.assertTrue(result["ok"])
        self.assertEqual(result["status"], "recovered")
        self.assertEqual(result["after"]["totalTools"], 78)
        self.assertEqual(observed_timeouts, [3, 10, 10])
        self.assertEqual(settings.unity_mcp_timeout_seconds, 3)

    def test_repair_unity_mcp_bridge_relaunch_keeps_actionable_error_when_tools_still_missing(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            project = root / "AvatarProject"
            editor = root / "Unity.exe"
            (project / "Assets").mkdir(parents=True)
            (project / "Packages").mkdir()
            (project / "ProjectSettings").mkdir()
            (project / "ProjectSettings" / "ProjectVersion.txt").write_text("m_EditorVersion: 2022.3.22f1\n", encoding="utf-8")
            editor.write_text("", encoding="utf-8")
            settings = SimpleNamespace(unity_mcp_timeout_seconds=30, unity_mcp_retries=3, unity_mcp_retry_backoff_seconds=1.0)
            offline = {
                "connected": True,
                "mcpServerReachable": True,
                "unityInstanceRegistered": False,
                "selectedInstanceMatched": False,
                "activeInstanceCount": 0,
                "vrcForgeToolsRegistered": False,
                "missingRequiredVrcForgeTools": [],
                "tools": {"totalTools": 0, "vrcForgeToolsCount": 0},
                "error": "",
            }
            registered_without_tools = {
                "connected": True,
                "mcpServerReachable": True,
                "unityInstanceRegistered": True,
                "selectedInstanceMatched": True,
                "activeInstanceCount": 1,
                "vrcForgeToolsRegistered": False,
                "totalTools": 0,
                "vrcForgeToolsCount": 0,
                "missingRequiredVrcForgeTools": ["vrc_export_blendshapes"],
                "toolsError": "tool list timed out",
                "error": "tool list timed out",
            }

            with (
                patch("dashboard_server.load_dashboard_settings", return_value=settings),
                patch("dashboard_server.build_unity_status_snapshot", return_value=offline),
                patch("dashboard_server.ensure_unity_mcp_server_running", return_value=True),
                patch(
                    "dashboard_server.wait_for_unity_project_registration",
                    side_effect=[
                        (False, {"instances": []}),
                        (True, {"instances": [{"project": project.name, "hash": "abc123"}]}),
                    ],
                ),
                patch("dashboard_server.wait_for_unity_tools_ready", return_value=(False, registered_without_tools)) as mock_wait_tools,
                patch("dashboard_server.register_vrcforge_unity_tools_from_project", return_value=(False, {"error": "no tools"})),
                patch("dashboard_server.recent_unity_mcp_execution_error", return_value={}),
                patch("dashboard_server.close_unity_project_gracefully", return_value=(True, "Unity closed cleanly.", {})),
                patch("dashboard_server.launch_unity_project", return_value=(True, "")),
            ):
                result = dashboard_server.repair_unity_mcp_bridge_sync(
                    dashboard_server.UnityMcpRepairRequest(
                        projectPath=str(project),
                        unityEditorPath=str(editor),
                        allowUnityRelaunch=True,
                        waitSeconds=12,
                    )
                )

        self.assertFalse(result["ok"])
        self.assertEqual(result["status"], "needs_user_action")
        self.assertFalse(result["after"]["vrcForgeToolsRegistered"])
        self.assertIn("unity_tools_after_launch", {phase["id"] for phase in result["phases"]})
        self.assertEqual(mock_wait_tools.call_args.args[0].unity_mcp_timeout_seconds, 10)

    def test_scene_capture_tool_supports_play_mode_game_view_status(self) -> None:
        source = (Path(__file__).resolve().parents[1] / "Assets" / "VRCForge" / "Editor" / "SceneViewCaptureTool.cs").read_text(
            encoding="utf-8"
        )

        self.assertIn("EditorApplication.isPlaying", source)
        self.assertIn("statusOnly", source)
        self.assertIn("requirePlayMode", source)
        self.assertIn('captureMode = isPlayMode ? "game_view" : "scene_view"', source)
        self.assertIn("ScreenCapture.CaptureScreenshotAsTexture", source)
        self.assertIn("CaptureCameraToPng(camera, absolutePath, width, height)", source)
        self.assertIn("active_game_camera", source)
        self.assertIn("avoid Gesture Manager menu overlays", source)
        self.assertIn("IsLikelyOverlayCamera", source)
        self.assertIn("IsGestureManagerRunning", source)
        self.assertIn("Gesture Manager recommended for accurate preview", source)
        self.assertIn("Play Mode with Gesture Manager is recommended", source)

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
            self.assertIn("components", payload)
            self.assertIn("paths", payload)
            self.assertIn("backend", payload["components"])
            self.assertIn("dashboardFiles", payload["components"])
            self.assertIn("configReadWrite", payload["components"])
            self.assertIn("logsWrite", payload["components"])
            self.assertIn("artifactsWrite", payload["components"])
            self.assertIn("selectedUnityProject", payload["components"])
            self.assertIn("unityPluginInstalled", payload["components"])
            self.assertIn("mcpPackageConfigured", payload["components"])
            self.assertIn("unityMcpBridgeReachable", payload["components"])
            self.assertIn("providerConfigPresent", payload["components"])
            self.assertEqual(payload["defaults"]["sourceMode"], "unity_live_export")
            self.assertFalse(payload["defaults"]["mockExecute"])
            self.assertNotIn("recentLogs", payload)
            self.assertEqual(payload["logRetentionHours"], 24)

    def test_windows_installer_sources_enforce_x64_and_release_gates(self) -> None:
        repo_root = Path(__file__).resolve().parents[1]
        build_script = (repo_root / "packaging" / "build_release.ps1").read_text(encoding="utf-8")
        publish_script = (repo_root / "packaging" / "publish_release.ps1").read_text(encoding="utf-8")
        launcher_project = (repo_root / "launcher" / "VRCForge.Launcher" / "VRCForge.Launcher.csproj").read_text(encoding="utf-8")
        offline_nsis = (repo_root / "installer" / "VRCForge_Offline_Installer_x64.nsi").read_text(encoding="utf-8")
        web_nsis = (repo_root / "installer" / "VRCForge_Web_Installer_x64.nsi").read_text(encoding="utf-8")

        self.assertIn("git status --short", build_script)
        self.assertIn("git log origin/main..HEAD --oneline", build_script)
        self.assertIn("git show origin/main:VERSION", build_script)
        self.assertIn("check_third_party_licenses.ps1", build_script)
        self.assertIn("check_coplaydev_mcp_license.ps1", build_script)
        self.assertIn("CoplayDev-Unity-MCP-DISTRIBUTION-NOTES.txt", build_script)
        self.assertIn("Install-UvRuntime", build_script)
        self.assertIn("uv-x86_64-pc-windows-msvc.zip", build_script)
        self.assertIn("uv-LICENSE-MIT.txt", build_script)
        self.assertIn("uv-LICENSE-APACHE-2.0.txt", build_script)
        self.assertIn("start_dashboard.cmd", build_script)
        self.assertIn("VRCForge-NOTICE.txt", build_script)
        self.assertIn("build_unitypackage.ps1", build_script)
        self.assertIn("PayloadDownloadUrl is required", build_script)
        self.assertIn("win-x64", build_script)
        self.assertIn("-p:DebugType=none", build_script)
        self.assertIn("-p:DebugSymbols=false", build_script)
        self.assertIn("Build-TauriDesktopApp", build_script)
        self.assertIn("vrcforge-agentic-app.exe", build_script)
        self.assertIn('Join-Path $payloadRoot "VRCForge.exe"', build_script)
        self.assertIn('Get-ChildItem -LiteralPath (Join-Path $payloadRoot "tools") -Recurse -Filter "*.ps1"', build_script)
        self.assertIn('Join-Path $payloadRoot "tools\\legacy-launcher"', build_script)
        self.assertIn('Remove-Item -LiteralPath (Join-Path $legacyLauncherBuildRoot "VRCForge.pdb")', build_script)
        self.assertIn("Resolve-DotNetExe", build_script)
        self.assertIn("Resolve-NpmExe", build_script)
        self.assertIn("Resolve-CargoExe", build_script)
        self.assertIn("Resolve-MakeNsisExe", build_script)
        self.assertIn("VRCForge_Web_Installer_x64.exe", publish_script)
        self.assertIn("VRCForge_Offline_Installer_x64.exe", publish_script)
        self.assertIn("VRCForge_Windows_x64_$Version.zip", publish_script)
        self.assertIn("VRCForge.unitypackage", publish_script)
        self.assertIn('(?i)(alpha|beta|rc)', publish_script)
        self.assertIn("targetCommitish", publish_script)
        self.assertIn("Existing GitHub Release $tag targets", publish_script)
        self.assertIn('release upload $tag @artifacts --clobber', publish_script)
        self.assertIn("win-x64", launcher_project)
        self.assertIn("<Platforms>x64</Platforms>", launcher_project)
        self.assertIn("<DebugType>none</DebugType>", launcher_project)
        self.assertIn("<DebugSymbols>false</DebugSymbols>", launcher_project)
        self.assertIn("VRCForge_Offline_Installer_x64.exe", offline_nsis)
        self.assertIn("VRCForge_Web_Installer_x64.exe", web_nsis)
        self.assertIn("$PROGRAMFILES64\\VRCForge", offline_nsis)
        self.assertIn("$LOCALAPPDATA\\VRCForge\\agentic-app\\config", offline_nsis)
        self.assertIn("$LOCALAPPDATA\\VRCForge\\agentic-app\\config", web_nsis)
        self.assertIn("nsDialogs.nsh", offline_nsis)
        self.assertIn("nsDialogs.nsh", web_nsis)
        self.assertIn("MUI_UNGETLANGUAGE", offline_nsis)
        self.assertIn("MUI_UNGETLANGUAGE", web_nsis)
        self.assertIn("UninstallShortcutName", offline_nsis)
        self.assertIn("UninstallShortcutName", web_nsis)
        self.assertIn('CreateShortCut "$SMPROGRAMS\\VRCForge\\$(UninstallShortcutName)"', offline_nsis)
        self.assertIn('CreateShortCut "$SMPROGRAMS\\VRCForge\\$(UninstallShortcutName)"', web_nsis)
        for shortcut_name in (
            "Uninstall VRCForge.lnk",
            "卸载 VRCForge.lnk",
            "解除安裝 VRCForge.lnk",
            "VRCForge をアンインストール.lnk",
        ):
            self.assertIn(shortcut_name, offline_nsis)
            self.assertIn(shortcut_name, web_nsis)
        self.assertIn("清除用户数据和历史对话", offline_nsis)
        self.assertIn("Clear user data and chat history", web_nsis)
        self.assertIn("--cleanup-user-data", offline_nsis)
        self.assertIn("--cleanup-user-data", web_nsis)
        self.assertNotIn("powershell -NoProfile", offline_nsis)
        self.assertNotIn("powershell -NoProfile", web_nsis)
        self.assertNotIn("NSISdl::download", web_nsis)
        self.assertIn("certutil -urlcache -f", web_nsis)
        self.assertIn("certutil -hashfile", web_nsis)
        self.assertIn("Pop $0", offline_nsis)
        self.assertIn("Pop $0", web_nsis)
        self.assertIn("Call un.ClearUserDataIfRequested", offline_nsis)
        self.assertIn("Call un.ClearUserDataIfRequested", web_nsis)

    def test_cleanup_user_data_root_removes_appdata_and_known_project_transcripts(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir) / "VRCForge" / "agentic-app"
            project = Path(temp_dir) / "AvatarProject"
            project_transcript = project / ".vrcforge" / "chat-transcripts.json"
            project_transcript.parent.mkdir(parents=True)
            project_transcript.write_text("{}", encoding="utf-8")
            root.mkdir(parents=True)
            (root / "chat-projects.json").write_text(json.dumps({"projectPaths": [str(project)]}), encoding="utf-8")

            payload = dashboard_server.cleanup_user_data_root(root)

            self.assertTrue(payload["ok"])
            self.assertTrue(payload["rootRemoved"])
            self.assertEqual(payload["projectTranscriptCount"], 1)
            self.assertFalse(root.exists())
            self.assertFalse(project_transcript.exists())

    def test_cleanup_user_data_root_rejects_unrelated_paths(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            with self.assertRaisesRegex(RuntimeError, "outside the VRCForge agentic-app"):
                dashboard_server.cleanup_user_data_root(Path(temp_dir) / "not-vrcforge")

    def test_project_install_request_accepts_camel_case_project_path(self) -> None:
        request = dashboard_server.ProjectInstallRequest(projectPath="C:/AvatarProject")
        self.assertEqual(request.project_path, "C:/AvatarProject")

    def test_coplaydev_mcp_distribution_notes_are_present(self) -> None:
        repo_root = Path(__file__).resolve().parents[1]
        package_root = repo_root / "third_party" / "com.coplaydev.unity-mcp"
        license_text = (package_root / "LICENSE").read_text(encoding="utf-8")
        notes_text = (package_root / "VRCFORGE_DISTRIBUTION_NOTES.txt").read_text(encoding="utf-8")
        manifest_text = (repo_root / "packaging" / "THIRD_PARTY_LICENSES.json").read_text(encoding="utf-8")
        general_gate = (repo_root / "packaging" / "check_third_party_licenses.ps1").read_text(encoding="utf-8")
        license_gate = (repo_root / "packaging" / "check_coplaydev_mcp_license.ps1").read_text(encoding="utf-8")

        self.assertIn("MIT License", license_text)
        self.assertIn("Copyright (c) 2025 CoplayDev", license_text)
        self.assertIn("https://github.com/CoplayDev/unity-mcp", notes_text)
        self.assertIn("b98193db05e9a2906f491f244ccdd1766283cab3", notes_text)
        self.assertIn("CoplayDev Unity MCP", manifest_text)
        self.assertIn("requiredLicenseText", manifest_text)
        self.assertIn("requiredDistributionNotes", manifest_text)
        self.assertIn("Third-party license gate passed", general_gate)
        self.assertIn("Copyright \\(c\\) 2025 CoplayDev", license_gate)
        self.assertIn("VRCFORGE_DISTRIBUTION_NOTES.txt", license_gate)

    def test_uv_runtime_license_gate_and_launcher_bootstrap_are_present(self) -> None:
        repo_root = Path(__file__).resolve().parents[1]
        uv_root = repo_root / "third_party" / "uv-runtime"
        manifest_text = (repo_root / "packaging" / "THIRD_PARTY_LICENSES.json").read_text(encoding="utf-8")
        general_gate = (repo_root / "packaging" / "check_third_party_licenses.ps1").read_text(encoding="utf-8")
        runtime_manager = (repo_root / "launcher" / "VRCForge.Launcher" / "RuntimeDependencyManager.cs").read_text(encoding="utf-8")
        backend_process = (repo_root / "launcher" / "VRCForge.Launcher" / "BackendProcess.cs").read_text(encoding="utf-8")
        main_form = (repo_root / "launcher" / "VRCForge.Launcher" / "MainForm.cs").read_text(encoding="utf-8")
        start_cmd = (repo_root / "start_dashboard.cmd").read_text(encoding="utf-8")

        self.assertIn("MIT License", (uv_root / "LICENSE-MIT").read_text(encoding="utf-8"))
        self.assertIn("Apache License", (uv_root / "LICENSE-APACHE").read_text(encoding="utf-8"))
        self.assertIn("uv Windows runtime", manifest_text)
        self.assertIn("requiredLicenseFiles", manifest_text)
        self.assertIn("Assert-LicenseFile", general_gate)
        self.assertIn("uv-x86_64-pc-windows-msvc.zip", runtime_manager)
        self.assertIn("mcpforunityserver", runtime_manager)
        self.assertIn("BundledUvxExe", runtime_manager)
        self.assertIn("UV_PYTHON_INSTALL_DIR", backend_process)
        self.assertIn("StartViaCmdFallback", backend_process)
        self.assertIn("Dashboard HTTP page is reachable", backend_process)
        self.assertIn("start_dashboard.cmd fallback", main_form)
        self.assertIn("启动 Dashboard", main_form)
        self.assertNotIn("安装 / 更新 Unity 插件", main_form)
        self.assertNotIn("外部 Agent 接入 / 打开 Dashboard", main_form)
        self.assertIn("backend\\vrcforge_backend.exe", start_cmd)
        self.assertIn("VRCFORGE_DASHBOARD_DIR", start_cmd)
        self.assertNotIn("start-dashboard.ps1", start_cmd)
        self.assertNotIn("powershell", start_cmd.lower())

    def test_unity_project_install_uses_project_backups_and_local_mcp(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            project = Path(temp_dir) / "AvatarProject"
            (project / "Assets" / "VRCAutoRig").mkdir(parents=True)
            (project / "Packages").mkdir(parents=True)
            (project / "ProjectSettings").mkdir(parents=True)
            (project / "Packages" / "manifest.json").write_text('{"dependencies":{}}\n', encoding="utf-8")
            (project / "ProjectSettings" / "ProjectVersion.txt").write_text("m_EditorVersion: 2022.3.22f1\n", encoding="utf-8")

            payload = dashboard_server.install_vrcforge_into_unity_project(project)

            self.assertIn(".vrcforge", payload["backupRoot"])
            self.assertTrue((project / "Assets" / "VRCForge").is_dir())
            self.assertFalse((project / "Assets" / "VRCAutoRig").exists())
            self.assertIn("legacy", payload["backups"])
            manifest = json.loads((project / "Packages" / "manifest.json").read_text(encoding="utf-8"))
            self.assertEqual(manifest["dependencies"]["com.coplaydev.unity-mcp"], "file:Packages/com.coplaydev.unity-mcp")
            self.assertTrue((project / "Packages" / "com.coplaydev.unity-mcp").is_dir())

    def test_unity_project_install_rolls_back_when_manifest_update_fails(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            project = Path(temp_dir) / "AvatarProject"
            legacy = project / "Assets" / "VRCAutoRig"
            legacy.mkdir(parents=True)
            (legacy / "legacy.txt").write_text("before", encoding="utf-8")
            (project / "Packages").mkdir(parents=True)
            (project / "ProjectSettings").mkdir(parents=True)
            (project / "Packages" / "manifest.json").write_text("[]", encoding="utf-8")
            (project / "ProjectSettings" / "ProjectVersion.txt").write_text("m_EditorVersion: 2022.3.22f1\n", encoding="utf-8")

            with self.assertRaisesRegex(RuntimeError, "manifest root is not an object"):
                dashboard_server.install_vrcforge_into_unity_project(project)

            self.assertTrue((project / "Assets" / "VRCAutoRig" / "legacy.txt").is_file())
            self.assertFalse((project / "Assets" / "VRCForge").exists())
            self.assertFalse((project / "Packages" / "com.coplaydev.unity-mcp").exists())
            self.assertEqual((project / "Packages" / "manifest.json").read_text(encoding="utf-8"), "[]")

    def test_unity_mcp_restart_reuses_uvx_unity_mcp_command(self) -> None:
        phases: list[dict[str, object]] = []
        settings = SimpleNamespace(unity_mcp_host="127.0.0.1", unity_mcp_port=8080, unity_mcp_command=[])
        processes = [
            {
                "processId": 123,
                "name": "uvx.exe",
                "executablePath": "C:\\Tools\\uvx.exe",
                "commandLine": "uvx --from mcpforunityserver unity-mcp --transport http --http-url http://127.0.0.1:8080",
            }
        ]
        popen_calls: list[list[str]] = []

        def fake_popen(command, **_kwargs):
            popen_calls.append(list(command))
            return SimpleNamespace(pid=456)

        with (
            patch("dashboard_server.list_running_unity_mcp_processes", return_value=processes),
            patch("dashboard_server.stop_unity_mcp_processes", return_value=(True, {"stopped": [123], "stillRunning": []}, "")),
            patch("dashboard_server.fetch_unity_http_json", return_value=(False, None, "offline", None)),
            patch("dashboard_server.Path.exists", return_value=True),
            patch("dashboard_server.subprocess.Popen", side_effect=fake_popen),
            patch("dashboard_server.wait_for_mcp_health", return_value=True),
        ):
            self.assertTrue(dashboard_server.restart_unity_mcp_server(settings, phases, 1))

        self.assertTrue(popen_calls)
        self.assertEqual(popen_calls[0][:4], ["C:\\Tools\\uvx.exe", "--from", "mcpforunityserver", "unity-mcp"])

    def test_unity_mcp_fresh_start_prefers_configured_command_over_legacy_exe(self) -> None:
        phases: list[dict[str, object]] = []
        settings = SimpleNamespace(
            unity_mcp_host="127.0.0.1",
            unity_mcp_port=8080,
            unity_mcp_command=["C:\\Tools\\uvx.exe", "--from", "mcpforunityserver", "unity-mcp"],
        )
        popen_calls: list[list[str]] = []

        def fake_popen(command, **_kwargs):
            popen_calls.append(list(command))
            return SimpleNamespace(pid=456)

        with (
            patch("dashboard_server.fetch_unity_http_json", return_value=(False, None, "offline", None)),
            patch("dashboard_server.find_unity_mcp_command_prefix", return_value=["C:\\Old\\mcp-for-unity.exe"]),
            patch("dashboard_server.subprocess.Popen", side_effect=fake_popen),
            patch("dashboard_server.wait_for_mcp_health", return_value=True),
        ):
            self.assertTrue(dashboard_server.ensure_unity_mcp_server_running(settings, phases, 1, force_start=True))

        self.assertTrue(popen_calls)
        self.assertEqual(popen_calls[0][:4], ["C:\\Tools\\uvx.exe", "--from", "mcpforunityserver", "unity-mcp"])

    def test_unity_mcp_discovery_prefers_current_cli_over_legacy_exe(self) -> None:
        with (
            patch("dashboard_server.find_unity_mcp_executable", return_value=Path("C:/Tools/unity-mcp.exe")),
            patch("dashboard_server.find_mcp_for_unity_executable", return_value=Path("C:/Old/mcp-for-unity.exe")),
        ):
            self.assertEqual(dashboard_server.find_unity_mcp_command_prefix(), ["C:\\Tools\\unity-mcp.exe"])

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
                "assetDir: Assets/VRCForge/Generated/FX\n"
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
        self.assertEqual(payload["assetDir"], "Assets/VRCForge/Generated/FX")

    def test_provider_config_default_path_is_outside_source_root(self) -> None:
        if os.environ.get("VRCFORGE_CONFIG_PATH") or os.environ.get("VRCFORGE_CONFIG_DIR"):
            self.skipTest("config location is explicitly overridden")

        source_root_config = (dashboard_server.ROOT_DIR / "config.json").resolve()

        self.assertNotEqual(dashboard_server.CONFIG_PATH.resolve(), source_root_config)
        self.assertNotEqual(dashboard_server.CONFIG_PATH.resolve().parent, dashboard_server.ROOT_DIR.resolve())
        self.assertEqual(dashboard_server.CONFIG_PATH.resolve().parent, dashboard_server.CONFIG_DIR.resolve())

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

        with patch("dashboard_server.create_legacy_write_checkpoint", return_value={"ok": True, "id": "ckpt_test"}):
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
                "imageUrl": f"/artifacts/latest/blendshape_{stage}.png",
            }
            return proof

        mock_capture_blendshape_visual_proof.side_effect = capture_proof_side_effect

        with patch("dashboard_server.create_legacy_write_checkpoint", return_value={"ok": True, "id": "ckpt_test"}):
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

    def test_preset_limit_trims_latest_presets_per_avatar(self) -> None:
        presets = [
            {"id": "a_old", "avatar_path": "AvatarA"},
            {"id": "a_mid", "avatar_path": "AvatarA"},
            {"id": "b_keep", "avatar_path": "AvatarB"},
            {"id": "a_new", "avatar_path": "AvatarA"},
        ]

        trimmed = dashboard_server.trim_presets_for_avatar(presets, 2)

        self.assertEqual([item["id"] for item in trimmed], ["a_mid", "a_new", "b_keep"])

    def test_ai_lock_selection_only_accepts_candidate_pairs(self) -> None:
        candidates = [
            {"rendererPath": "Avatar/Face", "blendshapeName": "EyeSmile_L"},
            {"rendererPath": "Avatar/Face", "blendshapeName": "MouthSmile"},
        ]

        selected = dashboard_server.validate_ai_lock_selection(
            {
                "selected": [
                    {"rendererPath": "Avatar/Face", "blendshapeName": "EyeSmile_L"},
                    {"rendererPath": "Avatar/Face", "blendshapeName": "EyeSmile_L"},
                    {"rendererPath": "Avatar/Face", "blendshapeName": "Hair_Fluffy"},
                ]
            },
            candidates,
        )

        self.assertEqual(selected, [{"rendererPath": "Avatar/Face", "blendshapeName": "EyeSmile_L"}])

    @patch("dashboard_server.request_llm_plan")
    @patch("dashboard_server.load_dashboard_settings")
    def test_ai_lock_selection_endpoint_returns_model_selected_blendshapes(
        self,
        mock_load_settings,
        mock_request_llm_plan,
    ) -> None:
        mock_load_settings.return_value = SimpleNamespace(llm_provider="ollama", llm_api_key="")
        mock_request_llm_plan.return_value = json.dumps(
            {
                "summary": "eye area",
                "selected": [
                    {"rendererPath": "Avatar/Face", "blendshapeName": "EyeSmile_L"},
                    {"rendererPath": "Avatar/Face", "blendshapeName": "MouthSmile"},
                    {"rendererPath": "Avatar/Face", "blendshapeName": "NotACandidate"},
                ],
            }
        )

        with TestClient(dashboard_server.app) as client:
            response = client.post(
                "/api/tuning/locks/ai-select",
                json={
                    "source_mode": "mvp_sample",
                    "avatar_path": "Avatar",
                    "action": "unlock",
                    "selection_instruction": "解锁眼睛相关形态键",
                    "candidate_blendshapes": [
                        {"rendererPath": "Avatar/Face", "blendshapeName": "EyeSmile_L"},
                        {"rendererPath": "Avatar/Face", "blendshapeName": "MouthSmile"},
                    ],
                    "current_locked_blendshapes": [
                        {"rendererPath": "Avatar/Face", "blendshapeName": "EyeSmile_L"}
                    ],
                },
            )

        self.assertEqual(response.status_code, 200)
        payload = response.json()
        self.assertEqual(payload["selectedBlendshapes"], [{"rendererPath": "Avatar/Face", "blendshapeName": "EyeSmile_L"}])
        self.assertIn("解锁眼睛", mock_request_llm_plan.call_args.args[1])

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

    def test_shader_plan_validation_allows_generic_semantic_fallback(self) -> None:
        inventory = make_shader_inventory()
        inventory["materials"].append(
            {
                "material_id": "mat_generic",
                "material_name": "Standardish",
                "shader_family": "Generic",
                "category": "clothes",
                "supported_properties": {
                    "base_color": {"type": "color", "value": "#FFFFFFFF", "writable": True},
                    "smoothness": {"type": "float", "value": 0.4, "writable": True},
                },
            }
        )

        validation = dashboard_server.validate_shader_material_tuning_plan(
            plan={
                "type": "material_tuning_plan",
                "version": "0.2",
                "changes": [
                    {"material_id": "mat_generic", "semantic_property": "smoothness", "after": 0.75},
                    {"material_id": "mat_generic", "shader_property": "_Color", "semantic_property": "_Color", "after": "#000000"},
                ],
            },
            inventory=inventory,
        )

        self.assertEqual(validation["validatedChanges"][0]["after"], 0.75)
        self.assertEqual(validation["skippedChanges"][0]["warning"], "Real shader property names are not accepted; use semantic_property only.")

    def test_unity_shader_adapter_source_keeps_poiyomi_and_generic_fallback(self) -> None:
        source = Path("Assets/VRCForge/Editor/ShaderMaterialAdapters.cs").read_text(encoding="utf-8-sig")

        self.assertIn("new PoiyomiShaderAdapter()", source)
        self.assertIn("new GenericShaderAdapter()", source)
        self.assertIn('base("Generic"', source)
        self.assertIn('"_BaseColor"', source)

    def test_shader_fixture_tool_sets_named_shader_with_undo_and_save(self) -> None:
        source = Path("Assets/VRCForge/Editor/ShaderFixtureTool.cs").read_text(encoding="utf-8-sig")

        self.assertIn('name: "vrc_set_material_shader"', source)
        self.assertIn("ResolveShader(shaderName, shaderAssetPath)", source)
        self.assertIn("shaderAssetPath", source)
        self.assertIn("LoadExplicitShaderAtAssetPath(shaderAssetPath)", source)
        self.assertIn("AssetDatabase.ImportAsset(normalizedPath, ImportAssetOptions.ForceSynchronousImport)", source)
        self.assertIn("AssetDatabase.FindAssets", source)
        self.assertIn("AssetDatabase.LoadAssetAtPath<Shader>", source)
        self.assertIn("Undo.RecordObject", source)
        self.assertIn("target.material.shader = shader", source)
        self.assertIn("AssetDatabase.SaveAssets", source)
        self.assertIn("rendererPath or materialAssetPath is required", source)

    def test_avatar_encryption_public_repo_keeps_only_connector_boundary(self) -> None:
        self.assertFalse(Path("Assets/VRCForge/Editor/AvatarEncryptionTool.cs").exists())
        self.assertFalse(Path("Assets/VRCForge/Runtime/AvatarEncryption/VRCForgeAvatarEncryptionRestore.shader").exists())
        source = Path("dashboard_server.py").read_text(encoding="utf-8-sig")
        self.assertIn("VRCFORGE_AVATAR_ENCRYPTION_ADDON_URL", source)
        self.assertIn("vrcforge_avatar_encryption_addon_apply", source)
        self.assertIn("vrcforge_avatar_encryption_addon_remove", source)
        self.assertNotIn("vrc_apply_avatar_encryption", source)
        self.assertNotIn("vrc_remove_avatar_encryption", source)

    def test_shader_adapter_smoke_script_uses_supervised_paths(self) -> None:
        source = Path("scripts/smoke_shader_adapter_apply_rollback.py").read_text(encoding="utf-8-sig")

        self.assertIn("vrcforge.shader_adapter_apply_rollback_smoke.v1", source)
        self.assertIn("/api/app/package-install/request", source)
        self.assertIn("vrcforge_unity_mcp_write", source)
        self.assertIn("vrc_set_material_shader", source)
        self.assertIn("vrcforge_apply_shader_tuning", source)
        self.assertIn('"projectPath": self.project_root', source)
        self.assertIn("/api/app/doctor/unity-mcp/repair", source)
        self.assertIn("/api/app/checkpoints/{checkpoint_id}/restore", source)
        self.assertIn("vrcforge.path_to_skill.v1", source)

    def test_agent_mcp_stdio_supports_no_start_flag(self) -> None:
        args = dashboard_server.parse_args(["--agent-mcp-stdio", "--no-start"])

        self.assertTrue(args.agent_mcp_stdio)
        self.assertTrue(args.no_start)
        self.assertFalse(args.start_runtime)

    def test_agent_mcp_stdio_start_runtime_is_explicit_opt_in(self) -> None:
        default_args = dashboard_server.parse_args(["--agent-mcp-stdio"])
        start_args = dashboard_server.parse_args(["--agent-mcp-stdio", "--start-runtime"])

        self.assertTrue(default_args.agent_mcp_stdio)
        self.assertFalse(default_args.start_runtime)
        self.assertTrue(start_args.start_runtime)

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

        with patch("dashboard_server.create_legacy_write_checkpoint", return_value={"ok": True, "id": "ckpt_test"}):
            with TestClient(dashboard_server.app) as client:
                response = client.post(
                    "/api/shader/presets/shader_preset_test/apply",
                    json={"avatar": "Scene/HeroAvatar", "source_mode": "unity_live_export"},
                )

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()["appliedChanges"][0]["after"], 0.8)

    @patch("dashboard_server.apply_shader_material_tuning_direct")
    @patch("dashboard_server.load_dashboard_settings")
    def test_shader_apply_reconstructs_undo_when_unity_flattens_applied_list(
        self,
        mock_load_settings,
        mock_apply_shader_material_tuning_direct,
    ) -> None:
        mock_load_settings.return_value = SimpleNamespace()
        dashboard_server.DASHBOARD_RUNTIME.shader_undo_stack.clear()

        mock_apply_shader_material_tuning_direct.side_effect = [
            {"ok": True, "appliedCount": 1, "applied": [], "skipped": []},
            {"ok": True, "appliedCount": 1, "applied": [], "skipped": []},
        ]

        apply_payload = dashboard_server.apply_shader_material_plan_sync(
            dashboard_server.ShaderMaterialApplyRequest(
                avatar_path="Scene/HeroAvatar",
                inventory=make_shader_inventory(),
                changes=[{"material_id": "mat_skin", "semantic_property": "smoothness", "after": 0.8}],
            )
        )

        self.assertTrue(apply_payload["ok"])
        self.assertEqual(apply_payload["appliedChanges"][0]["before"], 0.2)
        self.assertEqual(apply_payload["appliedChanges"][0]["after"], 0.8)
        self.assertEqual(apply_payload["undoDepth"], 1)

        restore_payload = dashboard_server.restore_shader_material_plan_sync(
            dashboard_server.ShaderMaterialRestoreRequest(avatar_path="Scene/HeroAvatar")
        )

        self.assertTrue(restore_payload["ok"])
        self.assertEqual(restore_payload["restoredChanges"][0]["after"], 0.2)
        self.assertEqual(restore_payload["undoDepth"], 0)

    def test_legacy_write_checkpoint_failure_blocks_callback(self) -> None:
        called = False

        def callback() -> dict:
            nonlocal called
            called = True
            return {"ok": True}

        with patch(
            "dashboard_server.create_legacy_write_checkpoint",
            side_effect=dashboard_server.HTTPException(status_code=409, detail="checkpoint failed"),
        ):
            with self.assertRaises(dashboard_server.HTTPException):
                dashboard_server.run_legacy_write_with_checkpoint(
                    "vrcforge_apply_shader_tuning",
                    dashboard_server.ShaderMaterialApplyRequest(
                        avatar_path="Scene/HeroAvatar",
                        inventory=make_shader_inventory(),
                        changes=[{"material_id": "mat_skin", "semantic_property": "smoothness", "after": 0.8}],
                    ),
                    callback,
                )

        self.assertFalse(called)

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
        self.assertEqual(params["outputPath"], "")

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
        _settings, tool_name, params = mock_invoke_unity_mcp.call_args.args
        self.assertEqual(tool_name, "vrc_scan_avatar_parameters")
        self.assertEqual(params["outputPath"], "")

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
        self.assertTrue(response.json()["imageUrl"].startswith("/artifacts/latest/vision_capture.png?"))
        self.assertIn("artifact_sig=", response.json()["imageUrl"])
        _settings, tool_name, params = mock_invoke_unity_mcp.call_args.args
        self.assertEqual(tool_name, "vrc_capture_scene_view")
        self.assertFalse(params["setRotation"])
        self.assertEqual(params["avatarPath"], "Scene/HeroAvatar")
        self.assertFalse(params["requirePlayMode"])

    @patch("dashboard_server.invoke_unity_mcp")
    @patch("dashboard_server.load_dashboard_settings")
    def test_vision_capture_status_uses_scene_capture_status_mode(
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
                    "isPlayMode": False,
                    "captureMode": "scene_view",
                    "gestureManagerDetected": False,
                    "warnings": ["Play Mode with Gesture Manager is recommended"],
                }
            },
        )

        with TestClient(dashboard_server.app) as client:
            response = client.post("/api/vision/capture-status", json={"require_play_mode": False})

        self.assertEqual(response.status_code, 200)
        payload = response.json()
        self.assertTrue(payload["ok"])
        self.assertFalse(payload["isPlayMode"])
        _settings, tool_name, params = mock_invoke_unity_mcp.call_args.args
        self.assertEqual(tool_name, "vrc_capture_scene_view")
        self.assertTrue(params["statusOnly"])
        self.assertFalse(params["requirePlayMode"])

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
            (project_dir / "Assets" / "VRCForge" / "Editor").mkdir(parents=True)

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
            (project_dir / "Assets" / "VRCForge" / "Editor" / "BlendshapeExporter.cs").write_text(
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
            self.assertTrue(projects[0]["hasVrcForge"])
            self.assertTrue(projects[0]["hasUnityMcpPackage"])
            self.assertTrue(projects[0]["selected"])

    def test_project_snapshot_refresh_updates_cache_for_fast_bootstrap(self) -> None:
        originals = {
            "roots": list(dashboard_server.DASHBOARD_STATE.project_roots),
            "selected": dashboard_server.DASHBOARD_STATE.selected_project_path,
            "cache": dashboard_server.PROJECT_SNAPSHOT_CACHE,
            "refreshing": dashboard_server.PROJECT_SNAPSHOT_REFRESHING,
            "updated": dashboard_server.PROJECT_SNAPSHOT_UPDATED_AT,
            "started": dashboard_server.PROJECT_SNAPSHOT_STARTED_AT,
            "error": dashboard_server.PROJECT_SNAPSHOT_LAST_ERROR,
            "duration": dashboard_server.PROJECT_SNAPSHOT_LAST_DURATION_MS,
            "changes": dashboard_server.PROJECT_SNAPSHOT_LAST_CHANGES,
            "cache_monotonic": dashboard_server.PROJECT_SNAPSHOT_CACHE_MONOTONIC,
            "started_monotonic": dashboard_server.PROJECT_SNAPSHOT_REFRESH_STARTED_MONOTONIC,
            "loaded": dashboard_server.PROJECT_SNAPSHOT_CACHE_LOADED,
            "cache_path": dashboard_server.PROJECT_SNAPSHOT_CACHE_PATH,
            "unity_status": dashboard_server.CURRENT_UNITY_STATUS,
        }
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            project_dir = root / "Cached Project"
            stale_project = root / "Removed Project"
            (project_dir / "ProjectSettings").mkdir(parents=True)
            (project_dir / "Packages").mkdir(parents=True)
            (project_dir / "Assets").mkdir(parents=True)
            (project_dir / "ProjectSettings" / "ProjectVersion.txt").write_text(
                "m_EditorVersion: 2022.3.22f1\n",
                encoding="utf-8",
            )
            dashboard_server.DASHBOARD_STATE.project_roots = [root]
            dashboard_server.DASHBOARD_STATE.selected_project_path = dashboard_server.normalize_path_string(str(project_dir))
            dashboard_server.PROJECT_SNAPSHOT_CACHE = {
                "selectedProjectPath": "",
                "unityEditorPath": "",
                "projects": [
                    {
                        "name": "Removed Project",
                        "path": dashboard_server.normalize_path_string(str(stale_project)),
                        "sources": ["cached-test"],
                    }
                ],
            }
            dashboard_server.PROJECT_SNAPSHOT_REFRESHING = False
            dashboard_server.PROJECT_SNAPSHOT_UPDATED_AT = ""
            dashboard_server.PROJECT_SNAPSHOT_STARTED_AT = ""
            dashboard_server.PROJECT_SNAPSHOT_LAST_ERROR = ""
            dashboard_server.PROJECT_SNAPSHOT_LAST_DURATION_MS = 0
            dashboard_server.PROJECT_SNAPSHOT_LAST_CHANGES = {}
            dashboard_server.PROJECT_SNAPSHOT_CACHE_MONOTONIC = 0.0
            dashboard_server.PROJECT_SNAPSHOT_REFRESH_STARTED_MONOTONIC = 0.0
            dashboard_server.PROJECT_SNAPSHOT_CACHE_LOADED = True
            dashboard_server.PROJECT_SNAPSHOT_CACHE_PATH = root / "project-cache.json"
            dashboard_server.CURRENT_UNITY_STATUS = {"instances": []}
            try:
                with (
                    patch("dashboard_server.discover_vcc_projects", return_value=[]),
                    patch("dashboard_server.discover_alcom_projects", return_value=[]),
                    patch("dashboard_server.discover_unity_hub_projects", return_value=[]),
                    patch("dashboard_server.discover_running_unity_projects", return_value=[]),
                    patch("dashboard_server.load_project_prefs", return_value={"customPaths": [], "hiddenPaths": []}),
                ):
                    refreshed = dashboard_server.refresh_project_snapshot_cache_sync()
                    cached = dashboard_server.project_snapshot_payload(use_cache=True, refresh_async=False)

                self.assertEqual(refreshed["scan"]["status"], "ready")
                self.assertFalse(refreshed["scan"]["refreshing"])
                self.assertEqual(cached["scan"]["status"], "ready")
                self.assertTrue(cached["scan"]["cached"])
                self.assertEqual(len(cached["projects"]), 1)
                self.assertEqual(cached["projects"][0]["name"], "Cached Project")
                self.assertEqual(cached["scan"]["addedCount"], 1)
                self.assertEqual(cached["scan"]["removedCount"], 1)
                self.assertEqual(cached["scan"]["addedProjects"][0]["name"], "Cached Project")
                self.assertEqual(cached["scan"]["removedProjects"][0]["name"], "Removed Project")
                self.assertTrue(dashboard_server.PROJECT_SNAPSHOT_CACHE_PATH.is_file())
            finally:
                dashboard_server.DASHBOARD_STATE.project_roots = originals["roots"]
                dashboard_server.DASHBOARD_STATE.selected_project_path = originals["selected"]
                dashboard_server.PROJECT_SNAPSHOT_CACHE = originals["cache"]
                dashboard_server.PROJECT_SNAPSHOT_REFRESHING = originals["refreshing"]
                dashboard_server.PROJECT_SNAPSHOT_UPDATED_AT = originals["updated"]
                dashboard_server.PROJECT_SNAPSHOT_STARTED_AT = originals["started"]
                dashboard_server.PROJECT_SNAPSHOT_LAST_ERROR = originals["error"]
                dashboard_server.PROJECT_SNAPSHOT_LAST_DURATION_MS = originals["duration"]
                dashboard_server.PROJECT_SNAPSHOT_LAST_CHANGES = originals["changes"]
                dashboard_server.PROJECT_SNAPSHOT_CACHE_MONOTONIC = originals["cache_monotonic"]
                dashboard_server.PROJECT_SNAPSHOT_REFRESH_STARTED_MONOTONIC = originals["started_monotonic"]
                dashboard_server.PROJECT_SNAPSHOT_CACHE_LOADED = originals["loaded"]
                dashboard_server.PROJECT_SNAPSHOT_CACHE_PATH = originals["cache_path"]
                dashboard_server.CURRENT_UNITY_STATUS = originals["unity_status"]

    def test_discover_projects_merges_active_mcp_instance_by_name(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            project_dir = root / "milltina"
            (project_dir / "ProjectSettings").mkdir(parents=True)
            (project_dir / "Packages").mkdir(parents=True)
            (project_dir / "Assets" / "VRCForge" / "Editor").mkdir(parents=True)
            (project_dir / "ProjectSettings" / "ProjectVersion.txt").write_text(
                "m_EditorVersion: 2022.3.22f1\n",
                encoding="utf-8",
            )
            (project_dir / "Packages" / "manifest.json").write_text(
                json.dumps({"dependencies": {"com.coplaydev.unity-mcp": "file:Packages/com.coplaydev.unity-mcp"}}),
                encoding="utf-8",
            )

            original_status = dashboard_server.CURRENT_UNITY_STATUS
            dashboard_server.CURRENT_UNITY_STATUS = {
                "instances": [
                    {
                        "project": "milltina",
                        "projectName": "milltina",
                        "projectPath": "",
                        "unityVersion": "2022.3.22f1",
                        "sessionId": "session-123",
                        "cliInstanceId": "hash-456",
                    }
                ]
            }
            try:
                with patch("dashboard_server.discover_vcc_projects", return_value=[]), patch(
                    "dashboard_server.discover_unity_hub_projects", return_value=[]
                ), patch(
                    "dashboard_server.discover_running_unity_projects", return_value=[]
                ):
                    projects = dashboard_server.discover_projects([root], include_external=True)
            finally:
                dashboard_server.CURRENT_UNITY_STATUS = original_status

            milltina = [project for project in projects if project["name"] == "milltina"]
            self.assertEqual(len(milltina), 1)
            self.assertEqual(milltina[0]["path"], dashboard_server.normalize_path_string(str(project_dir)))
            self.assertTrue(milltina[0]["activeMcp"])
            self.assertEqual(milltina[0]["cliInstanceId"], "hash-456")

    def test_discover_projects_merges_vcc_alcom_and_unity_hub_sources(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            project_dir = root / "Avatar Project"
            (project_dir / "Assets").mkdir(parents=True)
            (project_dir / "Packages").mkdir(parents=True)
            (project_dir / "ProjectSettings").mkdir(parents=True)
            (project_dir / "ProjectSettings" / "ProjectVersion.txt").write_text(
                "m_EditorVersion: 2022.3.22f1\n",
                encoding="utf-8",
            )

            vcc_settings = root / "vcc-settings.json"
            alcom_settings = root / "vrc-get-settings.json"
            unity_hub_projects = root / "projects-v1.json"
            vcc_settings.write_text(json.dumps({"userProjects": [str(project_dir)]}), encoding="utf-8")
            alcom_settings.write_text(json.dumps({"projects": [{"path": str(project_dir)}]}), encoding="utf-8")
            unity_hub_projects.write_text(
                json.dumps({"data": {str(project_dir): {"path": str(project_dir), "title": "Avatar Project", "version": "2022.3.22f1"}}}),
                encoding="utf-8",
            )

            with patch("dashboard_server.discover_vcc_projects", return_value=[str(project_dir)]), patch(
                "dashboard_server.discover_alcom_projects", return_value=[str(project_dir)]
            ), patch("dashboard_server.discover_unity_hub_projects", return_value=[
                {"name": "Avatar Project", "path": str(project_dir), "editorVersion": "2022.3.22f1"}
            ]), patch("dashboard_server.discover_running_unity_projects", return_value=[]):
                projects = dashboard_server.discover_projects([], include_external=True)

            self.assertEqual(len(projects), 1)
            self.assertEqual(projects[0]["name"], "Avatar Project")
            self.assertEqual(projects[0]["sources"], ["vcc", "alcom", "unity-hub"])
            self.assertEqual(projects[0]["editorVersion"], "2022.3.22f1")

            self.assertEqual(dashboard_server.discover_projects_from_settings_files([vcc_settings]), [dashboard_server.normalize_path_string(str(project_dir))])
            self.assertEqual(dashboard_server.discover_projects_from_settings_files([alcom_settings]), [dashboard_server.normalize_path_string(str(project_dir))])

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
        self.assertTrue(url.startswith("/artifacts/latest/vision_capture.png?"))
        self.assertIn("artifact_expires=", url)
        self.assertIn("artifact_sig=", url)

    def test_optimizer_proof_index_detail_and_screenshot_are_read_only(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            original_artifacts_dir = dashboard_server.ARTIFACTS_DIR
            temp_artifacts = Path(temp_dir) / "artifacts"
            dashboard_server.ARTIFACTS_DIR = temp_artifacts
            proof_root = temp_artifacts / "optimizer-apply-smoke"
            run_id = "optimizer-apply-smoke-20260624-010101"
            screenshot = proof_root / run_id / "screenshots" / "before.png"
            screenshot.parent.mkdir(parents=True)
            screenshot.write_bytes(b"proof image")
            (proof_root / f"{run_id}.json").write_text(
                json.dumps(
                    {
                        "ok": True,
                        "schema": "vrcforge.optimizer_apply_rollback_smoke.v1",
                        "startedAt": "2026-06-24T01:01:01+00:00",
                        "finishedAt": "2026-06-24T01:02:01+00:00",
                        "summary": {
                            "status": "passed",
                            "tool": "optimization.meshia.simplify-apply-request",
                            "checkpointId": "ckpt_123",
                            "rollbackDone": True,
                            "failedSteps": [],
                        },
                        "steps": [
                            {"name": "optimizer.verify_checkpoint_delta", "ok": True, "changedFileCount": 1},
                            {
                                "name": "validation.delta_after_rollback",
                                "ok": True,
                                "rollbackProof": {"matchesBeforeSeverityAndGate": True},
                                "profileDiff": {"pc": {"rankBefore": "Poor", "rankAfter": "Medium"}},
                                "parameterBudgetDelta": {"syncedBitsDelta": -12},
                            },
                        ],
                        "visualRegression": {
                            "schema": "vrcforge.visual_regression.v1",
                            "status": "captured",
                            "proofPassed": True,
                            "requiresHumanReview": True,
                            "scoring": {"mode": "not-run"},
                            "screenshots": {
                                "before": {
                                    "stage": "before",
                                    "captured": True,
                                    "artifactOk": True,
                                    "exists": True,
                                    "artifactImagePath": str(screenshot),
                                }
                            },
                        },
                    }
                ),
                encoding="utf-8",
            )
            try:
                with TestClient(dashboard_server.app) as client:
                    index_response = client.get("/api/app/optimization/proofs")
                    detail_response = client.get(f"/api/app/optimization/proofs/{run_id}")
                    screenshot_response = client.get(f"/api/app/optimization/proofs/{run_id}/screenshots/before")

                self.assertEqual(index_response.status_code, 200)
                index_payload = index_response.json()
                self.assertTrue(index_payload["readOnly"])
                self.assertEqual(index_payload["proofs"][0]["runId"], run_id)
                self.assertEqual(index_payload["proofs"][0]["profileDiff"]["pc"]["rankAfter"], "Medium")
                self.assertEqual(index_payload["proofs"][0]["parameterBudgetDelta"]["syncedBitsDelta"], -12)
                image_url = index_payload["proofs"][0]["visualRegression"]["screenshots"]["before"]["imageUrl"]
                self.assertTrue(image_url.startswith("/runtime-artifacts/optimizer-apply-smoke/"))
                self.assertIn("artifact_sig=", image_url)

                self.assertEqual(detail_response.status_code, 200)
                detail_payload = detail_response.json()
                self.assertTrue(detail_payload["readOnly"])
                self.assertEqual(detail_payload["proof"]["checkpointId"], "ckpt_123")
                self.assertEqual(detail_payload["report"]["summary"]["tool"], "optimization.meshia.simplify-apply-request")
                self.assertEqual(screenshot_response.status_code, 200)
                self.assertEqual(screenshot_response.content, b"proof image")
            finally:
                dashboard_server.ARTIFACTS_DIR = original_artifacts_dir

    def test_optimizer_proof_screenshot_rejects_paths_outside_artifacts(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            original_artifacts_dir = dashboard_server.ARTIFACTS_DIR
            temp_path = Path(temp_dir)
            temp_artifacts = temp_path / "artifacts"
            dashboard_server.ARTIFACTS_DIR = temp_artifacts
            proof_root = temp_artifacts / "optimizer-apply-smoke"
            proof_root.mkdir(parents=True)
            outside = temp_path / "outside.png"
            outside.write_bytes(b"outside")
            run_id = "optimizer-apply-smoke-20260624-020202"
            (proof_root / f"{run_id}.json").write_text(
                json.dumps(
                    {
                        "ok": True,
                        "summary": {"status": "passed"},
                        "visualRegression": {
                            "screenshots": {
                                "before": {"artifactImagePath": str(outside), "artifactOk": True},
                            }
                        },
                    }
                ),
                encoding="utf-8",
            )
            try:
                with TestClient(dashboard_server.app) as client:
                    response = client.get(f"/api/app/optimization/proofs/{run_id}/screenshots/before")

                self.assertEqual(response.status_code, 403)
            finally:
                dashboard_server.ARTIFACTS_DIR = original_artifacts_dir

    # ------------------------------------------------------------------
    # /api/clothes/apply-fx (dry_run=True — no Unity needed)
    # ------------------------------------------------------------------
    def test_apply_clothing_fx_dry_run_returns_apply_payload(self) -> None:
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
        self.assertIn("applyPayload", payload)
        self.assertIn("vrc_apply_clothing_fx", payload["applyPayload"])
        self.assertIn("Cloth_Jacket", payload["applyPayload"])

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
    def test_apply_parameter_optimization_dry_run_returns_diff_and_apply_payload(self) -> None:
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
        self.assertIn("vrc_apply_parameter_optimization", payload["applyPayload"])
        self.assertIn("IsWearing", payload["applyPayload"])

    def test_apply_parameter_optimization_no_suggestions_raises_400(self) -> None:
        with TestClient(dashboard_server.app) as client:
            response = client.post(
                "/api/parameters/apply-optimization",
                json={"avatar_path": "MyAvatar", "suggestions": [], "dry_run": True},
            )
        self.assertEqual(response.status_code, 400)

    def test_parameter_rollback_preview_accepts_scanner_parameter_names(self) -> None:
        preview = dashboard_server.build_parameter_rollback_preview(
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

        self.assertIn("vrc_rollback_avatar_parameters", preview)
        self.assertIn("DPS", preview)
        self.assertIn("Int", preview)

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
                    with patch("dashboard_server.create_legacy_write_checkpoint", return_value={"ok": True, "id": "ckpt_test"}):
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
                    with patch("dashboard_server.create_legacy_write_checkpoint", return_value={"ok": True, "id": "ckpt_test"}):
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
            execute_tool_name="vrc_apply_blendshapes",
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
    # Payload preview unit tests (no server)
    # ------------------------------------------------------------------
    def test_build_clothes_fx_apply_preview_contains_key_tokens(self) -> None:
        items = [{"displayName": "Hat", "parameterName": "Cloth_Hat", "sampleObjectPath": "Avatar/Hat", "animationClipName": "FX_Hat_Toggle"}]
        preview = dashboard_server.build_clothes_fx_apply_preview("Avatar", items)
        self.assertIn("vrc_apply_clothing_fx", preview)
        self.assertIn("Cloth_Hat", preview)
        self.assertIn("Avatar/Hat", preview)

    def test_build_parameter_apply_optimization_preview_contains_key_tokens(self) -> None:
        suggestions = [{"name": "IsWearing"}]
        preview = dashboard_server.build_parameter_apply_optimization_preview("Avatar", suggestions)
        self.assertIn("vrc_apply_parameter_optimization", preview)
        self.assertIn("IsWearing", preview)

    def test_build_parameter_rollback_preview_contains_key_tokens(self) -> None:
        rollback_preview = dashboard_server.build_parameter_rollback_preview(
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
        self.assertIn("vrc_rollback_avatar_parameters", rollback_preview)
        self.assertIn("IsWearing", rollback_preview)
        self.assertIn("networkSynced", rollback_preview)

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

    def test_outfit_import_request_creates_supervised_approval(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            project = root / "UnityProject"
            source = root / "LooseOutfit"
            (project / "Assets").mkdir(parents=True)
            (project / "Packages").mkdir()
            (project / "ProjectSettings").mkdir()
            (project / "ProjectSettings" / "ProjectVersion.txt").write_text("m_EditorVersion: 2022.3.22f1", encoding="utf-8")
            source.mkdir()
            (source / "Dress.prefab").write_text("%YAML prefab", encoding="utf-8")
            (source / "body.png").write_bytes(b"secret texture bytes")

            with TestClient(dashboard_server.app) as client:
                response = client.post(
                    "/api/app/outfit-imports/request",
                    json={"packagePath": str(source), "projectPath": str(project)},
                )

            self.assertEqual(response.status_code, 200)
            payload = response.json()
            self.assertTrue(payload["ok"])
            approval = payload["approval"]
            self.assertEqual(approval["targetTool"], "vrcforge_import_outfit_package")
            self.assertEqual(approval["status"], "pending")
            self.assertEqual(approval["preview"]["plan"]["kind"], "loose_prefab_copy")
            self.assertTrue(approval["preview"]["plan"]["requiresCheckpoint"])
            self.assertTrue(approval["preview"]["plan"]["rollbackProofRequired"])

    def test_package_install_diagnostics_is_read_only_and_suggests_supervised_fix(self) -> None:
        with (
            patch("dashboard_server.package_manager_status_sync", return_value={"ok": True, "preferredCli": {"name": "vrc-get"}}),
            patch(
                "dashboard_server.read_agent_compile_errors",
                return_value={"ok": True, "result": {"payload": {"errors": [{"message": "CS0246 missing type"}]}}},
            ),
        ):
            payload = dashboard_server.diagnose_package_install_errors_sync(
                {
                    "projectPath": "E:/unity/milltina",
                    "packageId": "nadena.dev.modular-avatar",
                    "stderrSummary": "network timeout then compilation failed CS0246",
                }
            )

        self.assertTrue(payload["ok"])
        self.assertEqual(payload["schema"], "vrcforge.package_install_diagnostics.v1")
        self.assertTrue(payload["readOnly"])
        codes = {symptom["code"] for symptom in payload["symptoms"]}
        self.assertIn("network", codes)
        self.assertIn("compile", codes)
        self.assertFalse(payload["repairPolicy"]["automaticRepair"])
        self.assertTrue(payload["repairPolicy"]["requiresPreviewApprovalCheckpointValidationRollback"])
        self.assertIn("retry_vpm_install_request", {item["id"] for item in payload["suggestedFixPlans"]})

    def test_package_install_diagnostics_does_not_scan_status_snapshot_as_log(self) -> None:
        with (
            patch(
                "dashboard_server.package_manager_status_sync",
                return_value={
                    "ok": True,
                    "preferredCli": {"name": "vrc-get"},
                    "sourceSummary": {"vpmManifest": True, "manifest": True},
                },
            ),
            patch(
                "dashboard_server.read_agent_compile_errors",
                return_value={"ok": True, "result": {"payload": {"errors": []}}},
            ),
        ):
            payload = dashboard_server.diagnose_package_install_errors_sync(
                {
                    "projectPath": "E:/unity/milltina",
                    "packageId": "com.anatawa12.avatar-optimizer",
                    "stdoutSummary": "",
                    "stderrSummary": "",
                }
            )

        self.assertTrue(payload["ok"])
        self.assertEqual({symptom["code"] for symptom in payload["symptoms"]}, {"unknown"})


class MaterialMagentaValidationTests(unittest.TestCase):
    """Fix #2: a post-import magenta/missing-shader material must block validation."""

    @staticmethod
    def _materials_result(materials: list[dict]) -> dict:
        return {"ok": True, "payload": {"inventory": {"materials": materials}, "materials": materials}}

    def test_magenta_material_emits_blocking_error_finding(self) -> None:
        findings: list[dict] = []
        result = self._materials_result(
            [
                {"material_id": "m_ok", "renderer_path": "Body", "shader_name": "lilToon"},
                {"material_id": "m_missing", "renderer_path": "Dress", "shader_name": ""},
                {"material_id": "m_err", "renderer_path": "Hair", "shader_name": "Hidden/InternalErrorShader"},
            ]
        )

        dashboard_server._material_validation(findings, result)

        magenta = [item for item in findings if item.get("severity") == "Error"]
        self.assertEqual(len(magenta), 1)
        detail = magenta[0].get("detail") or {}
        self.assertEqual(detail.get("magentaCount"), 2)
        self.assertIn("Dress", detail.get("affectedRenderers", []))
        self.assertIn("Hair", detail.get("affectedRenderers", []))
        self.assertTrue(detail.get("remediation"))

    def test_healthy_materials_do_not_emit_error(self) -> None:
        findings: list[dict] = []
        result = self._materials_result(
            [{"material_id": "m_ok", "renderer_path": "Body", "shader_name": "Poiyomi/Toon"}]
        )

        dashboard_server._material_validation(findings, result)

        self.assertFalse([item for item in findings if item.get("severity") == "Error"])
        self.assertTrue([item for item in findings if item.get("severity") == "Info"])


if __name__ == "__main__":
    unittest.main()
