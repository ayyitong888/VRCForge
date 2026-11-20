"""Recover outstanding operations from host-owned native tool receipts."""

from collections.abc import Mapping
from itertools import chain
from hashlib import sha256
import json
import ntpath
import re

from context_compaction import NATIVE_TRANSACTION_PREFIX
from agent_tool_result_contract import job_receipt
from agent_tool_result_reader import INPUT_SCHEMA, PAGE_SCHEMA, TOOL_NAME
from credential_redaction import redact_credential_text
from outfit_package_inspector import ARCHIVE_CURSOR_PATTERN, INSPECTION_SCHEMA
from agent_handoff_legacy_receipt import legacy_write_receipt_fields


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


_PACKAGE_INSPECT = "vrcforge_inspect_outfit_package"
_PACKAGE_PLAN = "vrcforge_plan_outfit_import"
_IMPORT_STATUS = "vrcforge_get_unitypackage_import_status"


def _archive_source_read(result):
    if result.get("schema") != INSPECTION_SCHEMA:
        return {}
    source_read = _object(result.get("sourceRead"))
    if source_read:
        coverage = {key: source_read[key] for key in ("status", "countUnit", "reason")
                    if isinstance(source_read.get(key), str)}
        coverage.update({key: source_read[key] for key in ("processedCount", "totalCount", "remainingCount", "remainingAtLeast")
                         if key in source_read and (source_read[key] is None or type(source_read[key]) is int)})
        request = _object(source_read.get("nextRequest"))
        args = _object(request.get("arguments"))
        cursor = args.get("archiveCursor")
        if (source_read.get("status") == "partial" and request.get("tool") == _PACKAGE_INSPECT
                and isinstance(cursor, str) and re.fullmatch(ARCHIVE_CURSOR_PATTERN, cursor)):
            coverage["nextRequest"] = {"tool": _PACKAGE_INSPECT, "arguments": {"archiveCursor": cursor}}
        count, total = coverage.get("processedCount"), coverage.get("totalCount")
        if coverage.get("status") == "complete" and (
            request or type(count) is not int or count != total or coverage.get("remainingCount") != 0
        ):
            coverage["status"] = "unknown"
        return coverage
    summary = _object(result.get("summary"))
    packages = result.get("unityPackages")
    if summary.get("truncated") is True or summary.get("nestedPathnamesTruncated") is True:
        return {"status": "partial", "remainingCount": None, "reason": "historical_archive_cursor_unavailable"}
    if (result.get("ok") is True and summary.get("truncated") is False
            and summary.get("archiveCensusComplete") is not False and type(summary.get("entryCount")) is int
            and isinstance(packages, list) and all(
                isinstance(row, Mapping) and row.get("pathnamesEvidenceComplete") is True
                and type(row.get("pathnamesEvidenceCount")) is int
                and isinstance(row.get("pathnamesEvidence"), list)
                and row["pathnamesEvidenceCount"] == len(row["pathnamesEvidence"])
                for row in packages
            )):
        count = summary["entryCount"] + int(summary.get("nestedPathnameCount") or 0)
        return {"status": "complete", "countUnit": "inventory_entries", "processedCount": count,
                "totalCount": count, "remainingCount": 0}
    return {"status": "unknown", "remainingCount": None, "reason": "source_coverage_unknown"}


def _tool_scope(tool, args):
    if not isinstance(tool, str) or not args or tool in {TOOL_NAME, "read_tool_result"}:
        return {}
    if tool.startswith("unity_"):
        tool = "vrcforge_" + tool.removeprefix("unity_")
    ignored = {"archiveCursor", "maxEntries", "max_entries"} if tool in {_PACKAGE_INSPECT, _PACKAGE_PLAN} else set()
    scope = {key: value for key, value in args.items() if key not in ignored}
    selectors = {key: value for key, value in scope.items() if key in {
        "packagePath", "package_path", "selectedUnityPackage", "selectedPrefab", "projectPath",
        "avatarPath", "assetPath", "path", "objectPath", "resultRef", "jsonPointer", "jobId",
        "executionId", "previewId", "checkpointId", "project_path", "job_id",
    } and isinstance(value, str) and redact_credential_text(value) == value}
    if not selectors:
        return {}
    return {"scopeId": sha256(json.dumps([tool, scope], sort_keys=True).encode()).hexdigest(),
            "selectors": selectors}


