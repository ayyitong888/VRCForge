from __future__ import annotations

import importlib.util
import json
import zipfile
from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parents[1]


def load_script(name: str):
    path = REPO_ROOT / "scripts" / name
    spec = importlib.util.spec_from_file_location(path.stem, path)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def write_support_bundle(path: Path, *, diagnostics: dict) -> None:
    metadata = {
        "schema": "vrcforge.support-bundle.v1",
        "version": "1.1.2",
        "portableMode": True,
        "privacy": {"redactsSecrets": True, "includesFullPaths": False},
    }
    members = {
        "metadata.json": metadata,
        "bootstrap.json": {"ok": True},
        "doctor.json": {"ok": True},
        "diagnostics.json": diagnostics,
        "agent-audit.json": {"events": []},
        "checkpoints.json": {"items": []},
    }
    with zipfile.ZipFile(path, "w", compression=zipfile.ZIP_DEFLATED) as bundle:
        for name, payload in members.items():
            bundle.writestr(name, json.dumps(payload))


def test_support_bundle_validation_accepts_redacted_relative_evidence(tmp_path: Path) -> None:
    smoke = load_script("smoke_packaged_backend.py")
    bundle = tmp_path / "support.zip"
    write_support_bundle(bundle, diagnostics={"apiKey": "<redacted>", "log": "logs/backend.log"})

    result = smoke.validate_support_bundle(bundle, "1.1.2")

    assert result["ok"] is True
    assert result["privacyFindings"] == []


def test_support_bundle_validation_rejects_secret_and_user_path(tmp_path: Path) -> None:
    smoke = load_script("smoke_packaged_backend.py")
    bundle = tmp_path / "support.zip"
    write_support_bundle(
        bundle,
        diagnostics={
            "apiKey": "sk-" + "0123456789abcdefghijklmnop",
            "settingsPath": "C:\\Users\\Example\\AppData\\Local\\VRCForge\\settings.json",
        },
    )

    result = smoke.validate_support_bundle(bundle, "1.1.2")

    assert result["ok"] is False
    assert "diagnostics.json:token-pattern" in result["privacyFindings"]
    assert "diagnostics.json:absolute-user-path" in result["privacyFindings"]


def packaged_doctor_report(*, extra_status: str | None = None) -> dict:
    checks = [
        {
            "id": "desktop.install_integrity",
            "status": "ok",
            "fixable": False,
            "detail": {
                "schemaValid": True,
                "manifestVersionMatched": True,
                "versionFileMatched": True,
                "fileChecks": [],
            },
        }
    ]
    if extra_status is not None:
        checks.append(
            {
                "id": f"runtime.{extra_status}",
                "status": extra_status,
                "fixable": False,
                "detail": {},
            }
        )
    summary = {
        "okCount": sum(item["status"] == "ok" for item in checks),
        "warningCount": sum(item["status"] == "warning" for item in checks),
        "errorCount": sum(item["status"] == "error" for item in checks),
        "unknownCount": sum(item["status"] == "unknown" for item in checks),
    }
    return {
        "schema": "vrcforge.doctor.v1",
        "ok": summary["errorCount"] == 0,
        "summary": summary,
        "checks": checks,
    }


def test_packaged_doctor_allows_explicit_warning_but_rejects_any_error() -> None:
    smoke = load_script("smoke_packaged_backend.py")

    warning_ok, warning_evidence = smoke.evaluate_packaged_doctor(
        packaged_doctor_report(extra_status="warning")
    )
    error_ok, error_evidence = smoke.evaluate_packaged_doctor(
        packaged_doctor_report(extra_status="error")
    )

    assert warning_ok is True
    assert warning_evidence["summary"]["warningCount"] == 1
    assert error_ok is False
    assert error_evidence["summary"]["errorCount"] == 1


def test_packaged_cli_doctor_never_accepts_semantic_exit_two() -> None:
    smoke = load_script("smoke_packaged_backend.py")
    report = packaged_doctor_report(extra_status="error")
    payload = {
        "schema": "vrcforge.cli-doctor.v1",
        "report": report,
        "summary": report["summary"],
        "error": None,
        "exitCode": 2,
    }

    ok, evidence = smoke.evaluate_packaged_cli_doctor(payload, 2)

    assert ok is False
    assert evidence["expectedExitCode"] == 2
    assert evidence["errorFree"] is False


