from __future__ import annotations

from pathlib import Path

import pytest

from mcp_resource_registry import McpResourceRegistry


def _spec(value: int) -> dict:
    return {
        "base_uri": f"vrcforge://test/{value}",
        "name": f"test-{value}",
        "resource_type": "test_record",
        "data": {"value": value},
        "source_mode": "test",
        "refresh_rule": "test refresh",
    }


def _seed(tmp_path: Path) -> tuple[Path, dict]:
    store = tmp_path / "resources"
    seed = McpResourceRegistry(store)
    record = seed.publish(**_spec(1))
    return store, record


def test_constructor_does_not_parse_existing_index(tmp_path, monkeypatch) -> None:
    store, _ = _seed(tmp_path)
    index = store / "registry.json"
    original = Path.open

    def fail_if_read(self: Path, *args, **kwargs):
        if self == index:
            raise AssertionError("constructor eagerly read registry.json")
        return original(self, *args, **kwargs)

    monkeypatch.setattr(Path, "open", fail_if_read)
    registry = McpResourceRegistry(store)
    assert registry.templates()


@pytest.mark.parametrize("operation", ["read", "list", "generation", "validate"])
def test_first_public_read_operation_loads_persistent_records(tmp_path, operation) -> None:
    store, record = _seed(tmp_path)
    registry = McpResourceRegistry(store)

    if operation == "read":
        result = registry.read(record["uri"])["structuredContent"]
        assert result["data"] == {"value": 1}
    elif operation == "list":
        assert registry.list()["resources"][0]["uri"] == record["uri"]
    elif operation == "generation":
        assert registry.generation == 1
    else:
        assert registry.validate_reference(record["uri"], expected_type="test_record")["uri"] == record["uri"]


def test_first_publish_preserves_existing_records(tmp_path) -> None:
    store, first = _seed(tmp_path)
    registry = McpResourceRegistry(store)
    second = registry.publish(**_spec(2))
    loaded = McpResourceRegistry(store)
    values = {item["data"]["value"] for item in (loaded.read(first["uri"])["structuredContent"], loaded.read(second["uri"])["structuredContent"])}
    assert values == {1, 2}


def test_existing_refresh_semantics_reload_changed_index(tmp_path) -> None:
    store, _ = _seed(tmp_path)
    registry = McpResourceRegistry(store)
    assert registry.generation == 1
    writer = McpResourceRegistry(store)
    writer.publish(**_spec(2))
    assert len(registry.list()["resources"]) == 2
