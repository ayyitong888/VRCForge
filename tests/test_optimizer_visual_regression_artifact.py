from __future__ import annotations

import hashlib
import importlib.util
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


def test_visual_regression_artifact_records_screenshot_hashes(tmp_path: Path) -> None:
    module = load_optimizer_smoke_module()
    before = tmp_path / "before.png"
    after = tmp_path / "after.png"
    rollback = tmp_path / "rollback.png"
    before.write_bytes(b"before")
    after.write_bytes(b"after")
    rollback.write_bytes(b"rollback")

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
    assert artifact["screenshots"]["before"]["sha256"] == hashlib.sha256(b"before").hexdigest()
    assert artifact["screenshots"]["after_apply"]["size"] == len(b"after")
    assert artifact["scoring"]["mode"] == "not-run"


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
    assert skipped["status"] == "skipped"
    assert skipped["requiresHumanReview"] is False


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
