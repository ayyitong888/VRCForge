"""Per-message limits for files and quoted selections."""

from typing import Any


MAX_MESSAGE_FILE_ATTACHMENTS = 10
MAX_MESSAGE_QUOTED_SELECTIONS = 5
MAX_MESSAGE_ATTACHMENT_ITEMS = MAX_MESSAGE_FILE_ATTACHMENTS + MAX_MESSAGE_QUOTED_SELECTIONS


def validate_message_attachment_counts(attachments: list[dict[str, Any]]) -> list[dict[str, Any]]:
    quotes = sum(
        item.get("name") == "Selected text" and not str(item.get("id") or "").startswith("att-")
        for item in attachments
    )
    if len(attachments) - quotes > MAX_MESSAGE_FILE_ATTACHMENTS:
        raise ValueError(f"A message accepts at most {MAX_MESSAGE_FILE_ATTACHMENTS} file attachments.")
    if quotes > MAX_MESSAGE_QUOTED_SELECTIONS:
        raise ValueError(f"A message accepts at most {MAX_MESSAGE_QUOTED_SELECTIONS} quoted selections.")
    return attachments
