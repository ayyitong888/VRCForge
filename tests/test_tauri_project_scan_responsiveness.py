from __future__ import annotations

from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


def test_startup_project_index_scan_never_blocks_the_tauri_event_loop() -> None:
    source = (ROOT / "src-tauri/src/commands.rs").read_text(encoding="utf-8")
    start = source.index("pub async fn scan_project_index(")
    end = source.index("#[cfg(test)]", start)
    command = source[start:end]

    assert "blocking_backend_json_request(move ||" in command
    assert ".await" in command
    assert "backend_json_request(" in command
