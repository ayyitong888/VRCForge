"""Contract completed task branches while retaining their sources and external dependencies."""

from collections import defaultdict
from graphlib import CycleError, TopologicalSorter


def fold_completed_subtrees(graph: dict) -> dict:
    nodes = dict(graph["nodes"])
    edges = graph["edges"]
    children = defaultdict(set)
    parents = defaultdict(set)
    order_sources = {member for node in nodes.values() for order in [node.get("order")] if order
                     for phase in ("before", "after") for member in order[phase]}
    for source, relation, target, *_ in edges:
        if relation == "belongs_to":
            children[target].add(source)
            parents[source].add(target)

    for root, root_node in graph["nodes"].items():
        if root not in nodes or root_node["kind"] != "task" or root_node.get("status") != "done":
            continue
        branch = {root}
        pending = list(children[root])
        while pending:
            child = pending.pop()
            if child in nodes and child not in branch:
                branch.add(child)
                pending.extend(children[child])
        if any(nodes[node].get("status") != "done" or len(parents[node]) != 1 for node in branch - {root}):
            continue

        # A shared decision, open issue or cross-task replacement chain remains independently addressable.
        details = set()
        for source, relation, target, *_ in edges:
            if relation == "attempt_of" and target in branch:
                details.add(source)
            elif (relation == "constrains" and target in branch and nodes[source].get("scope") != "graph"
                  and not nodes[source].get("userReplyId")):
                details.add(source)
            elif relation == "blocked_by" and source in branch and nodes[target]["status"] == "closed":
                details.add(target)
        shared = set()
        neighbors = defaultdict(set)
        for source, _, target, *_ in edges:
            if source in details and target in details:
                neighbors[source].add(target)
                neighbors[target].add(source)
            if source in details and target not in details and target not in branch:
                shared.add(source)
            if target in details and source not in details and source not in branch:
                shared.add(target)
        pending = list(shared)
        while pending:
            for neighbor in neighbors[pending.pop()] - shared:
                shared.add(neighbor)
                pending.append(neighbor)

        removed = (branch - {root}) | (details - shared)
        if not removed or removed & order_sources:
            continue
        pointers = []
        seen = set()
        for node_id, node in nodes.items():
            if node_id in removed or node_id == root:
                for pointer in node.get("evidence", []):
                    identity = (pointer["type"], pointer["id"])
                    if identity not in seen:
                        seen.add(identity)
                        pointers.append(pointer)
        if not pointers:
            continue
        owned = removed | {root}
        kept = {}
        for source, relation, target, *properties in edges:
            if source in removed or target in removed:
                if source in owned and target in owned:
                    continue
                # Dependencies on an already completed child remain satisfied by the collapsed task.
                # Its own historical outgoing prerequisites are recoverable from the source pointers.
                if source in removed and relation == "depends_on":
                    continue
                source = root if source in removed else source
                target = root if target in removed else target
            kept[(source, relation, target)] = [source, relation, target, *properties]
        # A task outside the branch can sit between a completed child and its parent.
        # Contracting such a branch would invent a dependency cycle.
        ordering = TopologicalSorter()
        for source, relation, target in kept:
            if relation == "depends_on":
                ordering.add(source, target)
        try:
            ordering.prepare()
        except CycleError:
            continue
        nodes[root] = {**nodes[root], "evidence": pointers}
        for node_id in removed:
            del nodes[node_id]
        edges = list(kept.values())
    return {**graph, "nodes": nodes, "edges": edges}
