from __future__ import annotations

import hashlib
import json
import shutil
from pathlib import Path

import pytest

import primitive_basis_matrix as matrix


DESCRIPTOR_DIR = Path(__file__).parent / "fixtures" / "primitive_basis"
PROJECT_ROOT = DESCRIPTOR_DIR / "projects" / "model_part_composition"
PRIMITIVE_ROOT = (
    PROJECT_ROOT / "Assets" / "VRCForge" / "PrimitiveBasis"
)

RUNTIME_GENERATED_FIXTURES = {
    "component_feature_application": {
        "schema": "vrcforge.primitive_basis_component_feature_fixture.v1",
        "script": "ComponentFeatureApplicationFixtureBootstrap.cs",
        "bootstrapType": (
            "VRCForge.PrimitiveBasisFixtures."
            "ComponentFeatureApplicationFixtureBootstrap"
        ),
        "environment": "VRCFORGE_PRIMITIVE_COMPONENT_FEATURE_APPLICATION_RUN_ID",
        "generatedRoot": (
            "Assets/VRCForge/PrimitiveBasis/RuntimeComponentFeatureApplication"
        ),
        "readyMarker": (
            "Library/VRCForge/"
            "primitive-basis-component-feature-application-ready.json"
        ),
        "packages": {
            "com.coplaydev.unity-mcp": "9.6.9-beta.7",
            "com.vrchat.avatars": "3.10.3",
            "com.vrchat.base": "3.10.3",
            "com.vrcfury.vrcfury": "1.1334.0",
        },
        "tokens": (
            'new GameObject("Avatar")',
            'AddChild(avatar.transform, "FeatureHost")',
            'AddChild(avatar.transform, "ArmatureFeatureHost")',
            'component.GetType().FullName == "VF.Model.VRCFury"',
            "baselineFeatureComponentCount = 0",
        ),
    },
    "parameter_optimization": {
        "schema": "vrcforge.primitive_basis_parameter_optimization_fixture.v1",
        "script": "ParameterOptimizationFixtureBootstrap.cs",
        "bootstrapType": (
            "VRCForge.PrimitiveBasisFixtures."
            "ParameterOptimizationFixtureBootstrap"
        ),
        "environment": "VRCFORGE_PRIMITIVE_PARAMETER_OPTIMIZATION_RUN_ID",
        "generatedRoot": (
            "Assets/VRCForge/PrimitiveBasis/RuntimeParameterOptimization"
        ),
        "readyMarker": (
            "Library/VRCForge/primitive-basis-parameter-optimization-ready.json"
        ),
        "packages": {
            "com.coplaydev.unity-mcp": "9.6.9-beta.7",
            "com.vrchat.avatars": "3.10.3",
            "com.vrchat.base": "3.10.3",
            "com.vrcfury.vrcfury": "1.1334.0",
            "nadena.dev.ndmf": "1.13.1",
        },
        "tokens": (
            "public const int SafeToggleCount = 260;",
            '"FT/JawOpen"',
            '"Puppet/X"',
            '"OSC/Raw"',
            "CreateMenuTree(out var menuAssetCount)",
            "AddStateMachineBehaviour<VRCAvatarParameterDriver>()",
            '"CreateToggle"',
            "baselineFeatureComponentCount = featureCount",
        ),
    },
    "cross_avatar_accessory_copy": {
        "schema": "vrcforge.primitive_basis_cross_avatar_copy_fixture.v1",
        "script": "CrossAvatarAccessoryCopyFixtureBootstrap.cs",
        "bootstrapType": (
            "VRCForge.PrimitiveBasisFixtures."
            "CrossAvatarAccessoryCopyFixtureBootstrap"
        ),
        "environment": "VRCFORGE_PRIMITIVE_CROSS_AVATAR_ACCESSORY_COPY_RUN_ID",
        "generatedRoot": (
            "Assets/VRCForge/PrimitiveBasis/RuntimeCrossAvatarAccessoryCopy"
        ),
        "readyMarker": (
            "Library/VRCForge/primitive-basis-cross-avatar-accessory-copy-ready.json"
        ),
        "packages": {
            "com.coplaydev.unity-mcp": "9.6.9-beta.7",
            "com.vrchat.avatars": "3.10.3",
            "com.vrchat.base": "3.10.3",
        },
        "tokens": (
            'new GameObject("AvatarA")',
            'new GameObject("AvatarB")',
            'AddChild(sourceAvatar.transform, "Accessory")',
            "accessory.AddComponent<MeshRenderer>()",
            "accessory.AddComponent<ParentConstraint>()",
            "EditorCurveBinding.FloatCurve(",
            '"Accessory",',
            "baselineTargetCopyCount = 0",
            "baselinePrefabCount = 0",
        ),
    },
}


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


def test_all_four_fixed_fixture_roots_materialize_from_pinned_sources() -> None:
    fixtures = matrix.load_fixture_set(
        DESCRIPTOR_DIR,
        repository_root=PROJECT_ROOT,
    )

    assert fixtures.descriptor_digest == (
        "e1e1422cdc40af2a3a0a7aef7d43ddac21e0961c8edaad31bae38ed792f28ea6"
    )
    assert fixtures.digest == (
        "8c6a3cd60ed64b819f0f4fbe33a7e81772d14ba4394866e277b3329c31f60471"
    )
    assert [fixture.scenario_id for fixture in fixtures.fixtures] == list(
        matrix.SCENARIO_ORDER
    )
    assert all(fixture.materialized for fixture in fixtures.fixtures)
    assert all(not fixture.materialization_error for fixture in fixtures.fixtures)


