"""Finalize the private protected-runtime authority bundle after packaging."""

from __future__ import annotations

import argparse
import hashlib
import json
import ntpath
import os
import re
import stat
import subprocess
import sys
from pathlib import Path
from typing import Any, Mapping, Sequence


_PACKAGING_ROOT = Path(__file__).resolve().parent
if str(_PACKAGING_ROOT) not in sys.path:
    sys.path.insert(0, str(_PACKAGING_ROOT))

import protected_runtime_dependency_set
import protected_runtime_source_manifest


BUNDLE_SCHEMA = "vrcforge.primitive_evidence_authority_bundle.v3"
BUNDLE_RECEIPT_SCHEMA = "vrcforge.primitive_evidence_authority_bundle_receipt.v3"
PLAN_SCHEMA = "vrcforge.primitive_evidence_authority_policy.v2"
PREVIEW_SCHEMA = "vrcforge.primitive_evidence_authority_maintenance_preview.v2"
DEPENDENCY_FILE_NAME = "protected-runtime-dependency-set.json"
SOURCE_MANIFEST_FILE_NAME = "protected-runtime-source-manifest.json"
BUNDLE_FILE_NAME = "authority-bundle.json"
PREVIEW_PAYLOAD_ORDER = (
    "service",
    "controller",
    "installHelper",
    "lifecycleDriver",
    "bridgeLauncher",
    "runtimeSourceManifest",
)
SCENARIO_ORDER = (
    "component_feature_application",
    "parameter_optimization",
    "cross_avatar_accessory_copy",
    "model_part_composition",
)
_LOWER_SHA256_RE = re.compile(r"^[0-9a-f]{64}$")
_MAX_JSON_OUTPUT_BYTES = 2 * 1024 * 1024
_MAX_BINARY_BYTES = 512 * 1024 * 1024
_MAX_INPUT_FILE_BYTES = 16 * 1024 * 1024 * 1024
_FILE_ATTRIBUTE_REPARSE_POINT = 0x400


class AuthorityBundleError(RuntimeError):
    """Fail-closed, non-sensitive authority-bundle error."""

    def __init__(self, code: str) -> None:
        super().__init__(code)
        self.code = code


def canonical_json_bytes(value: Any) -> bytes:
    return json.dumps(
        value,
        ensure_ascii=True,
        allow_nan=False,
        separators=(",", ":"),
        sort_keys=True,
    ).encode("utf-8")


