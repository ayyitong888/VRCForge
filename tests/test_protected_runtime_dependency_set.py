from __future__ import annotations

import copy
import json
import os
import shutil
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable

import pytest


ROOT = Path(__file__).resolve().parents[1]
PACKAGING = ROOT / "packaging"
if str(PACKAGING) not in sys.path:
    sys.path.insert(0, str(PACKAGING))

import protected_runtime_dependency_set as dependency_tool


BASE_PROJECT = (
    ROOT / "tests" / "fixtures" / "primitive_basis" / "projects"
    / "model_part_composition"
)
BASE_DESCRIPTORS = ROOT / "tests" / "fixtures" / "primitive_basis"


@dataclass
class DependencyFixture:
    paths: dependency_tool.DependencySetPaths
    lock: dict[str, Any]
    manifest: dict[str, Any]
    package_json: dict[str, Path]
    package_payload: dict[str, Path]


def _write_json(path: Path, value: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(dependency_tool.canonical_json_bytes(value) + b"\n")


def _fixture(tmp_path: Path) -> DependencyFixture:
    project = tmp_path / "project"
    descriptors = tmp_path / "descriptors"
    builtins = tmp_path / "editor-builtins"
    package_parent = tmp_path / "package-roots"
    (project / "Packages").mkdir(parents=True)
    (project / "ProjectSettings").mkdir(parents=True)
    descriptors.mkdir()
    builtins.mkdir()
    package_parent.mkdir()

    manifest = json.loads(
        (BASE_PROJECT / "Packages" / "manifest.json").read_text(encoding="utf-8")
    )
    for package_id in dependency_tool.DIRECT_PACKAGE_VERSIONS:
        manifest["dependencies"][package_id] = f"file:{package_id}"
    _write_json(project / "Packages" / "manifest.json", manifest)

    lock = json.loads(
        (BASE_PROJECT / "Packages" / "packages-lock.json").read_text(
            encoding="utf-8"
        )
    )
    lock["dependencies"][dependency_tool._FEATURE_PACKAGE_ID] = copy.deepcopy(
        dependency_tool._FEATURE_LOCK_ROW
    )
    _write_json(project / "Packages" / "packages-lock.json", lock)
    shutil.copyfile(
        BASE_PROJECT / "ProjectSettings" / "ProjectVersion.txt",
        project / "ProjectSettings" / "ProjectVersion.txt",
    )
    for scenario_id in dependency_tool.SCENARIO_ORDER:
        shutil.copyfile(
            BASE_DESCRIPTORS / f"{scenario_id}.json",
            descriptors / f"{scenario_id}.json",
        )

    roots: dict[str, Path] = {}
    package_json: dict[str, Path] = {}
    package_payload: dict[str, Path] = {}
    for package_id, row in lock["dependencies"].items():
        root = (
            builtins / package_id
            if row["source"] == "builtin"
            else package_parent / package_id
        )
        runtime = root / "Runtime"
        runtime.mkdir(parents=True)
        version = (
            dependency_tool.DIRECT_PACKAGE_VERSIONS[package_id]
            if row["source"] == "embedded"
            else row["version"]
        )
        package_document = {
            "name": package_id,
            "version": version,
            "dependencies": row["dependencies"],
        }
        package_path = root / "package.json"
        payload_path = runtime / "source.txt"
        _write_json(package_path, package_document)
        payload_path.write_text(f"fixed:{package_id}\n", encoding="utf-8")
        package_json[package_id] = package_path
        package_payload[package_id] = payload_path
        if row["source"] != "builtin":
            roots[package_id] = root

    return DependencyFixture(
        paths=dependency_tool.DependencySetPaths(
            project_root=project,
            descriptors_root=descriptors,
            editor_builtins_root=builtins,
            package_roots=roots,
            output=tmp_path / "dependency-set.json",
        ),
        lock=lock,
        manifest=manifest,
        package_json=package_json,
        package_payload=package_payload,
    )


def _rewrite_lock(fixture: DependencyFixture, value: dict[str, Any]) -> None:
    fixture.lock = value
    _write_json(fixture.paths.project_root / "Packages" / "packages-lock.json", value)


def _rewrite_manifest(fixture: DependencyFixture, value: dict[str, Any]) -> None:
    fixture.manifest = value
    _write_json(fixture.paths.project_root / "Packages" / "manifest.json", value)


def _recanonicalize_descriptor(
    fixture: DependencyFixture,
    mutate: Callable[[dict[str, Any]], None],
) -> None:
    document = json.loads(fixture.paths.output.read_text(encoding="utf-8"))
    mutate(document)
    document["setDigest"] = dependency_tool._set_digest(document)
    _write_json(fixture.paths.output, document)


def test_create_and_verify_exact_v2_canonical_contract(tmp_path: Path) -> None:
    fixture = _fixture(tmp_path)

    created = dependency_tool.create_dependency_set(fixture.paths)
    raw = fixture.paths.output.read_bytes()
    document = json.loads(raw)

    assert created == {
        "ok": True,
        "mode": "create",
        "schema": "vrcforge.protected_runtime_dependency_set_receipt.v2",
        "descriptorSchema": "vrcforge.protected_runtime_dependency_set.v2",
        "descriptorSha256": created["descriptorSha256"],
        "setDigest": document["setDigest"],
        "packageCount": len(dependency_tool.EXPECTED_PACKAGE_IDS),
        "scenarioCount": 4,
    }
    assert raw == dependency_tool.canonical_json_bytes(document) + b"\n"
    assert not raw.startswith(b"\xef\xbb\xbf")
    assert set(document) == {
        "schema",
        "unity",
        "inputs",
        "packages",
        "scenarioRequirements",
        "editorBuiltins",
        "setDigest",
    }
    assert document["unity"] == {
        "version": "2022.3.22f1",
        "revision": "887be4894c44",
    }
    assert set(document["inputs"]) == {
        "manifest",
        "packagesLock",
        "projectVersion",
    }
    assert [row["id"] for row in document["packages"]] == sorted(
        dependency_tool.EXPECTED_PACKAGE_IDS
    )
    assert all(row["tree"]["entryCount"] >= 2 for row in document["packages"])
    assert all(row["packageJsonSha256"] for row in document["packages"])
    assert [row["scenarioId"] for row in document["scenarioRequirements"]] == list(
        dependency_tool.SCENARIO_ORDER
    )
    assert document["editorBuiltins"]["relativeRoot"] == "EditorBuiltins"
    assert document["setDigest"] == dependency_tool._set_digest(document)

    verified = dependency_tool.verify_dependency_set(fixture.paths)
    assert verified["mode"] == "verify"
    assert verified["descriptorSha256"] == created["descriptorSha256"]
    assert fixture.paths.output.read_bytes() == raw


def test_checked_in_model_only_inputs_fail_closed_until_union_is_materialized(
    tmp_path: Path,
) -> None:
    fixture = _fixture(tmp_path)
    manifest = json.loads(
        (BASE_PROJECT / "Packages" / "manifest.json").read_text(encoding="utf-8")
    )
    lock = json.loads(
        (BASE_PROJECT / "Packages" / "packages-lock.json").read_text(
            encoding="utf-8"
        )
    )
    _rewrite_manifest(fixture, manifest)
    _rewrite_lock(fixture, lock)

    with pytest.raises(dependency_tool.ProtectedRuntimeDependencySetError) as failure:
        dependency_tool.create_dependency_set(fixture.paths)
    assert failure.value.code == "protected_runtime_dependency_input_invalid"
    assert not fixture.paths.output.exists()


@pytest.mark.parametrize("shape", ["missing", "extra", "wrong_edge", "wrong_depth"])
def test_create_rejects_lock_closure_drift(tmp_path: Path, shape: str) -> None:
    fixture = _fixture(tmp_path)
    lock = copy.deepcopy(fixture.lock)
    if shape == "missing":
        del lock["dependencies"]["com.unity.burst"]
    elif shape == "extra":
        lock["dependencies"]["example.unknown.package"] = {
            "version": "1.0.0",
            "depth": 0,
            "source": "registry",
            "dependencies": {},
            "url": "https://packages.unity.com",
        }
        manifest = copy.deepcopy(fixture.manifest)
        manifest["dependencies"]["example.unknown.package"] = "1.0.0"
        _rewrite_manifest(fixture, manifest)
    elif shape == "wrong_edge":
        lock["dependencies"]["com.unity.burst"]["dependencies"][
            "com.unity.modules.physics"
        ] = "1.0.0"
    else:
        lock["dependencies"]["com.unity.burst"]["depth"] = 2
    _rewrite_lock(fixture, lock)

    with pytest.raises(dependency_tool.ProtectedRuntimeDependencySetError) as failure:
        dependency_tool.create_dependency_set(fixture.paths)
    assert failure.value.code == "protected_runtime_dependency_input_invalid"
    assert not fixture.paths.output.exists()


def test_create_rejects_duplicate_package_json_key(tmp_path: Path) -> None:
    fixture = _fixture(tmp_path)
    fixture.package_json["com.vrcfury.vrcfury"].write_bytes(
        b'{"name":"com.vrcfury.vrcfury","name":"com.vrcfury.vrcfury",'
        b'"version":"1.1334.0","dependencies":{}}\n'
    )

    with pytest.raises(dependency_tool.ProtectedRuntimeDependencySetError) as failure:
        dependency_tool.create_dependency_set(fixture.paths)
    assert failure.value.code == "protected_runtime_dependency_input_invalid"


def test_create_rejects_package_json_dependency_edge_drift(tmp_path: Path) -> None:
    fixture = _fixture(tmp_path)
    package_path = fixture.package_json["com.vrchat.avatars"]
    document = json.loads(package_path.read_text(encoding="utf-8"))
    document["dependencies"]["com.unity.modules.physics"] = "1.0.0"
    _write_json(package_path, document)

    with pytest.raises(dependency_tool.ProtectedRuntimeDependencySetError) as failure:
        dependency_tool.create_dependency_set(fixture.paths)
    assert failure.value.code == "protected_runtime_dependency_input_invalid"


@pytest.mark.parametrize("shape", ["missing", "extra"])
def test_create_rejects_missing_or_extra_package_root(
    tmp_path: Path,
    shape: str,
) -> None:
    fixture = _fixture(tmp_path)
    roots = dict(fixture.paths.package_roots)
    if shape == "missing":
        del roots["com.vrcfury.vrcfury"]
    else:
        extra = tmp_path / "extra-package"
        extra.mkdir()
        roots["example.unknown.package"] = extra
    fixture.paths = dependency_tool.DependencySetPaths(
        project_root=fixture.paths.project_root,
        descriptors_root=fixture.paths.descriptors_root,
        editor_builtins_root=fixture.paths.editor_builtins_root,
        package_roots=roots,
        output=fixture.paths.output,
    )

    with pytest.raises(dependency_tool.ProtectedRuntimeDependencySetError) as failure:
        dependency_tool.create_dependency_set(fixture.paths)
    assert failure.value.code == "protected_runtime_dependency_input_invalid"


@pytest.mark.parametrize("relative_root", ["C:/unsafe", "../unsafe", "Packages\\unsafe"])
def test_verify_rejects_absolute_traversal_and_noncanonical_roots(
    tmp_path: Path,
    relative_root: str,
) -> None:
    fixture = _fixture(tmp_path)
    dependency_tool.create_dependency_set(fixture.paths)
    _recanonicalize_descriptor(
        fixture,
        lambda document: document["packages"][0].update(
            {"relativeRoot": relative_root}
        ),
    )

    with pytest.raises(dependency_tool.ProtectedRuntimeDependencySetError) as failure:
        dependency_tool.verify_dependency_set(fixture.paths)
    assert failure.value.code == "protected_runtime_dependency_descriptor_invalid"


def test_create_rejects_hardlinked_package_leaf(tmp_path: Path) -> None:
    fixture = _fixture(tmp_path)
    source = fixture.package_payload["com.vrcfury.vrcfury"]
    alias = source.with_name("alias.txt")
    try:
        os.link(source, alias)
    except OSError as exc:
        pytest.skip(f"hard links unavailable: {exc}")

    with pytest.raises(dependency_tool.ProtectedRuntimeDependencySetError) as failure:
        dependency_tool.create_dependency_set(fixture.paths)
    assert failure.value.code == "protected_runtime_dependency_input_invalid"


def test_create_rejects_reparse_or_symlink_leaf(tmp_path: Path) -> None:
    fixture = _fixture(tmp_path)
    source = fixture.package_payload["com.vrcfury.vrcfury"]
    link = source.with_name("linked.txt")
    try:
        link.symlink_to(source)
    except OSError as exc:
        pytest.skip(f"symbolic links unavailable: {exc}")

    with pytest.raises(dependency_tool.ProtectedRuntimeDependencySetError) as failure:
        dependency_tool.create_dependency_set(fixture.paths)
    assert failure.value.code == "protected_runtime_dependency_input_invalid"


def test_create_rejects_casefold_collision_when_supported(tmp_path: Path) -> None:
    fixture = _fixture(tmp_path)
    runtime = fixture.package_payload["com.vrcfury.vrcfury"].parent
    upper = runtime / "Case.txt"
    lower = runtime / "case.txt"
    upper.write_text("upper", encoding="utf-8")
    lower.write_text("lower", encoding="utf-8")
    if len({entry.name for entry in runtime.iterdir() if entry.name.casefold() == "case.txt"}) < 2:
        pytest.skip("the filesystem is case-insensitive")

    with pytest.raises(dependency_tool.ProtectedRuntimeDependencySetError) as failure:
        dependency_tool.create_dependency_set(fixture.paths)
    assert failure.value.code == "protected_runtime_dependency_input_invalid"


def test_create_rejects_alternate_data_stream_when_supported(tmp_path: Path) -> None:
    if os.name != "nt":
        pytest.skip("alternate data streams are Windows-specific")
    fixture = _fixture(tmp_path)
    package_json = fixture.package_json["com.vrcfury.vrcfury"]
    stream = Path(f"{package_json}:shadow")
    try:
        stream.write_bytes(b"hidden")
    except OSError as exc:
        pytest.skip(f"alternate data streams unavailable: {exc}")

    with pytest.raises(dependency_tool.ProtectedRuntimeDependencySetError) as failure:
        dependency_tool.create_dependency_set(fixture.paths)
    assert failure.value.code == "protected_runtime_dependency_input_invalid"


@pytest.mark.parametrize("directory", ["Library", "Temp", "__Generated", ".git"])
def test_create_rejects_generated_directories(tmp_path: Path, directory: str) -> None:
    fixture = _fixture(tmp_path)
    generated = fixture.package_json["com.vrcfury.vrcfury"].parent / directory
    generated.mkdir()
    (generated / "state.bin").write_bytes(b"generated")

    with pytest.raises(dependency_tool.ProtectedRuntimeDependencySetError) as failure:
        dependency_tool.create_dependency_set(fixture.paths)
    assert failure.value.code == "protected_runtime_dependency_input_invalid"


def test_verify_rejects_package_tree_digest_drift(tmp_path: Path) -> None:
    fixture = _fixture(tmp_path)
    dependency_tool.create_dependency_set(fixture.paths)
    fixture.package_payload["com.vrcfury.vrcfury"].write_text(
        "drifted-package-source\n", encoding="utf-8"
    )

    with pytest.raises(dependency_tool.ProtectedRuntimeDependencySetError) as failure:
        dependency_tool.verify_dependency_set(fixture.paths)
    assert failure.value.code == "protected_runtime_dependency_descriptor_mismatch"


def test_create_rechecks_held_tree_after_same_length_mutation(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    fixture = _fixture(tmp_path)
    target_root = fixture.package_json["com.vrcfury.vrcfury"].parent.resolve()
    target_file = fixture.package_payload["com.vrcfury.vrcfury"]
    original = dependency_tool._build_tree_document
    target_calls = 0

    def drifting(path: Path) -> dict[str, Any]:
        nonlocal target_calls
        document = original(path)
        if path.resolve() == target_root:
            target_calls += 1
            if target_calls == 1:
                original_bytes = target_file.read_bytes()
                target_file.write_bytes(b"x" * len(original_bytes))
        return document

    monkeypatch.setattr(dependency_tool, "_build_tree_document", drifting)
    with pytest.raises(dependency_tool.ProtectedRuntimeDependencySetError) as failure:
        dependency_tool.create_dependency_set(fixture.paths)
    assert failure.value.code == "protected_runtime_dependency_input_invalid"
    assert not fixture.paths.output.exists()


def test_create_rejects_extra_editor_builtin_root(tmp_path: Path) -> None:
    fixture = _fixture(tmp_path)
    extra = fixture.paths.editor_builtins_root / "com.unity.modules.unknown"
    extra.mkdir()
    _write_json(
        extra / "package.json",
        {
            "name": "com.unity.modules.unknown",
            "version": "1.0.0",
            "dependencies": {},
        },
    )

    with pytest.raises(dependency_tool.ProtectedRuntimeDependencySetError) as failure:
        dependency_tool.create_dependency_set(fixture.paths)
    assert failure.value.code == "protected_runtime_dependency_input_invalid"


def test_create_is_create_new_and_preserves_existing_target(tmp_path: Path) -> None:
    fixture = _fixture(tmp_path)
    fixture.paths.output.write_bytes(b"sentinel")

    with pytest.raises(dependency_tool.ProtectedRuntimeDependencySetError) as failure:
        dependency_tool.create_dependency_set(fixture.paths)
    assert failure.value.code == "protected_runtime_dependency_target_exists"
    assert fixture.paths.output.read_bytes() == b"sentinel"


def test_verify_rejects_bom_and_noncanonical_json(tmp_path: Path) -> None:
    fixture = _fixture(tmp_path)
    dependency_tool.create_dependency_set(fixture.paths)
    fixture.paths.output.write_bytes(b"\xef\xbb\xbf" + fixture.paths.output.read_bytes())

    with pytest.raises(dependency_tool.ProtectedRuntimeDependencySetError) as failure:
        dependency_tool.verify_dependency_set(fixture.paths)
    assert failure.value.code == "protected_runtime_dependency_descriptor_invalid"


def test_validate_rejects_arbitrary_empty_or_extra_document() -> None:
    for value in ({}, {"schema": dependency_tool.DEPENDENCY_SET_SCHEMA, "extra": True}):
        with pytest.raises(dependency_tool.ProtectedRuntimeDependencySetError) as failure:
            dependency_tool.validate_dependency_set_document(value)
        assert failure.value.code == "protected_runtime_dependency_descriptor_invalid"


def test_validate_rejects_rehashed_wrong_dependency_edge(tmp_path: Path) -> None:
    fixture = _fixture(tmp_path)
    dependency_tool.create_dependency_set(fixture.paths)
    document = json.loads(fixture.paths.output.read_text(encoding="utf-8"))
    package = next(
        row for row in document["packages"] if row["id"] == "com.unity.burst"
    )
    package["dependencies"].append(
        {"id": "com.unity.modules.physics", "requestedVersion": "1.0.0"}
    )
    package["dependencies"].sort(key=lambda row: row["id"])
    document["setDigest"] = dependency_tool._set_digest(document)

    with pytest.raises(dependency_tool.ProtectedRuntimeDependencySetError) as failure:
        dependency_tool.validate_dependency_set_document(document)
    assert failure.value.code == "protected_runtime_dependency_descriptor_invalid"


def test_cli_create_and_verify_emit_redacted_receipts(
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    fixture = _fixture(tmp_path)
    common = [
        "--output",
        str(fixture.paths.output),
        "--project-root",
        str(fixture.paths.project_root),
        "--descriptors-root",
        str(fixture.paths.descriptors_root),
        "--editor-builtins-root",
        str(fixture.paths.editor_builtins_root),
    ]
    for package_id, root in sorted(fixture.paths.package_roots.items()):
        common.extend(("--package-root", f"{package_id}={root}"))

    assert dependency_tool.main(["--create", *common]) == 0
    created = capsys.readouterr()
    assert created.err == ""
    assert str(tmp_path) not in created.out
    assert json.loads(created.out)["mode"] == "create"

    assert dependency_tool.main(["--verify", *common]) == 0
    verified = capsys.readouterr()
    assert verified.err == ""
    assert str(tmp_path) not in verified.out
    assert json.loads(verified.out)["mode"] == "verify"


def test_cli_rejects_relative_paths_without_leaking_them(
    capsys: pytest.CaptureFixture[str],
) -> None:
    assert dependency_tool.main(
        [
            "--create",
            "--output",
            "relative.json",
            "--project-root",
            "project",
            "--descriptors-root",
            "descriptors",
            "--editor-builtins-root",
            "builtins",
        ]
    ) == 2
    captured = capsys.readouterr()
    assert captured.out == ""
    assert "relative.json" not in captured.err
    assert json.loads(captured.err) == {
        "ok": False,
        "error": "protected_runtime_dependency_cli_invalid",
    }
