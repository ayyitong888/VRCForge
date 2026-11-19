"""Task continuity graph and local, incremental validation for model handoffs."""

from __future__ import annotations

from collections import defaultdict
import json
import math
import re
from typing import Any, Callable


HANDOFF_GRAPH_SCHEMA = "vrcforge.agent_handoff_graph.v3"
_V1_SCHEMA = "vrcforge.agent_handoff_graph.v1"
_PENDING_STATES = {"pending", "in_progress"}
_STATUSES = {
    "goal": (),
    "task": ("pending", "in_progress", "done", "abandoned"),
    "attempt": ("success", "failure"),
    "issue": ("open", "closed"),
    "decision": (),
}
_RELATIONS = {
    "belongs_to": (("task",), ("goal", "task")),
    "depends_on": (("task",), ("task",)),
    "attempt_of": (("attempt",), ("task",)),
    "replaced_by": (("attempt",), ("attempt",)),
    "blocked_by": (("task",), ("issue",)),
    "constrains": (("decision",), ("task",)),
}
_NODE_CONTROLS = {"kind", "text", "status", "scope", "reason", "facts", "evidence"}
HANDOFF_EDIT_CONTRACT = (
    'Return one compact JSON delta with optional "nodes", "edges", and "removeEdges". '
    '"nodes" maps stable ASCII slug IDs to changed node fields. New nodes need kind and text. '
    'A goal copies the user’s actual wording verbatim. An existing TODO-backed task uses todoId from TODO_SOURCE '
    'and has no status field. A task without a matching TODO has status pending, in_progress, done, or abandoned. '
    'Attempt status: success or failure, with reason explaining the method and outcome. '
    'Issue status: open or closed, with facts explaining the obstacle. Decisions with scope "graph" apply to the full graph. '
    'To represent a phase, create a task with group=true and use belongs_to for its member tasks. Its completion comes from its members. '
    'For an agreed order, update the source-linked decision with order={before:[task or group IDs],after:[task or group IDs],reason:"reason the named phases must be ordered"}. '
    'Every new node needs a nonempty evidence array, for example "evidence":[{"type":"receipt","id":"opaque source ID"}]. '
    'Use exact available message, receipt, or scan IDs. Preserve earlier evidence and failed attempts. '
    'Use distinct task IDs for distinct targets and acceptance criteria. A confirmed success finishes only its own task. '
    'Edges are [from,relation,to]. Use task belongs_to goal or parent task; task depends_on prerequisite task; '
    'attempt attempt_of task; task blocked_by issue; decision constrains task. '
    'A direct depends_on or blocked_by edge has a fourth object {reason:"concrete prerequisite",evidence:[exact source pointers]}. '
    'Use constrains for decisions on how to act that do not affect the runnable frontier. '
    'Use [failed-attempt,"replaced_by",new-attempt,"why the approach changed"] for a replacement. '
    'Example: {"nodes":{"verify":{"kind":"goal","text":"Verify the export","evidence":[{"type":"message","id":"message-example"}]},'
    '"check-file":{"kind":"task","text":"Verify the exported file","status":"pending",'
    '"evidence":[{"type":"message","id":"message-example"}]}},'
    '"edges":[["check-file","belongs_to","verify"]]}. '
    'Omitted fields, nodes and edges are kept. Added evidence pointers extend existing evidence. '
    'Use null to explicitly delete an obsolete node, '
    'and removeEdges triples to remove obsolete relationships. Keep useful issue facts after resolution. '
    'Include dependencies and open issues only when they affect the respective task. Independent runnable tasks may run in parallel.'
)


class HandoffSchemaError(ValueError):
    """Report invalid envelopes without echoing sensitive provider output."""

    def __init__(self, feedback: str):
        super().__init__("schema_error")
        self.feedback = feedback


def _is_id(value: Any) -> bool:
    return isinstance(value, str) and re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9_-]{0,63}", value) is not None


def _is_text(value: Any) -> bool:
    if not isinstance(value, str) or not value.strip():
        return False
    try:
        value.encode("utf-8")
    except UnicodeError:
        return False
    return True


