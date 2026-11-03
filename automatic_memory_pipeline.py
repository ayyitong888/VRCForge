"""Derived conversation summaries, separate from user-managed accepted facts.

One host-owned review run stages both model phases before replacing the scoped
snapshot. Failed/cancelled model work never changes the last usable snapshot.
"""
from __future__ import annotations

import copy
import json
import threading
from pathlib import Path
from typing import Any, Callable, Iterable, Mapping, Sequence

from memory_consolidation import MAX_PROVIDER_BATCH, MemoryConsolidationError, MemoryReviewStore, RevisionConflictError
from memory_consolidation_sources import MemoryScope, SourceProjection, redact_memory_text

REQUEST_SCHEMA = "vrcforge.automatic_memory_request.v1"
STORE_SCHEMA = "vrcforge.automatic_memory.v1"
SUMMARY_LIMIT = 6_000


def _identity(source: SourceProjection) -> tuple[str, str, str]:
    return source.source_type, source.source_revision, source.source_digest


def _summary(value: Any) -> str:
    if not isinstance(value, str) or len(value) > SUMMARY_LIMIT:
        raise MemoryConsolidationError("Automatic memory summary exceeds its contract.")
    text, _report = redact_memory_text(value, limit=SUMMARY_LIMIT)
    return text.strip()


def _bounded_evidence(text: str, limit: int) -> str:
    """Keep recent user evidence before assistant reports, in original order."""
    if len(text) <= limit:
        return text
    try:
        rows = json.loads(text)
    except (TypeError, ValueError):
        return text[-limit:]
    if not isinstance(rows, list):
        return text[-limit:]
    selected: dict[int, Any] = {}
    # Question and answer are one source record and are selected together.
    def evidence_priority(index: int) -> tuple[int, int]:
        row = rows[index]
        role = row.get("role") if isinstance(row, dict) else ""
        if role in {"user", "user_answer"}:
            priority = 0
        elif role == "assistant":
            priority = 1
        elif role == "assistant_progress":
            priority = 2
        else:
            priority = 3
        return priority, -index
    ranked = sorted(range(len(rows)), key=evidence_priority)
    for index in ranked:
        candidate = {**selected, index: rows[index]}
        rendered = json.dumps([candidate[i] for i in sorted(candidate)], ensure_ascii=False)
        if len(rendered) <= limit:
            selected = candidate
    if not selected:
        raise MemoryConsolidationError("Automatic memory evidence does not fit the input budget.")
    return json.dumps([selected[i] for i in sorted(selected)], ensure_ascii=False)


