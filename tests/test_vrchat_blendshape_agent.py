import json
import sys
import tempfile
import types
import unittest
from pathlib import Path
from types import SimpleNamespace
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
    extract_llm_reasoning_trace,
    extract_llm_token_usage,
    humanize_unity_mcp_error,
    load_settings,
    load_export_payload,
    mock_execute_payload,
    read_plan_json,
    read_export_json,
    resolve_export_result_path,
    resolve_unity_mcp_wrapper_command,
    resolve_avatar_selection,
    render_preview,
    request_anthropic_plan_with_metadata,
    request_gemini_plan_with_metadata,
    request_openai_compatible_plan_with_metadata,
    request_vertex_ai_plan_with_metadata,
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


def make_llm_settings(provider: str, model: str = "test-model", base_url: str = "https://provider.example/v1") -> Settings:
    return Settings(
        llm_provider=provider,
        llm_api_key="test-key",
        llm_base_url=base_url,
        llm_model=model,
        llm_api_key_env="TEST_API_KEY",
        gemini_thinking_level="",
        unity_mcp_command=["unity-mcp"],
        unity_mcp_host="127.0.0.1",
        unity_mcp_port=8080,
        unity_mcp_instance="",
        unity_mcp_retries=3,
        unity_mcp_retry_backoff_seconds=2.0,
        unity_mcp_timeout_seconds=30,
        export_tool_name="vrc_export_blendshapes",
        execute_tool_name="vrc_apply_blendshapes",
        export_path=Path("Assets/VRCForge/blendshapes_export.json"),
        min_confidence=0.65,
    )


class FakeGoogleModels:
    def __init__(self) -> None:
        self.generate_content_calls: list[dict] = []
        self.generate_content_stream_calls: list[dict] = []

    def generate_content_stream(self, **kwargs):
        self.generate_content_stream_calls.append(kwargs)
        yield SimpleNamespace(text='{"reply":"hel')
        yield SimpleNamespace(text='lo"}')

    def generate_content(self, **kwargs):
        self.generate_content_calls.append(kwargs)
        return SimpleNamespace(text='{"reply":"fallback"}')


class FakeGoogleClient:
    instances: list["FakeGoogleClient"] = []

    def __init__(self, **kwargs) -> None:
        self.kwargs = kwargs
        self.models = FakeGoogleModels()
        FakeGoogleClient.instances.append(self)


