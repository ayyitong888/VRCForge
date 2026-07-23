from __future__ import annotations

import argparse
import ctypes
import hashlib
import json
import os
import secrets
import shutil
import socket
import stat
import subprocess
import sys
import time
import urllib.error
import urllib.request
import zipfile
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path, PurePosixPath
from typing import Any, Mapping


REPOSITORY_ROOT = Path(__file__).resolve().parents[1]
if str(REPOSITORY_ROOT) not in sys.path:
    sys.path.insert(0, str(REPOSITORY_ROOT))

from diagnostic_privacy import redact_public_evidence  # noqa: E402
from primitive_basis_live_attestation import (  # noqa: E402
    LiveBootstrap,
    build_live_matrix_report,
    encode_bootstrap_frame,
    verify_live_finalization,
)
from primitive_basis_matrix import load_fixture_set  # noqa: E402
from primitive_basis_live_runtime import compute_fixed_project_input_digest  # noqa: E402


APP_ORIGIN = "http://127.0.0.1:8757"
APP_REQUEST_ORIGIN = "tauri://localhost"
APP_PORT = 8757
BRIDGE_PORT = 8080
FIXTURE_TEMPLATE = (
    REPOSITORY_ROOT
    / "tests"
    / "fixtures"
    / "primitive_basis"
    / "projects"
    / "model_part_composition"
)
MODEL_SCENARIO_ID = "model_part_composition"
REQUIRED_EXTERNAL_PACKAGES = {
    "com.vrchat.base": "3.10.3",
    "com.vrchat.avatars": "3.10.3",
    "nadena.dev.ndmf": "1.13.1",
    "nadena.dev.modular-avatar": "1.17.1",
}
PACKAGED_CONNECTOR_ID = "com.coplaydev.unity-mcp"
PACKAGED_CONNECTOR_VERSION = "9.6.9-beta.7"
MAX_ARCHIVE_ENTRIES = 50_000
MAX_ARCHIVE_BYTES = 2 * 1024 * 1024 * 1024
MAX_TREE_FILES = 100_000
MAX_TREE_BYTES = 8 * 1024 * 1024 * 1024
CREATE_NO_WINDOW = 0x08000000


class PackagedModelPartSmokeError(RuntimeError):
    """The strict packaged model-part runner could not produce live evidence."""


@dataclass(frozen=True)
class PackageArtifact:
    package_id: str
    version: str
    source_root: Path
    tree_digest: str


@dataclass(frozen=True)
class PreparedRun:
    run_root: Path
    package_root: Path
    project_root: Path
    server_root: Path
    desktop_executable: Path
    backend_executable: Path
    unity_package: Path
    unity_editor: Path
    manifest_digest: str
    portable_digest: str
    desktop_digest: str
    backend_digest: str
    unity_package_digest: str
    packaged_unity_tool_tree_digest: str
    runtime_unity_tool_tree_digest: str
    runner_digest: str
    unity_editor_digest: str
    connector_digest: str
    server_digest: str
    dependencies: tuple[PackageArtifact, ...]
    fixtures: Any
    fixture_digest: str
    bootstrap: LiveBootstrap
    challenge_text: str


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description=(
            "Run the fixed packaged model-part composition row through explicit "
            "approval, checkpoint, readback, restore, and zero-residue cleanup."
        )
    )
    parser.add_argument(
        "--release-manifest",
        default="dist/release/release-manifest.json",
    )
    parser.add_argument("--unity-editor", required=True)
    parser.add_argument(
        "--package-root",
        action="append",
        default=[],
        help="Exact external Unity package root; repeat once for every required package.",
    )
    parser.add_argument("--mcp-server-root", required=True)
    parser.add_argument(
        "--artifact-root",
        default="artifacts/primitive-basis-model-part-live",
    )
    parser.add_argument("--timeout", type=float, default=300.0)
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)
    runner = PackagedModelPartSmoke(args)
    report = runner.run()
    report_path = runner.write_report(report)
    relative_report = report_path.relative_to(REPOSITORY_ROOT).as_posix()
    print(
        json.dumps(
            {
                "ok": report.get("ok") is True,
                "targetOk": report.get("targetOk") is True,
                "reportPath": relative_report,
                "status": report.get("status"),
            },
            ensure_ascii=True,
            sort_keys=True,
        )
    )
    return 0 if report.get("ok") is True else 1


