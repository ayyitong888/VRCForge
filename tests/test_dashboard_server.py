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

    def test_agentic_permission_requires_one_time_roslyn_acknowledgement(self) -> None:
        with TestClient(dashboard_server.app) as client:
            blocked = client.post("/api/app/permission", json={"execution_mode": "roslyn_full_auto"})
            self.assertEqual(blocked.status_code, 409)

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

            approval = client.post("/api/app/permission", json={"execution_mode": "approval"})
            self.assertEqual(approval.status_code, 200)
            self.assertEqual(approval.json()["permission"]["executionMode"], "approval")
            self.assertTrue(approval.json()["permission"]["roslynRiskAcknowledged"])

            restored = client.post("/api/app/permission", json={"execution_mode": "roslyn_full_auto"})
            self.assertEqual(restored.status_code, 200)
            self.assertEqual(restored.json()["permission"]["executionMode"], "roslyn_full_auto")
            self.assertTrue(restored.json()["permission"]["roslynRiskAcknowledged"])

    def test_agent_gateway_preview_and_supervised_apply_flow(self) -> None:
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
                        "arguments": {"adjustments": []},
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

            approval_headers = {
                **headers,
                "X-VRCForge-Approval-Token": config.approval_token,
            }
            approved = client.post(f"/api/agent/approvals/{approval['id']}/approve", headers=approval_headers)
            self.assertEqual(approved.status_code, 200)

            with patch("dashboard_server.apply_manual_blendshapes_sync", return_value={"ok": True, "appliedAdjustments": []}) as mock_apply:
                applied = client.post(
                    "/api/agent/tool/vrcforge_apply_approved",
                    headers=headers,
                    json={"agent_name": "codex-test", "params": {"approval_id": approval["id"]}},
                )
            self.assertEqual(applied.status_code, 200)
            self.assertTrue(applied.json()["ok"])
            self.assertEqual(applied.json()["result"]["status"], "applied")
            mock_apply.assert_called_once()

    def test_agent_gateway_manifest_describes_codex_debug_loop_tools(self) -> None:
        config = dashboard_server.AGENT_GATEWAY.ensure_config()
        config.enabled = True
        dashboard_server.AGENT_GATEWAY.save_config(config)
        headers = {"Authorization": f"Bearer {config.token}"}

        with TestClient(dashboard_server.app) as client:
            payload = client.get("/api/agent/manifest", headers=headers).json()

        tool_names = {tool["name"] for tool in payload["tools"]}
        self.assertIn("vrcforge_capture_screenshot", tool_names)
        self.assertIn("vrcforge_vision_audit", tool_names)
        self.assertIn("vrcforge_request_apply", tool_names)
        self.assertIn("vrcforge_apply_approved", tool_names)
        self.assertIn("vrcforge_read_recent_logs", tool_names)
        self.assertIn("vrcforge_apply_blendshapes", {item["name"] for item in payload["writeTargets"]})
        self.assertNotIn("api_key", json.dumps(payload).lower())
        self.assertNotIn("approval_token", json.dumps(payload).lower())

    def test_roslyn_advanced_skill_requires_full_auto_env_and_confirmation(self) -> None:
        with patch.dict(os.environ, {"VRCFORGE_ENABLE_ROSLYN": "1"}):
            config = dashboard_server.AGENT_GATEWAY.ensure_config()
            config.enabled = True
            dashboard_server.AGENT_GATEWAY.save_config(config)
            dashboard_server.AGENT_GATEWAY.update_permission_state("roslyn_full_auto", acknowledge_roslyn_risk=True)
            headers = {"Authorization": f"Bearer {config.token}"}

            with TestClient(dashboard_server.app) as client:
                payload = client.get("/api/agent/manifest", headers=headers).json()
                tool_names = {tool["name"] for tool in payload["tools"]}
                self.assertIn("vrcforge_request_roslyn_advanced", tool_names)
                self.assertIn("vrcforge_roslyn_advanced", {item["name"] for item in payload["writeTargets"]})

                missing_confirm = client.post(
                    "/api/agent/tool/vrcforge_request_roslyn_advanced",
                    headers=headers,
                    json={"agent_name": "codex-test", "params": {"code": "1 + 1"}},
                )
                self.assertEqual(missing_confirm.status_code, 200)
                self.assertFalse(missing_confirm.json()["ok"])
                self.assertIn("confirmAdvancedPowerMode=true", missing_confirm.json()["error"])

                request = client.post(
                    "/api/agent/tool/vrcforge_request_roslyn_advanced",
                    headers=headers,
                    json={
                        "agent_name": "codex-test",
                        "params": {
                            "code": "1 + 1",
                            "confirmAdvancedPowerMode": True,
                        },
                    },
                )
                self.assertEqual(request.status_code, 200)
                self.assertTrue(request.json()["ok"])
                approval = request.json()["result"]["approval"]
                self.assertEqual(approval["targetTool"], "vrcforge_roslyn_advanced")

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
        self.assertIn("vrcforge_capture_screenshot", tool_names)
        self.assertIn("vrcforge_vision_audit", tool_names)
        self.assertIn("vrcforge_request_apply", tool_names)
        self.assertIn("vrcforge_apply_approved", tool_names)

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

    def test_roslyn_advanced_power_mode_requires_explicit_opt_in(self) -> None:
        editor_dir = Path(__file__).resolve().parents[1] / "Assets" / "VRCForge" / "Editor"
        source = (editor_dir / "RoslynExecutor.cs").read_text(encoding="utf-8")
        bootstrap = (editor_dir / "RoslynSupportBootstrap.cs").read_text(encoding="utf-8")

        self.assertNotIn("#if VRCFORGE_ENABLE_ROSLYN", source)
        self.assertNotIn("#if VRCFORGE_ENABLE_ROSLYN", bootstrap)
        self.assertIn('name: "vrc_execute_roslyn"', source)
        self.assertIn("Advanced Power Mode", source)
        self.assertIn("confirmAdvancedPowerMode", source)
        self.assertIn("EditorUtility.DisplayDialog", source)
        self.assertIn('"VRCForge Advanced Power Mode"', source)
        self.assertIn("AssemblyResolve", source)
        self.assertIn("Assets/Plugins/Roslyn", bootstrap)

        installer = (Path(__file__).resolve().parents[1] / "tools" / "install-roslyn-support.ps1").read_text(
            encoding="utf-8"
        )
        self.assertIn("VRCFORGE_ENABLE_ROSLYN", installer)
        self.assertIn("Assets\\csc.rsp", installer)
        self.assertIn("ProjectSettings\\ProjectSettings.asset", installer)
        self.assertIn("scriptingDefineSymbols", installer)
        self.assertIn("System.Memory.dll", installer)
        self.assertIn("System.Runtime.CompilerServices.Unsafe.dll", installer)

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
        self.assertIn('Remove-Item -LiteralPath (Join-Path $payloadRoot "VRCForge.pdb")', build_script)
        self.assertIn("Resolve-DotNetExe", build_script)
        self.assertIn("Resolve-MakeNsisExe", build_script)
        self.assertIn("VRCForge_Web_Installer_x64.exe", publish_script)
        self.assertIn("VRCForge_Offline_Installer_x64.exe", publish_script)
        self.assertIn("VRCForge_Windows_x64_$Version.zip", publish_script)
        self.assertIn("win-x64", launcher_project)
        self.assertIn("<Platforms>x64</Platforms>", launcher_project)
        self.assertIn("<DebugType>none</DebugType>", launcher_project)
        self.assertIn("<DebugSymbols>false</DebugSymbols>", launcher_project)
        self.assertIn("VRCForge_Offline_Installer_x64.exe", offline_nsis)
        self.assertIn("VRCForge_Web_Installer_x64.exe", web_nsis)
        self.assertIn("$PROGRAMFILES64\\VRCForge", offline_nsis)
        self.assertIn("$LOCALAPPDATA\\VRCForge\\config", web_nsis)

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

    def test_unity_install_script_uses_project_backups_and_local_mcp(self) -> None:
        script = (Path(__file__).resolve().parents[1] / "tools" / "install-unity-project.ps1").read_text(encoding="utf-8-sig")

        self.assertIn(".vrcforge", script)
        self.assertIn("backups", script)
        self.assertIn("VRCAutoRig", script)
        self.assertIn("file:Packages/com.coplaydev.unity-mcp", script)
        self.assertIn('New-BackupPath $backupRoot "manifest"', script)
        self.assertIn("Restored backup", script)
        self.assertNotIn("Library\\VRCForge\\LegacyAssets", script)
        self.assertNotIn("https://github.com/CoplayDev/unity-mcp", script)

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
                ):
                    projects = dashboard_server.discover_projects([root], include_external=True)
            finally:
                dashboard_server.CURRENT_UNITY_STATUS = original_status

            milltina = [project for project in projects if project["name"] == "milltina"]
            self.assertEqual(len(milltina), 1)
            self.assertEqual(milltina[0]["path"], dashboard_server.normalize_path_string(str(project_dir)))
            self.assertTrue(milltina[0]["activeMcp"])
            self.assertEqual(milltina[0]["cliInstanceId"], "hash-456")

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


if __name__ == "__main__":
    unittest.main()
