from __future__ import annotations

import json
from pathlib import Path
import subprocess
from typing import Any

import pytest

from bounded_process import BoundedProcessResult
from package_install_workflow_service import (
    PackageDetectionPorts,
    PackageDetectionService,
    PackageInstallApprovedWriteHandler,
    PackageInstallWorkflowPorts,
    PackageInstallWorkflowService,
    PackageManagerDiscoveryPorts,
    PackageManagerDiscoveryService,
    VpmPackageInstallExecutionPorts,
    VpmPackageInstallExecutor,
    VpmPackageInstallPreparationPorts,
    VpmPackageInstallPreparer,
    select_sealed_vpm_version,
)
from prepared_unity_execution import (
    PREPARED_UNITY_EXECUTION_ARGUMENT_KEY,
    prepared_call,
    prepared_evidence,
)


def _process_result(
    returncode: int = 0,
    stdout: str = "",
    stderr: str = "",
    *,
    stdout_truncated: bool = False,
    stderr_truncated: bool = False,
) -> BoundedProcessResult:
    return BoundedProcessResult(
        returncode,
        stdout,
        stderr,
        stdout_truncated,
        stderr_truncated,
    )


def _project(tmp_path: Path) -> Path:
    project = tmp_path / "Project"
    (project / "Assets").mkdir(parents=True)
    (project / "Packages").mkdir()
    (project / "ProjectSettings").mkdir()
    (project / "Packages" / "manifest.json").write_text(
        '{"dependencies":{}}',
        encoding="utf-8",
    )
    return project


def _detection() -> PackageDetectionService:
    return PackageDetectionService(
        PackageDetectionPorts(
            path_exists=lambda path: path.exists(),
            read_utf8_sig_text=lambda path: path.read_text(encoding="utf-8-sig"),
        )
    )


def _workflow(
    project: Path,
    manager: dict[str, Any],
    detection: PackageDetectionService,
) -> PackageInstallWorkflowService:
    return PackageInstallWorkflowService(
        PackageInstallWorkflowPorts(
            selected_project_path=lambda: str(project),
            locate_managers=lambda: [manager],
            detect_package=detection.detect,
            addon_frameworks={},
            optimizer_dependencies=[],
            summarize_debug=lambda value: value,
            read_compile_errors=lambda _params: {"ok": True, "errors": []},
            redact_support=lambda value: value,
            create_apply_request=lambda params, **_kwargs: params,
        )
    )


def _preparer(
    project: Path,
    cli: Path,
    detection: PackageDetectionService,
    probe_calls: list[tuple[list[str], dict[str, Any]]],
    *,
    versions: list[Any] | None = None,
) -> VpmPackageInstallPreparer:
    manager = {
        "name": "vrc-get",
        "path": str(cli),
        "kind": "managed-cli",
        "source": "vrcforge-managed",
        "supportsCommandInstall": True,
        "supportsUiHandoff": False,
    }
    workflow = _workflow(project, manager, detection)

    def run_probe(argv: list[str], **kwargs: Any) -> BoundedProcessResult:
        probe_calls.append((list(argv), dict(kwargs)))
        if argv[-1] == "--version":
            return _process_result(stdout="1.9.1\n")
        return _process_result(
            stdout=json.dumps(
                {
                    "versions": versions
                    if versions is not None
                    else [{"version": "1.2.3"}]
                }
            )
        )

    return VpmPackageInstallPreparer(
        VpmPackageInstallPreparationPorts(
            resolve_project_path=lambda params: str(
                params.get("projectPath") or project
            ),
            locate_managers=lambda: [manager],
            select_strategy=workflow.select_strategy,
            detect_package=detection.detect,
            process_environment=lambda: {
                "SystemRoot": "C:/Windows",
                "HTTPS_PROXY": "http://proxy.invalid",
                "UNRELATED_SECRET": "must-not-be-inherited",
            },
            run_probe_process=run_probe,
            creationflags=64,
        )
    )


