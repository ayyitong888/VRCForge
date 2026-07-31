from __future__ import annotations

import dataclasses
import hashlib
import importlib.util
import json
import os
import stat
import sys
import zipfile
from pathlib import Path
from types import SimpleNamespace
from typing import Any, Callable

import pytest


SCRIPT_PATH = (
    Path(__file__).resolve().parents[1]
    / "packaging"
    / "protected_runtime_source_manifest.py"
)
ROOT = Path(__file__).resolve().parents[1]
DEPENDENCY_PROJECT = (
    ROOT
    / "tests"
    / "fixtures"
    / "primitive_basis"
    / "projects"
    / "model_part_composition"
)
VERSION = "1.4.0"
SOURCE_COMMIT = "1" * 40


def _load_module() -> Any:
    spec = importlib.util.spec_from_file_location(
        "protected_runtime_source_manifest_for_test",
        SCRIPT_PATH,
    )
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


source_tool = _load_module()
dependency_tool = source_tool.protected_runtime_dependency_set


def _write_bytes(path: Path, content: bytes) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(content)
    return path


def _json_digest(value: Any) -> str:
    return hashlib.sha256(
        json.dumps(
            value,
            ensure_ascii=True,
            allow_nan=False,
            separators=(",", ":"),
            sort_keys=True,
        ).encode("utf-8")
    ).hexdigest()


def _write_json(path: Path, value: Any, *, canonical: bool = False) -> Path:
    if canonical:
        return _write_bytes(path, source_tool.canonical_json_bytes(value) + b"\n")
    return _write_bytes(
        path,
        (json.dumps(value, ensure_ascii=False, indent=2) + "\n").encode("utf-8"),
    )


def _fixed_digest(label: str) -> str:
    return hashlib.sha256(label.encode("utf-8")).hexdigest()


def _dependency_tree_record(label: str) -> dict[str, Any]:
    return {
        "schema": "vrcforge.protected_runtime_tree_source.v1",
        "treeDigest": _fixed_digest(f"tree:{label}"),
        "bindingDigest": _fixed_digest(f"binding:{label}"),
        "directoryCount": 1,
        "entryCount": 1,
        "byteCount": 1,
    }


def _valid_dependency_descriptor() -> dict[str, Any]:
    """Build a compact but complete descriptor accepted by the v2 validator."""

    lock = json.loads(
        (DEPENDENCY_PROJECT / "Packages" / "packages-lock.json").read_text(
            encoding="utf-8"
        )
    )["dependencies"]
    lock["com.vrcfury.vrcfury"] = {
        "version": "file:com.vrcfury.vrcfury",
        "depth": 0,
        "source": "embedded",
        "dependencies": {},
    }
    packages = []
    for package_id, row in sorted(lock.items()):
        source = row["source"]
        version = (
            dependency_tool.DIRECT_PACKAGE_VERSIONS[package_id]
            if source == "embedded"
            else row["version"]
        )
        if source == "embedded":
            relative_root = f"Packages/{package_id}"
        elif source == "registry":
            relative_root = f"PackageCache/{package_id}@{version}"
        else:
            relative_root = f"EditorBuiltins/{package_id}"
        packages.append(
            {
                "id": package_id,
                "version": version,
                "lockVersion": row["version"],
                "source": source,
                "depth": row["depth"],
                "relativeRoot": relative_root,
                "packageJsonSha256": _fixed_digest(f"package-json:{package_id}"),
                "dependencies": [
                    {"id": dependency_id, "requestedVersion": requested_version}
                    for dependency_id, requested_version in sorted(
                        row["dependencies"].items()
                    )
                ],
                "tree": _dependency_tree_record(package_id),
            }
        )

    document: dict[str, Any] = {
        "schema": dependency_tool.DEPENDENCY_SET_SCHEMA,
        "unity": {
            "version": dependency_tool.EXPECTED_UNITY_VERSION,
            "revision": dependency_tool.EXPECTED_UNITY_REVISION,
        },
        "inputs": {
            name: {"sha256": _fixed_digest(f"input:{name}"), "byteCount": 1}
            for name in ("manifest", "packagesLock", "projectVersion")
        },
        "packages": packages,
        "scenarioRequirements": [
            {
                "scenarioId": scenario_id,
                "descriptorSha256": _fixed_digest(f"descriptor:{scenario_id}"),
                "requiredPrimitives": list(
                    dependency_tool.SCENARIO_DEFINITIONS[scenario_id]
                ),
                "requiredPackages": [
                    {
                        "id": package_id,
                        "version": dependency_tool.DIRECT_PACKAGE_VERSIONS[
                            package_id
                        ],
                    }
                    for package_id in dependency_tool.SCENARIO_PACKAGE_IDS[
                        scenario_id
                    ]
                ],
            }
            for scenario_id in dependency_tool.SCENARIO_ORDER
        ],
        "editorBuiltins": {
            "relativeRoot": "EditorBuiltins",
            "tree": _dependency_tree_record("editor-builtins"),
        },
        "setDigest": "",
    }
    projection = {key: value for key, value in document.items() if key != "setDigest"}
    digest = hashlib.sha256()
    digest.update(b"vrcforge.protected_runtime_dependency_set.set.v2\0")
    digest.update(dependency_tool.canonical_json_bytes(projection))
    document["setDigest"] = digest.hexdigest()
    assert dependency_tool.validate_dependency_set_document(document) == document
    return document


def _tree_archive_entries(root: Path, prefix: str) -> list[tuple[str, bytes]]:
    return [
        (
            f"{prefix}/{path.relative_to(root).as_posix()}",
            path.read_bytes(),
        )
        for path in sorted(root.rglob("*"))
        if path.is_file()
    ]


