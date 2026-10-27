import copy
import hashlib
import json
import os
from pathlib import Path
from types import SimpleNamespace

import agent_gateway
import pytest

from execution_target import (
    EXECUTION_TARGET_SCHEMA,
    EXECUTION_TARGET_REF_SCHEMA,
    ExecutionTargetBindingRegistry,
    ExecutionTargetError,
    canonical_namespace,
    execution_target_digest,
    project_identity,
    process_start_time,
    standard_identity_metadata,
    validate_execution_target,
    validate_runtime_execution_target,
)
from agent_gateway import AgentGateway
from planner_structured_tool_evidence import project_structured_tool_evidence


def _assert_no_project_path(value, project_root):
    normalized = str(project_root).replace("\\", "/").casefold()
    def check(item):
        if isinstance(item, str):
            assert normalized not in item.replace("\\", "/").casefold()
        elif isinstance(item, dict):
            for key, child in item.items():
                check(str(key))
                check(child)
        elif isinstance(item, list):
            for child in item:
                check(child)
    check(value)


def test_external_write_result_reuses_digest_for_reference_projection(tmp_path, monkeypatch):
    gateway = AgentGateway(tmp_path / "config" / "gateway.json", tmp_path / "audit")
    name = "vrcforge_set_property"
    target = _target(tmp_path)
    reference = {"schema": EXECUTION_TARGET_REF_SCHEMA, "ref": "vrcforge-target-test"}
    gateway._write_handlers[name] = object()
    monkeypatch.setattr(gateway, "ensure_config", lambda: SimpleNamespace(enabled=True, allow_write_requests=True))
    monkeypatch.setattr(gateway, "_guard_external_mcp_project_scope", lambda *_args: None)
    monkeypatch.setattr(gateway, "_external_mcp_write_handler_block", lambda *_args: True)
    monkeypatch.setattr(gateway, "_validate_external_mcp_execution_target", lambda *_args, **_kwargs: target)
    monkeypatch.setattr(gateway, "shared_agent_tool_descriptor", lambda *_args, **_kwargs: {})
    monkeypatch.setattr(type(gateway.approval_transactions), "prepare_external_mcp_write",
                        lambda _self, _name, args: {"arguments": copy.deepcopy(args)})
    digest_calls = []
    real_digest = execution_target_digest

    def tracked_digest(value):
        digest_calls.append(value)
        return real_digest(value)

    def execute(*_args, **_kwargs):
        digest_calls.clear()  # Only count result projection, after preparation validation.
        return {"operationId": "fixture-operation"}

    monkeypatch.setattr(agent_gateway, "execution_target_digest", tracked_digest)
    monkeypatch.setattr(type(gateway.approval_transactions), "execute_prepared_external_mcp_write", execute)
    monkeypatch.setattr(gateway, "_external_mcp_write_result",
                        lambda *_args: {"outcome": {"executionTarget": copy.deepcopy(target)}})
    result = gateway.call_external_mcp_tool(name, {"executionTarget": reference})

    assert sum(value is target for value in digest_calls) == 1
    assert result["executionTargetDigest"] == real_digest(target)
    assert result["executionTarget"] == reference
    assert result["outcome"]["executionTarget"] == reference
    assert result["outcome"]["executionTargetDigest"] == real_digest(target)
    assert result["operationId"] == "fixture-operation"
    _assert_no_project_path(result, tmp_path)


