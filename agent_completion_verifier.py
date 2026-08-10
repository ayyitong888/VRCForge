from __future__ import annotations

from collections.abc import Callable, Mapping
import hashlib
import json
import time
from typing import Any


CONSOLE_VERIFICATION_PROFILES = frozenset(
    {
        "persisted_scene_write_console",
    }
)


class AgentCompletionVerificationError(RuntimeError):
    pass


class UnityConsoleCompletionVerifier:
    """Bounded, read-only verifier owned by one approved Unity write."""

    def __init__(
        self,
        read_diagnostics: Callable[[dict[str, Any]], Mapping[str, Any]],
        *,
        timeout_seconds: float = 20.0,
        poll_seconds: float = 0.25,
        monotonic: Callable[[], float] = time.monotonic,
        sleep: Callable[[float], None] = time.sleep,
    ) -> None:
        self._read_diagnostics = read_diagnostics
        self._timeout_seconds = max(0.1, float(timeout_seconds))
        self._poll_seconds = max(0.01, float(poll_seconds))
        self._monotonic = monotonic
        self._sleep = sleep

    def capture_baseline(self, profile: str, arguments: Mapping[str, Any]) -> dict[str, Any]:
        if profile not in CONSOLE_VERIFICATION_PROFILES:
            raise AgentCompletionVerificationError(f"Unsupported completion verification profile: {profile}")
        snapshot = self._read_snapshot(
            arguments,
            timeout_seconds=self._timeout_seconds,
        )
        if (
            not snapshot["readable"]
            or snapshot["isCompiling"]
            or not snapshot["captureComplete"]
            or not _identity_complete(snapshot)
        ):
            raise AgentCompletionVerificationError(
                "Unity compile diagnostics are not stable enough to start the approved write."
            )
        if snapshot["truncated"]:
            raise AgentCompletionVerificationError(
                "Unity compile diagnostics were truncated before the approved write."
            )
        return {
            "schema": "vrcforge.unity_console_baseline.v1",
            "projectPathDigest": snapshot["projectPathDigest"],
            "unityProcessId": snapshot["unityProcessId"],
            "unityProcessStartedAtUtc": snapshot["unityProcessStartedAtUtc"],
            "unityExecutableDigest": snapshot["unityExecutableDigest"],
            "diagnosticIds": snapshot["diagnosticIds"],
            "capturedAt": snapshot["capturedAt"],
        }

    def finalize(
        self,
        profile: str,
        arguments: Mapping[str, Any],
        baseline: Mapping[str, Any],
        raw_result: Any,
    ) -> dict[str, Any]:
        result = dict(raw_result) if isinstance(raw_result, Mapping) else {"result": raw_result}
        deadline = self._monotonic() + self._timeout_seconds
        stable_snapshot: dict[str, Any] | None = None
        stable_digest = ""
        stable_count = 0
        while True:
            remaining = deadline - self._monotonic()
            if remaining < 1.0:
                break
            snapshot = self._read_snapshot(
                arguments,
                timeout_seconds=remaining,
            )
            if (
                snapshot["readable"]
                and not snapshot["isCompiling"]
                and snapshot["captureComplete"]
                and not snapshot["truncated"]
                and _identity_complete(snapshot)
            ):
                digest = _stable_snapshot_digest(snapshot)
                if digest == stable_digest:
                    stable_count += 1
                else:
                    stable_digest = digest
                    stable_count = 1
                stable_snapshot = snapshot
                if stable_count >= 2:
                    break
            else:
                stable_count = 0
                stable_digest = ""
            sleep_seconds = min(
                self._poll_seconds,
                max(0.0, deadline - self._monotonic()),
            )
            if sleep_seconds <= 0:
                break
            self._sleep(sleep_seconds)

        baseline_ids = {
            str(item) for item in baseline.get("diagnosticIds", []) if str(item).strip()
        }
        if stable_snapshot is None or stable_count < 2:
            return _attach_console_verification(
                result,
                profile,
                passed=False,
                code="unity_console_unstable",
                summary="Unity compilation did not reach a stable readable state after the write.",
            )
        expected_project = str(baseline.get("projectPathDigest") or "")
        actual_project = str(stable_snapshot.get("projectPathDigest") or "")
        if expected_project != actual_project:
            return _attach_console_verification(
                result,
                profile,
                passed=False,
                code="unity_project_changed",
                summary="The Unity project changed before completion verification finished.",
            )
        expected_process = (
            baseline.get("unityProcessId"),
            str(baseline.get("unityProcessStartedAtUtc") or ""),
            str(baseline.get("unityExecutableDigest") or ""),
        )
        actual_process = (
            stable_snapshot.get("unityProcessId"),
            str(stable_snapshot.get("unityProcessStartedAtUtc") or ""),
            str(stable_snapshot.get("unityExecutableDigest") or ""),
        )
        if expected_process != actual_process:
            return _attach_console_verification(
                result,
                profile,
                passed=False,
                code="unity_process_changed",
                summary="The Unity process changed before completion verification finished.",
            )
        new_diagnostics = [
            item
            for item in stable_snapshot["diagnostics"]
            if str(item["id"]) not in baseline_ids
        ]
        new_errors = [item for item in new_diagnostics if item["severity"] == "error"]
        new_warnings = [item for item in new_diagnostics if item["severity"] == "warning"]
        passed = not new_errors and not new_warnings
        return _attach_console_verification(
            result,
            profile,
            passed=passed,
            code="" if passed else "unity_console_regression",
            summary=(
                "Unity compilation is stable with no new errors or warnings."
                if passed
                else "Unity reported new compile errors or warnings after the write."
            ),
            new_errors=new_errors,
            new_warnings=new_warnings,
        )

    def _read_snapshot(
        self,
        arguments: Mapping[str, Any],
        *,
        timeout_seconds: float,
    ) -> dict[str, Any]:
        params: dict[str, Any] = {
            "maxErrors": 200,
            "includeConsoleFallback": True,
            "_completionVerifierTimeoutSeconds": max(1, int(timeout_seconds)),
        }
        project_path = str(
            arguments.get("projectPath")
            or arguments.get("projectRoot")
            or arguments.get("project_path")
            or ""
        ).strip()
        if project_path:
            params["projectPath"] = project_path
        try:
            raw = self._read_diagnostics(params)
        except Exception:
            return _unreadable_snapshot()
        payload = _find_diagnostics_payload(raw)
        if payload is None:
            return _unreadable_snapshot()
        try:
            diagnostics = _normalize_diagnostics(payload)
        except (TypeError, ValueError, OverflowError):
            return _unreadable_snapshot()
        return {
            "readable": bool(payload.get("ok") is True),
            "isCompiling": bool(payload.get("isCompiling")),
            "captureComplete": bool(payload.get("captureComplete")),
            "truncated": bool(payload.get("truncated")),
            "capturedAt": str(payload.get("capturedAt") or "")[:80],
            "projectPathDigest": str(payload.get("projectPathDigest") or "")[:160],
            "unityProcessId": (
                int(payload.get("unityProcessId"))
                if isinstance(payload.get("unityProcessId"), int)
                and not isinstance(payload.get("unityProcessId"), bool)
                and int(payload.get("unityProcessId")) > 0
                else 0
            ),
            "unityProcessStartedAtUtc": str(payload.get("unityProcessStartedAtUtc") or "")[:120],
            "unityExecutableDigest": str(payload.get("unityExecutableDigest") or "")[:160],
            "diagnostics": diagnostics,
            "diagnosticIds": [str(item["id"]) for item in diagnostics],
        }


