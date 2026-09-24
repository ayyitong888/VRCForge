"""Shared schema parity assertion for the approved internal execution lane."""
from runtime_planner_service import bounded_planner_tool_schema


def assert_internal_write_schema(actual, external, *, approved_execution: bool) -> None:
    expected = bounded_planner_tool_schema(external)
    preview = expected.get("properties", {}).get("preview")
    if approved_execution and isinstance(preview, dict):
        # Internal approved execution applies; external MCP retains its preview.
        # Every other field remains byte-for-byte equivalent after bounding.
        preview.update({"const": False, "default": False})
    assert actual == expected
