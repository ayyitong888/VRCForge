"""Read bounded pages of already executed structured results in one runtime turn.

Authority and borrowed steps are gateway-bound for a callback's lifetime. This
module owns no raw-result store, files, processes, transport or Unity capability.
"""
from __future__ import annotations

from contextlib import contextmanager
from contextvars import ContextVar
from dataclasses import dataclass
import hashlib
import json
import re
from typing import Any, Callable, Iterator, Mapping

from planner_structured_tool_evidence import _identity_key, _private_or_opaque, project_structured_tool_evidence

TOOL_NAME = "vrcforge_read_tool_result"
PAGE_SCHEMA = "vrcforge.tool_result_page.v1"
MAX_PAGE_CHARS = 12000
INPUT_SCHEMA = {
    "type": "object",
    "required": ["resultRef"],
    "additionalProperties": False,
    "properties": {
        "resultRef": {"type": "string", "pattern": "^result_[0-9a-f]{32}$",
                      "description": "Exact current-turn resultRef from resultContinuation; never guess or reuse another turn's reference."},
        "jsonPointer": {"type": "string", "maxLength": 1024, "default": "",
                        "pattern": r"^(?:/(?:[^~/]|~[01])*){0,32}$",
                        "description": "RFC6901 pointer: empty string selects root; otherwise keep the leading slash, e.g. /parameters or /layers/0/states. Escape a key's slash as ~1 and tilde as ~0. Copy exact pointers from returned rows."},
        "offset": {"type": "integer", "minimum": 0, "default": 0,
                   "description": "Zero-based item or field offset within the selected collection; at most its returned count. Use nextRequest to continue."},
        "limit": {"type": "integer", "minimum": 1, "maximum": 20, "default": 6,
                  "description": "Maximum items in this page (1 through 20). The character budget may return fewer; follow nextRequest when hasMore is true."},
        "textOffset": {"type": "integer", "minimum": 0,
                       "description": "Only for an exact text field with offset=0: character offset in its sanitized text, independent of item offset/limit. Copy nextRequest to retrieve remaining text."},
        "source": {"type": "string", "enum": ["result", "owner_model"], "default": "result",
                   "description": "Use owner_model only with the owner-validated model payload attached to this exact current-turn step."},
    },
}
# Existing owner-validated channels must not gain a generic raw-result escape.
_RESTRICTED = frozenset({
    TOOL_NAME, "vrcforge_list_internal_tool_blocks", "vrcforge_load_internal_tool_block",
    "vrcforge_unload_internal_tool_block", "vrcforge_get_compile_errors", "vrcforge_read_recent_logs",
    "vrcforge_agent_desktop_action", "vrcforge_capture_screenshot", "vrcforge_capture_multi_view", "vrcforge_capture_multi_screenshot",
    "vrcforge_vision_audit", "vrcforge_vision_audit_multi", "vrcforge_read_text_file",
    "vrcforge_search_text", "vrcforge_find_files", "vrcforge_list_directory",
    "vrcforge_web_fetch", "vrcforge_web_search", "shell", "unity_shell",
    "vrcforge_execute_shell", "vrcforge_shell_process", "vrcforge_list_memory",
    "vrcforge_remember_memory", "vrcforge_delete_memory", "vrcforge_read_installed_skill",
    "vrcforge_list_installed_skills",
})


@dataclass(frozen=True)
class ResultReadContext:
    session_id: str
    turn_id: str
    project_root: str
    steps: tuple[Mapping[str, Any], ...]


_CONTEXT: ContextVar[ResultReadContext | None] = ContextVar("tool_result_read_context", default=None)


@contextmanager
def bind_tool_result_context(session_id: str, turn_id: str, project_root: str,
                             steps: list[dict[str, Any]]) -> Iterator[None]:
    token = _CONTEXT.set(ResultReadContext(session_id, turn_id, project_root, tuple(steps)))
    try:
        yield
    finally:
        _CONTEXT.reset(token)


def eligible_result(tool: str, result: object) -> bool:
    return tool not in _RESTRICTED and isinstance(result, (dict, list))


def result_reference(session_id: str, turn_id: str, project_root: str, step: Mapping[str, Any]) -> str:
    identity = [session_id, turn_id, project_root, step.get("index"), step.get("actionId"), step.get("tool")]
    return "result_" + hashlib.sha256(json.dumps(identity, ensure_ascii=False).encode()).hexdigest()[:32]