def test_packaged_runtime_probe_timeout_is_long_enough_but_bounded() -> None:
    smoke = load_script("smoke_packaged_backend.py")

    assert smoke.bounded_runtime_probe_timeout(1) == 30.0
    assert smoke.bounded_runtime_probe_timeout(60) == 60.0
    assert smoke.bounded_runtime_probe_timeout(600) == 120.0
    assert smoke.bounded_runtime_probe_timeout(float("nan")) == 60.0


def test_packaged_readiness_evidence_accepts_controlled_no_project_contract() -> None:
    smoke = load_script("smoke_packaged_backend.py")
    readiness_ok, readiness_evidence = smoke.evaluate_packaged_unity_readiness(
        {
            "ok": True,
            "schema": "vrcforge.unity_readiness_refresh.v1",
            "unityStatus": {
                "connected": False,
                "mcpServerReachable": False,
                "unityInstanceRegistered": False,
                "projectPath": "",
                "error": "No Unity project is selected.",
            },
        }
    )
    know_ok, know_evidence = smoke.evaluate_packaged_know_yourself(
        {
            "ok": True,
            "tool": "vrcforge_know_yourself",
            "result": {
                "ok": True,
                "schema": "vrcforge.know_yourself.v1",
                "readyForUnityWork": False,
                "provider": {"automaticTestCallMade": False},
                "projectContext": {"projectSelected": False},
                "operatingBoundaries": {
                    "skillMutatesUnityProject": False,
                    "skillInstallsDependencies": False,
                    "skillLaunchesOrClosesUnity": False,
                    "directUnityProjectWrites": False,
                },
            },
        }
    )

    assert readiness_ok is True
    assert readiness_evidence["projectPath"] == ""
    assert know_ok is True
    assert know_evidence["providerAutomaticTestCallMade"] is False
    assert know_evidence["readOnlyBoundaries"]["directUnityProjectWrites"] is False
    manifest_ok, manifest_evidence = smoke.evaluate_packaged_planning_manifest(
        {
            "ok": True,
            "enabled": True,
            "requiresToken": True,
            "allowWriteRequests": False,
            "exposureLayer": "planning",
            "writeTargets": [],
            "toolCount": 1,
            "tools": [{"name": "vrcforge_know_yourself", "write": False}],
        }
    )
    assert manifest_ok is True
    assert manifest_evidence["writeTargetsEmpty"] is True
    assert manifest_evidence["allToolsReadOnly"] is True


def test_packaged_readiness_evidence_fails_closed_for_invalid_result_or_token_leak() -> None:
    smoke = load_script("smoke_packaged_backend.py")
    invalid_ok, invalid_evidence = smoke.evaluate_packaged_know_yourself(
        {
            "ok": True,
            "tool": "vrcforge_know_yourself",
            "result": {
                "ok": True,
                "schema": "vrcforge.know_yourself.v1",
                "readyForUnityWork": True,
                "provider": {"automaticTestCallMade": False},
                "projectContext": {"projectSelected": False},
                "operatingBoundaries": {},
            },
        }
    )
    token = "probe-secret-token-0123456789"

    assert invalid_ok is False
    assert invalid_evidence["readyForUnityWork"] is True
    assert smoke.find_secret_leaks({"error": f"Bearer {token}"}, [token]) == ["secret-1"]


def test_packaged_planning_manifest_rejects_write_targets_and_write_tools() -> None:
    smoke = load_script("smoke_packaged_backend.py")
    base = {
        "ok": True,
        "enabled": True,
        "requiresToken": True,
        "allowWriteRequests": False,
        "exposureLayer": "planning",
        "writeTargets": [],
        "toolCount": 1,
        "tools": [{"name": "vrcforge_know_yourself", "write": False}],
    }

    targets_ok, targets_evidence = smoke.evaluate_packaged_planning_manifest(
        {**base, "writeTargets": [{"name": "unsafe"}]}
    )
    tools_ok, tools_evidence = smoke.evaluate_packaged_planning_manifest(
        {**base, "tools": [{"name": "vrcforge_know_yourself", "write": True}]}
    )

    assert targets_ok is False
    assert targets_evidence["writeTargetsEmpty"] is False
    assert tools_ok is False
    assert tools_evidence["allToolsReadOnly"] is False


