"""Recover outstanding operations from host-owned native tool receipts."""

from collections.abc import Mapping
from itertools import chain
import json
import re

from context_compaction import NATIVE_TRANSACTION_PREFIX
from agent_tool_result_contract import job_receipt
from agent_tool_result_reader import PAGE_SCHEMA, TOOL_NAME


def _object(value):
    return value if isinstance(value, Mapping) else {}


def _read_json(value):
    try:
        return json.loads(value) if isinstance(value, str) else None
    except ValueError:
        return None


def native_handoff_receipt_fields(step):
    """Keep exact recovery selectors from the executor's structured receipt."""
    result = _object(step.get("result"))
    detail = _object(result.get("result"))
    outcome = _object(step.get("outcome"))
    trace = _object(result.get("requestTrace"))
    job = job_receipt(result) or {}
    result_ref = _object(step.get("resultRead")).get("resultRef")
    if step.get("tool") == TOOL_NAME and result.get("schema") == PAGE_SCHEMA:
        result_ref = result.get("replacementRef") or result.get("resultRef")
    fields = {
        "executionId": trace.get("executionId") or outcome.get("operationId")
        or result.get("operationId") or detail.get("operationId"),
        "previewId": result.get("previewId") or detail.get("previewId"),
        "jobId": outcome.get("jobId") or job.get("jobId") or job.get("job_id"),
        "approvalId": outcome.get("approvalId") or result.get("approvalId") or _object(result.get("approval")).get("id"),
        "commitState": outcome.get("commitState") or job.get("commitState"),
        "readbackState": outcome.get("readbackState") or job.get("readbackState"),
        "nextAction": outcome.get("nextAction") or result.get("nextAction") or detail.get("nextAction"),
        "errorCode": _object(outcome.get("error")).get("code") or result.get("errorCode") or result.get("code"),
        "resultStatus": detail.get("status") or result.get("status"),
        "resultRef": result_ref if isinstance(result_ref, str) and re.fullmatch(r"result_[0-9a-f]{32}", result_ref) else None,
    }
    return {key: value for key, value in fields.items() if isinstance(value, str) and value.strip()}


def _archived_messages(snapshot):
    for recovery in snapshot.get("compactionRecovery", ()):
        for entry in recovery.get("sourceEntries", ()):
            text = entry.get("text", "")
            if entry.get("role") != "tool" or not text.startswith(NATIVE_TRANSACTION_PREFIX):
                continue
            group = _read_json(text[len(NATIVE_TRANSACTION_PREFIX):])
            if isinstance(group, list):
                yield from group


def _receipts(messages):
    for message in messages:
        if not isinstance(message, Mapping) or message.get("role") != "tool":
            continue
        call_id = message.get("tool_call_id")
        result = _object(_read_json(message.get("content")))
        observations = result.get("observations") or ()
        for value in observations:
            receipt = _object(value)
            if receipt:
                yield call_id, receipt
        if not observations and isinstance(result.get("status"), str):
            yield call_id, {key: result[key] for key in ("status", "outcome", "reason") if key in result}


def _call_context(messages):
    calls = {}
    for message in messages:
        if not isinstance(message, Mapping) or message.get("role") != "assistant":
            continue
        for call in message.get("tool_calls") or ():
            function = _object(call.get("function"))
            args = _object(_read_json(function.get("arguments")))
            tool_args = _object(args.get("arguments")) if "tool" in args and "arguments" in args else args
            ref = tool_args.get("resultRef")
            correction = args.get("correction_for_action_id")
            calls[call["id"]] = {
                **({"resultRef": ref} if isinstance(ref, str) and re.fullmatch(r"result_[0-9a-f]{32}", ref) else {}),
                **({"correctsActionId": correction} if isinstance(correction, str) and args.get("action") == "correct" else {}),
            }
    return calls


def _identity(call_id, observation):
    receipt = _object(observation.get("handoffReceipt"))
    if receipt.get("executionId"):
        return "execution", receipt["executionId"]
    if receipt.get("previewId"):
        return "preview", receipt["previewId"]
    return "receipt", call_id or observation.get("actionId")


