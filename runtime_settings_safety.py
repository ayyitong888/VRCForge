from __future__ import annotations

import json
import math
import threading
from pathlib import Path
from typing import Any, Callable

from vrchat_blendshape_agent import (
    DEFAULT_LLM_PROVIDER,
    DEFAULT_MIN_CONFIDENCE,
    Settings,
    build_llm_settings,
    load_settings,
)


_DIAGNOSTIC_LOCK = threading.RLock()
_HEALTHY_DIAGNOSTIC: dict[str, Any] = {
    "status": "ok",
    "code": "healthy",
    "message": "Runtime settings are readable.",
    "fallbackActive": False,
}
_LAST_DIAGNOSTIC: dict[str, Any] = dict(_HEALTHY_DIAGNOSTIC)
_LAST_DIAGNOSTIC_KEY = ""
_DIAGNOSTICS_BY_PATH: dict[str, dict[str, Any]] = {}
_MAX_RUNTIME_JSON_DEPTH = 64


class _InvalidRuntimeJson(ValueError):
    pass


def _load_runtime_document(settings_path: Path) -> dict[str, Any]:
    """Read a bounded, standards-compliant settings document without writing it."""

    try:
        text = settings_path.read_text(encoding="utf-8-sig")
    except UnicodeDecodeError as exc:
        raise _InvalidRuntimeJson("settings are not valid UTF-8") from exc

    def reject_constant(value: str) -> None:
        raise _InvalidRuntimeJson(f"non-finite JSON constant is not supported: {value}")

    def parse_finite_float(value: str) -> float:
        parsed = float(value)
        if not math.isfinite(parsed):
            raise _InvalidRuntimeJson(f"non-finite JSON number is not supported: {value}")
        return parsed

    try:
        payload = json.loads(text, parse_constant=reject_constant, parse_float=parse_finite_float)
    except (json.JSONDecodeError, RecursionError) as exc:
        raise _InvalidRuntimeJson("settings contain invalid JSON") from exc
    if not isinstance(payload, dict):
        raise _InvalidRuntimeJson("settings root must be an object")

    pending: list[tuple[Any, int]] = [(payload, 1)]
    while pending:
        value, depth = pending.pop()
        if not isinstance(value, (dict, list)):
            continue
        if depth > _MAX_RUNTIME_JSON_DEPTH:
            raise _InvalidRuntimeJson("settings nesting exceeds the supported limit")
        children = value.values() if isinstance(value, dict) else value
        pending.extend((child, depth + 1) for child in children if isinstance(child, (dict, list)))
    return payload


def _validate_runtime_settings(settings: Settings) -> None:
    port = settings.unity_mcp_port
    retries = settings.unity_mcp_retries
    timeout = settings.unity_mcp_timeout_seconds
    backoff = settings.unity_mcp_retry_backoff_seconds
    confidence = settings.min_confidence
    if isinstance(port, bool) or not isinstance(port, int) or not 1 <= port <= 65535:
        raise ValueError("Unity MCP port is outside the supported range")
    if isinstance(retries, bool) or not isinstance(retries, int) or retries < 1:
        raise ValueError("Unity MCP retries must be a positive integer")
    if isinstance(timeout, bool) or not isinstance(timeout, int) or timeout < 1:
        raise ValueError("Unity MCP timeout must be a positive integer")
    if isinstance(backoff, bool) or not isinstance(backoff, (int, float)) or not math.isfinite(float(backoff)) or backoff < 0:
        raise ValueError("Unity MCP retry backoff must be a finite non-negative number")
    if isinstance(confidence, bool) or not isinstance(confidence, (int, float)) or not math.isfinite(float(confidence)):
        raise ValueError("Planning confidence must be finite")


def _diagnostic_key(settings_path: Path) -> str:
    try:
        return str(Path(settings_path).absolute()).casefold()
    except (OSError, ValueError):
        return str(settings_path).casefold()


