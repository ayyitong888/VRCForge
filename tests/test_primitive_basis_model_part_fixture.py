from __future__ import annotations

import hashlib
import json
import re
import shutil
from pathlib import Path

import pytest

import primitive_basis_matrix as matrix


DESCRIPTOR_DIR = Path(__file__).parent / "fixtures" / "primitive_basis"
PROJECT_ROOT = DESCRIPTOR_DIR / "projects" / "model_part_composition"
FIXTURE_ROOT = (
    PROJECT_ROOT
    / "Assets"
    / "VRCForge"
    / "PrimitiveBasis"
    / "model_part_composition"
)
CONTRACT_PATH = FIXTURE_ROOT / "fixture-contract.json"
BASELINE_PATH = FIXTURE_ROOT / "baseline.json"
SCENE_PATH = FIXTURE_ROOT / "ModelPartComposition.unity"
BOOTSTRAP_PATH = FIXTURE_ROOT / "Editor" / "ModelPartCompositionFixtureBootstrap.cs"
LIVE_GUARD_PATH = (
    Path(__file__).parents[1] / "Assets" / "VRCForge" / "Editor" / "PrimitiveBasisLiveGuard.cs"
)
INSPECTOR_PATH = (
    Path(__file__).parents[1]
    / "Assets"
    / "VRCForge"
    / "Editor"
    / "PrimitiveBasisFixtureInspector.cs"
)
WRITER_PATH = Path(__file__).parents[1] / "Assets" / "VRCForge" / "Editor" / "MAComponentWriter.cs"


def load_json(path: Path) -> dict[str, object]:
    payload = json.loads(path.read_text(encoding="utf-8"))
    assert isinstance(payload, dict)
    return payload