class ProviderStreamingTests(unittest.TestCase):
    def setUp(self) -> None:
        FakeGoogleClient.instances = []

    def install_fake_google(self) -> None:
        google_module = types.ModuleType("google")
        genai_module = types.ModuleType("google.genai")
        genai_module.Client = FakeGoogleClient
        google_module.genai = genai_module
        self.addCleanup(lambda: sys.modules.pop("google", None))
        self.addCleanup(lambda: sys.modules.pop("google.genai", None))
        sys.modules["google"] = google_module
        sys.modules["google.genai"] = genai_module

    def test_gemini_streams_text_chunks(self) -> None:
        self.install_fake_google()
        settings = make_llm_settings("gemini", model="gemini-test", base_url="")
        chunks: list[str] = []

        response = request_gemini_plan_with_metadata(settings, "prompt", stream_callback=chunks.append)

        self.assertEqual(response.text, '{"reply":"hello"}')
        self.assertEqual(chunks, ['{"reply":"hel', 'lo"}'])
        self.assertEqual(FakeGoogleClient.instances[0].kwargs, {"api_key": "test-key"})
        self.assertEqual(FakeGoogleClient.instances[0].models.generate_content_calls, [])
        self.assertEqual(FakeGoogleClient.instances[0].models.generate_content_stream_calls[0]["model"], "gemini-test")

    def test_vertex_streams_text_chunks(self) -> None:
        self.install_fake_google()
        settings = make_llm_settings("vertexai", model="gemini-vertex", base_url="project=demo;location=asia-northeast1")
        chunks: list[str] = []

        response = request_vertex_ai_plan_with_metadata(settings, "prompt", stream_callback=chunks.append)

        self.assertEqual(response.text, '{"reply":"hello"}')
        self.assertEqual(chunks, ['{"reply":"hel', 'lo"}'])
        self.assertEqual(FakeGoogleClient.instances[0].kwargs, {"vertexai": True, "project": "demo", "location": "asia-northeast1"})
        self.assertEqual(FakeGoogleClient.instances[0].models.generate_content_calls, [])

    def test_anthropic_streams_text_chunks(self) -> None:
        class FakeMessageStream:
            text_stream = iter(['{"reply":"hel', 'lo"}'])

            def __enter__(self):
                return self

            def __exit__(self, exc_type, exc, tb) -> None:
                return None

            def get_final_message(self):
                return SimpleNamespace(content=[SimpleNamespace(text='{"reply":"hello"}')])

        class FakeMessages:
            def __init__(self) -> None:
                self.create_calls: list[dict] = []
                self.stream_calls: list[dict] = []

            def stream(self, **kwargs):
                self.stream_calls.append(kwargs)
                return FakeMessageStream()

            def create(self, **kwargs):
                self.create_calls.append(kwargs)
                return SimpleNamespace(content=[SimpleNamespace(text='{"reply":"fallback"}')])

        class FakeAnthropic:
            instances: list["FakeAnthropic"] = []

            def __init__(self, **kwargs) -> None:
                self.kwargs = kwargs
                self.messages = FakeMessages()
                FakeAnthropic.instances.append(self)

        anthropic_module = types.ModuleType("anthropic")
        anthropic_module.Anthropic = FakeAnthropic
        self.addCleanup(lambda: sys.modules.pop("anthropic", None))
        sys.modules["anthropic"] = anthropic_module
        settings = make_llm_settings("anthropic", model="claude-test", base_url="")
        chunks: list[str] = []

        response = request_anthropic_plan_with_metadata(settings, "prompt", stream_callback=chunks.append)

        self.assertEqual(response.text, '{"reply":"hello"}')
        self.assertEqual(chunks, ['{"reply":"hel', 'lo"}'])
        self.assertEqual(FakeAnthropic.instances[0].kwargs, {"api_key": "test-key"})
        self.assertEqual(FakeAnthropic.instances[0].messages.create_calls, [])
        self.assertEqual(FakeAnthropic.instances[0].messages.stream_calls[0]["model"], "claude-test")

    def test_ollama_stream_unsupported_falls_back_to_non_streaming(self) -> None:
        class FakeCompletions:
            def __init__(self) -> None:
                self.calls: list[dict] = []

            def create(self, **kwargs):
                self.calls.append(kwargs)
                if kwargs.get("stream"):
                    raise RuntimeError("stream is not supported by this endpoint")
                return SimpleNamespace(choices=[SimpleNamespace(message=SimpleNamespace(content='{"reply":"fallback"}'))])

        class FakeChat:
            def __init__(self) -> None:
                self.completions = FakeCompletions()

        class FakeOpenAI:
            instances: list["FakeOpenAI"] = []

            def __init__(self, **kwargs) -> None:
                self.kwargs = kwargs
                self.chat = FakeChat()
                FakeOpenAI.instances.append(self)

        openai_module = types.ModuleType("openai")
        openai_module.OpenAI = FakeOpenAI
        self.addCleanup(lambda: sys.modules.pop("openai", None))
        sys.modules["openai"] = openai_module
        settings = make_llm_settings("ollama", model="local-model", base_url="http://127.0.0.1:11434/v1")
        chunks: list[str] = []

        response = request_openai_compatible_plan_with_metadata(settings, "prompt", stream_callback=chunks.append)

        self.assertEqual(response.text, '{"reply":"fallback"}')
        self.assertEqual(chunks, [])
        self.assertEqual([call.get("stream", False) for call in FakeOpenAI.instances[0].chat.completions.calls], [True, False])