def _portable_archive_entries(
    *,
    package_trees: dict[str, Path],
    bridge_tree: Path,
    bridge_manifest: Path,
    unity_package: Path,
) -> list[tuple[str | zipfile.ZipInfo, bytes]]:
    entries: list[tuple[str | zipfile.ZipInfo, bytes]] = [
        ("VRCForge.exe", b"fixed-desktop-payload"),
        ("VERSION", VERSION.encode("ascii")),
    ]
    entries.extend(_tree_archive_entries(package_trees["backend"], "backend"))
    entries.extend(
        _tree_archive_entries(
            package_trees["packaged-tool"],
            "unity_plugin/Assets/VRCForge/Editor",
        )
    )
    entries.extend(
        _tree_archive_entries(
            package_trees["connector"],
            "unity_plugin/Packages/com.coplaydev.unity-mcp",
        )
    )
    entries.extend(_tree_archive_entries(bridge_tree, "bridge_target"))
    entries.extend(
        [
            ("bridge-target-manifest.json", bridge_manifest.read_bytes()),
            ("unity_plugin/VRCForge.unitypackage", unity_package.read_bytes()),
        ]
    )
    return entries


def _write_portable_archive(
    path: Path,
    *,
    package_trees: dict[str, Path],
    bridge_tree: Path,
    bridge_manifest: Path,
    unity_package: Path,
    omit: set[str] | None = None,
    replacements: dict[str, bytes] | None = None,
    additions: list[tuple[str | zipfile.ZipInfo, bytes]] | None = None,
) -> Path:
    omitted = omit or set()
    replacement_values = replacements or {}
    entries = _portable_archive_entries(
        package_trees=package_trees,
        bridge_tree=bridge_tree,
        bridge_manifest=bridge_manifest,
        unity_package=unity_package,
    )
    path.parent.mkdir(parents=True, exist_ok=True)
    with zipfile.ZipFile(path, "w", compression=zipfile.ZIP_DEFLATED) as archive:
        for name, content in [*entries, *(additions or [])]:
            archive_name = name.filename if isinstance(name, zipfile.ZipInfo) else name
            if archive_name in omitted:
                continue
            archive.writestr(name, replacement_values.get(archive_name, content))
    return path


def _refresh_release_manifest(fixture: SimpleNamespace) -> None:
    value = json.loads(fixture.strict_release_manifest.read_text(encoding="utf-8"))
    digests = {
        fixture.portable_archive.name: hashlib.sha256(
            fixture.portable_archive.read_bytes()
        ).hexdigest(),
        fixture.unity_package.name: hashlib.sha256(
            fixture.unity_package.read_bytes()
        ).hexdigest(),
    }
    for artifact in value["artifacts"]:
        if artifact["name"] in digests:
            artifact["sha256"] = digests[artifact["name"]]
    _write_json(fixture.strict_release_manifest, value)


def _rewrite_portable_archive(
    fixture: SimpleNamespace,
    *,
    omit: set[str] | None = None,
    replacements: dict[str, bytes] | None = None,
    additions: list[tuple[str | zipfile.ZipInfo, bytes]] | None = None,
) -> None:
    _write_portable_archive(
        fixture.portable_archive,
        package_trees=fixture.package_trees,
        bridge_tree=fixture.bridge_tree,
        bridge_manifest=fixture.bridge_manifest,
        unity_package=fixture.unity_package,
        omit=omit,
        replacements=replacements,
        additions=additions,
    )
    _refresh_release_manifest(fixture)


def _patch_first_zip_member(
    path: Path,
    *,
    flag_bits: int | None = None,
    compression: int | None = None,
) -> None:
    content = bytearray(path.read_bytes())
    local = content.find(b"PK\x03\x04")
    central = content.find(b"PK\x01\x02")
    assert local >= 0 and central >= 0
    if flag_bits is not None:
        content[local + 6 : local + 8] = flag_bits.to_bytes(2, "little")
        content[central + 8 : central + 10] = flag_bits.to_bytes(2, "little")
    if compression is not None:
        content[local + 8 : local + 10] = compression.to_bytes(2, "little")
        content[central + 10 : central + 12] = compression.to_bytes(2, "little")
    path.write_bytes(content)


def _materialize_fixture(
    root: Path,
    descriptor_path: Path,
    scenario_id: str,
) -> tuple[Path, Path]:
    files: dict[str, bytes] = {"payload.bin": f"fixture:{scenario_id}".encode("ascii")}
    if scenario_id == "model_part_composition":
        files["fixture-contract.json"] = (
            b'{"schema":"vrcforge.primitive_basis_model_part_fixture.v1"}\n'
        )
    for relative_path, content in files.items():
        _write_bytes(root / relative_path, content)

    inventory = [
        {
            "path": relative_path,
            "size": len(content),
            "sha256": hashlib.sha256(content).hexdigest(),
        }
        for relative_path, content in sorted(files.items())
    ]
    baseline = {
        "schema": "vrcforge.primitive_basis_baseline.v1",
        "scenarioId": scenario_id,
        "files": inventory,
    }
    baseline_path = _write_json(root / "baseline.json", baseline)
    descriptor = {
        "schema": "vrcforge.primitive_basis_fixture.v1",
        "scenarioId": scenario_id,
        "fixtureRoot": f"Assets/VRCForge/PrimitiveBasis/{scenario_id}",
        "baselineManifest": "baseline.json",
        "expectedBaselineDigest": _json_digest(baseline),
        "expectedTreeDigest": _json_digest(inventory),
        "requiredPrimitives": list(source_tool.SCENARIO_DEFINITIONS[scenario_id]),
    }
    _write_json(descriptor_path, descriptor)
    return root, baseline_path