def _is_json_value(value: Any) -> bool:
    if isinstance(value, dict):
        return all(isinstance(key, str) and _is_json_value(key) and _is_json_value(child) for key, child in value.items())
    if isinstance(value, list):
        return all(_is_json_value(child) for child in value)
    if isinstance(value, str):
        try:
            value.encode("utf-8")
        except UnicodeError:
            return False
        return True
    if isinstance(value, float):
        return math.isfinite(value)
    return value is None or isinstance(value, int)


def _valid_node(node: dict[str, Any]) -> bool:
    kind = node.get("kind")
    if not isinstance(kind, str) or kind not in _STATUSES or not _is_text(node.get("text")):
        return False
    statuses = _STATUSES[kind]
    if node.get("group") is True:
        if kind != "task" or "status" in node or ("todoId" in node and not _is_text(node["todoId"])):
            return False
    elif "todoId" in node:
        if kind != "task" or not _is_text(node["todoId"]) or "status" in node:
            return False
    elif (statuses and node.get("status") not in statuses) or (not statuses and "status" in node):
        return False
    if ("scope" in node and (kind != "decision" or node["scope"] != "graph")
            or ((kind == "attempt" or "reason" in node) and not _is_text(node.get("reason")))
            or ((kind == "issue" or "facts" in node) and not _is_text(node.get("facts")))):
        return False
    evidence = node.get("evidence", [])
    if not _valid_evidence(evidence):
        return False
    if "order" in node:
        order = node["order"]
        if (kind != "decision" or not isinstance(order, dict) or not _is_text(order.get("reason"))
                or not any(pointer["type"] in {"message", "archive"} for pointer in evidence)
                or any(not isinstance(ids, list) or not ids or not all(map(_is_id, ids))
                       for ids in (order.get("before"), order.get("after")))):
            return False
    try:
        return _is_json_value(node)
    except RecursionError:
        return False


def _valid_evidence(evidence: Any) -> bool:
    return isinstance(evidence, list) and all(
        isinstance(pointer, dict) and _is_text(pointer.get("type")) and _is_text(pointer.get("id"))
        for pointer in evidence
    )


def _edge_key(edge: Any) -> tuple[str, str, str] | None:
    if not isinstance(edge, list) or len(edge) not in (3, 4):
        return None
    source, relation, target = edge[:3]
    if not _is_id(source) or not isinstance(relation, str) or relation not in _RELATIONS or not _is_id(target):
        return None
    return source, relation, target


def _cyclic_edge(adjacency: dict[str, set[str]], source: str, target: str) -> bool:
    pending = [target]
    seen = set()
    while pending:
        current = pending.pop()
        if current == source:
            return True
        if current not in seen:
            seen.add(current)
            pending.extend(adjacency.get(current, ()))
    return False


def _keep_edges(nodes: dict[str, Any], candidates: list[Any]) -> list[list[str]]:
    edges = {}
    ownership = {}
    acyclic = {relation: defaultdict(set) for relation in ("belongs_to", "depends_on", "replaced_by")}

    def valid(key: tuple[str, str, str]) -> bool:
        source, relation, target = key
        source_kinds, target_kinds = _RELATIONS[relation]
        return (source != target and nodes.get(source, {}).get("kind") in source_kinds
                and nodes.get(target, {}).get("kind") in target_kinds)

    # Derived edges are regenerated from their source decision on every edit.
    candidates = [edge for edge in candidates if not (isinstance(edge, list) and len(edge) == 4
                  and isinstance(edge[3], dict) and "decisionId" in edge[3])]
    ordered = [[later, "depends_on", earlier, {"decisionId": node_id}]
               for node_id, node in nodes.items() if (order := node.get("order"))
               for earlier in order["before"] for later in order["after"]]
    for edge in [*ordered, *candidates]:
        key = _edge_key(edge)
        if key is None or not valid(key):
            continue
        source, relation, target = key
        if relation not in {"replaced_by", "depends_on", "blocked_by"} and len(edge) != 3:
            continue
        if relation in {"depends_on", "blocked_by"} and len(edge) == 4:
            source_link = edge[3]
            if not isinstance(source_link, dict) or ("decisionId" not in source_link
                    and (not _is_text(source_link.get("reason")) or not source_link.get("evidence")
                         or not _valid_evidence(source_link["evidence"]))):
                continue
        if relation == "replaced_by" and (len(edge) != 4 or not _is_text(edge[3]) or nodes[source]["status"] != "failure"):
            continue
        if relation == "attempt_of":
            if source in ownership and ownership[source] != target:
                continue
            ownership[source] = target
        if relation in acyclic:
            adjacency = acyclic[relation]
            if _cyclic_edge(adjacency, source, target):
                continue
            adjacency[source].add(target)
        previous = edges.get(key)
        if not previous or len(previous) != 4 or not isinstance(previous[3], dict) or "decisionId" not in previous[3]:
            edges[key] = list(edge)
    return [edge for edge in edges.values() if edge[1] != "replaced_by"
            or (edge[0] in ownership and edge[2] in ownership)]


