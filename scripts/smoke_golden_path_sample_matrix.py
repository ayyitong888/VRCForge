from __future__ import annotations

import argparse
import json
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable, Sequence

SCRIPTS_DIR = Path(__file__).resolve().parent
REPO_ROOT = SCRIPTS_DIR.parent

SCHEMA = "vrcforge.golden_path_sample_matrix.v1"

RunCommandFunc = Callable[..., subprocess.CompletedProcess[str]]


def main() -> int:
    args = parse_args()
    runner = GoldenPathSampleMatrix(args)
    report = runner.run()
    path = runner.write_report(report)
    print(json.dumps({"ok": report["ok"], "reportPath": str(path), "summary": report["summary"]}, ensure_ascii=True, indent=2))
    return 0 if report["ok"] else 1


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Run a multi-sample VRCForge 0.9 golden path matrix.")
    parser.add_argument("--case-file", required=True, help="JSON file containing a top-level cases array.")
    parser.add_argument("--base-url", default="http://127.0.0.1:8757")
    parser.add_argument("--app-token-file", default="")
    parser.add_argument("--python-exe", default=sys.executable)
    parser.add_argument("--timeout", type=float, default=180.0)
    parser.add_argument("--strict", action="store_true", help="Treat skipped optional rows in child matrices as failures.")
    parser.add_argument("--dry-run", action="store_true", help="Validate and report commands without executing child matrix runs.")
    parser.add_argument("--require-public-beta-coverage", action="store_true", help="Fail unless the matrix covers the 0.9 public-beta sample minimums.")
    return parser.parse_args()


