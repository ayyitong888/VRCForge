from __future__ import annotations

import copy
from dataclasses import dataclass
from typing import Any, Callable, Mapping, Protocol


class RuntimeQueuePort(Protocol):
    def submit_runtime_steer(
        self,
        params: Mapping[str, Any] | None = None,
    ) -> Mapping[str, Any]: ...

    def list_runtime_followups(
        self,
        *,
        session_id: str = "",
        include_terminal: bool = True,
    ) -> list[Mapping[str, Any]]: ...

    def enqueue_runtime_followup(
        self,
        params: Mapping[str, Any],
    ) -> Mapping[str, Any]: ...

    def claim_runtime_followups(
        self,
        *,
        session_id: str = "",
        owner_id: str = "",
        limit: int = 8,
        queue_id: str = "",
    ) -> list[Mapping[str, Any]]: ...

    def ack_runtime_followup(
        self,
        queue_id: str,
        session_id: str = "",
        claim_token: str = "",
    ) -> bool: ...

    def cancel_runtime_followup(
        self,
        queue_id: str,
        session_id: str = "",
        claim_token: str = "",
    ) -> bool: ...


@dataclass(frozen=True)
class RuntimeQueueCallbacks:
    submit_runtime_steer: Callable[
        [Mapping[str, Any] | None],
        Mapping[str, Any],
    ]
    list_runtime_followups: Callable[..., list[Mapping[str, Any]]]
    enqueue_runtime_followup: Callable[[Mapping[str, Any]], Mapping[str, Any]]
    claim_runtime_followups: Callable[..., list[Mapping[str, Any]]]
    ack_runtime_followup: Callable[[str, str, str], bool]
    cancel_runtime_followup: Callable[[str, str, str], bool]


class CallbackRuntimeQueueAdapter:
    def __init__(self, callbacks: RuntimeQueueCallbacks) -> None:
        self._callbacks = callbacks

    @staticmethod
    def _mapping_snapshot(value: Mapping[str, Any]) -> dict[str, Any]:
        return copy.deepcopy(dict(value))

    @classmethod
    def _list_snapshot(
        cls,
        values: list[Mapping[str, Any]],
    ) -> list[Mapping[str, Any]]:
        return [cls._mapping_snapshot(value) for value in values]

    def submit_runtime_steer(
        self,
        params: Mapping[str, Any] | None = None,
    ) -> Mapping[str, Any]:
        owned_params = copy.deepcopy(dict(params or {}))
        return self._mapping_snapshot(
            self._callbacks.submit_runtime_steer(owned_params)
        )

    def list_runtime_followups(
        self,
        *,
        session_id: str = "",
        include_terminal: bool = True,
    ) -> list[Mapping[str, Any]]:
        return self._list_snapshot(
            self._callbacks.list_runtime_followups(
                session_id=session_id,
                include_terminal=include_terminal,
            )
        )

    def enqueue_runtime_followup(
        self,
        params: Mapping[str, Any],
    ) -> Mapping[str, Any]:
        owned_params = copy.deepcopy(dict(params))
        return self._mapping_snapshot(
            self._callbacks.enqueue_runtime_followup(owned_params)
        )

    def claim_runtime_followups(
        self,
        *,
        session_id: str = "",
        owner_id: str = "",
        limit: int = 8,
        queue_id: str = "",
    ) -> list[Mapping[str, Any]]:
        return self._list_snapshot(
            self._callbacks.claim_runtime_followups(
                session_id=session_id,
                owner_id=owner_id,
                limit=limit,
                queue_id=queue_id,
            )
        )

    def ack_runtime_followup(
        self,
        queue_id: str,
        session_id: str = "",
        claim_token: str = "",
    ) -> bool:
        return self._callbacks.ack_runtime_followup(
            queue_id,
            session_id,
            claim_token,
        )

    def cancel_runtime_followup(
        self,
        queue_id: str,
        session_id: str = "",
        claim_token: str = "",
    ) -> bool:
        return self._callbacks.cancel_runtime_followup(
            queue_id,
            session_id,
            claim_token,
        )


__all__ = [
    "CallbackRuntimeQueueAdapter",
    "RuntimeQueueCallbacks",
    "RuntimeQueuePort",
]