def _size(value: object) -> int:
    return len(json.dumps(value, ensure_ascii=False, separators=(",", ":"), allow_nan=False))


def _pointer(parent: str, name: object) -> str:
    return parent + "/" + str(name).replace("~", "~0").replace("/", "~1")


def page_next_request_arguments(page: Mapping[str, Any]) -> dict[str, Any] | None:
    """Recognize the bounded reader continuation before generic log summarization.

    Only the validated reader cursor fields may bypass path shortening. Page content and
    unrelated arguments retain the existing recursive redaction behavior.
    """
    request = page.get("nextRequest")
    if (page.get("schema") != PAGE_SCHEMA or page.get("authority") != "untrusted_tool_output"
            or page.get("ok") is not True or page.get("hasMore") is not True
            or not isinstance(request, dict) or set(request) != {"tool", "arguments"}
            or request.get("tool") != TOOL_NAME):
        return None
    args = request.get("arguments")
    required = {"resultRef", "jsonPointer", "offset", "limit"}
    if not isinstance(args, dict) or not required <= set(args) or set(args) - required - {"textOffset", "source"}:
        return None
    source = args.get("source", "result")
    if source not in ("result", "owner_model") or source != page.get("source", "result"):
        return None
    ref, pointer = args.get("resultRef"), args.get("jsonPointer")
    if (not isinstance(ref, str) or re.fullmatch(r"result_[0-9a-f]{32}", ref) is None
            or ref != page.get("resultRef") or not isinstance(pointer, str)
            or len(pointer) > 1024 or pointer != page.get("jsonPointer")
            or re.fullmatch(INPUT_SCHEMA["properties"]["jsonPointer"]["pattern"], pointer) is None):
        return None
    offset, limit, current, count = args.get("offset"), args.get("limit"), page.get("offset"), page.get("totalItems")
    if any(type(value) is not int for value in (offset, limit, current, count)) or not 1 <= limit <= 20:
        return None
    if "textOffset" in args:
        start, following, total = page.get("textOffset"), args["textOffset"], page.get("textTotalChars")
        if (any(type(value) is not int for value in (start, following, total))
                or not 0 <= start < following < total or offset != 0 or current != 0 or count != 1):
            return None
        items = page.get("items")
        if (not isinstance(items, list) or len(items) != 1 or not isinstance(items[0], dict)
                or not isinstance(items[0].get("value"), str) or items[0].get("jsonPointer") != pointer
                or following != start + len(items[0]["value"])):
            return None
    elif not 0 <= current < offset < count:
        return None
    return dict(args)


def _safe_key(name: object, value: object, sanitize: Callable[[object, int], str]) -> bool:
    return isinstance(name, str) and not _private_or_opaque(name, value) and sanitize(name, 100) == name


def collection_manifest(value: object, sanitize: Callable[[object, int], str], parent: str = "") -> list[dict[str, Any]]:
    if isinstance(value, list):
        return [{"jsonPointer": parent, "type": "array", "count": len(value)}]
    rows: list[dict[str, Any]] = []
    if isinstance(value, dict):
        for name, child in value.items():
            if isinstance(child, (dict, list)) and _safe_key(name, child, sanitize):
                row = {"jsonPointer": _pointer(parent, name), "type": "array" if isinstance(child, list) else "object", "count": len(child)}
                if _size(rows + [row]) > 1600 or len(rows) >= 12:
                    break
                rows.append(row)
    return rows


def result_continuation(session_id: str, turn_id: str, project_root: str, step: Mapping[str, Any],
                        sanitize: Callable[[object, int], str]) -> dict[str, Any]:
    if not eligible_result(str(step.get("tool") or ""), step.get("result")):
        return {}
    ref = result_reference(session_id, turn_id, project_root, step)
    return {"resultRef": ref, "scope": "current_turn", "sourceStep": step["index"],
            "collections": collection_manifest(step["result"], sanitize),
            "nextRequest": {"tool": TOOL_NAME, "arguments": {"resultRef": ref, "jsonPointer": "", "offset": 0}},
            "instructions": "Read retained data by jsonPointer and offset without rerunning its tool. Collection counts are exact; offsets are zero-based. Root pages reveal further fields. References expire when this turn ends."}