def _apply_nodes(
    prior: dict[str, Any], edits: dict[str, Any], *, require_new_evidence: bool,
) -> tuple[dict[str, Any], set[str]]:
    original = prior.get("nodes", {})
    nodes = dict(original)
    linked = defaultdict(list)
    protected_attempts = set()
    for source, relation, target, *_ in prior.get("edges", []):
        if relation in {"belongs_to", "depends_on", "attempt_of", "blocked_by"}:
            linked[target].append(source)
        if relation == "attempt_of" and original[source]["status"] == "failure":
            protected_attempts.add(source)
    deleted = set()
    for node_id, edit in edits.items():
        if not _is_id(node_id):
            continue
        old = nodes.get(node_id, {})
        if edit is None:
            if (not old or linked[node_id] or node_id in protected_attempts
                    or (old["kind"] == "task" and old.get("status", "pending") in _PENDING_STATES)):
                continue
            del nodes[node_id]
            deleted.add(node_id)
            continue
        if not isinstance(edit, dict) or (old and old["kind"] != edit.get("kind", old["kind"])):
            continue
        if node_id in protected_attempts and edit.get("status", "failure") != "failure":
            continue
        node = {**old, **edit}
        if "todoId" in node or node.get("group") is True:
            node.pop("status", None)
        if _valid_node(node) and (old or not require_new_evidence or node.get("evidence")):
            if "evidence" in edit:
                evidence = {(pointer["type"], pointer["id"]): pointer
                            for pointer in [*old.get("evidence", []), *edit["evidence"]]}
                node["evidence"] = list(evidence.values())
            nodes[node_id] = node
    return nodes, deleted


def _apply_graph(
    graph: dict[str, Any] | None, payload: Any, *, require_new_evidence: bool = True,
) -> dict[str, Any]:
    if (not isinstance(payload, dict) or payload.keys() - {"nodes", "edges", "removeEdges"}
            or not isinstance(payload.get("nodes", {}), dict)
            or not isinstance(payload.get("edges", []), list)
            or not isinstance(payload.get("removeEdges", []), list)):
        raise HandoffSchemaError("Use an object with only nodes (object), edges (array), and removeEdges (array).")
    prior = graph or {}
    nodes, deleted = _apply_nodes(prior, payload.get("nodes", {}), require_new_evidence=require_new_evidence)
    removed = {_edge_key(edge) for edge in payload.get("removeEdges", [])}
    edges = []
    for edge in prior.get("edges", []):
        source, relation, target = edge[:3]
        if source in deleted or target in deleted:
            continue
        keep_failed_attempt = relation == "attempt_of" and nodes[source]["status"] == "failure"
        if keep_failed_attempt or tuple(edge[:3]) not in removed:
            edges.append(edge)
    edges.extend(payload.get("edges", []))
    return {"schema": HANDOFF_GRAPH_SCHEMA, "nodes": nodes, "edges": _keep_edges(nodes, edges)}


def fold_handoff_graph(graph: dict[str, Any]) -> dict[str, Any]:
    from agent_handoff_folding import fold_completed_subtrees

    return fold_completed_subtrees(graph)


def apply_handoff_edits(
    graph: dict[str, Any] | None, payload: Any, *, fold: bool = True,
) -> dict[str, Any]:
    updated = _apply_graph(graph, payload)
    return fold_handoff_graph(updated) if fold else updated