def test_manager_discovery_preserves_priority_shape_and_never_starts_a_process(
    tmp_path: Path,
) -> None:
    managed = tmp_path / "managed" / "vrc-get.exe"
    managed.parent.mkdir()
    managed.write_bytes(b"managed")
    local_app_data = tmp_path / "LocalAppData"
    vcc = (
        local_app_data
        / "Programs"
        / "VRChat Creator Companion"
        / "CreatorCompanion.exe"
    )
    vcc.parent.mkdir(parents=True)
    vcc.write_bytes(b"vcc")
    path_vpm = tmp_path / "PATH" / "vpm.exe"
    path_vpm.parent.mkdir()
    path_vpm.write_bytes(b"vpm")
    environment = {
        "VRCFORGE_VRC_GET_PATH": str(managed),
        "LOCALAPPDATA": str(local_app_data),
        "ProgramFiles": str(tmp_path / "ProgramFiles"),
        "ProgramFiles(x86)": str(tmp_path / "ProgramFilesX86"),
    }
    discovery = PackageManagerDiscoveryService(
        PackageManagerDiscoveryPorts(
            get_environment_value=lambda key: environment.get(key, ""),
            find_executable=lambda name: str(path_vpm) if name == "vpm" else None,
            is_file=lambda path: path.is_file(),
        )
    )

    managers = discovery.locate()

    assert [(item["name"], item["kind"]) for item in managers] == [
        ("vrc-get", "managed-cli"),
        ("vpm", "cli"),
        ("vcc", "app"),
    ]
    assert managers[0]["source"] == "vrcforge-managed"
    assert managers[0]["supportsCommandInstall"] is True
    assert managers[-1]["supportsUiHandoff"] is True
    assert all("\\" not in item["path"] for item in managers)


def test_package_detection_keeps_embedded_vpm_upm_precedence(tmp_path: Path) -> None:
    project = _project(tmp_path)
    detection = _detection()
    package_id = "com.example.package"
    upm = project / "Packages" / "manifest.json"
    upm.write_text(
        json.dumps({"dependencies": {package_id: "1.0.0"}}),
        encoding="utf-8",
    )
    vpm = project / "Packages" / "vpm-manifest.json"
    vpm.write_text(
        json.dumps(
            {
                "dependencies": {package_id: "2.0.0"},
                "locked": {package_id: {"version": "2.1.0"}},
            }
        ),
        encoding="utf-8",
    )

    assert detection.detect(project, [package_id]) == {
        "installed": True,
        "packageId": package_id,
        "version": "2.1.0",
        "source": "vpm",
    }

    embedded = project / "Packages" / package_id / "package.json"
    embedded.parent.mkdir()
    embedded.write_text('{"version":"3.0.0"}', encoding="utf-8")
    assert detection.detect(project, [package_id])["source"] == "embedded"
    assert detection.detect(project, [package_id])["version"] == "3.0.0"
    assert detection.detect(None, [package_id])["warning"].startswith(
        "No Unity project selected"
    )


def test_preparer_freezes_version_argv_identity_and_bounded_probe_policy(
    tmp_path: Path,
) -> None:
    project = _project(tmp_path)
    cli = tmp_path / "vrc-get.exe"
    cli.write_bytes(b"fixed-cli")
    detection = _detection()
    probe_calls: list[tuple[list[str], dict[str, Any]]] = []
    preparer = _preparer(project, cli, detection, probe_calls)

    prepared, preview = preparer.prepare(
        {"projectPath": str(project), "packageId": "com.example.package"},
        None,
    )
    tool_name, call = prepared_call(prepared)
    evidence = prepared_evidence(prepared)

    assert tool_name == "external.vpm.install"
    assert call["argv"][-2:] == ["com.example.package", "1.2.3"]
    assert call["timeoutSeconds"] == 300
    assert prepared["packageVersion"] == "1.2.3"
    assert evidence["packageVersion"] == "1.2.3"
    assert preview["processPolicy"] == {
        "scope": "one synchronous child owned by this approved request",
        "timeoutSeconds": 300,
        "maxOutputBytesPerStream": 4194304,
        "shell": False,
        "projectOnly": True,
        "authentication": "CLI existing local user configuration only",
    }
    assert [call[0][1:] for call in probe_calls] == [
        ["--version"],
        ["info", "package", "--no-update", "com.example.package"],
    ]
    for _argv, kwargs in probe_calls:
        assert kwargs["max_output_bytes"] == 4194304
        assert kwargs["creationflags"] == 64
        assert kwargs["env"]["PATH"] == str(cli.parent.resolve())
        assert "UNRELATED_SECRET" not in kwargs["env"]


