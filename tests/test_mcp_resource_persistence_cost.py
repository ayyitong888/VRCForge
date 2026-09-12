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


def test_new_revision_does_not_reencode_immutable_history(tmp_path, monkeypatch):
    registry = McpResourceRegistry(tmp_path)
    first = registry.publish(**entry({"pixels": "x" * 100_000}))
    historical = registry._records[first["uri"]]
    dumps = json.dumps
    repeated = []

    def contains_history(value):
        if value is historical:
            return True
        if isinstance(value, dict):
            return any(contains_history(child) for child in value.values())
        if isinstance(value, (list, tuple)):
            return any(contains_history(child) for child in value)
        return False

    def observed_dumps(value, *args, **kwargs):
        if contains_history(value):
            repeated.append(True)
        return dumps(value, *args, **kwargs)

    monkeypatch.setattr(json, "dumps", observed_dumps)
    registry.publish(**entry({"pixels": "new"}))
    assert not repeated, "Publishing new data must not reencode immutable historical payloads"
    assert McpResourceRegistry(tmp_path).read(first["uri"])["structuredContent"] == first


def test_existing_json_and_numeric_unicode_values_survive_restart(tmp_path):
    registry = McpResourceRegistry(tmp_path)
    first = registry.publish(**entry({"text": "简繁日🌸", "integer": 2**80, "float": 0.123456789}))
    path = tmp_path / "registry.json"
    original = json.loads(path.read_text("utf-8"))
    path.write_text(json.dumps(original, ensure_ascii=False, indent=2, sort_keys=True), encoding="utf-8")
    loaded = McpResourceRegistry(tmp_path)
    second = loaded.publish(**entry({"text": "new", "integer": -(2**80)}))
    reloaded = McpResourceRegistry(tmp_path)
    assert reloaded.read(first["uri"])["structuredContent"] == first
    assert reloaded.read(second["uri"])["structuredContent"] == second
    # Registry instances refresh their generation when another instance has
    # persisted a revision; a read of the stale instance is therefore part of
    # the shared-store contract rather than an extra revision.
    assert registry.generation == 2
    assert reloaded.generation == registry.generation


def test_failed_replace_cannot_reuse_failed_revision_encoding(tmp_path, monkeypatch):
    registry = McpResourceRegistry(tmp_path)
    first = registry.publish(**entry({"value": "original"}))
    before = (tmp_path / "registry.json").read_bytes()
    replace = Path.replace

    attempts = []

    def fail(path, target):
        attempts.append(path)
        if Path(target) == tmp_path / "registry.json" and len(attempts) == 1:
            raise OSError("replace rejected")
        return replace(path, target)

    with monkeypatch.context() as m:
        m.setattr(Path, "replace", fail)
        with pytest.raises(OSError, match="replace rejected"):
            registry.publish(**entry({"value": "failed"}))
    assert (tmp_path / "registry.json").read_bytes() == before
    assert registry.read(first["uri"])["structuredContent"] == first
    good = registry.publish(**entry({"value": "good"}))
    assert good["revision"] == 2
    assert McpResourceRegistry(tmp_path).read(good["uri"])["structuredContent"] == good


def test_nonfinite_values_still_rejected_without_losing_history(tmp_path):
    registry = McpResourceRegistry(tmp_path)
    first = registry.publish(**entry({"value": 1}))
    before = (tmp_path / "registry.json").read_bytes()
    with pytest.raises(ValueError):
        registry.publish(**entry({"value": float("nan")}))
    assert (tmp_path / "registry.json").read_bytes() == before
    assert registry.read(first["uri"])["structuredContent"] == first
