"""Working-history projection of human instructions, separate from tool data."""

from __future__ import annotations

import re
from typing import Any

from context_compaction import redact_user_context_text


_INLINE_IMAGE = re.compile(r"data:image/[^\s;,]+;base64,[A-Za-z0-9+/=_-]+", re.IGNORECASE)


def human_user_projection(message: Any) -> dict[str, str] | None:
    """Never promote assistants, tool receipts, or multimodal blocks to humans.

    The caller must establish human provenance before invoking this projection.
    Images are bound only to provider requests, not retained as instructions.
    Text parts survive, while literal inline image bytes do not.
    """
    if not isinstance(message, dict) or message.get("role") != "user":
        return None
    text = message.get("content")
    if isinstance(text, list):
        text = "\n".join(part["text"] for part in text if isinstance(part, dict)
                         and part.get("type") in {"text", "input_text"} and isinstance(part.get("text"), str))
    if not isinstance(text, str) or not text.strip():
        return None
    text = _INLINE_IMAGE.sub("[image omitted from historical user text]", text)
    return {"role": "user", "content": redact_user_context_text(text)}


def human_indices(snapshot: dict) -> list[int]:
    """Legacy snapshots prove only the initial/current direct input boundaries."""
    if "humanUserMessageIndices" in snapshot:
        return list(snapshot["humanUserMessageIndices"])
    messages = snapshot["messages"]
    return sorted({index for index in (0, snapshot["activeTurnStart"])
                   if 0 <= index < len(messages) and messages[index].get("role") == "user"})


def confirmed_human_indices(snapshot: dict, history: list[dict]) -> list[int]:
    """For legacy data, confirm unique exact text against original human input.

    A marker/prefix is never evidence of provenance. Ambiguous repeated text is
    left unknown; explicit owner-created boundaries are already known.
    """
    indices = set(human_indices(snapshot))
    if "humanUserMessageIndices" in snapshot:
        return sorted(indices)
    incoming = [projection for item in history if isinstance(item, dict)
                and (projection := human_user_projection({"role": item.get("role"),
                                                         "content": item.get("text")})) is not None]
    candidates = [(index, human_user_projection(item)) for index, item in enumerate(snapshot["messages"])
                  if item.get("role") == "user"]
    for index, projection in candidates:
        if projection is not None and incoming.count(projection) == 1 and sum(row == projection for _, row in candidates) == 1:
            indices.add(index)
    return sorted(indices)


def missing_human_history(history: list[dict], messages: list[dict]) -> list[dict[str, str]]:
    """Recover only earlier human text from this invocation's visible history.

    The same history is already accepted when creating a native binding. Match
    its ordered overlap with native human text so repeats/revocations retain
    their order without being appended again at every continuation.
    """
    incoming = [projected for item in history if isinstance(item, dict)
                and (projected := human_user_projection({"role": item.get("role"),
                                                        "content": item.get("text")})) is not None]
    present = [projected for item in messages
               if (projected := human_user_projection(item)) is not None]
    for count in range(min(len(incoming), len(present)), 0, -1):
        if incoming[-count:] == present[:count]:
            # Repeated identical human text cannot identify which occurrence a
            # legacy suffix represents. A divergent history is not a merge plan.
            matches = sum(incoming[index:index + count] == present[:count]
                          for index in range(len(incoming) - count + 1))
            if matches != 1:
                return []
            return incoming[:-count]
    return []
