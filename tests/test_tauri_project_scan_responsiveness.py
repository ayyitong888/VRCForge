from __future__ import annotations

from pathlib import Path

import pytest


ROOT = Path(__file__).resolve().parents[1]


def test_startup_project_index_scan_never_blocks_the_tauri_event_loop() -> None:
    source = (ROOT / "src-tauri/src/commands.rs").read_text(encoding="utf-8")
    start = source.index("pub async fn scan_project_index(")
    end = source.index("#[cfg(test)]", start)
    command = source[start:end]

    assert "blocking_backend_json_request(move ||" in command
    assert ".await" in command
    assert "backend_json_request(" in command


def test_settings_storage_commands_never_block_the_tauri_event_loop() -> None:
    source = (ROOT / "src-tauri/src/commands.rs").read_text(encoding="utf-8")
    for name in ("fetch_external_agent_connectors", "update_external_agent_gateway", "fetch_checkpoint_archive_usage"):
        start = source.index(f"pub async fn {name}(")
        end = source.index("#[tauri::command]", start)
        command = source[start:end]
        assert "blocking_backend_json_request(move ||" in command, name
        assert ".await" in command, name


@pytest.mark.parametrize("name", ("fetch_provider_models", "test_provider_capability"))
def test_provider_connection_commands_are_offloaded_and_sanitized(name: str) -> None:
    source = (ROOT / "src-tauri/src/commands.rs").read_text(encoding="utf-8")
    start = source.index(f"pub async fn {name}(")
    end = source.index("#[tauri::command]", start)
    command = source[start:end]

    assert "blocking_backend_json_request(move ||" in command, name
    assert ".await" in command, name
    assert "backend_json_request(" in command, name
    assert "sanitize_provider_result(" in command, name


@pytest.mark.parametrize("name", ("install_external_agent_connector", "uninstall_external_agent_connector"))
def test_connector_setup_commands_are_offloaded(name: str) -> None:
    source = (ROOT / "src-tauri/src/commands.rs").read_text(encoding="utf-8")
    start = source.index(f"pub async fn {name}(")
    end = source.index("#[tauri::command]", start)
    command = source[start:end]
    assert "blocking_backend_json_request(move ||" in command
    assert ".await" in command
    assert "sanitize_webview_response" in command
