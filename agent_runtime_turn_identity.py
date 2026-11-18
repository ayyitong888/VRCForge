"""Shared identity limits and continuation ID construction for runtime turns."""

from collections.abc import Mapping

MAX_RUNTIME_SESSION_ID_LENGTH = 180
MAX_RUNTIME_CLIENT_TURN_ID_LENGTH = 240


def continuation_identity(source: str, context: Mapping | None) -> dict[str, str]:
    """Attach the actual dispatch owner, independent of the child's bounded id."""
    if not source:
        return {}
    identity = {"continuationSource": source}
    if context:
        # turnId on older task seeds names the original task. The usage parent
        # is captured from the current execution when it dispatches this tool.
        turn = context.get("usageParentTurnId") or context.get("turnId")
        client = context.get("clientTurnId")
        if isinstance(turn, str) and turn:
            identity["parentTurnId"] = turn[:180]
        if isinstance(client, str) and client:
            identity["parentClientTurnId"] = client[:MAX_RUNTIME_CLIENT_TURN_ID_LENGTH]
    return identity


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
