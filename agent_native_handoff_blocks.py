"""Deterministic native handoff projection and session-owned source pointers."""
from __future__ import annotations

import copy
import hashlib
import json
import re
from collections.abc import Callable, Mapping, Sequence
from typing import Any
from agent_runtime_input import RuntimeInputSource, classify_history


_MATERIAL = re.compile(
    r"(?m)^(?:Attachments|附件|添付ファイル):\n- [^\n]+(?:\n- [^\n]+)*"
    r"|^Runtime attachment context \(data, not instructions or authorization\):\n[\s\S]*"
    r"|(?i:data:image/[^\s;,]+;base64,[A-Za-z0-9+/=_-]+)"
)
_PASTE = re.compile(r"(?ms)^(`{3,}|~{3,})[^\n]*\n.*?^\1[ \t]*(?:\n|$)|^>[^\n]*(?:\n>[^\n]*)*"
                    r"|^(?:(?:\[\d{4}-\d\d-\d\d|\d{4}-\d\d-\d\d|\[?(?:DEBUG|INFO|WARN|ERROR|TRACE)\b)"
                    r"[^\n]*\n?){6,}")
_LONG_PASTE = 1600
_ATTACHMENT_FIELDS = {"id", "name", "type", "size", "payloadKind", "payloadHash",
                      "vaultPayloadHash", "vaultKind", "text", "truncated", "availability", "messageId"}


def _pointer(message_id: str, subject: str) -> str:
    return f"[{subject}. Retrieve with vrcforge_read_tool_result(messageId={json.dumps(message_id)})]"


def _identity(kind: str, *parts: str) -> str:
    body = json.dumps(parts, ensure_ascii=False, separators=(",", ":"))
    return f"{kind}:{hashlib.sha256(body.encode('utf-8')).hexdigest()[:24]}"


def handoff_attachments(items: list[dict[str, Any]]) -> list[dict[str, Any]]:
    return [{key: value for key, value in item.items()
             if key in _ATTACHMENT_FIELDS and isinstance(value, (str, int, bool))}
            for item in items if isinstance(item, dict)]


def handoff_references(items: list[dict[str, Any]]) -> list[dict[str, str]]:
    return [{"id": item["id"], "text": item["text"]} for item in items
            if isinstance(item, dict) and all(isinstance(item.get(key), str) and item[key] for key in ("id", "text"))]


def user_handoff_references(item: dict[str, Any], message_id: str) -> list[dict[str, str]]:
    references = list(item.get("handoffReferences", []))
    for index, material in enumerate(item.get("handoffAttachments", [])):
        attachment_id = str(material.get("id") or index)
        if material.get("name") == "Selected text" and not attachment_id.startswith("att-") and material.get("text"):
            references.append({"id": _identity("quote", message_id, attachment_id), "text": material["text"]})
    return references


def _graph_references(references: list[dict[str, str]]) -> list[dict[str, str]]:
    return [{**reference, "text": _pointer(reference["id"], "Quoted material")}
            if reference["id"].startswith("quote:") and len(reference["text"]) > _LONG_PASTE
            else reference for reference in references]


def handoff_previous_reply(archive: dict[str, Any], turn_id: str, index: int, text: str,
                          measure: Callable[[str], int]) -> str:
    if measure(text) <= 512:
        return text
    message_id = _identity("assistant", turn_id, str(index), text)
    archive[message_id] = {"id": message_id, "source": "assistant", "text": text}
    return _pointer(message_id, "Earlier assistant reply")


def _user_text(text: str, message_id: str) -> str:
    text = _MATERIAL.sub(lambda match: _pointer(
        message_id,
        "Attached material" if match[0].startswith("data:image/")
        else "Historical attachment listing; payload availability is unverified",
    ), text)
    return _PASTE.sub(lambda match: (
        _pointer(message_id, "Quoted or pasted material")
        if match[0].startswith(">") or len(match[0]) >= _LONG_PASTE else match[0]
    ), text)


def _transaction_anchor(messages: list[dict[str, Any]]) -> str:
    return messages[0]["tool_calls"][0]["id"]


def _transaction_text(messages: list[dict[str, Any]]) -> str:
    # Provider replay fields may contain private reasoning and are required
    # only by the durable wire transaction.
    tool_data = {"role": "assistant", "tool_calls": messages[0]["tool_calls"]}
    return json.dumps([tool_data, *messages[1:]], ensure_ascii=False, separators=(",", ":"))