def archive_source_read_for_step(step):
    tool = step.get("tool")
    result = _object(step.get("result"))
    if tool == _PACKAGE_INSPECT:
        return _archive_source_read(result)
    if tool == _PACKAGE_PLAN:
        return _archive_source_read(_object(result.get("inspection")))
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
    source_read = archive_source_read_for_step(step)
    recovery = _object(result.get("recovery"))
    interrupted = (
        outcome.get("status") in {"interrupted", "cancelled", "canceled"}
        or result.get("status") in {"interrupted", "cancelled", "canceled"}
        or recovery.get("status") in {"interrupted", "cancelled", "canceled"}
    )
    fields = {
        "checkpointId": outcome.get("checkpointId") or step.get("checkpointId")
        or result.get("checkpointId") or trace.get("checkpointId") or detail.get("checkpointId")
        or recovery.get("checkpointId"),
        "executionId": outcome.get("executionId") or step.get("executionId")
        or trace.get("executionId") or recovery.get("executionId") or result.get("executionId")
        or detail.get("executionId") or outcome.get("operationId")
        or result.get("operationId") or detail.get("operationId"),
        "previewId": result.get("previewId") or detail.get("previewId"),
        "jobId": outcome.get("jobId") or job.get("jobId") or job.get("job_id"),
        "approvalId": outcome.get("approvalId") or result.get("approvalId") or _object(result.get("approval")).get("id"),
        "commitState": outcome.get("commitState") or job.get("commitState") or result.get("commitState"),
        "readbackState": outcome.get("readbackState") or job.get("readbackState") or result.get("readbackState"),
        "nextAction": outcome.get("nextAction") or result.get("nextAction") or detail.get("nextAction"),
        "errorCode": _object(outcome.get("error")).get("code") or result.get("errorCode") or result.get("code"),
        "resultStatus": detail.get("status") or result.get("status"),
        "resultRef": result_ref if _is_result_ref(result_ref) else None,
    }
    return {**{key: value for key, value in fields.items() if isinstance(value, str) and value.strip()},
            **({"resultPage": result_page} if result_page else {}),
            **({"sourceRead": source_read} if source_read else {}),
            **({"targetEffectVerified": outcome["targetEffectVerified"]} if type(outcome.get("targetEffectVerified")) is bool else {}),
            **({"interrupted": True} if interrupted else {})}


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
            cursor = tool_args.get("archiveCursor")
            calls[call["id"]] = {
                **({"archiveCursor": cursor} if isinstance(cursor, str) and re.fullmatch(ARCHIVE_CURSOR_PATTERN, cursor) else {}),
                **_tool_scope(args.get("tool", function.get("name")), tool_args),
                **({"resultRef": ref} if _is_result_ref(ref) else {}),
                **({"correctsActionId": correction} if is_correction else {}),
                **({"correctedSelector": selector} if selector else {}),
            }
    return calls


def _receipt_fields(observation):
    return _object(observation.get("handoffReceipt")) or legacy_write_receipt_fields(observation)


def native_archive_read_successors(messages):
    """Index later successful pages by the exact cursor submitted by their owner."""
    calls = _call_context(messages)
    successors = {}
    for call_id, observation in _receipts(messages):
        if observation.get("runtimeTool") != _PACKAGE_INSPECT or _object(observation.get("outcome")).get("status") != "ok":
            continue
        cursor = calls.get(call_id, {}).get("archiveCursor")
        coverage = _object(_receipt_fields(observation).get("sourceRead"))
        if cursor and coverage.get("status") == "complete":
            successors[_PACKAGE_INSPECT, cursor] = None
        elif cursor and (request := _object(coverage.get("nextRequest"))) and coverage.get("status") == "partial":
            successors[_PACKAGE_INSPECT, cursor] = request
    return successors


def _identity(call_id, observation, receipt):
    if receipt.get("executionId"):
        return "execution", receipt["executionId"]
    if receipt.get("previewId"):
        return "preview", receipt["previewId"]
    return "receipt", call_id or observation.get("actionId")


