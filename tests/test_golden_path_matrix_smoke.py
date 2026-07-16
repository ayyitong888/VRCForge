from __future__ import annotations

import importlib.util
import hashlib
import json
import subprocess
import zipfile
from argparse import Namespace
from pathlib import Path
from types import ModuleType
from typing import Any


def load_smoke_module() -> ModuleType:
    path = Path(__file__).resolve().parents[1] / "scripts" / "smoke_golden_path_matrix.py"
    spec = importlib.util.spec_from_file_location("smoke_golden_path_matrix", path)
    assert spec is not None
    assert spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def make_args(tmp_path: Path, **overrides: Any) -> Namespace:
    token_file = tmp_path / "app-session-token"
    token_file.write_text("test-token", encoding="utf-8")
    values: dict[str, Any] = {
        "base_url": "http://127.0.0.1:8757",
        "app_token_file": str(token_file),
        "project_root": "",
        "avatar_path": "",
        "target_profile": "pc_conservative",
        "include_quest": True,
        "outfit_package": "",
        "outfit_target_folder": "Assets/VRCForge/ImportedOutfits/GoldenPath",
        "vsk_package": "",
        "release_manifest": str(tmp_path / "missing-release-manifest.json"),
        "include_cli": False,
        "include_external_agent": False,
        "include_live_writes": False,
        "include_vsk_import": False,
        "strict": False,
        "timeout": 30.0,
        "python_exe": "python",
        "optimizer_tool": "",
        "optimizer_option": [],
        "material": [],
        "renderer_path": "",
        "relative_vertex_count": None,
        "capture_screenshots": False,
        "shader_renderer_path": "",
        "shader_slot_index": None,
        "shader_semantic_property": "",
    }
    values.update(overrides)
    return Namespace(**values)


def write_runtime_binding_fixture(
    tmp_path: Path,
    *,
    archive_backend: bytes = b"packaged-backend",
    installed_backend: bytes | None = None,
) -> tuple[Path, Path, Path, Path, str]:
    release_root = tmp_path / "dist" / "release"
    release_root.mkdir(parents=True, exist_ok=True)
    program_dir = tmp_path / "dist" / "VRCForge_Windows_x64"
    backend_exe = program_dir / "backend" / "vrcforge_backend.exe"
    backend_exe.parent.mkdir(parents=True, exist_ok=True)
    backend_exe.write_bytes(archive_backend if installed_backend is None else installed_backend)
    payload_path = release_root / "VRCForge_Windows_x64_1.3.0.zip"
    with zipfile.ZipFile(payload_path, "w") as archive:
        archive.writestr("backend/vrcforge_backend.exe", archive_backend)
    expected_sha = hashlib.sha256(payload_path.read_bytes()).hexdigest()
    manifest_path = release_root / "release-manifest.json"
    manifest_path.write_text(
        json.dumps(
            {
                "version": "1.3.0",
                "commit": "a" * 40,
                "artifacts": [{"name": payload_path.name, "sha256": expected_sha}],
            }
        ),
        encoding="utf-8",
    )
    return manifest_path, payload_path, program_dir, backend_exe, expected_sha


def test_release_binding_hashes_manifest_adjacent_payload(tmp_path: Path) -> None:
    module = load_smoke_module()
    manifest_path, _payload_path, program_dir, backend_exe, expected_sha = write_runtime_binding_fixture(tmp_path)

    def fake_listener(port: int) -> list[dict[str, Any]]:
        assert port == 8757
        return [{"processId": 4242, "executable": str(backend_exe)}]

    binding = module.load_release_binding(
        manifest_path,
        "1.3.0",
        base_url="http://127.0.0.1:8757",
        runtime_payload={"health": {"portableMode": True, "paths": {"programDir": str(program_dir)}}},
        listener_query_func=fake_listener,
        platform_name="win32",
    )

    assert binding == {
        "version": "1.3.0",
        "manifestCommit": "a" * 40,
        "payloadZipSha256": expected_sha,
        "artifactLocationSafe": True,
        "runtimeProvenance": {
            "ok": True,
            "portableMode": True,
            "listenerUnique": True,
            "executableInsideProgramDir": True,
            "executableMatchesArchive": True,
            "executableName": "vrcforge_backend.exe",
            "hash": hashlib.sha256(b"packaged-backend").hexdigest(),
        },
        "payloadMatchesManifest": True,
    }
    assert set(binding["runtimeProvenance"]) == {
        "ok",
        "portableMode",
        "listenerUnique",
        "executableInsideProgramDir",
        "executableMatchesArchive",
        "executableName",
        "hash",
    }