def sha256_file(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def fixture_inventory(root: Path) -> list[dict[str, object]]:
    inventory: list[dict[str, object]] = []
    for path in sorted(
        (candidate for candidate in root.rglob("*") if candidate.is_file()),
        key=lambda candidate: candidate.relative_to(root).as_posix(),
    ):
        relative_path = path.relative_to(root).as_posix()
        if relative_path == "baseline.json":
            continue
        inventory.append(
            {
                "path": relative_path,
                "size": path.stat().st_size,
                "sha256": sha256_file(path),
            }
        )
    return inventory


def test_model_part_fixture_is_pinned_and_materializes() -> None:
    fixtures = matrix.load_fixture_set(DESCRIPTOR_DIR, repository_root=PROJECT_ROOT)
    fixture = next(
        item for item in fixtures.fixtures if item.scenario_id == "model_part_composition"
    )

    assert fixtures.descriptor_digest == (
        "7189e1945ec594813371a628ae093f3d4c73892bd3b1102f545b0a9486887ae6"
    )
    assert fixture.descriptor_digest == (
        "80be020fb612159898506a69b1e4c32ca9665b951b5704da06931b0c1f16db5d"
    )
    assert fixture.digest == (
        "5e2173a4f73b505078947e452341f6f9ae75277f17e00e39aaa0f301d072e4e9"
    )
    assert fixture.materialized is True
    assert fixture.materialization_error == ""
    assert [item.scenario_id for item in fixtures.fixtures if item.materialized] == [
        "model_part_composition"
    ]
    assert all(
        item.materialization_error == "fixture_digest_unpinned"
        for item in fixtures.fixtures
        if item.scenario_id != "model_part_composition"
    )
    assert fixtures.digest == ""


def test_model_part_fixture_contract_binds_project_files_and_dependencies() -> None:
    contract = load_json(CONTRACT_PATH)

    assert contract["schema"] == "vrcforge.primitive_basis_model_part_fixture.v1"
    assert contract["scenarioId"] == "model_part_composition"
    assert contract["primitiveId"] == "non_destructive_part_composition"
    assert contract["unity"] == {
        "version": "2022.3.22f1",
        "revision": "887be4894c44",
    }
    assert contract["scene"] == {
        "assetPath": "Assets/VRCForge/PrimitiveBasis/model_part_composition/ModelPartComposition.unity",
        "guid": "285dbe12f5ede174cbcd075983e1410f",
        "avatarPath": "FixtureAvatar",
        "baseArmaturePath": "FixtureAvatar/Armature",
        "partRootPath": "FixtureAvatar/Part",
        "componentHostPath": "FixtureAvatar/Part/Armature",
        "mergeTargetPath": "FixtureAvatar/Armature",
        "rendererPath": "FixtureAvatar/Part/RendererProbe",
        "baselineComponentCount": 0,
    }
    scene_meta = SCENE_PATH.with_suffix(".unity.meta").read_text(encoding="utf-8")
    assert re.search(r"^guid: 285dbe12f5ede174cbcd075983e1410f$", scene_meta, re.MULTILINE)

    project_files = contract["projectFiles"]
    assert isinstance(project_files, list)
    assert [entry["path"] for entry in project_files] == [
        "Packages/manifest.json",
        "Packages/packages-lock.json",
        "ProjectSettings/ProjectVersion.txt",
    ]
    for entry in project_files:
        path = PROJECT_ROOT.joinpath(*Path(entry["path"]).parts)
        assert path.is_file()
        assert entry["sha256"] == sha256_file(path)

    assert contract["requiredPackages"] == [
        {
            "id": "com.coplaydev.unity-mcp",
            "version": "9.6.9-beta.7",
            "provisioning": "exact_artifact",
        },
        {
            "id": "com.vrchat.avatars",
            "version": "3.10.3",
            "provisioning": "exact_artifact",
        },
        {
            "id": "com.vrchat.base",
            "version": "3.10.3",
            "provisioning": "exact_artifact",
        },
        {
            "id": "nadena.dev.modular-avatar",
            "version": "1.17.1",
            "provisioning": "exact_artifact",
        },
        {
            "id": "nadena.dev.ndmf",
            "version": "1.13.1",
            "provisioning": "exact_artifact",
        },
    ]
    assert contract["runtime"] == {
        "bootstrapType": "VRCForge.PrimitiveBasisFixtures.ModelPartCompositionFixtureBootstrap",
        "runIdEnvironment": "VRCFORGE_PRIMITIVE_BASIS_RUN_ID",
        "readyMarker": "Library/VRCForge/primitive-basis-model-part-ready.json",
    }

    manifest = load_json(PROJECT_ROOT / "Packages" / "manifest.json")
    required_builtin_modules = {
        "com.unity.modules.ai",
        "com.unity.modules.androidjni",
        "com.unity.modules.animation",
        "com.unity.modules.assetbundle",
        "com.unity.modules.audio",
        "com.unity.modules.cloth",
        "com.unity.modules.director",
        "com.unity.modules.imageconversion",
        "com.unity.modules.imgui",
        "com.unity.modules.jsonserialize",
        "com.unity.modules.particlesystem",
        "com.unity.modules.physics",
        "com.unity.modules.physics2d",
        "com.unity.modules.screencapture",
        "com.unity.modules.terrain",
        "com.unity.modules.terrainphysics",
        "com.unity.modules.tilemap",
        "com.unity.modules.ui",
        "com.unity.modules.uielements",
        "com.unity.modules.umbra",
        "com.unity.modules.unityanalytics",
        "com.unity.modules.unitywebrequest",
        "com.unity.modules.unitywebrequestassetbundle",
        "com.unity.modules.unitywebrequestaudio",
        "com.unity.modules.unitywebrequesttexture",
        "com.unity.modules.unitywebrequestwww",
        "com.unity.modules.vehicles",
        "com.unity.modules.video",
        "com.unity.modules.vr",
        "com.unity.modules.wind",
        "com.unity.modules.xr",
    }
    assert manifest == {
        "dependencies": {name: "1.0.0" for name in sorted(required_builtin_modules)}
    }
    lock = load_json(PROJECT_ROOT / "Packages" / "packages-lock.json")
    locked_dependencies = lock["dependencies"]
    assert isinstance(locked_dependencies, dict)
    for name in required_builtin_modules:
        assert locked_dependencies[name]["version"] == "1.0.0"
        assert locked_dependencies[name]["depth"] == 0
        assert locked_dependencies[name]["source"] == "builtin"
    for name in (
        "com.coplaydev.unity-mcp",
        "com.vrchat.avatars",
        "com.vrchat.base",
        "nadena.dev.modular-avatar",
        "nadena.dev.ndmf",
    ):
        assert locked_dependencies[name]["version"] == f"file:{name}"
        assert locked_dependencies[name]["depth"] == 0
        assert locked_dependencies[name]["source"] == "embedded"
    assert locked_dependencies["com.unity.nuget.newtonsoft-json"]["version"] == "3.2.1"
    assert locked_dependencies["com.unity.burst"]["version"] == "1.8.12"
    assert locked_dependencies["com.unity.test-framework"]["version"] == "1.1.33"
    assert locked_dependencies["com.unity.xr.management"]["version"] == "4.4.0"
    assert (PROJECT_ROOT / "ProjectSettings" / "ProjectVersion.txt").read_text(
        encoding="utf-8"
    ) == (
        "m_EditorVersion: 2022.3.22f1\n"
        "m_EditorVersionWithRevision: 2022.3.22f1 (887be4894c44)\n"
    )

    descriptor_copy_root = PROJECT_ROOT / "VRCForgeFixture" / "descriptors"
    for source in sorted(DESCRIPTOR_DIR.glob("*.json")):
        copied = descriptor_copy_root / source.name
        assert copied.is_file()
        assert copied.read_bytes() == source.read_bytes()


def test_model_part_fixture_baseline_matches_real_tree_and_scene_contract() -> None:
    baseline = load_json(BASELINE_PATH)
    assert baseline["schema"] == matrix.BASELINE_SCHEMA
    assert baseline["scenarioId"] == "model_part_composition"
    assert baseline["files"] == fixture_inventory(FIXTURE_ROOT)

    scene = SCENE_PATH.read_text(encoding="utf-8")
    names = re.findall(r"^  m_Name: (.+)$", scene, flags=re.MULTILINE)
    assert names.count("FixtureAvatar") == 1
    assert names.count("Part") == 1
    assert names.count("Armature") == 2
    assert names.count("Hips") == 2
    assert names.count("RendererProbe") == 1
    assert scene.count("SkinnedMeshRenderer:") == 1
    assert "ModularAvatarMergeArmature" not in scene
    assert scene.count("guid: 52fa21b17bc14dc294959f976e3e184f") == 1

    bootstrap = BOOTSTRAP_PATH.read_text(encoding="utf-8")
    assert "Application.isBatchMode" in bootstrap
    assert "VRCFORGE_PRIMITIVE_BASIS_RUN_ID" in bootstrap
    assert "Assets/VRCForge/PrimitiveBasis/model_part_composition/ModelPartComposition.unity" in bootstrap
    assert "Library/VRCForge/primitive-basis-model-part-ready.json" in bootstrap
    assert "EditorSceneManager.OpenScene" in bootstrap
    assert "AddComponent<nadena.dev.ndmf.runtime.components.NDMFAvatarRoot>()" in bootstrap

    for generated_directory in ("Library", "Logs", "Temp", "UserSettings"):
        assert not (PROJECT_ROOT / generated_directory).exists()


def test_model_part_live_editor_contract_is_bound_and_reloadable() -> None:
    guard = LIVE_GUARD_PATH.read_text(encoding="utf-8")
    for field in (
        "expectedRunIdDigest",
        "expectedProjectPathDigest",
        "expectedUnityProcessId",
        "expectedUnityProcessStartedAtUtc",
        "expectedUnityExecutableDigest",
    ):
        assert field in guard
    assert "Process.GetCurrentProcess()" in guard
    assert "process.MainModule?.FileName" in guard
    assert "process.StartTime.ToUniversalTime()" in guard

    inspector = INSPECTOR_PATH.read_text(encoding="utf-8")
    assert 'name: "vrc_inspect_primitive_basis_fixture"' in inspector
    assert 'name: "vrc_reload_primitive_basis_fixture"' in inspector
    assert "activeScene.isDirty" in inspector
    assert "EditorSceneManager.OpenScene(ScenePath, OpenSceneMode.Single)" in inspector
    assert 'FindType("nadena.dev.ndmf.runtime.components.NDMFAvatarRoot")' in inspector

    writer = WRITER_PATH.read_text(encoding="utf-8")
    assert "PrimitiveBasisLiveGuard.RequireBoundRequest(@params)" in writer
    assert 'name: "vrc_inspect_modular_avatar_component"' in writer
    assert "AvatarObjectReference" in writer


def test_model_part_fixture_scene_drift_fails_materialization(tmp_path: Path) -> None:
    repository_root = tmp_path / "fixture-project"
    shutil.copytree(PROJECT_ROOT, repository_root)
    copied_scene = (
        repository_root
        / "Assets"
        / "VRCForge"
        / "PrimitiveBasis"
        / "model_part_composition"
        / "ModelPartComposition.unity"
    )
    copied_scene.write_bytes(copied_scene.read_bytes() + b"\n# drift\n")

    with pytest.raises(matrix.MatrixContractError, match="fixture file digest mismatch"):
        matrix.load_fixture_set(DESCRIPTOR_DIR, repository_root=repository_root)
