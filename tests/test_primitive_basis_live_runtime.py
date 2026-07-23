from __future__ import annotations

import hashlib
import json
import shutil
import socket
from pathlib import Path

import pytest

from agent_gateway import AgentGateway
import primitive_basis_live_attestation as live
import primitive_basis_live_runtime as runtime


TEMPLATE_ROOT = (
    Path(__file__).parent
    / "fixtures"
    / "primitive_basis"
    / "projects"
    / "model_part_composition"
)


def digest(value: bytes) -> str:
    return hashlib.sha256(value).hexdigest()


def make_bootstrap(project_root: Path) -> live.LiveBootstrap:
    descriptor_root = project_root / runtime.FIXTURE_DESCRIPTOR_DIRECTORY
    fixtures = __import__("primitive_basis_matrix").load_fixture_set(
        descriptor_root,
        repository_root=project_root,
    )
    fixture = next(
        item for item in fixtures.fixtures if item.scenario_id == runtime.MODEL_SCENARIO_ID
    )
    return live.LiveBootstrap(
        key=b"k" * 32,
        challenge=b"c" * 32,
        runtime_binding_digest=digest(b"runtime"),
        desktop_executable_digest=digest(b"desktop"),
        backend_executable_digest=digest(b"backend"),
        runner_digest=digest(b"runner"),
        unity_package_digest=digest(b"unity-package"),
        unity_editor_digest=digest(b"unity-editor"),
        fixture_project_input_digest=runtime.compute_fixed_project_input_digest(project_root),
        fixture_set_descriptor_digest=fixtures.descriptor_digest,
        fixture_descriptor_digest=fixture.descriptor_digest,
    )


