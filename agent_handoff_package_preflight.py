"""Extract decisions from owned outfit import plans without promoting preview to execution."""

import hashlib
import json
import ntpath
import re

from outfit_import_planner import IMPORT_PLAN_SCHEMA
from outfit_import_receipt import package_preview_receipt


_PLAN_TOOLS = {"unity_plan_outfit_import", "vrcforge_plan_outfit_import", "plan_outfit_import"}
_DECODER = json.JSONDecoder()


def _object_field_prefix(document, field):
    """Locate a field of a JSON object even when a later page cuts a different value."""
    remaining = document.lstrip()
    if not remaining.startswith("{"):
        return ""
    remaining = remaining[1:].lstrip()
    while remaining and not remaining.startswith("}"):
        key, end = _DECODER.raw_decode(remaining)
        remaining = remaining[end:].lstrip()
        if not isinstance(key, str) or not remaining.startswith(":"):
            return ""
        remaining = remaining[1:].lstrip()
        if key == field:
            return remaining
        _, end = _DECODER.raw_decode(remaining)
        remaining = remaining[end:].lstrip()
        if not remaining.startswith(","):
            return ""
        remaining = remaining[1:].lstrip()
    return ""


def _observation_field(observation, field):
    prefix = "; " + field + "="
    offset = observation.rfind(prefix)
    if offset < 0:
        return {}
    value, _ = _DECODER.raw_decode(observation[offset + len(prefix):])
    return value if isinstance(value, dict) else {}


def native_package_preflight_record(result, ref=None):
    """Record the typed readiness of the exact preview produced by the handler."""
    if result.get("schema") != IMPORT_PLAN_SCHEMA or result.get("preview") is not True:
        return None
    disposition = package_preview_receipt(result)
    ready = disposition.get("readyToApply")
    dep = result.get("dependencyPreflight")
    if not isinstance(dep, dict) or type(ready) is not bool or type(dep.get("readyForImport")) is not bool:
        return None
    order, compatibility = dep.get("packageOrder"), dep.get("compatibility")
    if not isinstance(order, dict) or not isinstance(compatibility, dict):
        return None
    return {"schema": dep.get("schema"), "readyForImport": ready and dep["readyForImport"],
            "blockingIssueCount": dep.get("blockingIssueCount"),
            "packageOrder": {key: order.get(key) for key in
                             ("blockingBeforeImport", "dependencyMode", "selectedUnityPackage")},
            "compatibility": {key: compatibility.get(key) for key in
                              ("blockingBeforeImport", "baseAvatarName", "status")},
            **({"resultRef": ref} if isinstance(ref, str) else {})}


def _owner_preflight(observation):
    if observation.get("runtimeTool") != "vrcforge_plan_outfit_import":
        return None
    modern = observation.get("packageImportPreflight")
    if isinstance(modern, dict) and modern.get("schema") == "vrcforge.outfit_dependency_preflight.v1":
        return modern, modern.get("resultRef")
    rendered = observation.get("observation")
    if not isinstance(rendered, str):
        return None
    continuation = _observation_field(rendered, "resultContinuation")
    ref = continuation.get("resultRef")
    page = _observation_field(rendered, "modelInformationPage")
    if page:
        if (page.get("schema") != "vrcforge.tool_result_page.v1" or page.get("source") != "owner_model"
                or page.get("sourceTool") != observation["runtimeTool"] or page.get("resultRef") != ref
                or page.get("textOffset") != 0 or page.get("offset") != 0 or page.get("jsonPointer") != "/text"):
            return None
        items = page.get("items")
        if not isinstance(items, list) or len(items) != 1 or items[0].get("jsonPointer") != "/text":
            return None
        rendered = items[0].get("value")
        if not isinstance(rendered, str):
            return None
    separator = "; structuredEvidence="
    if separator not in rendered:
        return None
    envelope = rendered.rsplit(separator, 1)[1]
    data = _object_field_prefix(envelope, "data")
    if (_DECODER.raw_decode(_object_field_prefix(data, "schema"))[0] != IMPORT_PLAN_SCHEMA
            or _DECODER.raw_decode(_object_field_prefix(data, "preview"))[0] is not True
            or _DECODER.raw_decode(_object_field_prefix(data, "ok"))[0] is not True):
        return None
    preflight = _DECODER.raw_decode(_object_field_prefix(data, "dependencyPreflight"))[0]
    if not isinstance(preflight, dict) or preflight.get("schema") != "vrcforge.outfit_dependency_preflight.v1":
        return None
    # The complete result can also carry the canonical plan-level disposition.
    # In a truncated owner-model page, the complete typed preflight above is
    # the available scope-specific proof.
    try:
        complete, _ = _DECODER.raw_decode(data)
    except ValueError:
        pass
    else:
        canonical = package_preview_receipt(complete)
        if canonical.get("readyToApply") is False and preflight.get("readyForImport") is True:
            preflight = {**preflight, "readyForImport": False}
    return preflight, ref


