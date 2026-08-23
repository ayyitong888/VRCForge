"""Validate the exact 78-command live Unity MCP acceptance catalog.

This helper validates acceptance inputs only. It never calls Unity, the App,
or a provider, and it never treats a missing dependency as a successful tool
result.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import re
import sys
from pathlib import Path
from typing import Any, Iterable, Mapping


ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from unity_mcp_tool_contract import EXPECTED_TOOL_NAMES


SCHEMA = "vrcforge.unity_mcp_78_success_matrix.part.v1"
DEFAULT_PARTS = tuple(
    sorted((ROOT / "tests" / "fixtures").glob("unity_mcp_64_success_matrix.part-*.json"))
)
MODE_ALIASES = {
    "read": "read",
    "read_only": "read",
    "direct_read": "read",
    "preview": "preview",
    "approved_write": "approved_write",
    "actual_write": "approved_write",
    "approved_apply": "approved_write",
    "approved_apply_after_preview": "approved_write",
    "approved_async_apply": "approved_write",
    "safety": "safety",
    "safety_control": "safety",
}
PLACEHOLDER = re.compile(r"\$\{([A-Z][A-Z0-9_]*)\}")


def _nonempty_strings(
    value: Any,
    field: str,
    tool: str,
    *,
    empty_value: str = "",
) -> list[str]:
    if isinstance(value, str):
        items = [value]
    elif isinstance(value, list):
        items = value
    else:
        raise ValueError(f"{tool}: {field} must be a string or string array")
    if not items and empty_value:
        return [empty_value]
    if not items or any(not isinstance(item, str) or not item.strip() for item in items):
        raise ValueError(f"{tool}: {field} must contain non-empty strings")
    return [item.strip() for item in items]


def load_catalog(paths: Iterable[Path] = DEFAULT_PARTS) -> list[dict[str, Any]]:
    cases: list[dict[str, Any]] = []
    seen_parts: set[str] = set()
    seen_tools: set[str] = set()
    for path in paths:
        payload = json.loads(path.read_text(encoding="utf-8"))
        if payload.get("schema") != SCHEMA:
            raise ValueError(f"{path}: invalid schema")
        part = str(payload.get("part") or "").strip()
        if not part or part in seen_parts:
            raise ValueError(f"{path}: duplicate or missing part id")
        seen_parts.add(part)
        raw_cases = payload.get("cases")
        if not isinstance(raw_cases, list) or not raw_cases:
            raise ValueError(f"{path}: cases must be a non-empty array")
        for raw in raw_cases:
            if not isinstance(raw, dict):
                raise ValueError(f"{path}: each case must be an object")
            tool = str(raw.get("tool") or "").strip()
            if tool not in EXPECTED_TOOL_NAMES:
                raise ValueError(f"{path}: unexpected tool {tool!r}")
            if tool in seen_tools:
                raise ValueError(f"{path}: duplicate tool {tool}")
            seen_tools.add(tool)
            raw_mode = str(raw.get("mode") or "").strip()
            mode = MODE_ALIASES.get(raw_mode)
            if not mode:
                raise ValueError(f"{tool}: unsupported mode {raw_mode!r}")
            arguments = raw.get("arguments", raw.get("minimalArguments"))
            if not isinstance(arguments, dict):
                raise ValueError(f"{tool}: arguments must be an object")
            required = _nonempty_strings(raw.get("requiredFixtures"), "requiredFixtures", tool)
            success = _nonempty_strings(raw.get("successFields"), "successFields", tool)
            cleanup = _nonempty_strings(raw.get("cleanup"), "cleanup", tool)
            runtime_injected = raw.get("runtimeInjectedArguments", [])
            if not isinstance(runtime_injected, list) or any(
                not isinstance(item, str) or not item.strip() for item in runtime_injected
            ):
                raise ValueError(f"{tool}: runtimeInjectedArguments must contain non-empty strings")
            cases.append(
                {
                    "id": str(raw.get("id") or tool.removeprefix("vrc_")),
                    "tool": tool,
                    "mode": mode,
                    "arguments": arguments,
                    "requiredFixtures": required,
                    "successFields": success,
                    "cleanup": cleanup,
                    "runtimeInjectedArguments": [item.strip() for item in runtime_injected],
                    "part": part,
                }
            )
    missing = sorted(EXPECTED_TOOL_NAMES - seen_tools)
    extra = sorted(seen_tools - EXPECTED_TOOL_NAMES)
    if missing or extra or len(cases) != 78:
        raise ValueError(f"catalog must cover exactly 78 tools; missing={missing}, extra={extra}")
    return sorted(cases, key=lambda item: item["tool"])


def required_placeholders(value: Any) -> set[str]:
    found: set[str] = set()
    if isinstance(value, dict):
        for key, item in value.items():
            found.update(PLACEHOLDER.findall(str(key)))
            found.update(required_placeholders(item))
    elif isinstance(value, list):
        for item in value:
            found.update(required_placeholders(item))
    elif isinstance(value, str):
        found.update(PLACEHOLDER.findall(value))
    return found


def resolve_placeholders(value: Any, context: Mapping[str, Any]) -> Any:
    if isinstance(value, dict):
        resolved: dict[str, Any] = {}
        for key, item in value.items():
            merge_placeholder = PLACEHOLDER.fullmatch(str(key))
            if merge_placeholder:
                merge_name = merge_placeholder.group(1)
                merge_value = context.get(merge_name)
                if isinstance(merge_value, Mapping):
                    for merge_key, merge_item in merge_value.items():
                        if not isinstance(merge_key, str) or not merge_key or merge_key in resolved:
                            raise ValueError(f"invalid or duplicate merged argument key: {merge_key!r}")
                        resolved[merge_key] = resolve_placeholders(merge_item, context)
                    continue
            resolved_key = resolve_placeholders(str(key), context)
            if not isinstance(resolved_key, str) or not resolved_key:
                raise ValueError("resolved argument keys must be non-empty strings")
            if resolved_key in resolved:
                raise ValueError(f"duplicate resolved argument key: {resolved_key}")
            resolved[resolved_key] = resolve_placeholders(item, context)
        return resolved
    if isinstance(value, list):
        return [resolve_placeholders(item, context) for item in value]
    if not isinstance(value, str):
        return value
    exact = PLACEHOLDER.fullmatch(value)
    if exact:
        name = exact.group(1)
        if name not in context:
            raise ValueError(f"missing placeholder: {name}")
        return context[name]

    def replace(match: re.Match[str]) -> str:
        name = match.group(1)
        if name not in context:
            raise ValueError(f"missing placeholder: {name}")
        return str(context[name])

    return PLACEHOLDER.sub(replace, value)


def catalog_digest(cases: list[dict[str, Any]]) -> str:
    canonical = json.dumps(cases, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


def validate_tool_schemas(
    cases: list[dict[str, Any]],
    tools: Iterable[Mapping[str, Any]],
    *,
    context: Mapping[str, Any] | None = None,
) -> dict[str, Any]:
    by_name: dict[str, Mapping[str, Any]] = {}
    for tool in tools:
        name = str(tool.get("name") or "")
        if not name or name in by_name:
            raise ValueError(f"tools/list contains a duplicate or missing name: {name!r}")
        by_name[name] = tool
    if set(by_name) != EXPECTED_TOOL_NAMES:
        raise ValueError("tools/list does not match the exact 78-command contract")

    for case in cases:
        name = case["tool"]
        schema = by_name[name].get("inputSchema")
        if not isinstance(schema, Mapping) or schema.get("type") != "object":
            raise ValueError(f"{name}: inputSchema must be an object schema")
        if schema.get("additionalProperties") is not False:
            raise ValueError(f"{name}: inputSchema must reject undeclared parameters")
        properties = schema.get("properties")
        if not isinstance(properties, Mapping):
            raise ValueError(f"{name}: inputSchema.properties must be an object")
        if not properties:
            raise ValueError(f"{name}: inputSchema unexpectedly exposes no parameters")
        required = schema.get("required") or []
        if not isinstance(required, list) or any(item not in properties for item in required):
            raise ValueError(f"{name}: inputSchema.required is invalid")
        for key, definition in properties.items():
            if not isinstance(key, str) or not isinstance(definition, Mapping) or not definition.get("type"):
                raise ValueError(f"{name}: parameter schema is invalid for {key!r}")

        arguments = case["arguments"]
        if context is not None:
            arguments = resolve_placeholders(arguments, context)
        concrete_keys = {
            str(key) for key in arguments
            if not PLACEHOLDER.fullmatch(str(key))
        }
        unknown = sorted(concrete_keys - set(properties))
        if unknown:
            raise ValueError(f"{name}: catalog arguments missing from live schema: {unknown}")
        injected = set(case.get("runtimeInjectedArguments") or [])
        unknown_injected = sorted(injected - set(properties))
        if unknown_injected:
            raise ValueError(f"{name}: runtime-injected arguments missing from live schema: {unknown_injected}")
        if context is not None or injected:
            missing_required = sorted(set(required) - concrete_keys - injected)
            if missing_required:
                raise ValueError(f"{name}: required live parameters are not supplied: {missing_required}")
    return {
        "ok": True,
        "toolCount": len(by_name),
        "emptySchemaTools": sorted(
            name for name, tool in by_name.items()
            if not ((tool.get("inputSchema") or {}).get("properties") or {})
        ),
    }


def main() -> int:
    parser = argparse.ArgumentParser(description="Validate the exact 78-tool live acceptance catalog.")
    parser.add_argument("--context-json", type=Path)
    parser.add_argument("--unity-project", type=Path)
    args = parser.parse_args()
    cases = load_catalog()
    placeholders = sorted(required_placeholders(cases))
    context: dict[str, Any] | None = None
    resolved = False
    if args.context_json:
        context = json.loads(args.context_json.read_text(encoding="utf-8"))
        if not isinstance(context, dict):
            raise ValueError("context JSON must be an object")
        resolve_placeholders(cases, context)
        resolved = True
    live_schema = None
    if args.unity_project:
        from unity_mcp_core_client import UnityMcpCoreClient

        live_schema = validate_tool_schemas(
            cases,
            UnityMcpCoreClient(args.unity_project.resolve()).list_tools(exposure_layer="execution"),
            context=context,
        )
    print(
        json.dumps(
            {
                "ok": True,
                "schema": SCHEMA,
                "toolCount": len(cases),
                "catalogSha256": catalog_digest(cases),
                "requiredPlaceholders": placeholders,
                "argumentsResolved": resolved,
                "liveSchema": live_schema,
                "executesTools": False,
            },
            ensure_ascii=True,
            sort_keys=True,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