class FakeRuntime:
    def __init__(self, project_root: Path, bootstrap: live.LiveBootstrap) -> None:
        self.project_root = project_root
        self.bootstrap = bootstrap
        self.scene_path = project_root / runtime.FIXTURE_SCENE
        self.original_scene = self.scene_path.read_bytes()
        self.component_present = False
        self.persisted_component_present = False
        self.reload_count = 0
        self.apply_arguments: dict[str, object] = {}
        self.restore_arguments: dict[str, object] = {}
        self.apply_approval_id = "approval-apply"
        self.restore_approval_id = "approval-restore"
        self.checkpoint_id = "checkpoint-apply"
        self.connection_digest = digest(b"connection")
        self.unity_process_started_at = "2026-07-23T00:00:00.0000000Z"

    def callbacks(self) -> runtime.LiveRuntimeCallbacks:
        return runtime.LiveRuntimeCallbacks(
            bind_connection=self.bind_connection,
            validate_connection=self.validate_connection,
            inspect_fixture=self.inspect_fixture,
            reload_fixture=self.reload_fixture,
            inspect_component=self.inspect_component,
            preview_component=self.preview_component,
            create_apply_request=self.create_apply_request,
            read_compile_status=self.read_compile_status,
            create_restore_request=self.create_restore_request,
            preview_checkpoint=lambda _checkpoint_id: {"ok": True, "changedFiles": []},
        )

    def bind_connection(self, params: dict[str, object]) -> dict[str, object]:
        assert Path(str(params["projectPath"])).resolve() == self.project_root.resolve()
        return self.connection_payload()

    def validate_connection(self, params: dict[str, object]) -> dict[str, object]:
        requested = str(params.get("connectionBindingDigest") or "")
        assert requested in {"", self.connection_digest}
        return self.connection_payload()

    def connection_payload(self) -> dict[str, object]:
        return {
            "ok": True,
            "schema": "vrcforge.primitive_basis_connection_binding.v1",
            "frozen": True,
            "projectPathDigest": runtime._hash_text(
                runtime._normalize_project_root(self.project_root)
            ),
            "connectionBindingDigest": self.connection_digest,
        }

    def unity_identity(self) -> dict[str, object]:
        return {
            "projectPathDigest": runtime._hash_text(
                runtime._normalize_project_root(self.project_root)
            ),
            "unityProcessId": 2_000_000_000,
            "unityProcessStartedAtUtc": self.unity_process_started_at,
            "unityExecutableDigest": self.bootstrap.unity_editor_digest,
        }

    def inspect_fixture(self, _params: dict[str, object]) -> dict[str, object]:
        return {
            "ok": True,
            "schema": "vrcforge.primitive_basis_unity_fixture.v1",
            "scenarioId": runtime.MODEL_SCENARIO_ID,
            "primitiveId": runtime.MODEL_PRIMITIVE_ID,
            **self.unity_identity(),
            "unityVersion": runtime.EXPECTED_UNITY_VERSION,
            "batchMode": False,
            "sceneDirty": False,
            "activeScenePath": runtime.FIXTURE_SCENE.as_posix(),
            "activeSceneGuid": runtime.EXPECTED_SCENE_GUID,
            "readyMarkerDigest": runtime._stable_file_digest(
                self.project_root / runtime.FIXTURE_READY_MARKER
            ),
            "readyRunIdDigest": self.bootstrap.challenge_digest,
            "contractDigest": runtime.EXPECTED_CONTRACT_DIGEST,
            "baselineManifestDigest": runtime.EXPECTED_BASELINE_FILE_DIGEST,
            "sceneDigest": runtime.EXPECTED_SCENE_DIGEST,
            "avatarRootType": runtime.EXPECTED_AVATAR_ROOT_TYPE,
            "transformPaths": list(runtime.EXPECTED_TRANSFORM_PATHS),
            "rendererPath": runtime.EXPECTED_RENDERER_PATH,
            "rendererRootBonePath": runtime.EXPECTED_RENDERER_BONE,
            "rendererBonePaths": [runtime.EXPECTED_RENDERER_BONE],
            "componentHostPath": runtime.EXPECTED_COMPONENT_HOST,
            "mergeTargetPath": runtime.EXPECTED_MERGE_TARGET,
        }

    def reload_fixture(self, _params: dict[str, object]) -> dict[str, object]:
        self.reload_count += 1
        self.component_present = self.persisted_component_present
        return {
            "ok": True,
            "schema": "vrcforge.primitive_basis_scene_reload.v1",
            "reloaded": True,
            "sceneDirty": False,
            "scenePath": runtime.FIXTURE_SCENE.as_posix(),
            "unityProcessId": 2_000_000_000,
            "unityProcessStartedAtUtc": self.unity_process_started_at,
            "unityExecutableDigest": self.bootstrap.unity_editor_digest,
            "projectPathDigest": runtime._hash_text(
                runtime._normalize_project_root(self.project_root)
            ),
        }

    def inspect_component(self, _params: dict[str, object]) -> dict[str, object]:
        references: list[dict[str, object]] = []
        if self.component_present:
            references.append(
                {
                    "componentIndex": 0,
                    "member": "mergeTarget",
                    "referencePath": "Armature",
                    "resolved": True,
                    "resolvedPath": runtime.EXPECTED_MERGE_TARGET,
                }
            )
        return {
            "ok": True,
            "gameObjectPath": runtime.EXPECTED_COMPONENT_HOST,
            "avatarPath": runtime.EXPECTED_AVATAR_ROOT,
            "present": self.component_present,
            "count": 1 if self.component_present else 0,
            "type": runtime.EXPECTED_COMPONENT_TYPE if self.component_present else None,
            "sceneDirty": False,
            "references": references,
        }

    def preview_component(self, _params: dict[str, object]) -> dict[str, object]:
        return {
            "ok": True,
            "preview": True,
            "gameObjectPath": runtime.EXPECTED_COMPONENT_HOST,
            "avatarPath": runtime.EXPECTED_AVATAR_ROOT,
            "componentType": runtime.EXPECTED_COMPONENT_TYPE,
            "existingCount": 0,
            "saveScene": True,
            "sceneSaved": False,
            "sceneDirty": False,
            "references": [{"field": "mergeTarget", "resolved": True}],
            "fields": [],
            "warnings": [],
        }

    def create_apply_request(self, params: dict[str, object]) -> dict[str, object]:
        self.apply_arguments = dict(params["arguments"])
        return {
            "ok": True,
            "status": "pending",
            "approval": {
                "id": self.apply_approval_id,
                "status": "pending",
                "targetTool": live.MODEL_TARGET_TOOL,
                "arguments": self.apply_arguments,
                "argumentsDigest": runtime._hash_json(self.apply_arguments),
            },
        }

    def read_compile_status(self, _params: dict[str, object]) -> dict[str, object]:
        return {
            "ok": True,
            "exitCode": 0,
            "errorCount": 0,
            "hasErrors": False,
            "isCompiling": False,
            "source": "unity_console",
            "capturedAt": "2026-07-23T00:00:30Z",
            "projectPathDigest": runtime._hash_text(
                runtime._normalize_project_root(self.project_root)
            ),
            "unityProcessId": 2_000_000_000,
            "unityProcessStartedAtUtc": self.unity_process_started_at,
            "unityExecutableDigest": self.bootstrap.unity_editor_digest,
        }

    def create_restore_request(self, checkpoint_id: str) -> dict[str, object]:
        self.restore_arguments = {
            "checkpointId": checkpoint_id,
            "confirmRestore": True,
            "projectRoot": str(self.project_root),
        }
        return {
            "ok": True,
            "status": "pending",
            "approval": {
                "id": self.restore_approval_id,
                "status": "pending",
                "targetTool": live.RESTORE_TARGET_TOOL,
                "arguments": self.restore_arguments,
                "argumentsDigest": runtime._hash_json(self.restore_arguments),
            },
        }

    def apply_payload(self) -> dict[str, object]:
        self.persisted_component_present = True
        self.scene_path.write_bytes(self.original_scene + b"\n# applied\n")
        approval = {
            "id": self.apply_approval_id,
            "status": "applied",
            "targetTool": live.MODEL_TARGET_TOOL,
            "arguments": self.apply_arguments,
            "approvedAt": "2026-07-23T00:00:00Z",
        }
        return {
            "ok": True,
            "approval": approval,
            "execution": {
                "ok": True,
                "status": "applied",
                "approval": approval,
                "checkpoint": {
                    "ok": True,
                    "id": self.checkpoint_id,
                    "status": "ready",
                    "strategy": "snapshot",
                    "unityPrepare": {"ok": True, **self.unity_identity()},
                },
                "result": {
                    "ok": True,
                    "action": "add_modular_avatar_component",
                    "gameObjectPath": runtime.EXPECTED_COMPONENT_HOST,
                    "componentType": runtime.EXPECTED_COMPONENT_TYPE,
                    "addedComponent": True,
                    "sceneSaved": True,
                    "sceneDirty": False,
                },
            },
        }

    def restore_payload(self) -> dict[str, object]:
        self.persisted_component_present = False
        self.scene_path.write_bytes(self.original_scene)
        approval = {
            "id": self.restore_approval_id,
            "status": "applied",
            "targetTool": live.RESTORE_TARGET_TOOL,
            "arguments": self.restore_arguments,
            "approvedAt": "2026-07-23T00:01:00Z",
        }
        return {
            "ok": True,
            "approval": approval,
            "execution": {
                "ok": True,
                "status": "applied",
                "approval": approval,
                "result": {
                    "ok": True,
                    "checkpointId": self.checkpoint_id,
                    "restored": True,
                    "unityReload": {"ok": True, **self.unity_identity()},
                },
            },
        }


