import json
import tempfile
import unittest
from pathlib import Path

from vrchat_blendshape_agent import (
    BlendshapeAdjustment,
    BlendshapePlan,
    build_planning_payload,
    load_settings,
    load_export_payload,
    mock_execute_csharp,
    read_plan_json,
    read_export_json,
    resolve_avatar_selection,
    render_preview,
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


class MvpFlowTests(unittest.TestCase):
    def test_load_settings_allows_model_override(self) -> None:
        settings_payload = {
            "gemini": {
                "api_key_env": "TEST_GEMINI_API_KEY",
                "model": "gemini-3.1-pro-preview",
                "thinking_level": "low",
            }
        }

        with tempfile.TemporaryDirectory() as temp_dir:
            settings_path = Path(temp_dir) / "settings.json"
            settings_path.write_text(json.dumps(settings_payload, ensure_ascii=False), encoding="utf-8")

            settings = load_settings(settings_path, gemini_model_override="gemini-2.5-flash")
            self.assertEqual(settings.gemini_model, "gemini-2.5-flash")

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