def _v1_to_v3(graph: dict[str, Any]) -> dict[str, Any]:
    sections = {"completed": ("action", "result", "evidence"), "attempted": ("action", "outcome", "reason"),
                "decisions": ("decision",), "blocked": ("blocker", "facts", "evidence"),
                "nextSteps": ("action", "phase", "dependsOn")}
    if set(graph) != {"goal", *sections} or not _is_text(graph["goal"]):
        raise HandoffSchemaError("Invalid legacy graph sections.")
    original_ids = {item.get("id") for section in sections if isinstance(graph[section], list)
                    for item in graph[section] if isinstance(item, dict) and _is_id(item.get("id"))}
    goal_id = "legacy-goal"
    while goal_id in original_ids:
        goal_id += "_"
    nodes = {goal_id: {"kind": "goal", "text": graph["goal"]}}
    edges = []
    phases = defaultdict(list)
    for section, fields in sections.items():
        if not isinstance(graph[section], list):
            continue
        for item in graph[section]:
            if not isinstance(item, dict) or set(item) != {"id", *fields}:
                continue
            node_id = item["id"]
            if not _is_id(node_id) or node_id in nodes:
                continue
            if not all(_is_text(item[field]) for field in fields if field not in ("phase", "dependsOn")):
                continue
            if section == "completed":
                node = {"kind": "task", "status": "done", "text": f"{item['action']}. {item['result']}"}
            elif section == "attempted":
                text, status, reason = item["action"], item["outcome"], item["reason"]
                if status in ("success", "failure"):
                    node = {"kind": "attempt", "status": status, "text": text, "reason": reason}
                else:
                    node = {"kind": "issue", "status": "open", "text": text,
                            "facts": f"Legacy attempt recorded outcome {status}: {reason}"}
            elif section == "decisions":
                node = {"kind": "decision", "text": item["decision"]}
            elif section == "blocked":
                node = {"kind": "issue", "status": "open", "text": item["blocker"], "facts": item["facts"]}
            else:
                phase, prerequisites = item["phase"], item["dependsOn"]
                if type(phase) is not int or phase < 0 or not isinstance(prerequisites, list):
                    continue
                phases[phase].append(node_id)
                edges.extend([node_id, "depends_on", dep] for dep in prerequisites if _is_id(dep))
                node = {"kind": "task", "status": "pending", "text": item["action"]}
            if node["kind"] == "task":
                edges.append([node_id, "belongs_to", goal_id])
            if "evidence" in item:
                node["evidence"] = [{"type": "legacy", "id": item["evidence"]}]
            nodes[node_id] = node
    # Each old phase was a barrier even where individual dependencies were omitted.
    previous = []
    for phase in sorted(phases):
        edges.extend([node, "depends_on", dep] for node in phases[phase] for dep in previous)
        previous = phases[phase]
    return _apply_graph(None, {"nodes": nodes, "edges": edges}, require_new_evidence=False)


def parse_handoff_graph(value: str) -> dict[str, Any] | None:
    """Read v3 and migrate explicitly identified continuity formats."""
    try:
        graph = json.loads(value)
        if not isinstance(graph, dict):
            return None
        schema = graph.pop("schema", None)
        if schema == _V1_SCHEMA:
            return _v1_to_v3(graph)
        if schema == HANDOFF_GRAPH_SCHEMA and set(graph) == {"nodes", "edges"}:
            return _apply_graph(None, graph, require_new_evidence=False)
    except (AttributeError, KeyError, TypeError, ValueError, RecursionError):
        return None
    return None