def _target(tmp_path: Path, *, scope: str = "component") -> dict:
    root = str(tmp_path.resolve())
    target = {
        "schema": EXECUTION_TARGET_SCHEMA,
        "scope": scope,
        "project": {"root": root, "projectId": project_identity(root)},
        "editor": {"unityPid": 1234, "processStartTime": "2026-09-02T00:00:00Z", "coreInstanceId": "core-1"},
    }
    if scope in {"scene", "avatar", "object", "component"}:
        target["scene"] = {"absolutePath": str(tmp_path / "Assets" / "Main.unity"), "guid": "scene-guid", "revision": "7", "digest": "a" * 64}
    if scope in {"avatar", "object", "component"}:
        target["avatar"] = {"globalObjectId": "avatar-global-id", "exactHierarchyPath": "Root/Avatar"}
    if scope in {"object", "component"}:
        target["object"] = {"globalObjectId": "object-global-id", "exactHierarchyPath": "Root/Avatar/Body"}
    if scope == "component":
        target["component"] = {"globalObjectId": "component-global-id", "type": "SkinnedMeshRenderer"}
    target["namespace"] = canonical_namespace(target)
    return target


def _replace_scene_guid(target: dict) -> None:
    target["scene"]["guid"] = "replacement-scene"
    target["namespace"] = canonical_namespace(target)


def test_execution_target_accepts_exact_namespace_and_digest(tmp_path: Path):
    target = _target(tmp_path)
    validated = validate_execution_target(
        target,
        project_root=str(tmp_path),
        core_identity={"processId": 1234, "processStartTime": "2026-09-02T00:00:00Z", "instanceId": "core-1", "sceneRevision": "7"},
        required_scope="component",
    )
    assert validated["namespace"].startswith("vrcforge://projects/")
    assert execution_target_digest(validated)


@pytest.mark.parametrize(
    "mutator,code",
    [
        (lambda value: value["editor"].update({"unityPid": 9999}), "editor_identity_mismatch"),
        (_replace_scene_guid, "scene_identity_mismatch"),
        (lambda value: value["object"].pop("globalObjectId"), "identity_field_missing"),
        (lambda value: value["scene"].update({"absolutePath": "C:/Other/Other.unity"}), "identity_path_outside_project"),
    ],
)
def test_identity_drift_fails_closed(tmp_path: Path, mutator, code: str):
    target = _target(tmp_path)
    mutator(target)
    with pytest.raises(ExecutionTargetError) as exc_info:
        validate_execution_target(
            target,
            project_root=str(tmp_path),
            core_identity={"processId": 1234, "processStartTime": "2026-09-02T00:00:00Z", "instanceId": "core-1", "sceneGuid": "scene-guid", "sceneRevision": "7"},
            required_scope="component",
        )
    assert exc_info.value.code == code


def test_identity_metadata_declares_no_hierarchy_fallback():
    metadata = standard_identity_metadata(scope="object", write=True)
    assert metadata["hierarchyPathFallback"] is False
    assert "object.globalObjectId" in metadata["required"]


def test_standalone_object_target_has_no_fabricated_avatar_requirement(tmp_path: Path):
    target = _target(tmp_path, scope="object")
    target.pop("avatar")
    target["object"]["exactHierarchyPath"] = "GestureManager"
    target["namespace"] = canonical_namespace(target)
    validated = validate_execution_target(target, project_root=str(tmp_path), required_scope="object")
    assert "avatar" not in validated
    assert "/objects/object-global-id" in validated["namespace"]