def _find_diagnostics_payload(value: Any) -> Mapping[str, Any] | None:
    current = value
    for _ in range(6):
        if not isinstance(current, Mapping):
            return None
        if "isCompiling" in current and ("errors" in current or "warnings" in current):
            return current
        next_value = None
        for key in ("result", "data", "payload"):
            candidate = current.get(key)
            if isinstance(candidate, Mapping):
                next_value = candidate
                break
        if next_value is None:
            return None
        current = next_value
    return None


def _normalize_diagnostics(payload: Mapping[str, Any]) -> list[dict[str, Any]]:
    values: list[dict[str, Any]] = []
    for severity, key in (("error", "errors"), ("warning", "warnings")):
        raw_items = payload.get(key)
        if not isinstance(raw_items, list):
            continue
        for item in raw_items[:200]:
            if not isinstance(item, Mapping):
                continue
            bounded = {
                "severity": severity,
                "assembly": str(item.get("assembly") or "")[:240],
                "file": str(item.get("file") or "")[:500],
                "line": int(item.get("line") or 0),
                "column": int(item.get("column") or 0),
                "message": str(item.get("message") or "")[:1000],
            }
            canonical = json.dumps(bounded, ensure_ascii=True, sort_keys=True, separators=(",", ":"))
            bounded["id"] = "diag_" + hashlib.sha256(canonical.encode("ascii")).hexdigest()
            values.append(bounded)
    return values


def _stable_snapshot_digest(snapshot: Mapping[str, Any]) -> str:
    payload = {
        "capturedAt": snapshot.get("capturedAt"),
        "diagnosticIds": snapshot.get("diagnosticIds"),
        "projectPathDigest": snapshot.get("projectPathDigest"),
        "unityProcessId": snapshot.get("unityProcessId"),
        "unityProcessStartedAtUtc": snapshot.get("unityProcessStartedAtUtc"),
        "unityExecutableDigest": snapshot.get("unityExecutableDigest"),
    }
    raw = json.dumps(payload, ensure_ascii=True, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(raw.encode("ascii")).hexdigest()


def _identity_complete(snapshot: Mapping[str, Any]) -> bool:
    process_id = snapshot.get("unityProcessId")
    return bool(
        _is_sha256(snapshot.get("projectPathDigest"))
        and isinstance(process_id, int)
        and not isinstance(process_id, bool)
        and process_id > 0
        and str(snapshot.get("unityProcessStartedAtUtc") or "").strip()
        and _is_sha256(snapshot.get("unityExecutableDigest"))
    )


def _is_sha256(value: Any) -> bool:
    text = str(value or "").strip()
    return len(text) == 64 and all(character in "0123456789abcdefABCDEF" for character in text)


def _attach_console_verification(
    result: dict[str, Any],
    profile: str,
    *,
    passed: bool,
    code: str,
    summary: str,
    new_errors: list[dict[str, Any]] | None = None,
    new_warnings: list[dict[str, Any]] | None = None,
) -> dict[str, Any]:
    result["consoleVerified"] = bool(passed)
    result["consoleVerification"] = {
        "schema": "vrcforge.unity_console_verification.v1",
        "profile": profile,
        "status": "passed" if passed else "failed",
        "code": code,
        "summary": summary,
        "newErrorCount": len(new_errors or []),
        "newWarningCount": len(new_warnings or []),
        "newErrors": list(new_errors or [])[:20],
        "newWarnings": list(new_warnings or [])[:20],
    }
    return result


def _unreadable_snapshot() -> dict[str, Any]:
    return {
        "readable": False,
        "isCompiling": False,
        "captureComplete": False,
        "truncated": False,
        "capturedAt": "",
        "projectPathDigest": "",
        "unityProcessId": 0,
        "unityProcessStartedAtUtc": "",
        "unityExecutableDigest": "",
        "diagnostics": [],
        "diagnosticIds": [],
    }
