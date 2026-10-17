from __future__ import annotations

import hashlib
import json
import random
import re
import sqlite3
import uuid
from pathlib import Path

import pytest

import agent_scan_result_store as store_module
import agent_scan_scope_client as scope_module
from agent_scan_result_store import ScanResultStore
from unity_mcp_core_client import SCAN_SCOPE_TOOL_NAMES, UnityMcpCoreClient, UnityMcpCoreError
from unity_mcp_tool_contract import EXPECTED_TOOL_NAMES, PLANNING_TOOL_NAMES, READ_ONLY_TOOL_NAMES


def test_core_rejects_project_clip_inventory_before_accepting_explicit_roots():
    source = (Path(__file__).resolve().parents[1] / "Assets/VRCForge/Editor/MCP/ScanScopeJournal.cs").read_text()
    roots = source.split("private static string[] Roots", 1)[1].split("private static JObject Receipt", 1)[0]
    assert roots.index('arguments["includeAllProjectClips"]') < roots.index("var explicitPaths")
    assert 'throw new InvalidDataException("project_inventory_scope_unavailable")' in roots


def _tool(name: str) -> dict:
    read_only = name in READ_ONLY_TOOL_NAMES or name in SCAN_SCOPE_TOOL_NAMES
    return {
        "name": name,
        "description": f"When to use: Use {name}.\nWhen NOT to use: Avoid unrelated work.\nNegative example: Mention {name} only.",
        "inputSchema": {"type": "object"},
        "annotations": {"readOnlyHint": True} if read_only else {"destructiveHint": True},
        "_meta": {
            "permission": "ReadOnly" if read_only else "RequiresApproval",
            "whenToUse": "Use for its exact operation.",
            "doNotUse": "Do not use for unrelated requests.",
            "negativeExample": f"Mention {name} only.",
            "exposureLayer": "planning" if name in PLANNING_TOOL_NAMES else "execution",
        },
    }


def _core_info(*, capability: bool = True) -> dict:
    info = {"schema": "vrcforge.core_info.v1", "coreIdentity": "VRCForge"}
    if capability:
        info["scanScope"] = {
            "schema": scope_module.SCAN_SCOPE_SCHEMA,
            "tools": list(scope_module.SCAN_SCOPE_TOOLS),
            "implementation": "scan-scope-v1",
        }
    return info


def _completed(data: dict) -> dict:
    return {"structuredContent": {"success": True, "message": "ok", "data": data}, "isError": False}


def test_optional_readonly_registry_pair_is_validated_then_hidden_from_model_catalog():
    client = object.__new__(UnityMcpCoreClient)
    client._expected_tool_names = EXPECTED_TOOL_NAMES
    base = [_tool(name) for name in sorted(EXPECTED_TOOL_NAMES)]
    client._request = lambda *_args: {"tools": base + [_tool(name) for name in sorted(SCAN_SCOPE_TOOL_NAMES)]}

    tools = client.list_tools(exposure_layer="execution")

    assert {tool["name"] for tool in tools} == EXPECTED_TOOL_NAMES
    assert len(tools) == 97


@pytest.mark.parametrize("tools", [
    lambda: [_tool(name) for name in sorted(EXPECTED_TOOL_NAMES)] + [_tool("vrc_capture_scan_scope")],
    lambda: [_tool(name) for name in sorted(EXPECTED_TOOL_NAMES)] + [
        dict(_tool("vrc_capture_scan_scope"), _meta={**_tool("vrc_capture_scan_scope")["_meta"], "permission": "RequiresApproval"}),
        _tool("vrc_validate_scan_scope"),
    ],
])
def test_optional_registry_pair_must_be_complete_and_readonly(tools):
    client = object.__new__(UnityMcpCoreClient)
    client._expected_tool_names = EXPECTED_TOOL_NAMES
    client._request = lambda *_args: {"tools": tools()}
    with pytest.raises(UnityMcpCoreError):
        client.list_tools(exposure_layer="execution")