def make_runtime(tmp_path: Path) -> tuple[Path, live.LiveBootstrap, FakeRuntime, runtime.ModelPartCompositionLiveRuntime]:
    project_root = tmp_path / "disposable-project"
    shutil.copytree(TEMPLATE_ROOT, project_root)
    editor_root = project_root / runtime.FIXED_EDITOR_TOOL_TREE
    editor_root.mkdir(parents=True)
    (editor_root / "fixed-live-tool.cs").write_text(
        "// fixed disposable tool tree\n", encoding="utf-8"
    )
    (project_root / runtime.FIXED_EDITOR_TOOL_TREE_META).write_text(
        "fileFormatVersion: 2\nguid: 11111111111111111111111111111111\n",
        encoding="utf-8",
    )
    for package_id, version in runtime._REQUIRED_PACKAGE_VERSIONS.items():
        package_root = project_root / "Packages" / package_id
        package_root.mkdir(parents=True)
        (package_root / "package.json").write_text(
            json.dumps({"name": package_id, "version": version}, separators=(",", ":")),
            encoding="utf-8",
        )
    bootstrap = make_bootstrap(project_root)
    marker_path = project_root / runtime.FIXTURE_READY_MARKER
    marker_path.parent.mkdir(parents=True)
    marker_path.write_text(
        json.dumps(
            {
                "schema": "vrcforge.primitive_basis_fixture_ready.v1",
                "runIdDigest": bootstrap.challenge_digest,
                "sceneGuid": runtime.EXPECTED_SCENE_GUID,
                "avatarPath": runtime.EXPECTED_AVATAR_ROOT,
                "componentHostPath": runtime.EXPECTED_COMPONENT_HOST,
                "mergeTargetPath": runtime.EXPECTED_MERGE_TARGET,
            },
            separators=(",", ":"),
        )
        + "\n",
        encoding="utf-8",
    )
    fake = FakeRuntime(project_root, bootstrap)
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as port_probe:
        if hasattr(socket, "SO_EXCLUSIVEADDRUSE"):
            port_probe.setsockopt(socket.SOL_SOCKET, socket.SO_EXCLUSIVEADDRUSE, 1)
        port_probe.bind(("127.0.0.1", 0))
        bridge_port = int(port_probe.getsockname()[1])
    coordinator = runtime.ModelPartCompositionLiveRuntime(
        live.PrimitiveBasisLiveSession(bootstrap),
        fake.callbacks(),
        bridge_port=bridge_port,
    )
    return project_root, bootstrap, fake, coordinator