def _state(observation, fields, awaiting_consumer):
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
    historical = list(_receipt_messages({**snapshot, "messages": ()}))
    call_context = _call_context(chain(historical, messages))
    last_assistant = next((index for index in range(len(messages) - 1, -1, -1)
                           if messages[index].get("role") == "assistant"), len(messages))
    unconsumed = {call_id for call_id, _ in _receipts(messages[last_assistant + 1:])}
    operations = {}
    readbacks = {}
    archive_scopes = {}
    correction_targets = {row.get("correctsActionId") for row in call_context.values() if row.get("correctsActionId")}
    for call_id, observation in chain(_receipts(historical), _receipts(messages)):
        fields = _receipt_fields(observation)
        kind, identity = _identity(call_id, observation, fields)
        if not isinstance(identity, str) or not identity:
            continue
        tool = observation.get("runtimeTool")
        outcome = _object(observation.get("outcome"))
        result_page = _observed_result_page(observation)
        request = dict(call_context.get(call_id, {}))
        if current_cursor := request.pop("archiveCursor", None):
            request.update(archive_scopes.get(current_cursor, {}))
        # A legacy page is input from the actual reader receipt, and its
        # resultRef must agree with the request when the latter is retained.
        if result_page and request.get("resultRef", result_page["resultRef"]) != result_page["resultRef"]:
            result_page = {}
        if outcome.get("status") != "ok" and "correctedSelector" in request:
            request = {key: value for key, value in request.items() if key != "correctedSelector"}
        job_id = fields.get("jobId")
        selectors = _object(request.get("selectors"))
        requested_job = selectors.get("jobId") or selectors.get("job_id")
        if (job_id and outcome.get("status") == "ok" and fields.get("commitState") == "complete"
                and fields.get("readbackState") in {"verified", "complete"}
                and (tool != _IMPORT_STATUS or requested_job == job_id)):
            project = selectors.get("projectPath") or selectors.get("project_path")
            readbacks.setdefault(job_id, []).append((call_id, project, fields.get("resultRef")))
        action_id = observation.get("actionId")
        source_read = _object(fields.get("sourceRead"))
        observed_text = observation.get("observation", "")
        partial = isinstance(observed_text, str) and (
            "scan input byte limit" in observed_text or '"nestedPathnamesTruncated":true' in observed_text
        )
        if not source_read and (tool == _PACKAGE_INSPECT or tool == _PACKAGE_PLAN and partial):
            source_read = {"status": "partial" if partial else "unknown", "remainingCount": None,
                           "reason": "historical_archive_cursor_unavailable" if partial else "source_coverage_unknown"}
        legacy_result_ref = None
        if source_read and not fields.get("resultRef") and isinstance(observed_text, str):
            match = re.search(r'; resultContinuation=\{"resultRef":"(result_[0-9a-f]{32})"', observed_text)
            if match:
                legacy_result_ref = match[1]
        next_cursor = _object(_object(source_read.get("nextRequest")).get("arguments")).get("archiveCursor")
        if next_cursor and "scopeId" in request:
            archive_scopes[next_cursor] = {key: request[key] for key in ("scopeId", "selectors")}
        preserve_attempt = request.get("scopeId") or request.get("correctsActionId") or action_id in correction_targets
        operations[kind, identity, call_id if preserve_attempt else None] = {
                                      "id": identity, "kind": kind, "callId": call_id, "tool": tool,
                                      "state": _state(observation, fields, call_id in unconsumed), **request,
                                      **({"resultPage": result_page} if result_page else {}),
                                      **({"sourceRead": source_read} if source_read else {}),
                                      **({"interrupted": True} if fields.get("interrupted") is True else {}),
                                      **({"targetEffectVerified": fields["targetEffectVerified"]} if type(fields.get("targetEffectVerified")) is bool else {}),
                                      **{key: value for key, value in {
                                          "actionId": action_id,
                                          "operationKind": observation.get("kind"),
                                          "commitState": fields.get("commitState"),
                                          "readbackState": fields.get("readbackState"),
                                          "checkpointId": fields.get("checkpointId"),
                                          "executionId": fields.get("executionId"),
                                          "outcomeStatus": outcome.get("status"),
                                          "resultStatus": result_page.get("status") or fields.get("resultStatus"),
                                          "failureReason": observation.get("admissionError") or fields.get("errorCode"),
                                          "resultRef": result_page.get("replacementRef") or result_page.get("resultRef")
                                          or fields.get("resultRef") or legacy_result_ref,
                                          "jobId": job_id,
                                      }.items() if isinstance(value, str) and value}}
    for row in operations.values():
        if row["state"] != "readback_pending":
            continue
        selectors = _object(row.get("selectors"))
        project = selectors.get("projectPath") or selectors.get("project_path")
        for settlement, status_project, settlement_ref in readbacks.get(row.get("jobId"), ()):
            if project and status_project and ntpath.normcase(ntpath.normpath(project)) != ntpath.normcase(ntpath.normpath(status_project)):
                continue
            if settlement != row["callId"]:
                row.update(state="done", settlementCallId=settlement)
                if _is_result_ref(settlement_ref):
                    row["settlementResultRef"] = settlement_ref
                break
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


