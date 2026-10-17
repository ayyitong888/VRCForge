"""Bounded keyword routes into the existing, permission-filtered tool tree."""
from __future__ import annotations

import re
from collections import defaultdict


def search_tool_leaves(query, tools, selections):
    tokens = set(re.findall(r"[a-z0-9]+|[\u4e00-\u9fff]+", query.casefold()))
    records = [t for t in tools if t.block != "core" and "/" in t.block]
    scores = {}
    for tool in records:
        names = (tool.name + " " + tool.runtime_name).casefold()
        name_hits = sum(token in names for token in tokens)
        text_hits = sum(token in (tool.block + " " + tool.description).casefold() for token in tokens)
        scores[tool.name] = (name_hits, text_hits)
    best = max((score[0] for score in scores.values()), default=0)
    exact_leaf = query.strip().casefold()
    leaf_matches = [t for t in records if t.block.casefold() == exact_leaf]
    matched = leaf_matches or [t for t in records if scores[t.name][0] == best and (best or scores[t.name][1])]
    grouped = defaultdict(list)
    for tool in matched:
        grouped[tool.block].append(tool)
    ordered = sorted(grouped, key=lambda block: (-max(scores[t.name][1] for t in grouped[block]), block))
    results = []
    for block in ordered[:5]:
        matches = grouped[block]
        # Only the exact preview/write pair is a contract dependency. Other
        # same-leaf operations remain matches, never inferred prerequisites.
        local = {t.runtime_name: t for t in records if t.block == block}
        required_names = set()
        matched_names = {t.name for t in matches}
        for preview in local.values():
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
        "status": "found" if results else "not_found", "matches": results,
        "hasMore": len(ordered) > 5,
        "fallbackCall": {"tools": []},
        "policy": "Copy a loadCall once for the needed leaf bundle; reuse loaded definitions. Loading never authorizes execution or writes.",
    }