def begin_apply_lifecycle(
    fake: FakeRuntime, coordinator: runtime.ModelPartCompositionLiveRuntime
) -> tuple[dict[str, object], dict[str, object]]:
    completed = fake.apply_payload()
    approval = dict(completed["approval"])
    approval["status"] = "applying"
    assert coordinator.observe_apply_lifecycle(
        "approval_started", {"approval": approval}
    ) is True
    assert coordinator.observe_apply_lifecycle(
        "checkpoint_created",
        {
            "approval": approval,
            "checkpoint": completed["execution"]["checkpoint"],
        },
    ) is True
    return completed, approval


def observe_apply_lifecycle(
    fake: FakeRuntime, coordinator: runtime.ModelPartCompositionLiveRuntime
) -> None:
    completed, approval = begin_apply_lifecycle(fake, coordinator)
    assert coordinator.observe_apply_lifecycle(
        "handler_starting",
        {
            "approval": approval,
            "checkpoint": completed["execution"]["checkpoint"],
            "argumentsDigest": runtime._hash_json(fake.apply_arguments),
        },
    ) is True
    assert coordinator.observe_apply_lifecycle(
        "handler_returned",
        {
            "approval": approval,
            "result": completed["execution"]["result"],
        },
    ) is True


def observe_restore_lifecycle(
    fake: FakeRuntime, coordinator: runtime.ModelPartCompositionLiveRuntime
) -> None:
    completed = fake.restore_payload()
    approval = dict(completed["approval"])
    approval["status"] = "applying"
    assert coordinator.observe_apply_lifecycle(
        "approval_started", {"approval": approval}
    ) is True
    assert coordinator.observe_apply_lifecycle(
        "handler_starting",
        {
            "approval": approval,
            "argumentsDigest": runtime._hash_json(fake.restore_arguments),
        },
    ) is True
    assert coordinator.observe_apply_lifecycle(
        "handler_returned",
        {
            "approval": approval,
            "result": completed["execution"]["result"],
        },
    ) is True