def test_scope_client_checks_capability_each_call_and_unwraps_completed_data(monkeypatch, tmp_path):
    calls = []
    capture = {
        "schema": scope_module.SCAN_SCOPE_SCHEMA,
        "verified": True,
        "token": "scope_" + uuid.uuid4().hex,
        "editorSession": str(uuid.uuid4()),
        "assetPaths": ["Assets/Avatar/Root.controller", "Assets/Avatar/Material.mat"],
        "capturedAt": "2026-10-05T00:00:00Z",
        "dependencyCoverage": "complete",
    }
    validation = {
        "schema": scope_module.SCAN_SCOPE_SCHEMA,
        "valid": True,
        "reason": "",
        "changedAssetPaths": [],
        "editorSession": capture["editorSession"],
    }

    class FakeClient:
        def __init__(self, _project, *, timeout_seconds):
            assert timeout_seconds == 3.0

        def core_info(self):
            calls.append(("core_info",))
            return _core_info()

        def call_tool(self, name, arguments):
            calls.append((name, arguments))
            return _completed(capture if name == scope_module.CAPTURE_SCAN_SCOPE_TOOL else validation)

    monkeypatch.setattr(scope_module, "UnityMcpCoreClient", FakeClient)
    client = scope_module.AgentScanScopeClient(tmp_path)
    args = {"controllerPath": "Assets/Avatar/Root.controller", "nested": {"limit": 20}}

    expected_capture = {**capture, "assetPaths": sorted(capture["assetPaths"])}
    assert client.capture("vrcforge_scan_fx_animator", args) == expected_capture
    assert client.capture("vrcforge_read_avatar_descriptor", args) == expected_capture
    assert client.validate(capture["token"]) == validation
    assert calls == [
        ("core_info",),
        (scope_module.CAPTURE_SCAN_SCOPE_TOOL, {"scanTool": "vrcforge_scan_fx_animator", "arguments": args}),
        ("core_info",),
        (scope_module.CAPTURE_SCAN_SCOPE_TOOL, {"scanTool": "vrcforge_read_avatar_descriptor", "arguments": args}),
        ("core_info",),
        (scope_module.VALIDATE_SCAN_SCOPE_TOOL, {"token": capture["token"]}),
    ]


def test_scope_adapter_accepts_scene_only_receipts_and_rejects_undeclared_scan_tools(monkeypatch, tmp_path):
    calls = []
    session = str(uuid.uuid4())

    class FakeClient:
        def __init__(self, *_args, **_kwargs):
            pass

        def core_info(self):
            return _core_info()

        def call_tool(self, name, args):
            calls.append((name, args))
            return _completed({
                "schema": scope_module.SCAN_SCOPE_SCHEMA,
                "verified": True,
                "token": "scope_" + uuid.uuid4().hex,
                "editorSession": session,
                "assetPaths": [],
                "capturedAt": "2026-10-05T00:00:00Z",
                "dependencyCoverage": "complete",
            })

    monkeypatch.setattr(scope_module, "UnityMcpCoreClient", FakeClient)
    client = scope_module.AgentScanScopeClient(tmp_path)
    proof = client.capture("vrcforge_scan_wardrobe", {"avatarPath": "Root"})
    assert proof["verified"] is True
    assert proof["assetPaths"] == []
    assert calls == [(scope_module.CAPTURE_SCAN_SCOPE_TOOL, {
        "scanTool": "vrcforge_scan_wardrobe", "arguments": {"avatarPath": "Root"},
    })]

    for tool in ("vrcforge_scan_project_index", "vrcforge_scan_avatar_performance", "vrcforge_scan_modular_avatar"):
        unsupported = client.capture(tool, {"projectPath": str(tmp_path)})
        assert unsupported == {
            "schema": scope_module.SCAN_SCOPE_SCHEMA,
            "verified": False,
            "reason": "scan_scope_undeclared",
        }
    assert len(calls) == 1


def test_old_core_and_domain_reload_gap_are_normal_unverified_results(monkeypatch, tmp_path):
    invoked = []

    class OldCore:
        def __init__(self, *_args, **_kwargs):
            pass

        def core_info(self):
            return _core_info(capability=False)

        def call_tool(self, *_args):
            invoked.append(True)
            raise AssertionError("optional tool called against an old Core")

    monkeypatch.setattr(scope_module, "UnityMcpCoreClient", OldCore)
    client = scope_module.AgentScanScopeClient(tmp_path)
    assert client.capture("vrcforge_scan_fx_animator", {})["reason"] == "capability_unavailable"
    assert client.validate("scope_" + uuid.uuid4().hex)["reason"] == "capability_unavailable"
    assert invoked == []

    class ReloadedCore(OldCore):
        def core_info(self):
            return _core_info()

        def call_tool(self, *_args):
            return _completed({
                "schema": scope_module.SCAN_SCOPE_SCHEMA,
                "valid": False,
                "reason": "domain_reload_observation_gap",
                "changedAssetPaths": [],
                "editorSession": str(uuid.uuid4()),
            })

    monkeypatch.setattr(scope_module, "UnityMcpCoreClient", ReloadedCore)
    assert client.validate("scope_" + uuid.uuid4().hex)["reason"] == "domain_reload_observation_gap"