def test_preparer_rejects_reserved_repository_and_unavailable_prerelease(
    tmp_path: Path,
) -> None:
    project = _project(tmp_path)
    cli = tmp_path / "vrc-get.exe"
    cli.write_bytes(b"fixed-cli")
    detection = _detection()
    preparer = _preparer(
        project,
        cli,
        detection,
        [],
        versions=[{"version": "1.2.3-beta.1"}],
    )
    with pytest.raises(RuntimeError, match="reserved"):
        preparer.prepare(
            {
                "projectPath": str(project),
                "packageId": "com.example.package",
                PREPARED_UNITY_EXECUTION_ARGUMENT_KEY: {},
            },
            None,
        )
    with pytest.raises(RuntimeError, match="Repository changes"):
        preparer.prepare(
            {
                "projectPath": str(project),
                "packageId": "com.example.package",
                "repository": "https://example.invalid",
            },
            None,
        )
    with pytest.raises(RuntimeError, match="No non-yanked"):
        preparer.prepare(
            {"projectPath": str(project), "packageId": "com.example.package"},
            None,
        )


def test_approved_executor_owns_one_child_and_requires_exact_readback(
    tmp_path: Path,
) -> None:
    project = _project(tmp_path)
    cli = tmp_path / "vrc-get.exe"
    cli.write_bytes(b"fixed-cli")
    detection = _detection()
    preparer = _preparer(project, cli, detection, [])
    prepared, _preview = preparer.prepare(
        {"projectPath": str(project), "packageId": "com.example.package"},
        None,
    )
    install_calls: list[tuple[list[str], dict[str, Any]]] = []

    def run_install(argv: list[str], **kwargs: Any) -> BoundedProcessResult:
        install_calls.append((list(argv), dict(kwargs)))
        (project / "Packages" / "vpm-manifest.json").write_text(
            json.dumps(
                {
                    "dependencies": {"com.example.package": "1.2.3"},
                    "locked": {
                        "com.example.package": {"version": "1.2.3"}
                    },
                }
            ),
            encoding="utf-8",
        )
        return _process_result(stdout="installed", stderr="warning")

    executor = VpmPackageInstallExecutor(
        VpmPackageInstallExecutionPorts(
            detect_package=detection.detect,
            process_environment=lambda: {"TEMP": "C:/Temp", "SECRET": "no"},
            run_install_process=run_install,
            creationflags=128,
        )
    )
    handlers = PackageInstallApprovedWriteHandler(
        prepare=preparer.prepare,
        execute=executor.execute,
    )

    result = handlers.execute(prepared)

    assert result["ok"] is True
    assert result["stdoutSummary"] == "installed"
    assert result["stderrSummary"] == "warning"
    assert len(install_calls) == 1
    assert install_calls[0][1]["timeout_seconds"] == 300
    assert install_calls[0][1]["max_output_bytes"] == 4194304
    assert install_calls[0][1]["creationflags"] == 128
    assert "SECRET" not in install_calls[0][1]["env"]
    assert result["vpmManifestReadback"] == {
        "packageId": "com.example.package",
        "version": "1.2.3",
        "source": "vpm-manifest.locked",
        "embeddedPackage": None,
    }


def test_prestate_drift_blocks_before_install_child(tmp_path: Path) -> None:
    project = _project(tmp_path)
    cli = tmp_path / "vrc-get.exe"
    cli.write_bytes(b"fixed-cli")
    detection = _detection()
    preparer = _preparer(project, cli, detection, [])
    prepared, _preview = preparer.prepare(
        {"projectPath": str(project), "packageId": "com.example.package"},
        None,
    )
    (project / "Packages" / "manifest.json").write_text(
        '{"dependencies":{"drift":"1.0.0"}}',
        encoding="utf-8",
    )

    def forbidden_process(*_args: Any, **_kwargs: Any) -> BoundedProcessResult:
        raise AssertionError("install child must not start")

    executor = VpmPackageInstallExecutor(
        VpmPackageInstallExecutionPorts(
            detect_package=detection.detect,
            process_environment=dict,
            run_install_process=forbidden_process,
        )
    )
    result = executor.execute(prepared)

    assert result["ok"] is False
    assert result["recovery"] == {
        "checkpointMustBeRestoredOnlyIfUserChooses": False,
        "committed": False,
        "commitState": "not_started",
    }


