from __future__ import annotations

import importlib.util
import subprocess
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
        return fake_read_only_request(base_url, method, path, token, payload, allow_http_error, timeout)

    report = smoke.GoldenPathMatrixSmoke(
        make_args(tmp_path, vsk_package=str(package)),
        request_func=fake_request,
    ).run()

    matrix = paths_by_id(report)
    assert report["ok"] is True
    assert matrix["vsk_import_dry_run_cleanup"]["status"] == "passed"
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
        if (method, path) == ("POST", "/api/app/skill-packages/import"):
            return {
                "ok": True,
                "imported": {"registry_entry": {"id": "com.example.helper", "enabled": True}},
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
        "vsk.import",
        "vsk.disable",
        "vsk.uninstall",
    ]
    assert ("PUT", "/api/app/skill-packages/com.example.helper", {"enabled": False, "syncProjectedSkill": True}) in calls
    assert ("DELETE", "/api/app/skill-packages/com.example.helper", {"removeProjectedSkill": True}) in calls


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