class GraphCore:
    """Mock Core transport whose scope comes from traversing actual fixture GUID references."""

    def __init__(self, project: Path):
        self.project = project
        self.editor_session = str(uuid.uuid4())
        self.scanned: list[str] = []
        self.visited_counts: list[int] = []
        self.capture_calls = 0
        self.validation_calls = 0
        self.proofs: dict[str, dict] = {}
        self.validation_reason = ""
        self.changed_paths: list[str] = []

    def _walk(self, root: str) -> list[str]:
        guid_paths = {}
        for meta in (self.project / "Assets").rglob("*.meta"):
            match = re.search(r"(?m)^guid:\s*([0-9a-f]{32})\s*$", meta.read_text(encoding="utf-8"))
            if match:
                guid_paths[match.group(1)] = meta.relative_to(self.project).as_posix()[:-5]
        found, pending = set(), [root]
        while pending:
            path = pending.pop()
            if path in found:
                continue
            found.add(path)
            content = (self.project / path).read_text(encoding="utf-8")
            for guid in re.findall(r"\b[0-9a-f]{32}\b", content):
                target = guid_paths.get(guid)
                if target is not None and target not in found:
                    pending.append(target)
        self.scanned.append(root)
        self.visited_counts.append(len(found))
        return sorted(found)

    @staticmethod
    def _fingerprint(project: Path, paths: list[str]) -> dict[str, str]:
        return {
            path: hashlib.sha256((project / path).read_bytes()).hexdigest()
            + hashlib.sha256((project / (path + ".meta")).read_bytes()).hexdigest()
            for path in paths
        }

    def capture(self, tool: str, arguments: dict) -> dict:
        assert tool == "vrcforge_scan_fx_animator"
        self.capture_calls += 1
        paths = self._walk(arguments["controllerPath"]) if "controllerPath" in arguments else []
        token = "scope_" + uuid.uuid4().hex
        proof = {
            "schema": scope_module.SCAN_SCOPE_SCHEMA,
            "verified": True,
            "token": token,
            "editorSession": self.editor_session,
            "assetPaths": paths,
            "capturedAt": "2026-10-05T00:00:00Z",
            "dependencyCoverage": "complete",
        }
        self.proofs[token] = {"proof": proof, "fingerprints": self._fingerprint(self.project, paths)}
        return proof

    def validate(self, token: str) -> dict:
        self.validation_calls += 1
        saved = self.proofs[token]
        current = self._fingerprint(self.project, saved["proof"]["assetPaths"])
        changed = sorted(set(self.changed_paths) | {
            path for path, digest in saved["fingerprints"].items() if current.get(path) != digest
        })
        valid = not changed and not self.validation_reason
        return {
            "schema": scope_module.SCAN_SCOPE_SCHEMA,
            "valid": valid,
            "reason": self.validation_reason or ("asset_fingerprint_changed" if changed else ""),
            "changedAssetPaths": changed,
            "editorSession": self.editor_session,
        }


@pytest.fixture
def graph_project(tmp_path, monkeypatch):
    project = tmp_path / "Project"
    assets = project / "Assets" / "Graph"
    assets.mkdir(parents=True)
    rng = random.Random(20261005)
    paths = [f"Assets/Graph/node-{i}.asset" for i in range(18)]
    guids = [uuid.uuid4().hex for _ in paths]
    edges = {0: {1, 2}}
    for index in range(1, len(paths)):
        edges[index] = {target for target in range(1, len(paths)) if target != index and rng.random() < 0.12}
    contents = {}
    for index, path in enumerate(paths):
        contents[path] = "references: " + " ".join(guids[target] for target in sorted(edges[index]))
        (project / path).write_text(contents[path], encoding="utf-8")
        (project / (path + ".meta")).write_text(f"fileFormatVersion: 2\nguid: {guids[index]}\n", encoding="utf-8")
    core = GraphCore(project)
    monkeypatch.setattr(store_module, "AgentScanScopeClient", lambda _root: core)
    return project, paths, core, contents