def _fixture(tmp_path: Path) -> SimpleNamespace:
    roles_root = tmp_path / "roles"
    role_paths = {
        name: _write_bytes(
            roles_root / f"vrcforge_{name}.exe",
            f"fixed-role:{name}".encode("ascii"),
        )
        for name in (
            "authority_service",
            "driver",
            "desktop",
            "backend",
            "unity",
            "bridge_launcher",
        )
    }

    bridge_tree = tmp_path / "payload" / "bridge_target"
    bridge_listener = _write_bytes(
        bridge_tree / "vrcforge_bridge_target.exe",
        b"fixed-bridge-listener",
    )
    _write_bytes(bridge_tree / "_internal" / "runtime.dat", b"fixed-runtime")
    bridge_manifest = bridge_tree.parent / "bridge-target-manifest.json"
    source_tool.bridge_target_manifest.write_manifest(bridge_tree, bridge_manifest)

    fixture_parent = (
        tmp_path
        / "project"
        / "Assets"
        / "VRCForge"
        / "PrimitiveBasis"
    )
    descriptor_root = tmp_path / "project" / "VRCForgeFixture" / "descriptors"
    fixture_roots: dict[str, Path] = {}
    fixture_descriptors: dict[str, Path] = {}
    fixture_baselines: dict[str, Path] = {}
    for scenario_id in source_tool.SCENARIO_ORDER:
        descriptor = descriptor_root / f"{scenario_id}.json"
        root, baseline = _materialize_fixture(
            fixture_parent / scenario_id,
            descriptor,
            scenario_id,
        )
        fixture_roots[scenario_id] = root
        fixture_descriptors[scenario_id] = descriptor
        fixture_baselines[scenario_id] = baseline

    fixture_root = fixture_roots["model_part_composition"]
    runtime_contract = fixture_root / "fixture-contract.json"
    fixture_baseline = fixture_baselines["model_part_composition"]

    package_trees: dict[str, Path] = {}
    for name in ("backend", "packaged-tool", "connector", "server"):
        root = tmp_path / "package-trees" / name
        _write_bytes(root / "payload" / f"{name}.bin", f"tree:{name}".encode("ascii"))
        package_trees[name] = root

    unity_package = _write_bytes(
        tmp_path / "release" / "VRCForge.unitypackage",
        b"fixed-unity-package",
    )
    portable_archive = _write_portable_archive(
        tmp_path / "release" / "VRCForge_Windows_x64_1.4.0.zip",
        package_trees=package_trees,
        bridge_tree=bridge_tree,
        bridge_manifest=bridge_manifest,
        unity_package=unity_package,
    )
    strict_release_manifest = _write_json(
        tmp_path / "release" / "release-manifest.json",
        {
            "version": VERSION,
            "commit": SOURCE_COMMIT,
            "buildPolicy": {
                "mode": "strict-evidence",
                "releaseEligible": False,
                "evidenceEligible": True,
                "allowDirty": False,
                "allowUnpushed": False,
                "allowVersionMismatch": False,
            },
            "artifacts": [
                {
                    "name": portable_archive.name,
                    "sha256": hashlib.sha256(portable_archive.read_bytes()).hexdigest(),
                },
                {
                    "name": unity_package.name,
                    "sha256": hashlib.sha256(unity_package.read_bytes()).hexdigest(),
                },
            ],
        },
    )
    dependency_set_descriptor = _write_json(
        tmp_path / "release" / "dependency-set.json",
        _valid_dependency_descriptor(),
        canonical=True,
    )
    source_manifest = tmp_path / "evidence" / "protected-runtime-source.json"
    source_manifest.parent.mkdir()

    paths = source_tool.ProtectedRuntimeSourcePaths(
        authority_service=role_paths["authority_service"],
        driver=role_paths["driver"],
        desktop=role_paths["desktop"],
        backend=role_paths["backend"],
        unity=role_paths["unity"],
        bridge_launcher=role_paths["bridge_launcher"],
        bridge_listener=bridge_listener,
        runtime_contract=runtime_contract,
        fixture_baseline=fixture_baseline,
        bridge_tree=bridge_tree,
        bridge_manifest=bridge_manifest,
        strict_release_manifest=strict_release_manifest,
        portable_archive=portable_archive,
        unity_package=unity_package,
        backend_tree=package_trees["backend"],
        packaged_tool_tree=package_trees["packaged-tool"],
        connector_tree=package_trees["connector"],
        server_tree=package_trees["server"],
        dependency_set_descriptor=dependency_set_descriptor,
        component_feature_application_descriptor=(
            fixture_descriptors["component_feature_application"]
        ),
        parameter_optimization_descriptor=(
            fixture_descriptors["parameter_optimization"]
        ),
        cross_avatar_accessory_copy_descriptor=(
            fixture_descriptors["cross_avatar_accessory_copy"]
        ),
        model_part_composition_descriptor=(
            fixture_descriptors["model_part_composition"]
        ),
        component_feature_application_root=(
            fixture_roots["component_feature_application"]
        ),
        parameter_optimization_root=fixture_roots["parameter_optimization"],
        cross_avatar_accessory_copy_root=(
            fixture_roots["cross_avatar_accessory_copy"]
        ),
        model_part_composition_root=fixture_roots["model_part_composition"],
    )
    return SimpleNamespace(
        paths=paths,
        source_manifest=source_manifest,
        role_paths=role_paths,
        bridge_tree=bridge_tree,
        bridge_listener=bridge_listener,
        bridge_manifest=bridge_manifest,
        runtime_contract=runtime_contract,
        fixture_baseline=fixture_baseline,
        fixture_roots=fixture_roots,
        fixture_descriptors=fixture_descriptors,
        package_trees=package_trees,
        strict_release_manifest=strict_release_manifest,
        portable_archive=portable_archive,
        unity_package=unity_package,
        dependency_set_descriptor=dependency_set_descriptor,
    )


def _create(fixture: SimpleNamespace) -> dict[str, Any]:
    return source_tool.create_source_manifest(
        fixture.source_manifest,
        version=VERSION,
        source_commit=SOURCE_COMMIT,
        paths=fixture.paths,
    )


def _verify(fixture: SimpleNamespace) -> dict[str, Any]:
    return source_tool.verify_source_manifest(
        fixture.source_manifest,
        version=VERSION,
        source_commit=SOURCE_COMMIT,
        paths=fixture.paths,
    )


