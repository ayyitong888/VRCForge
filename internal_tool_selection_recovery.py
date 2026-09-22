"""Exact, bounded correction identity for internal tool-block loading failures."""
from __future__ import annotations

from typing import Any

LOADER_TOOL = "vrcforge_load_internal_tool_block"
SELECTION_ERROR = "internal_tool_selection_invalid"


def selection_error_code(outcome: dict[str, Any]) -> str:
    error = outcome.get("error")
    nested = error.get("code") if isinstance(error, dict) else None
    return str(nested or outcome.get("errorCode") or "").strip().casefold()


def selection_correction_matches(
    outcome: dict[str, Any], tool: str, arguments: dict[str, Any],
) -> bool:
    if tool != LOADER_TOOL or selection_error_code(outcome) != SELECTION_ERROR:
        return False
    data = outcome.get("data")
    if not isinstance(data, dict):
        return False
    expected_block = data.get("expectedBlock")
    expected_tools = data.get("requestedTools")
    requested_tools = arguments.get("tools")
    if not isinstance(expected_block, str) or not expected_block:
        return False
    if arguments.get("block") != expected_block:
        return False
    if not all(
        isinstance(items, list) and bool(items)
        and all(isinstance(item, str) and item for item in items)
        for items in (expected_tools, requested_tools)
    ):
        return False
    return data.get("tool") in requested_tools and set(expected_tools) == set(requested_tools)
