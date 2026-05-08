import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from vrchat_blendshape_agent import (
    BlendshapeAdjustment,
    BlendshapePlan,
    McpResult,
    Settings,
    build_custom_tool_cli_args,
    build_unity_mcp_command,
    create_blendshape_plan,
    extract_unity_mcp_stdout_error,
    filter_plan_by_instruction_relevance,
    filter_planning_payload_to_face_blendshapes,
    build_planning_payload,
    humanize_unity_mcp_error,
    load_settings,
    load_export_payload,
    mock_execute_csharp,
    read_plan_json,
    read_export_json,
    resolve_export_result_path,
    resolve_unity_mcp_wrapper_command,
    resolve_avatar_selection,
    render_preview,
    run_unity_mcp_process,
    validate_plan,
)


def make_export_payload() -> dict:
    return {
        "generatedAtUtc": "2026-04-30T00:00:00Z",
        "unityProject": "SampleProject",
        "scenes": ["AvatarScene"],
        "summary": {
            "avatarCount": 2,
            "rendererCount": 2,
            "blendshapeCount": 4,
        },
        "avatars": [
            {
                "avatarName": "HeroAvatar",
                "avatarPath": "Scene/HeroAvatar",
                "sceneName": "AvatarScene",
                "scenePath": "Assets/Scenes/AvatarScene.unity",
                "isVrChatAvatar": True,
                "renderers": [
                    {
                        "rendererName": "Face",
                        "rendererPath": "Scene/HeroAvatar/Body/Face",
                        "relativeRendererPath": "Body/Face",
                        "meshName": "HeroFace",
                        "blendshapeCount": 2,
                        "blendshapes": [
                            {"index": 0, "name": "EyeWide", "currentWeight": 0, "normalizedWeight": 0},
                            {"index": 1, "name": "Smile", "currentWeight": 0, "normalizedWeight": 0},
                        ],
                    }
                ],
            },
            {
                "avatarName": "VillainAvatar",
                "avatarPath": "Scene/VillainAvatar",
                "sceneName": "AvatarScene",
                "scenePath": "Assets/Scenes/AvatarScene.unity",
                "isVrChatAvatar": True,
                "renderers": [
                    {
                        "rendererName": "Face",
                        "rendererPath": "Scene/VillainAvatar/Body/Face",
                        "relativeRendererPath": "Body/Face",
                        "meshName": "VillainFace",
                        "blendshapeCount": 2,
                        "blendshapes": [
                            {"index": 0, "name": "EyeNarrow", "currentWeight": 0, "normalizedWeight": 0},
                            {"index": 1, "name": "Sneer", "currentWeight": 0, "normalizedWeight": 0},
                        ],
                    }
                ],
            },
        ],
    }


class AvatarSelectionTests(unittest.TestCase):
    def test_requires_explicit_avatar_when_multiple_exist(self) -> None:
        with self.assertRaises(RuntimeError):
            resolve_avatar_selection(make_export_payload(), None)

    def test_resolves_avatar_by_exact_path(self) -> None:
        selected = resolve_avatar_selection(make_export_payload(), "Scene/HeroAvatar")
        self.assertEqual(selected.avatar_name, "HeroAvatar")
        self.assertEqual(selected.avatar_path, "Scene/HeroAvatar")

    def test_resolves_avatar_by_partial_name(self) -> None:
        selected = resolve_avatar_selection(make_export_payload(), "villain")
        self.assertEqual(selected.avatar_name, "VillainAvatar")
        self.assertEqual(selected.avatar_path, "Scene/VillainAvatar")


