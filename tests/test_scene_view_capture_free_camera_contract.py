from pathlib import Path


SOURCE = Path(__file__).parents[1] / "Assets" / "VRCForge" / "Editor" / "SceneViewCaptureTool.cs"


def test_free_camera_preserves_target_renderer_visibility_and_evidence():
    source = SOURCE.read_text(encoding="utf-8")

    assert "CaptureFreeCamera(activeGameCamera, absolutePath, width, height, freeCamera, avatarPath)" in source
    assert "CaptureFreeCamera(camera, absolutePath, width, height, freeCamera, avatarPath)" in source
    assert "camera.cullingMask |= evidence.RendererLayerMask;" in source
    assert "renderer.enabled && renderer.gameObject.activeInHierarchy" in source
    assert '["targetSceneValid"]' in source
    assert '["targetRendererLayersIncluded"]' in source


def test_free_camera_rejects_unresolved_explicit_avatar_target():
    source = SOURCE.read_text(encoding="utf-8")

    assert '"capture_target_not_found"' in source
    assert "free-camera capture refused without an unverified renderer target." in source