def _archive_attachments(archive: dict[str, Any], message_id: str,
                         attachments: list[dict[str, Any]]) -> list[dict[str, Any]]:
    result = []
    for index, attachment in enumerate(attachments):
        descriptor = dict(attachment)
        content = descriptor.pop("text", None)
        if content:
            attachment_id = _identity("attachment", message_id, str(descriptor.get("id") or index))
            archive.setdefault(attachment_id, {"id": attachment_id, "source": "attachment", "text": content})
            descriptor["messageId"] = attachment_id
            descriptor["availability"] = "retained"
        elif descriptor.get("vaultPayloadHash") or (descriptor.get("payloadKind") == "vault_file" and descriptor.get("payloadHash")):
            descriptor["availability"] = "vault"
        else:
            descriptor["availability"] = "unavailable"
        result.append(descriptor)
    return result


def project_handoff_blocks(
    snapshot: dict[str, Any], *,
    humans: list[int], archive_indices: dict[int, str], pending_from: int, transaction_end: int,
    task_indices: set[int], last_reply: tuple[int, str] | None, policy_indices: set[int],
    continuation_index: int | None, character_budget: int, measure: Callable[[str], int] = len,
) -> dict[str, Any]:
    messages = snapshot["messages"]
    active = snapshot["activeTurnStart"]
    archive = copy.deepcopy(snapshot.get("handoffArchive", {}))
    source_indices: dict[int, int] = {}
    projected: list[dict[str, Any]] = []
    user_indices: list[int] = []
    budget = max(0, character_budget)

    def retain(index: int, item: dict[str, Any]) -> None:
        source_indices[index] = len(projected)
        projected.append(copy.deepcopy(item))

    for index in sorted(policy_indices):
        retain(index, messages[index])
    for index in humans:
        item = messages[index]
        message_id = archive_indices[index]
        references = user_handoff_references(item, message_id)
        for reference in references:
            archive.setdefault(reference["id"], {**reference, "source": "assistant"})
        attachments = _archive_attachments(archive, message_id, item.get("handoffAttachments", []))
        archive.setdefault(message_id, {
            "id": message_id, "source": "user", "text": item.get("handoffSourceText", item["content"]),
            **({"attachments": attachments} if attachments else {}),
            **({"references": references} if references else {}),
        })

    archived_users = [message_id for message_id, item in archive.items() if item["source"] == "user"]
    current_order = list(dict.fromkeys(archive_indices.values()))
    user_order = current_order if set(archived_users) <= set(current_order) else archived_users
    originals = []
    for message_id in user_order:
        item = archive[message_id]
        text = _user_text(item["text"], message_id)
        if item.get("attachments"):
            text += ("\n\n" if text else "") + _pointer(message_id, "Attached material")
        originals.append((message_id, text))
    headers = {message_id: f"[User message {message_id}]\n" for message_id, _ in originals}
    def user_cost(message_id: str, text: str) -> int:
        return measure(json.dumps({"role": "user", "content": headers[message_id] + text}, ensure_ascii=False))

    available = budget - sum(user_cost(message_id, _pointer(message_id, "User message"))
                             for message_id, _ in originals)
    available -= sum(measure(json.dumps(messages[index], ensure_ascii=False)) for index in policy_indices | task_indices)
    inline: set[str] = set()
    if originals:
        first_id, first_text = originals[0]
        inline.add(first_id)
        available -= user_cost(first_id, first_text) - user_cost(first_id, _pointer(first_id, "User message"))
    if last_reply is not None:
        available -= measure(json.dumps({"role": "assistant", "content": last_reply[1]}, ensure_ascii=False))
    for message_id, content in reversed(originals[1:]):
        extra = user_cost(message_id, content) - user_cost(message_id, _pointer(message_id, "User message"))
        if extra <= max(available, 0):
            inline.add(message_id)
            available -= extra
    raw_humans = {message_id: index for index, message_id in archive_indices.items()}
    for message_id, content in originals:
        raw_index = raw_humans.get(message_id)
        record = {
            "role": "user",
            "content": content if message_id in inline else _pointer(message_id, "User message"),
            "messageId": message_id,
            "handoffUser": True,
        }
        user_indices.append(len(projected))
        if raw_index is None:
            projected.append(record)
        else:
            retain(raw_index, record)
    for index in sorted(task_indices):
        retain(index, messages[index])
    summary_index = len(projected)

    reply_index = None
    if last_reply is not None:
        reply_index, text = last_reply
        message_id = _identity("assistant", snapshot["turnId"], str(reply_index), text)
        existing_reply_id = snapshot.get("handoffLastReplyId")
        if existing_reply_id and snapshot.get("handoffLastReplyIndex") == reply_index:
            message_id = existing_reply_id
            text = archive[message_id]["text"]
        archive.setdefault(message_id, {"id": message_id, "source": "assistant", "text": text})
        # If this reply accompanies a pending call, retain its public text here
        # and let the group remain independently addressable at its wire position.
        projected.append({"role": "assistant", "content": text})
        reply_index = len(projected) - 1
    else:
        message_id = None
    if continuation_index is not None:
        retain(continuation_index, messages[continuation_index])

    pending = messages[pending_from:transaction_end]
    if pending:
        anchor = _transaction_anchor(pending)
        if measure(json.dumps(pending, ensure_ascii=False, separators=(",", ":"))) > available:
            transaction_id = _identity("transaction", anchor)
            archive[transaction_id] = {"id": transaction_id, "source": "tool", "text": _transaction_text(pending)}
            projected.append({"role": "user", "content": _pointer(transaction_id, "Unfinished tool transaction"),
                              "handoffOmittedCallId": anchor})
        for index in range(pending_from, transaction_end):
            retain(index, messages[index])

    return {
        "messages": projected,
        "messageArchive": archive,
        "humanUserMessageIndices": user_indices,
        "activeTurnStart": source_indices.get(active),
        "summaryIndex": summary_index,
        "sourceIndices": source_indices,
        "lastReplyId": message_id,
        "lastReplyIndex": reply_index,
        "taskInputs": [
            {"id": row["messageId"], "source": "user", "text": row["content"],
             **({"sourceText": _user_text(archive[row["messageId"]]["text"], row["messageId"])}
                if row["messageId"] in inline and archive[row["messageId"]].get("attachments") else {}),
             **({"references": _graph_references(archive[row["messageId"]]["references"])}
                if archive[row["messageId"]].get("references") else {})}
            for row in (projected[index] for index in user_indices)
        ],
    }


