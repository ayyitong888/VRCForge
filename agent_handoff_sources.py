"""Bind continuity graph nodes to the records that own their meaning."""

from collections.abc import Mapping
from hashlib import sha256


def index_todos(items):
    return {item.get("progressId") or item.get("id"): item for item in items
            if isinstance(item, Mapping) and isinstance(item.get("progressId") or item.get("id"), str)}


def _node_id(prefix, identity):
    return prefix + "-" + sha256(identity.encode("utf-8")).hexdigest()[:32]


def source_nodes(task_inputs, operations, prior=None):
    nodes = {}
    old_nodes = (prior or {}).get("nodes", {})
    previous_operations = {node["operationRef"].get("callId"): node_id for node_id, node in old_nodes.items()
                           if node.get("operationRef", {}).get("callId")}
    for row in task_inputs:
        if row["source"] != "user":
            continue
        references = [ref for ref in row.get("references", ()) if isinstance(ref, Mapping)
                      and isinstance(ref.get("id"), str) and isinstance(ref.get("text"), str) and ref["text"].strip()]
        if not references:
            continue
        reply_id = row["id"]
        references_text = "\n".join(ref["text"] for ref in references)
        nodes[_node_id("user-reply", reply_id)] = {
            "kind": "decision", "text": f"Referenced question or proposal:\n{references_text}\nUser answer:\n{row['text']}",
            "userReplyId": reply_id,
            "evidence": [{"type": "message", "id": reply_id},
                         *({"type": "message", "id": ref["id"]} for ref in references)],
        }
    for row in operations:
        kind, identity = row.get("kind"), row.get("id")
        if kind not in {"execution", "preview", "receipt"} or not isinstance(identity, str) or not identity:
            continue
        node_id = previous_operations.get(row.get("callId")) or _node_id("operation", f"{kind}:{identity}")
        state = row.get("state")
        if state in {"done", "failed"} and node_id not in old_nodes:
            continue
        operation = {key: row[key] for key in ("id", "kind", "tool", "state", "callId") if key in row}
        operation = {key: value for key, value in operation.items() if isinstance(value, str)}
        evidence = [{"type": kind, "id": identity}]
        if row.get("settlementCallId"):
            evidence.append({"type": "receipt", "id": row["settlementCallId"]})
        nodes[node_id] = {
            "kind": "task", "status": {"done": "done", "failed": "abandoned"}.get(state, "in_progress"),
            "text": row.get("text") or row.get("tool") or "Recover outstanding tool operation",
            "operationRef": operation, "evidence": evidence,
        }
    return nodes


def source_attempt_edits(nodes, operations):
    task_by_call = {node["operationRef"].get("callId"): node_id for node_id, node in nodes.items()
                    if node.get("operationRef", {}).get("callId")}
    failed = {row.get("actionId"): row for row in operations
              if row.get("actionId") and row.get("outcomeStatus") == "failed" and row.get("failureReason")}
    attempts = {}
    edges = []

    def add_attempt(row, task_id, status, reason):
        call_id = row["callId"]
        node_id = _node_id("attempt", call_id)
        evidence = [{"type": "receipt", "id": call_id}]
        if row.get("resultRef"):
            evidence.append({"type": "scan", "id": row["resultRef"]})
        attempts[node_id] = {"kind": "attempt", "status": status, "reason": reason,
                             "text": row.get("tool") or "Tool call", "evidence": evidence,
                             "attemptReceiptId": call_id}
        edges.append([node_id, "attempt_of", task_id])
        return node_id

    corrected = {row.get("correctsActionId"): row for row in operations
                 if row.get("correctsActionId") and row.get("outcomeStatus") == "ok"}
    for action_id, failure in failed.items():
        correction = corrected.get(action_id, {})
        task_id = task_by_call.get(failure.get("callId")) or task_by_call.get(correction.get("callId"))
        if not task_id or not failure.get("callId"):
            continue
        failed_id = add_attempt(failure, task_id, "failure", failure["failureReason"])
        if correction.get("callId"):
            reason = "Corrected the reported tool call failure: " + failure["failureReason"]
            corrected_id = add_attempt(correction, task_id, "success", "The corrected call was accepted; see its receipt for the returned state.")
            edges.append([failed_id, "replaced_by", corrected_id, reason])
    return {"nodes": attempts, "edges": edges}
