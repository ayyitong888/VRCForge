import json
from pathlib import Path

import pytest

from mcp_resource_registry import McpResourceRegistry


def entry(value):
    return {
        "base_uri": "vrcforge://snapshot/project/encoding-cost",
        "name": "衣柜 / 衣櫃 / wardrobe",
        "resource_type": "test",
        "data": value,
        "identity": {"projectId": "exact"},
        "source_mode": "test",
        "refresh_rule": "Explicit publication only",
    }


def test_small_publication_changes_only_bounded_database_pages(tmp_path):
    registry = McpResourceRegistry(tmp_path)
    first = registry.publish(**entry({"pixels": "x" * 4_000_000}))
    path = registry._index_path()
    before = path.read_bytes()
    file_identity = path.stat().st_ino
    second = registry.publish(**{**entry({"value": "new"}), "base_uri": "vrcforge://snapshot/new"})
    after = path.read_bytes()
    assert path.stat().st_ino == file_identity, "A small publication must not replace the full historical store"
    changed = sum(left != right for left, right in zip(before, after)) + abs(len(after) - len(before))
    assert changed < 64_000, "A small publication must not rewrite historical payload pages"
    assert McpResourceRegistry(tmp_path).read(first["uri"])["structuredContent"] == first
    assert registry.read(second["uri"])["structuredContent"] == second


def test_publication_does_not_deserialize_unrelated_history(tmp_path, monkeypatch):
    registry = McpResourceRegistry(tmp_path)
    first = registry.publish(**entry({"historical-marker": "x" * 100_000}))
    loads = json.loads
    def observed_loads(value, *args, **kwargs):
        assert "historical-marker" not in value
        return loads(value, *args, **kwargs)
    with monkeypatch.context() as patch:
        patch.setattr(json, "loads", observed_loads)
        registry.publish(**{**entry({"value": "new"}), "base_uri": "vrcforge://snapshot/new"})
    assert registry.read(first["uri"])["structuredContent"] == first


def test_numeric_unicode_values_survive_restart(tmp_path):
    registry = McpResourceRegistry(tmp_path)
    first = registry.publish(**entry({"text": "简繁日🌸", "integer": 2**80, "float": 0.123456789}))
    loaded = McpResourceRegistry(tmp_path)
    second = loaded.publish(**entry({"text": "new", "integer": -(2**80)}))
    reloaded = McpResourceRegistry(tmp_path)
    assert reloaded.read(first["uri"])["structuredContent"] == first
    assert reloaded.read(second["uri"])["structuredContent"] == second
    assert registry.generation == reloaded.generation == 2


def test_failed_commit_cannot_reuse_failed_revision_payload(tmp_path, monkeypatch):
    registry = McpResourceRegistry(tmp_path)
    first = registry.publish(**entry({"value": "original"}))
    before = registry._index_path().read_bytes()
    def fail():
        raise OSError("commit rejected")
    with monkeypatch.context() as patch:
        patch.setattr(registry, "_persist_locked", fail)
        with pytest.raises(OSError, match="commit rejected"):
            registry.publish(**entry({"value": "failed"}))
    assert registry._index_path().read_bytes() == before
    assert registry.read(first["uri"])["structuredContent"] == first
    good = registry.publish(**entry({"value": "good"}))
    assert good["revision"] == 2
    assert McpResourceRegistry(tmp_path).read(good["uri"])["structuredContent"] == good


def test_nonfinite_values_still_rejected_without_losing_history(tmp_path):
    registry = McpResourceRegistry(tmp_path)
    first = registry.publish(**entry({"value": 1}))
    before = registry._index_path().read_bytes()
    with pytest.raises(ValueError):
        registry.publish(**entry({"value": float("nan")}))
    assert registry._index_path().read_bytes() == before
    assert registry.read(first["uri"])["structuredContent"] == first


def test_breaking_store_preserves_legacy_bytes_without_loading_and_rejects_old_handle(tmp_path, monkeypatch):
    from mcp_resource_registry import McpResourceError
    path = tmp_path / "registry.json"
    legacy = b"arbitrary historical bytes are not read or migrated"
    path.write_bytes(legacy)
    read_bytes = Path.read_bytes
    def reject_legacy_read(self):
        assert self != path
        return read_bytes(self)
    with monkeypatch.context() as patch:
        patch.setattr(Path, "read_bytes", reject_legacy_read)
        registry = McpResourceRegistry(tmp_path)
        record = registry.publish(**entry({"new": True}))
    assert path.read_bytes() == legacy
    old_uri = record["canonicalUri"] + "?revision=1"
    with pytest.raises(McpResourceError, match="recapture"):
        registry.read(old_uri)
    with pytest.raises(McpResourceError, match="recapture"):
        registry.validate_reference(old_uri)
    assert registry.read(record["canonicalUri"])["structuredContent"] == record


def test_recreated_store_never_reuses_retired_immutable_handles(tmp_path):
    from mcp_resource_registry import McpResourceError
    registry = McpResourceRegistry(tmp_path)
    old = registry.publish(**entry({"old": True}))
    registry._index_path().rename(tmp_path / "retired.sqlite3")
    new = registry.publish(**entry({"new": True}))
    assert old["uri"] != new["uri"] and old["revision"] == new["revision"] == 1
    with pytest.raises(McpResourceError, match="recapture"):
        registry.read(old["uri"])
    assert registry.read(new["uri"])["structuredContent"] == new


def test_retired_epoch_cannot_alias_latest_without_revision(tmp_path):
    from mcp_resource_registry import McpResourceError
    registry = McpResourceRegistry(tmp_path)
    item = registry.publish(**entry({"value": 1}))
    with pytest.raises(McpResourceError, match="recapture"):
        registry.read(item["canonicalUri"] + "?store=retired")


def test_unknown_database_version_is_preserved(tmp_path):
    import sqlite3
    from mcp_resource_registry import McpResourceError
    registry = McpResourceRegistry(tmp_path)
    registry.publish(**entry({"value": 1}))
    with sqlite3.connect(registry._index_path()) as connection:
        connection.execute("PRAGMA user_version=99")
    connection.close()
    before = registry._index_path().read_bytes()
    with pytest.raises(McpResourceError, match="Unsupported"):
        registry.publish(**entry({"value": 2}))
    assert registry._index_path().read_bytes() == before