def model_payload_continuation(session_id: str, turn_id: str, project_root: str,
                               step: Mapping[str, Any], payload: object,
                               sanitize: Callable[[object, int], str], *,
                               authority: str = "untrusted_tool_output") -> dict[str, Any]:
    """Describe an owner-validated model payload without creating another store.

    The gateway must attach this descriptor to the same step that owns ``resultRead``
    and retain the payload in that step for the lifetime of the bound ContextVar.
    This helper deliberately accepts a payload supplied by the owner only; the
    reader never accepts an arbitrary payload or field name from model arguments.
    """
    # Restricted tools keep their raw result inaccessible; an owner may still
    # publish a separately projected dict/list for model reading.
    if not isinstance(payload, (dict, list, str)):
        return {}
    ref = result_reference(session_id, turn_id, project_root, step)
    return {"resultRef": ref, "source": "owner_model", "scope": "current_turn",
            "sourceStep": step["index"], "authority": authority,
            "collections": collection_manifest(payload, sanitize),
            "nextRequest": {"tool": TOOL_NAME, "arguments": {
                "resultRef": ref, "source": "owner_model", "jsonPointer": "", "offset": 0}},
            "instructions": "Read the owner-validated model payload by jsonPointer and offset. References expire when this turn ends."}


def retain_model_information(session_id: str, turn_id: str, project_root: str,
                             step: Mapping[str, Any], payload: object,
                             sanitize: Callable[[object, int], str], *,
                             authority: str = "untrusted_tool_output") -> dict[str, Any]:
    """Return the step attachments for an owner-approved model payload.

    The caller owns the authorization decision. This function only packages the
    payload into the existing step lifetime; it creates no store or transport.
    The gateway should merge the returned mapping into both step and loop_state.
    """
    if not authority or not isinstance(payload, (dict, list, str)):
        return {}
    descriptor = model_payload_continuation(session_id, turn_id, project_root, step, payload, sanitize,
                                             authority=authority)
    if not descriptor:
        return {}
    model_information = {"authority": authority, "retainedBy": "agent_tool_result_reader", "payload": payload}
    # Generate only the first bounded page through the existing reader engine.
    # The temporary context contains this step alone and is discarded before
    # returning; no second store or alternate pagination implementation exists.
    probe_step = {**dict(step), "modelInformation": model_information,
                  "modelInformationRead": descriptor}
    pointer = "/text" if isinstance(payload, dict) and isinstance(payload.get("text"), str) else ""
    with bind_tool_result_context(session_id, turn_id, project_root, [probe_step]):
        first_page = read_tool_result({
            "resultRef": descriptor["resultRef"], "source": "owner_model",
            "jsonPointer": pointer, "offset": 0, "limit": 6,
        }, sanitize=sanitize)
    return {"modelInformation": model_information,
            "modelInformationRead": {**descriptor, "page": first_page}}


def _select(root: object, pointer: str, sanitize: Callable[[object, int], str]) -> object:
    if not isinstance(pointer, str) or len(pointer) > 1024 or (pointer and not pointer.startswith("/")):
        raise ValueError("jsonPointer must be a bounded RFC6901 pointer")
    parts = pointer.split("/")[1:] if pointer else []
    if len(parts) > 32:
        raise ValueError("jsonPointer is too deep")
    value = root
    for part in parts:
        if re.search(r"~(?![01])", part):
            raise ValueError("Invalid JSON pointer escape")
        key = part.replace("~1", "/").replace("~0", "~")
        if isinstance(value, dict):
            if key not in value or not _safe_key(key, value[key], sanitize):
                raise PermissionError("Requested result field is unavailable")
            value = value[key]
        elif isinstance(value, list) and re.fullmatch(r"0|[1-9][0-9]*", key):
            index = int(key)
            if index >= len(value):
                raise ValueError("Result array index is outside its range")
            value = value[index]
        else:
            raise ValueError("JSON pointer does not resolve to a retained result field")
    return value


