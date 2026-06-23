from __future__ import annotations

import importlib.util
import json
import subprocess
from argparse import Namespace
from pathlib import Path
from types import ModuleType
from typing import Any


def load_sample_matrix_module() -> ModuleType:
    path = Path(__file__).resolve().parents[1] / "scripts" / "smoke_golden_path_sample_matrix.py"
    spec = importlib.util.spec_from_file_location("smoke_golden_path_sample_matrix", path)
    assert spec is not None
    assert spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def make_args(tmp_path: Path, case_file: Path, **overrides: Any) -> Namespace:
    values: dict[str, Any] = {
        "case_file": str(case_file),
        "base_url": "http://127.0.0.1:8757",
        "app_token_file": str(tmp_path / "app-session-token"),
        "python_exe": "python",
        "timeout": 30.0,
        "strict": False,
        "dry_run": False,
        "require_public_beta_coverage": False,
    }
    values.update(overrides)
    return Namespace(**values)


def write_case_file(tmp_path: Path, cases: list[dict[str, Any]]) -> Path:
    path = tmp_path / "sample-cases.json"
    path.write_text(json.dumps({"cases": cases}), encoding="utf-8")
    return path


def test_sample_matrix_dry_run_reports_commands_and_coverage(tmp_path: Path) -> None:
    module = load_sample_matrix_module()
    case_file = write_case_file(
        tmp_path,
        [
            {
                "id": "milltina-safe",
                "projectRoot": "E:/unity/milltina",
                "avatarPath": "Milltina",
                "coverage": {"shaderStacks": ["lilToon"]},
            },
            {
                "id": "negative-missing-dep",
                "notRun": True,
                "reason": "unsupported dependency sample",
                "coverage": {"negativeSamples": ["missing_dependency"]},
            },
        ],
    )

    report = module.GoldenPathSampleMatrix(make_args(tmp_path, case_file, dry_run=True)).run()

    assert report["ok"] is True
    assert report["schema"] == "vrcforge.golden_path_sample_matrix.v1"
    assert report["summary"]["counts"] == {"not-run": 2}
    assert report["summary"]["coverageCounts"]["avatars"] == 1
    assert report["summary"]["coverageCounts"]["shaderStacks"] == 1
    assert report["cases"][0]["command"][:2] == ["python", str(Path(__file__).resolve().parents[1] / "scripts" / "smoke_golden_path_matrix.py")]


def test_sample_matrix_executes_child_matrices_and_summarizes_failures(tmp_path: Path) -> None:
    module = load_sample_matrix_module()
    case_file = write_case_file(
        tmp_path,
        [
            {"id": "pass-case", "projectRoot": "E:/unity/milltina", "avatarPath": "Milltina"},
            {"id": "fail-case", "projectRoot": "E:/unity/bad", "avatarPath": "Bad"},
        ],
    )
    calls: list[list[str]] = []

    def fake_run(command: list[str], **kwargs: Any) -> subprocess.CompletedProcess[str]:
        calls.append(command)
        if "--project-root" in command and command[command.index("--project-root") + 1] == "E:/unity/bad":
            return subprocess.CompletedProcess(command, 1, stdout='{"ok": false, "summary": {"failedCount": 1}}', stderr="bad")
        return subprocess.CompletedProcess(command, 0, stdout='{"ok": true, "reportPath": "child.json", "summary": {"failedCount": 0}}', stderr="")

    report = module.GoldenPathSampleMatrix(make_args(tmp_path, case_file), run_command_func=fake_run).run()

    assert report["ok"] is False
    assert len(calls) == 2
    assert report["summary"]["counts"] == {"passed": 1, "failed": 1}
    assert report["summary"]["failedCases"] == ["fail-case"]
    assert report["cases"][0]["reportPath"] == "child.json"


def test_sample_matrix_strict_fails_required_not_run_case(tmp_path: Path) -> None:
    module = load_sample_matrix_module()
    case_file = write_case_file(tmp_path, [{"id": "installer-admin", "required": True, "notRun": True}])

    report = module.GoldenPathSampleMatrix(make_args(tmp_path, case_file, strict=True)).run()

    assert report["ok"] is False
    assert report["summary"]["failedCases"] == ["installer-admin"]


def test_sample_matrix_can_enforce_public_beta_coverage(tmp_path: Path) -> None:
    module = load_sample_matrix_module()
    case_file = write_case_file(
        tmp_path,
        [
            {
                "id": "thin-sample",
                "coverage": {
                    "avatars": ["A"],
                    "projects": ["P1"],
                    "outfits": ["O1"],
                    "shaderStacks": ["lilToon"],
                    "negativeSamples": ["missing_dependency"],
                },
            }
        ],
    )

    report = module.GoldenPathSampleMatrix(make_args(tmp_path, case_file, dry_run=True, require_public_beta_coverage=True)).run()

    assert report["ok"] is False
    assert {"category": "avatars", "required": 3, "actual": 1} in report["summary"]["coverageFailures"]
    assert {"category": "projects", "required": 2, "actual": 1} in report["summary"]["coverageFailures"]
    assert {"category": "outfits", "required": 3, "actual": 1} in report["summary"]["coverageFailures"]