def _package_scope(arguments, selected_package):
    fields = ("packagePath", "projectPath", "selectedPrefab", "baseAvatarName", "dependencyMode")
    if arguments.get("targetFolder"):
        fields += ("targetFolder",)
    return hashlib.sha256("\0".join(
        ntpath.normcase(ntpath.normpath(value)) if value else ""
        for value in (*(arguments.get(key, "") for key in fields), selected_package)
    ).encode()).hexdigest()[:32]


def native_package_preflight_evidence(messages):
    calls = {}
    rows = {}
    for message in messages:
        if message.get("role") == "assistant":
            for call in message.get("tool_calls") or ():
                fn = call.get("function") or {}
                try:
                    arguments = json.loads(fn.get("arguments") or "{}")
                except (ValueError, TypeError):
                    continue
                if not isinstance(arguments, dict):
                    continue
                name = arguments.get("tool", fn.get("name"))
                if name not in _PLAN_TOOLS:
                    continue
                if arguments.get("tool"):
                    arguments = arguments.get("arguments")
                if not isinstance(arguments, dict) or not isinstance(arguments.get("packagePath"), str):
                    continue
                calls[call["id"]] = {key: value for key, value in arguments.items() if isinstance(value, str)}
            continue
        identity = message.get("tool_call_id")
        if message.get("role") != "tool" or identity not in calls:
            continue
        arguments = calls[identity]
        try:
            result = json.loads(message.get("content") or "{}")
            payload = next((parsed for observation in result.get("observations", ())
                            if (parsed := _owner_preflight(observation)) is not None), None)
            if payload is None:
                continue
            preflight, ref = payload
            order, compatibility = preflight.get("packageOrder"), preflight.get("compatibility")
            if not isinstance(order, dict) or not isinstance(compatibility, dict):
                continue
            selected_package = order.get("selectedUnityPackage")
            ready = preflight.get("readyForImport")
            count = preflight.get("blockingIssueCount")
            mode = order.get("dependencyMode")
            avatar = compatibility.get("baseAvatarName")
            if (not isinstance(selected_package, str) or not selected_package or type(ready) is not bool
                    or type(count) is not int or count < 0
                    or (ready and (count or order.get("blockingBeforeImport") is not False
                                   or compatibility.get("blockingBeforeImport") is not False))
                    or (arguments.get("selectedUnityPackage") and arguments["selectedUnityPackage"] != selected_package)
                    or not isinstance(mode, str) or (arguments.get("dependencyMode") and arguments["dependencyMode"] != mode)
                    or (isinstance(avatar, str) and arguments.get("baseAvatarName")
                        and arguments["baseAvatarName"].casefold() != avatar.casefold())):
                continue
        except (KeyError, TypeError, ValueError, AttributeError):
            continue
        scope = _package_scope({**arguments, "dependencyMode": mode}, selected_package)
        rows[scope] = {"callId": identity, "packagePreflight": {
            "scopeKey": scope,
            "receiptId": identity,
            "ready": ready,
            "blockingIssueCount": count,
            "packageName": ntpath.basename(arguments["packagePath"]),
            "selectedUnityPackage": selected_package,
            "compatibility": compatibility.get("status"),
            "dependencyMode": mode,
            **{key: arguments[key] for key in ("selectedPrefab", "baseAvatarName", "targetFolder") if key in arguments},
            **({"resultRef": ref} if isinstance(ref, str) and re.fullmatch(r"result_[0-9a-f]{32}", ref) else {}),
        }}
    return list(rows.values())
