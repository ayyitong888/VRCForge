from __future__ import annotations

import json
from typing import Any, Callable


LIGHTWEIGHT_REVIEW_MODEL_MARKERS = ("flash", "mini", "nano", "haiku", "luna", "terra")


def configured_model_is_lightweight_reviewer(model: str) -> bool:
    normalized = str(model or "").strip().lower()
    return bool(normalized) and any(marker in normalized for marker in LIGHTWEIGHT_REVIEW_MODEL_MARKERS)


def review_saved_project_category_approval(
    approval: dict[str, Any],
    *,
    model: str,
    request_text: Callable[[str], str],
) -> str:
    """Review only a request already matched by a saved project/category rule.

    The reviewer has no tools and can return only ``allow_auto`` or
    ``manual``. Missing lightweight configuration, transport errors, invalid
    JSON, or uncertainty always preserve the ordinary manual approval path.
    """

    if not configured_model_is_lightweight_reviewer(model):
        return "manual"
    evidence = {
        "targetTool": str(approval.get("targetTool") or ""),
        "riskLevel": str(approval.get("riskLevel") or ""),
        "arguments": approval.get("arguments") if isinstance(approval.get("arguments"), dict) else {},
        "preview": approval.get("preview") if isinstance(approval.get("preview"), dict) else {},
    }
    prompt = (
        "You are a narrow approval reviewer. No tools are available. Return JSON only, exactly "
        '{"decision":"allow_auto"} or {"decision":"manual"}. '
        "Choose allow_auto only for a routine, non-destructive creation matching the saved category. "
        "Choose manual for any uncertainty, rename/reparent/delete/restore/package/shell action, or unexpected arguments.\n"
        + json.dumps(evidence, ensure_ascii=True, sort_keys=True, separators=(",", ":"))
    )
    try:
        parsed = json.loads(request_text(prompt))
    except Exception:
        return "manual"
    if not isinstance(parsed, dict) or set(parsed) != {"decision"}:
        return "manual"
    return "allow_auto" if parsed.get("decision") == "allow_auto" else "manual"
