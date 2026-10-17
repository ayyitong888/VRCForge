"""Bounded keyword routes into the existing, permission-filtered tool tree."""
from __future__ import annotations

import re
from collections.abc import Mapping
from collections import defaultdict

QUERY_MAX_CHARS = 160


def _bounded_query(query):
    original = query.strip()
    if len(original) <= QUERY_MAX_CHARS:
        return original, {}
    words = []
    size = 0
    for word in original.split():
        needed = len(word) + bool(words)
        if size + needed > QUERY_MAX_CHARS:
            break
        words.append(word)
        size += needed
    # Never split a keyword. If even the first keyword is too long, return
    # the normal no-match/root-navigation result with explicit truncation.
    effective = " ".join(words)
    return effective, {
        "queryTruncated": True,
        "queryLimit": QUERY_MAX_CHARS,
        "originalQueryChars": len(original),
        "effectiveQueryChars": len(effective),
    }


_WRITE_INTENT = frozenset({
    "add", "apply", "change", "create", "delete", "edit", "ensure", "fix", "manage",
    "modify", "rename", "set", "update", "write", "\u6539", "\u4fee\u6539", "\u521b\u5efa", "\u5220\u9664", "\u8c03\u6574",
})
_READ_INTENT = frozenset({
    "check", "inspect", "list", "read", "scan", "show", "\u626b\u63cf", "\u53ea\u8bfb",
    "\u67e5\u770b", "\u68c0\u67e5", "\u67e5\u8be2",
})
_OPERATION_PREFIXES = frozenset({
    "apply", "create", "delete", "ensure", "manage", "preview", "read", "rename", "scan",
    "set", "update", "write",
})


def _terms(value):
    # Split alpha and numeric runs so compact requests such as ``speed1``
    # still match the schema's ``speed`` field.
    text = re.sub(r"(?<=[a-z])(?=[A-Z])", " ", str(value or ""))
    return set(re.findall(r"[a-z]+|[0-9]+|[\u4e00-\u9fff]+", text.casefold()))


def _schema_terms(schema):
    # Score executable capability fields and operation enums, never generic
    # schema vocabulary or prose such as "change" in an unrelated description.
    if not isinstance(schema, Mapping):
        return set()
    found = set()
    properties = schema.get("properties")
    if isinstance(properties, Mapping):
        for name, child in properties.items():
            found.update(_terms(name))
            found.update(_schema_terms(child))
    for value in schema.get("enum", []):
        if isinstance(value, str):
            found.update(_terms(value))
    if isinstance(schema.get("const"), str):
        found.update(_terms(schema["const"]))
    found.update(_schema_terms(schema.get("items")))
    for key in ("oneOf", "anyOf", "allOf"):
        for child in schema.get(key, []):
            found.update(_schema_terms(child))
    return found


def _target_terms(runtime_name):
    parts = runtime_name.removeprefix("vrcforge_").removeprefix("unity_").split("_")
    if parts and parts[0] in _OPERATION_PREFIXES:
        parts = parts[1:]
    return set(parts)


def _is_write_intent(tokens):
    return bool(tokens & _WRITE_INTENT)