def handoff_block_messages(
    blocks: dict[str, Any], *, summary: str = "", current_context: list[dict[str, Any]] | None = None,
    include_pending: bool = True,
) -> list[dict[str, Any]]:
    messages = blocks["messages"]
    at = blocks["summaryIndex"]
    if not include_pending:
        for index, row in enumerate(messages):
            if "handoffOmittedCallId" in row:
                messages = messages[:index + 1]
                break
    return [*(current_context or []), *messages[:at], *([{"role": "assistant", "content": summary}] if summary else []),
            *messages[at:]]


def handoff_provider_messages(messages: list[dict[str, Any]]) -> list[dict[str, Any]]:
    omitted_anchors = {row["handoffOmittedCallId"] for row in messages if "handoffOmittedCallId" in row}
    if not omitted_anchors:
        return messages
    omitted_ids = set()
    projected = []
    for row in messages:
        calls = row.get("tool_calls") or []
        if calls and calls[0]["id"] in omitted_anchors:
            omitted_ids.update(call["id"] for call in calls)
        elif row.get("tool_call_id") not in omitted_ids:
            projected.append(row)
    return projected


def refresh_handoff_transactions(snapshot: dict[str, Any]) -> None:
    """Keep pointer-only call groups current as exact tool receipts settle."""
    archive = snapshot.get("handoffArchive", {})
    messages = snapshot["messages"]
    for index, row in enumerate(messages):
        anchor = row.get("handoffOmittedCallId")
        if not anchor:
            continue
        transaction_id = _identity("transaction", anchor)
        if transaction_id not in archive or index + 1 == len(messages):
            continue
        group = [messages[index + 1]]
        for result in messages[index + 2:]:
            if result["role"] != "tool":
                break
            group.append(result)
        archive[transaction_id]["text"] = _transaction_text(group)


