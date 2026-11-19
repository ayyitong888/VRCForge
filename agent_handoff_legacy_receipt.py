"""Read write proof from the structured projection used by old native histories."""

import json
import re

from agent_tool_result_reader import PAGE_SCHEMA


_IMPORT = "vrcforge_import_outfit_package"
_IMPORT_STATUS = "vrcforge_get_unitypackage_import_status"


def _object(value):
    return value if isinstance(value, dict) else {}


def _projected_object(text, field):
    _, marker, encoded = text.partition(f"; {field}=")
    if marker:
        try:
            result, end = json.JSONDecoder().raw_decode(encoded)
        except ValueError:
            return {}
        if isinstance(result, dict) and (not encoded[end:] or encoded[end:].startswith("; ")):
            return result
    return {}


def _owner_prefix(text, tool, result_ref, action_id):
    page = _projected_object(text, "modelInformationPage")
    if (not result_ref or page.get("schema") != PAGE_SCHEMA or page.get("source") != "owner_model"
            or page.get("ok") is not True or page.get("authority") != "untrusted_tool_output"
            or page.get("sourceTool") != tool or page.get("resultRef") != result_ref
            or page.get("jsonPointer") != "/text" or page.get("textOffset") != 0):
        return ""
    items = page.get("items")
    if isinstance(items, list) and len(items) == 1:
        item = _object(items[0])
        value = item.get("value")
        if item.get("jsonPointer") == "/text" and isinstance(value, str) and value.startswith(f"actionId={action_id}; "):
            return value
    return ""


def legacy_write_receipt_fields(observation):
    tool = observation.get("runtimeTool")
    if ((observation.get("kind") != "write" and tool != _IMPORT_STATUS)
            or not isinstance(text := observation.get("observation"), str)):
        return {}
    action_id = observation.get("actionId")
    if not isinstance(action_id, str) or not text.startswith(f"actionId={action_id}; "):
        return {}
    outcome = _object(observation.get("outcome"))
    canonical = _projected_object(text, "canonicalOutcome")
    if not canonical or canonical.get("status") != outcome.get("status"):
        return {}
    fields = {}
    if commit_state := canonical.get("commitState"):
        fields["commitState"] = commit_state
    if checkpoint := re.match(r"actionId=[^;]+; checkpointId=(ckpt_[A-Za-z0-9_]+); ", text):
        fields["checkpointId"] = checkpoint[1]
    continuation = _projected_object(text, "resultContinuation")
    result_ref = continuation.get("resultRef")
    if isinstance(result_ref, str) and re.fullmatch(r"result_[0-9a-f]{32}", result_ref):
        fields["resultRef"] = result_ref
    if outcome.get("status") == "ok" and canonical.get("committed") is True and canonical.get("commitState") == "complete":
        if outcome.get("readbackState") == "verified":
            fields["readbackState"] = "verified"
        elif tool == _IMPORT:
            evidence = _object(_projected_object(text, "structuredEvidence").get("data"))
            readback = _object(evidence.get("contentReadback"))
            prefix = _owner_prefix(text, tool, fields.get("resultRef"), action_id)
            if (readback.get("verified") is True and type(readback.get("checked")) is int and readback["checked"] > 0
                    or re.search(r'"contentReadback":\{"checked":[1-9][0-9]*,"verified":true[,}]', prefix)):
                fields["readbackState"] = "verified"
        elif tool == _IMPORT_STATUS:
            # Core emits completed + committed only after reading the expected assets.
            prefix = _owner_prefix(text, tool, fields.get("resultRef"), action_id)
            if job := re.search(r"; ok=True; status=completed; jobId=([0-9a-f]{32}); completedReadbackFailureCount=0; ", prefix):
                fields.update(readbackState="verified", jobId=job[1])
    if (tool == _IMPORT and canonical.get("mutationStarted") is True
            and canonical.get("commitState") == "unknown" and canonical.get("safeToRetry") is False):
        evidence = _object(_projected_object(text, "structuredEvidence").get("data"))
        job_id = evidence.get("jobId")
        if isinstance(job_id, str) and re.fullmatch(r"[0-9a-f]{32}", job_id):
            fields.update(jobId=job_id, readbackState="unknown", interrupted=True)
    return fields