@pytest.mark.parametrize(
    ("scenario_id", "expected"),
    RUNTIME_GENERATED_FIXTURES.items(),
)
def test_runtime_generated_fixture_contract_and_inventory_are_exact(
    scenario_id: str,
    expected: dict[str, object],
) -> None:
    root = PRIMITIVE_ROOT / scenario_id
    descriptor_path = DESCRIPTOR_DIR / f"{scenario_id}.json"
    copied_descriptor_path = (
        PROJECT_ROOT / "VRCForgeFixture" / "descriptors" / descriptor_path.name
    )
    baseline = load_json(root / "baseline.json")
    contract = load_json(root / "fixture-contract.json")

    assert copied_descriptor_path.read_bytes() == descriptor_path.read_bytes()
    assert baseline == {
        "schema": matrix.BASELINE_SCHEMA,
        "scenarioId": scenario_id,
        "files": fixture_inventory(root),
    }
    assert contract["schema"] == expected["schema"]
    assert contract["scenarioId"] == scenario_id
    assert contract["unity"] == {
        "version": "2022.3.22f1",
        "revision": "887be4894c44",
    }
    assert contract["sourceMode"] == "isolated_runtime_generation"
    assert {
        package["id"]: package["version"]
        for package in contract["requiredPackages"]
    } == expected["packages"]
    assert contract["runtime"] == {
        "bootstrapType": expected["bootstrapType"],
        "runIdEnvironment": expected["environment"],
        "generatedRoot": expected["generatedRoot"],
        "generatedRootPrecondition": "absent",
        "readyMarker": expected["readyMarker"],
        "cleanup": "remove_generated_root_and_ready_marker",
    }
    scene = contract["scene"]
    if scenario_id == "component_feature_application":
        assert scene["baselineFeatureComponentCount"] == 0
        assert scene["targetPaths"] == [
            "Avatar/PropRoot",
            "Avatar/ChestTarget",
        ]
    elif scenario_id == "parameter_optimization":
        assert scene["parameterCount"] == 263
        assert scene["safeNetworkedBoolCount"] == 260
        assert scene["eligibleCostBits"] == 260
        assert scene["budgetBits"] == 256
        assert scene["eligibleCostBits"] > scene["budgetBits"]
        assert scene["menuAssetCount"] == 41
        assert scene["excludedParameterNames"] == [
            "FT/JawOpen",
            "Puppet/X",
            "OSC/Raw",
        ]
        assert scene["baselineFeatureComponentCount"] == 1
    else:
        assert scene["rendererCount"] == 1
        assert scene["constraintSourceCount"] == 1
        assert scene["baselineTargetCopyCount"] == 0
        assert scene["baselinePrefabCount"] == 0


@pytest.mark.parametrize(
    ("scenario_id", "expected"),
    RUNTIME_GENERATED_FIXTURES.items(),
)
def test_runtime_generated_fixture_bootstrap_is_gated_and_semantic(
    scenario_id: str,
    expected: dict[str, object],
) -> None:
    source = (
        PRIMITIVE_ROOT / scenario_id / "Editor" / str(expected["script"])
    ).read_text(encoding="utf-8")

    for token in (
        "Application.isBatchMode",
        f'Environment.GetEnvironmentVariable(RunIdEnvironment)',
        f'public const string RunIdEnvironment =',
        str(expected["environment"]),
        str(expected["generatedRoot"]),
        str(expected["readyMarker"]),
        "RequireGeneratedRootAbsent()",
        "FileMode.CreateNew",
        "stream.Flush(true)",
        "File.Move(temporaryPath, finalPath)",
        *expected["tokens"],
    ):
        assert token in source

    assert not (PROJECT_ROOT / str(expected["generatedRoot"])).exists()
    assert not (PROJECT_ROOT / str(expected["readyMarker"])).exists()


@pytest.mark.parametrize(
    ("scenario_id", "script_name"),
    [
        (scenario_id, str(expected["script"]))
        for scenario_id, expected in RUNTIME_GENERATED_FIXTURES.items()
    ],
)
def test_runtime_generated_fixture_source_drift_fails_materialization(
    tmp_path: Path,
    scenario_id: str,
    script_name: str,
) -> None:
    repository_root = tmp_path / "fixture-project"
    shutil.copytree(PROJECT_ROOT, repository_root)
    copied_source = (
        repository_root
        / "Assets"
        / "VRCForge"
        / "PrimitiveBasis"
        / scenario_id
        / "Editor"
        / script_name
    )
    copied_source.write_bytes(copied_source.read_bytes() + b"\n// drift\n")

    with pytest.raises(matrix.MatrixContractError, match="fixture file digest mismatch"):
        matrix.load_fixture_set(
            DESCRIPTOR_DIR,
            repository_root=repository_root,
        )
