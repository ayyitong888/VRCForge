"""Pin the checkpoint editor-state gate to its declared file scope.

Source-level contract check (no Unity load), mirroring the production-method
assertions in test_prefab_unpack_dirty_scene_runtime.
"""
from pathlib import Path
import re

ROOT = Path(__file__).resolve().parents[1]
SOURCE = (ROOT / "Assets/VRCForge/Editor/CheckpointRecoveryTool.cs").read_text(encoding="utf-8-sig")


def test_dirty_scenes_are_filtered_by_the_declared_file_scope():
    # Dirty assets already had the file-scope filter; dirty scenes must mirror
    # it so an unrelated unsaved open scene cannot block this write.
    pattern = re.compile(
        r"blockingDirtyScenePaths = dirtyScenePaths\.Where\("
        r"path => fileScope == null \|\| fileScope\.Contains\(path\)\)"
    )
    assert pattern.search(SOURCE), "dirty scene gate lost its file-scope filter"
    assert re.search(
        r"blockingDirtyScenePaths\.Length > 0 \|\| blockingDirtyAssetPaths\.Length > 0",
        SOURCE,
    ), "blocking decision must use the scoped scene list"


def test_editor_state_gate_rejection_carries_an_actionable_next_action():
    assert 'var nextAction = code == "unsaved_open_scene"' in SOURCE
    assert "Save the untitled scene under Assets" in SOURCE
    assert "Save the blocking dirty scenes or assets" in SOURCE
    assert re.search(r"nextAction, blocking = true", SOURCE), "nextAction must be part of the failure payload"


def test_success_path_still_reports_all_dirty_scenes_as_diagnostics():
    assert re.search(
        r"VRCForgeToolResult\.Completed\(\"Inspected saved state[^\"]*\",\s*new\s*\{[^}]*dirtyScenePaths",
        SOURCE,
        re.DOTALL,
    )


def test_source_only_shader_dirty_is_benign_in_the_gate():
    # The gate classifies shader/compiled-source assets as benign: their disk
    # file is the only writable state a disk checkpoint captures, so a
    # re-import dirty flag (lilToon on 2022.3) must not deadlock full-project
    # checkpoints.
    assert "IsSourceOnlyAssetPath" in SOURCE
    for extension in ('".shader"', '".hlsl"', '".cginc"', '".compute"'):
        assert extension in SOURCE
    assert "benignDirtyAssetPaths" in SOURCE
    # Blocking decision excludes the benign set.
    assert re.search(
        r"blockingDirtyAssetPaths\s*=\s*blockingDirtyAssetPaths\s*\.Where\(path => !IsSourceOnlyAssetPath",
        SOURCE,
    ), "benign exclusion missing"
