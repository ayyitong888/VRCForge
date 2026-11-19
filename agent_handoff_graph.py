"""Typed continuity state and incremental edits shared by native model handoffs."""

from __future__ import annotations

import json
import re
from typing import Any, Callable


HANDOFF_GRAPH_SCHEMA = "vrcforge.agent_handoff_graph.v1"
_ATTEMPT_OUTCOMES = ("success", "failure", "partial", "unknown")
_FIELDS = {
    "completed": {"action": "text", "result": "text", "evidence": "text"},
    "attempted": {"action": "text", "outcome": "status", "reason": "text"},
    "decisions": {"decision": "text"},
    "blocked": {"blocker": "text", "facts": "text", "evidence": "text"},
    "nextSteps": {"action": "text", "phase": "integer", "dependsOn": "ids"},
}
HANDOFF_EDIT_CONTRACT = (
    "Return one JSON object with goal (string or null) and operations (array). Set goal only to establish or revise it. "
    'Return edits, never the full graph. Complete example: {"goal":"Validate the document","operations":'
    '[{"op":"add","section":"nextSteps","id":"review","fields":{"action":"Review the document","phase":0,"dependsOn":[]}}]}. '
    "Each operation has op, section, id, and fields: add with all fields, update with only changed fields, or remove with "
    "no fields. IDs are stable ASCII slugs unique across the graph. Use these actual JSON value shapes for fields by section: "
    '{"completed":{"action":"Read artifact","result":"Artifact present","evidence":"call-abc"},'
    '"attempted":{"action":"Read index","outcome":"failure","reason":"Index unavailable"},'
    '"decisions":{"decision":"Use the selected variant"},'
    '"blocked":{"blocker":"Approval pending","facts":"Write paused","evidence":"call-def"},'
    '"nextSteps":{"action":"Review entries","phase":2,"dependsOn":["read-artifact"]}}. '
    "attempted.outcome must be success, failure, partial, or unknown (exact English values). Record what happened and why "
    "in reason. Write each text field as a concise fact. phase is an unquoted nonnegative JSON integer, and dependsOn "
    "is a JSON array of strings ([] when empty). Run all items in the lowest unfinished phase before advancing. "
    "Use the same phase for parallel items. dependsOn lists IDs of completed items or "
    "prerequisites in earlier phases. Keep IDs when moving an item by removing it from its old section and adding "
    "it to the new one. Preserve the order within each phase unless new evidence or a user decision changes it."
)


class HandoffSchemaError(ValueError):
    """A schema failure with feedback limited to contract-defined names and indices."""

    def __init__(self, feedback: str):
        super().__init__("schema_error")
        self.feedback = feedback


def _is_id(value: Any) -> bool:
    return isinstance(value, str) and re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9_-]{0,63}", value) is not None


def _is_text(value: Any) -> bool:
    return isinstance(value, str) and bool(value.strip()) and value.splitlines() == [value]


def _valid_field(kind: str, value: Any) -> bool:
    if kind == "text":
        return _is_text(value)
    if kind == "integer":
        return type(value) is int and value >= 0
    if kind == "status":
        return value in _ATTEMPT_OUTCOMES
    return isinstance(value, list) and all(_is_id(item) for item in value) and len(set(value)) == len(value)


def _validate_graph(graph: Any) -> None:
    if (not isinstance(graph, dict) or set(graph) != {"schema", "goal", *_FIELDS}
            or graph["schema"] != HANDOFF_GRAPH_SCHEMA or not _is_text(graph["goal"])):
        raise HandoffSchemaError("The resulting graph needs a nonempty, single-line goal.")
    ids = set()
    for section, fields in _FIELDS.items():
        if not isinstance(graph[section], list):
            raise HandoffSchemaError(f"The {section} section must be an array.")
        for index, node in enumerate(graph[section]):
            if (not isinstance(node, dict) or set(node) != {"id", *fields}
                    or not _is_id(node["id"]) or node["id"] in ids):
                raise HandoffSchemaError(f"Resulting {section}[{index}] needs the section fields and a globally unique slug id.")
            for field, kind in fields.items():
                if not _valid_field(kind, node[field]):
                    raise HandoffSchemaError(f"Resulting {section}[{index}].{field} has an invalid {kind} value.")
            ids.add(node["id"])
    completed = {node["id"] for node in graph["completed"]}
    phases = {node["id"]: node["phase"] for node in graph["nextSteps"]}
    for index, node in enumerate(graph["nextSteps"]):
        if any(dependency not in completed and phases.get(dependency, node["phase"]) >= node["phase"]
               for dependency in node["dependsOn"]):
            raise HandoffSchemaError(
                f"Resulting nextSteps[{index}].dependsOn references an item outside completed or an earlier nextSteps phase."
            )


