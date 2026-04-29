import unittest

from vrchat_blendshape_agent import (
    BlendshapeAdjustment,
    BlendshapePlan,
    SelectedAvatar,
    build_planning_payload,
    resolve_avatar_selection,
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


if __name__ == "__main__":
    unittest.main()
