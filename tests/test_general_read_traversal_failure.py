from pathlib import Path

import pytest

from general_agent_tools import find_files, list_directory, search_text


@pytest.mark.parametrize("tool", [list_directory, find_files, search_text])
def test_unreadable_subtree_cannot_be_reported_as_complete(tmp_path, monkeypatch, tool):
    hidden = tmp_path / "unreadable"
    hidden.mkdir()
    (hidden / "fact.txt").write_text("needle", encoding="utf-8")
    original = Path.iterdir

    def unreadable(path):
        if path == hidden:
            raise PermissionError("fixture directory cannot be read")
        return original(path)

    monkeypatch.setattr(Path, "iterdir", unreadable)
    args = (tmp_path, "needle") if tool is search_text else (tmp_path,)
    with pytest.raises(PermissionError, match="cannot be read"):
        tool(*args, allowed_roots=[tmp_path], max_depth=2)
