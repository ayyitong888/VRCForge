"""Project an archived package inspection's source and coverage into the model handoff."""

import json
import re

from context_compaction import NATIVE_TRANSACTION_PREFIX


_PACKAGE_TOOLS = {"vrcforge_inspect_outfit_package", "vrcforge_plan_outfit_import"}
_TOOL_ROW = re.compile(r'\{"role": "tool", "tool_call_id": "([A-Za-z0-9_:-]+)", "content": "')
_DECODER = json.JSONDecoder()


def _package_inventories(group):
    calls = {call.get("id") for row in group if isinstance(row, dict) and row.get("role") == "assistant"
             for call in row.get("tool_calls") or () if isinstance(call, dict)}
    for row in group:
        if not isinstance(row, dict) or row.get("role") != "tool" or row.get("tool_call_id") not in calls:
            continue
        try:
            result = json.loads(row["content"])
        except (KeyError, ValueError, TypeError):
            continue
        if not isinstance(result, dict):
            continue
        for obs in result.get("observations", ()):
            if not isinstance(obs, dict) or obs.get("runtimeTool") not in _PACKAGE_TOOLS:
                continue
            text = obs.get("observation")
            if not isinstance(text, str) or "; importReceipt=" not in text:
                continue
            encoded = text.partition("; importReceipt=")[2]
            try:
                receipt, end = _DECODER.raw_decode(encoded)
                source, coverage = receipt["source"], receipt["inventory"]
                name, scope = source["name"], source["scopeKey"]
            except (TypeError, KeyError, ValueError):
                continue
            if not isinstance(name, str) or not isinstance(scope, str) or not isinstance(coverage, dict):
                continue
            fact = {"packageName": name, "callId": row["tool_call_id"]}
            if type(coverage.get("truncated")) is bool:
                fact["partialInventory"] = coverage["truncated"]
            if type(coverage.get("materialCount")) is int:
                fact["observedMaterialCount"] = coverage["materialCount"]
            remaining = encoded[end:]
            if remaining.startswith("; resultContinuation="):
                try:
                    continuation, _ = _DECODER.raw_decode(remaining.partition("=")[2])
                    ref = continuation.get("resultRef")
                except (ValueError, AttributeError):
                    ref = None
                if isinstance(ref, str) and re.fullmatch(r"result_[0-9a-f]{32}", ref):
                    fact["historicalResultRef"] = ref
            yield scope, fact


def _redacted_inventories(text):
    # Older archives redacted already-serialized nested JSON, masking some
    # quotes along with the path. Only recover fields from a paired tool row.
    sections = _TOOL_ROW.split(text)
    for call_id, body in zip(sections[1::2], sections[2::2]):
        if f'"id": "{call_id}"' not in sections[0]:
            continue
        body = re.sub(r'\\+"', '"', re.sub(r'\{\{[a-z]+:[0-9a-f]{12}\}\}', '', body))
        tool = re.search(r'"runtimeTool":\s*"([a-z_]+)",\s*"outcome":\s*\{\s*"status":\s*"ok"', body[:1000])
        if not tool or tool[1] not in _PACKAGE_TOOLS:
            continue
        source = re.search(r'"source"\s*:\s*\{\s*"name"\s*:\s*"([^"/\\]{1,240})"', body)
        ref = re.search(r'; resultContinuation=\{"resultRef":"(result_[0-9a-f]{32})"', body)
        if not source or not ref:
            continue
        fact = {"packageName": source[1], "historicalResultRef": ref[1], "callId": call_id}
        if re.search(r'"nestedPathnamesTruncated"\s*:\s*true', body):
            fact["partialInventory"] = True
        count = re.search(r'; summary=\{[^{}]*?"materialCount":(\d+)', body)
        if count:
            fact["observedMaterialCount"] = int(count[1])
        yield call_id, fact


def archived_package_inventories(snapshot):
    """Each fact points to its own historical receipt. It does not attest installation."""
    facts = {}
    for recovery in snapshot.get("compactionRecovery", ()):
        for index, row in enumerate(recovery.get("sourceEntries", ())):
            text = row.get("text", "")
            if (row.get("role") != "tool" or not text.startswith(NATIVE_TRANSACTION_PREFIX)
                    or not any(tool in text for tool in _PACKAGE_TOOLS)):
                continue
            try:
                group = json.loads(text[len(NATIVE_TRANSACTION_PREFIX):])
            except ValueError:
                rows = _redacted_inventories(text)
            else:
                rows = _package_inventories(group) if isinstance(group, list) else ()
            for key, fact in rows:
                facts[key] = {**fact, "sourceDigest": recovery.get("sourceDigest"), "entryIndex": index}
    # A newer inspection in the visible history owns its scope's current state.
    for key, _ in _package_inventories(snapshot.get("messages", ())):
        facts.pop(key, None)
    return [fact for fact in facts.values() if fact.get("partialInventory") is True]