def test_runtime_provenance_rejects_dev_or_external_listener(tmp_path: Path) -> None:
    module = load_smoke_module()
    _manifest_path, payload_path, program_dir, backend_exe, _expected_sha = write_runtime_binding_fixture(tmp_path)
    runtime_payload = {"health": {"portableMode": True, "paths": {"programDir": str(program_dir)}}}

    dev = module.build_runtime_provenance(
        base_url="http://127.0.0.1:8757",
        runtime_payload={"health": {"portableMode": False, "paths": {"programDir": str(program_dir)}}},
        payload_path=payload_path,
        listener_query_func=lambda _port: [{"processId": 1, "executable": str(backend_exe)}],
        platform_name="win32",
    )
    assert dev["ok"] is False
    assert dev["portableMode"] is False

    external_exe = tmp_path / "external" / "vrcforge_backend.exe"
    external_exe.parent.mkdir(parents=True)
    external_exe.write_bytes(backend_exe.read_bytes())
    external = module.build_runtime_provenance(
        base_url="http://127.0.0.1:8757",
        runtime_payload=runtime_payload,
        payload_path=payload_path,
        listener_query_func=lambda _port: [{"processId": 2, "executable": str(external_exe)}],
        platform_name="win32",
    )
    assert external["ok"] is False
    assert external["listenerUnique"] is True
    assert external["executableInsideProgramDir"] is False
    assert external["executableMatchesArchive"] is True


def test_runtime_provenance_rejects_backend_hash_mismatch(tmp_path: Path) -> None:
    module = load_smoke_module()
    _manifest_path, payload_path, program_dir, backend_exe, _expected_sha = write_runtime_binding_fixture(
        tmp_path,
        archive_backend=b"archive-backend",
        installed_backend=b"different-listener-backend",
    )

    provenance = module.build_runtime_provenance(
        base_url="http://127.0.0.1:8757",
        runtime_payload={"health": {"portableMode": True, "paths": {"programDir": str(program_dir)}}},
        payload_path=payload_path,
        listener_query_func=lambda _port: [{"processId": 3, "executable": str(backend_exe)}],
        platform_name="win32",
    )

    assert provenance["ok"] is False
    assert provenance["listenerUnique"] is True
    assert provenance["executableInsideProgramDir"] is True
    assert provenance["executableMatchesArchive"] is False
    assert provenance["hash"] == hashlib.sha256(b"different-listener-backend").hexdigest()


def test_runtime_provenance_fails_closed_for_platform_query_and_listener_ambiguity(tmp_path: Path) -> None:
    module = load_smoke_module()
    _manifest_path, payload_path, program_dir, backend_exe, _expected_sha = write_runtime_binding_fixture(tmp_path)
    runtime_payload = {"health": {"portableMode": True, "paths": {"programDir": str(program_dir)}}}

    non_windows = module.build_runtime_provenance(
        base_url="http://127.0.0.1:8757",
        runtime_payload=runtime_payload,
        payload_path=payload_path,
        listener_query_func=lambda _port: (_ for _ in ()).throw(AssertionError("must not query")),
        platform_name="linux",
    )
    assert non_windows["ok"] is False
    assert non_windows["listenerUnique"] is False

    remote_base_url = module.build_runtime_provenance(
        base_url="http://example.com:8757",
        runtime_payload=runtime_payload,
        payload_path=payload_path,
        listener_query_func=lambda _port: (_ for _ in ()).throw(AssertionError("must not query")),
        platform_name="win32",
    )
    assert remote_base_url["ok"] is False
    assert remote_base_url["listenerUnique"] is False

    query_failure = module.build_runtime_provenance(
        base_url="http://127.0.0.1:8757",
        runtime_payload=runtime_payload,
        payload_path=payload_path,
        listener_query_func=lambda _port: (_ for _ in ()).throw(RuntimeError("PowerShell failed")),
        platform_name="win32",
    )
    assert query_failure["ok"] is False
    assert query_failure["listenerUnique"] is False

    ambiguous = module.build_runtime_provenance(
        base_url="http://127.0.0.1:8757",
        runtime_payload=runtime_payload,
        payload_path=payload_path,
        listener_query_func=lambda _port: [
            {"processId": 4, "executable": str(backend_exe)},
            {"processId": 5, "executable": str(backend_exe)},
        ],
        platform_name="win32",
    )
    assert ambiguous["ok"] is False
    assert ambiguous["listenerUnique"] is False


def test_release_binding_rejects_runtime_or_payload_mismatch(tmp_path: Path) -> None:
    module = load_smoke_module()
    manifest_path = tmp_path / "release-manifest.json"
    manifest_path.write_text(
        json.dumps(
            {
                "version": "1.3.0",
                "commit": "b" * 40,
                "artifacts": [
                    {
                        "name": "VRCForge_Windows_x64_1.2.0.zip",
                        "sha256": "c" * 64,
                    }
                ],
            }
        ),
        encoding="utf-8",
    )

    binding = module.load_release_binding(manifest_path, "1.2.0")

    assert binding["version"] == "1.3.0"
    assert binding["artifactLocationSafe"] is True
    assert binding["payloadMatchesManifest"] is False


