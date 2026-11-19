"""Recover outstanding operations from host-owned native tool receipts."""

from collections.abc import Mapping
from itertools import chain
import json
import re

from context_compaction import NATIVE_TRANSACTION_PREFIX
from agent_tool_result_contract import job_receipt
from agent_tool_result_reader import INPUT_SCHEMA, PAGE_SCHEMA, TOOL_NAME
from credential_redaction import redact_credential_text


def _object(value):
    return value if isinstance(value, Mapping) else {}


def _read_json(value):
    try:
        return json.loads(value) if isinstance(value, str) else None
    except ValueError:
        return None


def _retained_result_page(result):
    if (result.get("schema") != PAGE_SCHEMA or result.get("authority") != "untrusted_tool_output"
            or not _is_result_ref(result.get("resultRef"))):
        return {}
    page = {key: result[key] for key in (
        "status", "freshness", "reason", "source", "sourceTool", "jsonPointer",
    ) if isinstance(result.get(key), str)}
    page.update({key: result[key] for key in ("ok", "expired", "hasMore", "rescanScopeTruncated")
                 if type(result.get(key)) is bool})
    page["resultRef"] = result["resultRef"]
    if _is_result_ref(result.get("replacementRef")):
        page["replacementRef"] = result["replacementRef"]
    if isinstance(result.get("rescanScope"), dict):
        page["rescanScope"] = result["rescanScope"]
    return page


def _is_result_ref(value):
    return isinstance(value, str) and re.fullmatch(r"result_[0-9a-f]{32}", value) is not None


def _observed_result_page(observation):
    if observation.get("runtimeTool") not in {TOOL_NAME, "read_tool_result"}:
        return {}
    if page := _object(_object(observation.get("handoffReceipt")).get("resultPage")):
        return page if _is_result_ref(page.get("resultRef")) else {}
    text = observation.get("observation")
    if isinstance(text, str):
        _prefix, marker, page = text.partition("; retainedResultPage=")
        if marker:
            return _retained_result_page(_object(_read_json(page)))
    return {}