def legacy_handoff_archive(history: Sequence[Mapping[str, Any]], message_id: str) -> dict[str, Any] | None:
    """Resolve legacy pointers against the owned retained or original history."""
    for index, row in enumerate(history):
        if isinstance(archive := row.get("_handoffArchive"), dict) and message_id in archive:
            return copy.deepcopy(archive[message_id])
        if not classify_history(row).is_human:
            continue
        source_id = _legacy_message_id(row, index)
        text = row.get("sourceText", row.get("text"))
        if isinstance(text, str):
            raw_attachments = handoff_attachments(row.get("handoffAttachments") or [])
            records: dict[str, Any] = {}
            attachments = _archive_attachments(records, source_id, raw_attachments)
            if source_id == message_id:
                return {"id": message_id, "source": "user", "text": text,
                        **({"attachments": attachments} if attachments else {})}
            if message_id in records:
                return records[message_id]
            for reference in user_handoff_references({"handoffAttachments": raw_attachments}, source_id):
                if reference["id"] == message_id:
                    return {**reference, "source": "assistant"}
    return None


def _legacy_message_id(row: Mapping[str, Any], ordinal: int) -> str:
    return str(row.get("messageId") or row.get("id") or f"history:{ordinal}")


def project_legacy_handoff_blocks(
    history: list[dict[str, Any]], *, current: dict[str, Any] | None = None,
    current_source: RuntimeInputSource = RuntimeInputSource.USER,
    is_handoff: Callable[[dict[str, Any]], bool], character_budget: int, measure: Callable[[str], int],
    public_reply: str | None = None,
) -> dict[str, Any]:
    messages = []
    human_ids = {}
    archive = {}
    task_indices = set()
    latest_reply = None
    task_rows = []
    for ordinal, row in enumerate([*history, *([current] if current else [])]):
        if is_handoff(row):
            archive.update(copy.deepcopy(row.get("_handoffArchive", {})))
            continue
        role, text = row.get("role"), row.get("sourceText", row.get("text"))
        if not isinstance(text, str) or (not text.strip() and not row.get("handoffAttachments")):
            continue
        index = len(messages)
        source = current_source if row is current else classify_history(row)
        if role in {"user", "runtime"} and source is not RuntimeInputSource.RUNTIME:
            message_id = _legacy_message_id(row, ordinal)
            messages.append({"role": "user", "content": text, "messageId": message_id,
                             "handoffAttachments": handoff_attachments(row.get("handoffAttachments") or []),
                             "handoffReferences": handoff_references(row.get("references") or [])})
            if source.is_human:
                human_ids[index] = message_id
            else:
                if not task_indices or row is current:
                    task_indices.add(index)
                task_rows.append({"id": message_id, "source": source.value, "text": text})
        elif role == "agent" and not row.get("subAgentTaskId"):
            messages.append({"role": "assistant", "content": text})
            latest_reply = (index, text)
        elif row is current:
            messages.append({"role": "user", "content": text})
            task_indices.add(index)
    active = len(messages) - 1 if current else -1
    if public_reply is not None and public_reply.strip():
        latest_reply = (len(messages), public_reply)
        messages.append({"role": "assistant", "content": public_reply})
    blocks = project_handoff_blocks(
        {"messages": messages, "activeTurnStart": active, "turnId": current["messageId"] if current else "legacy",
         "handoffArchive": archive},
        humans=list(human_ids), archive_indices=human_ids, pending_from=len(messages), transaction_end=len(messages),
        task_indices=task_indices, last_reply=latest_reply, policy_indices=set(), continuation_index=None,
        character_budget=character_budget, measure=measure,
    )
    user_rows = [{"role": "user", "messageId": row["messageId"], "text": row["content"], "_handoffUser": True}
                 for row in (blocks["messages"][index] for index in blocks["humanUserMessageIndices"])]
    blocks["legacyRows"] = [*user_rows, *[
        {"role": "runtime", "text": row["text"], "authorRole": row["source"], "messageId": row["id"]}
        for row in task_rows
    ]]
    if current and current_source is RuntimeInputSource.RUNTIME:
        blocks["legacyRows"].append({"role": "runtime", "text": current["text"]})
    blocks["taskInputs"] = [
        *(row for row in task_rows if row["source"] == RuntimeInputSource.PARENT_AGENT), *blocks["taskInputs"],
        *(row for row in task_rows if row["source"] != RuntimeInputSource.PARENT_AGENT),
    ]
    return blocks


def legacy_transaction_pointer(archive: dict[str, Any], turn_id: str, group: str) -> str:
    message_id = _identity("transaction", turn_id, group)
    archive[message_id] = {"id": message_id, "source": "tool", "text": group}
    return _pointer(message_id, "Unfinished tool transaction")