def test_release_binding_rejects_external_or_duplicate_manifest_entries(tmp_path: Path) -> None:
    module = load_smoke_module()
    release_root = tmp_path / "release"
    release_root.mkdir()
    payload_name = "VRCForge_Windows_x64_1.3.0.zip"
    payload_path = release_root / payload_name
    payload_path.write_bytes(b"fixed-release-payload")
    expected_sha = hashlib.sha256(payload_path.read_bytes()).hexdigest()
    manifest_path = release_root / "release-manifest.json"
    base = {
        "version": "1.3.0",
        "commit": "d" * 40,
    }

    manifest_path.write_text(
        json.dumps(
            {
                **base,
                "artifacts": [
                    {"name": payload_name, "path": f"../{payload_name}", "sha256": expected_sha}
                ],
            }
        ),
        encoding="utf-8",
    )
    external = module.load_release_binding(manifest_path, "1.3.0")
    assert external["artifactLocationSafe"] is False
    assert external["payloadMatchesManifest"] is False

    manifest_path.write_text(
        json.dumps(
            {
                **base,
                "artifacts": [
                    {"name": payload_name, "sha256": expected_sha},
                    {"name": payload_name, "sha256": expected_sha},
                ],
            }
        ),
        encoding="utf-8",
    )
    duplicate = module.load_release_binding(manifest_path, "1.3.0")
    assert duplicate["artifactLocationSafe"] is False
    assert duplicate["payloadMatchesManifest"] is False


def fake_read_only_request(
    base_url: str,
    method: str,
    path: str,
    token: str,
    payload: dict[str, Any] | None,
    allow_http_error: bool,
    timeout: float,
) -> dict[str, Any]:
    assert base_url == "http://127.0.0.1:8757"
    assert token == "test-token"
    assert allow_http_error is False
    assert timeout == 30.0
    if (method, path) == ("GET", "/api/app/bootstrap"):
        return {
            "ok": True,
            "app": {"version": "0.9.0-test"},
            "health": {
                "state": {"selected_project_path": "E:/unity/Hero"},
                "components": {"unity_mcp": {"status": "ok"}, "provider": {"status": "fallback"}},
            },
        }
    if (method, path) == ("GET", "/api/app/doctor"):
        return {"ok": True, "schema": "vrcforge.doctor.v1", "version": "0.9.0-test", "status": "ok"}
    if (method, path) == ("POST", "/api/app/avatars"):
        assert payload == {"projectPath": "E:/unity/Hero"}
        return {"ok": True, "avatars": [{"avatarPath": "Scene/Hero"}], "avatarCount": 1}
    if (method, path) == ("POST", "/api/app/validation/report"):
        assert payload is not None
        assert payload["projectPath"] == "E:/unity/Hero"
        assert payload["avatarPath"] == "Scene/Hero"
        return {
            "ok": True,
            "schema": "vrcforge.validation.v1",
            "summary": {"gateStatus": "pass", "findingCount": 0, "severityCounts": {}},
            "gate": {"status": "pass"},
        }
    if (method, path) == ("POST", "/api/app/optimization/plan"):
        assert payload is not None
        assert payload["projectPath"] == "E:/unity/Hero"
        assert payload["avatarPath"] == "Scene/Hero"
        return {
            "ok": True,
            "schema": "vrcforge.optimization.v1",
            "targetProfile": {"id": payload["targetProfile"]},
            "recommendedSteps": [{"id": "validation-first"}],
        }
    raise AssertionError(f"unexpected request: {method} {path} {payload}")


def paths_by_id(report: dict[str, Any]) -> dict[str, dict[str, Any]]:
    return {str(item["id"]): item for item in report["paths"]}


def fake_unity_unavailable_request(
    base_url: str,
    method: str,
    path: str,
    token: str,
    payload: dict[str, Any] | None,
    allow_http_error: bool,
    timeout: float,
) -> dict[str, Any]:
    if (method, path) == ("GET", "/api/app/bootstrap"):
        return {
            "ok": True,
            "app": {"version": "1.1.2"},
            "health": {
                "state": {"selected_project_path": "E:/unity/Hero"},
                "components": {
                    "backend": {"status": "ok"},
                    "unityMcpBridgeReachable": {"status": "warning", "message": "Unity MCP bridge is not reachable."},
                    "unityMcpInstance": {
                        "status": "warning",
                        "message": "No Unity instance is registered.",
                        "detail": {"activeInstanceCount": 0},
                    },
                },
            },
        }
    if (method, path) == ("GET", "/api/app/doctor"):
        return {
            "ok": False,
            "schema": "vrcforge.doctor.v1",
            "version": "1.1.2",
            "summary": {"errorCount": 1, "warningCount": 2, "unknownCount": 0},
            "checks": [
                {"id": "backend.online", "status": "ok"},
                {"id": "unity.mcp.bridge", "status": "warning", "message": "Unity MCP bridge is not reachable."},
                {"id": "unity.mcp.instance", "status": "warning", "message": "No Unity instance is registered."},
                {"id": "package.vrchat_sdk", "status": "error", "message": "VRChat SDK was not detected."},
            ],
        }
    raise AssertionError(f"Unity-dependent request should have been skipped: {method} {path} {payload}")