def _read_document(fixture: SimpleNamespace) -> dict[str, Any]:
    return json.loads(fixture.source_manifest.read_text(encoding="utf-8"))


def _write_document(fixture: SimpleNamespace, document: dict[str, Any]) -> None:
    fixture.source_manifest.write_bytes(source_tool.canonical_json_bytes(document) + b"\n")


def _mutate_document(
    fixture: SimpleNamespace,
    mutate: Callable[[dict[str, Any]], None],
) -> None:
    document = _read_document(fixture)
    mutate(document)
    _write_document(fixture, document)


def _expect_code(expected: str, operation: Callable[[], Any]) -> None:
    with pytest.raises(source_tool.ProtectedRuntimeSourceManifestError) as caught:
        operation()
    assert caught.value.code == expected
    assert str(caught.value) == expected


def _cli_arguments(fixture: SimpleNamespace, mode: str) -> list[str]:
    return [
        mode,
        "--source-manifest",
        str(fixture.source_manifest),
        "--version",
        VERSION,
        "--source-commit",
        SOURCE_COMMIT,
        "--authority-service",
        str(fixture.paths.authority_service),
        "--driver",
        str(fixture.paths.driver),
        "--desktop",
        str(fixture.paths.desktop),
        "--backend",
        str(fixture.paths.backend),
        "--unity",
        str(fixture.paths.unity),
        "--bridge-launcher",
        str(fixture.paths.bridge_launcher),
        "--bridge-listener",
        str(fixture.paths.bridge_listener),
        "--runtime-contract",
        str(fixture.paths.runtime_contract),
        "--fixture-baseline",
        str(fixture.paths.fixture_baseline),
        "--bridge-tree",
        str(fixture.paths.bridge_tree),
        "--bridge-manifest",
        str(fixture.paths.bridge_manifest),
        "--strict-release-manifest",
        str(fixture.paths.strict_release_manifest),
        "--portable-archive",
        str(fixture.paths.portable_archive),
        "--unity-package",
        str(fixture.paths.unity_package),
        "--backend-tree",
        str(fixture.paths.backend_tree),
        "--packaged-tool-tree",
        str(fixture.paths.packaged_tool_tree),
        "--connector-tree",
        str(fixture.paths.connector_tree),
        "--server-tree",
        str(fixture.paths.server_tree),
        "--dependency-set-descriptor",
        str(fixture.paths.dependency_set_descriptor),
        "--component-feature-application-descriptor",
        str(fixture.paths.component_feature_application_descriptor),
        "--parameter-optimization-descriptor",
        str(fixture.paths.parameter_optimization_descriptor),
        "--cross-avatar-accessory-copy-descriptor",
        str(fixture.paths.cross_avatar_accessory_copy_descriptor),
        "--model-part-composition-descriptor",
        str(fixture.paths.model_part_composition_descriptor),
        "--component-feature-application-root",
        str(fixture.paths.component_feature_application_root),
        "--parameter-optimization-root",
        str(fixture.paths.parameter_optimization_root),
        "--cross-avatar-accessory-copy-root",
        str(fixture.paths.cross_avatar_accessory_copy_root),
        "--model-part-composition-root",
        str(fixture.paths.model_part_composition_root),
    ]