def test_packaged_backend_environment_removes_all_inherited_vrcforge_controls() -> None:
    smoke = load_script("smoke_packaged_backend.py")
    environment = smoke.isolated_backend_environment(
        {"VRCFORGE_CONFIG_PATH": "isolated/config.json", "VRCFORGE_APP_SESSION_TOKEN": "owned"},
        {
            "PATH": "kept",
            "VRCFORGE_DISABLE_APP_AUTH": "1",
            "VRCFORGE_AGENT_START_RUNTIME": "1",
            "VRCFORGE_PRIMITIVE_LIVE_STDIN": "1",
            "vrcforge_untrusted_control": "1",
        },
    )

    assert environment == {
        "PATH": "kept",
        "VRCFORGE_CONFIG_PATH": "isolated/config.json",
        "VRCFORGE_APP_SESSION_TOKEN": "owned",
    }


def test_packaged_authentication_negatives_require_both_rejections() -> None:
    smoke = load_script("smoke_packaged_backend.py")

    ok, evidence = smoke.evaluate_authentication_negatives(
        {"status": 401, "body": "missing"},
        {"status": 403, "body": "wrong"},
    )
    accepted_wrong_token, accepted_evidence = smoke.evaluate_authentication_negatives(
        {"status": 401, "body": "missing"},
        {"status": 200, "body": "unexpectedly accepted"},
    )

    assert ok is True
    assert evidence == {"missingTokenStatus": 401, "wrongTokenStatus": 403}
    assert accepted_wrong_token is False
    assert accepted_evidence["wrongTokenStatus"] == 200


def test_token_privacy_covers_raw_doctor_cli_and_late_large_evidence(tmp_path: Path) -> None:
    smoke = load_script("smoke_packaged_backend.py")
    token = "late-shutdown-secret-token-0123456789"
    wrong_gateway_token = "wrong-gateway-token-0123456789"
    late_log = tmp_path / "backend-stderr.log"
    late_log.write_bytes(b"x" * (5 * 1024 * 1024 + 17) + token.encode("utf-8"))
    bundle_path = tmp_path / "support.zip"
    token_bytes = token.encode("utf-8")
    split = len(token_bytes) // 2
    large_member = (
        b"y" * (1024 * 1024 - split)
        + token_bytes
        + b"z" * (5 * 1024 * 1024)
    )
    with zipfile.ZipFile(bundle_path, "w", compression=zipfile.ZIP_DEFLATED) as bundle:
        bundle.writestr("large.log", large_member)

    raw_values = {
        "doctorRawBody": {"diagnostic": token},
        "cliPayload": {"detail": token},
        "cliStdout": token,
        "cliStderr": token,
        "httpFailureBody": token,
        "wrongGatewayFailureBody": wrong_gateway_token,
    }
    findings = smoke.collect_token_privacy_findings(
        raw_values=raw_values,
        evidence_files=[late_log],
        support_bundle=bundle_path,
        secrets_to_protect=[token, wrong_gateway_token],
    )
    with zipfile.ZipFile(bundle_path) as bundle:
        generic_findings = smoke.scan_support_bundle_privacy(bundle)

    assert "secret-1" in findings
    assert "secret-2" in findings
    assert "backend-stderr.log:secret-1" in findings
    assert "large.log:secret-1" in findings
    assert "large.log:privacy-scan-size-limit" in generic_findings
    for name, value in raw_values.items():
        expected = ["secret-2"] if name == "wrongGatewayFailureBody" else ["secret-1"]
        assert smoke.find_secret_leaks(
            {name: value},
            [token, wrong_gateway_token],
        ) == expected


def test_payload_zip_rejects_traversal_and_duplicate_members() -> None:
    smoke = load_script("smoke_payload_zip_unpack.py")
    infos = [
        zipfile.ZipInfo("../escape.txt"),
        zipfile.ZipInfo("dashboard/index.html"),
        zipfile.ZipInfo("DASHBOARD/index.html"),
    ]

    unsafe = smoke.unsafe_archive_members(infos)

    assert "../escape.txt" in unsafe
    assert "duplicate:DASHBOARD/index.html" in unsafe