def test_default_matrix_runs_safe_paths_and_skips_live_writes(tmp_path: Path) -> None:
    smoke = load_smoke_module()
    run_calls: list[list[str]] = []

    def fake_run(command: list[str], **kwargs: Any) -> subprocess.CompletedProcess[str]:
        run_calls.append(command)
        return subprocess.CompletedProcess(command, 0, stdout='{"ok": true}', stderr="")

    report = smoke.GoldenPathMatrixSmoke(make_args(tmp_path), request_func=fake_read_only_request, run_command_func=fake_run).run()

    assert report["ok"] is True
    assert report["schema"] == "vrcforge.golden_path_matrix.v1"
    assert report["projectRoot"] == "E:/unity/Hero"
    assert report["avatarPath"] == "Scene/Hero"
    assert run_calls == []
    matrix = paths_by_id(report)
    assert matrix["install_doctor_provider_connect"]["status"] == "passed"
    assert matrix["scan_avatar_validation"]["status"] == "passed"
    assert matrix["model_optimization_validation_rollback"]["status"] == "passed"
    assert matrix["face_material_edit_checkpoint_rollback"]["status"] == "skipped"
    assert matrix["booth_outfit_import_validation_rollback"]["status"] == "skipped"
    assert matrix["external_agent_write_request_rollback"]["status"] == "skipped"
    assert matrix["vsk_import_dry_run_cleanup"]["status"] == "skipped"
    assert matrix["cli_doctor_readiness_checkpoint"]["status"] == "skipped"


def test_safe_default_skips_confirmed_unity_dependencies(tmp_path: Path) -> None:
    smoke = load_smoke_module()

    report = smoke.GoldenPathMatrixSmoke(make_args(tmp_path), request_func=fake_unity_unavailable_request).run()

    matrix = paths_by_id(report)
    assert report["ok"] is True
    assert report["summary"]["failedCount"] == 0
    assert matrix["install_doctor_provider_connect"]["status"] == "skipped"
    assert matrix["scan_avatar_validation"]["status"] == "skipped"
    assert matrix["model_optimization_validation_rollback"]["status"] == "skipped"
    doctor_step = next(step for step in matrix["install_doctor_provider_connect"]["steps"] if step["name"] == "doctor.report")
    assert doctor_step["reportOk"] is False
    assert doctor_step["contractError"] == ""


def test_strict_mode_still_fails_confirmed_unity_skips(tmp_path: Path) -> None:
    smoke = load_smoke_module()

    report = smoke.GoldenPathMatrixSmoke(
        make_args(tmp_path, strict=True),
        request_func=fake_unity_unavailable_request,
    ).run()

    matrix = paths_by_id(report)
    assert report["ok"] is False
    assert matrix["install_doctor_provider_connect"]["status"] == "skipped"
    assert matrix["install_doctor_provider_connect"]["ok"] is False
    assert "scan_avatar_validation" in report["summary"]["failedPaths"]
    assert "model_optimization_validation_rollback" in report["summary"]["failedPaths"]


def test_live_write_mode_does_not_downgrade_unity_failures(tmp_path: Path) -> None:
    smoke = load_smoke_module()
    calls: list[tuple[str, str]] = []

    def fake_request(*args: Any) -> dict[str, Any]:
        method = str(args[1])
        path = str(args[2])
        calls.append((method, path))
        if (method, path) in {("GET", "/api/app/bootstrap"), ("GET", "/api/app/doctor")}:
            return fake_unity_unavailable_request(*args)
        if (method, path) == ("POST", "/api/app/avatars"):
            raise RuntimeError("HTTP 503 from /api/app/avatars: No Unity instances connected")
        if (method, path) == ("POST", "/api/app/optimization/plan"):
            raise TimeoutError("timed out")
        raise AssertionError(f"unexpected request: {method} {path}")

    report = smoke.GoldenPathMatrixSmoke(
        make_args(tmp_path, include_live_writes=True),
        request_func=fake_request,
    ).run()

    matrix = paths_by_id(report)
    assert report["ok"] is False
    assert matrix["install_doctor_provider_connect"]["status"] == "failed"
    assert matrix["scan_avatar_validation"]["status"] == "failed"
    assert matrix["model_optimization_validation_rollback"]["status"] == "failed"
    assert ("POST", "/api/app/avatars") in calls
    assert ("POST", "/api/app/optimization/plan") in calls