def _live_runtime_target(tmp_path: Path) -> dict:
    assets = tmp_path / "Assets"
    assets.mkdir()
    scene = assets / "Main.unity"
    scene.write_bytes(b"%YAML 1.1\n--- !u!1 &1\n")
    scene.with_suffix(".unity.meta").write_text(
        "fileFormatVersion: 2\nguid: 0123456789abcdef0123456789abcdef\n",
        encoding="utf-8",
    )
    root = str(tmp_path.resolve())
    start = process_start_time(os.getpid())
    descriptor_dir = tmp_path / "Library" / "VRCForge"
    descriptor_dir.mkdir(parents=True)
    descriptor_dir.joinpath("mcp-core.json").write_text(
        json.dumps(
            {
                "projectPath": root,
                "projectId": project_identity(root),
                "processId": os.getpid(),
                "processStartTime": start,
                "instanceId": "core-live",
            }
        ),
        encoding="utf-8",
    )
    stat = scene.stat()
    target = {
        "schema": EXECUTION_TARGET_SCHEMA,
        "scope": "avatar",
        "project": {"root": root, "projectId": project_identity(root)},
        "editor": {
            "unityPid": os.getpid(),
            "processStartTime": start,
            "coreInstanceId": "core-live",
        },
        "scene": {
            "assetPath": "Assets/Main.unity",
            "absolutePath": str(scene.resolve()),
            "guid": "0123456789abcdef0123456789abcdef",
            "revision": str(621355968000000000 + stat.st_mtime_ns // 100),
            "digest": hashlib.sha256(scene.read_bytes()).hexdigest(),
        },
        "avatar": {
            "globalObjectId": "GlobalObjectId_V1-2-avatar",
            "exactHierarchyPath": "Root/Avatar",
            "name": "Avatar",
        },
        "ambiguous": False,
        "resolutionCandidateCount": 1,
    }
    target["namespace"] = canonical_namespace(target)
    return target


def test_runtime_target_rechecks_descriptor_and_reports_current_scene_state(tmp_path: Path):
    target = _live_runtime_target(tmp_path)
    assert validate_runtime_execution_target(
        target,
        project_root=str(tmp_path),
        required_scope="avatar",
    )["scene"]["digest"] == target["scene"]["digest"]

    stale = copy.deepcopy(target)
    stale["scene"]["digest"] = "f" * 64
    refreshed_state = validate_runtime_execution_target(
        stale, project_root=str(tmp_path), required_scope="avatar",
    )
    assert refreshed_state["scene"]["digest"] == target["scene"]["digest"]
    assert execution_target_digest(refreshed_state) == execution_target_digest(target)


def test_runtime_target_resolves_core_asset_path_when_absolute_path_is_omitted(tmp_path: Path):
    target = _live_runtime_target(tmp_path)
    target["scene"].pop("absolutePath")

    validated = validate_runtime_execution_target(
        target,
        project_root=str(tmp_path),
        required_scope="avatar",
    )

    assert validated["scene"]["absolutePath"] == str((tmp_path / "Assets" / "Main.unity").resolve())


def test_binding_refresh_allows_display_change_but_rejects_avatar_replacement(tmp_path: Path):
    target = _live_runtime_target(tmp_path)
    registry = ExecutionTargetBindingRegistry()
    bound = registry.bind(target, project_root=str(tmp_path), session_context="runtime:session-a")
    reference = bound["executionTarget"]
    assert reference["schema"] == EXECUTION_TARGET_REF_SCHEMA
    assert set(reference) == {"schema", "ref"}
    assert "executionTargetHandle" not in bound
    _assert_no_project_path(bound, tmp_path)

    repeated = registry.bind(target, project_root=str(tmp_path), session_context="runtime:session-a")
    assert repeated["executionTarget"] == reference
    isolated = registry.bind(target, project_root=str(tmp_path), session_context="runtime:session-b")
    assert isolated["executionTarget"] != reference
    other_project_root = tmp_path / "another-project"
    other_project_root.mkdir()
    other_project = registry.bind(
        _live_runtime_target(other_project_root),
        project_root=str(other_project_root),
        session_context="runtime:session-a",
    )
    assert other_project["executionTarget"] != reference

    resolved = registry.resolve(
        reference,
        project_root=str(tmp_path),
        session_context="runtime:session-a",
        required_scope="avatar",
    )
    assert execution_target_digest(resolved) == bound["executionTargetDigest"]
    with pytest.raises(ExecutionTargetError) as exc_info:
        registry.resolve(
            reference,
            project_root=str(tmp_path),
            session_context="runtime:session-b",
            required_scope="avatar",
        )
    assert exc_info.value.code == "execution_target_session_mismatch"
    with pytest.raises(ExecutionTargetError) as exc_info:
        registry.resolve(
            reference,
            project_root=str(tmp_path / "another-project"),
            session_context="runtime:session-a",
            required_scope="avatar",
        )
    assert exc_info.value.code == "project_root_drifted"

    display_only = copy.deepcopy(target)
    display_only["avatar"]["exactHierarchyPath"] = "Renamed/Avatar"
    display_only["avatar"]["name"] = "Avatar Renamed"
    refreshed = registry.refresh(
        reference,
        display_only,
        project_root=str(tmp_path),
        session_context="runtime:session-a",
    )
    assert refreshed["changed"] is False
    assert refreshed["executionTargetDigest"] == bound["executionTargetDigest"]
    assert refreshed["executionTarget"] == reference

    replaced = copy.deepcopy(target)
    replaced["avatar"]["globalObjectId"] = "GlobalObjectId_V1-2-replacement"
    replaced["namespace"] = canonical_namespace(replaced)
    with pytest.raises(ExecutionTargetError) as exc_info:
        registry.refresh(
            reference,
            replaced,
            project_root=str(tmp_path),
            session_context="runtime:session-a",
        )
    assert exc_info.value.code == "execution_target_identity_drifted"


def test_public_reference_survives_scene_state_change_and_ambiguous_capture(tmp_path: Path):
    target = _live_runtime_target(tmp_path)
    registry = ExecutionTargetBindingRegistry()
    bound = registry.bind(target, project_root=str(tmp_path), session_context="runtime:session-a")
    scene = Path(target["scene"]["absolutePath"])
    old_stat = scene.stat()
    scene.write_bytes(scene.read_bytes() + b"# changed scene bytes after binding\n")
    os.utime(scene, ns=(old_stat.st_atime_ns, old_stat.st_mtime_ns + 10_000_000))
    resolved = registry.resolve(
        bound["executionTarget"],
        project_root=str(tmp_path),
        session_context="runtime:session-a",
        required_scope="avatar",
    )
    assert resolved["scene"]["revision"] != target["scene"]["revision"]
    assert resolved["scene"]["digest"] != target["scene"]["digest"]
    assert execution_target_digest(resolved) == bound["executionTargetDigest"]
    refreshed = registry.refresh(
        bound["executionTarget"],
        target,
        project_root=str(tmp_path),
        session_context="runtime:session-a",
    )
    assert refreshed["changed"] is False
    assert refreshed["executionTargetDigest"] == bound["executionTargetDigest"]

    replacement = copy.deepcopy(target)
    replacement["scene"]["guid"] = "replacement-scene-guid"
    replacement["namespace"] = canonical_namespace(replacement)
    with pytest.raises(ExecutionTargetError) as exc_info:
        validate_runtime_execution_target(
            replacement,
            project_root=str(tmp_path),
            required_scope="avatar",
        )
    assert exc_info.value.code == "scene_identity_mismatch"

    ambiguous_root = tmp_path / "ambiguous"
    ambiguous_root.mkdir()
    ambiguous = copy.deepcopy(_live_runtime_target(ambiguous_root))
    ambiguous["ambiguous"] = True
    ambiguous["resolutionCandidateCount"] = 2
    ambiguous_registry = ExecutionTargetBindingRegistry()
    with pytest.raises(ExecutionTargetError) as exc_info:
        ambiguous_registry.bind(
            ambiguous,
            project_root=ambiguous["project"]["root"],
            session_context="runtime:session-a",
        )
    assert exc_info.value.code == "identity_ambiguous"


def test_public_reference_rejects_core_instance_drift(tmp_path: Path):
    target = _live_runtime_target(tmp_path)
    registry = ExecutionTargetBindingRegistry()
    bound = registry.bind(target, project_root=str(tmp_path), session_context="runtime:session-a")
    descriptor_path = tmp_path / "Library" / "VRCForge" / "mcp-core.json"
    descriptor = json.loads(descriptor_path.read_text(encoding="utf-8"))
    descriptor["instanceId"] = "replacement-core"
    descriptor_path.write_text(json.dumps(descriptor), encoding="utf-8")

    with pytest.raises(ExecutionTargetError) as exc_info:
        registry.resolve(
            bound["executionTarget"],
            project_root=str(tmp_path),
            session_context="runtime:session-a",
            required_scope="avatar",
        )
    assert exc_info.value.code == "editor_identity_mismatch"


def test_runtime_target_rejects_active_scene_path_drift(tmp_path: Path):
    target = _live_runtime_target(tmp_path)
    other_scene = tmp_path / "Assets" / "Other.unity"
    other_scene.write_bytes(b"%YAML 1.1\n--- !u!1 &2\n")
    other_scene.with_suffix(".unity.meta").write_text(
        "fileFormatVersion: 2\nguid: 0123456789abcdef0123456789abcdef\n",
        encoding="utf-8",
    )
    core = {
        "processId": os.getpid(),
        "processStartTime": target["editor"]["processStartTime"],
        "instanceId": target["editor"]["coreInstanceId"],
        "scenePath": str(other_scene.resolve()),
        "sceneGuid": target["scene"]["guid"],
    }
    with pytest.raises(ExecutionTargetError) as exc_info:
        validate_execution_target(
            target,
            project_root=str(tmp_path),
            core_identity=core,
            required_scope="avatar",
        )
    assert exc_info.value.code == "scene_identity_mismatch"


def test_public_reference_keeps_paths_private_through_external_read_and_resource(tmp_path: Path, monkeypatch):
    target = _live_runtime_target(tmp_path)
    gateway = AgentGateway(tmp_path / "config" / "gateway.json", tmp_path / "audit")
    handler_arguments = []
    gateway.register_tool(
        "vrcforge_scan_animator",
        "Read an identity-bound Animator fixture.",
        "unity",
        lambda arguments: handler_arguments.append(copy.deepcopy(arguments))
        or {"ok": True, "result": {"executionTarget": copy.deepcopy(arguments["executionTarget"])}},
    )
    monkeypatch.setattr(gateway, "ensure_config", lambda: SimpleNamespace(enabled=True))
    monkeypatch.setattr(gateway, "_external_mcp_read_tool_visible", lambda *_args: True)

    with gateway.resource_context("external:session-a"):
        bound = gateway.bind_execution_target(target, project_root=str(tmp_path))
        reference = bound["executionTarget"]
        result = gateway.call_external_mcp_tool(
            "vrcforge_scan_animator",
            {"projectPath": str(tmp_path), "executionTarget": reference},
        )
        assert handler_arguments[0]["executionTarget"]["project"]["root"] == str(tmp_path)
        assert result["executionTarget"] == reference
        assert result["result"]["result"]["executionTarget"] == reference
        projected = project_structured_tool_evidence(
            {"executionTarget": reference},
            sanitize_text=lambda value, limit=None: str(value) if limit is None else str(value)[:limit],
        )
        assert projected["data"]["executionTarget"] == reference
        _assert_no_project_path(bound, tmp_path)
        _assert_no_project_path(result, tmp_path)
        gateway.publish_mcp_tool_result_resource(
            "vrcforge_scan_animator",
            {"projectPath": str(tmp_path), "executionTarget": reference},
            result,
            source_mode="test",
        )
        _assert_no_project_path(result, tmp_path)
        identity_uri = result["resources"]["identityLockUri"]
        identity_resource = gateway._validate_mcp_prompt_resource(
            identity_uri,
            "session_identity_lock",
        )
        assert identity_resource["data"]["exactExecutionTarget"] == reference
        assert identity_resource["identity"] == {
            "executionTarget": reference,
            "executionTargetDigest": bound["executionTargetDigest"],
        }
        _assert_no_project_path(identity_resource, tmp_path)


@pytest.mark.parametrize(
    ("capture_group", "scope"),
    [("live-material", "avatar"), ("live-fx", "project")],
)
def test_material_and_fx_receipt_identity_shapes_bind_to_path_free_refs(
    tmp_path: Path,
    capture_group: str,
    scope: str,
):
    fixture_path = Path(__file__).parent / "fixtures" / "execution_target_receipt_shapes.json"
    captured = json.loads(fixture_path.read_text(encoding="utf-8"))[capture_group]
    assert captured["scope"] == scope
    live = _live_runtime_target(tmp_path)
    captured["project"] = copy.deepcopy(live["project"])
    captured["editor"] = copy.deepcopy(live["editor"])
    captured["scope"] = scope
    if scope == "avatar":
        captured["scene"] = copy.deepcopy(live["scene"])
    else:
        for section in ("scene", "avatar", "object", "component"):
            captured.pop(section, None)
    captured["namespace"] = canonical_namespace(captured)

    bound = ExecutionTargetBindingRegistry().bind(
        captured,
        project_root=str(tmp_path),
        session_context="runtime:captured-fixture-session",
    )
    assert bound["executionTarget"]["schema"] == EXECUTION_TARGET_REF_SCHEMA
    assert set(bound["executionTarget"]) == {"schema", "ref"}
    assert "executionTargetHandle" not in bound
    _assert_no_project_path(bound, tmp_path)


def test_runtime_target_rejects_descriptor_from_another_project(tmp_path: Path):
    target = _live_runtime_target(tmp_path)
    descriptor_path = tmp_path / "Library" / "VRCForge" / "mcp-core.json"
    descriptor = json.loads(descriptor_path.read_text(encoding="utf-8"))
    descriptor["projectPath"] = str((tmp_path / "Other").resolve())
    descriptor["projectId"] = project_identity(descriptor["projectPath"])
    descriptor_path.write_text(json.dumps(descriptor), encoding="utf-8")
    with pytest.raises(ExecutionTargetError) as exc_info:
        validate_runtime_execution_target(target, project_root=str(tmp_path), required_scope="avatar")
    assert exc_info.value.code == "project_root_drifted"


@pytest.mark.parametrize("refresh", [False, True])
def test_runtime_identity_validation_reads_scene_only_for_requested_snapshot(tmp_path, monkeypatch, refresh):
    target = _live_runtime_target(tmp_path)
    scene = Path(target["scene"]["absolutePath"])
    scene.write_bytes(b"changed scene contents")
    original_read = Path.read_bytes
    reads = []
    def read_bytes(path):
        if path == scene:
            reads.append(path)
        return original_read(path)
    monkeypatch.setattr(Path, "read_bytes", read_bytes)
    verified = validate_runtime_execution_target(
        target, project_root=str(tmp_path), required_scope="avatar", refresh_scene_snapshot=refresh)
    assert len(reads) == int(refresh)
    assert execution_target_digest(verified) == execution_target_digest(target)
    assert verified["scene"]["digest"] == (hashlib.sha256(b"changed scene contents").hexdigest() if refresh else target["scene"]["digest"])


@pytest.mark.parametrize("drift", ["scene_missing", "guid_changed", "process_changed"])
def test_runtime_identity_only_retains_disk_and_lifetime_validation(tmp_path, drift):
    target = _live_runtime_target(tmp_path)
    scene = Path(target["scene"]["absolutePath"])
    if drift == "scene_missing":
        scene.unlink()
    elif drift == "guid_changed":
        Path(str(scene) + ".meta").write_text("guid: replacement-guid", encoding="utf-8")
    else:
        target["editor"]["processStartTime"] = "1.000000"
    with pytest.raises(ExecutionTargetError):
        validate_runtime_execution_target(target, project_root=str(tmp_path), required_scope="avatar", refresh_scene_snapshot=False)
