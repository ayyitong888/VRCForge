from __future__ import annotations

import hashlib
import importlib.util
import struct
import zlib
from pathlib import Path
from types import ModuleType


def load_optimizer_smoke_module() -> ModuleType:
    path = Path(__file__).resolve().parents[1] / "scripts" / "smoke_optimizer_apply_rollback.py"
    spec = importlib.util.spec_from_file_location("smoke_optimizer_apply_rollback", path)
    assert spec is not None
    assert spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def write_rgb_png(path: Path, width: int, height: int, pixels: list[tuple[int, int, int]]) -> None:
    assert len(pixels) == width * height
    rows = []
    for row_index in range(height):
        start = row_index * width
        row_pixels = pixels[start : start + width]
        rows.append(b"\x00" + bytes(channel for pixel in row_pixels for channel in pixel))
    ihdr = struct.pack(">IIBBBBB", width, height, 8, 2, 0, 0, 0)
    path.write_bytes(
        b"\x89PNG\r\n\x1a\n"
        + png_chunk(b"IHDR", ihdr)
        + png_chunk(b"IDAT", zlib.compress(b"".join(rows)))
        + png_chunk(b"IEND", b"")
    )


def png_chunk(kind: bytes, payload: bytes) -> bytes:
    crc = zlib.crc32(kind)
    crc = zlib.crc32(payload, crc) & 0xFFFFFFFF
    return struct.pack(">I", len(payload)) + kind + payload + struct.pack(">I", crc)


def test_visual_regression_artifact_records_screenshot_hashes(tmp_path: Path) -> None:
    module = load_optimizer_smoke_module()
    before = tmp_path / "before.png"
    after = tmp_path / "after.png"
    rollback = tmp_path / "rollback.png"
    write_rgb_png(before, 2, 1, [(0, 0, 0), (255, 255, 255)])
    write_rgb_png(after, 2, 1, [(0, 0, 0), (0, 255, 255)])
    write_rgb_png(rollback, 2, 1, [(0, 0, 0), (255, 255, 255)])

    artifact = module.build_visual_regression_artifact(
        [
            {"name": "screenshot.before", "captureOk": True, "artifactOk": True, "artifactImagePath": str(before)},
            {"name": "screenshot.after_apply", "captureOk": True, "artifactOk": True, "artifactImagePath": str(after)},
            {"name": "screenshot.after_rollback", "captureOk": True, "artifactOk": True, "artifactImagePath": str(rollback)},
        ],
        source="optimizer_apply_rollback",
        proof_passed=True,
    )

    assert artifact["schema"] == "vrcforge.visual_regression.v1"
    assert artifact["status"] == "captured"
    assert artifact["requiresHumanReview"] is True
    assert artifact["screenshots"]["before"]["sha256"] == hashlib.sha256(before.read_bytes()).hexdigest()
    assert artifact["screenshots"]["after_apply"]["image"]["width"] == 2
    assert artifact["screenshots"]["after_apply"]["image"]["height"] == 1

    scoring = artifact["scoring"]
    after_comparison = scoring["comparisons"]["afterApplyVsBefore"]
    rollback_comparison = scoring["comparisons"]["rollbackVsBefore"]
    assert scoring["mode"] == "deterministic-png-rgb-v1"
    assert scoring["comparable"] is True
    assert after_comparison["comparable"] is True
    assert after_comparison["changedPixelRatio"] == 0.5
    assert after_comparison["meanAbsoluteDifference"] == 0.1666666667
    assert rollback_comparison["comparable"] is True
    assert rollback_comparison["changedPixelRatio"] == 0.0
    assert scoring["rollbackVsBeforeSimilarity"] == 1.0


def test_visual_regression_artifact_marks_partial_and_skipped() -> None:
    module = load_optimizer_smoke_module()

    partial = module.build_visual_regression_artifact(
        [{"name": "screenshot.before", "captureOk": True, "artifactOk": False, "artifactError": "missing file"}],
        source="optimizer_apply_rollback",
        proof_passed=False,
    )
    skipped = module.build_visual_regression_artifact([], source="optimizer_apply_rollback", proof_passed=False)

    assert partial["status"] == "partial"
    assert partial["screenshots"]["before"]["warning"] == "missing file"
    assert partial["scoring"]["comparable"] is False
    assert skipped["status"] == "skipped"
    assert skipped["requiresHumanReview"] is False
    assert skipped["scoring"]["comparisons"]["afterApplyVsBefore"]["comparable"] is False


