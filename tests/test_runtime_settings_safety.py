from __future__ import annotations

import json
from pathlib import Path

import pytest

from runtime_settings_safety import (
    load_runtime_settings_safely,
    read_runtime_settings_document_safely,
    runtime_settings_diagnostic,
)


def test_missing_settings_use_in_memory_defaults_without_creating_file(tmp_path: Path) -> None:
    path = tmp_path / "missing.json"

    settings = load_runtime_settings_safely(path)

    assert settings.unity_mcp_host == "127.0.0.1"
    assert settings.unity_mcp_port == 8080
    assert not path.exists()
    assert runtime_settings_diagnostic() == {
        "status": "warning",
        "code": "missing_settings",
        "message": "Runtime settings are missing; conservative in-memory defaults are active.",
        "fallbackActive": True,
    }


def test_invalid_settings_are_preserved_byte_for_byte(tmp_path: Path) -> None:
    path = tmp_path / "settings.json"
    damaged = b'{"llm":'
    path.write_bytes(damaged)

    settings = load_runtime_settings_safely(path, llm_override={"provider": "ollama"})

    assert settings.llm_provider == "ollama"
    assert path.read_bytes() == damaged
    assert runtime_settings_diagnostic()["code"] == "invalid_json"
    assert read_runtime_settings_document_safely(path) == {}
    assert path.read_bytes() == damaged


def test_valid_settings_clear_fallback_diagnostic(tmp_path: Path) -> None:
    path = tmp_path / "settings.json"
    path.write_text(
        json.dumps({"llm": {"provider": "ollama"}, "unity_mcp": {"port": 8123}, "dashboard": {"project_roots": []}}),
        encoding="utf-8",
    )

    settings = load_runtime_settings_safely(path)

    assert settings.llm_provider == "ollama"
    assert settings.unity_mcp_port == 8123
    assert runtime_settings_diagnostic()["fallbackActive"] is False
    assert read_runtime_settings_document_safely(path)["dashboard"] == {"project_roots": []}


@pytest.mark.parametrize(
    "raw_value",
    ("1e999", "-1e999", "NaN", "Infinity", "-Infinity"),
)
def test_non_finite_runtime_numbers_fall_back_without_touching_source(tmp_path: Path, raw_value: str) -> None:
    path = tmp_path / "settings.json"
    original = f'{{"unity_mcp":{{"retry_backoff_seconds":{raw_value}}}}}'.encode()
    path.write_bytes(original)

    settings = load_runtime_settings_safely(path)

    assert settings.unity_mcp_retry_backoff_seconds == 2.0
    assert runtime_settings_diagnostic(path)["code"] == "invalid_json"
    assert runtime_settings_diagnostic(path)["fallbackActive"] is True
    assert read_runtime_settings_document_safely(path) == {}
    assert path.read_bytes() == original


def test_runtime_json_depth_is_bounded_deterministically(tmp_path: Path) -> None:
    path = tmp_path / "settings.json"
    original = (b'{"nested":' * 65) + b"0" + (b"}" * 65)
    path.write_bytes(original)

    settings = load_runtime_settings_safely(path)

    assert settings.unity_mcp_host == "127.0.0.1"
    assert runtime_settings_diagnostic(path)["code"] == "invalid_json"
    assert path.read_bytes() == original


def test_runtime_settings_accept_utf8_bom_without_rewriting_source(tmp_path: Path) -> None:
    path = tmp_path / "settings.json"
    original = b"\xef\xbb\xbf" + json.dumps(
        {"llm": {"provider": "ollama"}, "unity_mcp": {"port": 8124}},
        separators=(",", ":"),
    ).encode()
    path.write_bytes(original)

    settings = load_runtime_settings_safely(path)

    assert settings.unity_mcp_port == 8124
    assert runtime_settings_diagnostic(path)["code"] == "healthy"
    assert read_runtime_settings_document_safely(path)["llm"]["provider"] == "ollama"
    assert path.read_bytes() == original
