from pathlib import Path

import pytest

from general_agent_tools import read_text_file


def test_read_text_file_returns_complete_safe_text_then_line_selects(tmp_path: Path):
    target = tmp_path / "complete.txt"
    target.write_text("prefix\npassword=secret-value\nTAIL😀\n", encoding="utf-8")
    result = read_text_file(target, allowed_roots=[tmp_path], max_bytes=128, max_output_chars=3, start_line=3, end_line=3)
    assert result["text"].replace("\r\n", "\n") == "TAIL😀\n"
    assert result["truncated"] is False
    assert "secret-value" not in result["text"]


def test_read_text_file_rejects_resource_overflow_without_returning_prefix(tmp_path: Path):
    target = tmp_path / "overflow.txt"
    target.write_text("prefix\nTAIL-EVIDENCE\n", encoding="utf-8")
    with pytest.raises(ValueError, match="resource|maximum|complete"):
        read_text_file(target, allowed_roots=[tmp_path], max_bytes=7)


def test_read_text_file_utf8_boundary_is_not_a_partial_result(tmp_path: Path):
    target = tmp_path / "utf8.txt"
    target.write_text("😀tail", encoding="utf-8")
    with pytest.raises(ValueError, match="resource|maximum|complete"):
        read_text_file(target, allowed_roots=[tmp_path], max_bytes=2)
    result = read_text_file(target, allowed_roots=[tmp_path], max_bytes=32)
    assert result["text"] == "😀tail"