def test_fixed_runtime_binds_apply_readback_restore_and_cleanup(tmp_path: Path) -> None:
    project_root, bootstrap, fake, coordinator = make_runtime(tmp_path)
    started = coordinator.start(str(project_root))
    assert started["approvalId"] == fake.apply_approval_id
    observe_apply_lifecycle(fake, coordinator)

    restore = coordinator.readback_and_request_restore()
    assert restore["approvalId"] == fake.restore_approval_id
    observe_restore_lifecycle(fake, coordinator)
    cleanup = coordinator.prepare_cleanup()
    assert cleanup["deleteOnlyAfterUnityExit"] is True
    assert fake.reload_count >= 2

    fixtures = coordinator.fixtures
    project_binding = coordinator.project_binding_digest
    shutil.rmtree(project_root)
    finalization = coordinator.finalize_after_cleanup()
    verified = live.verify_live_finalization(
        finalization,
        bootstrap=bootstrap,
        fixture_digest=next(
            item.digest
            for item in fixtures.fixtures
            if item.scenario_id == runtime.MODEL_SCENARIO_ID
        ),
        project_binding_digest=project_binding,
    )
    report = live.build_live_matrix_report(fixtures, verified)
    assert report["ok"] is False
    assert report["transcriptOk"] is True
    assert report["targetOk"] is False
    assert report["runtimeBinding"]["liveRunnerAttested"] is False
    assert report["runtimeBinding"]["transcriptMacVerified"] is True
    assert report["summary"]["fullRowCount"] == 0
    assert report["summary"]["blockedRowCount"] == 6


def test_real_gateway_apply_and_restore_satisfy_live_lifecycle_contract(
    tmp_path: Path,
) -> None:
    project_root, bootstrap, fake, _coordinator = make_runtime(tmp_path)
    gateway = AgentGateway(tmp_path / "config" / "gateway.json", tmp_path / "audit")
    gateway.checkpoint_prepare_handler = lambda _path: {
        "ok": True,
        **fake.unity_identity(),
    }

    def apply_component(arguments: dict[str, object]) -> dict[str, object]:
        assert arguments["projectPath"] == str(project_root)
        fake.persisted_component_present = True
        fake.component_present = True
        fake.scene_path.write_bytes(fake.original_scene + b"\n# applied\n")
        return {
            "ok": True,
            "action": "add_modular_avatar_component",
            "gameObjectPath": runtime.EXPECTED_COMPONENT_HOST,
            "componentType": runtime.EXPECTED_COMPONENT_TYPE,
            "addedComponent": True,
            "sceneSaved": True,
            "sceneDirty": False,
        }

    gateway.register_write_handler(
        live.MODEL_TARGET_TOOL,
        "Apply the fixed test component.",
        "high",
        apply_component,
    )
    gateway.register_write_handler(
        live.RESTORE_TARGET_TOOL,
        "Restore the fixed test checkpoint.",
        "high",
        lambda params: gateway.restore_checkpoint(params or {}),
    )

    def create_real_apply_request(
        params: dict[str, object],
    ) -> dict[str, object]:
        return gateway.create_apply_request(
            params,
            include_arguments_digest=True,
        )

    def create_real_restore_request(checkpoint_id: str) -> dict[str, object]:
        return gateway.create_apply_request(
            {
                "target_tool": live.RESTORE_TARGET_TOOL,
                "arguments": {
                    "checkpointId": checkpoint_id,
                    "confirmRestore": True,
                    "projectRoot": str(project_root),
                },
            },
            include_arguments_digest=True,
        )

    callbacks = runtime.LiveRuntimeCallbacks(
        **{
            **fake.callbacks().__dict__,
            "create_apply_request": create_real_apply_request,
            "create_restore_request": create_real_restore_request,
        }
    )
    coordinator = runtime.ModelPartCompositionLiveRuntime(
        live.PrimitiveBasisLiveSession(bootstrap),
        callbacks,
        bridge_port=65_534,
    )
    def reload_restored_fixture(path: Path) -> dict[str, object]:
        assert path.resolve() == project_root.resolve()
        fake.persisted_component_present = False
        return fake.reload_fixture({})

    gateway.checkpoint_restore_handler = reload_restored_fixture
    gateway.apply_lifecycle_observer_fn = coordinator.observe_apply_lifecycle

    apply_request = coordinator.start(str(project_root))
    apply_approval_id = str(apply_request["approvalId"])
    gateway.approve(apply_approval_id)
    apply_execution = gateway.apply_approved({"approval_id": apply_approval_id})

    assert apply_execution["ok"] is True
    assert apply_execution["checkpoint"]["ok"] is True
    fake.checkpoint_id = str(apply_execution["checkpoint"]["id"])
    restore_request = coordinator.readback_and_request_restore()
    restore_approval_id = str(restore_request["approvalId"])
    gateway.approve(restore_approval_id)
    restore_execution = gateway.apply_approved({"approval_id": restore_approval_id})

    assert restore_execution["ok"] is True
    assert restore_execution["result"]["restored"] is True
    assert restore_execution["result"]["checkpointId"] == fake.checkpoint_id
    assert restore_execution["result"]["unityReload"]["ok"] is True
    assert coordinator.prepare_cleanup()["deleteOnlyAfterUnityExit"] is True


