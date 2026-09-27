from pathlib import Path

from general_agent_tools import search_text


def test_search_preserves_complete_redacted_match_line(tmp_path: Path):
    target = tmp_path / "long.txt"
    line = "prefix-" + ("x" * 2500) + "-TAIL-EVIDENCE"
    target.write_text(line + "\n", encoding="utf-8")
    result = search_text(target, "TAIL-EVIDENCE", allowed_roots=[target])
    assert result["matches"][0]["text"] == line