class GoldenPathSampleMatrix:
    def __init__(self, args: argparse.Namespace, *, run_command_func: RunCommandFunc | None = None) -> None:
        self.args = args
        self.run_command_func = run_command_func or subprocess.run
        self.started_at = utc_now()
        self.run_id = f"golden-path-sample-matrix-{datetime.now(timezone.utc).strftime('%Y%m%d-%H%M%S')}"
        self.artifact_root = Path.cwd() / "artifacts" / "golden-path-sample-matrix"

    def run(self) -> dict[str, Any]:
        case_file = Path(self.args.case_file).expanduser().resolve()
        cases = load_cases(case_file)
        results = [self.run_case(case) for case in cases]
        summary = build_summary(results, strict=bool(self.args.strict), require_public_beta_coverage=bool(self.args.require_public_beta_coverage))
        return {
            "ok": summary["failedCount"] == 0,
            "schema": SCHEMA,
            "startedAt": self.started_at,
            "finishedAt": utc_now(),
            "artifactRunId": self.run_id,
            "caseFile": str(case_file),
            "dryRun": bool(self.args.dry_run),
            "strict": bool(self.args.strict),
            "cases": redact_evidence(results),
            "summary": summary,
        }

    def run_case(self, case: dict[str, Any]) -> dict[str, Any]:
        case_id = str(case.get("id") or "").strip()
        command = self.build_case_command(case)
        if not case_id:
            return {"id": "", "status": "failed", "ok": False, "error": "case id is required", "command": safe_command(command)}
        if self.args.dry_run or bool(case.get("notRun")):
            default_reason = "dry-run requested" if self.args.dry_run else "case marked notRun"
            reason = str(case.get("reason") or default_reason)
            return {
                "id": case_id,
                "label": case.get("label") or case_id,
                "status": "not-run",
                "ok": True,
                "required": bool(case.get("required", False)),
                "reason": reason,
                "coverage": normalize_coverage(case),
                "command": safe_command(command),
            }
        try:
            completed = self.run_command_func(
                command,
                cwd=str(REPO_ROOT),
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                text=True,
                encoding="utf-8",
                errors="replace",
                timeout=max(float(self.args.timeout), float(case.get("timeout", self.args.timeout))) + 60.0,
                check=False,
            )
        except Exception as exc:  # noqa: BLE001
            return {
                "id": case_id,
                "label": case.get("label") or case_id,
                "status": "failed",
                "ok": False,
                "required": bool(case.get("required", False)),
                "error": str(exc),
                "coverage": normalize_coverage(case),
                "command": safe_command(command),
            }
        child = parse_json_tail(completed.stdout)
        child_summary = ensure_dict(child.get("summary"))
        child_ok = completed.returncode == 0 and bool(child.get("ok", False))
        status = "passed" if child_ok else "failed"
        if not child and completed.returncode == 0:
            status = "blocked"
        return {
            "id": case_id,
            "label": case.get("label") or case_id,
            "status": status,
            "ok": child_ok,
            "required": bool(case.get("required", False)),
            "coverage": normalize_coverage(case),
            "reportPath": child.get("reportPath"),
            "childSummary": child_summary,
            "command": safe_command(command),
            "exitCode": completed.returncode,
            "stdoutTail": (completed.stdout or "")[-2000:],
            "stderrTail": (completed.stderr or "")[-2000:],
        }

    def build_case_command(self, case: dict[str, Any]) -> list[str]:
        command = [
            str(self.args.python_exe),
            str(SCRIPTS_DIR / "smoke_golden_path_matrix.py"),
            "--base-url",
            str(case.get("baseUrl") or self.args.base_url),
            "--timeout",
            str(float(case.get("timeout", self.args.timeout))),
        ]
        app_token_file = str(case.get("appTokenFile") or self.args.app_token_file or "").strip()
        if app_token_file:
            command += ["--app-token-file", app_token_file]
        add_arg(command, "--project-root", case.get("projectRoot"))
        add_arg(command, "--avatar-path", case.get("avatarPath"))
        add_arg(command, "--target-profile", case.get("targetProfile"))
        add_arg(command, "--outfit-package", case.get("outfitPackage"))
        add_arg(command, "--vsk-package", case.get("vskPackage"))
        add_arg(command, "--optimizer-tool", case.get("optimizerTool"))
        add_arg(command, "--renderer-path", case.get("rendererPath"))
        add_arg(command, "--shader-renderer-path", case.get("shaderRendererPath"))
        add_arg(command, "--shader-semantic-property", case.get("shaderSemanticProperty"))
        if case.get("shaderSlotIndex") is not None:
            command += ["--shader-slot-index", str(int(case["shaderSlotIndex"]))]
        if case.get("relativeVertexCount") is not None:
            command += ["--relative-vertex-count", str(float(case["relativeVertexCount"]))]
        for option in ensure_list(case.get("optimizerOptions")):
            command += ["--optimizer-option", str(option)]
        for material in ensure_list(case.get("materials")):
            command += ["--material", str(material)]
        if bool(case.get("includeLiveWrites")):
            command.append("--include-live-writes")
        if bool(case.get("includeExternalAgent")):
            command.append("--include-external-agent")
        if bool(case.get("includeVskImport")):
            command.append("--include-vsk-import")
        if bool(case.get("includeCli")):
            command.append("--include-cli")
        if bool(case.get("captureScreenshots")):
            command.append("--capture-screenshots")
        if bool(case.get("strict")) or bool(self.args.strict):
            command.append("--strict")
        return command

    def write_report(self, report: dict[str, Any]) -> Path:
        self.artifact_root.mkdir(parents=True, exist_ok=True)
        path = self.artifact_root / f"{self.run_id}.json"
        path.write_text(json.dumps(report, ensure_ascii=True, indent=2, sort_keys=True), encoding="utf-8")
        return path


def load_cases(path: Path) -> list[dict[str, Any]]:
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except OSError as exc:
        raise RuntimeError(f"Could not read case file: {path}") from exc
    except json.JSONDecodeError as exc:
        raise RuntimeError(f"Case file is not valid JSON: {path}") from exc
    raw_cases = payload.get("cases") if isinstance(payload, dict) else payload
    if not isinstance(raw_cases, list):
        raise RuntimeError("Case file must contain a top-level cases array or be an array.")
    cases = [case for case in raw_cases if isinstance(case, dict)]
    if len(cases) != len(raw_cases):
        raise RuntimeError("Every sample matrix case must be an object.")
    return cases