class PackagedModelPartSmoke:
    def __init__(self, args: argparse.Namespace) -> None:
        self.args = args
        stamp = datetime.now(timezone.utc).strftime("%Y%m%d-%H%M%S")
        self.run_id = f"primitive-model-part-{stamp}-{secrets.token_hex(4)}"
        artifact_root = _resolve_under_repository(Path(args.artifact_root))
        self.run_root = artifact_root / self.run_id
        self.private_root = self.run_root / "private"
        self.report_path = self.run_root / "report.json"
        self.desktop_process: subprocess.Popen[bytes] | None = None
        self.unity_process: subprocess.Popen[bytes] | None = None
        self.desktop_log_handle: Any | None = None
        self.prepared: PreparedRun | None = None
        self.app_token = ""
        self.apply_approval_id = ""
        self.checkpoint_id = ""
        self.restore_verified = False
        self.project_deleted = False
        self.desktop_clean = False
        self.app_port_released = False
        self.bridge_port_released = False
        self.secrets_removed = False
        self.transcript_verified = False
        self.failure = ""

    def run(self) -> dict[str, Any]:
        started_at = _utc_now()
        finalization: dict[str, Any] | None = None
        matrix_report: dict[str, Any] | None = None
        try:
            self.prepared = self.prepare()
            self._launch_desktop()
            self._launch_unity()
            self._wait_for_fixture_runtime()
            self._configure_isolated_app()
            started = self._app_request(
                "POST",
                "/api/app/primitive-basis/live/model-part/start",
                {"projectPath": str(self.prepared.project_root)},
                timeout=self.args.timeout,
            )
            self.apply_approval_id = _require_safe_id(
                started.get("approvalId"), "apply approval"
            )
            apply_payload = self._approve(self.apply_approval_id)
            self.checkpoint_id = str(
                _mapping(_mapping(apply_payload.get("execution")).get("checkpoint")).get("id")
                or ""
            )
            self._refresh_live_status()
            restore_request = self._app_request(
                "POST",
                "/api/app/primitive-basis/live/model-part/readback",
                {},
                timeout=self.args.timeout,
            )
            restore_approval_id = _require_safe_id(
                restore_request.get("approvalId"), "restore approval"
            )
            restore_payload = self._approve(restore_approval_id)
            restore_execution = _mapping(restore_payload.get("execution"))
            self.restore_verified = (
                restore_payload.get("ok") is True
                and restore_execution.get("status") == "applied"
                and _mapping(restore_execution.get("result")).get("ok") is True
            )
            if not self.restore_verified:
                raise PackagedModelPartSmokeError("The fixed restore did not complete.")
            self._app_request(
                "POST",
                "/api/app/primitive-basis/live/model-part/prepare-cleanup",
                {},
                timeout=self.args.timeout,
            )
            self._close_unity_for_proof()
            self._delete_disposable_project()
            finalization = self._app_request(
                "POST",
                "/api/app/primitive-basis/live/model-part/finalize",
                {},
                timeout=self.args.timeout,
            )
            verified = verify_live_finalization(
                finalization,
                bootstrap=self.prepared.bootstrap,
                fixture_digest=self.prepared.fixture_digest,
                project_binding_digest=str(started.get("projectBindingDigest") or ""),
            )
            matrix_report = build_live_matrix_report(self.prepared.fixtures, verified)
            target_rows = [
                row
                for row in matrix_report.get("rows") or []
                if isinstance(row, Mapping)
                and row.get("scenarioId") == MODEL_SCENARIO_ID
            ]
            if (
                matrix_report.get("transcriptOk") is not True
                or matrix_report.get("targetOk") is not False
                or len(target_rows) != 1
                or target_rows[0].get("transcriptStatus") != "passed"
                or target_rows[0].get("status") != "blocked"
                or target_rows[0].get("reasons") != ["live_runner_origin_not_trusted"]
            ):
                raise PackagedModelPartSmokeError(
                    "The fixed live transcript trust boundary was invalid."
                )
            self.transcript_verified = True
        except Exception as exc:  # noqa: BLE001 - cleanup and a bounded report are mandatory.
            self.failure = _safe_failure(exc)
            self._attempt_emergency_restore()
        finally:
            try:
                self._cleanup_owned_processes()
            except Exception as exc:  # noqa: BLE001 - preserve a bounded failure report.
                self._record_cleanup_failure(exc)
            try:
                self._remove_isolated_secrets()
            except Exception as exc:  # noqa: BLE001 - preserve a bounded failure report.
                self._record_cleanup_failure(exc)

        transcript_ok = bool(
            finalization
            and matrix_report
            and matrix_report.get("transcriptOk") is True
            and matrix_report.get("targetOk") is False
            and self.transcript_verified
            and self.restore_verified
            and self.project_deleted
            and self.desktop_clean
            and self.app_port_released
            and self.bridge_port_released
            and self.secrets_removed
            and not self.failure
        )
        ok = False
        prepared = self.prepared
        report = {
            "schema": "vrcforge.primitive_basis_model_part_packaged_smoke.v2",
            "ok": ok,
            "targetOk": bool(matrix_report and matrix_report.get("targetOk") is True),
            "transcriptOk": transcript_ok,
            "status": "blocked" if transcript_ok else "failed",
            "blockers": ["live_runner_origin_not_trusted"] if transcript_ok else [],
            "startedAt": started_at,
            "finishedAt": _utc_now(),
            "runId": (
                prepared.bootstrap.run_id if prepared is not None else self.run_id
            ),
            "failure": self.failure,
            "releaseBinding": self._release_projection(prepared),
            "dependencyBinding": self._dependency_projection(prepared),
            "cleanup": {
                "restoreVerified": self.restore_verified,
                "projectRemoved": self.project_deleted,
                "desktopAndBackendStopped": self.desktop_clean,
                "appPortReleased": self.app_port_released,
                "bridgePortReleased": self.bridge_port_released,
                "isolatedSecretsRemoved": self.secrets_removed,
            },
            "matrix": matrix_report or {},
            "finalization": finalization or {},
        }
        safe = redact_public_evidence(report)
        if safe != report:
            raise PackagedModelPartSmokeError("The public smoke report was not privacy-safe.")
        return report

    def prepare(self) -> PreparedRun:
        if os.name != "nt":
            raise PackagedModelPartSmokeError("The packaged live runner requires Windows.")
        _require_port_released(APP_PORT, "app")
        _require_port_released(BRIDGE_PORT, "fixture bridge")
        if self.run_root.exists():
            raise PackagedModelPartSmokeError("The isolated run directory already exists.")
        self.private_root.mkdir(parents=True)

        manifest_path = _resolve_repository_file(Path(self.args.release_manifest))
        manifest_bytes = _stable_read(manifest_path, 2 * 1024 * 1024)
        manifest = _json_object(manifest_bytes, "release manifest")
        source_version = _stable_read(REPOSITORY_ROOT / "VERSION", 128).decode(
            "utf-8-sig"
        ).strip()
        head = _git("rev-parse", "HEAD").lower()
        origin_main = _git("rev-parse", "origin/main").lower()
        if head != origin_main or manifest.get("commit") != head:
            raise PackagedModelPartSmokeError("Strict source and release commits do not match.")
        if str(manifest.get("version") or "") != source_version:
            raise PackagedModelPartSmokeError("Strict source and release versions do not match.")
        if _git("status", "--porcelain"):
            raise PackagedModelPartSmokeError("Strict packaged evidence requires a clean worktree.")
        policy = _mapping(manifest.get("buildPolicy"))
        if policy != {
            "mode": "strict",
            "releaseEligible": True,
            "allowDirty": False,
            "allowUnpushed": False,
            "allowVersionMismatch": False,
        }:
            raise PackagedModelPartSmokeError("The release manifest is not strict.")

        portable_name = f"VRCForge_Windows_x64_{source_version}.zip"
        expected_artifacts = {
            "VRCForge.unitypackage",
            portable_name,
            "VRCForge_Offline_Installer_x64.exe",
            "VRCForge_Web_Installer_x64.exe",
        }
        artifact_rows = manifest.get("artifacts")
        if not isinstance(artifact_rows, list):
            raise PackagedModelPartSmokeError("The release artifact set is invalid.")
        artifacts: dict[str, str] = {}
        for item in artifact_rows:
            row = _mapping(item)
            name = str(row.get("name") or "")
            digest_value = str(row.get("sha256") or "").lower()
            if name in artifacts or not _is_sha256(digest_value):
                raise PackagedModelPartSmokeError("The release artifact set is invalid.")
            artifacts[name] = digest_value
        if set(artifacts) != expected_artifacts:
            raise PackagedModelPartSmokeError("The release artifact set is not exact.")
        release_root = manifest_path.parent
        portable_path = release_root / portable_name
        unity_package_path = release_root / "VRCForge.unitypackage"
        portable_digest = _stable_digest(portable_path)
        unity_package_digest = _stable_digest(unity_package_path)
        if (
            portable_digest != artifacts[portable_name]
            or unity_package_digest != artifacts["VRCForge.unitypackage"]
        ):
            raise PackagedModelPartSmokeError("A release artifact digest changed.")

        package_root = self.private_root / "package"
        _extract_safe_zip(portable_path, package_root)
        desktop = package_root / "VRCForge.exe"
        backend = package_root / "backend" / "vrcforge_backend.exe"
        internal_unity_package = (
            package_root / "unity_plugin" / "VRCForge.unitypackage"
        )
        desktop_digest = _stable_digest(desktop)
        backend_digest = _stable_digest(backend)
        if _stable_digest(internal_unity_package) != unity_package_digest:
            raise PackagedModelPartSmokeError("The portable Unity package copy changed.")
        embedded_version = _stable_read(package_root / "VERSION", 128).decode(
            "utf-8-sig"
        ).strip()
        if embedded_version != source_version:
            raise PackagedModelPartSmokeError("The portable version is invalid.")

        unity_editor = Path(self.args.unity_editor).expanduser().resolve(strict=True)
        if not unity_editor.is_file() or _is_reparse_point(unity_editor):
            raise PackagedModelPartSmokeError("The requested Unity editor is invalid.")
        unity_editor_digest = _stable_digest(unity_editor)

        project_root = self.private_root / "project"
        shutil.copytree(FIXTURE_TEMPLATE, project_root)
        unity_tool_source = (
            package_root / "unity_plugin" / "Assets" / "VRCForge" / "Editor"
        )
        packaged_unity_tool_tree_digest = _tree_digest(unity_tool_source)
        shutil.copytree(
            package_root / "unity_plugin" / "Assets" / "VRCForge",
            project_root / "Assets" / "VRCForge",
            dirs_exist_ok=True,
        )
        if (
            _tree_digest(project_root / "Assets" / "VRCForge" / "Editor")
            != packaged_unity_tool_tree_digest
        ):
            raise PackagedModelPartSmokeError("The packaged Unity tool copy changed.")
        runtime_unity_tool_root = project_root / "Assets" / "VRCForge" / "Editor"
        _materialize_deterministic_unity_metas(runtime_unity_tool_root)
        runtime_unity_tool_tree_digest = _tree_digest(runtime_unity_tool_root)
        connector_source = (
            package_root
            / "unity_plugin"
            / "Packages"
            / PACKAGED_CONNECTOR_ID
        )
        connector = _verify_package_root(
            connector_source,
            expected_id=PACKAGED_CONNECTOR_ID,
            expected_version=PACKAGED_CONNECTOR_VERSION,
        )
        connector_target = project_root / "Packages" / PACKAGED_CONNECTOR_ID
        shutil.copytree(connector_source, connector_target)
        if _tree_digest(connector_target) != connector.tree_digest:
            raise PackagedModelPartSmokeError("The packaged connector copy changed.")

        dependencies = _resolve_external_packages(self.args.package_root)
        for dependency in dependencies:
            target = project_root / "Packages" / dependency.package_id
            shutil.copytree(dependency.source_root, target)
            if _tree_digest(target) != dependency.tree_digest:
                raise PackagedModelPartSmokeError("An external package copy changed.")

        fixture_project_input_digest = compute_fixed_project_input_digest(project_root)

        server_source = Path(self.args.mcp_server_root).expanduser().resolve(strict=True)
        _verify_server_root(server_source)
        server_digest = _tree_digest(server_source)
        server_root = self.private_root / "mcp-server"
        shutil.copytree(server_source, server_root)
        if _tree_digest(server_root) != server_digest:
            raise PackagedModelPartSmokeError("The fixed bridge server copy changed.")

        fixtures = load_fixture_set(
            project_root / "VRCForgeFixture" / "descriptors",
            repository_root=project_root,
        )
        fixture = next(
            item for item in fixtures.fixtures if item.scenario_id == MODEL_SCENARIO_ID
        )
        if not fixture.materialized or not fixture.digest:
            raise PackagedModelPartSmokeError("The fixed fixture did not materialize.")
        runner_digest = _stable_digest(Path(__file__).resolve())
        runtime_binding_digest = _hash_json(
            {
                "manifestDigest": hashlib.sha256(manifest_bytes).hexdigest(),
                "portableDigest": portable_digest,
                "desktopDigest": desktop_digest,
                "backendDigest": backend_digest,
                "unityPackageDigest": unity_package_digest,
                "packagedUnityToolTreeDigest": packaged_unity_tool_tree_digest,
                "runtimeUnityToolTreeDigest": runtime_unity_tool_tree_digest,
                "runnerDigest": runner_digest,
                "unityEditorDigest": unity_editor_digest,
                "connectorDigest": connector.tree_digest,
                "serverDigest": server_digest,
                "dependencyDigests": {
                    item.package_id: item.tree_digest for item in dependencies
                },
                "fixtureSetDescriptorDigest": fixtures.descriptor_digest,
                "fixtureDescriptorDigest": fixture.descriptor_digest,
                "fixtureProjectInputDigest": fixture_project_input_digest,
            }
        )
        challenge_text = secrets.token_hex(16)
        bootstrap = LiveBootstrap(
            key=os.urandom(32),
            challenge=challenge_text.encode("ascii"),
            runtime_binding_digest=runtime_binding_digest,
            desktop_executable_digest=desktop_digest,
            backend_executable_digest=backend_digest,
            runner_digest=runner_digest,
            unity_package_digest=unity_package_digest,
            unity_editor_digest=unity_editor_digest,
            fixture_project_input_digest=fixture_project_input_digest,
            fixture_set_descriptor_digest=fixtures.descriptor_digest,
            fixture_descriptor_digest=fixture.descriptor_digest,
        )
        return PreparedRun(
            run_root=self.run_root,
            package_root=package_root,
            project_root=project_root,
            server_root=server_root,
            desktop_executable=desktop,
            backend_executable=backend,
            unity_package=unity_package_path,
            unity_editor=unity_editor,
            manifest_digest=hashlib.sha256(manifest_bytes).hexdigest(),
            portable_digest=portable_digest,
            desktop_digest=desktop_digest,
            backend_digest=backend_digest,
            unity_package_digest=unity_package_digest,
            packaged_unity_tool_tree_digest=packaged_unity_tool_tree_digest,
            runtime_unity_tool_tree_digest=runtime_unity_tool_tree_digest,
            runner_digest=runner_digest,
            unity_editor_digest=unity_editor_digest,
            connector_digest=connector.tree_digest,
            server_digest=server_digest,
            dependencies=dependencies,
            fixtures=fixtures,
            fixture_digest=fixture.digest,
            bootstrap=bootstrap,
            challenge_text=challenge_text,
        )

    def _launch_desktop(self) -> None:
        prepared = self._required_prepared()
        config_root = self.private_root / "config"
        user_data_root = self.private_root / "user-data"
        webview_root = self.private_root / "webview"
        for path in (config_root, user_data_root, webview_root):
            path.mkdir(parents=True, exist_ok=False)
        environment = dict(os.environ)
        environment.pop("VRCFORGE_APP_SESSION_TOKEN", None)
        environment.pop("VRCFORGE_PRIMITIVE_LIVE_STDIN", None)
        environment.update(
            {
                "VRCFORGE_USER_DATA_DIR": str(user_data_root),
                "VRCFORGE_CONFIG_DIR": str(config_root),
                "VRCFORGE_CONFIG_PATH": str(config_root / "config.json"),
                "VRCFORGE_SETTINGS_PATH": str(config_root / "settings.json"),
                "VRCFORGE_LOG_DIR": str(user_data_root / "logs"),
                "VRCFORGE_ARTIFACTS_DIR": str(user_data_root / "artifacts"),
                "WEBVIEW2_USER_DATA_FOLDER": str(webview_root),
                "VRCFORGE_PRIMITIVE_LIVE_STDIN": "1",
            }
        )
        desktop_log = (self.private_root / "desktop.log").open("wb")
        self.desktop_log_handle = desktop_log
        process = subprocess.Popen(
            [str(prepared.desktop_executable), "--primitive-live-stdin"],
            cwd=prepared.package_root,
            env=environment,
            stdin=subprocess.PIPE,
            stdout=desktop_log,
            stderr=subprocess.STDOUT,
        )
        self.desktop_process = process
        if process.stdin is None:
            raise PackagedModelPartSmokeError("The packaged desktop pipe was unavailable.")
        try:
            process.stdin.write(encode_bootstrap_frame(prepared.bootstrap))
            process.stdin.flush()
        finally:
            process.stdin.close()
        self.app_token = self._wait_for_app_token(
            user_data_root / "config" / "app-session-token", self.args.timeout
        )
        health = self._wait_for_app_health(self.args.timeout)
        if (
            health.get("ok") is not True
            or health.get("portableMode") is not True
        ):
            raise PackagedModelPartSmokeError("The packaged runtime health was invalid.")

    def _launch_unity(self) -> None:
        prepared = self._required_prepared()
        uv_directory = prepared.package_root / "tools" / "uv"
        if not (uv_directory / "uv.exe").is_file() or not (uv_directory / "uvx.exe").is_file():
            raise PackagedModelPartSmokeError("The packaged uv runtime is incomplete.")
        environment = dict(os.environ)
        environment.pop("VRCFORGE_PRIMITIVE_LIVE_STDIN", None)
        environment.update(
            {
                "VRCFORGE_PRIMITIVE_BASIS_RUN_ID": prepared.challenge_text,
                "VRCFORGE_PRIMITIVE_MCP_SERVER_ROOT": str(prepared.server_root),
                "UNITY_MCP_DISABLE_TELEMETRY": "1",
                "DISABLE_TELEMETRY": "1",
                "UV_OFFLINE": "1",
                "UV_FROZEN": "1",
                "PATH": str(uv_directory) + os.pathsep + environment.get("PATH", ""),
            }
        )
        unity_log = self.private_root / "unity.log"
        self.unity_process = subprocess.Popen(
            [
                str(prepared.unity_editor),
                "-projectPath",
                str(prepared.project_root),
                "-logFile",
                str(unity_log),
            ],
            cwd=prepared.project_root,
            env=environment,
            stdin=subprocess.DEVNULL,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        )

    def _wait_for_fixture_runtime(self) -> None:
        prepared = self._required_prepared()
        marker = (
            prepared.project_root
            / "Library"
            / "VRCForge"
            / "primitive-basis-model-part-ready.json"
        )
        deadline = time.monotonic() + max(30.0, float(self.args.timeout))
        while time.monotonic() < deadline:
            self._require_children_alive()
            if marker.is_file() and _port_open(BRIDGE_PORT):
                try:
                    payload = _json_object(_stable_read(marker, 32 * 1024), "fixture marker")
                except PackagedModelPartSmokeError:
                    payload = {}
                if payload.get("runIdDigest") == prepared.bootstrap.challenge_digest:
                    tools = self._app_request(
                        "POST",
                        "/api/unity/tools",
                        {"projectPath": str(prepared.project_root)},
                        timeout=20,
                    )
                    names = {str(item) for item in tools.get("toolNames") or []}
                    if {
                        "vrc_inspect_primitive_basis_fixture",
                        "vrc_reload_primitive_basis_fixture",
                        "vrc_inspect_modular_avatar_component",
                        "vrc_add_modular_avatar_component",
                    }.issubset(names):
                        return
            time.sleep(1.0)
        raise PackagedModelPartSmokeError("The fixed Unity fixture runtime did not become ready.")

    def _configure_isolated_app(self) -> None:
        prepared = self._required_prepared()
        state = self._app_request(
            "POST",
            "/api/state",
            {
                "projectPath": str(prepared.project_root),
                "unityHost": "127.0.0.1",
                "unityPort": BRIDGE_PORT,
            },
        )
        if str(state.get("selected_project_path") or state.get("selectedProjectPath") or "") not in {
            str(prepared.project_root),
            "",
        }:
            raise PackagedModelPartSmokeError("The isolated project selection failed.")
        permission = self._app_request(
            "POST",
            "/api/app/permission",
            {"execution_mode": "approval"},
        )
        if _mapping(permission.get("permission")).get("executionMode") != "approval":
            raise PackagedModelPartSmokeError("Approval mode was not enabled.")
        gateway = self._app_request(
            "POST",
            "/api/app/external-agent/gateway",
            {"enabled": True, "allowWriteRequests": True},
        )
        gateway_state = _mapping(gateway.get("gateway"))
        if gateway_state.get("enabled") is not True or gateway_state.get("allowWriteRequests") is not True:
            raise PackagedModelPartSmokeError("The isolated write-request lane was not enabled.")

    def _approve(self, approval_id: str) -> dict[str, Any]:
        prepared = self._required_prepared()
        payload = self._app_request(
            "POST",
            f"/api/app/agent/approvals/{approval_id}/approve",
            {
                "expected_project_root": str(prepared.project_root),
                "global_only": False,
            },
            timeout=self.args.timeout,
        )
        if payload.get("ok") is not True:
            raise PackagedModelPartSmokeError("An explicit live approval failed.")
        return payload

    def _close_unity_for_proof(self) -> None:
        process = self.unity_process
        if process is None or process.poll() is not None:
            raise PackagedModelPartSmokeError("The fixture Unity process exited too early.")
        if not _post_close_to_process_windows(process.pid):
            raise PackagedModelPartSmokeError("The fixture Unity window could not be closed normally.")
        try:
            process.wait(timeout=60)
        except subprocess.TimeoutExpired as exc:
            raise PackagedModelPartSmokeError("The fixture Unity process did not close normally.") from exc
        self.unity_process = None
        _wait_for_port_released(BRIDGE_PORT, "fixture bridge", timeout=30)
        self.bridge_port_released = True

    def _delete_disposable_project(self) -> None:
        prepared = self._required_prepared()
        project = prepared.project_root.resolve(strict=True)
        private_root = self.private_root.resolve(strict=True)
        if project.parent != private_root or project.name != "project":
            raise PackagedModelPartSmokeError("The disposable project deletion target is invalid.")
        shutil.rmtree(project)
        self.project_deleted = not project.exists()
        if not self.project_deleted:
            raise PackagedModelPartSmokeError("The disposable project was not removed.")

    def _attempt_emergency_restore(self) -> None:
        if not self.app_token or self.restore_verified:
            return
        try:
            self._refresh_live_status()
            if not self.checkpoint_id:
                return
            request = self._app_request(
                "POST",
                f"/api/app/checkpoints/{self.checkpoint_id}/restore",
                {},
                timeout=min(float(self.args.timeout), 120.0),
            )
            approval_id = _require_safe_id(
                _mapping(request.get("approval")).get("id"), "emergency restore approval"
            )
            restored = self._approve(approval_id)
            execution = _mapping(restored.get("execution"))
            self.restore_verified = (
                restored.get("ok") is True
                and execution.get("status") == "applied"
                and _mapping(execution.get("result")).get("ok") is True
            )
        except Exception:
            self.restore_verified = False

    def _refresh_live_status(self) -> None:
        if not self.app_token:
            return
        status = self._app_request(
            "GET",
            "/api/app/primitive-basis/live/model-part/status",
            None,
            timeout=min(float(self.args.timeout), 30.0),
        )
        if status.get("ok") is not True:
            raise PackagedModelPartSmokeError("The fixed live status was invalid.")
        status_approval = str(status.get("approvalId") or "")
        if self.apply_approval_id and status_approval != self.apply_approval_id:
            raise PackagedModelPartSmokeError("The fixed live approval identity changed.")
        checkpoint_id = str(status.get("checkpointId") or "")
        if checkpoint_id:
            self.checkpoint_id = _require_safe_id(checkpoint_id, "checkpoint")

    def _cleanup_owned_processes(self) -> None:
        failures: list[str] = []
        for attribute, label in (
            ("unity_process", "fixture Unity process"),
            ("desktop_process", "desktop process"),
        ):
            process = getattr(self, attribute)
            if process is not None:
                failures.extend(_stop_owned_process(process, label))
            setattr(self, attribute, None)

        if self.desktop_log_handle is not None:
            try:
                self.desktop_log_handle.close()
            except Exception as exc:  # noqa: BLE001 - the other cleanup steps must still run.
                failures.append(f"desktop log close failed ({type(exc).__name__})")
            finally:
                self.desktop_log_handle = None

        for port, label, attribute in (
            (APP_PORT, "app", "app_port_released"),
            (BRIDGE_PORT, "fixture bridge", "bridge_port_released"),
        ):
            try:
                _wait_for_port_released(port, label, timeout=20)
            except Exception as exc:  # noqa: BLE001 - verify both ports before reporting failure.
                failures.append(f"{label} port cleanup failed ({type(exc).__name__})")
            else:
                setattr(self, attribute, True)

        self.desktop_clean = not failures
        if failures:
            raise PackagedModelPartSmokeError("; ".join(failures))

    def _remove_isolated_secrets(self) -> None:
        self.app_token = ""
        for name in ("config", "user-data", "webview"):
            path = self.private_root / name
            if path.parent != self.private_root:
                raise PackagedModelPartSmokeError("An isolated secret target was invalid.")
            if path.exists():
                shutil.rmtree(path)
            if path.exists():
                raise PackagedModelPartSmokeError("An isolated secret directory remained.")
        self.secrets_removed = True

    def _record_cleanup_failure(self, exc: Exception) -> None:
        message = _safe_failure(exc)
        if not self.failure:
            self.failure = message
        elif message not in self.failure:
            self.failure = f"{self.failure}; {message}"[:240]

    def _app_request(
        self,
        method: str,
        path: str,
        payload: dict[str, Any] | None = None,
        *,
        timeout: float = 30.0,
    ) -> dict[str, Any]:
        if not self.app_token:
            raise PackagedModelPartSmokeError("The isolated app token is unavailable.")
        body = None if payload is None else json.dumps(payload).encode("utf-8")
        request = urllib.request.Request(
            APP_ORIGIN + path,
            data=body,
            method=method,
            headers={
                "Authorization": f"Bearer {self.app_token}",
                "Origin": APP_REQUEST_ORIGIN,
                "Content-Type": "application/json",
            },
        )
        try:
            with urllib.request.urlopen(request, timeout=max(1.0, float(timeout))) as response:
                raw = response.read(8 * 1024 * 1024 + 1)
        except (urllib.error.URLError, TimeoutError, OSError) as exc:
            raise PackagedModelPartSmokeError("The packaged app request failed.") from exc
        if len(raw) > 8 * 1024 * 1024:
            raise PackagedModelPartSmokeError("The packaged app response was too large.")
        return _json_object(raw, "packaged app response")

    def _wait_for_app_token(self, path: Path, timeout: float) -> str:
        deadline = time.monotonic() + max(10.0, float(timeout))
        while time.monotonic() < deadline:
            self._require_desktop_alive()
            if path.is_file() and not _is_reparse_point(path):
                token = path.read_text(encoding="utf-8").strip()
                if len(token) >= 32:
                    return token
            time.sleep(0.25)
        raise PackagedModelPartSmokeError("The isolated app token was not created.")

    def _wait_for_app_health(self, timeout: float) -> dict[str, Any]:
        deadline = time.monotonic() + max(10.0, float(timeout))
        while time.monotonic() < deadline:
            self._require_desktop_alive()
            try:
                return self._app_request("GET", "/api/health", None, timeout=3)
            except PackagedModelPartSmokeError:
                time.sleep(0.25)
        raise PackagedModelPartSmokeError("The packaged runtime did not become healthy.")

    def _require_desktop_alive(self) -> None:
        if self.desktop_process is None or self.desktop_process.poll() is not None:
            raise PackagedModelPartSmokeError("The packaged desktop exited early.")

    def _require_children_alive(self) -> None:
        self._require_desktop_alive()
        if self.unity_process is None or self.unity_process.poll() is not None:
            raise PackagedModelPartSmokeError("The fixture Unity process exited early.")

    def _required_prepared(self) -> PreparedRun:
        if self.prepared is None:
            raise PackagedModelPartSmokeError("The packaged run is not prepared.")
        return self.prepared

    def _release_projection(self, prepared: PreparedRun | None) -> dict[str, Any]:
        if prepared is None:
            return {}
        return {
            "manifestDigest": prepared.manifest_digest,
            "portableDigest": prepared.portable_digest,
            "desktopDigest": prepared.desktop_digest,
            "backendDigest": prepared.backend_digest,
            "unityPackageDigest": prepared.unity_package_digest,
            "packagedUnityToolTreeDigest": prepared.packaged_unity_tool_tree_digest,
            "runtimeUnityToolTreeDigest": prepared.runtime_unity_tool_tree_digest,
            "runnerDigest": prepared.runner_digest,
            "unityEditorDigest": prepared.unity_editor_digest,
            "runtimeBindingDigest": prepared.bootstrap.runtime_binding_digest,
        }

    def _dependency_projection(self, prepared: PreparedRun | None) -> dict[str, Any]:
        if prepared is None:
            return {}
        return {
            "connectorDigest": prepared.connector_digest,
            "serverDigest": prepared.server_digest,
            "packages": {
                item.package_id: {
                    "version": item.version,
                    "treeDigest": item.tree_digest,
                }
                for item in prepared.dependencies
            },
        }

    def write_report(self, report: Mapping[str, Any]) -> Path:
        self.run_root.mkdir(parents=True, exist_ok=True)
        safe = redact_public_evidence(dict(report))
        if safe != report:
            raise PackagedModelPartSmokeError("The report was not public-safe.")
        self.report_path.write_text(
            json.dumps(safe, ensure_ascii=True, indent=2, sort_keys=True) + "\n",
            encoding="utf-8",
        )
        return self.report_path