class LlmReasoningTraceTests(unittest.TestCase):
    def test_extracts_openai_compatible_reasoning_fields(self) -> None:
        settings = SimpleNamespace(llm_provider="deepseek", llm_model="deepseek-reasoner")
        response = SimpleNamespace(
            choices=[
                SimpleNamespace(
                    message=SimpleNamespace(
                        content='{"summary":"done"}',
                        reasoning_content="checked avatar context first",
                    )
                )
            ]
        )

        trace = extract_llm_reasoning_trace(response, settings, source="openai-compatible")

        self.assertEqual(trace["provider"], "deepseek")
        self.assertEqual(trace["model"], "deepseek-reasoner")
        self.assertEqual(trace["itemCount"], 1)
        self.assertEqual(trace["items"][0]["title"], "reasoning_content")
        self.assertIn("checked avatar", trace["items"][0]["text"])

    def test_extracts_anthropic_thinking_blocks(self) -> None:
        settings = SimpleNamespace(llm_provider="anthropic", llm_model="claude-sonnet")
        response = SimpleNamespace(
            content=[
                SimpleNamespace(type="thinking", thinking="inspect tool state, then answer"),
                SimpleNamespace(type="text", text='{"reply":"ok"}'),
            ]
        )

        trace = extract_llm_reasoning_trace(response, settings, source="anthropic")

        self.assertEqual(trace["itemCount"], 1)
        self.assertEqual(trace["items"][0]["kind"], "thinking")
        self.assertIn("inspect tool", trace["items"][0]["text"])

    def test_extracts_gemini_thought_summary_parts(self) -> None:
        settings = SimpleNamespace(llm_provider="gemini", llm_model="gemini-2.5-flash")
        response = SimpleNamespace(
            candidates=[
                SimpleNamespace(
                    content=SimpleNamespace(
                        parts=[
                            SimpleNamespace(thought=True, text="summarized thought"),
                            SimpleNamespace(thought=False, text='{"reply":"ok"}'),
                        ]
                    )
                )
            ]
        )

        trace = extract_llm_reasoning_trace(response, settings, source="gemini")

        self.assertEqual(trace["itemCount"], 1)
        self.assertEqual(trace["items"][0]["title"], "thought summary")
        self.assertEqual(trace["items"][0]["text"], "summarized thought")

    def test_extracts_openrouter_reasoning_details_and_opaque_items(self) -> None:
        settings = SimpleNamespace(llm_provider="openrouter", llm_model="deepseek/deepseek-r1")
        response = {
            "choices": [
                {
                    "message": {
                        "content": "{}",
                        "reasoning_details": [{"type": "reasoning.text", "text": "visible block"}],
                    }
                }
            ],
            "output": [{"type": "reasoning", "encrypted_content": "opaque"}],
        }

        trace = extract_llm_reasoning_trace(response, settings, source="openrouter")

        self.assertEqual(trace["itemCount"], 2)
        self.assertTrue(trace["redacted"])
        self.assertIn("visible block", trace["items"][0]["text"])
        self.assertTrue(trace["items"][1]["opaque"])


