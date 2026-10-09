import pytest

from mcp_resource_registry import McpResourceError, McpResourceRegistry, scoped_base_uri


def publish(registry, context, *, kind="session_identity_lock", value=1, absent=False):
    return registry.publish(
        base_uri="vrcforge://catalog/tools/test" if kind == "tool_catalog" else "vrcforge://session/current/identity", name="identity",
        resource_type=kind, data={"value": value}, source_mode="test",
        refresh_rule="explicit", context_id=context, only_if_absent=absent,
    )


def test_contexts_partition_same_base_and_only_if_absent(tmp_path):
    registry = McpResourceRegistry(tmp_path)
    first = publish(registry, "chat-a")
    second = publish(registry, "chat-b", absent=True)
    assert first["uri"] != second["uri"]
    assert first["revision"] == second["revision"] == 1
    assert second["contextId"] == "chat-b"
    assert publish(registry, "chat-a", value=2, absent=True) == first
    assert registry.read(first["uri"])["structuredContent"] == first


def test_list_contexts_share_only_catalog_and_bind_cursor(tmp_path):
    registry = McpResourceRegistry(tmp_path)
    legacy = publish(registry, None)
    first = publish(registry, "chat-a")
    second = publish(registry, "chat-b")
    catalog = publish(registry, "chat-a", kind="tool_catalog")
    assert publish(registry, "chat-b", kind="tool_catalog") == catalog
    assert "contextId" not in catalog
    assert {r["uri"] for r in registry.list(context_id="chat-a")["resources"]} == {first["uri"], catalog["uri"]}
    assert {r["uri"] for r in registry.list(context_id="chat-b")["resources"]} == {second["uri"], catalog["uri"]}
    page = registry.list(context_id="chat-a", page_size=1)
    cursor = page["nextCursor"]
    assert registry.list(context_id="chat-a", cursor=cursor)["resources"][0]["uri"] == first["uri"]
    for context in ("chat-b", None):
        with pytest.raises(McpResourceError):
            registry.list(context_id=context, cursor=cursor)
    with pytest.raises(McpResourceError):
        registry.list(context_id="chat-a", cursor="1")
    assert registry.read(legacy["uri"])["structuredContent"] == legacy
    with pytest.raises(McpResourceError, match="context"):
        registry.validate_reference(legacy["uri"], context_id="chat-a")
    assert registry.validate_reference(catalog["uri"], context_id="chat-b") == catalog


def test_cross_context_history_read_remains_but_prompt_admission_rejected(tmp_path, monkeypatch):
    registry = McpResourceRegistry(tmp_path)
    record = publish(registry, "chat-a")
    assert registry.read(record["uri"])["structuredContent"] == record
    assert registry.validate_reference(record["uri"], context_id="chat-a") == record
    with pytest.raises(McpResourceError, match="context"):
        registry.validate_reference(record["uri"], context_id="chat-b")
    assert registry.validate_reference(record["uri"]) == record
    monkeypatch.setattr("mcp_resource_registry.CURRENT_CONTEXT_TTL_SECONDS", -1)
    with pytest.raises(McpResourceError, match="expired"):
        registry.validate_reference(record["uri"], context_id="chat-a")
    assert registry.read(record["uri"])["structuredContent"] == record


def test_batch_context_partition(tmp_path):
    registry = McpResourceRegistry(tmp_path)
    entries = [{"base_uri": "vrcforge://operation/one/receipt", "name": "receipt",
                "resource_type": "operation_receipt", "data": {},
                "source_mode": "test", "refresh_rule": "explicit"}]
    a = registry.publish_many(entries, context_id="a")[0]
    b = registry.publish_many(entries, context_id="b")[0]
    assert a["uri"] != b["uri"]
    assert a["contextId"] == "a"
    assert b["contextId"] == "b"


def test_scope_uri_preserves_query_and_encodes_context(tmp_path):
    registry = McpResourceRegistry(tmp_path)
    context = "chat:项目 & a/b?x=1"
    record = publish(registry, context)
    assert record["canonicalUri"] == scoped_base_uri("vrcforge://session/current/identity", context)
    assert registry.read(record["canonicalUri"])["structuredContent"] == record
    assert scoped_base_uri("vrcforge://snapshot/a?view=x", "one") == "vrcforge://snapshot/a?view=x&context=one"
    assert scoped_base_uri("vrcforge://snapshot/a", None) == "vrcforge://snapshot/a"


def test_boundary_context_defaults_and_restoration(tmp_path):
    registry = McpResourceRegistry(tmp_path)
    legacy = publish(registry, None)
    token = registry.context.set("runtime-chat")
    try:
        implicit = publish(registry, None)
        assert implicit["contextId"] == "runtime-chat"
        assert [r["uri"] for r in registry.list()["resources"]] == [implicit["uri"]]
        assert registry.validate_reference(implicit["uri"]) == implicit
        with pytest.raises(McpResourceError, match="context"):
            registry.validate_reference(legacy["uri"])
        nested = registry.context.set("second-chat")
        try:
            assert registry.list()["resources"] == []
        finally:
            registry.context.reset(nested)
        assert registry.validate_reference(implicit["uri"]) == implicit
    finally:
        registry.context.reset(token)
    assert registry.context.get() is None
    assert len(registry.list()["resources"]) == 2
    assert registry.validate_reference(legacy["uri"]) == legacy


def test_boundary_context_inherits_to_worker_thread(tmp_path):
    import asyncio

    registry = McpResourceRegistry(tmp_path)

    async def run():
        token = registry.context.set("thread-chat")
        try:
            return await asyncio.to_thread(publish, registry, None)
        finally:
            registry.context.reset(token)

    record = asyncio.run(run())
    assert record["contextId"] == "thread-chat"
    assert registry.context.get() is None