def native_handoff_receipt_fields(step):
    """Keep exact recovery selectors from the executor's structured receipt."""
    result = _object(step.get("result"))
    detail = _object(result.get("result"))
    outcome = _object(step.get("outcome"))
    trace = _object(result.get("requestTrace"))
    job = job_receipt(result) or {}
    result_ref = _object(step.get("resultRead")).get("resultRef")
    result_page = {}
    if step.get("tool") in {TOOL_NAME, "read_tool_result"} and result.get("schema") == PAGE_SCHEMA:
        result_ref = result.get("replacementRef") or result.get("resultRef")
        result_page = _retained_result_page(result)
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
        "resultRef": result_ref if _is_result_ref(result_ref) else None,
    }
    return {**{key: value for key, value in fields.items() if isinstance(value, str) and value.strip()},
            **({"resultPage": result_page} if result_page else {})}


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
            is_correction = (isinstance(correction, str) and correction
                             and function.get("name") in {"vrcforge_runtime_action", "runtime_action"}
                             and args.get("action") == "correct")
            selector = {}
            if is_correction and args.get("tool") in {TOOL_NAME, "read_tool_result"}:
                pointer = tool_args.get("jsonPointer")
                pointer_schema = INPUT_SCHEMA["properties"]["jsonPointer"]
                if _is_result_ref(ref) and (pointer is None or (
                    isinstance(pointer, str) and len(pointer) <= pointer_schema["maxLength"]
                    and re.fullmatch(pointer_schema["pattern"], pointer)
                )):
                    selector = {"resultRef": ref, **({"jsonPointer": pointer} if pointer is not None else {})}
                elif isinstance(message_id := tool_args.get("messageId"), str) and message_id.strip():
                    selector = {"messageId": message_id}
            calls[call["id"]] = {
                **({"resultRef": ref} if _is_result_ref(ref) else {}),
                **({"correctsActionId": correction} if is_correction else {}),
                **({"correctedSelector": selector} if selector else {}),
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
    correction_targets = {row.get("correctsActionId") for row in call_context.values() if row.get("correctsActionId")}
    for call_id, observation in chain(_receipts(historical), _receipts(messages)):
        kind, identity = _identity(call_id, observation)
        if not isinstance(identity, str) or not identity:
            continue
        tool = observation.get("runtimeTool")
        fields = _object(observation.get("handoffReceipt"))
        outcome = _object(observation.get("outcome"))
        result_page = _observed_result_page(observation)
        request = call_context.get(call_id, {})
        # A legacy page is input from the actual reader receipt, and its
        # resultRef must agree with the request when the latter is retained.
        if result_page and request.get("resultRef", result_page["resultRef"]) != result_page["resultRef"]:
            result_page = {}
        if outcome.get("status") != "ok" and "correctedSelector" in request:
            request = {key: value for key, value in request.items() if key != "correctedSelector"}
        job_id = fields.get("jobId")
        if (job_id and outcome.get("status") == "ok" and fields.get("commitState") == "complete"
                and fields.get("readbackState") in {"verified", "complete"}):
            readbacks[job_id] = call_id
        action_id = observation.get("actionId")
        preserve_attempt = request.get("correctsActionId") or action_id in correction_targets
        operations[kind, identity, call_id if preserve_attempt else None] = {
                                      "id": identity, "kind": kind, "callId": call_id, "tool": tool,
                                      "state": _state(observation, call_id in unconsumed), **request,
                                      **({"resultPage": result_page} if result_page else {}),
                                      **{key: value for key, value in {
                                          "actionId": action_id,
                                          "outcomeStatus": outcome.get("status"),
                                          "resultStatus": result_page.get("status") or fields.get("resultStatus"),
                                          "failureReason": observation.get("admissionError") or fields.get("errorCode"),
                                          "resultRef": result_page.get("replacementRef") or result_page.get("resultRef")
                                          or fields.get("resultRef"),
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
            operations["receipt", dispatch_id, None] = {
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
    messages = tuple(_receipt_messages(snapshot))
    for call_id, observation in _receipts(messages):
        kind, identity = _identity(call_id, observation)
        if execution_id in (identity, call_id):
            exact = {**observation, "id": identity, "kind": kind, "callId": call_id}
        elif execution_id == observation.get("actionId"):
            aliases[call_id] = {**observation, "id": identity, "kind": kind, "callId": call_id}
    result = exact if exact is not None else next(iter(aliases.values())) if len(aliases) == 1 else None
    if result is not None:
        saved = _package_selector_receipt(snapshot, result["callId"])
        selector = (saved.get("sourceSelector") if saved and _package_selector_tool(saved.get("runtimeTool"))
                    == _package_selector_tool(result.get("runtimeTool")) and _object(result.get("outcome")).get("status") == "ok"
                    else None) or _package_source_selector(messages, result)
        # Put exact recovery coordinates before long old observation text, so
        # the model gets them in the first executionId page.
        return {"sourceSelector": selector, **result} if selector else result
    if saved := _package_selector_receipt(snapshot, execution_id):
        return saved
    if execution_id == snapshot.get("activeDispatchCallId"):
        return {"id": execution_id, "kind": "receipt", "status": "executing"}
    return None


def _package_selector_archive_id(call_id):
    return f"package-selector:{call_id}"


def _package_selector_receipt(snapshot, call_id):
    if not isinstance(call_id, str) or not call_id:
        return None
    record = _object(snapshot.get("handoffArchive", {}).get(_package_selector_archive_id(call_id)))
    saved = _object(_read_json(record.get("text")))
    if (record.get("source") == "tool" and saved.get("callId") == call_id
            and _package_selector_tool(saved.get("runtimeTool")) and _object(saved.get("sourceSelector")).get("packagePath")):
        return saved
    return None


def native_handoff_package_selector_archive(snapshot):
    """Keep only exact successful inspection/plan selectors in the owned source archive."""
    messages = tuple(snapshot.get("messages", ()))
    archive = {}
    for call_id, observation in _receipts(messages):
        owner = {"callId": call_id, **observation}
        selector = _package_source_selector(messages, owner)
        if not selector:
            continue
        record_id = _package_selector_archive_id(call_id)
        receipt = {"sourceSelector": selector, "runtimeTool": observation["runtimeTool"],
                   "outcome": {"status": "ok"}, "id": call_id, "callId": call_id, "kind": "receipt"}
        if isinstance(action_id := observation.get("actionId"), str) and action_id:
            receipt["actionId"] = action_id
        archive[record_id] = {"id": record_id, "source": "tool",
                              "text": json.dumps(receipt, ensure_ascii=False, separators=(",", ":"))}
    return archive


def _receipt_messages(snapshot):
    for record in snapshot.get("handoffArchive", {}).values():
        if record.get("source") == "tool":
            group = _read_json(record.get("text"))
            if isinstance(group, list):
                yield from group
    yield from _archived_messages(snapshot)
    yield from snapshot.get("messages", ())


def _package_selector_tool(name):
    if isinstance(name, str):
        short_name = name.removeprefix("vrcforge_").removeprefix("unity_")
        if short_name in {"inspect_outfit_package", "plan_outfit_import"}:
            return short_name
    return None


def _package_source_selector(messages, receipt):
    tool = _package_selector_tool(receipt.get("runtimeTool"))
    call_id = receipt.get("callId")
    if not tool or not call_id or _object(receipt.get("outcome")).get("status") != "ok":
        return {}
    for message in reversed(messages):
        if not isinstance(message, Mapping) or message.get("role") != "assistant":
            continue
        for call in message.get("tool_calls") or ():
            if call.get("id") != call_id:
                continue
            function = _object(call.get("function"))
            name = function.get("name")
            arguments = _object(_read_json(function.get("arguments")))
            if name == "call_tool" or (
                    name in {"vrcforge_runtime_action", "runtime_action"} and arguments.get("action") == "correct"
            ):
                name, arguments = arguments.get("tool"), _object(arguments.get("arguments"))
            if _package_selector_tool(name) != tool:
                return {}
            selectors = ("packagePath",) if tool == "inspect_outfit_package" else (
                "packagePath", "selectedUnityPackage", "selectedPrefab", "dependencyMode",
            )
            result = {key: value for key in selectors if isinstance(value := arguments.get(key), str)
                      and value.strip() and redact_credential_text(value) == value
                      and not any(marker in value for marker in ("{{path:", "{{project:", "[REDACTED", "<path redacted>"))}
            if result.get("dependencyMode") not in {"auto", "selected_only"}:
                result.pop("dependencyMode", None)
            return result if "packagePath" in result else {}
    return {}
