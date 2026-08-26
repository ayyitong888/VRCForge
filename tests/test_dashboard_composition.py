from __future__ import annotations

import ast
import copy
import hashlib
import json
from collections import Counter
from pathlib import Path

import dashboard_server


CONTRACT_PATH = Path(__file__).parent / "fixtures" / "dashboard_composition_contract_v1.json"
ROOT_SOURCE_PATH = Path(__file__).parents[1] / "dashboard_server.py"
EVENT_SOURCE_PATHS = (
    ROOT_SOURCE_PATH,
    Path(__file__).parents[1] / "memory_review_composition.py",
)


def _canonical_sha256(payload: object) -> str:
    encoded = json.dumps(
        payload,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


def _route_manifest() -> list[dict[str, object]]:
    return [
        {
            "kind": type(route).__name__,
            "name": str(getattr(route, "name", "") or ""),
            "path": str(getattr(route, "path", "") or ""),
            "methods": sorted(str(method) for method in (getattr(route, "methods", None) or [])),
        }
        for route in dashboard_server.app.routes
    ]


def _observed_literal_event_types() -> list[str]:
    event_types: set[str] = set()
    for source_path in EVENT_SOURCE_PATHS:
        tree = ast.parse(source_path.read_text(encoding="utf-8-sig"))
        for node in ast.walk(tree):
            if not isinstance(node, ast.Call) or not node.args:
                continue
            function = node.func
            if isinstance(function, ast.Attribute):
                name = function.attr
            elif isinstance(function, ast.Name):
                name = function.id
            else:
                name = ""
            if name not in {"broadcast", "broadcast_from_sync", "build_event_message"}:
                continue
            first_argument = node.args[0]
            if isinstance(first_argument, ast.Constant) and isinstance(first_argument.value, str):
                event_types.add(first_argument.value)
    return sorted(event_types)


def _top_level_composition_calls() -> list[str]:
    tree = ast.parse(ROOT_SOURCE_PATH.read_text(encoding="utf-8-sig"))
    return [
        ast.unparse(statement.value)
        for statement in tree.body
        if isinstance(statement, ast.Expr) and isinstance(statement.value, ast.Call)
    ]


def test_route_table_contract_matches_the_entry_baseline() -> None:
    contract = json.loads(CONTRACT_PATH.read_text(encoding="utf-8"))
    assert contract["schema"] == "vrcforge.dashboard_composition_contract.v1"
    manifest = _route_manifest()
    route_kinds = Counter(str(item["kind"]) for item in manifest)
    method_counts = Counter(
        method
        for item in manifest
        for method in item["methods"]
    )

    assert manifest == contract["routes"]["items"]
    assert len(manifest) == contract["routes"]["count"]
    assert dict(sorted(route_kinds.items())) == contract["routes"]["kinds"]
    assert dict(sorted(method_counts.items())) == contract["routes"]["methods"]
    assert _canonical_sha256(manifest) == contract["routes"]["tableSha256"]


def test_openapi_contract_matches_the_entry_baseline() -> None:
    contract = json.loads(CONTRACT_PATH.read_text(encoding="utf-8"))
    openapi = copy.deepcopy(dashboard_server.app.openapi())
    openapi.setdefault("info", {})["version"] = "<runtime-version>"
    schemas = openapi.get("components", {}).get("schemas", {})
    assert len(openapi.get("paths", {})) == contract["openApi"]["pathCount"]
    assert len(schemas) == contract["openApi"]["schemaCount"]
    assert sorted(schemas) == contract["openApi"]["schemaNames"]
    assert _canonical_sha256(schemas) == contract["openApi"]["schemasSha256"]
    assert _canonical_sha256(openapi) == contract["openApi"]["sha256WithoutRuntimeVersion"]


def test_catch_all_agent_mcp_mount_is_registered_last() -> None:
    routes = dashboard_server.app.routes
    catch_all = [
        (index, route)
        for index, route in enumerate(routes)
        if type(route).__name__ == "Mount"
        and str(getattr(route, "name", "")) == "agent_mcp"
    ]

    assert len(catch_all) == 1
    index, route = catch_all[0]
    assert index == len(routes) - 1
    assert str(getattr(route, "path", "")) == ""
    assert "app.mount('/', AGENT_MCP_MOUNT, name='agent_mcp')" in _top_level_composition_calls()


def test_composition_root_calls_are_exactly_once() -> None:
    top_level_calls = _top_level_composition_calls()
    calls = Counter(top_level_calls)
    expected_order = [
        "register_agent_gateway_tools()",
        "AGENT_GATEWAY.bind_runtime_planner(RUNTIME_PLANNER)",
        "install_primitive_basis_live_runtime(PRIMITIVE_BASIS_LIVE_SESSION)",
        "app.mount('/', AGENT_MCP_MOUNT, name='agent_mcp')",
    ]

    assert calls["register_agent_gateway_tools()"] == 1
    assert calls["AGENT_GATEWAY.bind_runtime_planner(RUNTIME_PLANNER)"] == 1
    assert calls["install_primitive_basis_live_runtime(PRIMITIVE_BASIS_LIVE_SESSION)"] == 1
    assert [top_level_calls.index(call) for call in expected_order] == sorted(
        top_level_calls.index(call) for call in expected_order
    )


def test_event_envelope_contract_keeps_exact_public_keys() -> None:
    contract = json.loads(CONTRACT_PATH.read_text(encoding="utf-8"))
    message = dashboard_server.build_event_message("contractProbe", {"value": 1})

    assert sorted(message) == contract["events"]["envelopeKeys"]
    assert message["type"] == "contractProbe"
    assert message["payload"] == {"value": 1}
    assert isinstance(message["timestamp"], str)
    assert _observed_literal_event_types() == contract["events"]["observedTypes"]


def test_atomic_disk_and_chat_rollback_contract_preserves_exact_bytes(tmp_path: Path) -> None:
    existing = tmp_path / "existing.json"
    created = tmp_path / "created.json"
    original = b"{\"exact\":true}\r\n"
    existing.write_bytes(original)
    snapshots, parent_existence = dashboard_server.snapshot_chat_storage_files([existing, created])

    dashboard_server.atomic_write_bytes(existing, b"changed")
    dashboard_server.atomic_write_bytes(created, b"created")
    assert dashboard_server.restore_chat_storage_files(snapshots, parent_existence)

    assert existing.read_bytes() == original
    assert not created.exists()
    assert not list(tmp_path.glob(".*.tmp"))