class LlmTokenUsageTests(unittest.TestCase):
    def test_extracts_openai_compatible_usage(self) -> None:
        settings = SimpleNamespace(llm_provider="deepseek", llm_model="deepseek-v4-pro")
        response = {"usage": {"prompt_tokens": 1200, "completion_tokens": 34, "total_tokens": 1234}}

        usage = extract_llm_token_usage(response, settings, source="openai-compatible")

        self.assertTrue(usage["exact"])
        self.assertEqual(usage["inputTokens"], 1200)
        self.assertEqual(usage["outputTokens"], 34)
        self.assertEqual(usage["totalTokens"], 1234)

    def test_extracts_gemini_usage_metadata(self) -> None:
        settings = SimpleNamespace(llm_provider="gemini", llm_model="gemini-2.5-flash")
        response = SimpleNamespace(
            usageMetadata=SimpleNamespace(
                promptTokenCount=2048,
                candidatesTokenCount=128,
                totalTokenCount=2176,
                cachedTokens=512,
            )
        )

        usage = extract_llm_token_usage(response, settings, source="gemini")

        self.assertTrue(usage["exact"])
        self.assertEqual(usage["inputTokens"], 2048)
        self.assertEqual(usage["outputTokens"], 128)
        self.assertEqual(usage["totalTokens"], 2176)
        self.assertEqual(usage["cacheReadTokens"], 512)

    def test_extracts_anthropic_usage_and_marks_missing_usage_unavailable(self) -> None:
        settings = SimpleNamespace(llm_provider="anthropic", llm_model="claude-sonnet")
        response = SimpleNamespace(usage=SimpleNamespace(input_tokens=100, output_tokens=20))

        usage = extract_llm_token_usage(response, settings, source="anthropic")
        missing = extract_llm_token_usage(SimpleNamespace(), settings, source="anthropic")

        self.assertTrue(usage["exact"])
        self.assertEqual(usage["inputTokens"], 100)
        self.assertEqual(usage["outputTokens"], 20)
        self.assertEqual(usage["totalTokens"], 120)
        self.assertFalse(missing["exact"])
        self.assertEqual(missing["unavailableReason"], "provider_usage_missing")


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
            execute_tool_name="vrc_apply_blendshapes",
            export_path=Path("Assets/VRCForge/blendshapes_export.json"),
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
        self.assertEqual(request_mock.call_args.kwargs["reference_image_paths"], [Path("reference.png")])
        self.assertIn("image(s) are attached", request_mock.call_args.args[1].lower())

    def test_create_blendshape_plan_passes_multiple_reference_images_with_labels(self) -> None:
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
            execute_tool_name="vrc_apply_blendshapes",
            export_path=Path("Assets/VRCForge/blendshapes_export.json"),
            min_confidence=0.65,
        )
        raw_plan = {"summary": "Noop", "warnings": [], "adjustments": []}
        image_paths = [Path("before.png"), Path("target-a.png"), Path("target-b.png")]
        labels = ["原图 / 当前脸 1", "目标参考图 1", "目标参考图 2"]

        with patch("vrchat_blendshape_agent.request_llm_plan", return_value=json.dumps(raw_plan)) as request_mock:
            create_blendshape_plan(
                settings,
                self.planning_payload,
                "match the target face",
                reference_image_paths=image_paths,
                reference_image_labels=labels,
            )

        self.assertEqual(request_mock.call_args.kwargs["reference_image_paths"], image_paths)
        prompt = request_mock.call_args.args[1]
        self.assertIn("Image 1: 原图 / 当前脸 1", prompt)
        self.assertIn("Image 3: 目标参考图 2", prompt)

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
            execute_tool_name="vrc_apply_blendshapes",
            export_path=Path("Assets/VRCForge/blendshapes_export.json"),
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
            execute_tool_name="vrc_apply_blendshapes",
            export_path=Path("Assets/VRCForge/blendshapes_export.json"),
            min_confidence=0.65,
        )

        args = build_custom_tool_cli_args(settings, "vrc_apply_blendshapes", {"avatarPath": "Avatar", "adjustments": []})

        self.assertEqual(args[:3], ["editor", "custom-tool", "vrc_apply_blendshapes"])
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
            "❌ Error: Unity MCP server is not ready yet.\n"
        )

        self.assertEqual(message, "Unity MCP server is not ready yet.")

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
                execute_tool_name="vrc_apply_blendshapes",
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
            execute_tool_name="vrc_apply_blendshapes",
            export_path=Path("Assets/VRCForge/blendshapes_export.json"),
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

    def test_load_settings_accepts_utf8_bom(self) -> None:
        settings_payload = {
            "gemini": {
                "api_key_env": "TEST_GEMINI_API_KEY",
                "model": "gemini-2.5-flash",
            }
        }

        with tempfile.TemporaryDirectory() as temp_dir:
            settings_path = Path(temp_dir) / "settings.json"
            settings_path.write_text(json.dumps(settings_payload, ensure_ascii=False), encoding="utf-8-sig")

            settings = load_settings(settings_path)
            self.assertEqual(settings.llm_provider, "gemini")
            self.assertEqual(settings.llm_model, "gemini-2.5-flash")
            self.assertEqual(settings.llm_api_key_env, "TEST_GEMINI_API_KEY")

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
        result = mock_execute_payload(
            apply_payload='{"tool":"vrc_apply_blendshapes"}',
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