def _reject_duplicate_keys(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for key, value in pairs:
        if key in result:
            raise AuthorityBundleError("protected_runtime_authority_json_invalid")
        result[key] = value
    return result


def _parse_json_object(raw: bytes) -> dict[str, Any]:
    if not raw or len(raw) > _MAX_JSON_OUTPUT_BYTES:
        raise AuthorityBundleError("protected_runtime_authority_json_invalid")
    try:
        value = json.loads(
            raw.decode("utf-8", errors="strict"),
            object_pairs_hook=_reject_duplicate_keys,
        )
    except (UnicodeError, json.JSONDecodeError) as exc:
        raise AuthorityBundleError(
            "protected_runtime_authority_json_invalid"
        ) from exc
    if not isinstance(value, dict):
        raise AuthorityBundleError("protected_runtime_authority_json_invalid")
    return value


def _require_bool(value: Any) -> bool:
    if not isinstance(value, bool):
        raise AuthorityBundleError("protected_runtime_authority_contract_invalid")
    return value


def _require_nonempty_list(value: Any) -> list[Any]:
    if not isinstance(value, list) or not value:
        raise AuthorityBundleError("protected_runtime_authority_contract_invalid")
    return value


def _require_digest(value: Any) -> str:
    if (
        not isinstance(value, str)
        or _LOWER_SHA256_RE.fullmatch(value) is None
        or set(value) == {"0"}
    ):
        raise AuthorityBundleError("protected_runtime_authority_contract_invalid")
    return value


def _is_link_or_reparse(path: Path, metadata: os.stat_result) -> bool:
    return path.is_symlink() or bool(
        int(getattr(metadata, "st_file_attributes", 0))
        & _FILE_ATTRIBUTE_REPARSE_POINT
    )


def _absolute_path(value: os.PathLike[str] | str) -> Path:
    path = Path(value)
    if (
        not path.is_absolute()
        or (os.name == "nt" and str(path).startswith(("\\\\", "//")))
        or any(
            part in {"", ".", ".."} or ":" in part
            for part in path.parts[1:]
        )
    ):
        raise AuthorityBundleError("protected_runtime_authority_path_invalid")
    return path


def _regular_file(
    value: os.PathLike[str] | str,
    maximum_bytes: int = _MAX_INPUT_FILE_BYTES,
) -> Path:
    path = _absolute_path(value)
    try:
        absolute = path.absolute()
        resolved = path.resolve(strict=True)
        metadata = resolved.lstat()
    except OSError as exc:
        raise AuthorityBundleError(
            "protected_runtime_authority_input_unavailable"
        ) from exc
    if (
        os.path.normcase(str(absolute)) != os.path.normcase(str(resolved))
        or _is_link_or_reparse(resolved, metadata)
        or not stat.S_ISREG(metadata.st_mode)
        or int(metadata.st_nlink) != 1
        or int(metadata.st_size) <= 0
        or int(metadata.st_size) > maximum_bytes
    ):
        raise AuthorityBundleError("protected_runtime_authority_input_invalid")
    return resolved


def _directory(value: os.PathLike[str] | str) -> Path:
    path = _absolute_path(value)
    try:
        absolute = path.absolute()
        resolved = path.resolve(strict=True)
        metadata = resolved.lstat()
    except OSError as exc:
        raise AuthorityBundleError(
            "protected_runtime_authority_input_unavailable"
        ) from exc
    if (
        os.path.normcase(str(absolute)) != os.path.normcase(str(resolved))
        or _is_link_or_reparse(resolved, metadata)
        or not stat.S_ISDIR(metadata.st_mode)
    ):
        raise AuthorityBundleError("protected_runtime_authority_input_invalid")
    return resolved


def _same_path(first: Path, second: Path) -> bool:
    return os.path.normcase(str(first)) == os.path.normcase(str(second))


def _is_within(path: Path, root: Path) -> bool:
    try:
        path.relative_to(root)
        return True
    except ValueError:
        return False


def _hash_file(path: Path) -> dict[str, Any]:
    digest = hashlib.sha256()
    length = 0
    try:
        with path.open("rb") as stream:
            while chunk := stream.read(1024 * 1024):
                digest.update(chunk)
                length += len(chunk)
    except OSError as exc:
        raise AuthorityBundleError(
            "protected_runtime_authority_input_unavailable"
        ) from exc
    if length <= 0 or length > _MAX_BINARY_BYTES:
        raise AuthorityBundleError("protected_runtime_authority_input_invalid")
    return {"sha256": digest.hexdigest(), "byteLength": length}


def _require_file_name(path: Path, expected: str) -> None:
    if path.name != expected:
        raise AuthorityBundleError("protected_runtime_authority_layout_invalid")


def _parse_package_roots(values: Sequence[str]) -> dict[str, Path]:
    roots: dict[str, Path] = {}
    folded: set[str] = set()
    for value in values:
        package_id, separator, raw_path = value.partition("=")
        if (
            separator != "="
            or not package_id
            or package_id.casefold() in folded
            or not raw_path
        ):
            raise AuthorityBundleError("protected_runtime_authority_cli_invalid")
        folded.add(package_id.casefold())
        roots[package_id] = _directory(raw_path)
    return roots


def _run_json(executable: Path, arguments: Sequence[str]) -> dict[str, Any]:
    try:
        completed = subprocess.run(
            [str(executable), *arguments],
            check=False,
            capture_output=True,
            timeout=30,
        )
    except (OSError, subprocess.SubprocessError) as exc:
        raise AuthorityBundleError(
            "protected_runtime_authority_helper_failed"
        ) from exc
    if completed.returncode != 0 or completed.stderr.strip():
        raise AuthorityBundleError("protected_runtime_authority_helper_failed")
    return _parse_json_object(completed.stdout)


def _validate_plan(plan: Mapping[str, Any]) -> dict[str, str]:
    if (
        plan.get("schema") != PLAN_SCHEMA
        or _require_bool(plan.get("mutationSupported")) is not False
        or _require_bool(plan.get("trustedBoundaryReady")) is not False
        or _require_bool(plan.get("candidatePayloadIncludesAuthority")) is not False
        or not isinstance(plan.get("serviceSecuritySddl"), str)
        or not plan["serviceSecuritySddl"]
        or plan.get("generationPathPolicy")
        != "authority-generation-sha256-parent-create-new-never-reuse"
    ):
        raise AuthorityBundleError("protected_runtime_authority_plan_invalid")
    _require_nonempty_list(plan.get("blockers"))
    layout = plan.get("layout")
    if not isinstance(layout, dict):
        raise AuthorityBundleError("protected_runtime_authority_plan_invalid")

    binary_anchor = layout.get("binaryAnchor")
    state_anchor = layout.get("stateAnchor")
    if not isinstance(binary_anchor, str) or not isinstance(state_anchor, str):
        raise AuthorityBundleError("protected_runtime_authority_plan_invalid")
    binary_base = ntpath.join(binary_anchor, "VRCForgeEvidenceAuthority")
    state_base = ntpath.join(state_anchor, "VRCForgeEvidenceAuthority")
    binary_version_root = ntpath.join(binary_base, "v1")
    state_version_root = ntpath.join(state_base, "v1")
    generation_placeholder = "{authority-generation-sha256-lower}"
    generation_binary_pattern = ntpath.join(
        binary_version_root, "generations", generation_placeholder
    )
    generation_state_pattern = ntpath.join(
        state_version_root, "generations", generation_placeholder
    )
    expected = {
        "binaryBase": binary_base,
        "stateBase": state_base,
        "binaryVersionRoot": binary_version_root,
        "stateVersionRoot": state_version_root,
        "generationBinaryRootPattern": generation_binary_pattern,
        "generationStateRootPattern": generation_state_pattern,
        "serviceExecutablePattern": ntpath.join(
            generation_binary_pattern, "vrcforge_primitive_evidence_service.exe"
        ),
        "controllerExecutablePattern": ntpath.join(
            generation_binary_pattern, "vrcforge_primitive_evidence_controller.exe"
        ),
        "installHelperExecutablePattern": ntpath.join(
            generation_binary_pattern,
            "vrcforge_primitive_evidence_install_helper.exe",
        ),
        "lifecycleDriverExecutablePattern": ntpath.join(
            generation_binary_pattern,
            "vrcforge_primitive_lifecycle_driver.exe",
        ),
        "bridgeLauncherExecutablePattern": ntpath.join(
            generation_binary_pattern,
            "vrcforge_primitive_bridge_launcher.exe",
        ),
    }
    if any(layout.get(name) != value for name, value in expected.items()):
        raise AuthorityBundleError("protected_runtime_authority_plan_invalid")
    return {
        "binaryAnchor": binary_anchor,
        "stateAnchor": state_anchor,
        **expected,
    }


def _content_record(value: Any) -> dict[str, Any]:
    if not isinstance(value, dict) or set(value) != {"sha256", "byteLength"}:
        raise AuthorityBundleError("protected_runtime_authority_preview_invalid")
    digest = _require_digest(value.get("sha256"))
    length = value.get("byteLength")
    if isinstance(length, bool) or not isinstance(length, int) or length <= 0:
        raise AuthorityBundleError("protected_runtime_authority_preview_invalid")
    return {"sha256": digest, "byteLength": length}


def _validate_preview(
    preview: Mapping[str, Any],
    *,
    plan: Mapping[str, Any],
    plan_layout: Mapping[str, str],
    payload_records: Mapping[str, Mapping[str, Any]],
) -> tuple[str, dict[str, str]]:
    if (
        preview.get("schema") != PREVIEW_SCHEMA
        or preview.get("operation") != "install"
        or _require_bool(preview.get("automaticExecutionAllowed")) is not False
        or _require_bool(preview.get("nativeMutationBackendAvailable")) is not False
        or _require_bool(
            preview.get("executionRequiresVerifiedElevatedMaintenanceCapability")
        )
        is not True
        or _require_bool(preview.get("trustedBoundaryReady")) is not False
    ):
        raise AuthorityBundleError("protected_runtime_authority_preview_invalid")
    _require_nonempty_list(preview.get("blockers"))
    _require_nonempty_list(preview.get("steps"))
    generation = _require_digest(preview.get("generation"))
    _require_digest(preview.get("policySha256"))
    _require_digest(preview.get("planSha256"))

    raw_content = preview.get("content")
    if (
        not isinstance(raw_content, dict)
        or len(raw_content) != len(PREVIEW_PAYLOAD_ORDER)
        or set(raw_content) != set(PREVIEW_PAYLOAD_ORDER)
    ):
        raise AuthorityBundleError("protected_runtime_authority_preview_invalid")
    for content_name in PREVIEW_PAYLOAD_ORDER:
        if _content_record(raw_content.get(content_name)) != payload_records[content_name]:
            raise AuthorityBundleError("protected_runtime_authority_preview_invalid")

    layout = preview.get("layout")
    if not isinstance(layout, dict):
        raise AuthorityBundleError("protected_runtime_authority_preview_invalid")
    generation_binary_root = ntpath.join(
        plan_layout["binaryVersionRoot"], "generations", generation
    )
    generation_state_root = ntpath.join(
        plan_layout["stateVersionRoot"], "generations", generation
    )
    installed_paths = {
        "service": ntpath.join(
            generation_binary_root, "vrcforge_primitive_evidence_service.exe"
        ),
        "controller": ntpath.join(
            generation_binary_root, "vrcforge_primitive_evidence_controller.exe"
        ),
        "installHelper": ntpath.join(
            generation_binary_root,
            "vrcforge_primitive_evidence_install_helper.exe",
        ),
        "lifecycleDriver": ntpath.join(
            generation_binary_root,
            "vrcforge_primitive_lifecycle_driver.exe",
        ),
        "bridgeLauncher": ntpath.join(
            generation_binary_root,
            "vrcforge_primitive_bridge_launcher.exe",
        ),
        "runtimeSourceManifest": ntpath.join(
            generation_state_root, "runtime-source-manifest.json"
        ),
    }
    expected_layout = {
        "binaryAnchor": plan_layout["binaryAnchor"],
        "stateAnchor": plan_layout["stateAnchor"],
        "binaryBase": plan_layout["binaryBase"],
        "stateBase": plan_layout["stateBase"],
        "binaryVersionRoot": plan_layout["binaryVersionRoot"],
        "stateVersionRoot": plan_layout["stateVersionRoot"],
        "generationBinaryRoot": generation_binary_root,
        "generationStateRoot": generation_state_root,
        "serviceExecutable": installed_paths["service"],
        "controllerExecutable": installed_paths["controller"],
        "installHelperExecutable": installed_paths["installHelper"],
        "lifecycleDriverExecutable": installed_paths["lifecycleDriver"],
        "bridgeLauncherExecutable": installed_paths["bridgeLauncher"],
        "runtimeSourceManifest": installed_paths["runtimeSourceManifest"],
    }
    if any(layout.get(name) != value for name, value in expected_layout.items()):
        raise AuthorityBundleError("protected_runtime_authority_preview_invalid")

    fixed_policy = preview.get("fixedPolicy")
    service_policy = fixed_policy.get("service") if isinstance(fixed_policy, dict) else None
    if (
        not isinstance(service_policy, dict)
        or service_policy.get("binaryCommand")
        != f'"{installed_paths["service"]}" --service'
        or service_policy.get("securitySddl") != plan.get("serviceSecuritySddl")
    ):
        raise AuthorityBundleError("protected_runtime_authority_preview_invalid")
    return generation, installed_paths


def _matching_receipts(
    created: Mapping[str, Any], verified: Mapping[str, Any], *, schema: str
) -> None:
    if (
        created.get("ok") is not True
        or verified.get("ok") is not True
        or created.get("mode") != "create"
        or verified.get("mode") != "verify"
        or created.get("schema") != schema
        or verified.get("schema") != schema
        or {key: value for key, value in created.items() if key != "mode"}
        != {key: value for key, value in verified.items() if key != "mode"}
    ):
        raise AuthorityBundleError("protected_runtime_authority_receipt_invalid")


def _write_create_new(path: Path, content: bytes) -> dict[str, Any]:
    if path.exists() or path.is_symlink():
        raise AuthorityBundleError("protected_runtime_authority_target_exists")
    parent = _directory(path.parent)
    target = parent / path.name
    descriptor: int | None = None
    created_identity: tuple[int, int] | None = None
    try:
        descriptor = os.open(target, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
        metadata = os.fstat(descriptor)
        created_identity = (int(metadata.st_dev), int(metadata.st_ino))
        written = 0
        while written < len(content):
            count = os.write(descriptor, content[written:])
            if count <= 0:
                raise OSError("short write")
            written += count
        os.fsync(descriptor)
        os.close(descriptor)
        descriptor = None
        resolved = _regular_file(target)
        if resolved.read_bytes() != content:
            raise AuthorityBundleError("protected_runtime_authority_write_failed")
        return _hash_file(resolved)
    except FileExistsError as exc:
        raise AuthorityBundleError("protected_runtime_authority_target_exists") from exc
    except AuthorityBundleError:
        raise
    except OSError as exc:
        raise AuthorityBundleError("protected_runtime_authority_write_failed") from exc
    finally:
        if descriptor is not None:
            os.close(descriptor)
        if created_identity is not None and sys.exc_info()[0] is not None:
            try:
                metadata = target.lstat()
                if (
                    not _is_link_or_reparse(target, metadata)
                    and (int(metadata.st_dev), int(metadata.st_ino)) == created_identity
                ):
                    target.unlink()
            except OSError:
                pass


def build_bundle(arguments: argparse.Namespace) -> dict[str, Any]:
    authority_root = _directory(arguments.authority_root)
    repository_relative_root = arguments.repository_relative_root
    relative_parts = repository_relative_root.split("/")
    if (
        repository_relative_root.startswith("/")
        or "\\" in repository_relative_root
        or relative_parts[:2] != ["artifacts", "primitive-evidence-authority"]
        or len(relative_parts) != 4
        or relative_parts[2] != arguments.source_commit
        or any(part in {"", ".", ".."} or ":" in part for part in relative_parts)
    ):
        raise AuthorityBundleError("protected_runtime_authority_cli_invalid")
    if (
        os.path.normcase(authority_root.parent.name)
        != os.path.normcase(relative_parts[2])
        or os.path.normcase(authority_root.name)
        != os.path.normcase(relative_parts[3])
    ):
        raise AuthorityBundleError("protected_runtime_authority_layout_invalid")

    binaries = {
        "service": _regular_file(arguments.authority_service, _MAX_BINARY_BYTES),
        "controller": _regular_file(arguments.authority_controller, _MAX_BINARY_BYTES),
        "installHelper": _regular_file(
            arguments.authority_install_helper, _MAX_BINARY_BYTES
        ),
        "lifecycleDriver": _regular_file(arguments.driver, _MAX_BINARY_BYTES),
        "bridgeLauncher": _regular_file(
            arguments.bridge_launcher, _MAX_BINARY_BYTES
        ),
    }
    expected_names = {
        "service": "vrcforge_primitive_evidence_service.exe",
        "controller": "vrcforge_primitive_evidence_controller.exe",
        "installHelper": "vrcforge_primitive_evidence_install_helper.exe",
        "lifecycleDriver": "vrcforge_primitive_lifecycle_driver.exe",
        "bridgeLauncher": "vrcforge_primitive_bridge_launcher.exe",
    }
    for name, path in binaries.items():
        _require_file_name(path, expected_names[name])
        if not _same_path(path.parent, authority_root):
            raise AuthorityBundleError("protected_runtime_authority_layout_invalid")
    binary_records = {name: _hash_file(path) for name, path in binaries.items()}

    project_root = _directory(arguments.project_root)
    descriptors_root = _directory(project_root / "VRCForgeFixture" / "descriptors")
    fixture_parent = _directory(project_root / "Assets" / "VRCForge" / "PrimitiveBasis")
    fixture_roots = {
        scenario_id: _directory(fixture_parent / scenario_id)
        for scenario_id in SCENARIO_ORDER
    }
    fixture_descriptors = {
        scenario_id: _regular_file(descriptors_root / f"{scenario_id}.json")
        for scenario_id in SCENARIO_ORDER
    }
    model_root = fixture_roots["model_part_composition"]

    dependency_path = authority_root / DEPENDENCY_FILE_NAME
    source_manifest_path = authority_root / SOURCE_MANIFEST_FILE_NAME
    sidecar_path = authority_root / BUNDLE_FILE_NAME
    for output in (dependency_path, source_manifest_path, sidecar_path):
        if output.exists() or output.is_symlink():
            raise AuthorityBundleError("protected_runtime_authority_target_exists")

    strict_release_manifest = _regular_file(
        arguments.strict_release_manifest, _MAX_JSON_OUTPUT_BYTES
    )
    if (
        _is_within(authority_root, strict_release_manifest.parent)
        or _is_within(strict_release_manifest.parent, authority_root)
    ):
        raise AuthorityBundleError("protected_runtime_authority_layout_invalid")
    release_manifest_document = _parse_json_object(strict_release_manifest.read_bytes())
    if any(key.casefold() == "evidenceauthority" for key in release_manifest_document):
        raise AuthorityBundleError("protected_runtime_authority_public_manifest_invalid")
    release_manifest_record = _hash_file(strict_release_manifest)

    dependency_paths = protected_runtime_dependency_set.DependencySetPaths(
        project_root=project_root,
        descriptors_root=descriptors_root,
        editor_builtins_root=_directory(arguments.editor_builtins_root),
        package_roots=_parse_package_roots(arguments.package_root),
        output=dependency_path,
    )
    dependency_created = protected_runtime_dependency_set.create_dependency_set(
        dependency_paths
    )
    dependency_verified = protected_runtime_dependency_set.verify_dependency_set(
        dependency_paths
    )
    _matching_receipts(
        dependency_created,
        dependency_verified,
        schema=protected_runtime_dependency_set.DEPENDENCY_SET_RECEIPT_SCHEMA,
    )

    source_paths = protected_runtime_source_manifest.ProtectedRuntimeSourcePaths(
        authority_service=binaries["service"],
        driver=binaries["lifecycleDriver"],
        desktop=_regular_file(arguments.desktop),
        backend=_regular_file(arguments.backend),
        unity=_regular_file(arguments.unity),
        bridge_launcher=binaries["bridgeLauncher"],
        bridge_listener=_regular_file(arguments.bridge_listener),
        runtime_contract=_regular_file(model_root / "fixture-contract.json"),
        fixture_baseline=_regular_file(model_root / "baseline.json"),
        bridge_tree=_directory(arguments.bridge_tree),
        bridge_manifest=_regular_file(arguments.bridge_manifest),
        strict_release_manifest=strict_release_manifest,
        portable_archive=_regular_file(arguments.portable_archive),
        unity_package=_regular_file(arguments.unity_package),
        backend_tree=_directory(arguments.backend_tree),
        packaged_tool_tree=_directory(arguments.packaged_tool_tree),
        connector_tree=_directory(arguments.connector_tree),
        server_tree=_directory(arguments.server_tree),
        dependency_set_descriptor=dependency_path,
        component_feature_application_descriptor=fixture_descriptors[
            "component_feature_application"
        ],
        parameter_optimization_descriptor=fixture_descriptors[
            "parameter_optimization"
        ],
        cross_avatar_accessory_copy_descriptor=fixture_descriptors[
            "cross_avatar_accessory_copy"
        ],
        model_part_composition_descriptor=fixture_descriptors[
            "model_part_composition"
        ],
        component_feature_application_root=fixture_roots[
            "component_feature_application"
        ],
        parameter_optimization_root=fixture_roots["parameter_optimization"],
        cross_avatar_accessory_copy_root=fixture_roots[
            "cross_avatar_accessory_copy"
        ],
        model_part_composition_root=model_root,
    )
    source_created = protected_runtime_source_manifest.create_source_manifest(
        source_manifest_path,
        version=arguments.version,
        source_commit=arguments.source_commit,
        paths=source_paths,
    )
    source_verified = protected_runtime_source_manifest.verify_source_manifest(
        source_manifest_path,
        version=arguments.version,
        source_commit=arguments.source_commit,
        paths=source_paths,
    )
    _matching_receipts(
        source_created,
        source_verified,
        schema=protected_runtime_source_manifest.SOURCE_RECEIPT_SCHEMA,
    )

    plan = _run_json(binaries["installHelper"], ["--plan"])
    plan_layout = _validate_plan(plan)
    payload_paths = {
        "service": binaries["service"],
        "controller": binaries["controller"],
        "installHelper": binaries["installHelper"],
        "lifecycleDriver": binaries["lifecycleDriver"],
        "bridgeLauncher": binaries["bridgeLauncher"],
        "runtimeSourceManifest": _regular_file(source_manifest_path),
    }
    payload_records = {name: _hash_file(path) for name, path in payload_paths.items()}
    preview_arguments = [
        "--preview-install",
        *(str(payload_paths[name]) for name in PREVIEW_PAYLOAD_ORDER),
    ]
    preview = _run_json(binaries["installHelper"], preview_arguments)
    generation, installed_paths = _validate_preview(
        preview,
        plan=plan,
        plan_layout=plan_layout,
        payload_records=payload_records,
    )

    dependency_final = protected_runtime_dependency_set.verify_dependency_set(
        dependency_paths
    )
    source_final = protected_runtime_source_manifest.verify_source_manifest(
        source_manifest_path,
        version=arguments.version,
        source_commit=arguments.source_commit,
        paths=source_paths,
    )
    if dependency_final != dependency_verified or source_final != source_verified:
        raise AuthorityBundleError("protected_runtime_authority_receipt_invalid")
    if _hash_file(strict_release_manifest) != release_manifest_record:
        raise AuthorityBundleError("protected_runtime_authority_input_changed")
    if any(_hash_file(path) != payload_records[name] for name, path in payload_paths.items()):
        raise AuthorityBundleError("protected_runtime_authority_input_changed")
    if any(_hash_file(path) != binary_records[name] for name, path in binaries.items()):
        raise AuthorityBundleError("protected_runtime_authority_input_changed")

    file_entries = [
        {
            "name": binaries[name].name,
            **binary_records[name],
            "protectedInstallPayload": name in PREVIEW_PAYLOAD_ORDER,
        }
        for name in (
            "service",
            "controller",
            "installHelper",
            "lifecycleDriver",
            "bridgeLauncher",
        )
    ]
    dependency_record = _hash_file(dependency_path)
    source_record = _hash_file(source_manifest_path)
    if (
        dependency_record["sha256"] != dependency_verified["descriptorSha256"]
        or source_record["sha256"] != source_verified["manifestSha256"]
    ):
        raise AuthorityBundleError("protected_runtime_authority_input_changed")
    bundle = {
        "schema": BUNDLE_SCHEMA,
        "repositoryRelativeRoot": repository_relative_root,
        "files": file_entries,
        "dependencySet": {
            "repositoryRelativePath": f"{repository_relative_root}/{DEPENDENCY_FILE_NAME}",
            "descriptorSchema": dependency_verified["descriptorSchema"],
            "setDigest": dependency_verified["setDigest"],
            "sha256": dependency_record["sha256"],
            "byteLength": dependency_record["byteLength"],
            "verified": True,
        },
        "runtimeSourceManifest": {
            "repositoryRelativePath": f"{repository_relative_root}/{SOURCE_MANIFEST_FILE_NAME}",
            "schema": protected_runtime_source_manifest.SOURCE_MANIFEST_SCHEMA,
            "sha256": source_record["sha256"],
            "byteLength": source_record["byteLength"],
            "verified": True,
        },
        "strictReleaseManifest": {
            "sha256": release_manifest_record["sha256"],
            "byteLength": release_manifest_record["byteLength"],
        },
        "planSchema": plan["schema"],
        "previewSchema": preview["schema"],
        "installationSupported": False,
        "trustedBoundaryReady": False,
        "candidatePayloadIncluded": False,
        "automaticExecutionAllowed": False,
        "layout": {
            name: plan_layout[name]
            for name in (
                "binaryAnchor",
                "stateAnchor",
                "binaryBase",
                "stateBase",
                "binaryVersionRoot",
                "stateVersionRoot",
            )
        },
        "generation": {
            "sha256": generation,
            "policySha256": preview["policySha256"],
            "planSha256": preview["planSha256"],
            "binaryRoot": preview["layout"]["generationBinaryRoot"],
            "stateRoot": preview["layout"]["generationStateRoot"],
            "installMode": "create-new-never-reuse",
            "files": [
                {
                    "name": ntpath.basename(installed_paths[name]),
                    **payload_records[name],
                    "installedPath": installed_paths[name],
                }
                for name in PREVIEW_PAYLOAD_ORDER
            ],
        },
    }
    raw_bundle = canonical_json_bytes(bundle) + b"\n"
    sidecar_record = _write_create_new(sidecar_path, raw_bundle)
    if _parse_json_object(sidecar_path.read_bytes()) != bundle:
        raise AuthorityBundleError("protected_runtime_authority_write_failed")
    return {
        "ok": True,
        "schema": BUNDLE_RECEIPT_SCHEMA,
        "bundleSchema": BUNDLE_SCHEMA,
        "sidecarSha256": sidecar_record["sha256"],
        "dependencySetSha256": dependency_record["sha256"],
        "runtimeSourceManifestSha256": source_record["sha256"],
        "strictReleaseManifestSha256": release_manifest_record["sha256"],
        "generation": generation,
        "previewPayloadCount": len(PREVIEW_PAYLOAD_ORDER),
    }


class _ArgumentParser(argparse.ArgumentParser):
    def error(self, message: str) -> None:
        del message
        raise AuthorityBundleError("protected_runtime_authority_cli_invalid")


def _parser() -> argparse.ArgumentParser:
    parser = _ArgumentParser(
        description="Finalize one private VRCForge protected-runtime authority bundle."
    )
    parser.add_argument("--authority-root", required=True)
    parser.add_argument("--repository-relative-root", required=True)
    parser.add_argument("--version", required=True)
    parser.add_argument("--source-commit", required=True)
    parser.add_argument("--authority-service", required=True)
    parser.add_argument("--authority-controller", required=True)
    parser.add_argument("--authority-install-helper", required=True)
    parser.add_argument("--driver", required=True)
    parser.add_argument("--bridge-launcher", required=True)
    parser.add_argument("--desktop", required=True)
    parser.add_argument("--backend", required=True)
    parser.add_argument("--unity", required=True)
    parser.add_argument("--bridge-listener", required=True)
    parser.add_argument("--bridge-tree", required=True)
    parser.add_argument("--bridge-manifest", required=True)
    parser.add_argument("--strict-release-manifest", required=True)
    parser.add_argument("--portable-archive", required=True)
    parser.add_argument("--unity-package", required=True)
    parser.add_argument("--backend-tree", required=True)
    parser.add_argument("--packaged-tool-tree", required=True)
    parser.add_argument("--connector-tree", required=True)
    parser.add_argument("--server-tree", required=True)
    parser.add_argument("--project-root", required=True)
    parser.add_argument("--editor-builtins-root", required=True)
    parser.add_argument("--package-root", action="append", default=[])
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    try:
        receipt = build_bundle(_parser().parse_args(argv))
    except AuthorityBundleError as exc:
        failure = {"ok": False, "error": exc.code}
        sys.stderr.buffer.write(canonical_json_bytes(failure) + b"\n")
        return 2
    except (
        protected_runtime_dependency_set.ProtectedRuntimeDependencySetError,
        protected_runtime_source_manifest.ProtectedRuntimeSourceManifestError,
    ) as exc:
        failure = {"ok": False, "error": exc.code}
        sys.stderr.buffer.write(canonical_json_bytes(failure) + b"\n")
        return 2
    except BaseException:
        failure = {"ok": False, "error": "protected_runtime_authority_internal_failure"}
        sys.stderr.buffer.write(canonical_json_bytes(failure) + b"\n")
        return 2
    sys.stdout.buffer.write(canonical_json_bytes(receipt) + b"\n")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