def test_nonzero_child_exit_is_unknown_and_recoverable(tmp_path: Path) -> None:
    project = _project(tmp_path)
    cli = tmp_path / "vrc-get.exe"
    cli.write_bytes(b"fixed-cli")
    detection = _detection()
    preparer = _preparer(project, cli, detection, [])
    prepared, _preview = preparer.prepare(
        {"projectPath": str(project), "packageId": "com.example.package"},
        None,
    )
    executor = VpmPackageInstallExecutor(
        VpmPackageInstallExecutionPorts(
            detect_package=detection.detect,
            process_environment=dict,
            run_install_process=lambda _argv, **_kwargs: _process_result(
                7,
                stderr="failed",
            ),
        )
    )

    result = executor.execute(prepared)

    assert result["ok"] is False
    assert result["exitCode"] == 7
    assert result["recovery"] == {
        "checkpointMustBeRestoredOnlyIfUserChooses": True,
        "committed": True,
        "commitState": "unknown",
    }


def test_timeout_after_child_start_is_unknown_and_recoverable(tmp_path: Path) -> None:
    project = _project(tmp_path)
    cli = tmp_path / "vrc-get.exe"
    cli.write_bytes(b"fixed-cli")
    detection = _detection()
    preparer = _preparer(project, cli, detection, [])
    prepared, _preview = preparer.prepare(
        {"projectPath": str(project), "packageId": "com.example.package"},
        None,
    )

    def timeout(argv: list[str], **_kwargs: Any) -> BoundedProcessResult:
        raise subprocess.TimeoutExpired(argv, 300)

    executor = VpmPackageInstallExecutor(
        VpmPackageInstallExecutionPorts(
            detect_package=detection.detect,
            process_environment=dict,
            run_install_process=timeout,
        )
    )

    result = executor.execute(prepared)

    assert result["ok"] is False
    assert result["recovery"] == {
        "checkpointMustBeRestoredOnlyIfUserChooses": True,
        "committed": True,
        "commitState": "unknown",
    }


@pytest.mark.parametrize("readback_version", [None, "9.9.9"])
def test_missing_or_wrong_readback_after_success_is_unknown_and_recoverable(
    tmp_path: Path,
    readback_version: str | None,
) -> None:
    project = _project(tmp_path)
    cli = tmp_path / "vrc-get.exe"
    cli.write_bytes(b"fixed-cli")
    detection = _detection()
    preparer = _preparer(project, cli, detection, [])
    prepared, _preview = preparer.prepare(
        {"projectPath": str(project), "packageId": "com.example.package"},
        None,
    )

    def run_install(_argv: list[str], **_kwargs: Any) -> BoundedProcessResult:
        if readback_version is not None:
            (project / "Packages" / "vpm-manifest.json").write_text(
                json.dumps(
                    {
                        "dependencies": {
                            "com.example.package": readback_version,
                        },
                        "locked": {
                            "com.example.package": {
                                "version": readback_version,
                            }
                        },
                    }
                ),
                encoding="utf-8",
            )
        return _process_result(stdout="installed")

    executor = VpmPackageInstallExecutor(
        VpmPackageInstallExecutionPorts(
            detect_package=detection.detect,
            process_environment=dict,
            run_install_process=run_install,
        )
    )

    result = executor.execute(prepared)

    assert result["ok"] is False
    assert result["recovery"] == {
        "checkpointMustBeRestoredOnlyIfUserChooses": True,
        "committed": True,
        "commitState": "unknown",
    }


def test_semver_selection_matches_stable_and_exact_prerelease_policy() -> None:
    info = {
        "versions": [
            {"version": "1.9.0-beta.2"},
            {"version": "1.9.0-beta.10"},
            {"version": "1.8.9"},
            {"version": "1.9.0", "yanked": True},
            {"version": "2.0"},
        ]
    }
    assert select_sealed_vpm_version(info, "", False) == "1.8.9"
    assert select_sealed_vpm_version(info, "", True) == "1.9.0-beta.10"
    assert (
        select_sealed_vpm_version(info, "1.9.0-beta.2", True)
        == "1.9.0-beta.2"
    )
    with pytest.raises(RuntimeError, match="unavailable"):
        select_sealed_vpm_version(info, "1.9.0-beta.2", False)