def test_safe_default_does_not_swallow_generic_http_503(tmp_path: Path) -> None:
    smoke = load_smoke_module()

    def fake_request(
        base_url: str,
        method: str,
        path: str,
        token: str,
        payload: dict[str, Any] | None,
        allow_http_error: bool,
        timeout: float,
    ) -> dict[str, Any]:
        if (method, path) == ("GET", "/api/app/bootstrap"):
            return {
                "ok": True,
                "app": {"version": "1.1.2"},
                "health": {
                    "state": {"selected_project_path": "E:/unity/Hero"},
                    "components": {"unityMcpBridgeReachable": {"status": "unknown"}},
                },
            }
        if (method, path) == ("GET", "/api/app/doctor"):
            return {"ok": True, "schema": "vrcforge.doctor.v1", "version": "1.1.2", "checks": []}
        if (method, path) == ("POST", "/api/app/avatars"):
            raise RuntimeError("HTTP 503 from /api/app/avatars: backend database unavailable")
        if (method, path) == ("POST", "/api/app/optimization/plan"):
            return {
                "ok": True,
                "schema": "vrcforge.optimization.v1",
                "targetProfile": {"id": "pc_conservative"},
                "recommendedSteps": [],
            }
        raise AssertionError(f"unexpected request: {method} {path} {payload}")

    report = smoke.GoldenPathMatrixSmoke(make_args(tmp_path), request_func=fake_request).run()

    assert report["ok"] is False
    assert paths_by_id(report)["scan_avatar_validation"]["status"] == "failed"


def test_safe_default_accepts_exact_unity_http_503_as_unavailable(tmp_path: Path) -> None:
    smoke = load_smoke_module()

    def fake_request(
        base_url: str,
        method: str,
        path: str,
        token: str,
        payload: dict[str, Any] | None,
        allow_http_error: bool,
        timeout: float,
    ) -> dict[str, Any]:
        if (method, path) == ("GET", "/api/app/bootstrap"):
            return {
                "ok": True,
                "app": {"version": "1.1.2"},
                "health": {
                    "state": {"selected_project_path": "E:/unity/Hero"},
                    "components": {"unityMcpBridgeReachable": {"status": "unknown"}},
                },
            }
        if (method, path) == ("GET", "/api/app/doctor"):
            return {"ok": True, "schema": "vrcforge.doctor.v1", "version": "1.1.2", "checks": []}
        if (method, path) == ("POST", "/api/app/avatars"):
            raise RuntimeError("HTTP 503 from /api/app/avatars: Unity MCP server is not ready yet")
        raise AssertionError(f"request after Unity 503 should have been skipped: {method} {path} {payload}")

    report = smoke.GoldenPathMatrixSmoke(make_args(tmp_path), request_func=fake_request).run()

    matrix = paths_by_id(report)
    assert report["ok"] is True
    assert matrix["scan_avatar_validation"]["status"] == "skipped"
    assert matrix["model_optimization_validation_rollback"]["status"] == "skipped"


def test_safe_default_does_not_swallow_doctor_version_mismatch(tmp_path: Path) -> None:
    smoke = load_smoke_module()

    def fake_request(*args: Any) -> dict[str, Any]:
        payload = fake_unity_unavailable_request(*args)
        if (str(args[1]), str(args[2])) == ("GET", "/api/app/doctor"):
            payload["version"] = "1.1.3"
        return payload

    report = smoke.GoldenPathMatrixSmoke(make_args(tmp_path), request_func=fake_request).run()

    matrix = paths_by_id(report)
    assert report["ok"] is False
    assert matrix["install_doctor_provider_connect"]["status"] == "failed"
    doctor_step = next(step for step in matrix["install_doctor_provider_connect"]["steps"] if step["name"] == "doctor.report")
    assert "version mismatch" in doctor_step["contractError"]
    assert matrix["scan_avatar_validation"]["status"] == "skipped"


def test_safe_default_does_not_swallow_doctor_backend_error(tmp_path: Path) -> None:
    smoke = load_smoke_module()

    def fake_request(*args: Any) -> dict[str, Any]:
        payload = fake_unity_unavailable_request(*args)
        if (str(args[1]), str(args[2])) == ("GET", "/api/app/doctor"):
            payload["checks"].append({"id": "backend.online", "status": "error", "message": "Backend state store failed."})
        return payload

    report = smoke.GoldenPathMatrixSmoke(make_args(tmp_path), request_func=fake_request).run()

    matrix = paths_by_id(report)
    assert report["ok"] is False
    assert matrix["install_doctor_provider_connect"]["status"] == "failed"
    doctor_step = next(step for step in matrix["install_doctor_provider_connect"]["steps"] if step["name"] == "doctor.report")
    assert "non-Unity error" in doctor_step["contractError"]


def test_unity_bridge_protocol_error_is_not_treated_as_normal_absence() -> None:
    smoke = load_smoke_module()

    reason = smoke.confirmed_unity_unavailable_from_components(
        {
            "unityMcpBridgeReachable": {
                "status": "error",
                "message": "Unity MCP authentication failed with an incompatible protocol response.",
            }
        }
    )

    assert reason == ""