def test_random_guid_reference_graph_expands_real_store_fingerprints_and_reader_lookup(tmp_path, graph_project, monkeypatch):
    project, paths, core, contents = graph_project
    # Derive the same deterministic adjacency from the actual serialized GUID references.
    guid_to_path = {
        re.search(r"(?m)^guid:\s*([0-9a-f]{32})\s*$", (project / (path + ".meta")).read_text()).group(1): path
        for path in paths
    }
    expected_paths = set()
    pending = [paths[0]]
    while pending:
        current = pending.pop()
        if current in expected_paths:
            continue
        expected_paths.add(current)
        body = (project / current).read_text(encoding="utf-8")
        pending.extend(guid_to_path[guid] for guid in re.findall(r"\b[0-9a-f]{32}\b", body) if guid in guid_to_path)
    assert expected_paths == set(core._walk(paths[0]))
    core.scanned.clear()
    core.visited_counts.clear()

    fingerprint_batches = []
    actual_fingerprints = store_module._fingerprints

    def counted_fingerprints(root, selected):
        fingerprint_batches.append(set(selected))
        return actual_fingerprints(root, selected)

    monkeypatch.setattr(store_module, "_fingerprints", counted_fingerprints)
    store = ScanResultStore(tmp_path / "result-store")
    arguments = {"controllerPath": paths[0]}
    step = {"id": str(uuid.uuid4()), "tool": "vrcforge_scan_fx_animator", "status": "ok", "result": {"count": len(contents)}}
    start = store.start(project, step["tool"], arguments)
    assert start["proof"]["verified"] is True
    metadata = store.capture("session", "turn-a", project, step, arguments, expected_generation=start)

    assert metadata["scope"] == "validated_scope"
    assert metadata["freshness"] == "validated"
    assert metadata["scanScope"]["dependencyCoverage"] == "complete"
    assert set(metadata["scanScope"]["assetPaths"]) == expected_paths
    assert len(core.scanned) == 1
    assert core.visited_counts == [len(expected_paths)]
    assert core.capture_calls == 1
    assert core.validation_calls == 1
    assert all(expected_paths.issubset(batch) for batch in fingerprint_batches)
    with sqlite3.connect(store._path) as connection:
        saved_scope = json.loads(connection.execute("SELECT scope FROM snapshots").fetchone()[0])
        assert saved_scope["dependencyCoverage"] == "unverified"
        assert saved_scope["assetPaths"] == [paths[0]]
        assert connection.execute("SELECT COUNT(*) FROM scan_scope_proofs").fetchone()[0] == 1
        assert connection.execute("SELECT COUNT(*) FROM scan_scope_links").fetchone()[0] == 1

    fingerprint_batches.clear()
    found = store.lookup("session", "turn-b", project, metadata["resultRef"])
    assert found == {"step": step}
    assert core.validation_calls == 2
    assert expected_paths.issubset(fingerprint_batches[-1])


def test_validated_scope_survives_backend_store_restart_with_same_live_core(tmp_path, graph_project):
    project, paths, _core, _contents = graph_project
    store = ScanResultStore(tmp_path / "persistent-store")
    arguments = {"controllerPath": paths[0]}
    step = {"id": "restart", "tool": "vrcforge_scan_fx_animator", "status": "ok", "result": {"count": 1}}
    start = store.start(project, step["tool"], arguments)
    metadata = store.capture("session", "turn-a", project, step, arguments, expected_generation=start)

    restarted_store = ScanResultStore(tmp_path / "persistent-store")
    assert restarted_store.lookup("session", "turn-b", project, metadata["resultRef"]) == {"step": step}


