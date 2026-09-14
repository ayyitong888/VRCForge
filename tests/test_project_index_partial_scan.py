import json
from pathlib import Path

from project_memory_index import scan_project_memory


def test_partial_index_preserves_unscanned_history_without_false_deletions(tmp_path):
    project = tmp_path / "Project"
    assets = project / "Assets"
    assets.mkdir(parents=True)
    for name in ("A.txt", "B.txt", "C.txt"):
        (assets / name).write_text(name)
    index = tmp_path / "Index"
    first = scan_project_memory(project, index)
    original = json.loads(Path(first["indexPath"]).read_text())
    (assets / "B.txt").unlink()
    partial = scan_project_memory(project, index, max_files=1)
    saved = json.loads(Path(partial["indexPath"]).read_text())
    assert partial["summary"]["truncated"] is True
    assert partial["changes"]["deleted"] == []
    assert saved["files"]["Assets/C.txt"] == original["files"]["Assets/C.txt"]
    assert saved["files"]["Assets/B.txt"] == original["files"]["Assets/B.txt"]
    complete = scan_project_memory(project, index)
    assert complete["summary"]["truncated"] is False
    assert [row["path"] for row in complete["changes"]["deleted"]] == ["Assets/B.txt"]
    assert complete["changes"]["added"] == []
