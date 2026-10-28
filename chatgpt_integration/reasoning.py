"""Reasoning values accepted by the direct Responses transport.

The account model catalog also advertises client orchestration modes. They
are not all valid values of Responses reasoning.effort.
"""

DIRECT_REASONING_EFFORTS = frozenset({
    "none", "minimal", "low", "medium", "high", "xhigh", "max",
})


def direct_reasoning_effort(value: str) -> str:
    effort = str(value or "").strip().lower()
    if effort and effort not in DIRECT_REASONING_EFFORTS:
        raise ValueError(
            f"Reasoning level '{effort}' is unavailable on the direct Responses connection. "
            "The account catalog can include Codex orchestration modes such as Ultra; "
            "they cannot be sent as reasoning.effort. Choose an API-supported level "
            "from the ChatGPT model settings (Max only if advertised for that model). "
            "No request was sent and your selected level was not changed."
        )
    return effort
