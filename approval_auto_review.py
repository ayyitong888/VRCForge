from __future__ import annotations

import json
from typing import Any, Callable


LIGHTWEIGHT_REVIEW_MODEL_MARKERS = ("flash", "mini", "nano", "haiku", "luna", "terra")
UNSUITABLE_REVIEW_MODEL_MARKERS = (
    "audio", "embedding", "image", "moderation", "realtime", "speech", "transcribe", "tts",
)


def configured_model_is_lightweight_reviewer(model: str) -> bool:
    normalized = str(model or "").strip().lower()
    return bool(normalized) and any(marker in normalized for marker in LIGHTWEIGHT_REVIEW_MODEL_MARKERS)


def select_independent_reviewer_model(active_model: str, models: list[dict[str, Any]]) -> str:
    """Select a distinct text-capable lightweight model from the user's provider."""

    active = str(active_model or "").strip().casefold()
    candidates: list[str] = []
    for item in models:
        model = str(item.get("id") or "").strip()
        normalized = model.casefold()
        if (
            not model
            or normalized == active
            or not configured_model_is_lightweight_reviewer(model)
            or any(marker in normalized for marker in UNSUITABLE_REVIEW_MODEL_MARKERS)
        ):
            continue
        candidates.append(model)
    return sorted(candidates, key=lambda value: (len(value), value.casefold()))[0] if candidates else ""


def review_general_auto_approval(
    approval: dict[str, Any],
    *,
    active_model: str,
    reviewer_model: str,
    request_text: Callable[[str], str],
) -> str:
    """Fail-closed review for a non-destructive General-project creation."""

    if (
        not reviewer_model
        or reviewer_model.strip().casefold() == str(active_model or "").strip().casefold()
        or not configured_model_is_lightweight_reviewer(reviewer_model)
    ):
        return "manual"
    arguments = approval.get("arguments") if isinstance(approval.get("arguments"), dict) else {}
    evidence = {
        "targetTool": str(approval.get("targetTool") or ""),
        "riskLevel": str(approval.get("riskLevel") or ""),
        "path": str(arguments.get("path") or "")[:1024],
        "overwrite": bool(arguments.get("overwrite")),
        "contentBytes": len(str(arguments.get("content") or "").encode("utf-8")),
    }
    prompt = (
        "You are an independent approval reviewer with no tools. Return JSON only, exactly "
        '{"decision":"allow_auto"} or {"decision":"manual"}. '
        "Allow only creation of a new file inside the already-authorized General project. "
        "Choose manual for overwrite, edit, patch, move, delete, ambiguity, an unusual path, "
        "or any uncertainty. Never infer that the executing model is trustworthy.\n"
        + json.dumps(evidence, ensure_ascii=True, sort_keys=True, separators=(",", ":"))
    )
    try:
        parsed = json.loads(request_text(prompt))
    except Exception:
        return "manual"
    if not isinstance(parsed, dict) or set(parsed) != {"decision"}:
        return "manual"
    return "allow_auto" if parsed.get("decision") == "allow_auto" else "manual"


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
