"""Shared identity limits and continuation ID construction for runtime turns."""

MAX_RUNTIME_SESSION_ID_LENGTH = 180
MAX_RUNTIME_CLIENT_TURN_ID_LENGTH = 240


def continuation_client_turn_id(
    parent_id: str, kind: str, resource_id: str, *, feedback_revision: int = 0,
) -> str:
    suffix = f"{kind}:{resource_id}"
    if feedback_revision > 0:
        suffix += f":feedback:{feedback_revision}"
    if parent_id or kind == "question":
        suffix = ":" + suffix
    if kind != "question":
        # Preserve the prefix used by existing non-question continuations.
        parent_id = parent_id[:180]
    return parent_id[:MAX_RUNTIME_CLIENT_TURN_ID_LENGTH - len(suffix)] + suffix
