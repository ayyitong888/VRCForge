"""Bind continuity graph nodes to the records that own their meaning."""

from collections.abc import Mapping
from hashlib import sha256


def index_todos(items):
    return {item.get("progressId") or item.get("id"): item for item in items
            if isinstance(item, Mapping) and isinstance(item.get("progressId") or item.get("id"), str)}


def _node_id(prefix, identity):
    return prefix + "-" + sha256(identity.encode("utf-8")).hexdigest()[:32]


def _source_tasks(graph):
    nodes = graph.get("nodes", {})
    by_call = {}
    for node_id, node in nodes.items():
        if node.get("kind") != "task":
            continue
        operation = node.get("operationRef")
        for call_id in (operation.get("callId") if isinstance(operation, Mapping) else None, node.get("correctionOriginCallId"),
                        node.get("correctionReceiptId")):
            if call_id:
                by_call[call_id] = node_id
    for source, relation, target, *_ in graph.get("edges", ()):
        if relation == "attempt_of" and (call_id := nodes.get(source, {}).get("attemptReceiptId")):
            by_call.setdefault(call_id, target)
    return by_call


def _correction_pairs(operations):
    """Pair an explicit correction with a unique earlier failed action."""
    seen = {}
    for row in operations:
        call_id, action_id = row.get("callId"), row.get("actionId")
        if not call_id or not action_id:
            continue
        candidates = seen.get(row.get("correctsActionId"), {})
        if len(candidates) == 1 and row.get("outcomeStatus") in {"ok", "failed"}:
            failure = next(iter(candidates.values()))
            if failure.get("outcomeStatus") == "failed" and failure["callId"] != call_id:
                yield failure, row
        seen.setdefault(action_id, {})[call_id] = row


def _correction_task(failure, correction, previous):
    page = correction.get("resultPage") or {}
    status = page.get("status") or correction.get("resultStatus")
    reason = page.get("reason")
    scope_tool = (page.get("rescanScope") or {}).get("tool")
    if status == "needs_rescan":
        text = "Refresh the expired retained result" + (f" using {scope_tool}" if scope_tool else "")
    elif status == "replaced":
        text = "Read the replacement reference returned by the corrected result reader"
    elif correction.get("outcomeStatus") == "failed" or page.get("ok") is False:
        text = "Resolve the returned failure from the corrective tool call"
    else:
        text = "Corrected tool call completed"
    if reason:
        text += f" ({reason})"
    evidence = [{"type": "receipt", "id": row["callId"]} for row in (failure, correction)]
    if correction.get("resultRef"):
        evidence.append({"type": "scan", "id": correction["resultRef"]})
    selector = correction.get("correctedSelector") or {}
    if selector.get("messageId"):
        evidence.append({"type": "message", "id": selector["messageId"]})
    ready = status in {"needs_rescan", "replaced"} or correction.get("outcomeStatus") == "failed" or page.get("ok") is False
    same_receipt = previous.get("correctionReceiptId") == correction["callId"]
    return {
        "kind": "task", "status": (previous.get("status") if same_receipt else None) or ("pending" if ready else "done"),
        "text": previous["text"] if same_receipt else text,
        "correctionOriginCallId": previous.get("correctionOriginCallId") or failure["callId"],
        "correctionReceiptId": correction["callId"],
        **({"operationRef": None} if previous.get("operationRef") else {}),
        **({"correctionResult": page} if page else {}),
        **({"requestedSelector": selector} if selector else {}),
        "evidence": evidence,
    }


def source_nodes(task_inputs, operations, prior=None):
    nodes = {}
    prior = prior or {}
    old_nodes = prior.get("nodes", {})
    previous_operations = _source_tasks(prior)
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
    paired_calls = set()
    for failure, correction in _correction_pairs(operations):
        failure_call, corrected_call = failure["callId"], correction["callId"]
        node_id = (previous_operations.get(failure_call) or previous_operations.get(corrected_call)
                   or _node_id("correction", failure_call))
        previous = nodes.get(node_id, old_nodes.get(node_id, {}))
        nodes[node_id] = _correction_task(failure, correction, previous)
        previous_operations.update({failure_call: node_id, corrected_call: node_id})
        paired_calls.update((failure_call, corrected_call))
    for row in operations:
        kind, identity = row.get("kind"), row.get("id")
        if (row.get("callId") in paired_calls or kind not in {"execution", "preview", "receipt"}
                or not isinstance(identity, str) or not identity):
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
    task_by_call = _source_tasks({"nodes": nodes})
    attempts = {}
    edges = []

    def add_attempt(row, task_id, status, reason):
        call_id = row["callId"]
        node_id = _node_id("attempt", call_id)
        evidence = [{"type": "receipt", "id": call_id}]
        if row.get("resultRef"):
            evidence.append({"type": "scan", "id": row["resultRef"]})
        selector = row.get("correctedSelector") or {}
        if selector.get("messageId"):
            evidence.append({"type": "message", "id": selector["messageId"]})
        attempts[node_id] = {"kind": "attempt", "status": status, "reason": reason,
                             "text": row.get("tool") or "Tool call", "evidence": evidence,
                             "attemptReceiptId": call_id,
                             **({"attemptActionId": row["actionId"]} if row.get("actionId") else {}),
                             **({"requestedSelector": selector} if selector else {}),
                             **({"resultPage": row["resultPage"]} if row.get("resultPage") else {})}
        edges.append([node_id, "attempt_of", task_id])
        return node_id

    for failure, correction in _correction_pairs(operations):
        failed_call, corrected_call = failure["callId"], correction["callId"]
        task_id = task_by_call.get(failed_call) or task_by_call.get(corrected_call)
        if not task_id:
            continue
        task_by_call.update({failed_call: task_id, corrected_call: task_id})
        failure_reason = failure.get("failureReason") or "The executor returned a failed outcome."
        failed_id = add_attempt(failure, task_id, "failure", failure_reason)
        page = correction.get("resultPage") or {}
        returned = page.get("status") or correction.get("resultStatus")
        failed = correction.get("outcomeStatus") == "failed" or page.get("ok") is False
        note = (f"The corrected call returned {returned}." if returned
                else "The corrected call failed." if failed else "The corrected call completed.")
        corrected_id = add_attempt(correction, task_id, "failure" if failed else "success", note)
        edges.append([failed_id, "replaced_by", corrected_id, "Corrected the reported tool call failure: " + failure_reason])
    for row in operations:
        if (row.get("outcomeStatus") == "failed" and row.get("failureReason") and row.get("callId") in task_by_call
                and _node_id("attempt", row["callId"]) not in attempts):
            add_attempt(row, task_by_call[row["callId"]], "failure", row["failureReason"])
    return {"nodes": attempts, "edges": edges}


def package_preflight_edits(rows):
    nodes = {}
    for row in rows:
        proof = row.get("packagePreflight")
        if not isinstance(proof, Mapping) or not proof.get("scopeKey") or not row.get("callId"):
            continue
        label = proof["packageName"]
        if proof.get("selectedPrefab"):
            label += " / " + proof["selectedPrefab"]
        state = "ready for import" if proof["ready"] else "blocking import issues found"
        evidence = [{"type": "receipt", "id": row["callId"]}]
        if proof.get("resultRef"):
            evidence.append({"type": "scan", "id": proof["resultRef"]})
        nodes[_node_id("package-preflight", proof["scopeKey"])] = {
            "kind": "task", "status": "done", "text": f"Import preflight: {label} ({state}).",
            "packagePreflight": dict(proof), "evidence": evidence,
        }
    return {"nodes": nodes, "edges": []}
