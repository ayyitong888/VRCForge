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
