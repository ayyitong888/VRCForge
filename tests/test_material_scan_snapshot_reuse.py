from __future__ import annotations

import json
import re
import uuid
from pathlib import Path

import pytest
import agent_scan_scope_client as scope_module
import agent_scan_result_store as store_module
from agent_scan_scope_budget import ScopeCaptureBudget, capture_admission_reason, scope_request
import dashboard_server
from agent_scan_result_store import ScanResultStore
from agent_tool_result_reader import result_continuation
from dashboard_api_models import ShaderMaterialScanRequest
from runtime_planner_service import sanitize_planner_observation_text
from tests.test_native_runtime_gateway import call, finish, run, setup_gateway


def _material_rows(count: int) -> list[dict]:
    return [
        {
            "material_id": f"material-{index:03d}",
            "material_name": f"Material {index:03d}",
            "renderer_path": f"Avatar/Renderer{index:03d}",
            "shader_name": "Standard",
            "material_id_ambiguous": False,
            "renderer_id_ambiguous": False,
        }
        for index in range(count)
    ]


def _result_ref(request: dict) -> str:
    for message in reversed(request["messages"]):
        if message.get("role") != "tool":
            continue
        content = message.get("content")
        if not isinstance(content, str):
            continue
        match = re.search(r"result_[0-9a-f]{32}", content)
        if match:
            return match.group(0)
    raise AssertionError("The scan result continuation was not returned to the Agent")


def _read_page(call_id: str, offset: int):
    def reply(request: dict) -> dict:
        return call(call_id, "vrcforge_read_tool_result", {
            "resultRef": _result_ref(request),
            "jsonPointer": "/index",
            "offset": offset,
            "limit": 20,
        })

    return reply


def test_material_index_pages_from_one_private_result_reference(tmp_path, monkeypatch):
    rows = _material_rows(76)
    business_scans = []
    editor_session = str(uuid.uuid4())
    proof = {
        "schema": scope_module.SCAN_SCOPE_SCHEMA,
        "verified": True,
        "token": "scope_" + uuid.uuid4().hex,
        "editorSession": editor_session,
        "assetPaths": [],
        "capturedAt": "2026-10-18T00:00:00Z",
        "dependencyCoverage": "complete",
    }

    class ScopeClient:
        def __init__(self, *_args, **_kwargs):
            self.capture_budget = None

        def capture(self, *_args, **_kwargs):
            return proof

        def validate(self, _token):
            return {"valid": True, "reason": "", "changedAssetPaths": [],
                    "editorSession": editor_session}

    gateway, model, _ = setup_gateway(tmp_path, [
        call("material-scan", "vrcforge_scan_materials", {
            "avatarPath": "Avatar", "indexOnly": True, "limit": 25,
        }),
        _read_page("material-page-0", 0),
        _read_page("material-page-20", 20),
        _read_page("material-page-40", 40),
        _read_page("material-page-60", 60),
        finish,
    ])

    def scan_once(*_args, **_kwargs):
        business_scans.append(True)
        return {"materials": rows, "summary": {"materialCount": len(rows)}}

    monkeypatch.setattr(dashboard_server, "AGENT_GATEWAY", gateway)
    monkeypatch.setattr(dashboard_server, "load_dashboard_settings", lambda _request: object())
    monkeypatch.setattr(dashboard_server, "scan_shader_materials_direct", scan_once)
    monkeypatch.setattr(dashboard_server, "emit_log", lambda *_args, **_kwargs: None)
    monkeypatch.setattr(store_module, "AgentScanScopeClient", ScopeClient)
    gateway.register_tool(
        "vrcforge_scan_materials",
        "When to use: scan the current avatar material index.\nWhen NOT to use: edit materials.",
        "read/debug",
        lambda params: dashboard_server.scan_shader_materials_sync(
            ShaderMaterialScanRequest(**params)
        ),
    )

    response = run(gateway, tmp_path, message="List all material IDs")

    scan_steps = [step for step in response["steps"] if step.get("tool") == "vrcforge_scan_materials"]
    page_steps = [step for step in response["steps"] if step.get("tool") == "vrcforge_read_tool_result"]
    assert len(scan_steps) == 1
    assert len(business_scans) == 1
    assert scan_steps[0]["result"]["paging"]["returned"] == 25
    assert len(page_steps) == 4

    # The source's 25-row page was hidden in the actual model summary.
    # Reconstruct coverage from delivered reader rows, not the private receipt.
    observed = []
    reference = scan_steps[0]["resultRead"]["resultRef"]
    for step in page_steps:
        page = step["result"]
        assert page["resultRef"] == reference
        assert page["jsonPointer"] == "/index"
        assert page["sourceTool"] == "vrcforge_scan_materials"
        assert page["totalItems"] == 76
        observed.extend(item["value"] for item in page["items"])
    assert [row["material_id"] for row in observed] == [row["material_id"] for row in rows]
    assert len({row["material_id"] for row in observed}) == 76

    tool_message = next(message for message in model.requests[1]["messages"] if message.get("role") == "tool")
    tool_observations = json.loads(tool_message["content"])["observations"]
    scan_observation = next(item["observation"] for item in tool_observations
                            if item.get("runtimeTool") == "vrcforge_scan_materials")
    exposed_scan_messages = json.dumps(model.requests[1]["messages"], ensure_ascii=False)
    assert "material-075" not in exposed_scan_messages
    continuation = json.loads(scan_observation.split("; resultContinuation=", 1)[1])
    assert continuation["nextRequest"]["tool"] == "vrcforge_read_tool_result"
    assert continuation["nextRequest"]["arguments"] == {
        "resultRef": reference, "jsonPointer": "/index", "offset": 0, "limit": 20,
    }
    assert "source scan for later offsets" in scan_observation


