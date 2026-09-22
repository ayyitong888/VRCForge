"""Shared model-facing tool trigger contract.

The gateway and planner expose the same three trigger sections.  Keeping the
base renderer here prevents the two entry points from drifting while leaving
the planner's separate bounded summary policy intact.
"""

from __future__ import annotations


def tool_usage_description(name: str, summary: str, *, write: bool) -> str:
    """Render the stable when-to-use contract for one tool."""

    text = str(summary or name).strip()
    if all(section in text for section in ("When to use:", "When NOT to use:", "Negative example:")):
        return text
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
    return f"When to use: {text}\nWhen NOT to use: {when_not}\nNegative example: {negative}"