def build_summary(results: list[dict[str, Any]], *, strict: bool, require_public_beta_coverage: bool) -> dict[str, Any]:
    counts: dict[str, int] = {}
    failed: list[str] = []
    coverage: dict[str, set[str]] = {"avatars": set(), "projects": set(), "outfits": set(), "shaderStacks": set(), "negativeSamples": set()}
    for result in results:
        status = str(result.get("status") or "unknown")
        counts[status] = counts.get(status, 0) + 1
        if not bool(result.get("ok")) or (strict and status == "not-run" and bool(result.get("required"))):
            failed.append(str(result.get("id") or ""))
        case_coverage = ensure_dict(result.get("coverage"))
        for key in coverage:
            for value in ensure_list(case_coverage.get(key)):
                if value:
                    coverage[key].add(str(value))
    coverage_counts = {key: len(values) for key, values in coverage.items()}
    coverage_requirements = public_beta_coverage_requirements() if require_public_beta_coverage else {}
    coverage_failures = [
        {"category": key, "required": required, "actual": coverage_counts.get(key, 0)}
        for key, required in coverage_requirements.items()
        if coverage_counts.get(key, 0) < required
    ]
    if coverage_failures:
        failed.extend(f"coverage:{item['category']}" for item in coverage_failures)
    return {
        "status": "passed" if not failed else "failed",
        "caseCount": len(results),
        "counts": counts,
        "failedCount": len(failed),
        "failedCases": failed,
        "coverage": {key: sorted(values) for key, values in coverage.items()},
        "coverageCounts": coverage_counts,
        "coverageRequirements": coverage_requirements,
        "coverageFailures": coverage_failures,
    }


def public_beta_coverage_requirements() -> dict[str, int]:
    return {
        "avatars": 3,
        "projects": 2,
        "outfits": 3,
        "shaderStacks": 2,
        "negativeSamples": 1,
    }


def normalize_coverage(case: dict[str, Any]) -> dict[str, list[str]]:
    coverage = ensure_dict(case.get("coverage"))
    return {
        "avatars": normalize_list(coverage.get("avatars") or case.get("avatarPath")),
        "projects": normalize_list(coverage.get("projects") or case.get("projectRoot")),
        "outfits": normalize_list(coverage.get("outfits") or case.get("outfitPackage")),
        "shaderStacks": normalize_list(coverage.get("shaderStacks")),
        "negativeSamples": normalize_list(coverage.get("negativeSamples")),
    }


def normalize_list(value: Any) -> list[str]:
    if value is None:
        return []
    if isinstance(value, list):
        return [str(item) for item in value if str(item or "").strip()]
    text = str(value or "").strip()
    return [text] if text else []


def add_arg(command: list[str], flag: str, value: Any) -> None:
    if value is None:
        return
    text = str(value).strip()
    if text:
        command += [flag, text]


def ensure_dict(value: Any) -> dict[str, Any]:
    return value if isinstance(value, dict) else {}


def ensure_list(value: Any) -> list[Any]:
    return value if isinstance(value, list) else []


def parse_json_tail(text: str) -> dict[str, Any]:
    stripped = (text or "").strip()
    if not stripped:
        return {}
    for start in range(len(stripped)):
        if stripped[start] != "{":
            continue
        try:
            parsed = json.loads(stripped[start:])
        except json.JSONDecodeError:
            continue
        return parsed if isinstance(parsed, dict) else {}
    return {}


def safe_command(command: Sequence[str]) -> list[str]:
    redacted: list[str] = []
    redact_next = False
    for part in [str(item) for item in command]:
        if redact_next:
            redacted.append("[REDACTED]")
            redact_next = False
            continue
        redacted.append(part)
        if part in {"--token", "--app-token", "--gateway-token"}:
            redact_next = True
    return redacted


def redact_evidence(value: Any) -> Any:
    if isinstance(value, dict):
        return {key: ("[redacted]" if "token" in key.lower() else redact_evidence(item)) for key, item in value.items()}
    if isinstance(value, list):
        return [redact_evidence(item) for item in value]
    return value


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


if __name__ == "__main__":
    raise SystemExit(main())