def test_material_proof_unavailable_does_not_expose_private_full_index(tmp_path, monkeypatch):
    rows = _material_rows(76)
    business_scans = []
    gateway, model, _ = setup_gateway(tmp_path, [
        call("material-scan", "vrcforge_scan_materials", {
            "avatarPath": "Avatar", "indexOnly": True, "limit": 25,
        }),
        _read_page("material-page-25", 25),
        finish,
    ])

    def scan_once(*_args, **_kwargs):
        business_scans.append(True)
        return {"materials": rows, "summary": {"materialCount": len(rows)}}

    monkeypatch.setattr(dashboard_server, "AGENT_GATEWAY", gateway)
    monkeypatch.setattr(dashboard_server, "load_dashboard_settings", lambda _request: object())
    monkeypatch.setattr(dashboard_server, "scan_shader_materials_direct", scan_once)
    monkeypatch.setattr(dashboard_server, "emit_log", lambda *_args, **_kwargs: None)
    monkeypatch.setattr(gateway._scan_results, "start", lambda *_args, **_kwargs: {
        "generation": 0,
        "proof": {"schema": scope_module.SCAN_SCOPE_SCHEMA, "verified": False,
                  "reason": "capability_unavailable"},
    })
    gateway.register_tool(
        "vrcforge_scan_materials",
        "When to use: scan the current avatar material index.\nWhen NOT to use: edit materials.",
        "read/debug",
        lambda params: dashboard_server.scan_shader_materials_sync(
            ShaderMaterialScanRequest(**params)
        ),
    )

    response = run(gateway, tmp_path, message="List all material IDs")
    scan = next(step for step in response["steps"] if step.get("tool") == "vrcforge_scan_materials")
    page = next(step for step in response["steps"] if step.get("tool") == "vrcforge_read_tool_result")
    assert len(business_scans) == 1
    assert scan["result"]["paging"]["total"] == 76
    assert "nextRequest" not in scan["result"]["paging"]
    assert "Only the returned page is readable" in scan["result"]["readHints"]["discovery"]
    assert page["result"]["status"] == "needs_rescan"
    assert page["result"]["reason"] == "core_scope_unavailable"
    assert page["result"]["items"] == []
    assert "material-075" not in json.dumps(
        [request["messages"] for request in model.requests], ensure_ascii=False,
    )