class PlanningValidationTests(unittest.TestCase):
    def setUp(self) -> None:
        export_payload = make_export_payload()
        self.selected_avatar = resolve_avatar_selection(export_payload, "Scene/HeroAvatar")
        self.planning_payload = build_planning_payload(export_payload, self.selected_avatar)

    def test_build_planning_payload_scopes_to_one_avatar(self) -> None:
        self.assertEqual(self.planning_payload["summary"]["avatarCount"], 1)
        self.assertEqual(len(self.planning_payload["avatars"]), 1)
        self.assertEqual(self.planning_payload["avatars"][0]["avatarPath"], "Scene/HeroAvatar")

    def test_create_blendshape_plan_passes_reference_image_to_llm_request(self) -> None:
        settings = Settings(
            llm_provider="openai",
            llm_api_key="test-key",
            llm_base_url="https://api.openai.com/v1",
            llm_model="gpt-4.1-mini",
            llm_api_key_env="OPENAI_API_KEY",
            gemini_thinking_level="",
            unity_mcp_command=["unity-mcp"],
            unity_mcp_host="127.0.0.1",
            unity_mcp_port=8080,
            unity_mcp_instance="",
            unity_mcp_retries=1,
            unity_mcp_retry_backoff_seconds=0.0,
            unity_mcp_timeout_seconds=30,
            export_tool_name="vrc_export_blendshapes",
            execute_tool_name="vrc_execute_roslyn",
            export_path=Path("Assets/VRCAutoRig/blendshapes_export.json"),
            min_confidence=0.65,
        )
        raw_plan = {
            "summary": "Smile",
            "warnings": [],
            "adjustments": [
                {
                    "avatar_path": "Scene/HeroAvatar",
                    "renderer_path": "Scene/HeroAvatar/Body/Face",
                    "blendshape_name": "Smile",
                    "target_weight": 40,
                    "reason": "Match the requested smile.",
                    "confidence": 0.95,
                }
            ],
        }

        with patch("vrchat_blendshape_agent.request_llm_plan", return_value=json.dumps(raw_plan)) as request_mock:
            plan = create_blendshape_plan(
                settings,
                self.planning_payload,
                "make the smile softer",
                reference_image_path=Path("reference.png"),
            )

        self.assertEqual(plan.adjustments[0].blendshape_name, "Smile")
        self.assertEqual(request_mock.call_args.kwargs["reference_image_path"], Path("reference.png"))
        self.assertIn("reference image is attached", request_mock.call_args.args[1].lower())

    def test_rejects_invalid_targets(self) -> None:
        plan = BlendshapePlan(
            summary="Test",
            adjustments=[
                BlendshapeAdjustment(
                    avatar_path="Scene/HeroAvatar",
                    renderer_path="Scene/HeroAvatar/Body/Face",
                    blendshape_name="DoesNotExist",
                    target_weight=50,
                    reason="Invalid target",
                    confidence=0.9,
                )
            ],
        )

        with self.assertRaises(RuntimeError):
            validate_plan(plan, self.planning_payload, self.selected_avatar, min_confidence=0.65, allow_low_confidence=False)

    def test_rejects_low_confidence_without_override(self) -> None:
        plan = BlendshapePlan(
            summary="Test",
            adjustments=[
                BlendshapeAdjustment(
                    avatar_path="Scene/HeroAvatar",
                    renderer_path="Scene/HeroAvatar/Body/Face",
                    blendshape_name="Smile",
                    target_weight=40,
                    reason="Low confidence",
                    confidence=0.4,
                )
            ],
        )

        with self.assertRaises(RuntimeError):
            validate_plan(plan, self.planning_payload, self.selected_avatar, min_confidence=0.65, allow_low_confidence=False)

    def test_allows_low_confidence_with_override(self) -> None:
        plan = BlendshapePlan(
            summary="Test",
            adjustments=[
                BlendshapeAdjustment(
                    avatar_path="Scene/HeroAvatar",
                    renderer_path="Scene/HeroAvatar/Body/Face",
                    blendshape_name="Smile",
                    target_weight=40,
                    reason="Low confidence",
                    confidence=0.4,
                )
            ],
        )

        validated = validate_plan(
            plan,
            self.planning_payload,
            self.selected_avatar,
            min_confidence=0.65,
            allow_low_confidence=True,
        )
        self.assertEqual(len(validated.adjustments), 1)
        self.assertIn("Low-confidence adjustments were allowed by CLI override.", validated.warnings)

    def test_deduplicates_same_target(self) -> None:
        plan = BlendshapePlan(
            summary="Test",
            adjustments=[
                BlendshapeAdjustment(
                    avatar_path="Scene/HeroAvatar",
                    renderer_path="Scene/HeroAvatar/Body/Face",
                    blendshape_name="Smile",
                    target_weight=20,
                    reason="First",
                    confidence=0.9,
                ),
                BlendshapeAdjustment(
                    avatar_path="Scene/HeroAvatar",
                    renderer_path="Scene/HeroAvatar/Body/Face",
                    blendshape_name="Smile",
                    target_weight=60,
                    reason="Second",
                    confidence=0.9,
                ),
            ],
        )

        validated = validate_plan(
            plan,
            self.planning_payload,
            self.selected_avatar,
            min_confidence=0.65,
            allow_low_confidence=False,
        )
        self.assertEqual(len(validated.adjustments), 1)
        self.assertEqual(validated.adjustments[0].target_weight, 60)
        self.assertTrue(any("duplicate edits" in warning for warning in validated.warnings))

    def test_instruction_relevance_filter_drops_unrequested_body_hair_and_teeth(self) -> None:
        plan = BlendshapePlan(
            summary="Wrong drift",
            adjustments=[
                BlendshapeAdjustment(
                    avatar_path="Scene/HeroAvatar",
                    renderer_path="Scene/HeroAvatar/Body",
                    blendshape_name="Breast_big",
                    target_weight=100,
                    reason="Make breasts larger.",
                    confidence=1.0,
                ),
                BlendshapeAdjustment(
                    avatar_path="Scene/HeroAvatar",
                    renderer_path="Scene/HeroAvatar/Hair",
                    blendshape_name="Hair_front_ahoge 1_x",
                    target_weight=100,
                    reason="Hide ahoge.",
                    confidence=0.95,
                ),
                BlendshapeAdjustment(
                    avatar_path="Scene/HeroAvatar",
                    renderer_path="Scene/HeroAvatar/Body/Face",
                    blendshape_name="mouth_giza (tooth)",
                    target_weight=100,
                    reason="Show jagged teeth.",
                    confidence=1.0,
                ),
                BlendshapeAdjustment(
                    avatar_path="Scene/HeroAvatar",
                    renderer_path="Scene/HeroAvatar/Body/Face",
                    blendshape_name="Smile",
                    target_weight=45,
                    reason="Raise the mouth corners.",
                    confidence=0.9,
                ),
            ],
        )

        filtered = filter_plan_by_instruction_relevance(plan, "把嘴角明显上扬一些，眼睛稍微眯一点")

        self.assertEqual([item.blendshape_name for item in filtered.adjustments], ["Smile"])
        self.assertTrue(any("Dropped unrelated" in warning for warning in filtered.warnings))

    def test_face_planning_payload_filter_is_generic(self) -> None:
        payload = {
            "summary": {"avatarCount": 1, "rendererCount": 3, "blendshapeCount": 6},
            "avatars": [
                {
                    "avatarName": "GenericAvatar",
                    "avatarPath": "GenericAvatar",
                    "sceneName": "Scene",
                    "renderers": [
                        {
                            "rendererName": "Body",
                            "rendererPath": "GenericAvatar/Body",
                            "meshName": "Body",
                            "blendshapes": [
                                {"name": "eye_narrow", "currentWeight": 0},
                                {"name": "mouth_smile", "currentWeight": 0},
                                {"name": "Breast_big", "currentWeight": 0},
                                {"name": "Hair_front_ahoge_x", "currentWeight": 0},
                                {"name": "Earring_Left_X", "currentWeight": 0},
                                {"name": "Shrink_Shoulder", "currentWeight": 0},
                                {"name": "vrc.v_aa", "currentWeight": 0},
                                {"name": "EyeBlinkLeft", "currentWeight": 0},
                                {"name": "MouthSmileLeft", "currentWeight": 0},
                            ],
                        },
                        {
                            "rendererName": "Hair",
                            "rendererPath": "GenericAvatar/Hair",
                            "meshName": "Hair",
                            "blendshapes": [{"name": "eye_decoy", "currentWeight": 0}],
                        },
                        {
                            "rendererName": "FaceRenderer",
                            "rendererPath": "GenericAvatar/FaceRenderer",
                            "meshName": "FaceMesh",
                            "blendshapes": [{"name": "cheek_soft", "currentWeight": 0}],
                        },
                    ],
                }
            ],
        }

        filtered = filter_planning_payload_to_face_blendshapes(payload)
        names = [
            blendshape["name"]
            for avatar in filtered["avatars"]
            for renderer in avatar["renderers"]
            for blendshape in renderer["blendshapes"]
        ]

        self.assertEqual(names, ["eye_narrow", "mouth_smile", "cheek_soft"])
        self.assertEqual(filtered["summary"]["blendshapeCount"], 3)