def test_visual_regression_artifact_marks_unreadable_png_without_failing(tmp_path: Path) -> None:
    module = load_optimizer_smoke_module()
    before = tmp_path / "before.png"
    after = tmp_path / "after.png"
    before.write_bytes(b"not a png")
    write_rgb_png(after, 1, 1, [(255, 0, 0)])

    artifact = module.build_visual_regression_artifact(
        [
            {"name": "screenshot.before", "captureOk": True, "artifactOk": True, "artifactImagePath": str(before)},
            {"name": "screenshot.after_apply", "captureOk": True, "artifactOk": True, "artifactImagePath": str(after)},
        ],
        source="optimizer_apply_rollback",
        proof_passed=False,
    )

    assert artifact["status"] == "partial"
    assert artifact["screenshots"]["before"]["exists"] is True
    assert artifact["screenshots"]["before"]["image"]["ok"] is False
    assert artifact["screenshots"]["before"]["image"]["error"] == "not a PNG file"
    assert artifact["scoring"]["comparisons"]["afterApplyVsBefore"] == {
        "comparable": False,
        "reason": "before_missing_or_unreadable",
    }


def test_delta_summary_keeps_profile_diff_and_parameter_delta() -> None:
    module = load_optimizer_smoke_module()

    summary = module.delta_summary(
        {
            "ok": True,
            "schema": "vrcforge.optimization.validation_delta.v1",
            "status": "improved",
            "findingDelta": {"addedCount": 0, "removedCount": 2},
            "rollbackProof": {"matchesBeforeSeverityAndGate": True},
            "profileDiff": {"pc": {"rankBefore": "Poor", "rankAfter": "Medium"}},
            "parameterBudgetDelta": {"syncedBitsDelta": -8},
        },
        require_rollback=True,
    )

    assert summary["ok"] is True
    assert summary["profileDiff"]["pc"]["rankAfter"] == "Medium"
    assert summary["parameterBudgetDelta"]["syncedBitsDelta"] == -8


def base_todo_rollback_audit() -> dict[str, object]:
    return {
        "ok": True,
        "schema": "vrcforge.rollback_coverage_audit.v1",
        "phase": "restore",
        "gateStatus": "todo",
        "pathspecs": ["Assets", "Packages", "ProjectSettings"],
        "checks": [
            {"id": "scene_prefab_component_state", "status": "covered"},
            {"id": "packages_manifest", "status": "covered"},
            {"id": "project_settings", "status": "covered"},
            {"id": "unity_reload_after_restore", "status": "passed"},
            {"id": "validation_after_restore", "status": "todo"},
        ],
        "blockingGaps": [],
        "todos": [{"id": "run_post_restore_validation", "status": "todo", "required": True}],
    }


def passing_validation_report() -> dict[str, object]:
    return {
        "ok": True,
        "schema": "vrcforge.validation.v1",
        "summary": {"gateStatus": "pass", "findingCount": 0, "severityCounts": {}},
        "gate": {"status": "pass"},
    }


def ready_build_test_readiness() -> dict[str, object]:
    return {
        "ok": True,
        "schema": "vrcforge.build_test_readiness.v1",
        "status": "ready",
        "gate": {"status": "pass"},
        "validationSummary": {"gateStatus": "pass", "findingCount": 0},
    }


def test_rollback_coverage_audit_stays_todo_without_post_restore_validation() -> None:
    module = load_optimizer_smoke_module()

    audit = module.attach_post_restore_validation_to_rollback_audit(base_todo_rollback_audit())
    summary = module.rollback_coverage_summary(audit)

    assert audit["schema"] == "vrcforge.rollback_coverage_audit.v1"
    assert audit["gateStatus"] == "todo"
    assert summary["ok"] is False
    assert any(item["id"] == "run_post_restore_validation" for item in audit["todos"])
    checks = {item["id"]: item for item in audit["checks"]}
    assert checks["validation_after_restore"]["status"] == "todo"


def test_rollback_coverage_audit_becomes_ready_with_validation_and_readiness() -> None:
    module = load_optimizer_smoke_module()

    audit = module.attach_post_restore_validation_to_rollback_audit(
        base_todo_rollback_audit(),
        validation=passing_validation_report(),
        readiness=ready_build_test_readiness(),
    )
    summary = module.rollback_coverage_summary(audit)

    assert audit["gateStatus"] == "ready"
    assert audit["todos"] == []
    assert audit["postRestoreValidation"]["status"] == "pass"
    assert audit["postRestoreReadiness"]["status"] == "pass"
    assert summary["ok"] is True
    checks = {item["id"]: item for item in audit["checks"]}
    assert checks["validation_after_restore"]["status"] == "passed"
    assert checks["build_test_readiness_after_restore"]["status"] == "passed"


def test_optimizer_cli_output_includes_rollback_coverage_audit(tmp_path: Path) -> None:
    module = load_optimizer_smoke_module()
    audit = module.attach_post_restore_validation_to_rollback_audit(
        base_todo_rollback_audit(),
        validation=passing_validation_report(),
        readiness=ready_build_test_readiness(),
    )

    output = module.build_cli_output(
        {"ok": True, "summary": {"status": "passed"}, "rollbackCoverageAudit": audit},
        tmp_path / "report.json",
    )

    assert output["ok"] is True
    assert output["rollbackCoverageAudit"]["schema"] == "vrcforge.rollback_coverage_audit.v1"
    assert output["rollbackCoverageAudit"]["gateStatus"] == "ready"