def test_material_store_failure_does_not_advertise_full_index_paging(tmp_path, monkeypatch):
    rows = _material_rows(76)
    gateway, _, _ = setup_gateway(tmp_path, [
        call("material-scan", "vrcforge_scan_materials", {
            "avatarPath": "Avatar", "indexOnly": True, "limit": 25,
        }),
        finish,
    ])
    monkeypatch.setattr(dashboard_server, "AGENT_GATEWAY", gateway)
    monkeypatch.setattr(dashboard_server, "load_dashboard_settings", lambda _request: object())
    monkeypatch.setattr(dashboard_server, "scan_shader_materials_direct", lambda *_args, **_kwargs: {
        "materials": rows, "summary": {"materialCount": len(rows)},
    })
    monkeypatch.setattr(dashboard_server, "emit_log", lambda *_args, **_kwargs: None)
    monkeypatch.setattr(gateway._scan_results, "start", lambda *_args, **_kwargs: {
        "generation": 0,
        "proof": {"schema": scope_module.SCAN_SCOPE_SCHEMA, "verified": False,
                  "reason": "capability_unavailable"},
    })
    monkeypatch.setattr(gateway._scan_results, "capture", lambda *_args, **_kwargs: (
        _ for _ in ()
    ).throw(ValueError("private store unavailable")))
    gateway.register_tool(
        "vrcforge_scan_materials",
        "When to use: scan the current avatar material index.\nWhen NOT to use: edit materials.",
        "read/debug",
        lambda params: dashboard_server.scan_shader_materials_sync(
            ShaderMaterialScanRequest(**params)
        ),
    )

    response = run(gateway, tmp_path, message="List all material IDs")
    scan = next(step for step in response["steps"] if step.get("tool") == "vrcforge_scan_materials")
    assert scan["result"]["paging"]["total"] == 76
    assert "nextOffset" not in scan["result"]["paging"]
    assert "nextRequest" not in scan["result"]["paging"]
    assert scan["resultRead"]["collections"][0]["count"] == 25
    assert "Only the returned page is readable" in scan["result"]["readHints"]["discovery"]


def test_material_scan_alias_uses_core_scope_and_unproven_snapshot_is_unreadable(
    tmp_path, monkeypatch,
):
    scope_call = []
    editor_session = str(uuid.uuid4())
    target = {
        "schema": "vrcforge.execution_target.v1",
        "scope": "avatar",
        "scene": {"guid": "a" * 32, "revision": "1", "digest": "b" * 64},
        "avatar": {
            "name": "Avatar",
            "exactHierarchyPath": "World/Avatar",
            "globalObjectId": "GlobalObjectId_V1-2-" + "c" * 32 + "-123-0",
        },
    }

    def validate_target(value, *, project_root, required_scope):
        assert Path(project_root) == tmp_path
        assert required_scope == "avatar"
        return value

    monkeypatch.setattr(scope_module, "validate_runtime_execution_target", validate_target)

    class FakeCore:
        def __init__(self, *_args, **_kwargs):
            pass

        def core_info(self):
            return {
                "instanceId": "test-core-instance",
                "scanScope": {
                    "schema": scope_module.SCAN_SCOPE_SCHEMA,
                    "implementation": "scan-scope-v1",
                    "tools": list(scope_module.SCAN_SCOPE_TOOLS),
                }
            }

        def call_tool(self, name, arguments):
            scope_call.append((name, arguments))
            if name == "vrc_get_execution_targets":
                return {"isError": False, "structuredContent": {"success": True, "data": {
                    "targets": [target], "ambiguous": False, "resolutionCandidateCount": 1,
                }}}
            return {"isError": False, "structuredContent": {"success": True, "data": {
                "schema": scope_module.SCAN_SCOPE_SCHEMA,
                "verified": True,
                "token": "scope_" + uuid.uuid4().hex,
                "editorSession": editor_session,
                "assetPaths": [],
                "capturedAt": "2026-10-18T00:00:00Z",
                "dependencyCoverage": "complete",
            }}}

    monkeypatch.setattr(scope_module, "UnityMcpCoreClient", FakeCore)
    client = scope_module.AgentScanScopeClient(tmp_path)
    client.capture_budget = ScopeCaptureBudget()
    proof = client.capture("vrcforge_scan_materials", {
        "avatarPath": "Avatar",
        "objectIds": ["global:GlobalObjectId_V1-2-spoofed"],
        "assetPaths": ["Assets/Other.asset"],
        "assetPath": "Assets/Other.asset",
    })
    assert proof["verified"] is True
    assert scope_call == [
        ("vrc_get_execution_targets", {"scope": "avatar"}),
        (scope_module.CAPTURE_SCAN_SCOPE_TOOL, {
            "scanTool": "vrcforge_scan_avatar_materials",
            "arguments": {"avatarPath": "World/Avatar"},
            "objectIds": ["global:" + target["avatar"]["globalObjectId"]],
        }),
    ]

    project = tmp_path / "UnityProject"
    project.mkdir()
    store = ScanResultStore(tmp_path / "store")
    arguments = {"avatarPath": "Avatar"}
    step = {
        "index": 0,
        "actionId": uuid.uuid4().hex,
        "tool": "vrcforge_scan_materials",
        "status": "executed",
        "result": {"ok": True, "index": _material_rows(2)},
    }
    step["resultRead"] = result_continuation(
        "owner-session", "turn-one", str(project), step, sanitize_planner_observation_text,
    )
    reference = step["resultRead"]["resultRef"]
    unavailable = {"generation": 0, "proof": {
        "schema": scope_module.SCAN_SCOPE_SCHEMA,
        "verified": False,
        "reason": "capability_unavailable",
    }}
    metadata = store.capture(
        "owner-session", "turn-one", project, step, arguments,
        expected_generation=unavailable,
    )

    assert metadata["scope"] == "current_turn"
    same_turn = store.lookup("owner-session", "turn-one", project, reference)
    assert same_turn["expired"] is True
    assert same_turn["reason"] == "core_scope_unavailable"
    assert store.lookup("other-session", "turn-one", project, reference) is None
    assert store.lookup("owner-session", "turn-one", tmp_path / "other-project", reference) is None
    cross_turn = store.lookup("owner-session", "turn-two", project, reference)
    assert cross_turn["expired"] is True
    assert cross_turn["reason"] == "core_scope_unavailable"

    marker = store.begin_mutation(project, "test-material-revision")
    store.end_mutation(project, "test-material-revision", marker=marker)
    changed = store.lookup("owner-session", "turn-one", project, reference)
    assert changed["expired"] is True
    # The earlier cross-turn attempt already expired this proofless ref; later
    # events do not rewrite its first, authoritative expiry reason.
    assert changed["reason"] == "core_scope_unavailable"