def _resolve_external_packages(values: list[str]) -> tuple[PackageArtifact, ...]:
    resolved: dict[str, PackageArtifact] = {}
    for value in values:
        root = Path(value).expanduser().resolve(strict=True)
        package = _verify_package_root(root)
        if package.package_id in resolved:
            raise PackagedModelPartSmokeError("An external package id was duplicated.")
        resolved[package.package_id] = package
    if set(resolved) != set(REQUIRED_EXTERNAL_PACKAGES):
        raise PackagedModelPartSmokeError("The external package set is not exact.")
    for package_id, version in REQUIRED_EXTERNAL_PACKAGES.items():
        if resolved[package_id].version != version:
            raise PackagedModelPartSmokeError("An external package version is invalid.")
    return tuple(resolved[key] for key in sorted(resolved))


def _verify_package_root(
    root: Path,
    *,
    expected_id: str = "",
    expected_version: str = "",
) -> PackageArtifact:
    if not root.is_dir() or _is_reparse_point(root):
        raise PackagedModelPartSmokeError("A package root is invalid.")
    payload = _json_object(_stable_read(root / "package.json", 2 * 1024 * 1024), "package")
    package_id = str(payload.get("name") or "")
    version = str(payload.get("version") or "")
    if expected_id and package_id != expected_id:
        raise PackagedModelPartSmokeError("The packaged connector id is invalid.")
    if expected_version and version != expected_version:
        raise PackagedModelPartSmokeError("The packaged connector version is invalid.")
    if package_id not in REQUIRED_EXTERNAL_PACKAGES and package_id != PACKAGED_CONNECTOR_ID:
        raise PackagedModelPartSmokeError("An unexpected package root was supplied.")
    return PackageArtifact(package_id, version, root, _tree_digest(root))