def test_known_project_write_uses_exact_scope_while_unknown_global_write_still_expires(tmp_path, graph_project):
    project, paths, core, _contents = graph_project
    store = ScanResultStore(tmp_path / "write-store")
    arguments = {"controllerPath": paths[0]}
    step = {"id": "write", "tool": "vrcforge_scan_fx_animator", "status": "ok", "result": {}}
    start = store.start(project, step["tool"], arguments)
    metadata = store.capture("session", "turn-a", project, step, arguments, expected_generation=start)

    unrelated = project / "Assets" / "Graph" / "unrelated.asset"
    unrelated.write_text("outside proof closure", encoding="utf-8")
    unrelated.with_name(unrelated.name + ".meta").write_text("guid: " + uuid.uuid4().hex, encoding="utf-8")
    marker = store.begin_mutation(project, "vrcforge_set_material_shader")
    unrelated.write_text("ordinary known write", encoding="utf-8")
    store.end_mutation(project, "vrcforge_set_material_shader", marker=marker)
    assert store.lookup("session", "turn-b", project, metadata["resultRef"]) == {"step": step}

    future_step = {"id": "future-write", "tool": "vrcforge_scan_fx_animator", "status": "ok", "result": {}}
    future_start = store.start(project, future_step["tool"], arguments)
    future_metadata = store.capture("session", "turn-c", project, future_step, arguments,
                                    expected_generation=future_start)
    store.invalidate(project, None, "vrcforge_future_project_write")
    unknown_write = store.lookup("session", "turn-d", project, future_metadata["resultRef"])
    assert unknown_write["expired"] is True
    assert unknown_write["reason"] == "vrcforge_future_project_write"

    shell_step = {"id": "shell-write", "tool": "vrcforge_scan_fx_animator", "status": "ok", "result": {}}
    shell_start = store.start(project, shell_step["tool"], arguments)
    shell_metadata = store.capture("session", "turn-e", project, shell_step, arguments,
                                   expected_generation=shell_start)
    marker = store.begin_mutation("", "vrcforge_shell_execute")
    store.end_mutation("", "vrcforge_shell_execute", marker=marker)
    expired = store.lookup("session", "turn-f", project, shell_metadata["resultRef"])
    assert expired["expired"] is True
    assert expired["reason"] == "vrcforge_shell_execute"


def test_domain_reload_observation_gap_is_reported_without_claiming_core_reload_reuse(tmp_path, graph_project):
    project, paths, core, _contents = graph_project
    store = ScanResultStore(tmp_path / "reload-store")
    arguments = {"controllerPath": paths[0]}
    step = {"id": "reload", "tool": "vrcforge_scan_fx_animator", "status": "ok", "result": {}}
    start = store.start(project, step["tool"], arguments)
    metadata = store.capture("session", "turn-a", project, step, arguments, expected_generation=start)
    core.validation_reason = "domain_reload_observation_gap"

    expired = store.lookup("session", "turn-b", project, metadata["resultRef"])

    assert expired["expired"] is True
    assert expired["reason"] == "domain_reload_observation_gap"


def test_post_scan_core_change_expires_same_turn_and_preserves_full_rescan_scope(tmp_path, graph_project):
    project, paths, core, _contents = graph_project
    store = ScanResultStore(tmp_path / "post-scan-expiry")
    arguments = {"controllerPath": paths[0], "avatarPath": "Avatar/Root"}
    step = {"id": "changed", "tool": "vrcforge_scan_fx_animator", "status": "ok", "result": {}}
    core.validation_reason = "scope_content_or_revision_changed"
    core.changed_paths = [paths[-1]]
    start = store.start(project, step["tool"], arguments)

    metadata = store.capture("session", "turn-a", project, step, arguments, expected_generation=start)

    assert metadata["expired"] is True
    assert metadata["reason"] == "scope_content_or_revision_changed"
    assert metadata["changedAssetPaths"] == [paths[-1]]
    assert metadata["rescanPolicy"] == "full_scope"
    lookup = store.lookup("session", "turn-a", project, metadata["resultRef"])
    assert lookup["expired"] is True
    assert lookup["changedAssetPaths"] == [paths[-1]]
    assert lookup["rescanScope"]["arguments"] == arguments
    assert set(lookup["rescanScope"]["assetPaths"]) == set(core.proofs[next(iter(core.proofs))]["proof"]["assetPaths"])
    assert core.validation_calls == 1


def test_scene_only_verified_scope_can_link_without_disk_paths(tmp_path, graph_project):
    project, _paths, core, _contents = graph_project
    store = ScanResultStore(tmp_path / "scene-only")
    arguments = {"avatarPath": "Avatar/Root"}
    step = {"id": "scene", "tool": "vrcforge_scan_fx_animator", "status": "ok", "result": {}}
    start = store.start(project, step["tool"], arguments)
    assert start["proof"]["verified"] is True
    assert start["proof"]["assetPaths"] == []

    metadata = store.capture("session", "turn-a", project, step, arguments, expected_generation=start)

    assert metadata["scope"] == "validated_scope"
    assert metadata["scanScope"]["dependencyCoverage"] == "complete"
    assert metadata["scanScope"]["assetPaths"] == []
    assert store.lookup("session", "turn-b", project, metadata["resultRef"]) == {"step": step}