def test_material_scan_scope_requires_one_validated_host_target_before_capture(tmp_path, monkeypatch):
    state = {"targets": [], "validation_fails": False, "incomplete": False}
    calls = []

    class FakeCore:
        def __init__(self, *_args, **_kwargs):
            pass

        def core_info(self):
            return {"scanScope": {
                "schema": scope_module.SCAN_SCOPE_SCHEMA,
                "implementation": "scan-scope-v1",
                "tools": list(scope_module.SCAN_SCOPE_TOOLS),
            }}

        def call_tool(self, name, arguments):
            calls.append((name, arguments))
            if name == "vrc_get_execution_targets":
                data = {
                    "targets": list(state["targets"]),
                    "ambiguous": len(state["targets"]) != 1,
                }
                if not state["incomplete"]:
                    data["resolutionCandidateCount"] = len(state["targets"])
                return {"structuredContent": {"success": True, "data": {
                    **data,
                }}}
            raise AssertionError("Capture must not run without a unique validated target")

    def validate_target(value, **_kwargs):
        if state["validation_fails"]:
            raise ValueError("target validation failed")
        return value

    monkeypatch.setattr(scope_module, "UnityMcpCoreClient", FakeCore)
    monkeypatch.setattr(scope_module, "validate_runtime_execution_target", validate_target)
    client = scope_module.AgentScanScopeClient(tmp_path)
    candidate = {
        "schema": "vrcforge.execution_target.v1", "scope": "avatar",
        "scene": {"guid": "a" * 32},
        "avatar": {"name": "Avatar", "exactHierarchyPath": "World/Avatar",
                   "globalObjectId": "GlobalObjectId_V1-2-" + "c" * 32 + "-123-0"},
    }
    state["targets"] = [candidate, candidate]
    ambiguous = client.capture("vrcforge_scan_materials", {"avatarPath": "Avatar"})
    assert ambiguous["verified"] is False
    assert ambiguous["reason"] == "scope_capture_identity_unavailable"
    assert [name for name, _ in calls] == ["vrc_get_execution_targets"]

    calls.clear()
    state.update(targets=[candidate], validation_fails=True)
    unvalidated = client.capture("vrcforge_scan_materials", {"avatarPath": "Avatar"})
    assert unvalidated["verified"] is False
    assert unvalidated["reason"] == "scope_capture_identity_unavailable"
    assert [name for name, _ in calls] == ["vrc_get_execution_targets"]

    calls.clear()
    state.update(targets=[candidate], validation_fails=False, incomplete=True)
    incomplete = client.capture("vrcforge_scan_materials", {"avatarPath": "Avatar"})
    assert incomplete["verified"] is False
    assert incomplete["reason"] == "scope_capture_identity_unavailable"
    assert [name for name, _ in calls] == ["vrc_get_execution_targets"]