def parse_handoff_graph(value: str) -> dict[str, Any] | None:
    """Read a persisted graph payload without interpreting historical prose as a graph."""
    try:
        graph = json.loads(value)
        _validate_graph(graph)
    except (TypeError, ValueError):
        return None
    return graph


def apply_handoff_edits(graph: dict[str, Any] | None, payload: Any) -> dict[str, Any]:
    if (not isinstance(payload, dict) or set(payload) != {"goal", "operations"}
            or not isinstance(payload["operations"], list)):
        raise HandoffSchemaError("Return exactly the top-level keys goal and operations, with operations as a JSON array.")
    goal = payload["goal"] if payload["goal"] is not None else graph["goal"] if graph else None
    sections = {section: {node["id"]: node for node in graph[section]} if graph else {} for section in _FIELDS}
    for index, edit in enumerate(payload["operations"]):
        if not isinstance(edit, dict):
            raise HandoffSchemaError(f"operations[{index}] must be a JSON object.")
        operation, section, node_id = edit.get("op"), edit.get("section"), edit.get("id")
        if not isinstance(section, str) or section not in sections:
            raise HandoffSchemaError(f"operations[{index}].section must be one of {', '.join(_FIELDS)}.")
        if not _is_id(node_id):
            raise HandoffSchemaError(f"operations[{index}].id must be a valid slug.")
        nodes, rules = sections[section], _FIELDS[section]
        if operation == "remove":
            if set(edit) != {"op", "section", "id"} or node_id not in nodes:
                raise HandoffSchemaError(f"operations[{index}] must remove an existing {section} id and omit fields.")
            del nodes[node_id]
            continue
        fields = edit.get("fields")
        if (set(edit) != {"op", "section", "id", "fields"} or not isinstance(fields, dict)
                or not fields or not fields.keys() <= rules.keys()
                or operation not in ("add", "update")
                or (operation == "add" and (node_id in nodes or fields.keys() != rules.keys()))
                or (operation == "update" and node_id not in nodes)):
            raise HandoffSchemaError(
                f"operations[{index}] must add a new {section} id with all section fields, "
                f"or update an existing {section} id with changed fields only. "
                f"The only allowed fields are {', '.join(rules)}."
            )
        node = {**nodes.get(node_id, {}), "id": node_id, **fields}
        if section == "nextSteps":
            # The provider may quote typed values. Only project forms with a
            # unique interpretation and let graph validation check all edges.
            phase = node["phase"]
            if isinstance(phase, str) and re.fullmatch(r"[0-9]{1,10}", phase):
                node["phase"] = int(phase)
            dependencies = node["dependsOn"]
            if isinstance(dependencies, str):
                ids = [item.strip() for item in dependencies.split(",")]
                if all(_is_id(item) for item in ids):
                    node["dependsOn"] = ids
        if section == "attempted" and _is_text(node["outcome"]) and node["outcome"] not in _ATTEMPT_OUTCOMES:
            if not _is_text(node["reason"]):
                raise HandoffSchemaError(f"operations[{index}].fields.reason must be nonempty single-line text.")
            # A narrative outcome cannot establish a status on its own.
            node["reason"] = f"{node['outcome']} {node['reason']}"
            node["outcome"] = "unknown"
        nodes[node_id] = node
    result = {"schema": HANDOFF_GRAPH_SCHEMA, "goal": goal,
              **{section: list(nodes.values()) for section, nodes in sections.items()}}
    _validate_graph(result)
    result["nextSteps"].sort(key=lambda node: node["phase"])
    return result


def serialize_handoff_graph(graph: dict[str, Any]) -> str:
    return json.dumps(graph, ensure_ascii=False, separators=(",", ":"))


def map_handoff_graph_text(graph: dict[str, Any], transform: Callable[[str], str]) -> dict[str, Any]:
    projected = {**graph, "goal": transform(graph["goal"])}
    for section, fields in _FIELDS.items():
        projected[section] = [
            {**node, "id": transform(node["id"]),
             **{field: transform(node[field]) for field, kind in fields.items() if kind == "text"},
             **({"dependsOn": [transform(item) for item in node["dependsOn"]]} if section == "nextSteps" else {})}
            for node in graph[section]
        ]
    return projected
