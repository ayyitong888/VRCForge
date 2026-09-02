"""Request-local operation identity carried from Gateway to Unity Core."""

from __future__ import annotations

from contextlib import contextmanager
from contextvars import ContextVar
from typing import Any, Iterator, Mapping


_CURRENT_OPERATION: ContextVar[dict[str, Any] | None] = ContextVar("vrcforge_operation_context", default=None)


@contextmanager
def bind_operation_context(operation_id: str, execution_target: Mapping[str, Any] | None = None) -> Iterator[None]:
    value: dict[str, Any] = {"operationId": str(operation_id)}
    if isinstance(execution_target, Mapping):
        value["executionTarget"] = dict(execution_target)
    token = _CURRENT_OPERATION.set(value)
    try:
        yield
    finally:
        _CURRENT_OPERATION.reset(token)


def current_operation_context() -> dict[str, Any] | None:
    value = _CURRENT_OPERATION.get()
    return dict(value) if isinstance(value, Mapping) else None