def test_validation_summary_requires_explicit_pass_and_ok_true() -> None:
    smoke = load_smoke_module()

    assert smoke.validation_summary(
        {"ok": True, "schema": "vrcforge.validation.v1", "summary": {"gateStatus": "pass"}}
    )["ok"] is True
    assert smoke.validation_summary(
        {"ok": False, "schema": "vrcforge.validation.v1", "summary": {"gateStatus": "pass"}}
    )["ok"] is False
    assert smoke.validation_summary(
        {"ok": True, "schema": "vrcforge.validation.v1", "summary": {"gateStatus": "failed"}}
    )["ok"] is False
    assert smoke.validation_summary(
        {"ok": True, "schema": "vrcforge.validation.v1", "summary": {}}
    )["ok"] is False


def test_strict_mode_treats_skipped_paths_as_failures(tmp_path: Path) -> None:
    smoke = load_smoke_module()

    report = smoke.GoldenPathMatrixSmoke(
        make_args(tmp_path, strict=True),
        request_func=fake_read_only_request,
    ).run()

    assert report["ok"] is False
    assert report["summary"]["failedCount"] > 0
    assert "face_material_edit_checkpoint_rollback" in report["summary"]["failedPaths"]
    assert "cli_doctor_readiness_checkpoint" in report["summary"]["failedPaths"]


def test_vsk_package_preflight_skips_import_by_default(tmp_path: Path) -> None:
    smoke = load_smoke_module()
    package = tmp_path / "helper.vsk"
    package.write_bytes(b"fixture")

    def fake_request(
        base_url: str,
        method: str,
        path: str,
        token: str,
        payload: dict[str, Any] | None,
        allow_http_error: bool,
        timeout: float,
    ) -> dict[str, Any]:
        if (method, path) == ("POST", "/api/app/skill-packages/preflight"):
            assert payload == {"packagePath": str(package)}
            return {
                "ok": True,
                "preview": {
                    "id": "com.example.helper",
                    "name": "Helper",
                    "version": "1.0.0",
                    "riskLevel": "low",
                    "updateAction": "new",
                },
            }
        if (method, path) == ("GET", "/api/app/skill-packages"):
            assert payload is None
            return {"ok": True, "installed": []}
        if (method, path) == ("POST", "/api/app/skill-packages/import"):
            assert payload == {"packagePath": str(package), "dryRun": True}
            return {
                "ok": True,
                "dryRun": True,
                "preview": {"dryRun": {"willWrite": False}},
            }
        return fake_read_only_request(base_url, method, path, token, payload, allow_http_error, timeout)

    report = smoke.GoldenPathMatrixSmoke(
        make_args(tmp_path, vsk_package=str(package)),
        request_func=fake_request,
    ).run()

    matrix = paths_by_id(report)
    assert report["ok"] is True
    assert matrix["vsk_import_dry_run_cleanup"]["status"] == "passed"
    assert matrix["vsk_import_dry_run_cleanup"]["steps"][1] == {
        "name": "vsk.dry_run",
        "ok": True,
        "dryRun": True,
        "willWrite": False,
        "registryUnchanged": True,
    }
    assert matrix["vsk_import_dry_run_cleanup"]["steps"][-1]["name"] == "vsk.import_cleanup"
    assert matrix["vsk_import_dry_run_cleanup"]["steps"][-1]["status"] == "skipped"


def test_vsk_import_mode_disables_and_uninstalls_package(tmp_path: Path) -> None:
    smoke = load_smoke_module()
    package = tmp_path / "helper.vsk"
    package.write_bytes(b"fixture")
    calls: list[tuple[str, str, dict[str, Any] | None]] = []

    def fake_request(
        base_url: str,
        method: str,
        path: str,
        token: str,
        payload: dict[str, Any] | None,
        allow_http_error: bool,
        timeout: float,
    ) -> dict[str, Any]:
        calls.append((method, path, payload))
        if (method, path) == ("POST", "/api/app/skill-packages/preflight"):
            return {
                "ok": True,
                "preview": {
                    "id": "com.example.helper",
                    "name": "Helper",
                    "version": "1.0.0",
                    "riskLevel": "low",
                    "updateAction": "new",
                },
            }
        if (method, path) == ("GET", "/api/app/skill-packages"):
            return {"ok": True, "installed": []}
        if (method, path) == ("POST", "/api/app/skill-packages/import"):
            if payload == {"packagePath": str(package), "dryRun": True}:
                return {
                    "ok": True,
                    "dryRun": True,
                    "preview": {"dryRun": {"willWrite": False}},
                }
            assert payload == {"packagePath": str(package)}
            return {
                "ok": True,
                "imported": {
                    "changed": True,
                    "registry_entry": {"id": "com.example.helper", "enabled": True},
                },
                "projectedSkill": {"name": "helper"},
            }
        if (method, path) == ("PUT", "/api/app/skill-packages/com.example.helper"):
            assert payload == {"enabled": False, "syncProjectedSkill": True}
            return {
                "ok": True,
                "state": {"registry_entry": {"id": "com.example.helper", "enabled": False}},
                "projectedSkill": {"name": "helper"},
            }
        if (method, path) == ("DELETE", "/api/app/skill-packages/com.example.helper"):
            assert payload == {"removeProjectedSkill": True}
            return {
                "ok": True,
                "uninstalled": {"skill_id": "com.example.helper"},
                "projectedSkill": {"deleted": "helper"},
            }
        return fake_read_only_request(base_url, method, path, token, payload, allow_http_error, timeout)

    report = smoke.GoldenPathMatrixSmoke(
        make_args(tmp_path, vsk_package=str(package), include_vsk_import=True),
        request_func=fake_request,
    ).run()

    matrix = paths_by_id(report)
    assert report["ok"] is True
    assert matrix["vsk_import_dry_run_cleanup"]["status"] == "passed"
    assert [step["name"] for step in matrix["vsk_import_dry_run_cleanup"]["steps"]] == [
        "vsk.preflight",
        "vsk.dry_run",
        "vsk.import",
        "vsk.disable",
        "vsk.uninstall",
    ]
    assert matrix["vsk_import_dry_run_cleanup"]["steps"][-1]["absentAfterReadback"] is True
    assert ("POST", "/api/app/skill-packages/import", {"packagePath": str(package), "dryRun": True}) in calls
    assert ("PUT", "/api/app/skill-packages/com.example.helper", {"enabled": False, "syncProjectedSkill": True}) in calls
    assert ("DELETE", "/api/app/skill-packages/com.example.helper", {"removeProjectedSkill": True}) in calls