def test_start_rejects_copied_descriptor_drift(tmp_path: Path) -> None:
    project_root, _bootstrap, _fake, coordinator = make_runtime(tmp_path)
    descriptor = (
        project_root
        / runtime.FIXTURE_DESCRIPTOR_DIRECTORY
        / "model_part_composition.json"
    )
    payload = json.loads(descriptor.read_text(encoding="utf-8"))
    payload["requiredPrimitives"] = ["changed_primitive"]
    descriptor.write_text(json.dumps(payload), encoding="utf-8")

    with pytest.raises(runtime.PrimitiveBasisLiveRuntimeError, match="fixture"):
        coordinator.start(str(project_root))


def test_start_rejects_dependency_tree_drift(tmp_path: Path) -> None:
    project_root, _bootstrap, _fake, coordinator = make_runtime(tmp_path)
    drift = project_root / "Packages" / "nadena.dev.ndmf" / "unexpected.txt"
    drift.write_text("changed", encoding="utf-8")

    with pytest.raises(runtime.PrimitiveBasisLiveRuntimeError, match="input binding"):
        coordinator.start(str(project_root))


@pytest.mark.parametrize(
    "relative_path",
    (
        Path("Packages/nadena.dev.ndmf/transient-source.cs"),
        runtime.FIXED_EDITOR_TOOL_TREE / "transient-tool.cs",
    ),
)
def test_handler_start_rejects_fixed_code_tree_drift(
    tmp_path: Path, relative_path: Path
) -> None:
    project_root, _bootstrap, fake, coordinator = make_runtime(tmp_path)
    coordinator.start(str(project_root))
    completed, approval = begin_apply_lifecycle(fake, coordinator)
    drift = project_root / relative_path
    drift.write_text("// changed\n", encoding="utf-8")

    with pytest.raises(runtime.PrimitiveBasisLiveRuntimeError, match="input binding"):
        coordinator.observe_apply_lifecycle(
            "handler_starting",
            {
                "approval": approval,
                "checkpoint": completed["execution"]["checkpoint"],
                "argumentsDigest": runtime._hash_json(fake.apply_arguments),
            },
        )


def test_handler_result_rejects_fixed_code_tree_drift(tmp_path: Path) -> None:
    project_root, _bootstrap, fake, coordinator = make_runtime(tmp_path)
    coordinator.start(str(project_root))
    completed, approval = begin_apply_lifecycle(fake, coordinator)
    assert coordinator.observe_apply_lifecycle(
        "handler_starting",
        {
            "approval": approval,
            "checkpoint": completed["execution"]["checkpoint"],
            "argumentsDigest": runtime._hash_json(fake.apply_arguments),
        },
    ) is True
    drift = project_root / "Packages" / "nadena.dev.ndmf" / "post-write-source.cs"
    drift.write_text("// changed\n", encoding="utf-8")

    with pytest.raises(runtime.PrimitiveBasisLiveRuntimeError, match="input binding"):
        coordinator.observe_apply_lifecycle(
            "handler_returned",
            {
                "approval": approval,
                "result": completed["execution"]["result"],
            },
        )


