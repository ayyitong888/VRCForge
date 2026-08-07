from __future__ import annotations

import ast
import copy
import hashlib
import json
from collections import Counter
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

import dashboard_server


CONTRACT_PATH = Path(__file__).parent / "fixtures" / "dashboard_composition_contract_v1.json"
DASHBOARD_SOURCE_PATH = Path(__file__).parents[1] / "dashboard_server.py"


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
    tree = ast.parse(DASHBOARD_SOURCE_PATH.read_text(encoding="utf-8-sig"))
    event_types: set[str] = set()
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


def test_composition_context_resolves_the_existing_facade_owners_late() -> None:
    context = dashboard_server.DASHBOARD_COMPOSITION_CONTEXT

    assert context.dashboard_state() is dashboard_server.DASHBOARD_STATE
    assert context.runtime_state() is dashboard_server.DASHBOARD_RUNTIME
    assert context.event_bus() is dashboard_server.EVENT_BUS
    assert context.agent_gateway() is dashboard_server.AGENT_GATEWAY

    replacements = {
        "DASHBOARD_STATE": SimpleNamespace(owner="state"),
        "DASHBOARD_RUNTIME": SimpleNamespace(owner="runtime"),
        "EVENT_BUS": SimpleNamespace(owner="events"),
        "AGENT_GATEWAY": SimpleNamespace(owner="gateway"),
    }
    with (
        patch("dashboard_server.DASHBOARD_STATE", replacements["DASHBOARD_STATE"]),
        patch("dashboard_server.DASHBOARD_RUNTIME", replacements["DASHBOARD_RUNTIME"]),
        patch("dashboard_server.EVENT_BUS", replacements["EVENT_BUS"]),
        patch("dashboard_server.AGENT_GATEWAY", replacements["AGENT_GATEWAY"]),
    ):
        assert context.dashboard_state() is replacements["DASHBOARD_STATE"]
        assert context.runtime_state() is replacements["DASHBOARD_RUNTIME"]
        assert context.event_bus() is replacements["EVENT_BUS"]
        assert context.agent_gateway() is replacements["AGENT_GATEWAY"]


def test_route_and_openapi_contract_match_the_1_5_entry_baseline() -> None:
    contract = json.loads(CONTRACT_PATH.read_text(encoding="utf-8"))
    assert contract["schema"] == "vrcforge.dashboard_composition_contract.v1"
    manifest = _route_manifest()
    route_kinds = Counter(str(item["kind"]) for item in manifest)
    method_counts = Counter(
        method
        for item in manifest
        for method in item["methods"]
    )

    assert len(manifest) == contract["routes"]["count"]
    assert dict(sorted(route_kinds.items())) == contract["routes"]["kinds"]
    assert dict(sorted(method_counts.items())) == contract["routes"]["methods"]
    assert _canonical_sha256(manifest) == contract["routes"]["tableSha256"]

    openapi = copy.deepcopy(dashboard_server.app.openapi())
    openapi.setdefault("info", {})["version"] = "<runtime-version>"
    assert len(openapi.get("paths", {})) == contract["openApi"]["pathCount"]
    assert len(openapi.get("components", {}).get("schemas", {})) == contract["openApi"]["schemaCount"]
    assert _canonical_sha256(openapi) == contract["openApi"]["sha256WithoutRuntimeVersion"]


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