def test_vsk_import_refuses_to_touch_preexisting_package(tmp_path: Path) -> None:
    smoke = load_smoke_module()
    package = tmp_path / "helper.vsk"
    package.write_bytes(b"fixture")
    calls: list[tuple[str, str]] = []

    def fake_request(
        base_url: str,
        method: str,
        path: str,
        token: str,
        payload: dict[str, Any] | None,
        allow_http_error: bool,
        timeout: float,
    ) -> dict[str, Any]:
        calls.append((method, path))
        if (method, path) == ("POST", "/api/app/skill-packages/preflight"):
            return {"ok": True, "preview": {"id": "com.example.helper", "name": "Helper"}}
        if (method, path) == ("GET", "/api/app/skill-packages"):
            return {"ok": True, "installed": [{"id": "com.example.helper", "enabled": True}]}
        raise AssertionError(f"Unexpected request: {(method, path)}")

    report = smoke.GoldenPathMatrixSmoke(
        make_args(tmp_path, vsk_package=str(package), include_vsk_import=True),
        request_func=fake_request,
    ).run()

    matrix = paths_by_id(report)
    row = matrix["vsk_import_dry_run_cleanup"]
    assert row["status"] == "failed"
    assert row["steps"][-1]["name"] == "vsk.preexisting_guard"
    assert not any(method in {"PUT", "DELETE"} for method, _path in calls)


def test_vsk_import_disable_failure_still_uninstalls_and_verifies_absence(tmp_path: Path) -> None:
    smoke = load_smoke_module()
    package = tmp_path / "helper.vsk"
    package.write_bytes(b"fixture")
    installed = False
    delete_calls = 0

    def fake_request(
        base_url: str,
        method: str,
        path: str,
        token: str,
        payload: dict[str, Any] | None,
        allow_http_error: bool,
        timeout: float,
    ) -> dict[str, Any]:
        nonlocal installed, delete_calls
        if (method, path) == ("POST", "/api/app/skill-packages/preflight"):
            return {"ok": True, "preview": {"id": "com.example.helper", "name": "Helper"}}
        if (method, path) == ("GET", "/api/app/skill-packages"):
            items = [{"id": "com.example.helper", "enabled": True}] if installed else []
            return {"ok": True, "installed": items}
        if (method, path) == ("POST", "/api/app/skill-packages/import"):
            if payload and payload.get("dryRun") is True:
                return {"ok": True, "dryRun": True, "preview": {"dryRun": {"willWrite": False}}}
            installed = True
            return {
                "ok": True,
                "imported": {
                    "changed": True,
                    "registry_entry": {"id": "com.example.helper", "enabled": True},
                },
                "projectedSkill": {"name": "helper"},
            }
        if (method, path) == ("PUT", "/api/app/skill-packages/com.example.helper"):
            raise RuntimeError("simulated disable failure")
        if (method, path) == ("DELETE", "/api/app/skill-packages/com.example.helper"):
            delete_calls += 1
            installed = False
            return {"ok": True, "uninstalled": {"skill_id": "com.example.helper"}}
        raise AssertionError(f"Unexpected request: {(method, path)}")

    report = smoke.GoldenPathMatrixSmoke(
        make_args(tmp_path, vsk_package=str(package), include_vsk_import=True),
        request_func=fake_request,
    ).run()

    matrix = paths_by_id(report)
    row = matrix["vsk_import_dry_run_cleanup"]
    assert row["status"] == "failed"
    assert delete_calls == 1
    assert installed is False
    assert row["steps"][-1]["name"] == "vsk.uninstall"
    assert row["steps"][-1]["ok"] is True
    assert row["steps"][-1]["absentAfterReadback"] is True