def _verify_server_root(root: Path) -> None:
    if not root.is_dir() or _is_reparse_point(root):
        raise PackagedModelPartSmokeError("The fixed bridge server root is invalid.")
    for relative in ("pyproject.toml", "uv.lock"):
        if not (root / relative).is_file() or _is_reparse_point(root / relative):
            raise PackagedModelPartSmokeError("The fixed bridge server is incomplete.")
    windows_python = root / ".venv" / "Scripts" / "python.exe"
    unix_python = root / ".venv" / "bin" / "python"
    if not windows_python.is_file() and not unix_python.is_file():
        raise PackagedModelPartSmokeError("The fixed bridge server environment is absent.")


def _extract_safe_zip(source: Path, destination: Path) -> None:
    if destination.exists():
        raise PackagedModelPartSmokeError("The package extraction target already exists.")
    destination.mkdir(parents=True)
    seen: set[str] = set()
    total_bytes = 0
    with zipfile.ZipFile(source, "r") as archive:
        entries = archive.infolist()
        if not entries or len(entries) > MAX_ARCHIVE_ENTRIES:
            raise PackagedModelPartSmokeError("The portable archive entry count is invalid.")
        for entry in entries:
            normalized = entry.filename.replace("\\", "/")
            path = PurePosixPath(normalized)
            if (
                not normalized
                or normalized.startswith("/")
                or path.is_absolute()
                or any(part in {"", ".", ".."} or ":" in part for part in path.parts)
            ):
                raise PackagedModelPartSmokeError("The portable archive contains an unsafe path.")
            key = normalized.rstrip("/").casefold()
            if key in seen:
                raise PackagedModelPartSmokeError("The portable archive contains duplicate paths.")
            seen.add(key)
            mode = (entry.external_attr >> 16) & 0o170000
            if mode == stat.S_IFLNK:
                raise PackagedModelPartSmokeError("The portable archive contains a link.")
            total_bytes += int(entry.file_size)
            if total_bytes > MAX_ARCHIVE_BYTES:
                raise PackagedModelPartSmokeError("The portable archive is too large.")
            target = destination.joinpath(*path.parts)
            if entry.is_dir() or normalized.endswith("/"):
                target.mkdir(parents=True, exist_ok=True)
                continue
            target.parent.mkdir(parents=True, exist_ok=True)
            with archive.open(entry, "r") as source_stream, target.open("xb") as target_stream:
                shutil.copyfileobj(source_stream, target_stream, length=1024 * 1024)