class MvpFlowTests(unittest.TestCase):
    def test_humanize_unity_mcp_error_adds_startup_hint(self) -> None:
        detail = "Cannot connect to Unity MCP server at 127.0.0.1:8080"
        message = humanize_unity_mcp_error(detail)
        self.assertIn("Unity MCP server is not ready yet.", message)
        self.assertIn(detail, message)

    def test_build_unity_mcp_command_includes_host_port_and_instance(self) -> None:
        settings = Settings(
            llm_provider="gemini",
            llm_api_key="",
            llm_base_url="https://generativelanguage.googleapis.com/v1beta/openai",
            llm_model="gemini-2.5-flash",
            llm_api_key_env="GEMINI_API_KEY",
            gemini_thinking_level="",
            unity_mcp_command=["powershell", "-File", "tools/unity-mcp-cli.ps1"],
            unity_mcp_host="127.0.0.1",
            unity_mcp_port=8080,
            unity_mcp_instance="Karin FT Rework@abc123",
            unity_mcp_retries=3,
            unity_mcp_retry_backoff_seconds=2.0,
            unity_mcp_timeout_seconds=30,
            export_tool_name="vrc_export_blendshapes",
            execute_tool_name="vrc_execute_roslyn",
            export_path=Path("Assets/VRCAutoRig/blendshapes_export.json"),
            min_confidence=0.65,
        )

        command = build_unity_mcp_command(settings, ["status"])
        self.assertEqual(
            command,
            [
                "powershell",
                "-File",
                "tools/unity-mcp-cli.ps1",
                "--host",
                "127.0.0.1",
                "--port",
                "8080",
                "--instance",
                "Karin FT Rework@abc123",
                "status",
            ],
        )

    def test_build_custom_tool_cli_args_uses_base64_for_powershell_wrapper(self) -> None:
        settings = Settings(
            llm_provider="gemini",
            llm_api_key="",
            llm_base_url="https://generativelanguage.googleapis.com/v1beta/openai",
            llm_model="gemini-2.5-flash",
            llm_api_key_env="GEMINI_API_KEY",
            gemini_thinking_level="",
            unity_mcp_command=["powershell", "-File", "tools/unity-mcp-cli.ps1"],
            unity_mcp_host="127.0.0.1",
            unity_mcp_port=8080,
            unity_mcp_instance="Karin FT Rework@abc123",
            unity_mcp_retries=3,
            unity_mcp_retry_backoff_seconds=2.0,
            unity_mcp_timeout_seconds=30,
            export_tool_name="vrc_export_blendshapes",
            execute_tool_name="vrc_execute_roslyn",
            export_path=Path("Assets/VRCAutoRig/blendshapes_export.json"),
            min_confidence=0.65,
        )

        args = build_custom_tool_cli_args(settings, "vrc_execute_roslyn", {"code": 'return "ok";'})

        self.assertEqual(args[:3], ["editor", "custom-tool", "vrc_execute_roslyn"])
        self.assertEqual(args[3], "--params-b64")
        self.assertNotIn("--params", args)

    def test_resolve_unity_mcp_wrapper_command_decodes_base64_params_for_direct_cli(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            fake_cli = Path(temp_dir) / "unity-mcp.exe"
            fake_cli.write_text("", encoding="utf-8")
            command = [
                "powershell",
                "-File",
                "tools/unity-mcp-cli.ps1",
                "--host",
                "127.0.0.1",
                "editor",
                "custom-tool",
                "vrc_export_blendshapes",
                "--params-b64",
                "eyJvdXRwdXRQYXRoIjogIkFzc2V0cy9leHBvcnQuanNvbiJ9",
            ]

            with patch("vrchat_blendshape_agent.find_unity_mcp_executable_prefix", return_value=[str(fake_cli)]):
                resolved = resolve_unity_mcp_wrapper_command(command)

        self.assertEqual(resolved[0], str(fake_cli))
        self.assertIn("--params", resolved)
        self.assertIn('{"outputPath": "Assets/export.json"}', resolved)
        self.assertNotIn("--params-b64", resolved)

    def test_extract_unity_mcp_stdout_error_reads_cli_error_prefix(self) -> None:
        message = extract_unity_mcp_stdout_error(
            "❌ Error: Roslyn runtime is disabled. Install the Roslyn DLLs.\n"
        )

        self.assertEqual(message, "Roslyn runtime is disabled. Install the Roslyn DLLs.")

    def test_resolve_export_result_path_uses_absolute_stdout_path(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            export_path = Path(temp_dir) / "unity-export.json"
            export_path.write_text("{}", encoding="utf-8")
            settings = Settings(
                llm_provider="gemini",
                llm_api_key="",
                llm_base_url="https://generativelanguage.googleapis.com/v1beta/openai",
                llm_model="gemini-2.5-flash",
                llm_api_key_env="GEMINI_API_KEY",
                gemini_thinking_level="",
                unity_mcp_command=["unity-mcp"],
                unity_mcp_host="127.0.0.1",
                unity_mcp_port=8080,
                unity_mcp_instance="",
                unity_mcp_retries=3,
                unity_mcp_retry_backoff_seconds=2.0,
                unity_mcp_timeout_seconds=30,
                export_tool_name="vrc_export_blendshapes",
                execute_tool_name="vrc_execute_roslyn",
                export_path=Path("missing-local-export.json"),
                min_confidence=0.65,
            )
            result = McpResult(
                exit_code=0,
                stdout=f"absoluteOutputPath: {export_path.as_posix()}\n✅ Executed custom tool",
                stderr="",
                payload=None,
            )

            self.assertEqual(resolve_export_result_path(settings, result), export_path)

    def test_run_unity_mcp_process_forces_utf8_environment(self) -> None:
        settings = Settings(
            llm_provider="gemini",
            llm_api_key="",
            llm_base_url="https://generativelanguage.googleapis.com/v1beta/openai",
            llm_model="gemini-2.5-flash",
            llm_api_key_env="GEMINI_API_KEY",
            gemini_thinking_level="",
            unity_mcp_command=["unity-mcp"],
            unity_mcp_host="127.0.0.1",
            unity_mcp_port=8080,
            unity_mcp_instance="",
            unity_mcp_retries=3,
            unity_mcp_retry_backoff_seconds=2.0,
            unity_mcp_timeout_seconds=30,
            export_tool_name="vrc_export_blendshapes",
            execute_tool_name="vrc_execute_roslyn",
            export_path=Path("Assets/VRCAutoRig/blendshapes_export.json"),
            min_confidence=0.65,
        )

        with patch("vrchat_blendshape_agent.subprocess.run") as run_mock:
            run_unity_mcp_process(settings, ["status"])

        self.assertEqual(run_mock.call_args.kwargs["env"]["PYTHONIOENCODING"], "utf-8")
        self.assertEqual(run_mock.call_args.kwargs["env"]["PYTHONUTF8"], "1")

    def test_load_settings_allows_model_override(self) -> None:
        settings_payload = {
            "gemini": {
                "api_key_env": "TEST_GEMINI_API_KEY",
                "model": "gemini-3.1-pro-preview",
                "thinking_level": "low",
            },
            "unity_mcp": {
                "host": "127.0.0.1",
                "port": 8080,
                "instance": "Karin FT Rework@abc123",
            },
        }

        with tempfile.TemporaryDirectory() as temp_dir:
            settings_path = Path(temp_dir) / "settings.json"
            settings_path.write_text(json.dumps(settings_payload, ensure_ascii=False), encoding="utf-8")

            settings = load_settings(settings_path, gemini_model_override="gemini-2.5-flash")
            self.assertEqual(settings.llm_provider, "gemini")
            self.assertEqual(settings.llm_model, "gemini-2.5-flash")
            self.assertEqual(settings.llm_base_url, "")
            self.assertEqual(settings.llm_api_key_env, "TEST_GEMINI_API_KEY")
            self.assertEqual(settings.unity_mcp_host, "127.0.0.1")
            self.assertEqual(settings.unity_mcp_port, 8080)
            self.assertEqual(settings.unity_mcp_instance, "Karin FT Rework@abc123")

    def test_load_settings_supports_ollama_and_vertex_ai_providers(self) -> None:
        settings_payload = {
            "llm": {
                "provider": "ollama",
                "model": "qwen3-vl:8b",
            }
        }

        with tempfile.TemporaryDirectory() as temp_dir:
            settings_path = Path(temp_dir) / "settings.json"
            settings_path.write_text(json.dumps(settings_payload, ensure_ascii=False), encoding="utf-8")

            settings = load_settings(settings_path)
            self.assertEqual(settings.llm_provider, "ollama")
            self.assertEqual(settings.llm_base_url, "http://127.0.0.1:11434/v1")
            self.assertEqual(settings.llm_model, "qwen3-vl:8b")

            vertex_settings = load_settings(
                settings_path,
                llm_override={
                    "provider": "google-vertex-ai",
                    "model": "gemini-2.5-flash",
                    "base_url": "project=my-project;location=asia-northeast1",
                },
            )
            self.assertEqual(vertex_settings.llm_provider, "vertexai")
            self.assertEqual(vertex_settings.llm_base_url, "project=my-project;location=asia-northeast1")

    def test_load_settings_accepts_llm_override(self) -> None:
        settings_payload = {
            "llm": {
                "provider": "openai",
                "api_key_env": "OPENAI_API_KEY",
                "base_url": "https://api.openai.com/v1",
                "model": "gpt-4.1-mini",
            }
        }

        with tempfile.TemporaryDirectory() as temp_dir:
            settings_path = Path(temp_dir) / "settings.json"
            settings_path.write_text(json.dumps(settings_payload, ensure_ascii=False), encoding="utf-8")

            settings = load_settings(
                settings_path,
                llm_override={
                    "provider": "anthropic",
                    "api_key": "test-key",
                    "model": "claude-opus-4-6",
                    "base_url": "https://ignored.example.com",
                },
            )
            self.assertEqual(settings.llm_provider, "anthropic")
            self.assertEqual(settings.llm_model, "claude-opus-4-6")
            self.assertEqual(settings.llm_base_url, "")
            self.assertEqual(settings.llm_api_key, "test-key")

    def test_reads_export_json_from_local_file(self) -> None:
        payload = make_export_payload()

        with tempfile.TemporaryDirectory() as temp_dir:
            export_path = Path(temp_dir) / "export.json"
            export_path.write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")

            loaded = read_export_json(export_path)
            self.assertEqual(loaded["unityProject"], "SampleProject")
            self.assertEqual(len(loaded["avatars"]), 2)

    def test_load_export_payload_uses_local_json_in_mvp_mode(self) -> None:
        payload = make_export_payload()

        with tempfile.TemporaryDirectory() as temp_dir:
            export_path = Path(temp_dir) / "export.json"
            export_path.write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")

            class DummySettings:
                export_path = Path("unused.json")

            loaded_payload, export_source, using_mock_execute = load_export_payload(
                settings=DummySettings(),
                export_json_path=export_path,
                skip_export=False,
                mvp_mode=True,
                mock_execute=False,
            )

            self.assertEqual(export_source, str(export_path))
            self.assertTrue(using_mock_execute)
            self.assertEqual(loaded_payload["unityProject"], "SampleProject")

    def test_mock_execute_returns_success_payload(self) -> None:
        selected_avatar = resolve_avatar_selection(make_export_payload(), "Scene/HeroAvatar")
        result = mock_execute_csharp(
            code='RoslynExecutor.Log("Hello");',
            selected_avatar=selected_avatar,
            export_source="examples/mvp_blendshapes_export.json",
        )

        self.assertEqual(result.exit_code, 0)
        self.assertEqual(result.payload["mode"], "mock")
        self.assertEqual(result.payload["avatarPath"], "Scene/HeroAvatar")

    def test_reads_plan_json_from_local_file(self) -> None:
        plan = {
            "summary": "Test",
            "warnings": [],
            "adjustments": [
                {
                    "avatar_path": "Scene/HeroAvatar",
                    "renderer_path": "Scene/HeroAvatar/Body/Face",
                    "blendshape_name": "Smile",
                    "target_weight": 50,
                    "reason": "Test reason",
                    "confidence": 0.95,
                }
            ],
        }

        with tempfile.TemporaryDirectory() as temp_dir:
            plan_path = Path(temp_dir) / "plan.json"
            plan_path.write_text(json.dumps(plan, ensure_ascii=False), encoding="utf-8")

            loaded = read_plan_json(plan_path)
            self.assertEqual(loaded.summary, "Test")
            self.assertEqual(len(loaded.adjustments), 1)

    def test_render_preview_includes_execution_mode_and_source(self) -> None:
        selected_avatar = resolve_avatar_selection(make_export_payload(), "Scene/HeroAvatar")
        plan = BlendshapePlan(
            summary="Preview test",
            adjustments=[
                BlendshapeAdjustment(
                    avatar_path="Scene/HeroAvatar",
                    renderer_path="Scene/HeroAvatar/Body/Face",
                    blendshape_name="Smile",
                    target_weight=50,
                    reason="Preview",
                    confidence=0.9,
                )
            ],
        )

        preview = render_preview(
            selected_avatar=selected_avatar,
            plan=plan,
            export_source="examples/mvp_blendshapes_export.json",
            using_mock_execute=True,
        )

        self.assertIn("Execution mode: mock", preview)
        self.assertIn("Export source: examples/mvp_blendshapes_export.json", preview)


if __name__ == "__main__":
    unittest.main()