def test_live_write_flag_invokes_existing_shader_and_optimizer_smokes(tmp_path: Path) -> None:
    smoke = load_smoke_module()
    run_calls: list[list[str]] = []

    def fake_run(command: list[str], **kwargs: Any) -> subprocess.CompletedProcess[str]:
        run_calls.append(command)
        if any(str(part).endswith("smoke_optimizer_apply_rollback.py") for part in command):
            return subprocess.CompletedProcess(
                command,
                0,
                stdout='{"ok": true, "summary": {"status": "passed"}, "rollbackCoverageAudit": {"schema": "vrcforge.rollback_coverage_audit.v1", "gateStatus": "ready"}}',
                stderr="",
            )
        return subprocess.CompletedProcess(command, 0, stdout='{"ok": true, "summary": {"status": "passed"}}', stderr="")

    report = smoke.GoldenPathMatrixSmoke(
        make_args(
            tmp_path,
            avatar_path="Scene/Hero",
            include_live_writes=True,
            optimizer_tool="optimization.lac.apply-request",
        ),
        request_func=fake_read_only_request,
        run_command_func=fake_run,
    ).run()

    called_scripts = {Path(part).name for command in run_calls for part in command if str(part).endswith(".py")}
    assert report["ok"] is True
    assert "smoke_shader_adapter_apply_rollback.py" in called_scripts
    assert "smoke_optimizer_apply_rollback.py" in called_scripts
    optimizer_steps = [
        step
        for step in paths_by_id(report)["model_optimization_validation_rollback"]["steps"]
        if step["name"] == "smoke_optimizer_apply_rollback.py"
    ]
    assert optimizer_steps[0]["rollbackCoverageAudit"]["schema"] == "vrcforge.rollback_coverage_audit.v1"
    assert optimizer_steps[0]["rollbackCoverageGateStatus"] == "ready"


def test_cli_matrix_passes_app_token_and_previews_checkpoint(tmp_path: Path) -> None:
    smoke = load_smoke_module()
    run_calls: list[list[str]] = []

    def fake_run(command: list[str], **kwargs: Any) -> subprocess.CompletedProcess[str]:
        run_calls.append(command)
        if command[-2:] == ["checkpoint", "list"] or "list" in command:
            return subprocess.CompletedProcess(command, 0, stdout='{"ok": true, "checkpoints": [{"id": "ckpt_test"}]}', stderr="")
        return subprocess.CompletedProcess(command, 0, stdout='{"ok": true}', stderr="")

    report = smoke.GoldenPathMatrixSmoke(
        make_args(tmp_path, include_cli=True),
        request_func=fake_read_only_request,
        run_command_func=fake_run,
    ).run()

    assert report["ok"] is True
    assert run_calls
    assert all("--token" in command and "test-token" in command for command in run_calls)
    assert any(command[-3:] == ["checkpoint", "preview", "ckpt_test"] for command in run_calls)


def test_matrix_selects_explicit_project_before_health_checks(tmp_path: Path) -> None:
    smoke = load_smoke_module()
    project = tmp_path / "UnityProject"
    calls: list[tuple[str, str, dict[str, Any] | None]] = []

    def fake_request(
        base_url: str,
        method: str,
        path: str,
        token: str,
        payload: dict[str, Any] | None,
        allow_http_error: bool,
        timeout: float,
    ) -> dict[str, Any]:
        calls.append((method, path, payload))
        assert base_url == "http://127.0.0.1:8757"
        assert token == "test-token"
        assert allow_http_error is False
        assert timeout == 30.0
        assert (method, path) == ("POST", "/api/state")
        assert payload == {"projectPath": str(project.resolve())}
        return {"selectedProjectPath": str(project.resolve())}

    matrix = smoke.GoldenPathMatrixSmoke(make_args(tmp_path, project_root=str(project)), request_func=fake_request)
    step = matrix.select_project()

    assert step is not None
    assert step["ok"] is True
    assert calls == [("POST", "/api/state", {"projectPath": str(project.resolve())})]
    assert matrix.project_root == str(project.resolve())


def test_matrix_resolves_package_paths_for_packaged_runtime(tmp_path: Path) -> None:
    smoke = load_smoke_module()
    vsk_package = tmp_path / "helper.vsk"
    outfit_package = tmp_path / "outfit.zip"

    matrix = smoke.GoldenPathMatrixSmoke(
        make_args(tmp_path, vsk_package=str(vsk_package), outfit_package=str(outfit_package)),
        request_func=fake_read_only_request,
    )

    assert matrix.vsk_package == str(vsk_package.resolve())
    assert matrix.outfit_package == str(outfit_package.resolve())