def _source_constraints(root: object, pointer: str, sanitize: Callable[[object, int], str]) -> tuple[list[dict[str, Any]], bool]:
    """Carry source uncertainty into narrow reads without traversing sibling data."""
    keys = ("classification", "resolutionStatus", "candidateCount", "analysisRequired",
            "hasAmbiguousDestinations", "candidateEnumerationComplete",
            "generalTopologyComplete", "missingMatchProvesAbsence")
    parts = pointer.split("/")[1:] if pointer else []
    rows: list[dict[str, Any]] = []
    # Most local constraints have priority. Parent scopes remain explicit; a
    # global incomplete search does not invalidate an individually proven item.
    for depth in range(len(parts), -1, -1):
        path = "/" + "/".join(parts[:depth]) if depth else ""
        value = _select(root, path, sanitize)
        if not isinstance(value, dict):
            continue
        scopes = [(path, value)]
        scopes.extend((_pointer(path, name), child) for name, child in value.items()
                      if isinstance(child, dict) and _safe_key(name, child, sanitize)
                      and (name.lower().endswith("evidence") or name == "recognitionCoverage"))
        for scope, fields in scopes:
            facts = {}
            for key in keys:
                raw = fields.get(key)
                if type(raw) in (bool, int):
                    facts[key] = raw
                elif isinstance(raw, str) and len(raw) <= 100 and sanitize(raw, 100) == raw:
                    facts[key] = raw
            if not facts or any(row["jsonPointer"] == scope for row in rows):
                continue
            candidate = [*rows, {"jsonPointer": scope, "facts": facts}]
            if len(candidate) > 12 or _size(candidate) > 1600:
                return rows, True
            rows = candidate
    return rows, False