class AutomaticMemoryPipeline:
    def __init__(self, path: Path) -> None:
        self.path = Path(path)
        self._lock = threading.RLock()

    def _load(self) -> dict[str, Any]:
        if not self.path.exists():
            return {"schema": STORE_SCHEMA, "revision": 0, "scopes": {}}
        payload = json.loads(self.path.read_text(encoding="utf-8"))
        if not isinstance(payload, dict) or payload.get("schema") != STORE_SCHEMA or not isinstance(payload.get("scopes"), dict):
            raise MemoryConsolidationError("Automatic memory snapshot is invalid.")
        return payload

    def read(self, scope: MemoryScope) -> dict[str, Any]:
        """No creation or mutation; callers decide whether memory is enabled."""
        with self._lock:
            state = self._load()
            saved = state["scopes"].get(scope.scope_key, {})
            return {"revision": state["revision"], "scopeKey": scope.scope_key,
                    "summary": saved.get("summary", ""),
                    "sources": copy.deepcopy(list(saved.get("sources", {}).values()))}

    def prepare(
        self, scope: MemoryScope, sources: Sequence[SourceProjection],
        complete_source_types: Iterable[str], input_char_cap: int = 12_000,
    ) -> dict[str, Any]:
        with self._lock:
            state = self._load()
        eligible_type = "project_chat" if scope.kind == "project" else "user_chat"
        current = {source.source_id: source for source in sources
                   if source.source_type == eligible_type and source.scope.scope_key == scope.scope_key}
        old = copy.deepcopy(state["scopes"].get(scope.scope_key, {"sources": {}, "summary": ""}))
        retained = copy.deepcopy(old["sources"])
        if eligible_type in set(complete_source_types):
            retained = {key: value for key, value in retained.items() if key in current}
        changed = []
        for key, source in current.items():
            prior = old["sources"].get(key, {})
            if (prior.get("sourceType"), prior.get("sourceRevision"), prior.get("sourceDigest")) != _identity(source):
                changed.append(source)
        changed.sort(key=lambda source: (source.observed_at, source.source_id), reverse=True)
        changed = changed[:MAX_PROVIDER_BATCH]
        needs_work = bool(changed or retained != old["sources"])
        return {"changed": needs_work, "baseRevision": state["revision"], "scopeKey": scope.scope_key,
                "scopeKind": scope.kind, "inputCharCap": input_char_cap,
                "current": current, "retained": retained, "changedSources": changed,
                "complete": eligible_type in set(complete_source_types), "sourceType": eligible_type,
                "providerCallCount": (len(changed) + int(bool(changed or any(row.get("summary") for row in retained.values())))) if needs_work else 0,
                "maxInputBytes": input_char_cap * 4}

    def generate(self, plan: Mapping[str, Any], invoke: Callable, guard: Callable) -> dict[str, Any]:
        staged = {"plan": plan, "changed": bool(plan["changed"]),
                  "sources": copy.deepcopy(plan["retained"]), "summary": ""}
        if not plan["changed"]:
            return staged
        cap = int(plan["inputCharCap"])

        def call(phase: str, data: Mapping[str, Any]) -> Mapping[str, Any]:
            guard_result = guard()
            if guard_result is False:
                raise MemoryConsolidationError("Automatic memory work was revoked.")
            payload = {"schema": REQUEST_SCHEMA, "phase": phase, "tools": [], "input": dict(data)}
            if len(json.dumps(payload, ensure_ascii=False)) > cap:
                raise MemoryConsolidationError("Automatic memory phase exceeds its input budget.")
            result = invoke(payload)
            if not isinstance(result, Mapping):
                raise MemoryConsolidationError("Automatic memory provider result is invalid.")
            return result

        for source in plan["changedSources"]:
            evidence = _bounded_evidence(source.text, max(1, cap - 1000))
            result = call("extract", {"sourceId": source.source_id, "evidence": evidence})
            summary = _summary(result.get("summary"))
            staged["sources"][source.source_id] = {**source.reference(), "summary": summary,
                "observedAt": source.observed_at,
                **({"chatId": source.source_chat_id} if source.source_chat_id else {})}
        # Empty extractions retain their revision watermark but contribute no claim.
        inputs = [{"sourceId": key, "summary": row["summary"], "observedAt": row.get("observedAt", "")}
                  for key, row in sorted(staged["sources"].items(), key=lambda pair: (pair[1].get("observedAt", ""), pair[0])) if row["summary"]]
        if inputs:
            # The complete per-chat summaries stay readable in the store. Mark
            # bounded excerpts explicitly rather than treating omitted text as
            # evidence that an older task or constraint no longer exists.
            while len(json.dumps({"schema": REQUEST_SCHEMA, "phase": "consolidate", "tools": [],
                                  "input": {"scope": plan["scopeKind"], "sources": inputs}}, ensure_ascii=False)) > cap:
                longest = max(inputs, key=lambda row: len(row["summary"]))
                text = longest["summary"]
                if len(text) <= 256:
                    raise MemoryConsolidationError("Automatic memory source index exceeds its input budget.")
                keep = max(64, len(text) // 4)
                longest["summary"] = text[:keep] + "\n[history excerpt omitted]\n" + text[-keep:]
                longest["excerpted"] = True
            result = call("consolidate", {"scope": plan["scopeKind"], "sources": inputs})
            references = result.get("sourceIds")
            allowed = {row["sourceId"] for row in inputs}
            if not isinstance(references, list) or any(not isinstance(key, str) or key not in allowed for key in references):
                raise MemoryConsolidationError("Automatic memory summary has unknown sources.")
            staged["summary"] = _summary(result.get("summary"))
            if staged["summary"] and not references:
                raise MemoryConsolidationError("Automatic memory summary needs source attribution.")
            staged["sourceIds"] = sorted(set(references))
        else:
            staged["sourceIds"] = []
        return staged

    def commit(self, staged: Mapping[str, Any], current_sources: Sequence[SourceProjection],
               complete_source_types: Iterable[str]) -> dict[str, Any]:
        plan = staged["plan"]
        if not staged["changed"]:
            return {"changed": False, "revision": plan["baseRevision"]}
        current = {source.source_id: source for source in current_sources
                   if source.source_type == plan["sourceType"] and source.scope.scope_key == plan["scopeKey"]}
        if {key: _identity(value) for key, value in current.items()} != {
            key: _identity(value) for key, value in plan["current"].items()
        } or (plan["sourceType"] in set(complete_source_types)) != plan["complete"]:
            raise RevisionConflictError("Automatic memory evidence changed during extraction.")
        with self._lock:
            state = self._load()
            if state["revision"] != plan["baseRevision"]:
                raise RevisionConflictError("Automatic memory changed during extraction.")
            state["scopes"][plan["scopeKey"]] = {
                "summary": staged["summary"], "sourceIds": staged["sourceIds"],
                "sources": staged["sources"],
            }
            state["revision"] += 1
            MemoryReviewStore._atomic_write(self.path, state)
        return {"changed": True, "revision": state["revision"], "summary": staged["summary"],
                "sourceCount": len(staged["sources"])}