def _materialize_deterministic_unity_metas(editor_root: Path) -> None:
    if not editor_root.is_dir() or _is_reparse_point(editor_root):
        raise PackagedModelPartSmokeError("The copied Unity tool root is invalid.")
    assets: list[Path] = [editor_root]
    for current_root, directory_names, file_names in os.walk(
        editor_root, followlinks=False
    ):
        current = Path(current_root)
        for directory_name in sorted(directory_names):
            path = current / directory_name
            if _is_reparse_point(path):
                raise PackagedModelPartSmokeError("The copied Unity tool tree contains a link.")
            assets.append(path)
        for file_name in sorted(file_names):
            path = current / file_name
            if path.suffix.lower() != ".meta":
                assets.append(path)

    for asset in assets:
        relative = asset.relative_to(editor_root.parent).as_posix()
        meta_path = Path(str(asset) + ".meta")
        if meta_path.exists():
            _stable_digest(meta_path)
            continue
        guid = hashlib.sha256(
            ("vrcforge-fixed-unity-meta-v1:" + relative).encode("utf-8")
        ).hexdigest()[:32]
        if asset.is_dir():
            importer = "folderAsset: yes\nDefaultImporter:\n  externalObjects: {}"
        elif asset.suffix.lower() == ".cs":
            importer = (
                "MonoImporter:\n"
                "  externalObjects: {}\n"
                "  serializedVersion: 2\n"
                "  defaultReferences: []\n"
                "  executionOrder: 0\n"
                "  icon: {instanceID: 0}"
            )
        else:
            importer = "DefaultImporter:\n  externalObjects: {}"
        content = (
            f"fileFormatVersion: 2\nguid: {guid}\n{importer}\n"
            "  userData:\n  assetBundleName:\n  assetBundleVariant:\n"
        )
        try:
            with meta_path.open("x", encoding="utf-8", newline="\n") as handle:
                handle.write(content)
        except FileExistsError:
            pass
        _stable_digest(meta_path)


