"""Bounded runtime execution for Provider protocol negotiation."""

from __future__ import annotations

from typing import Callable, TypeVar

from provider_protocol_negotiation import (
    ProviderProtocolCandidate,
    is_explicit_protocol_compatibility_error,
    provider_protocol_candidates,
)


ResponseT = TypeVar("ResponseT")


def execute_provider_protocol_negotiation(
    *,
    provider: str,
    model: str,
    requested_api_type: str,
    dispatch: Callable[[ProviderProtocolCandidate, Callable[[str], None] | None], ResponseT],
    stream_callback: Callable[[str], None] | None = None,
) -> ResponseT:
    """Dispatch one candidate and apply the fail-before-output fallback rule."""

    candidates = provider_protocol_candidates(provider, model, requested_api_type)
    last_error: BaseException | None = None
    for index, candidate in enumerate(candidates):
        output_seen = False

        def observe_stream(text: str) -> None:
            nonlocal output_seen
            if text:
                output_seen = True
            if stream_callback is not None:
                stream_callback(text)

        try:
            return dispatch(
                candidate,
                observe_stream if stream_callback is not None else None,
            )
        except Exception as exc:  # noqa: BLE001
            last_error = exc
            may_fallback = (
                requested_api_type == "auto"
                and index + 1 < len(candidates)
                and not output_seen
                and is_explicit_protocol_compatibility_error(exc)
            )
            if not may_fallback:
                raise
    if last_error is not None:
        raise last_error
    raise RuntimeError("Provider protocol negotiation returned no candidates.")