def test_start_rejects_project_generation_change_during_core_capture(tmp_path, graph_project):
    project, paths, core, _contents = graph_project
    store = ScanResultStore(tmp_path / "start-race")
    original_capture = core.capture

    def mutate_while_capturing(tool, arguments):
        proof = original_capture(tool, arguments)
        marker = store.begin_mutation(project, "vrcforge_set_property")
        store.end_mutation(project, "vrcforge_set_property", marker=marker)
        return proof

    core.capture = mutate_while_capturing
    start = store.start(project, "vrcforge_scan_fx_animator", {"controllerPath": paths[0]})
    assert start["generation"] == 0
    assert start["proof"]["verified"] is False
    assert start["proof"]["reason"] == "mutation_during_scope_capture"


def test_malformed_database_proof_never_reports_complete_or_validated_scope(tmp_path, graph_project):
    project, paths, _core, _contents = graph_project
    store = ScanResultStore(tmp_path / "malformed-proof")
    arguments = {"controllerPath": paths[0]}
    step = {"id": "malformed", "tool": "vrcforge_scan_fx_animator", "status": "ok", "result": {}}
    start = store.start(project, step["tool"], arguments)
    metadata = store.capture("session", "turn-a", project, step, arguments, expected_generation=start)
    with sqlite3.connect(store._path) as connection:
        connection.execute("UPDATE scan_scope_proofs SET proof=?", (json.dumps({"schema": scope_module.SCAN_SCOPE_SCHEMA, "verified": True}),))

    expired = store.lookup("session", "turn-b", project, metadata["resultRef"])

    assert expired["expired"] is True
    assert expired["reason"] == "core_scope_unavailable"
    assert expired["rescanScope"]["dependencyCoverage"] == "unverified"


def test_asset_path_limit_still_applies_without_a_valid_proof(tmp_path, graph_project, monkeypatch):
    project, _paths, _core, _contents = graph_project
    monkeypatch.setattr(store_module, "AgentScanScopeClient", lambda _root: type("NoScope", (), {
        "capture": lambda _self, *_args: {"verified": False, "reason": "capability_unavailable"},
        "validate": lambda _self, *_args: {"valid": False, "reason": "capability_unavailable"},
    })())
    store = ScanResultStore(tmp_path / "path-limit")
    arguments = {"assetPaths": [f"Assets/Missing/{index}.asset" for index in range(store_module.MAX_ASSET_PATHS + 1)]}
    step = {"id": "too-many", "tool": "vrcforge_scan_fx_animator", "status": "ok", "result": {}}
    with pytest.raises(ValueError, match="path limit"):
        store.capture("session", "turn-a", project, step, arguments)


def test_legacy_snapshot_remains_current_turn_only_when_core_capability_is_absent(tmp_path, graph_project, monkeypatch):
    project, paths, core, _contents = graph_project
    monkeypatch.setattr(store_module, "AgentScanScopeClient", lambda _root: type("NoScope", (), {
        "capture": lambda _self, *_args: {"schema": scope_module.SCAN_SCOPE_SCHEMA, "verified": False,
                         "reason": "capability_unavailable"},
        "validate": lambda _self, *_args: {"valid": False, "reason": "capability_unavailable"},
    })())
    store = ScanResultStore(tmp_path / "legacy-store")
    step = {"id": "legacy", "tool": "vrcforge_scan_fx_animator", "status": "ok", "result": {}}
    arguments = {"controllerPath": paths[0]}
    start = store.start(project, step["tool"], arguments)
    assert start["proof"]["verified"] is False
    metadata = store.capture("session", "turn-a", project, step, arguments, expected_generation=start)
    assert metadata["scope"] == "current_turn"
    assert store.lookup("session", "turn-a", project, metadata["resultRef"]) == {"step": step}
    assert store.lookup("session", "turn-b", project, metadata["resultRef"])["reason"] == "core_scope_unavailable"