def handoff_view(graph: dict[str, Any], todo_items=()) -> dict[str, Any]:
    """Derive the current task frontier and unresolved issues from typed edges."""
    nodes = graph["nodes"]
    from agent_handoff_sources import index_todos

    todos = index_todos(todo_items)
    todo_status = {
        "pending": "pending", "in_progress": "in_progress", "running": "in_progress", "blocked": "blocked",
        "completed": "done", "cancelled": "abandoned", "deleted": "abandoned",
    }
    statuses = {node_id: (None if node.get("group") else
                         todo_status.get(todos.get(node["todoId"], {}).get("status"), "unavailable")
                         if "todoId" in node else node.get("status"))
                for node_id, node in nodes.items()}
    ancestors = defaultdict(set)
    members = defaultdict(set)
    for source, relation, target, *_ in graph["edges"]:
        if relation == "belongs_to":
            ancestors[source].add(target)
            members[target].add(source)

    def group_status(node_id):
        if not nodes[node_id].get("group"):
            return statuses[node_id]
        if statuses[node_id] is None:
            children = {group_status(child) for child in members[node_id] if nodes[child]["kind"] == "task"}
            statuses[node_id] = "done" if children == {"done"} else "pending"
        return statuses[node_id]

    for node_id, node in nodes.items():
        if node["kind"] == "task":
            group_status(node_id)
    waiting = set()
    for source, relation, target, *_ in graph["edges"]:
        if relation == "depends_on" and statuses[target] != "done":
            waiting.add(source)
        elif relation == "blocked_by" and nodes[target]["status"] == "open":
            waiting.add(source)
    # A phase-level prerequisite or blocker applies to all of its nested tasks.
    pending_groups = [node_id for node_id in waiting if nodes[node_id].get("group")]
    while pending_groups:
        for member in members[pending_groups.pop()] - waiting:
            waiting.add(member)
            pending_groups.append(member)
    ready = []
    recoveries = []
    goals = {node_id for node_id, node in nodes.items() if node["kind"] == "goal"}
    completed_goals = set()
    pending = False
    for node_id, node in nodes.items():
        if node["kind"] != "task":
            continue
        status = statuses[node_id]
        if status == "done":
            completed_goals.update(ancestors[node_id] & goals)
        elif status != "abandoned":
            pending = True
            if node.get("operationRef") and status == "in_progress":
                recoveries.append(node_id)
            elif status in _PENDING_STATES and node_id not in waiting and not node.get("group"):
                ready.append(node_id)
    ready = recoveries + ready
    incomplete = not goals or pending or bool(goals - completed_goals)
    return {
        "readyTaskIds": ready,
        **({"recoverTaskIds": recoveries} if recoveries else {}),
        "openIssueIds": [node_id for node_id, node in nodes.items()
                         if node["kind"] == "issue" and node["status"] == "open"],
        "incomplete": incomplete,
        "anomaly": incomplete and not ready,
    }


def ready_task_ids(graph: dict[str, Any]) -> list[str]:
    return handoff_view(graph)["readyTaskIds"]


def serialize_handoff_graph(graph: dict[str, Any]) -> str:
    return json.dumps(graph, ensure_ascii=False, separators=(",", ":"), allow_nan=False)


def map_handoff_graph_text(
    graph: dict[str, Any],
    transform: Callable[[str], str],
    *,
    transform_properties: Callable[[dict[str, Any]], dict[str, Any]],
) -> dict[str, Any]:
    ids = {node_id: transform(node_id) for node_id in graph["nodes"]}
    used = set(ids)
    for index, (node_id, projected) in enumerate(ids.items()):
        if projected != node_id:
            candidate = f"redacted-node-{index}"
            while candidate in used:
                candidate += "_"
            used.add(candidate)
            ids[node_id] = candidate
    nodes = {}
    for node_id, node in graph["nodes"].items():
        control = {key: value for key, value in node.items() if key in _NODE_CONTROLS}
        for key in ("text", "reason", "facts"):
            if isinstance(control.get(key), str):
                control[key] = transform(control[key])
        if "evidence" in control:
            control["evidence"] = transform_properties({"evidence": control["evidence"]})["evidence"]
        properties = {key: value for key, value in node.items() if key not in _NODE_CONTROLS}
        projected = transform_properties(properties)
        if "order" in node:
            for field in ("before", "after"):
                projected["order"][field] = [ids[ref] for ref in node["order"][field] if ref in ids]
        nodes[ids[node_id]] = {**projected, **control}
    edges = []
    for source, relation, target, *details in graph["edges"]:
        edge = [ids[source], relation, ids[target]]
        if details:
            detail = details[0]
            if isinstance(detail, dict):
                projected = transform_properties(detail)
                if "decisionId" in detail:
                    projected["decisionId"] = ids[detail["decisionId"]]
                edge.append(projected)
            else:
                edge.append(transform(detail))
        edges.append(edge)
    return {**graph, "nodes": nodes, "edges": edges}