def test_material_host_root_is_the_only_scene_capture_admission_exception():
    exact = "global:GlobalObjectId_V1-2-" + "c" * 32 + "-123-0"
    request = scope_request("vrcforge_scan_materials", {
        "avatarPath": "World/Avatar",
        "objectIds": ["global:GlobalObjectId_V1-spoofed"],
        "assetPaths": ["Assets/Spoof.asset"],
    }, host_object_ids=[exact])
    assert request == {
        "scanTool": "vrcforge_scan_avatar_materials",
        "arguments": {"avatarPath": "World/Avatar"},
        "objectIds": [exact],
    }
    assert capture_admission_reason(request) == "scope_capture_unbounded"
    assert capture_admission_reason(request, trusted_object_root=True) is None

    broad = {**request, "arguments": {"avatarPath": "World/Avatar", "includeAllProjectClips": True}}
    assert capture_admission_reason(broad, trusted_object_root=True) == "scope_capture_unbounded"
    assert capture_admission_reason({**request, "objectIds": ["model-root"]},
                                    trusted_object_root=True) == "scope_capture_unbounded"


@pytest.mark.parametrize(("invalid_reason", "expected_reason"), [
    ("scan_scope_changed", "scan_scope_changed"),
    ("capability_unavailable", "core_scope_unavailable"),
    ("unity_core_timeout", "core_scope_unavailable"),
])
def test_material_full_index_ref_requires_live_core_proof_and_never_rescans(
    tmp_path, monkeypatch, invalid_reason, expected_reason,
):
    editor_session = str(uuid.uuid4())
    proof = {
        "schema": scope_module.SCAN_SCOPE_SCHEMA,
        "verified": True,
        "token": "scope_" + uuid.uuid4().hex,
        "editorSession": editor_session,
        "assetPaths": [],
        "capturedAt": "2026-10-18T00:00:00Z",
        "dependencyCoverage": "complete",
    }
    state = {"valid": True, "reason": ""}

    class ScopeClient:
        def __init__(self, *_args, **_kwargs):
            self.capture_budget = None

        def capture(self, *_args, **_kwargs):
            return proof

        def validate(self, _token):
            return {
                "schema": scope_module.SCAN_SCOPE_SCHEMA,
                "valid": state["valid"],
                "reason": state["reason"],
                "changedAssetPaths": [],
                "editorSession": editor_session,
            }

    def forbidden_rescan(*_args, **_kwargs):
        raise AssertionError("A changed retained index must expire; it must not be replaced by a fresh page")

    monkeypatch.setattr(store_module, "AgentScanScopeClient", ScopeClient)
    import agent_scan_rescan
    monkeypatch.setattr(agent_scan_rescan, "direct_scan", forbidden_rescan)

    project = tmp_path / "UnityProject"
    project.mkdir()
    store = ScanResultStore(tmp_path / "store")
    arguments = {"avatarPath": "World/Avatar", "indexOnly": True, "limit": 25}
    start = store.start(project, "vrcforge_scan_materials", arguments)
    assert start["proof"] == proof
    step = {
        "index": 0,
        "actionId": uuid.uuid4().hex,
        "tool": "vrcforge_scan_materials",
        "status": "executed",
        "result": {"ok": True, "index": _material_rows(76)},
    }
    metadata = store.capture("owner-session", "turn-one", project, step, arguments,
                            expected_generation=start)
    assert metadata["scope"] == "validated_scope"
    reference = metadata["resultRef"]
    assert "vrcforge_scan_materials" not in agent_scan_rescan.WHITELIST
    assert agent_scan_rescan.eligible(step, arguments) is False

    reused = store.lookup("owner-session", "turn-two", project, reference)
    assert reused == {"step": step, "scopeValidated": True}

    state.update(valid=False, reason=invalid_reason)
    expired_same_turn = store.lookup("owner-session", "turn-one", project, reference)
    assert expired_same_turn["expired"] is True
    assert expired_same_turn["reason"] == expected_reason
    assert "step" not in expired_same_turn
