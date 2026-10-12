"""Runtime-only project selection request for channel frontends."""
from __future__ import annotations

from contextlib import contextmanager
from contextvars import ContextVar
from typing import Any, Callable


TOOL_NAME = "vrcforge_request_project_selection"
_selection_available: ContextVar[bool] = ContextVar("channel_project_selection", default=False)


@contextmanager
def bind_channel_project_selection():
    """Application-owned channel turn only; never populated from model or HTTP input."""
    token = _selection_available.set(True)
    try:
        yield
    finally:
        _selection_available.reset(token)


def channel_project_selection_available() -> bool:
    return _selection_available.get()


def register_channel_project_selection(gateway: Any, request_selection: Callable[[dict[str, Any]], dict[str, Any]]) -> None:
    if not callable(request_selection):
        raise TypeError("request_selection must be callable")

    def handler(params: dict[str, Any]) -> dict[str, Any]:
        return gateway.request_runtime_project_selection(dict(params or {}), request_selection)

    gateway.register_tool(
        TOOL_NAME,
        "When to use: request the channel user to choose an authorized project before a project-specific edit or Unity operation when the current turn has no project context. When NOT to use: ordinary chat, general file questions, already-selected projects, or questions about which tools exist. Negative example: never use this tool to accept a filesystem path or to authorize a write.",
        "plan/preview",
        handler,
    )
