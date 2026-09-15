import pytest
import dashboard_server as dashboard
from external_mcp_tool_blocks import EXTERNAL_MCP_READ_TOOL_BLOCKS
from unity_read_input_schemas import UNITY_READ_TOOL_INPUT_SCHEMAS
from tuning_record_discovery import page_tuning_records
from internal_tool_blocks import canonical_tool_owner

CASES = [
    ("vrcforge_list_shader_tuning_history", "read_shader_tuning_history", "records", "materials"),
    ("vrcforge_list_shader_tuning_presets", "read_shader_tuning_presets", "presets", "materials"),
    ("vrcforge_list_tuning_history", "read_tuning_history", "records", "checkpoint"),
    ("vrcforge_list_tuning_presets", "read_tuning_presets", "presets", "checkpoint"),
]

@pytest.mark.parametrize("name,reader,key,block", CASES)
def test_registered_saved_record_reader_pages_exact_ids(monkeypatch, name, reader, key, block):
    rows = [{"id": f"saved-{i}", "avatar_path": "Avatar", "changes": [{"after": i}]} for i in range(5)]
    seen = []
    def read(avatar):
        seen.append(avatar)
        return {"ok": True, key: rows, "count": len(rows)}
    monkeypatch.setattr(dashboard, reader, read)
    tool = dashboard.AGENT_GATEWAY._tools[name]
    result = tool.handler({"avatarPath": "Avatar", "offset": 1, "limit": 2})
    assert seen == ["Avatar"]
    assert result[key] == rows[1:3]
    assert result["count"] == 5 and result["nextOffset"] == 3
    assert result["hasMore"] is True
    assert len(rows) == 5
    assert name in EXTERNAL_MCP_READ_TOOL_BLOCKS[block]
    assert UNITY_READ_TOOL_INPUT_SCHEMAS[name]["properties"]["limit"]["maximum"] == 50
    assert "when-NOT-to-use" in tool.description
    assert canonical_tool_owner(block, name) == (
        "appearance/materials_shaders" if "shader" in name else "diagnostics_build/checkpoints_history"
    )


@pytest.mark.parametrize("arguments", [{"offset": -1}, {"offset": True}, {"limit": 0}, {"limit": 51}, {"limit": "2"}, {"avatarPath": {}}])
def test_invalid_paging_does_not_read_store(arguments):
    def unexpected(_):
        pytest.fail("invalid input reached store")
    with pytest.raises(ValueError):
        page_tuning_records(unexpected, "records", arguments)


def test_empty_and_last_pages_are_explicit_and_do_not_mutate_store():
    rows = [{"id": "one"}, {"id": "two"}]
    reader = lambda _: {"ok": True, "records": rows, "count": 2}
    last = page_tuning_records(reader, "records", {"offset": 1, "limit": 1})
    assert last["records"] == [{"id": "two"}]
    assert last["nextOffset"] is None and not last["hasMore"]
    empty = page_tuning_records(reader, "records", {"offset": 10})
    assert empty["records"] == [] and empty["count"] == 2
    assert len(rows) == 2
    error = {"ok": False, "error": "store unavailable"}
    assert page_tuning_records(lambda _: error, "records", {}) == error