def _state(observation, awaiting_consumer):
    fields = _object(observation.get("handoffReceipt"))
    outcome = _object(observation.get("outcome"))
    status = outcome.get("status") or observation.get("status")
    readback = fields.get("readbackState") or outcome.get("readbackState")
    approvals = {"needs_approval", "approval_required", "pending_approval"}
    if status in approvals or fields.get("resultStatus") in approvals or (
        status == "needs_user_action" and fields.get("approvalId")
    ):
        return "awaiting_approval"
    if (readback in {"pending", "not_started"} or fields.get("commitState") == "pending"
            or (fields.get("jobId") and readback == "unknown")):
        return "readback_pending"
    if status in {"pending", "running", "invoked", "executing"}:
        return "executing"
    if awaiting_consumer:
        return "receipt_pending"
    return "done" if status in {"ok", "success", "executed", "completed", "applied"} else "failed"


def native_handoff_operations(snapshot):
    messages = snapshot.get("messages", ())
    historical = list(_archived_messages(snapshot))
    call_context = _call_context(chain(historical, messages))
    last_assistant = next((index for index in range(len(messages) - 1, -1, -1)
                           if messages[index].get("role") == "assistant"), len(messages))
    unconsumed = {call_id for call_id, _ in _receipts(messages[last_assistant + 1:])}
    operations = {}
    readbacks = {}
    for call_id, observation in chain(_receipts(historical), _receipts(messages)):
        kind, identity = _identity(call_id, observation)
        if not isinstance(identity, str) or not identity:
            continue
        tool = observation.get("runtimeTool")
        fields = _object(observation.get("handoffReceipt"))
        outcome = _object(observation.get("outcome"))
        job_id = fields.get("jobId")
        if (job_id and outcome.get("status") == "ok" and fields.get("commitState") == "complete"
                and fields.get("readbackState") in {"verified", "complete"}):
            readbacks[job_id] = call_id
        operations[kind, identity] = {"id": identity, "kind": kind, "callId": call_id, "tool": tool,
                                      "state": _state(observation, call_id in unconsumed), **call_context.get(call_id, {}),
                                      **{key: value for key, value in {
                                          "actionId": observation.get("actionId"),
                                          "outcomeStatus": outcome.get("status"),
                                          "failureReason": observation.get("admissionError") or fields.get("errorCode"),
                                          "resultRef": fields.get("resultRef"),
                                          "jobId": job_id,
                                      }.items() if isinstance(value, str) and value}}
    for row in operations.values():
        if row["state"] == "readback_pending" and (settlement := readbacks.get(row.get("jobId"))):
            row.update(state="done", settlementCallId=settlement)
    dispatch_id = snapshot.get("activeDispatchCallId")
    if dispatch_id and not any(row["callId"] == dispatch_id for row in operations.values()):
        for call in messages[last_assistant].get("tool_calls", ()) if last_assistant < len(messages) else ():
            if call.get("id") != dispatch_id:
                continue
            function = _object(call.get("function"))
            args = _object(_read_json(function.get("arguments")))
            operations["receipt", dispatch_id] = {
                "id": dispatch_id, "kind": "receipt", "callId": dispatch_id,
                "tool": args.get("tool", function.get("name")), "state": "executing",
            }
    return list(operations.values())


def lookup_native_handoff_receipt(snapshot, execution_id):
    """Read an exact receipt from a snapshot already scoped by its owner."""
    if not isinstance(execution_id, str) or not execution_id:
        return None
    exact = None
    aliases = {}
    for call_id, observation in chain(_receipts(_archived_messages(snapshot)), _receipts(snapshot.get("messages", ()))):
        kind, identity = _identity(call_id, observation)
        if execution_id in (identity, call_id):
            exact = {**observation, "id": identity, "kind": kind, "callId": call_id}
        elif execution_id == observation.get("actionId"):
            aliases[call_id] = {**observation, "id": identity, "kind": kind, "callId": call_id}
    if exact is not None:
        return exact
    if len(aliases) == 1:
        return next(iter(aliases.values()))
    if execution_id == snapshot.get("activeDispatchCallId"):
        return {"id": execution_id, "kind": "receipt", "status": "executing"}
    return None