def search_tool_leaves(query, tools, selections):
    query, truncation = _bounded_query(query)
    tokens = _terms(query)
    records = [t for t in tools if t.block != "core" and "/" in t.block]
    scores = {}
    for tool in records:
        # Match keywords as complete tokens.  Substring matching makes a read
        # query score ``readiness`` and other unrelated words, which can route
        # broad inspection requests to the wrong leaf.
        name_terms = _terms(tool.name + " " + tool.runtime_name)
        text_terms = _terms(tool.block + " " + tool.description)
        name_hits = len(tokens & name_terms)
        text_hits = len(tokens & text_terms)
        schema_hits = len((tokens - _WRITE_INTENT - _READ_INTENT) & _schema_terms(tool.input_schema))
        scores[tool.name] = (name_hits, schema_hits, text_hits)
    best = max((score[0] for score in scores.values()), default=0)
    exact_leaf = query.strip().casefold()
    leaf_matches = [t for t in records if t.block.casefold() == exact_leaf]
    exact_tool_matches = [
        t for t in records
        if t.name.casefold() == exact_leaf or t.runtime_name.casefold() == exact_leaf
    ]
    direct_matches = leaf_matches or exact_tool_matches
    matched = direct_matches or [t for t in records if scores[t.name][0] == best and (best or scores[t.name][2])]
    write_intent = _is_write_intent(tokens)
    numeric_change = any(token.isdigit() for token in tokens)
    if not write_intent and not (tokens & _READ_INTENT):
        # A task-shaped query can omit an imperative (for example, a compact
        # request naming a controller and speed). Prefer a write-capable tool
        # only when the visible schema exposes multiple matching task fields.
        write_capability = any(
            tool.write and scores[tool.name][1] >= 2
            for tool in records
        )
        write_intent = write_capability and ("preview" in tokens or numeric_change)
    if write_intent and not direct_matches:
        content_tokens = tokens - _WRITE_INTENT - _READ_INTENT
        candidates = [
            tool for tool in records
            if tool.write and (
                scores[tool.name][1] > 0
                or bool(content_tokens & _terms(tool.name + " " + tool.runtime_name))
            )
        ]
        if candidates:
            selected = max(
                candidates,
                key=lambda tool: (
                    scores[tool.name][1] * 3 + scores[tool.name][0] * 2,
                    tool.name,
                ),
            )
            selected_block = selected.block
            target = _target_terms(selected.runtime_name)
            pair_names = {selected.name}
            for tool in records:
                if (tool.block == selected_block and tool.runtime_name.startswith("vrcforge_preview_")
                        and not tool.write
                        and "vrcforge_" + tool.runtime_name[len("vrcforge_preview_"):] == selected.runtime_name
                        and dict(tool.input_schema) == dict(selected.input_schema)):
                    pair_names.add(tool.name)
            matched = [
                tool for tool in records
                if tool.block == selected_block
                and (
                    tool.name in pair_names
                    or (
                        not tool.write
                        and not tool.runtime_name.startswith("vrcforge_preview_")
                        and target <= _target_terms(tool.runtime_name)
                        and _target_terms(tool.runtime_name)
                    )
                )
            ]
        else:
            matched = []
    elif tokens & _READ_INTENT:
        matched = [tool for tool in matched if not tool.write]
    grouped = defaultdict(list)
    for tool in matched:
        grouped[tool.block].append(tool)
    ordered = sorted(grouped, key=lambda block: (-max(scores[t.name][2] for t in grouped[block]), block))
    results = []
    for block in ordered[:5]:
        matches = grouped[block]
        # Only the exact preview/write pair is a contract dependency. Other
        # same-leaf operations remain matches, never inferred prerequisites.
        local = {t.runtime_name: t for t in records if t.block == block}
        required_names = set()
        matched_names = {t.name for t in matches}
        for preview in local.values():
            if tokens & _READ_INTENT:
                continue
            prefix = "vrcforge_preview_"
            if not preview.runtime_name.startswith(prefix) or preview.write:
                continue
            write = local.get("vrcforge_" + preview.runtime_name[len(prefix):])
            if (write is not None and write.write
                    and dict(preview.input_schema) == dict(write.input_schema)
                    and matched_names & {preview.name, write.name}):
                required_names.update((preview.name, write.name))
        required = sorted(required_names)
        bundle = sorted(matched_names | required_names)
        loaded = selections.get(block, [])
        missing = [n for n in bundle if loaded is not None and n not in loaded]
        results.append({
            "leafPath": block,
            "toolNames": sorted(t.name for t in matches),
            "requiredTools": required,
            "companionScope": "same-leaf exact preview/write pair with identical input contract",
            "sameLeafToolNames": sorted(t.name for t in records if t.block == block),
            "alreadyLoadedTools": [n for n in bundle if n not in missing],
            "reuse": not missing,
            "loadCall": {"block": block, "tools": missing} if missing else None,
        })
    return {
        "ok": True, "schema": "vrcforge.internal_tool_search.v1", "query": query,
        **truncation,
        "status": "found" if results else "not_found", "matches": results,
        "hasMore": len(ordered) > 5,
        "fallbackCall": {"tools": []},
        "policy": "Copy a loadCall once for the needed leaf bundle; reuse loaded definitions. Loading never authorizes execution or writes.",
    }
