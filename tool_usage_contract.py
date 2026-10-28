"""Shared trigger-description contract for Agent tool projections."""

from __future__ import annotations


def tool_usage_description(name: str, summary: str, *, write: bool) -> str:
    text = str(summary or name).strip()
    if all(section in text for section in ("When to use:", "When NOT to use:", "Negative example:")):
        description = text
    else:
        when_not = (
            "Do not use while planning, for hypothetical or quoted requests, or without an explicit project change request and approval."
            if write
            else "Do not use for general questions, quoted examples, hypothetical requests, or when the user forbids inspection."
        )
        negative = (
            f"Explain {name} conceptually, but do not modify the project."
            if write
            else f"Mention {name} without inspecting the current project."
        )
        description = f"When to use: {text}\nWhen NOT to use: {when_not}\nNegative example: {negative}"
    if write and "Checkpoint choice:" not in description:
        description += (
            "\nCheckpoint choice: include checkpointChoice={mode:'skip', quote:<exact user instruction>} "
            "only when the user explicitly said not to use checkpoints; never decide this yourself. "
            "Use mode:'require' only when the user explicitly asks to re-enable checkpoints. The choice persists across writes in this session. Omit checkpointChoice to keep the current session setting; new sessions default to enabled. For a "
            "checkpointed write, skipping loses the retained restore point; atomic write behavior is unchanged."
        )
    return description


__all__ = ["tool_usage_description"]