def test_cli_create_and_verify_exact_canonical_contract(
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    fixture = _fixture(tmp_path)

    assert source_tool.main(_cli_arguments(fixture, "--create")) == 0
    create_output = capsys.readouterr()
    assert create_output.err == ""
    create_receipt = json.loads(create_output.out)
    assert create_receipt["ok"] is True
    assert create_receipt["mode"] == "create"
    assert str(tmp_path) not in create_output.out

    raw = fixture.source_manifest.read_bytes()
    document = json.loads(raw)
    assert raw == source_tool.canonical_json_bytes(document) + b"\n"
    assert set(document) == {
        "schema",
        "version",
        "sourceCommit",
        "scenarioId",
        "buildPolicy",
        "roles",
        "sources",
        "bridgeTargetRuntime",
        "releaseArtifacts",
        "packageTrees",
        "dependencySet",
        "fixtureSet",
        "modelFixture",
    }
    assert document["schema"] == "vrcforge.protected_runtime_source.v2"
    assert document["scenarioId"] == "model_part_composition"
    assert document["buildPolicy"] == {
        "mode": "strict-evidence",
        "releaseEligible": False,
        "evidenceEligible": True,
        "allowDirty": False,
        "allowUnpushed": False,
        "allowVersionMismatch": False,
    }
    assert [row["role"] for row in document["roles"]] == [
        "authority_service",
        "driver",
        "desktop",
        "backend",
        "unity",
        "bridge_launcher",
        "bridge_listener",
    ]
    assert [row["source"] for row in document["sources"]] == [
        "runtime_contract",
        "fixture_baseline",
    ]
    assert all(row["byteCount"] > 0 for row in document["roles"])
    assert all(row["byteCount"] > 0 for row in document["sources"])
    assert set(document["releaseArtifacts"]) == {
        "strictManifest",
        "portableArchive",
        "unityPackage",
    }
    assert set(document["packageTrees"]) == {
        "backend",
        "packagedTool",
        "connector",
        "server",
    }
    dependency_document = json.loads(
        fixture.dependency_set_descriptor.read_text(encoding="utf-8")
    )
    dependency_summary = {
        "descriptorSchema": dependency_tool.DEPENDENCY_SET_SCHEMA,
        "setDigest": dependency_document["setDigest"],
        "descriptorSha256": hashlib.sha256(
            fixture.dependency_set_descriptor.read_bytes()
        ).hexdigest(),
        "byteCount": len(fixture.dependency_set_descriptor.read_bytes()),
        "canonicalJson": True,
    }
    dependency_summary["bindingDigest"] = source_tool._contract_json_digest(
        dependency_summary
    )
    assert document["dependencySet"] == dependency_summary
    assert [
        row["scenarioId"] for row in document["fixtureSet"]["descriptors"]
    ] == list(source_tool.SCENARIO_ORDER)
    assert [
        row["scenarioId"] for row in document["fixtureSet"]["materializedRoots"]
    ] == list(source_tool.SCENARIO_ORDER)
    assert document["modelFixture"] == {
        "scenarioId": "model_part_composition",
        "descriptorDigest": document["fixtureSet"]["descriptors"][-1][
            "descriptorDigest"
        ],
        "fixtureDigest": document["fixtureSet"]["materializedRoots"][-1][
            "fixtureDigest"
        ],
    }
    runtime = document["bridgeTargetRuntime"]
    assert set(runtime) == {
        "schema",
        "runtimeRelativeRoot",
        "executableRelativePath",
        "executableSha256",
        "manifestRelativePath",
        "manifestSha256",
        "treeDigest",
        "directoryCount",
        "entryCount",
        "byteCount",
        "candidatePayloadIncluded",
        "strictSourceBound",
        "verifiedAfterBuild",
    }
    assert runtime["executableSha256"] == document["roles"][-1]["sha256"]

    assert source_tool.main(_cli_arguments(fixture, "--verify")) == 0
    verify_output = capsys.readouterr()
    assert verify_output.err == ""
    verify_receipt = json.loads(verify_output.out)
    assert verify_receipt["mode"] == "verify"
    assert verify_receipt["manifestSha256"] == create_receipt["manifestSha256"]
    assert fixture.source_manifest.read_bytes() == raw
    assert str(tmp_path) not in verify_output.out


@pytest.mark.parametrize("shape", ["role_order", "extra", "missing"])
def test_verify_rejects_role_order_and_root_shape_drift(
    tmp_path: Path,
    shape: str,
) -> None:
    fixture = _fixture(tmp_path)
    _create(fixture)

    def mutate(document: dict[str, Any]) -> None:
        if shape == "role_order":
            document["roles"][0], document["roles"][1] = (
                document["roles"][1],
                document["roles"][0],
            )
        elif shape == "extra":
            document["extra"] = False
        else:
            document.pop("sources")

    _mutate_document(fixture, mutate)
    _expect_code("protected_runtime_source_manifest_invalid", lambda: _verify(fixture))


@pytest.mark.parametrize("variant", ["pretty", "duplicate_key"])
def test_verify_rejects_noncanonical_and_duplicate_json(
    tmp_path: Path,
    variant: str,
) -> None:
    fixture = _fixture(tmp_path)
    _create(fixture)
    document = _read_document(fixture)

    if variant == "pretty":
        fixture.source_manifest.write_text(
            json.dumps(document, ensure_ascii=False, indent=2) + "\n",
            encoding="utf-8",
        )
        expected = "protected_runtime_source_manifest_noncanonical"
    else:
        raw = fixture.source_manifest.read_bytes()
        fixture.source_manifest.write_bytes(
            b'{"schema":"vrcforge.protected_runtime_source.v2",' + raw[1:]
        )
        expected = "protected_runtime_source_manifest_invalid"

    _expect_code(expected, lambda: _verify(fixture))


def test_production_verify_fails_closed_for_legacy_v1_schema(tmp_path: Path) -> None:
    fixture = _fixture(tmp_path)
    _create(fixture)
    _mutate_document(
        fixture,
        lambda document: document.__setitem__(
            "schema", "vrcforge.protected_runtime_source.v1"
        ),
    )

    _expect_code("protected_runtime_source_manifest_invalid", lambda: _verify(fixture))


def test_verify_rejects_role_digest_drift(tmp_path: Path) -> None:
    fixture = _fixture(tmp_path)
    _create(fixture)
    fixture.role_paths["driver"].write_bytes(b"changed-driver")

    _expect_code("protected_runtime_source_manifest_mismatch", lambda: _verify(fixture))


def test_verify_rejects_role_alias(tmp_path: Path) -> None:
    fixture = _fixture(tmp_path)
    _create(fixture)
    _mutate_document(
        fixture,
        lambda document: document["roles"][0].__setitem__(
            "role", "authority-service"
        ),
    )

    _expect_code("protected_runtime_source_manifest_invalid", lambda: _verify(fixture))


@pytest.mark.parametrize(
    "forbidden",
    ["generation", "authorityGenerationSha256", "finalCommit", "scm"],
)
def test_verify_rejects_forbidden_self_reference_fields(
    tmp_path: Path,
    forbidden: str,
) -> None:
    fixture = _fixture(tmp_path)
    _create(fixture)
    _mutate_document(
        fixture,
        lambda document: document.__setitem__(forbidden, "2" * 64),
    )

    _expect_code(
        "protected_runtime_source_self_reference_forbidden",
        lambda: _verify(fixture),
    )


def test_create_rejects_manifest_inside_bridge_tree(tmp_path: Path) -> None:
    fixture = _fixture(tmp_path)
    fixture.source_manifest = fixture.bridge_tree / "source-manifest.json"

    _expect_code(
        "protected_runtime_source_self_reference_forbidden",
        lambda: _create(fixture),
    )
    assert not fixture.source_manifest.exists()


def test_verify_rejects_bridge_runtime_metadata_mismatch(tmp_path: Path) -> None:
    fixture = _fixture(tmp_path)
    _create(fixture)
    _mutate_document(
        fixture,
        lambda document: document["bridgeTargetRuntime"].__setitem__(
            "manifestSha256", "2" * 64
        ),
    )

    _expect_code("protected_runtime_source_manifest_mismatch", lambda: _verify(fixture))


def test_verify_rejects_actual_bridge_tree_drift(tmp_path: Path) -> None:
    fixture = _fixture(tmp_path)
    _create(fixture)
    (fixture.bridge_tree / "_internal" / "runtime.dat").write_bytes(b"changed-runtime")

    _expect_code("protected_runtime_source_bridge_invalid", lambda: _verify(fixture))


def test_verify_rejects_actual_bridge_executable_drift(tmp_path: Path) -> None:
    fixture = _fixture(tmp_path)
    _create(fixture)
    fixture.bridge_listener.write_bytes(b"changed-listener")

    _expect_code("protected_runtime_source_bridge_invalid", lambda: _verify(fixture))


@pytest.mark.parametrize(
    ("mutation", "expected_code"),
    [
        ("add", "protected_runtime_source_manifest_mismatch"),
        ("remove", "protected_runtime_source_input_invalid"),
        ("rename", "protected_runtime_source_manifest_mismatch"),
        ("same_length", "protected_runtime_source_manifest_mismatch"),
    ],
)
def test_verify_rejects_package_tree_inventory_or_content_drift(
    tmp_path: Path,
    mutation: str,
    expected_code: str,
) -> None:
    fixture = _fixture(tmp_path)
    _create(fixture)
    tree = fixture.package_trees["backend"]
    original = tree / "payload" / "backend.bin"

    if mutation == "add":
        _write_bytes(tree / "payload" / "added.bin", b"added")
    elif mutation == "remove":
        original.unlink()
    elif mutation == "rename":
        original.rename(tree / "payload" / "renamed.bin")
    else:
        content = original.read_bytes()
        original.write_bytes(bytes(byte ^ 0x01 for byte in content))

    _expect_code(expected_code, lambda: _verify(fixture))


def test_create_accepts_exact_portable_archive_bindings(tmp_path: Path) -> None:
    fixture = _fixture(tmp_path)

    receipt = _create(fixture)

    assert receipt["ok"] is True
    assert receipt["packageTreeCount"] == 4


@pytest.mark.skipif(os.name != "nt", reason="Windows sharing contract")
def test_held_source_denies_concurrent_write_and_delete(tmp_path: Path) -> None:
    source = _write_bytes(tmp_path / "held-source.bin", b"stable-source")
    held = source_tool._HeldFile.open(source, 1024)
    try:
        with pytest.raises(OSError):
            source.write_bytes(b"drifted-source")
        with pytest.raises(OSError):
            source.unlink()
        content, digest = held.read()
        assert content == b"stable-source"
        assert digest == hashlib.sha256(content).digest()
    finally:
        held.close()

    source.write_bytes(b"released-source")
    assert source.read_bytes() == b"released-source"


def test_create_rejects_duplicate_portable_archive_member(tmp_path: Path) -> None:
    fixture = _fixture(tmp_path)
    path = "backend/payload/backend.bin"
    with pytest.warns(UserWarning, match="Duplicate name"):
        _rewrite_portable_archive(
            fixture,
            additions=[
                (
                    path,
                    (
                        fixture.package_trees["backend"] / "payload" / "backend.bin"
                    ).read_bytes(),
                )
            ],
        )

    _expect_code("protected_runtime_source_input_invalid", lambda: _create(fixture))


def test_create_rejects_casefold_portable_archive_alias(tmp_path: Path) -> None:
    fixture = _fixture(tmp_path)
    _rewrite_portable_archive(
        fixture,
        additions=[("BACKEND/PAYLOAD/BACKEND.BIN", b"case-alias")],
    )

    _expect_code("protected_runtime_source_input_invalid", lambda: _create(fixture))


@pytest.mark.parametrize(
    "unsafe_name",
    [
        "../escape.bin",
        "/absolute.bin",
        "C:/drive.bin",
        "//server/share/file.bin",
        "\\\\server\\share\\file.bin",
    ],
)
def test_create_rejects_unsafe_portable_archive_paths(
    tmp_path: Path,
    unsafe_name: str,
) -> None:
    fixture = _fixture(tmp_path)
    _rewrite_portable_archive(fixture, additions=[(unsafe_name, b"unsafe")])

    _expect_code("protected_runtime_source_input_invalid", lambda: _create(fixture))


def test_create_rejects_portable_archive_file_directory_conflict(
    tmp_path: Path,
) -> None:
    fixture = _fixture(tmp_path)
    _rewrite_portable_archive(
        fixture,
        additions=[("conflict", b"file"), ("conflict/child.bin", b"child")],
    )

    _expect_code("protected_runtime_source_input_invalid", lambda: _create(fixture))


@pytest.mark.parametrize("entry_kind", ["symlink", "fifo", "reparse"])
def test_create_rejects_link_like_or_unsupported_archive_entries(
    tmp_path: Path,
    entry_kind: str,
) -> None:
    fixture = _fixture(tmp_path)
    info = zipfile.ZipInfo(f"unsupported/{entry_kind}")
    if entry_kind == "reparse":
        info.create_system = 0
        info.external_attr = source_tool._FILE_ATTRIBUTE_REPARSE_POINT
    else:
        info.create_system = 3
        file_type = stat.S_IFLNK if entry_kind == "symlink" else stat.S_IFIFO
        info.external_attr = (file_type | 0o600) << 16
    _rewrite_portable_archive(fixture, additions=[(info, b"target")])

    _expect_code("protected_runtime_source_input_invalid", lambda: _create(fixture))


@pytest.mark.parametrize(
    ("omit", "replacements", "additions"),
    [
        (set(), {}, [("backend/payload/extra.bin", b"extra")]),
        ({"backend/payload/backend.bin"}, {}, []),
        (set(), {"backend/payload/backend.bin": b"changed-backend"}, []),
        (
            set(),
            {
                "unity_plugin/Assets/VRCForge/Editor/payload/packaged-tool.bin": (
                    b"changed-tool"
                )
            },
            [],
        ),
        (
            set(),
            {
                "unity_plugin/Packages/com.coplaydev.unity-mcp/payload/connector.bin": (
                    b"changed-connector"
                )
            },
            [],
        ),
        (set(), {"bridge_target/_internal/runtime.dat": b"changed-bridge"}, []),
    ],
)
def test_create_rejects_portable_bound_tree_inventory_or_content_drift(
    tmp_path: Path,
    omit: set[str],
    replacements: dict[str, bytes],
    additions: list[tuple[str | zipfile.ZipInfo, bytes]],
) -> None:
    fixture = _fixture(tmp_path)
    _rewrite_portable_archive(
        fixture,
        omit=omit,
        replacements=replacements,
        additions=additions,
    )

    _expect_code("protected_runtime_source_input_invalid", lambda: _create(fixture))


@pytest.mark.parametrize(
    ("path", "content"),
    [
        ("unity_plugin/VRCForge.unitypackage", b"changed-unity-package"),
        ("bridge-target-manifest.json", b"changed-bridge-manifest"),
    ],
)
def test_create_rejects_embedded_held_file_byte_drift(
    tmp_path: Path,
    path: str,
    content: bytes,
) -> None:
    fixture = _fixture(tmp_path)
    _rewrite_portable_archive(fixture, replacements={path: content})

    _expect_code("protected_runtime_source_input_invalid", lambda: _create(fixture))


@pytest.mark.parametrize(
    ("constant_name", "limit"),
    [
        ("MAX_PORTABLE_ARCHIVE_ENTRIES", 1),
        ("MAX_PORTABLE_ARCHIVE_ENTRY_BYTES", 1),
        ("MAX_PORTABLE_ARCHIVE_EXPANDED_BYTES", 1),
    ],
)
def test_create_rejects_portable_archive_count_and_size_bounds(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    constant_name: str,
    limit: int,
) -> None:
    fixture = _fixture(tmp_path)
    monkeypatch.setattr(source_tool, constant_name, limit)

    _expect_code("protected_runtime_source_input_invalid", lambda: _create(fixture))


def test_create_rejects_portable_archive_compression_ratio_bound(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    fixture = _fixture(tmp_path)
    _rewrite_portable_archive(
        fixture,
        additions=[("documentation/repeated.bin", b"0" * 4096)],
    )
    monkeypatch.setattr(source_tool, "MAX_PORTABLE_ARCHIVE_COMPRESSION_RATIO", 2)

    _expect_code("protected_runtime_source_input_invalid", lambda: _create(fixture))


@pytest.mark.parametrize("mutation", ["encrypted", "compression"])
def test_create_rejects_encrypted_or_unsupported_compression(
    tmp_path: Path,
    mutation: str,
) -> None:
    fixture = _fixture(tmp_path)
    if mutation == "encrypted":
        _patch_first_zip_member(fixture.portable_archive, flag_bits=1)
    else:
        _patch_first_zip_member(fixture.portable_archive, compression=99)
    _refresh_release_manifest(fixture)

    _expect_code("protected_runtime_source_input_invalid", lambda: _create(fixture))


def test_create_rejects_duplicate_tree_identity(tmp_path: Path) -> None:
    fixture = _fixture(tmp_path)
    fixture.paths = dataclasses.replace(
        fixture.paths,
        server_tree=fixture.paths.connector_tree,
    )

    _expect_code(
        "protected_runtime_source_duplicate_identity",
        lambda: _create(fixture),
    )


def test_create_rejects_hardlinked_static_input_when_supported(tmp_path: Path) -> None:
    fixture = _fixture(tmp_path)
    alias = fixture.strict_release_manifest.with_name("release-manifest-alias.json")
    try:
        os.link(fixture.strict_release_manifest, alias)
    except OSError:
        pytest.skip("local hardlink creation is unavailable")

    _expect_code("protected_runtime_source_input_invalid", lambda: _create(fixture))


def test_create_rejects_hardlinked_tree_leaf_when_supported(tmp_path: Path) -> None:
    fixture = _fixture(tmp_path)
    source = fixture.package_trees["backend"] / "payload" / "backend.bin"
    alias = source.with_name("backend-alias.bin")
    try:
        os.link(source, alias)
    except OSError:
        pytest.skip("local hardlink creation is unavailable")

    _expect_code("protected_runtime_source_input_invalid", lambda: _create(fixture))


def test_create_rejects_casefold_tree_entry_alias_when_supported(
    tmp_path: Path,
) -> None:
    fixture = _fixture(tmp_path)
    tree = fixture.package_trees["backend"]
    first = _write_bytes(tree / "Alias.bin", b"first")
    second = _write_bytes(tree / "alias.bin", b"second")
    names = {entry.name for entry in os.scandir(tree)}
    if first.name not in names or second.name not in names or len(names) < 3:
        pytest.skip("filesystem does not support distinct casefold aliases")

    _expect_code("protected_runtime_source_input_invalid", lambda: _create(fixture))


def test_create_rejects_casefold_aliases_in_release_manifest(tmp_path: Path) -> None:
    fixture = _fixture(tmp_path)
    release = json.loads(fixture.strict_release_manifest.read_text(encoding="utf-8"))
    release["artifacts"].append(
        {
            "name": fixture.portable_archive.name.swapcase(),
            "sha256": hashlib.sha256(fixture.portable_archive.read_bytes()).hexdigest(),
        }
    )
    _write_json(fixture.strict_release_manifest, release)

    _expect_code("protected_runtime_source_input_invalid", lambda: _create(fixture))


def test_create_rejects_noncanonical_dependency_descriptor(tmp_path: Path) -> None:
    fixture = _fixture(tmp_path)
    dependency = json.loads(
        fixture.dependency_set_descriptor.read_text(encoding="utf-8")
    )
    _write_json(fixture.dependency_set_descriptor, dependency, canonical=False)

    _expect_code("protected_runtime_source_input_invalid", lambda: _create(fixture))


@pytest.mark.parametrize("shape", ["empty", "legacy_schema", "set_digest"])
def test_create_rejects_invalid_dependency_descriptor_contract(
    tmp_path: Path,
    shape: str,
) -> None:
    fixture = _fixture(tmp_path)
    dependency = json.loads(
        fixture.dependency_set_descriptor.read_text(encoding="utf-8")
    )
    if shape == "empty":
        dependency = {}
    elif shape == "legacy_schema":
        dependency["schema"] = "vrcforge.protected_runtime_dependency_set.v1"
    else:
        dependency["setDigest"] = "2" * 64
    _write_json(fixture.dependency_set_descriptor, dependency, canonical=True)

    _expect_code("protected_runtime_source_input_invalid", lambda: _create(fixture))


@pytest.mark.parametrize(
    ("field", "value", "expected"),
    [
        (
            "descriptorSchema",
            "vrcforge.protected_runtime_dependency_set.v1",
            "protected_runtime_source_manifest_invalid",
        ),
        ("setDigest", "2" * 64, "protected_runtime_source_manifest_invalid"),
        (
            "descriptorSha256",
            "2" * 64,
            "protected_runtime_source_manifest_invalid",
        ),
        ("bindingDigest", "2" * 64, "protected_runtime_source_manifest_invalid"),
    ],
)
def test_verify_rejects_dependency_summary_drift(
    tmp_path: Path,
    field: str,
    value: str,
    expected: str,
) -> None:
    fixture = _fixture(tmp_path)
    _create(fixture)
    _mutate_document(
        fixture,
        lambda document: document["dependencySet"].__setitem__(field, value),
    )

    _expect_code(expected, lambda: _verify(fixture))


def test_create_rejects_release_manifest_artifact_digest_claim(
    tmp_path: Path,
) -> None:
    fixture = _fixture(tmp_path)
    release = json.loads(fixture.strict_release_manifest.read_text(encoding="utf-8"))
    release["artifacts"][0]["sha256"] = "2" * 64
    _write_json(fixture.strict_release_manifest, release)

    _expect_code("protected_runtime_source_input_invalid", lambda: _create(fixture))


def test_create_rejects_input_larger_than_fixed_bound(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    fixture = _fixture(tmp_path)
    monkeypatch.setattr(source_tool, "MAX_SOURCE_BYTES", 8)

    _expect_code("protected_runtime_source_input_invalid", lambda: _create(fixture))


def test_create_rejects_existing_target_without_replacement(tmp_path: Path) -> None:
    fixture = _fixture(tmp_path)
    original_receipt = _create(fixture)
    original = fixture.source_manifest.read_bytes()

    _expect_code("protected_runtime_source_target_exists", lambda: _create(fixture))
    assert fixture.source_manifest.read_bytes() == original
    assert original_receipt["mode"] == "create"


def test_create_removes_its_partial_target_after_write_failure(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    fixture = _fixture(tmp_path)

    def fail_write(_descriptor: int, _content: bytes) -> int:
        raise OSError("synthetic write failure")

    monkeypatch.setattr(source_tool.os, "write", fail_write)
    _expect_code("protected_runtime_source_write_failed", lambda: _create(fixture))
    assert not fixture.source_manifest.exists()


def test_create_rejects_duplicate_role_identity(tmp_path: Path) -> None:
    fixture = _fixture(tmp_path)
    fixture.paths = dataclasses.replace(
        fixture.paths,
        desktop=fixture.paths.driver,
    )

    _expect_code(
        "protected_runtime_source_duplicate_identity",
        lambda: _create(fixture),
    )


def test_create_rejects_real_symlink_when_supported(tmp_path: Path) -> None:
    fixture = _fixture(tmp_path)
    symlink = tmp_path / "roles" / "linked-driver.exe"
    target = _write_bytes(tmp_path / "elsewhere" / "driver.exe", b"linked-driver")
    try:
        symlink.symlink_to(target)
    except OSError:
        pytest.skip("local symlink creation is unavailable")
    fixture.paths = dataclasses.replace(fixture.paths, driver=symlink)

    _expect_code("protected_runtime_source_input_invalid", lambda: _create(fixture))


def test_cli_rejects_dynamic_role_flag_and_redacts_paths(
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    fixture = _fixture(tmp_path)
    arguments = _cli_arguments(fixture, "--create") + [
        "--role",
        "unexpected-role.exe",
    ]

    assert source_tool.main(arguments) == 1
    output = capsys.readouterr()
    assert output.out == ""
    assert json.loads(output.err) == {
        "code": "protected_runtime_source_cli_invalid",
        "ok": False,
    }
    assert str(tmp_path) not in output.err
    assert "unexpected-role.exe" not in output.err


@pytest.mark.parametrize(
    "flag",
    [
        "--manifest-sha256",
        "--portable-sha256",
        "--backend-tree-digest",
        "--fixture-set-digest",
    ],
)
def test_cli_rejects_all_caller_provided_digest_flags(
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
    flag: str,
) -> None:
    fixture = _fixture(tmp_path)

    assert source_tool.main(_cli_arguments(fixture, "--create") + [flag, "2" * 64]) == 1
    output = capsys.readouterr()
    assert output.out == ""
    assert json.loads(output.err) == {
        "code": "protected_runtime_source_cli_invalid",
        "ok": False,
    }


def test_cli_missing_arguments_emit_only_fixed_error_code(
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    secret_path = tmp_path / "sensitive-source-manifest.json"

    assert (
        source_tool.main(
            ["--verify", "--source-manifest", str(secret_path)]
        )
        == 1
    )
    output = capsys.readouterr()
    assert output.out == ""
    assert json.loads(output.err) == {
        "code": "protected_runtime_source_cli_invalid",
        "ok": False,
    }
    assert str(tmp_path) not in output.err
    assert secret_path.name not in output.err


def test_source_manifest_has_no_generation_final_commit_or_scm_fields(
    tmp_path: Path,
) -> None:
    fixture = _fixture(tmp_path)
    _create(fixture)
    raw = fixture.source_manifest.read_text(encoding="utf-8").casefold()

    assert "generation" not in raw
    assert "finalcommit" not in raw
    assert "scm" not in raw
    assert os.linesep not in raw[:-1] or os.linesep == "\n"