def test_start_rejects_dirty_scene_identity(tmp_path: Path) -> None:
    project_root, bootstrap, fake, _coordinator = make_runtime(tmp_path)

    def dirty_fixture(params: dict[str, object]) -> dict[str, object]:
        payload = fake.inspect_fixture(params)
        payload["sceneDirty"] = True
        return payload

    coordinator = runtime.ModelPartCompositionLiveRuntime(
        live.PrimitiveBasisLiveSession(bootstrap),
        runtime.LiveRuntimeCallbacks(
            **{**fake.callbacks().__dict__, "inspect_fixture": dirty_fixture}
        ),
        bridge_port=65_534,
    )
    with pytest.raises(runtime.PrimitiveBasisLiveRuntimeError, match="identity"):
        coordinator.start(str(project_root))


def test_connection_switch_is_rejected_before_component_read(tmp_path: Path) -> None:
    project_root, bootstrap, fake, _coordinator = make_runtime(tmp_path)
    validation_count = 0

    def changed_connection(params: dict[str, object]) -> dict[str, object]:
        nonlocal validation_count
        validation_count += 1
        payload = fake.validate_connection(params)
        if validation_count > 1:
            payload["connectionBindingDigest"] = digest(b"other-connection")
        return payload

    coordinator = runtime.ModelPartCompositionLiveRuntime(
        live.PrimitiveBasisLiveSession(bootstrap),
        runtime.LiveRuntimeCallbacks(
            **{**fake.callbacks().__dict__, "validate_connection": changed_connection}
        ),
        bridge_port=65_534,
    )
    with pytest.raises(runtime.PrimitiveBasisLiveRuntimeError, match="connection changed"):
        coordinator.start(str(project_root))


def test_compile_status_rejects_replacement_unity_process(tmp_path: Path) -> None:
    project_root, bootstrap, fake, _coordinator = make_runtime(tmp_path)

    def replaced_process(params: dict[str, object]) -> dict[str, object]:
        payload = fake.read_compile_status(params)
        payload["unityProcessId"] = 1_999_999_999
        return payload

    coordinator = runtime.ModelPartCompositionLiveRuntime(
        live.PrimitiveBasisLiveSession(bootstrap),
        runtime.LiveRuntimeCallbacks(
            **{**fake.callbacks().__dict__, "read_compile_status": replaced_process}
        ),
        bridge_port=65_534,
    )
    coordinator.start(str(project_root))
    observe_apply_lifecycle(fake, coordinator)

    with pytest.raises(runtime.PrimitiveBasisLiveRuntimeError, match="process identity"):
        coordinator.readback_and_request_restore()


@pytest.mark.parametrize("failure_mode", ("failed", "replacement"))
def test_checkpoint_rejects_untrusted_unity_prepare(
    tmp_path: Path, failure_mode: str
) -> None:
    _project_root, _bootstrap, fake, coordinator = make_runtime(tmp_path)
    coordinator.start(str(fake.project_root))
    completed = fake.apply_payload()
    approval = dict(completed["approval"])
    approval["status"] = "applying"
    assert coordinator.observe_apply_lifecycle(
        "approval_started", {"approval": approval}
    ) is True
    checkpoint = completed["execution"]["checkpoint"]
    if failure_mode == "failed":
        checkpoint["unityPrepare"]["ok"] = False
    else:
        checkpoint["unityPrepare"]["unityProcessId"] = 1_999_999_999

    with pytest.raises(runtime.PrimitiveBasisLiveRuntimeError):
        coordinator.observe_apply_lifecycle(
            "checkpoint_created",
            {"approval": approval, "checkpoint": checkpoint},
        )