def _tree_digest(root: Path) -> str:
    if not root.is_dir() or _is_reparse_point(root):
        raise PackagedModelPartSmokeError("A bound tree root is invalid.")
    rows: list[dict[str, Any]] = []
    total_bytes = 0
    for current_root, directory_names, file_names in os.walk(root, followlinks=False):
        current = Path(current_root)
        for directory_name in list(directory_names):
            if _is_reparse_point(current / directory_name):
                raise PackagedModelPartSmokeError("A bound tree contains a link.")
        for file_name in sorted(file_names):
            path = current / file_name
            if _is_reparse_point(path) or not path.is_file():
                raise PackagedModelPartSmokeError("A bound tree contains an unsafe file.")
            size = path.stat().st_size
            total_bytes += size
            if len(rows) >= MAX_TREE_FILES or total_bytes > MAX_TREE_BYTES:
                raise PackagedModelPartSmokeError("A bound tree is too large.")
            rows.append(
                {
                    "path": path.relative_to(root).as_posix(),
                    "size": size,
                    "sha256": _stable_digest(path),
                }
            )
    rows.sort(key=lambda item: str(item["path"]))
    return _hash_json(rows)


def _stable_read(path: Path, maximum_size: int) -> bytes:
    if not path.is_file() or _is_reparse_point(path):
        raise PackagedModelPartSmokeError("A required fixed file is unavailable.")
    if path.stat().st_size > maximum_size:
        raise PackagedModelPartSmokeError("A required fixed file is too large.")
    try:
        with path.open("rb") as handle:
            before = os.fstat(handle.fileno())
            content = handle.read(maximum_size + 1)
            after = os.fstat(handle.fileno())
        current = path.stat()
    except OSError as exc:
        raise PackagedModelPartSmokeError("A required fixed file could not be read.") from exc
    if len(content) > maximum_size or _file_identity(before) != _file_identity(after) or _file_identity(after) != _file_identity(current):
        raise PackagedModelPartSmokeError("A required fixed file changed during reading.")
    return content


