"""Provider usage accounting for the standalone history compaction path."""

from typing import Any, Mapping, Sequence

from vrchat_blendshape_agent import first_usage_int


_COUNT_FIELDS = ("inputTokens", "outputTokens", "totalTokens")
_CACHE_FIELDS = ("cacheReadTokens", "cacheWriteTokens")


def record_compact_provider_receipt(
    receipts: list[Mapping[str, Any]], usage: Any, reason: str = ""
) -> None:
    """Record one summarizer invocation, including a partial or missing receipt."""
    receipt = dict(usage) if isinstance(usage, Mapping) else {}
    if receipt.get("exact") is not True:
        receipt["exact"] = False
        receipt.setdefault("unavailableReason", reason or "provider_usage_missing")
    receipts.append(receipt)


def aggregate_compact_provider_usage(
    receipts: Sequence[Mapping[str, Any]], *, provider: str = "", model: str = ""
) -> dict[str, Any]:
    """Aggregate one receipt per compaction summarizer invocation.

    A retry is a new provider invocation and is counted once when its summarizer
    call returns or raises. This helper cannot observe provider SDK-internal wire
    retries, so it never claims to account for those separately.
    """
    request_count = len(receipts)
    result: dict[str, Any] = {
        "schema": "vrcforge.provider_usage.v1",
        "provider": str(provider or ""),
        "model": str(model or ""),
        "source": "compaction",
        "exact": bool(request_count),
        "requestCount": request_count,
    }
    totals = {field: 0 for field in (*_COUNT_FIELDS, *_CACHE_FIELDS)}
    observed = {field: 0 for field in totals}
    present = {field: 0 for field in totals}
    coverage = {
        field: {"requestCount": request_count, "reportedRequestCount": 0, "complete": False}
        for field in totals
    }
    exact = bool(request_count)
    reasons: list[str] = []

    for usage in receipts:
        exact_receipt = usage.get("exact") is True
        exact = exact and exact_receipt
        if not exact_receipt:
            reason = str(usage.get("unavailableReason") or "provider_usage_missing")
            if reason not in reasons:
                reasons.append(reason)
        input_tokens = first_usage_int(usage, ("inputTokens", "input_tokens"))
        output_tokens = first_usage_int(usage, ("outputTokens", "output_tokens"))
        reported_total_tokens = first_usage_int(usage, ("totalTokens", "total_tokens"))
        total_tokens = reported_total_tokens
        if total_tokens is None and input_tokens is not None and output_tokens is not None:
            total_tokens = input_tokens + output_tokens
        values = {
            "inputTokens": input_tokens,
            "outputTokens": output_tokens,
            "totalTokens": total_tokens,
            "cacheReadTokens": first_usage_int(
                usage, ("cacheReadTokens", "cache_read_tokens", "cached_tokens")
            ),
            "cacheWriteTokens": first_usage_int(
                usage, ("cacheWriteTokens", "cache_write_tokens", "cache_creation_input_tokens")
            ),
        }
        reported_values = {**values, "totalTokens": reported_total_tokens}
        for field, value in values.items():
            if value is None:
                continue
            totals[field] += value
            observed[field] += 1
            if reported_values[field] is not None:
                present[field] += 1
                coverage[field]["reportedRequestCount"] += 1

    for field, value in totals.items():
        if observed[field]:
            result[field] = value
        coverage[field]["complete"] = bool(
            request_count and present[field] == request_count and exact
        )
    result["coverage"] = coverage
    result["partial"] = any(not field["complete"] for field in coverage.values())
    result["cacheCoverage"] = {
        "requestCount": request_count,
        "read": coverage["cacheReadTokens"],
        "write": coverage["cacheWriteTokens"],
    }
    result["exact"] = exact
    if not request_count:
        result["unavailableReason"] = "provider_not_called"
    elif not exact:
        result["unavailableReason"] = reasons[0] if reasons else "provider_usage_missing"
    return result