def _record_diagnostic(
    settings_path: Path,
    status: str,
    code: str,
    message: str,
    *,
    fallback_active: bool,
) -> None:
    global _LAST_DIAGNOSTIC
    global _LAST_DIAGNOSTIC_KEY
    diagnostic = {
        "status": status,
        "code": code,
        "message": message,
        "fallbackActive": fallback_active,
    }
    key = _diagnostic_key(settings_path)
    with _DIAGNOSTIC_LOCK:
        _LAST_DIAGNOSTIC = diagnostic
        _LAST_DIAGNOSTIC_KEY = key
        _DIAGNOSTICS_BY_PATH[key] = diagnostic


def runtime_settings_diagnostic(settings_path: Path | None = None) -> dict[str, Any]:
    with _DIAGNOSTIC_LOCK:
        if settings_path is not None:
            return dict(_DIAGNOSTICS_BY_PATH.get(_diagnostic_key(settings_path), _HEALTHY_DIAGNOSTIC))
        return dict(_LAST_DIAGNOSTIC)


def _fallback_settings(
    model_override: str | None,
    llm_override: dict[str, Any] | None,
) -> Settings:
    llm = build_llm_settings({}, model_override, llm_override)
    return Settings(
        llm_provider=str(llm.get("provider") or DEFAULT_LLM_PROVIDER),
        llm_api_key=str(llm.get("api_key") or ""),
        llm_base_url=str(llm.get("base_url") or ""),
        llm_model=str(llm.get("model") or ""),
        llm_api_key_env=str(llm.get("api_key_env") or ""),
        gemini_thinking_level=str(llm.get("thinking_level") or ""),
        unity_mcp_command=["unity-mcp"],
        unity_mcp_host="127.0.0.1",
        unity_mcp_port=8080,
        unity_mcp_instance="",
        unity_mcp_retries=3,
        unity_mcp_retry_backoff_seconds=2.0,
        unity_mcp_timeout_seconds=30,
        export_tool_name="vrc_export_blendshapes",
        execute_tool_name="vrc_apply_blendshapes",
        export_path=Path("Assets/VRCForge/blendshapes_export.json"),
        min_confidence=DEFAULT_MIN_CONFIDENCE,
    )


def load_runtime_settings_safely(
    settings_path: Path,
    model_override: str | None = None,
    llm_override: dict[str, Any] | None = None,
    *,
    loader: Callable[..., Settings] | None = None,
) -> Settings:
    """Load runtime settings while keeping Doctor reachable after corruption.

    The source file is never created, rewritten, or removed here.  A missing or
    malformed file activates conservative in-memory defaults and records a
    path-free diagnostic for Doctor.
    """

    try:
        _load_runtime_document(settings_path)
        settings = (loader or load_settings)(settings_path, model_override, llm_override=llm_override)
        _validate_runtime_settings(settings)
    except (FileNotFoundError, SystemExit):
        _record_diagnostic(
            settings_path,
            "warning",
            "missing_settings",
            "Runtime settings are missing; conservative in-memory defaults are active.",
            fallback_active=True,
        )
        return _fallback_settings(model_override, llm_override)
    except (_InvalidRuntimeJson, json.JSONDecodeError):
        _record_diagnostic(
            settings_path,
            "error",
            "invalid_json",
            "Runtime settings contain invalid JSON and were preserved unchanged.",
            fallback_active=True,
        )
        return _fallback_settings(model_override, llm_override)
    except Exception:  # noqa: BLE001 - malformed user values must not make Doctor unreachable.
        _record_diagnostic(
            settings_path,
            "error",
            "invalid_settings",
            "Runtime settings could not be loaded and were preserved unchanged.",
            fallback_active=True,
        )
        return _fallback_settings(model_override, llm_override)

    _record_diagnostic(settings_path, "ok", "healthy", "Runtime settings are readable.", fallback_active=False)
    return settings


def read_runtime_settings_document_safely(settings_path: Path) -> dict[str, Any]:
    """Return the dashboard subsection without making startup depend on it."""

    try:
        payload = _load_runtime_document(settings_path)
    except (OSError, _InvalidRuntimeJson):
        return {}
    return payload
