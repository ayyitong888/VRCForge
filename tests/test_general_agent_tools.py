from pathlib import Path

import pytest

from general_agent_tools import (
    extract_explicit_local_roots,
    find_files,
    list_directory,
    read_text_file,
    search_text,
)


def test_list_directory_is_bounded_and_reports_types(tmp_path: Path) -> None:
    (tmp_path / "a.txt").write_text("a", encoding="utf-8")
    (tmp_path / "nested").mkdir()
    (tmp_path / "nested" / "b.txt").write_text("b", encoding="utf-8")

    result = list_directory(tmp_path, allowed_roots=[tmp_path], max_depth=1, max_count=2)

    assert [entry["name"] for entry in result["entries"]] == ["a.txt", "nested"]
    assert result["entries"][0]["type"] == "file"
    assert result["entries"][1]["type"] == "directory"
    assert result["truncated"] is False
    assert "b.txt" not in str(result)


def test_read_text_file_rejects_binary_and_bounds_bytes(tmp_path: Path) -> None:
    text = tmp_path / "text.txt"
    text.write_text("abcdef", encoding="utf-8")
    binary = tmp_path / "binary.bin"
    binary.write_bytes(b"a\x00b")

    assert read_text_file(text, allowed_roots=[tmp_path], max_bytes=4)["text"] == "abcd"
    with pytest.raises(ValueError, match="binary"):
        read_text_file(binary, allowed_roots=[tmp_path])
    with pytest.raises(FileNotFoundError):
        read_text_file(tmp_path / "missing.txt", allowed_roots=[tmp_path])
    with pytest.raises(IsADirectoryError):
        read_text_file(tmp_path, allowed_roots=[tmp_path])


def test_find_files_and_search_text_are_recursive_and_bounded(tmp_path: Path) -> None:
    (tmp_path / "one.py").write_text("needle one\n", encoding="utf-8")
    (tmp_path / "two.txt").write_text("needle two\n", encoding="utf-8")
    (tmp_path / "sub").mkdir()
    (tmp_path / "sub" / "three.py").write_text("needle three\n", encoding="utf-8")

    found = find_files(tmp_path, allowed_roots=[tmp_path], pattern="*.py", max_depth=2, max_count=10)
    assert [Path(item["path"]).name for item in found["files"]] == ["one.py", "three.py"]
    matches = search_text(tmp_path, "needle", allowed_roots=[tmp_path], pattern="*.py", max_depth=2, max_count=1)
    assert len(matches["matches"]) == 1
    assert matches["truncated"] is True
    assert matches["matches"][0]["line"] == 1


def test_tools_reject_invalid_directory_and_depth(tmp_path: Path) -> None:
    with pytest.raises(FileNotFoundError):
        list_directory(tmp_path / "missing", allowed_roots=[tmp_path])
    file_path = tmp_path / "x.txt"
    file_path.write_text("x", encoding="utf-8")
    with pytest.raises(NotADirectoryError):
        find_files(file_path, allowed_roots=[tmp_path])
    with pytest.raises(ValueError):
        list_directory(tmp_path, allowed_roots=[tmp_path], max_depth=-1)


def test_tools_reject_unscoped_paths_symlinks_sensitive_files_and_excessive_limits(tmp_path: Path) -> None:
    allowed = tmp_path / "allowed"
    outside = tmp_path / "outside"
    allowed.mkdir()
    outside.mkdir()
    secret = outside / ".env"
    secret.write_text("API_KEY=should-not-leak", encoding="utf-8")

    with pytest.raises(PermissionError, match="authorized root"):
        read_text_file(secret, allowed_roots=[allowed])
    with pytest.raises(PermissionError, match="authorized root"):
        read_text_file(outside / "does-not-exist.txt", allowed_roots=[allowed])
    with pytest.raises(PermissionError, match="sensitive"):
        read_text_file(secret, allowed_roots=[outside])
    with pytest.raises(ValueError, match="maximum"):
        list_directory(allowed, allowed_roots=[allowed], max_count=10_000_000)
    with pytest.raises(ValueError, match="maximum"):
        read_text_file(allowed / "missing", allowed_roots=[allowed], max_bytes=10_000_000)

    link = allowed / "escape"
    try:
        link.symlink_to(outside, target_is_directory=True)
    except OSError:
        pytest.skip("symlink creation is unavailable")
    with pytest.raises(PermissionError, match="link|reparse"):
        list_directory(link, allowed_roots=[allowed])


def test_explicit_message_path_scope_and_content_redaction(tmp_path: Path) -> None:
    target = tmp_path / "target directory"
    target.mkdir()
    config = target / "ordinary-config.txt"
    config.write_text('api_key = "secret-value"\nmode = safe\n', encoding="utf-8")

    roots = extract_explicit_local_roots(f"请检查{target}是怎么工作的")
    assert roots == [str(target.resolve())]
    payload = read_text_file(config, allowed_roots=roots)
    assert payload["redacted"] is True
    assert "secret-value" not in payload["text"]
    assert "[REDACTED]" in payload["text"]


def test_relative_paths_resolve_against_single_authorized_root(tmp_path: Path) -> None:
    (tmp_path / "nested").mkdir()
    (tmp_path / "nested" / "note.txt").write_text("relative evidence", encoding="utf-8")

    payload = read_text_file("nested/note.txt", allowed_roots=[tmp_path])
    assert payload["text"] == "relative evidence"

    with pytest.raises(PermissionError, match="ambiguous"):
        read_text_file("nested/note.txt", allowed_roots=[tmp_path, tmp_path / "nested"])


def test_vrcforge_internal_directory_is_never_exposed(tmp_path: Path) -> None:
    internal = tmp_path / ".vrcforge"
    internal.mkdir()
    (internal / "chat-transcripts.json").write_text('{"private":true}', encoding="utf-8")
    variant = tmp_path / ".VRCFORGE"
    (variant / "secret.txt").write_text("private", encoding="utf-8")
    (tmp_path / "public.txt").write_text("public", encoding="utf-8")

    listing = list_directory(tmp_path, allowed_roots=[tmp_path])
    assert all(entry["name"].casefold() != ".vrcforge" for entry in listing["entries"])
    assert all(entry["name"].casefold() != ".vrcforge" for entry in find_files(tmp_path, allowed_roots=[tmp_path])["files"])
    assert search_text(tmp_path, "private", allowed_roots=[tmp_path])["matches"] == []
    for path in (internal / "chat-transcripts.json", variant / "secret.txt", ".vrcforge/chat-transcripts.json"):
        with pytest.raises(PermissionError, match="internal"):
            read_text_file(path, allowed_roots=[tmp_path])