def read_tool_result(params: Mapping[str, Any], *, sanitize: Callable[[object, int], str]) -> dict[str, Any]:
    context = _CONTEXT.get()
    if context is None or not context.session_id or not context.turn_id:
        raise PermissionError("Result reads require the owning active runtime turn")
    if set(params) - set(INPUT_SCHEMA["properties"]):
        raise ValueError("Result reader accepts only resultRef, source, jsonPointer, offset, limit and textOffset")
    source = params.get("source", "result")
    if source not in ("result", "owner_model"):
        raise ValueError("source must be result or owner_model")
    ref = params.get("resultRef")
    descriptor_key = "modelInformationRead" if source == "owner_model" else "resultRead"
    step = next((row for row in context.steps if isinstance(ref, str)
                 and row.get(descriptor_key, {}).get("resultRef") == ref
                 and result_reference(context.session_id, context.turn_id, context.project_root, row) == ref), None)
    if step is None or (source == "result" and not eligible_result(str(step.get("tool") or ""), step.get("result"))):
        raise PermissionError("Result reference is unavailable in this runtime turn")
    if source == "owner_model":
        model = step.get("modelInformation")
        if (not isinstance(model, Mapping)
                or model.get("retainedBy") != "agent_tool_result_reader"
                or not isinstance(model.get("authority"), str) or not model.get("authority")
                or not isinstance(model.get("payload"), (dict, list, str))):
            raise PermissionError("Owner-validated model payload is unavailable in this runtime turn")
        root = model["payload"]
    else:
        root = step["result"]
    pointer = params.get("jsonPointer", "")
    value = _select(root, pointer, sanitize)
    offset, limit = params.get("offset", 0), params.get("limit", 6)
    if type(offset) is not int or offset < 0 or type(limit) is not int or not 1 <= limit <= 20:
        raise ValueError("offset must be nonnegative and limit must be between 1 and 20")
    members = list(value.items()) if isinstance(value, dict) else list(enumerate(value)) if isinstance(value, list) else [(None, value)]
    if offset > len(members):
        raise ValueError("offset is outside the retained result")
    field_name = pointer.rsplit("/", 1)[-1].replace("~1", "/").replace("~0", "~")
    scalar_text = isinstance(value, str) and not _identity_key(field_name)
    if "textOffset" in params and (not scalar_text or offset != 0):
        raise ValueError("textOffset requires an exact non-identity text field with offset=0")
    page: dict[str, Any] = {"schema": PAGE_SCHEMA, "ok": True, "authority": "untrusted_tool_output",
                           "resultRef": ref, "source": source, "sourceStep": step["index"], "sourceTool": step["tool"],
                           "jsonPointer": pointer, "offset": offset, "totalItems": len(members),
                           "items": [], "redactedFields": 0, "hasMore": False, "previewTruncated": False}
    constraints, constraints_truncated = _source_constraints(root, pointer, sanitize)
    if constraints or constraints_truncated:
        page["sourceConstraints"] = constraints
        page["sourceConstraintsTruncated"] = constraints_truncated
        page["interpretation"] = (
            "Preserve these source-scoped constraints when interpreting this page. "
            "Candidate details are not confirmed mappings when their resolution is ambiguous or unresolved. "
            "hasMore=false only completes this retained page, not source resolution or search coverage. "
            "If constraints are truncated, inspect the parent scopes before claiming resolution."
        )
    if scalar_text and offset == 0:
        # Sanitize the entire retained string before slicing: a secret must not
        # straddle chunks and escape the normal redactor. Cursors address this
        # deterministic sanitized text, not raw-source or collection offsets.
        # Owner projection is already complete and safety-filtered before it is
        # retained. Re-sanitizing serialized JSON would reinterpret escapes or
        # whitespace; raw-result reads keep the historical sanitizer.
        text = value if source == "owner_model" else sanitize(value, max(1, len(value) * 4 + 100))
        start = params.get("textOffset", 0)
        if type(start) is not int or not 0 <= start <= len(text):
            raise ValueError("textOffset is outside the sanitized text")

        def text_page(end: int) -> dict[str, Any]:
            more = end < len(text)
            candidate = {**page, "textOffset": start, "textTotalChars": len(text),
                         "items": [{"jsonPointer": pointer, "value": text[start:end],
                                    "truncated": more, "redactedFields": 0}],
                         "returnedItems": 1, "hasMore": more, "previewTruncated": more}
            if more:
                candidate["nextRequest"] = {"tool": TOOL_NAME, "arguments": {
                    "resultRef": ref, "source": source, "jsonPointer": pointer, "offset": 0,
                    "limit": limit, "textOffset": end}}
            return candidate

        if len(text) - start <= MAX_PAGE_CHARS:
            complete = text_page(len(text))
            if _size(complete) <= MAX_PAGE_CHARS:
                return complete
        low, high = start, min(len(text), start + MAX_PAGE_CHARS)
        while low < high:
            middle = (low + high + 1) // 2
            if _size(text_page(middle)) <= MAX_PAGE_CHARS:
                low = middle
            else:
                high = middle - 1
        result = text_page(low)
        if _size(result) > MAX_PAGE_CHARS or low == start < len(text):
            raise ValueError("Text page metadata exceeds the page budget; select a narrower field")
        return result
    cursor = offset
    for key, child in members[offset:]:
        if len(page["items"]) >= limit:
            break
        if isinstance(value, dict) and not _safe_key(key, child, sanitize):
            page["redactedFields"] += 1
            cursor += 1
            continue
        child_pointer = pointer if key is None else _pointer(pointer, key)
        # Wrapping a scalar under its original key preserves the exact-identity
        # and sensitive-value rules of the existing structured evidence channel.
        wrapper_key = str(key) if isinstance(value, dict) else pointer.rsplit("/", 1)[-1] if pointer else "value"
        evidence = project_structured_tool_evidence({wrapper_key: child}, sanitize_text=sanitize, max_chars=1500)
        safe = evidence["data"]
        row = {"jsonPointer": child_pointer, "truncated": evidence["truncated"],
               "redactedFields": evidence["redactedFields"]}
        if wrapper_key in safe:
            row["value"] = safe[wrapper_key]
        if evidence["truncated"]:
            wrapper_pointer = _pointer("", wrapper_key)
            row["incompleteFields"] = [
                {**field, "jsonPointer": child_pointer + field["jsonPointer"][len(wrapper_pointer):]}
                for field in evidence.get("incompleteFields", [])
                if field["jsonPointer"] == wrapper_pointer or field["jsonPointer"].startswith(wrapper_pointer + "/")
            ]
            row["incompleteFieldsTruncated"] = evidence.get("incompleteFieldsTruncated", False)
        if isinstance(child, (dict, list)):
            row["type"] = "object" if isinstance(child, dict) else "array"
            row["count"] = len(child)
            # The exact pointer can always be selected again to page its fields.
            row["expandable"] = True
        candidate = {**page, "items": [*page["items"], row], "returnedItems": len(page["items"]) + 1,
                     "nextRequest": {"tool": TOOL_NAME, "arguments": {"resultRef": ref, "source": source, "jsonPointer": pointer, "offset": cursor + 1, "limit": limit}}}
        if _size(candidate) > MAX_PAGE_CHARS:
            break
        page["items"].append(row)
        cursor += 1
    page["hasMore"] = cursor < len(members)
    if page["hasMore"]:
        page["nextRequest"] = {"tool": TOOL_NAME, "arguments": {"resultRef": ref, "source": source, "jsonPointer": pointer, "offset": cursor, "limit": limit}}
    page["returnedItems"] = len(page["items"])
    # hasMore describes this page only, not completeness of nested previews.
    page["previewTruncated"] = any(row["truncated"] for row in page["items"])
    assert _size(page) <= MAX_PAGE_CHARS
    return page