def _stable_digest(path: Path) -> str:
    if not path.is_file() or _is_reparse_point(path):
        raise PackagedModelPartSmokeError("A required fixed file is unavailable.")
    digest_value = hashlib.sha256()
    try:
        with path.open("rb") as handle:
            before = os.fstat(handle.fileno())
            for chunk in iter(lambda: handle.read(1024 * 1024), b""):
                digest_value.update(chunk)
            after = os.fstat(handle.fileno())
        current = path.stat()
    except OSError as exc:
        raise PackagedModelPartSmokeError("A required fixed file could not be hashed.") from exc
    if _file_identity(before) != _file_identity(after) or _file_identity(after) != _file_identity(current):
        raise PackagedModelPartSmokeError("A required fixed file changed during hashing.")
    return digest_value.hexdigest()


def _file_identity(value: os.stat_result) -> tuple[int, int, int, int]:
    return (value.st_dev, value.st_ino, value.st_size, value.st_mtime_ns)


def _is_reparse_point(path: Path) -> bool:
    try:
        info = path.lstat()
    except OSError:
        return False
    reparse_flag = getattr(stat, "FILE_ATTRIBUTE_REPARSE_POINT", 0x400)
    return path.is_symlink() or bool(getattr(info, "st_file_attributes", 0) & reparse_flag)


