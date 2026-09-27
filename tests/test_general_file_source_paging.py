"""Source pages are complete, deterministic, scoped, and change-detecting."""
from pathlib import Path

import pytest

from general_agent_tools import find_files, list_directory, search_text


@pytest.mark.parametrize("tool,key", [(list_directory, "entries"), (find_files, "files"), (search_text, "matches")])
def test_all_pages_equal_independent_expected_rows(tmp_path, tool, key):
    paths = [tmp_path / f"file-{index:02d}.txt" for index in range(9)]
    for path in paths:
        path.write_text("needle first\nordinary\nneedle last\n", encoding="utf-8")
    expected = ([{"path": str(path), "line": line, "text": text} for path in paths
                 for line, text in ((1, "needle first"), (3, "needle last"))] if key == "matches" else
                [{"name": path.name, "path": str(path), "type": "file", "size": path.stat().st_size} for path in paths]
                if key == "entries" else [{"path": str(path), "name": path.name} for path in paths])
    args = (tmp_path, "needle") if key == "matches" else (tmp_path,)
    rows, offset, digest = [], 0, None
    while True:
        result = tool(*args, allowed_roots=[tmp_path], max_count=2, offset=offset, snapshot_digest=digest)
        assert result["offset"] == offset and result["totalCount"] == len(expected)
        assert len(result[key]) <= 2
        assert result["truncated"] == result["hasMore"]
        if digest:
            assert result["snapshotDigest"] == digest
        digest = result["snapshotDigest"]
        rows.extend(result[key])
        if not result["hasMore"]:
            assert result["nextOffset"] is None
            break
        assert result["nextOffset"] > offset
        offset = result["nextOffset"]
    assert rows == expected


@pytest.mark.parametrize("tool", [list_directory, find_files, search_text])
def test_changed_source_rejects_continuation(tmp_path, tool):
    (tmp_path / "a.txt").write_text("needle\nneedle", encoding="utf-8")
    args = (tmp_path, "needle") if tool is search_text else (tmp_path,)
    first = tool(*args, allowed_roots=[tmp_path], max_count=1)
    (tmp_path / "b.txt").write_text("needle", encoding="utf-8")
    with pytest.raises(ValueError, match="snapshot.*changed"):
        tool(*args, allowed_roots=[tmp_path], max_count=1, offset=1, snapshot_digest=first["snapshotDigest"])


def test_search_reaches_match_after_many_nonmatching_files(tmp_path):
    for index in range(2050):
        (tmp_path / f"a-{index:04d}.txt").write_text("ordinary", encoding="utf-8")
    tail = tmp_path / "z-tail.txt"
    tail.write_text("needle at final authorized file", encoding="utf-8")
    result = search_text(tmp_path, "needle", allowed_roots=[tmp_path], max_count=1)
    assert result["matches"] == [{"path": str(tail), "line": 1, "text": "needle at final authorized file"}]
    assert result["totalCount"] == 1 and result["truncated"] is False


@pytest.mark.parametrize("tool,key", [(list_directory, "entries"), (find_files, "files"), (search_text, "matches")])
def test_zero_limit_and_past_end_do_not_offer_nonprogress_page(tmp_path, tool, key):
    (tmp_path / "a.txt").write_text("needle", encoding="utf-8")
    args = (tmp_path, "needle") if tool is search_text else (tmp_path,)
    zero = tool(*args, allowed_roots=[tmp_path], max_count=0)
    assert zero[key] == [] and zero["totalCount"] == 1
    assert zero["hasMore"] and zero["truncated"] and zero["nextOffset"] is None
    final = tool(*args, allowed_roots=[tmp_path], offset=10, snapshot_digest=zero["snapshotDigest"])
    assert final[key] == [] and final["hasMore"] is False and final["nextOffset"] is None
    for bad in (-1, True, 1.5):
        with pytest.raises(ValueError, match="offset"):
            tool(*args, allowed_roots=[tmp_path], offset=bad)


def test_search_pages_keep_privacy_and_incomplete_resource_truth(tmp_path):
    (tmp_path / ".env").write_text("needle credential-secret", encoding="utf-8")
    (tmp_path / "a.txt").write_text("api_key=private-secret\nneedle one\nneedle two", encoding="utf-8")
    (tmp_path / "large.txt").write_text("needle" * 100, encoding="utf-8")
    first = search_text(tmp_path, "needle", allowed_roots=[tmp_path], max_count=1, max_file_bytes=100)
    last = search_text(tmp_path, "needle", allowed_roots=[tmp_path], max_count=1, max_file_bytes=100,
                       offset=first["nextOffset"], snapshot_digest=first["snapshotDigest"])
    assert last["hasMore"] is False and last["truncated"] is True
    assert first["skipped_resource_limit"] == last["skipped_resource_limit"] == 1
    assert first["skipped_binary"] == last["skipped_binary"] == 1
    assert "credential-secret" not in str(first) + str(last)
    redacted = search_text(tmp_path / "a.txt", "api_key", allowed_roots=[tmp_path / "a.txt"], max_count=1)
    assert "private-secret" not in str(redacted) and "[REDACTED]" in redacted["matches"][0]["text"]