def legacy_handoff_operations(steps):
    """Project raw loop steps through the same receipt logic as native observations."""
    messages = []
    for step in steps:
        if not isinstance(step, Mapping) or not (action_id := step.get("actionId")):
            continue
        tool = step.get("tool")
        if not isinstance(tool, str):
            continue
        args = _object(step.get("arguments") or step.get("requestedArguments") or step.get("args") or step.get("params"))
        if not args and tool in {_PACKAGE_INSPECT, _PACKAGE_PLAN}:
            raw = _object(step.get("result"))
            if tool == _PACKAGE_PLAN:
                raw = _object(raw.get("inspection"))
            if isinstance(path := _object(raw.get("source")).get("path"), str) and path:
                args = {"packagePath": path}
        receipt = native_handoff_receipt_fields(step)
        call_id = step.get("callId") or action_id
        messages.extend((
            {"role": "assistant", "tool_calls": [{"id": call_id, "function": {
                "name": tool, "arguments": json.dumps(args, default=str),
            }}]},
            {"role": "tool", "tool_call_id": call_id, "content": json.dumps({"observations": [{
                "actionId": action_id, "runtimeTool": tool, "kind": step.get("kind"),
                "status": step.get("status"), "outcome": _object(step.get("outcome")),
                "handoffReceipt": receipt,
            }]}, default=str)},
        ))
    if messages:
        messages.append({"role": "assistant"})
    return native_handoff_operations({"messages": messages})


def lookup_native_handoff_receipt(snapshot, execution_id):
    """Read an exact receipt from a snapshot already scoped by its owner."""
    if not isinstance(execution_id, str) or not execution_id:
        return None
    exact = None
    aliases = {}
    messages = tuple(_receipt_messages(snapshot))
    for call_id, observation in _receipts(messages):
        kind, identity = _identity(call_id, observation, _receipt_fields(observation))
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
        receipt = _receipt_fields(result)
        return {**({"sourceSelector": selector} if selector else {}), **result,
                **({"handoffReceipt": receipt} if receipt else {})}
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


def native_handoff_package_inputs(snapshot, graph):
    """Resolve each verified package preflight to its original owner arguments."""
    inputs = {}
    for node_id, node in graph["nodes"].items():
        proof = _object(node.get("packagePreflight"))
        receipt_id = proof.get("receiptId")
        if (node.get("status") != "done" or proof.get("ready") is not True
                or {"type": "receipt", "id": receipt_id} not in node.get("evidence", ())):
            continue
        receipt = _package_selector_receipt(snapshot, receipt_id)
        if not receipt or _package_selector_tool(receipt["runtimeTool"]) != "plan_outfit_import":
            continue
        selector = receipt["sourceSelector"]
        path = selector["packagePath"]
        if (not isinstance(path, str) or ntpath.basename(path) != proof.get("packageName")
                or any(selector[key] != proof.get(key)
                       for key in ("selectedPrefab", "selectedUnityPackage", "dependencyMode") if key in selector)):
            continue
        inputs[node_id] = {"receiptId": receipt_id, "packagePath": path,
                           **{key: proof[key] for key in ("selectedUnityPackage", "selectedPrefab", "dependencyMode",
                                                         "baseAvatarName", "targetFolder") if key in proof}}
    return inputs


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