def _resolve_repository_file(path: Path) -> Path:
    candidate = path if path.is_absolute() else REPOSITORY_ROOT / path
    resolved = candidate.resolve(strict=True)
    if not resolved.is_relative_to(REPOSITORY_ROOT) or not resolved.is_file():
        raise PackagedModelPartSmokeError("The release manifest path is invalid.")
    return resolved


def _resolve_under_repository(path: Path) -> Path:
    candidate = path if path.is_absolute() else REPOSITORY_ROOT / path
    resolved = candidate.resolve(strict=False)
    if not resolved.is_relative_to(REPOSITORY_ROOT) or resolved == REPOSITORY_ROOT:
        raise PackagedModelPartSmokeError("The artifact root is invalid.")
    return resolved


def _git(*arguments: str) -> str:
    result = subprocess.run(
        ["git", "-C", str(REPOSITORY_ROOT), *arguments],
        check=False,
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        timeout=30,
    )
    if result.returncode != 0:
        raise PackagedModelPartSmokeError("A strict Git preflight failed.")
    return result.stdout.strip()


def _json_object(raw: bytes, label: str) -> dict[str, Any]:
    try:
        payload = json.loads(raw.decode("utf-8-sig"))
    except (UnicodeDecodeError, json.JSONDecodeError, RecursionError, ValueError) as exc:
        raise PackagedModelPartSmokeError(f"The {label} JSON is invalid.") from exc
    if not isinstance(payload, dict):
        raise PackagedModelPartSmokeError(f"The {label} JSON is invalid.")
    return payload


def _mapping(value: Any) -> Mapping[str, Any]:
    return value if isinstance(value, Mapping) else {}


def _require_safe_id(value: Any, label: str) -> str:
    text = str(value or "").strip()
    if not text or len(text) > 128 or any(
        not (character.isalnum() or character in "._-") for character in text
    ):
        raise PackagedModelPartSmokeError(f"The {label} id is invalid.")
    return text


def _is_sha256(value: str) -> bool:
    return len(value) == 64 and all(character in "0123456789abcdef" for character in value)


def _hash_json(value: Any) -> str:
    return hashlib.sha256(
        json.dumps(value, ensure_ascii=True, sort_keys=True, separators=(",", ":")).encode(
            "utf-8"
        )
    ).hexdigest()


def _port_open(port: int) -> bool:
    try:
        with socket.create_connection(("127.0.0.1", int(port)), timeout=0.25):
            return True
    except OSError:
        return False


def _port_released(port: int, label: str) -> bool:
    probe = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    try:
        if os.name == "nt":
            probe.setsockopt(socket.SOL_SOCKET, socket.SO_EXCLUSIVEADDRUSE, 1)
        probe.bind(("127.0.0.1", int(port)))
        return True
    except OSError as exc:
        code = int(getattr(exc, "winerror", 0) or getattr(exc, "errno", 0) or 0)
        if code in {48, 98, 10048}:
            return False
        raise PackagedModelPartSmokeError(
            f"The {label} port release could not be verified."
        ) from exc
    finally:
        probe.close()


def _require_port_released(port: int, label: str) -> None:
    if not _port_released(port, label):
        raise PackagedModelPartSmokeError(f"The {label} port is already in use.")


def _wait_for_port_released(port: int, label: str, *, timeout: float) -> None:
    deadline = time.monotonic() + max(1.0, float(timeout))
    while time.monotonic() < deadline:
        if _port_released(port, label):
            return
        time.sleep(0.25)
    if not _port_released(port, label):
        raise PackagedModelPartSmokeError(f"The {label} port did not close.")


def _post_close_to_process_windows(process_id: int) -> bool:
    if os.name != "nt" or process_id <= 0:
        return False
    user32 = ctypes.windll.user32
    posted = False
    enum_callback_type = ctypes.WINFUNCTYPE(ctypes.c_bool, ctypes.c_void_p, ctypes.c_void_p)

    def callback(window: int, _parameter: int) -> bool:
        nonlocal posted
        owner = ctypes.c_ulong()
        user32.GetWindowThreadProcessId(window, ctypes.byref(owner))
        if int(owner.value) == int(process_id) and user32.IsWindowVisible(window):
            if user32.PostMessageW(window, 0x0010, 0, 0):
                posted = True
        return True

    user32.EnumWindows(enum_callback_type(callback), 0)
    return posted


def _stop_owned_process(process: subprocess.Popen[bytes], label: str) -> list[str]:
    failures: list[str] = []

    def running() -> bool:
        try:
            return process.poll() is None
        except Exception as exc:  # noqa: BLE001 - keep escalating cleanup after a bad probe.
            failures.append(f"{label} state probe failed ({type(exc).__name__})")
            return True

    def wait_for_exit(timeout: float) -> bool:
        try:
            process.wait(timeout=timeout)
            return True
        except subprocess.TimeoutExpired:
            return False
        except Exception as exc:  # noqa: BLE001 - keep escalating cleanup after a bad wait.
            failures.append(f"{label} wait failed ({type(exc).__name__})")
            return False

    if not running():
        return failures

    try:
        _post_close_to_process_windows(process.pid)
    except Exception as exc:  # noqa: BLE001 - termination remains available.
        failures.append(f"{label} graceful close failed ({type(exc).__name__})")

    if wait_for_exit(20):
        return failures

    if running():
        try:
            process.terminate()
        except Exception as exc:  # noqa: BLE001 - force termination remains available.
            failures.append(f"{label} terminate failed ({type(exc).__name__})")
        if wait_for_exit(10):
            return failures

    if running():
        try:
            process.kill()
        except Exception as exc:  # noqa: BLE001 - final state check is still required.
            failures.append(f"{label} kill failed ({type(exc).__name__})")
        wait_for_exit(10)

    if running():
        failures.append(f"{label} remained running")
    return failures


def _safe_failure(exc: Exception) -> str:
    if isinstance(exc, PackagedModelPartSmokeError):
        return str(exc)[:240]
    return "The packaged model-part live run failed unexpectedly."


def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="microseconds").replace(
        "+00:00", "Z"
    )


if __name__ == "__main__":
    raise SystemExit(main())