@pytest.mark.parametrize("failure_mode", ("failed", "replacement"))
def test_restore_rejects_untrusted_unity_reload(
    tmp_path: Path, failure_mode: str
) -> None:
    _project_root, _bootstrap, fake, coordinator = make_runtime(tmp_path)
    coordinator.start(str(fake.project_root))
    observe_apply_lifecycle(fake, coordinator)
    coordinator.readback_and_request_restore()
    completed = fake.restore_payload()
    approval = dict(completed["approval"])
    approval["status"] = "applying"
    assert coordinator.observe_apply_lifecycle(
        "approval_started", {"approval": approval}
    ) is True
    result = completed["execution"]["result"]
    if failure_mode == "failed":
        result["unityReload"]["ok"] = False
    else:
        result["unityReload"]["unityProcessId"] = 1_999_999_999

    with pytest.raises(runtime.PrimitiveBasisLiveRuntimeError):
        coordinator.observe_apply_lifecycle(
            "handler_returned",
            {"approval": approval, "result": result},
        )


def test_compile_projection_unwraps_serialized_tool_payload() -> None:
    projection = runtime._compile_projection(
        {
            "ok": True,
            "result": {
                "exitCode": 0,
                "stdout": "",
                "payload": {
                    "data": {
                        "ok": True,
                        "isCompiling": False,
                        "hasErrors": False,
                        "errorCount": 0,
                        "source": "compilation_pipeline",
                        "capturedAt": "2026-07-23T00:00:30Z",
                        "projectPathDigest": digest(b"project"),
                        "unityProcessId": 2_000_000_000,
                        "unityProcessStartedAtUtc": "2026-07-23T00:00:00.0000000Z",
                        "unityExecutableDigest": digest(b"unity-editor"),
                    }
                },
            },
        }
    )

    assert projection == {
        "exitCode": 0,
        "errorCount": 0,
        "hasErrors": False,
        "isCompiling": False,
        "source": "compilation_pipeline",
        "capturedAt": "2026-07-23T00:00:30Z",
        "projectPathDigest": digest(b"project"),
        "unityProcessId": 2_000_000_000,
        "unityProcessStartedAtUtc": "2026-07-23T00:00:00.0000000Z",
        "unityExecutableDigest": digest(b"unity-editor"),
    }


def test_apply_argument_rebinding_is_rejected(tmp_path: Path) -> None:
    project_root, bootstrap, fake, _coordinator = make_runtime(tmp_path)

    def changed_request(params: dict[str, object]) -> dict[str, object]:
        payload = fake.create_apply_request(params)
        payload["approval"]["arguments"]["gameObjectPath"] = "FixtureAvatar/Other"
        payload["approval"]["argumentsDigest"] = runtime._hash_json(
            payload["approval"]["arguments"]
        )
        return payload

    coordinator = runtime.ModelPartCompositionLiveRuntime(
        live.PrimitiveBasisLiveSession(bootstrap),
        runtime.LiveRuntimeCallbacks(
            **{**fake.callbacks().__dict__, "create_apply_request": changed_request}
        ),
        bridge_port=65_534,
    )
    with pytest.raises(runtime.PrimitiveBasisLiveRuntimeError, match="arguments changed"):
        coordinator.start(str(project_root))


def test_unrelated_approval_is_not_counted(tmp_path: Path) -> None:
    project_root, _bootstrap, _fake, coordinator = make_runtime(tmp_path)
    coordinator.start(str(project_root))

    assert coordinator.observe_approval_execution(
        {"approval": {"id": "approval-unrelated"}}
    ) is False
    with pytest.raises(runtime.PrimitiveBasisLiveRuntimeError, match="phase order"):
        coordinator.readback_and_request_restore()


def test_finalization_requires_project_process_and_port_cleanup(tmp_path: Path) -> None:
    project_root, _bootstrap, fake, coordinator = make_runtime(tmp_path)
    coordinator.start(str(project_root))
    observe_apply_lifecycle(fake, coordinator)
    coordinator.readback_and_request_restore()
    observe_restore_lifecycle(fake, coordinator)
    coordinator.prepare_cleanup()

    with pytest.raises(runtime.PrimitiveBasisLiveRuntimeError, match="still exists"):
        coordinator.finalize_after_cleanup()
