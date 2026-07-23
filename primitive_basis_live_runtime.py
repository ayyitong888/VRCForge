from __future__ import annotations

import hashlib
import json
import os
import socket
import stat
import threading
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable, Mapping

from primitive_basis_live_attestation import (
    MODEL_TARGET_TOOL,
    RESTORE_TARGET_TOOL,
    PrimitiveBasisLiveSession,
)
from primitive_basis_matrix import FixtureSet, MatrixContractError, load_fixture_set


MODEL_SCENARIO_ID = "model_part_composition"
MODEL_PRIMITIVE_ID = "non_destructive_part_composition"
FIXTURE_DESCRIPTOR_DIRECTORY = Path("VRCForgeFixture/descriptors")
FIXTURE_ROOT = Path("Assets/VRCForge/PrimitiveBasis/model_part_composition")
FIXTURE_CONTRACT = FIXTURE_ROOT / "fixture-contract.json"
FIXTURE_BASELINE = FIXTURE_ROOT / "baseline.json"
FIXTURE_SCENE = FIXTURE_ROOT / "ModelPartComposition.unity"
FIXED_EDITOR_TOOL_TREE = Path("Assets/VRCForge/Editor")
FIXED_EDITOR_TOOL_TREE_META = Path("Assets/VRCForge/Editor.meta")
FIXTURE_READY_MARKER = Path("Library/VRCForge/primitive-basis-model-part-ready.json")
EXPECTED_UNITY_VERSION = "2022.3.22f1"
EXPECTED_SCENE_GUID = "285dbe12f5ede174cbcd075983e1410f"
EXPECTED_SCENE_DIGEST = "59de4a023cd1912acbbe9215c722886cc700879f43d8d4d477145f24b58aa97f"
EXPECTED_CONTRACT_DIGEST = "68a466d27cdb6718586fa74661d7337404c684c6a1c5a21e0af618c7b790e74d"
EXPECTED_BASELINE_FILE_DIGEST = "68160ac3203b41c5ccf2a5e882f4436b7a46fffc3c5e6eb8a89dea9d97c25f33"
EXPECTED_COMPONENT_HOST = "FixtureAvatar/Part/Armature"
EXPECTED_AVATAR_ROOT = "FixtureAvatar"
EXPECTED_MERGE_TARGET = "FixtureAvatar/Armature"
EXPECTED_COMPONENT_TYPE = "nadena.dev.modular_avatar.core.ModularAvatarMergeArmature"
EXPECTED_AVATAR_ROOT_TYPE = "nadena.dev.ndmf.runtime.components.NDMFAvatarRoot"
EXPECTED_TRANSFORM_PATHS = tuple(
    sorted(
        (
            "FixtureAvatar",
            "FixtureAvatar/Armature",
            "FixtureAvatar/Armature/Hips",
            "FixtureAvatar/Part",
            "FixtureAvatar/Part/Armature",
            "FixtureAvatar/Part/Armature/Hips",
            "FixtureAvatar/Part/RendererProbe",
        )
    )
)
EXPECTED_RENDERER_PATH = "FixtureAvatar/Part/RendererProbe"
EXPECTED_RENDERER_BONE = "FixtureAvatar/Part/Armature/Hips"
FIXTURE_INSPECT_TOOL = "vrc_inspect_primitive_basis_fixture"
FIXTURE_RELOAD_TOOL = "vrc_reload_primitive_basis_fixture"
COMPONENT_INSPECT_TOOL = "vrc_inspect_modular_avatar_component"
DEFAULT_BRIDGE_PORT = 8080
_INVENTORY_ROOTS = ("Assets", "Packages", "ProjectSettings", "VRCForgeFixture")
_MAX_INVENTORY_FILES = 50_000
_MAX_INVENTORY_BYTES = 4 * 1024 * 1024 * 1024
_REQUIRED_PACKAGE_VERSIONS = {
    "com.coplaydev.unity-mcp": "9.6.9-beta.7",
    "com.vrchat.avatars": "3.10.3",
    "com.vrchat.base": "3.10.3",
    "nadena.dev.modular-avatar": "1.17.1",
    "nadena.dev.ndmf": "1.13.1",
}
_EXPECTED_PROJECT_FILES = {
    "Packages/manifest.json",
    "Packages/packages-lock.json",
    "ProjectSettings/ProjectVersion.txt",
}


class PrimitiveBasisLiveRuntimeError(RuntimeError):
    """The fixed live composition run did not satisfy its package-owned contract."""


@dataclass(frozen=True)
class LiveRuntimeCallbacks:
    bind_connection: Callable[[dict[str, Any]], dict[str, Any]]
    validate_connection: Callable[[dict[str, Any]], dict[str, Any]]
    inspect_fixture: Callable[[dict[str, Any]], dict[str, Any]]
    reload_fixture: Callable[[dict[str, Any]], dict[str, Any]]
    inspect_component: Callable[[dict[str, Any]], dict[str, Any]]
    preview_component: Callable[[dict[str, Any]], dict[str, Any]]
    create_apply_request: Callable[[dict[str, Any]], dict[str, Any]]
    read_compile_status: Callable[[dict[str, Any]], dict[str, Any]]
    create_restore_request: Callable[[str], dict[str, Any]]
    preview_checkpoint: Callable[[str], dict[str, Any]]


class ModelPartCompositionLiveRuntime:
    """Backend-owned state machine for the first fixed packaged live row."""

    def __init__(
        self,
        session: PrimitiveBasisLiveSession,
        callbacks: LiveRuntimeCallbacks,
        *,
        bridge_port: int = DEFAULT_BRIDGE_PORT,
    ) -> None:
        self._session = session
        self._callbacks = callbacks
        self._bridge_port = int(bridge_port)
        self._lock = threading.Lock()
        self._project_root: Path | None = None
        self._fixtures: FixtureSet | None = None
        self._fixture_digest = ""
        self._project_binding_digest = ""
        self._connection_binding_digest = ""
        self._fixture_project_input_digest = ""
        self._unity_process_id = 0
        self._unity_process_started_at = ""
        self._unity_executable_digest = ""
        self._baseline_state_digest = ""
        self._baseline_inventory_digest = ""
        self._request_id = ""
        self._approval_id = ""
        self._checkpoint_id = ""
        self._arguments_digest = ""
        self._operation_digest = ""
        self._restore_request_id = ""
        self._restore_approval_id = ""
        self._restore_arguments_digest = ""
        self._pre_apply_project_input_digest = ""
        self._cleanup_facts: tuple[dict[str, Any], dict[str, Any]] | None = None

    @property
    def state(self) -> str:
        return self._session.state

    @property
    def fixtures(self) -> FixtureSet | None:
        return self._fixtures

    @property
    def project_binding_digest(self) -> str:
        return self._project_binding_digest

    def start(self, project_path: str) -> dict[str, Any]:
        with self._lock:
            if self._session.state != "issued" or self._project_root is not None:
                raise PrimitiveBasisLiveRuntimeError("The fixed live run has already started.")
            project_root = _resolve_project_root(project_path)
            fixtures = _load_and_verify_fixture_set(project_root, self._session)
            fixture_project_input_digest = compute_fixed_project_input_digest(project_root)
            if fixture_project_input_digest != self._session.fixture_project_input_digest:
                raise PrimitiveBasisLiveRuntimeError("The fixed project input binding changed.")
            fixture = next(
                item for item in fixtures.fixtures if item.scenario_id == MODEL_SCENARIO_ID
            )
            marker = _load_ready_marker(project_root, self._session.challenge_digest)
            connection_binding = self._invoke_callback(
                self._callbacks.bind_connection,
                {"projectPath": str(project_root)},
                "The fixed Unity connection could not be frozen.",
            )
            connection_binding_digest = _verify_connection_binding(
                connection_binding,
                project_root=project_root,
            )
            _require_fixed_project_inputs(project_root)
            fixture_identity = self._invoke_callback(
                self._callbacks.inspect_fixture,
                {
                    "projectPath": str(project_root),
                    "expectedRunIdDigest": self._session.challenge_digest,
                },
                "Unity fixture identity could not be inspected.",
            )
            _verify_unity_fixture_identity(
                fixture_identity,
                project_root=project_root,
                marker=marker,
                expected_unity_executable_digest=self._session.unity_editor_digest,
            )
            validated_connection = self._invoke_callback(
                self._callbacks.validate_connection,
                {"connectionBindingDigest": connection_binding_digest},
                "The fixed Unity connection changed during bootstrap.",
            )
            if _verify_connection_binding(validated_connection, project_root=project_root) != connection_binding_digest:
                raise PrimitiveBasisLiveRuntimeError("The fixed Unity connection changed during bootstrap.")
            _require_fixed_project_inputs(project_root)
            unity_process_id = fixture_identity.get("unityProcessId")
            if type(unity_process_id) is not int or unity_process_id <= 0:
                raise PrimitiveBasisLiveRuntimeError("Unity fixture process identity is invalid.")

            project_binding_digest = _hash_json(
                {
                    "runId": self._session.run_id,
                    "fixtureDigest": fixture.digest,
                    "projectPathDigest": fixture_identity["projectPathDigest"],
                    "unityProcessId": unity_process_id,
                    "unityProcessStartedAtUtc": fixture_identity["unityProcessStartedAtUtc"],
                    "unityExecutableDigest": fixture_identity["unityExecutableDigest"],
                    "connectionBindingDigest": connection_binding_digest,
                    "fixtureProjectInputDigest": fixture_project_input_digest,
                    "readyMarkerDigest": fixture_identity["readyMarkerDigest"],
                    "activeSceneGuid": fixture_identity["activeSceneGuid"],
                    "sceneDigest": fixture_identity["sceneDigest"],
                    "contractDigest": fixture_identity["contractDigest"],
                    "baselineManifestDigest": fixture_identity[
                        "baselineManifestDigest"
                    ],
                }
            )
            self._session.begin(
                fixture_digest=fixture.digest,
                project_binding_digest=project_binding_digest,
            )
            self._project_root = project_root
            self._fixtures = fixtures
            self._fixture_digest = fixture.digest
            self._project_binding_digest = project_binding_digest
            self._connection_binding_digest = connection_binding_digest
            self._fixture_project_input_digest = fixture_project_input_digest
            self._unity_process_id = unity_process_id
            self._unity_process_started_at = str(fixture_identity["unityProcessStartedAtUtc"])
            self._unity_executable_digest = str(fixture_identity["unityExecutableDigest"])

            component = self._inspect_component()
            _require_component_absent(component)
            state_digest = _component_state_digest(project_root, component)
            inventory_digest = _project_inventory_digest(project_root)
            self._baseline_state_digest = state_digest
            self._baseline_inventory_digest = inventory_digest
            self._session.record(
                "detect",
                {
                    "stateDigest": state_digest,
                    "inventoryDigest": inventory_digest,
                    "componentPresent": False,
                },
                authoritative_event={
                    "source": "unity_readback",
                    "fixtureIdentityDigest": _hash_json(fixture_identity),
                    "componentStateDigest": state_digest,
                },
            )

            preview = self._call(
                self._callbacks.preview_component,
                self._component_arguments(preview=True),
                "The fixed component preview failed.",
            )
            if preview.get("ok") is not True or preview.get("preview") is not True:
                raise PrimitiveBasisLiveRuntimeError("The fixed component preview was invalid.")
            after_preview = self._inspect_component()
            _require_component_absent(after_preview)
            preview_state_digest = _component_state_digest(project_root, after_preview)
            preview_inventory_digest = _project_inventory_digest(project_root)
            if (
                preview_state_digest != state_digest
                or preview_inventory_digest != inventory_digest
            ):
                raise PrimitiveBasisLiveRuntimeError("The fixed preview changed project state.")
            self._session.record(
                "preview",
                {
                    "beforeStateDigest": state_digest,
                    "afterStateDigest": preview_state_digest,
                    "mutationCount": 0,
                },
                authoritative_event={
                    "source": "unity_preview",
                    "previewDigest": _hash_json(_public_preview_projection(preview)),
                    "inventoryDigest": preview_inventory_digest,
                },
            )

            apply_arguments = self._component_arguments(preview=False)
            request_payload = self._call(
                self._callbacks.create_apply_request,
                {
                    "target_tool": MODEL_TARGET_TOOL,
                    "arguments": apply_arguments,
                    "reason": "Run the fixed supervised model-part composition fixture.",
                    "preview": _public_preview_projection(preview),
                    "agent_name": "primitive-basis-live-runner",
                    "never_auto_approve": True,
                    "requires_explicit_approval": True,
                },
                "The fixed apply request could not be created.",
            )
            approval = _require_pending_approval(request_payload, MODEL_TARGET_TOOL)
            self._approval_id = _safe_id(approval.get("id"), "apply approval")
            self._arguments_digest = _require_arguments_digest(
                approval,
                "apply arguments",
            )
            if self._arguments_digest != _hash_json(apply_arguments):
                raise PrimitiveBasisLiveRuntimeError("The fixed apply arguments changed.")
            self._operation_digest = _hash_json(
                {
                    "targetTool": MODEL_TARGET_TOOL,
                    "argumentsDigest": self._arguments_digest,
                    "previewDigest": _hash_json(_public_preview_projection(preview)),
                    "projectBindingDigest": project_binding_digest,
                }
            )
            self._request_id = _derived_id("request", self._operation_digest)
            self._session.record(
                "request",
                {
                    "requestId": self._request_id,
                    "targetTool": MODEL_TARGET_TOOL,
                    "argumentsDigest": self._arguments_digest,
                    "operationDigest": self._operation_digest,
                    "projectBindingDigest": project_binding_digest,
                    "state": "approval_pending",
                },
                authoritative_event={
                    "source": "approval_store",
                    "approvalId": self._approval_id,
                    "operationDigest": self._operation_digest,
                },
            )
            return {
                "ok": True,
                "schema": "vrcforge.primitive_basis_live_start.v1",
                "runId": self._session.run_id,
                "scenarioId": MODEL_SCENARIO_ID,
                "primitiveId": MODEL_PRIMITIVE_ID,
                "approvalId": self._approval_id,
                "projectBindingDigest": project_binding_digest,
                "unityProcessId": unity_process_id,
            }

    def observe_approval_execution(self, payload: Mapping[str, Any]) -> bool:
        with self._lock:
            if self._session.state != "running":
                return False
            approval = _mapping_or_empty(payload.get("approval"))
            approval_id = str(approval.get("id") or "")
            if approval_id == self._approval_id:
                self._observe_apply_execution(payload, approval)
                return True
            if approval_id == self._restore_approval_id:
                self._observe_restore_execution(payload, approval)
                return True
            return False

    def observe_apply_lifecycle(self, stage: str, payload: Mapping[str, Any]) -> bool:
        with self._lock:
            if self._session.state != "running":
                return False
            approval = _mapping_or_empty(payload.get("approval"))
            approval_id = str(approval.get("id") or "")
            if approval_id == self._approval_id:
                return self._observe_apply_lifecycle_stage(stage, payload, approval)
            if approval_id == self._restore_approval_id:
                return self._observe_restore_lifecycle_stage(stage, payload, approval)
            return False

    def status(self) -> dict[str, Any]:
        with self._lock:
            return {
                "ok": True,
                "schema": "vrcforge.primitive_basis_live_status.v1",
                "runId": self._session.run_id,
                "state": self._session.state,
                "receiptCount": self._session.receipt_count,
                "approvalId": self._approval_id,
                "checkpointId": self._checkpoint_id,
                "restoreApprovalId": self._restore_approval_id,
                "projectBindingDigest": self._project_binding_digest,
                "connectionBindingDigest": self._connection_binding_digest,
            }

    def _observe_apply_lifecycle_stage(
        self,
        stage: str,
        payload: Mapping[str, Any],
        approval: Mapping[str, Any],
    ) -> bool:
        arguments = _require_mapping(approval.get("arguments"), "approved apply arguments")
        if _hash_json(arguments) != self._arguments_digest:
            raise PrimitiveBasisLiveRuntimeError("Approved apply arguments changed.")
        if stage == "approval_started":
            if self._session.receipt_count >= 4:
                return True
            self._require_running_phase("approval")
            if approval.get("status") != "applying" or not approval.get("approvedAt"):
                raise PrimitiveBasisLiveRuntimeError("The fixed approval transition is invalid.")
            self._session.record(
                "approval",
                {
                    "requestId": self._request_id,
                    "approvalId": self._approval_id,
                    "targetTool": MODEL_TARGET_TOOL,
                    "argumentsDigest": self._arguments_digest,
                    "operationDigest": self._operation_digest,
                    "projectBindingDigest": self._project_binding_digest,
                    "pendingObserved": True,
                    "approved": True,
                },
                authoritative_event={
                    "source": "approval_transition",
                    "approvalId": self._approval_id,
                    "approvedAtDigest": _hash_text(str(approval.get("approvedAt") or "")),
                },
            )
            return True
        if stage == "checkpoint_created":
            if self._session.receipt_count >= 5:
                return True
            self._require_running_phase("checkpoint")
            checkpoint = _require_mapping(payload.get("checkpoint"), "apply checkpoint")
            if checkpoint.get("ok") is not True:
                raise PrimitiveBasisLiveRuntimeError("The fixed checkpoint failed.")
            unity_prepare = _require_mapping(
                checkpoint.get("unityPrepare"), "Unity checkpoint preparation"
            )
            if unity_prepare.get("ok") is not True:
                raise PrimitiveBasisLiveRuntimeError("The fixed Unity checkpoint failed.")
            unity_process_identity_digest = self._require_unity_process_identity(
                unity_prepare
            )
            project_input_digest = self._require_fixed_project_input_digest()
            self._checkpoint_id = _safe_id(checkpoint.get("id"), "checkpoint")
            self._session.record(
                "checkpoint",
                {
                    "approvalId": self._approval_id,
                    "checkpointId": self._checkpoint_id,
                    "targetTool": MODEL_TARGET_TOOL,
                    "argumentsDigest": self._arguments_digest,
                    "operationDigest": self._operation_digest,
                    "projectBindingDigest": self._project_binding_digest,
                    "fixtureProjectInputDigest": project_input_digest,
                    "unityProcessIdentityDigest": unity_process_identity_digest,
                    "created": True,
                },
                authoritative_event={
                    "source": "checkpoint_store",
                    "checkpointId": self._checkpoint_id,
                    "checkpointDigest": _hash_json(_checkpoint_projection(checkpoint)),
                    "fixtureProjectInputDigest": project_input_digest,
                    "unityProcessIdentityDigest": unity_process_identity_digest,
                },
            )
            return True
        if stage == "handler_starting":
            if self._session.receipt_count >= 6:
                return True
            self._require_running_phase("apply")
            if not self._checkpoint_id:
                raise PrimitiveBasisLiveRuntimeError("The fixed checkpoint identity is missing.")
            if _require_arguments_digest(payload, "handler arguments") != self._arguments_digest:
                raise PrimitiveBasisLiveRuntimeError("Approved apply arguments changed.")
            self._pre_apply_project_input_digest = self._require_fixed_project_input_digest()
            return True
        if stage == "handler_returned":
            if self._session.receipt_count >= 6:
                return True
            self._require_running_phase("apply")
            if not self._checkpoint_id:
                raise PrimitiveBasisLiveRuntimeError("The fixed checkpoint identity is missing.")
            if not self._pre_apply_project_input_digest:
                raise PrimitiveBasisLiveRuntimeError("The fixed write boundary was not observed.")
            project_input_digest = self._require_fixed_project_input_digest()
            if project_input_digest != self._pre_apply_project_input_digest:
                raise PrimitiveBasisLiveRuntimeError("The fixed project input binding changed.")
            result = _require_mapping(payload.get("result"), "apply result")
            if result.get("ok") is not True or result.get("sceneSaved") is not True:
                raise PrimitiveBasisLiveRuntimeError("The fixed apply execution failed.")
            execution_id = _derived_id(
                "execution",
                _hash_json(
                    {
                        "approvalId": self._approval_id,
                        "checkpointId": self._checkpoint_id,
                        "result": _apply_result_projection(result),
                    }
                ),
            )
            self._session.record(
                "apply",
                {
                    "executionId": execution_id,
                    "approvalId": self._approval_id,
                    "checkpointId": self._checkpoint_id,
                    "targetTool": MODEL_TARGET_TOOL,
                    "argumentsDigest": self._arguments_digest,
                    "operationDigest": self._operation_digest,
                    "projectBindingDigest": self._project_binding_digest,
                    "fixtureProjectInputDigest": project_input_digest,
                    "applied": True,
                },
                authoritative_event={
                    "source": "write_handler_result",
                    "executionId": execution_id,
                    "resultDigest": _hash_json(_apply_result_projection(result)),
                    "fixtureProjectInputDigest": project_input_digest,
                },
            )
            return True
        return False

    def _observe_restore_lifecycle_stage(
        self,
        stage: str,
        payload: Mapping[str, Any],
        approval: Mapping[str, Any],
    ) -> bool:
        arguments = _require_mapping(approval.get("arguments"), "approved restore arguments")
        if _hash_json(arguments) != self._restore_arguments_digest:
            raise PrimitiveBasisLiveRuntimeError("Approved restore arguments changed.")
        if stage == "approval_started":
            if self._session.receipt_count >= 10:
                return True
            self._require_running_phase("restore_approval")
            if approval.get("status") != "applying" or not approval.get("approvedAt"):
                raise PrimitiveBasisLiveRuntimeError("The fixed restore approval is invalid.")
            self._session.record(
                "restore_approval",
                {
                    "requestId": self._restore_request_id,
                    "approvalId": self._restore_approval_id,
                    "targetTool": RESTORE_TARGET_TOOL,
                    "checkpointId": self._checkpoint_id,
                    "projectBindingDigest": self._project_binding_digest,
                    "argumentsDigest": self._restore_arguments_digest,
                    "pendingObserved": True,
                    "approved": True,
                },
                authoritative_event={
                    "source": "approval_transition",
                    "approvalId": self._restore_approval_id,
                    "approvedAtDigest": _hash_text(str(approval.get("approvedAt") or "")),
                },
            )
            return True
        if stage == "handler_starting":
            if _require_arguments_digest(
                payload,
                "restore handler arguments",
            ) != self._restore_arguments_digest:
                raise PrimitiveBasisLiveRuntimeError("Approved restore arguments changed.")
            return True
        if stage == "handler_returned":
            if self._session.receipt_count >= 11:
                return True
            self._require_running_phase("restore_execution")
            result = _require_mapping(payload.get("result"), "restore result")
            unity_reload = _require_mapping(
                result.get("unityReload"), "Unity checkpoint reload"
            )
            if (
                result.get("ok") is not True
                or result.get("restored") is not True
                or result.get("checkpointId") != self._checkpoint_id
                or unity_reload.get("ok") is not True
            ):
                raise PrimitiveBasisLiveRuntimeError("The fixed restore execution failed.")
            unity_process_identity_digest = self._require_unity_process_identity(
                unity_reload
            )
            execution_id = _derived_id(
                "restore-execution",
                _hash_json(
                    {
                        "approvalId": self._restore_approval_id,
                        "checkpointId": self._checkpoint_id,
                        "result": _restore_result_projection(result),
                    }
                ),
            )
            self._session.record(
                "restore_execution",
                {
                    "executionId": execution_id,
                    "approvalId": self._restore_approval_id,
                    "targetTool": RESTORE_TARGET_TOOL,
                    "checkpointId": self._checkpoint_id,
                    "projectBindingDigest": self._project_binding_digest,
                    "argumentsDigest": self._restore_arguments_digest,
                    "unityProcessIdentityDigest": unity_process_identity_digest,
                    "restored": True,
                },
                authoritative_event={
                    "source": "checkpoint_restore_result",
                    "executionId": execution_id,
                    "resultDigest": _hash_json(_restore_result_projection(result)),
                    "unityProcessIdentityDigest": unity_process_identity_digest,
                },
            )
            return True
        return False

    def readback_and_request_restore(self) -> dict[str, Any]:
        with self._lock:
            self._require_running_phase("readback")
            project_root = self._require_project_root()
            self._reload_fixed_scene()
            component = self._inspect_component()
            _require_component_applied(component)
            actual_state_digest = _component_state_digest(project_root, component)
            expected_state_digest = _hash_json(
                {
                    "sceneDigest": _stable_file_digest(project_root / FIXTURE_SCENE),
                    "component": _expected_component_projection(component),
                }
            )
            if actual_state_digest != expected_state_digest:
                raise PrimitiveBasisLiveRuntimeError("Component readback did not match.")
            self._session.record(
                "readback",
                {
                    "checkpointId": self._checkpoint_id,
                    "expectedStateDigest": expected_state_digest,
                    "actualStateDigest": actual_state_digest,
                    "matched": True,
                },
                authoritative_event={
                    "source": "unity_readback",
                    "componentStateDigest": actual_state_digest,
                },
            )

            compile_projection = self._read_stable_compile_status()
            passed = (
                compile_projection["exitCode"] == 0
                and compile_projection["errorCount"] == 0
                and compile_projection["hasErrors"] is False
                and compile_projection["isCompiling"] is False
                and component.get("sceneDirty") is False
            )
            if not passed:
                raise PrimitiveBasisLiveRuntimeError("The focused live validation failed.")
            report_digest = _hash_json(
                {
                    "componentStateDigest": actual_state_digest,
                    "compile": compile_projection,
                    "sceneDirty": component.get("sceneDirty"),
                }
            )
            self._session.record(
                "validation",
                {
                    "checkpointId": self._checkpoint_id,
                    "passed": True,
                    "reportDigest": report_digest,
                },
                authoritative_event={
                    "source": "bounded_validation",
                    "reportDigest": report_digest,
                    "compileDigest": _hash_json(compile_projection),
                },
            )

            expected_restore_arguments = self._restore_arguments()
            restore_payload = self._call_restore_request(self._checkpoint_id)
            restore_approval = _require_pending_approval(
                restore_payload, RESTORE_TARGET_TOOL
            )
            self._restore_approval_id = _safe_id(
                restore_approval.get("id"), "restore approval"
            )
            self._restore_arguments_digest = _require_arguments_digest(
                restore_approval,
                "restore arguments",
            )
            if self._restore_arguments_digest != _hash_json(
                expected_restore_arguments
            ):
                raise PrimitiveBasisLiveRuntimeError("Restore request changed checkpoint scope.")
            self._restore_request_id = _derived_id(
                "restore-request", self._restore_arguments_digest
            )
            self._session.record(
                "restore_request",
                {
                    "requestId": self._restore_request_id,
                    "targetTool": RESTORE_TARGET_TOOL,
                    "checkpointId": self._checkpoint_id,
                    "projectBindingDigest": self._project_binding_digest,
                    "argumentsDigest": self._restore_arguments_digest,
                    "state": "approval_pending",
                },
                authoritative_event={
                    "source": "checkpoint_restore_request",
                    "approvalId": self._restore_approval_id,
                    "checkpointId": self._checkpoint_id,
                },
            )
            return {
                "ok": True,
                "schema": "vrcforge.primitive_basis_live_restore_request.v1",
                "runId": self._session.run_id,
                "approvalId": self._restore_approval_id,
                "checkpointId": self._checkpoint_id,
            }

    def prepare_cleanup(self) -> dict[str, Any]:
        with self._lock:
            self._require_running_phase("baseline_comparison")
            project_root = self._require_project_root()
            self._reload_fixed_scene()
            component = self._inspect_component()
            _require_component_absent(component)
            actual_state_digest = _component_state_digest(project_root, component)
            actual_inventory_digest = _project_inventory_digest(project_root)
            if compute_fixed_project_input_digest(project_root) != self._fixture_project_input_digest:
                raise PrimitiveBasisLiveRuntimeError("The fixed project input binding changed.")
            checkpoint_preview = self._call_checkpoint_preview(self._checkpoint_id)
            changed_files = checkpoint_preview.get("changedFiles")
            if changed_files is not None and (
                not isinstance(changed_files, list) or changed_files
            ):
                raise PrimitiveBasisLiveRuntimeError("Checkpoint restore still has changes.")
            if (
                actual_state_digest != self._baseline_state_digest
                or actual_inventory_digest != self._baseline_inventory_digest
            ):
                raise PrimitiveBasisLiveRuntimeError("The fixed project baseline was not restored.")
            baseline_facts = {
                "checkpointId": self._checkpoint_id,
                "expectedBaselineDigest": self._baseline_state_digest,
                "actualStateDigest": actual_state_digest,
                "matched": True,
            }
            residue_facts = {
                "checkpointId": self._checkpoint_id,
                "inventoryDigest": actual_inventory_digest,
                "count": 0,
                "projectRemoved": True,
                "unityProcessExited": True,
                "bridgePortReleased": True,
            }
            self._cleanup_facts = (baseline_facts, residue_facts)
            return {
                "ok": True,
                "schema": "vrcforge.primitive_basis_live_cleanup_ready.v1",
                "runId": self._session.run_id,
                "unityProcessId": self._unity_process_id,
                "baselineDigest": actual_state_digest,
                "inventoryDigest": actual_inventory_digest,
                "deleteOnlyAfterUnityExit": True,
            }

    def finalize_after_cleanup(self) -> dict[str, Any]:
        with self._lock:
            self._require_running_phase("baseline_comparison")
            project_root = self._require_project_root()
            if self._cleanup_facts is None:
                raise PrimitiveBasisLiveRuntimeError("Cleanup was not prepared.")
            if project_root.exists():
                raise PrimitiveBasisLiveRuntimeError("Disposable fixture project still exists.")
            _require_process_exited(self._unity_process_id)
            _require_tcp_port_released(self._bridge_port)
            baseline_facts, residue_facts = self._cleanup_facts
            self._session.record(
                "baseline_comparison",
                baseline_facts,
                authoritative_event={
                    "source": "checkpoint_and_project_snapshot",
                    "baselineDigest": baseline_facts["actualStateDigest"],
                },
            )
            self._session.record(
                "residue",
                residue_facts,
                authoritative_event={
                    "source": "post_cleanup_probe",
                    "projectRemoved": True,
                    "unityProcessExited": True,
                    "bridgePortReleased": True,
                },
            )
            return self._session.finalize()

    def _observe_apply_execution(
        self, payload: Mapping[str, Any], approval: Mapping[str, Any]
    ) -> None:
        self._require_running_phase("approval")
        if not self._pre_apply_project_input_digest:
            raise PrimitiveBasisLiveRuntimeError("The fixed write boundary was not observed.")
        project_input_digest = self._require_fixed_project_input_digest()
        if project_input_digest != self._pre_apply_project_input_digest:
            raise PrimitiveBasisLiveRuntimeError("The fixed project input binding changed.")
        execution = _require_mapping(payload.get("execution"), "apply execution")
        nested_approval = _mapping_or_empty(execution.get("approval"))
        checkpoint = _require_mapping(execution.get("checkpoint"), "apply checkpoint")
        unity_prepare = _require_mapping(
            checkpoint.get("unityPrepare"), "Unity checkpoint preparation"
        )
        result = _require_mapping(execution.get("result"), "apply result")
        approval_record = nested_approval or approval
        if (
            payload.get("ok") is not True
            or approval_record.get("status") != "applied"
            or execution.get("status") != "applied"
            or checkpoint.get("ok") is not True
            or unity_prepare.get("ok") is not True
            or result.get("ok") is not True
            or result.get("sceneSaved") is not True
        ):
            raise PrimitiveBasisLiveRuntimeError("The fixed apply execution failed.")
        approval_arguments = _require_mapping(
            approval_record.get("arguments"), "approved apply arguments"
        )
        if _hash_json(approval_arguments) != self._arguments_digest:
            raise PrimitiveBasisLiveRuntimeError("Approved apply arguments changed.")
        unity_process_identity_digest = self._require_unity_process_identity(
            unity_prepare
        )
        checkpoint_id = _safe_id(checkpoint.get("id"), "checkpoint")
        self._checkpoint_id = checkpoint_id
        self._session.record(
            "approval",
            {
                "requestId": self._request_id,
                "approvalId": self._approval_id,
                "targetTool": MODEL_TARGET_TOOL,
                "argumentsDigest": self._arguments_digest,
                "operationDigest": self._operation_digest,
                "projectBindingDigest": self._project_binding_digest,
                "pendingObserved": True,
                "approved": True,
            },
            authoritative_event={
                "source": "approval_transition",
                "approvalId": self._approval_id,
                "approvedAtDigest": _hash_text(str(approval_record.get("approvedAt") or "")),
            },
        )
        self._session.record(
            "checkpoint",
            {
                "approvalId": self._approval_id,
                "checkpointId": checkpoint_id,
                "targetTool": MODEL_TARGET_TOOL,
                "argumentsDigest": self._arguments_digest,
                "operationDigest": self._operation_digest,
                "projectBindingDigest": self._project_binding_digest,
                "fixtureProjectInputDigest": project_input_digest,
                "unityProcessIdentityDigest": unity_process_identity_digest,
                "created": True,
            },
            authoritative_event={
                "source": "checkpoint_store",
                "checkpointId": checkpoint_id,
                "checkpointDigest": _hash_json(_checkpoint_projection(checkpoint)),
                "fixtureProjectInputDigest": project_input_digest,
                "unityProcessIdentityDigest": unity_process_identity_digest,
            },
        )
        execution_id = _derived_id(
            "execution",
            _hash_json(
                {
                    "approvalId": self._approval_id,
                    "checkpointId": checkpoint_id,
                    "result": _apply_result_projection(result),
                }
            ),
        )
        self._session.record(
            "apply",
            {
                "executionId": execution_id,
                "approvalId": self._approval_id,
                "checkpointId": checkpoint_id,
                "targetTool": MODEL_TARGET_TOOL,
                "argumentsDigest": self._arguments_digest,
                "operationDigest": self._operation_digest,
                "projectBindingDigest": self._project_binding_digest,
                "fixtureProjectInputDigest": project_input_digest,
                "applied": True,
            },
            authoritative_event={
                "source": "supervised_write_result",
                "executionId": execution_id,
                "resultDigest": _hash_json(_apply_result_projection(result)),
                "fixtureProjectInputDigest": project_input_digest,
            },
        )

    def _observe_restore_execution(
        self, payload: Mapping[str, Any], approval: Mapping[str, Any]
    ) -> None:
        self._require_running_phase("restore_approval")
        execution = _require_mapping(payload.get("execution"), "restore execution")
        nested_approval = _mapping_or_empty(execution.get("approval"))
        result = _require_mapping(execution.get("result"), "restore result")
        unity_reload = _require_mapping(
            result.get("unityReload"), "Unity checkpoint reload"
        )
        approval_record = nested_approval or approval
        if (
            payload.get("ok") is not True
            or approval_record.get("status") != "applied"
            or execution.get("status") != "applied"
            or result.get("ok") is not True
            or result.get("restored") is not True
            or result.get("checkpointId") != self._checkpoint_id
            or unity_reload.get("ok") is not True
        ):
            raise PrimitiveBasisLiveRuntimeError("The fixed restore execution failed.")
        arguments = _require_mapping(
            approval_record.get("arguments"), "approved restore arguments"
        )
        if _hash_json(arguments) != self._restore_arguments_digest:
            raise PrimitiveBasisLiveRuntimeError("Approved restore arguments changed.")
        unity_process_identity_digest = self._require_unity_process_identity(
            unity_reload
        )
        self._session.record(
            "restore_approval",
            {
                "requestId": self._restore_request_id,
                "approvalId": self._restore_approval_id,
                "targetTool": RESTORE_TARGET_TOOL,
                "checkpointId": self._checkpoint_id,
                "projectBindingDigest": self._project_binding_digest,
                "argumentsDigest": self._restore_arguments_digest,
                "pendingObserved": True,
                "approved": True,
            },
            authoritative_event={
                "source": "approval_transition",
                "approvalId": self._restore_approval_id,
                "approvedAtDigest": _hash_text(str(approval_record.get("approvedAt") or "")),
            },
        )
        execution_id = _derived_id(
            "restore-execution",
            _hash_json(
                {
                    "approvalId": self._restore_approval_id,
                    "checkpointId": self._checkpoint_id,
                    "result": _restore_result_projection(result),
                }
            ),
        )
        self._session.record(
            "restore_execution",
            {
                "executionId": execution_id,
                "approvalId": self._restore_approval_id,
                "targetTool": RESTORE_TARGET_TOOL,
                "checkpointId": self._checkpoint_id,
                "projectBindingDigest": self._project_binding_digest,
                "argumentsDigest": self._restore_arguments_digest,
                "unityProcessIdentityDigest": unity_process_identity_digest,
                "restored": True,
            },
            authoritative_event={
                "source": "checkpoint_restore_result",
                "executionId": execution_id,
                "resultDigest": _hash_json(_restore_result_projection(result)),
                "unityProcessIdentityDigest": unity_process_identity_digest,
            },
        )

    def _component_arguments(self, *, preview: bool) -> dict[str, Any]:
        project_root = self._require_project_root()
        return {
            "projectPath": str(project_root),
            "gameObjectPath": EXPECTED_COMPONENT_HOST,
            "avatarPath": EXPECTED_AVATAR_ROOT,
            "componentType": "MergeArmature",
            "allowDuplicate": False,
            "references": {"mergeTarget": EXPECTED_MERGE_TARGET},
            "saveScene": True,
            "preview": preview,
            **self._unity_guard_arguments(),
        }

    def _restore_arguments(self) -> dict[str, Any]:
        return {
            "checkpointId": self._checkpoint_id,
            "confirmRestore": True,
            "projectRoot": str(self._require_project_root()),
        }

    def _unity_guard_arguments(self) -> dict[str, Any]:
        project_root = self._require_project_root()
        return {
            "expectedRunIdDigest": self._session.challenge_digest,
            "expectedProjectPathDigest": _hash_text(_normalize_project_root(project_root)),
            "expectedUnityProcessId": self._unity_process_id,
            "expectedUnityProcessStartedAtUtc": self._unity_process_started_at,
            "expectedUnityExecutableDigest": self._unity_executable_digest,
        }

    def _reload_fixed_scene(self) -> dict[str, Any]:
        payload = self._call(
            self._callbacks.reload_fixture,
            self._unity_guard_arguments(),
            "The fixed scene could not be reloaded from disk.",
        )
        expected = {
            "schema": "vrcforge.primitive_basis_scene_reload.v1",
            "reloaded": True,
            "sceneDirty": False,
            "scenePath": FIXTURE_SCENE.as_posix(),
            "unityProcessId": self._unity_process_id,
            "unityProcessStartedAtUtc": self._unity_process_started_at,
            "unityExecutableDigest": self._unity_executable_digest,
            "projectPathDigest": _hash_text(
                _normalize_project_root(self._require_project_root())
            ),
        }
        if any(payload.get(key) != value for key, value in expected.items()):
            raise PrimitiveBasisLiveRuntimeError("The fixed scene reload identity changed.")
        return payload

    def _read_stable_compile_status(self) -> dict[str, Any]:
        deadline = time.monotonic() + 30.0
        last_projection: dict[str, Any] | None = None
        while time.monotonic() < deadline:
            payload = self._call(
                self._callbacks.read_compile_status,
                {
                    "projectPath": str(self._require_project_root()),
                    "maxErrors": 20,
                    **self._unity_guard_arguments(),
                },
                "Compile status could not be read.",
            )
            last_projection = _compile_projection(payload)
            self._require_unity_process_identity(last_projection)
            if last_projection["isCompiling"] is False:
                return last_projection
            time.sleep(0.25)
        raise PrimitiveBasisLiveRuntimeError("Unity compilation did not become idle.")

    def _inspect_component(self) -> dict[str, Any]:
        return self._call(
            self._callbacks.inspect_component,
            self._component_arguments(preview=False),
            "The fixed component state could not be inspected.",
        )

    def _call(
        self,
        operation: Callable[[dict[str, Any]], dict[str, Any]],
        params: dict[str, Any],
        message: str,
    ) -> dict[str, Any]:
        self._require_frozen_connection()
        self._require_fixed_project_input_digest()
        payload = self._invoke_callback(operation, params, message)
        self._require_frozen_connection()
        self._require_fixed_project_input_digest()
        return payload

    def _require_fixed_project_input_digest(self) -> str:
        project_input_digest = compute_fixed_project_input_digest(
            self._require_project_root()
        )
        if project_input_digest != self._fixture_project_input_digest:
            raise PrimitiveBasisLiveRuntimeError("The fixed project input binding changed.")
        return project_input_digest

    def _require_unity_process_identity(self, payload: Mapping[str, Any]) -> str:
        expected = {
            "projectPathDigest": _hash_text(
                _normalize_project_root(self._require_project_root())
            ),
            "unityProcessId": self._unity_process_id,
            "unityProcessStartedAtUtc": self._unity_process_started_at,
            "unityExecutableDigest": self._unity_executable_digest,
        }
        if any(payload.get(key) != value for key, value in expected.items()):
            raise PrimitiveBasisLiveRuntimeError("Unity process identity changed.")
        return _hash_json(expected)

    @staticmethod
    def _invoke_callback(
        operation: Callable[[dict[str, Any]], dict[str, Any]],
        params: dict[str, Any],
        message: str,
    ) -> dict[str, Any]:
        try:
            payload = operation(params)
        except Exception as exc:  # noqa: BLE001 - private bridge details stay internal.
            raise PrimitiveBasisLiveRuntimeError(message) from exc
        if not isinstance(payload, dict) or payload.get("ok") is not True:
            raise PrimitiveBasisLiveRuntimeError(message)
        return payload

    def _require_frozen_connection(self) -> None:
        payload = self._invoke_callback(
            self._callbacks.validate_connection,
            {"connectionBindingDigest": self._connection_binding_digest},
            "The fixed Unity connection changed.",
        )
        actual = _verify_connection_binding(
            payload,
            project_root=self._require_project_root(),
        )
        if actual != self._connection_binding_digest:
            raise PrimitiveBasisLiveRuntimeError("The fixed Unity connection changed.")

    def _call_restore_request(self, checkpoint_id: str) -> dict[str, Any]:
        try:
            payload = self._callbacks.create_restore_request(checkpoint_id)
        except Exception as exc:  # noqa: BLE001
            raise PrimitiveBasisLiveRuntimeError(
                "The fixed restore request could not be created."
            ) from exc
        if not isinstance(payload, dict) or payload.get("ok") is not True:
            raise PrimitiveBasisLiveRuntimeError(
                "The fixed restore request could not be created."
            )
        return payload

    def _call_checkpoint_preview(self, checkpoint_id: str) -> dict[str, Any]:
        try:
            payload = self._callbacks.preview_checkpoint(checkpoint_id)
        except Exception as exc:  # noqa: BLE001
            raise PrimitiveBasisLiveRuntimeError("Checkpoint state could not be read.") from exc
        if not isinstance(payload, dict) or payload.get("ok") is not True:
            raise PrimitiveBasisLiveRuntimeError("Checkpoint state could not be read.")
        return payload

    def _require_running_phase(self, phase: str) -> None:
        if self._session.state != "running":
            raise PrimitiveBasisLiveRuntimeError("The fixed live run is not active.")
        # The receipt collector is the authoritative order check. This local
        # guard gives callers a stable error without exposing its internals.
        expected_counts = {
            "approval": 3,
            "checkpoint": 4,
            "apply": 5,
            "readback": 6,
            "restore_approval": 9,
            "restore_execution": 10,
            "baseline_comparison": 11,
        }
        if self._session.receipt_count != expected_counts[phase]:
            raise PrimitiveBasisLiveRuntimeError("The fixed live phase order is invalid.")

    def _require_project_root(self) -> Path:
        if self._project_root is None:
            raise PrimitiveBasisLiveRuntimeError("The fixed live project is unavailable.")
        return self._project_root


def _load_and_verify_fixture_set(
    project_root: Path,
    session: PrimitiveBasisLiveSession,
) -> FixtureSet:
    try:
        fixtures = load_fixture_set(
            project_root / FIXTURE_DESCRIPTOR_DIRECTORY,
            repository_root=project_root,
        )
    except (MatrixContractError, OSError, ValueError) as exc:
        raise PrimitiveBasisLiveRuntimeError("The fixed fixture set is invalid.") from exc
    if fixtures.descriptor_digest != session.fixture_set_descriptor_digest:
        raise PrimitiveBasisLiveRuntimeError("The fixed fixture descriptor set changed.")
    fixture = next(
        (item for item in fixtures.fixtures if item.scenario_id == MODEL_SCENARIO_ID),
        None,
    )
    if (
        fixture is None
        or fixture.descriptor_digest != session.fixture_descriptor_digest
        or fixture.materialized is not True
        or not fixture.digest
    ):
        raise PrimitiveBasisLiveRuntimeError("The fixed model-part fixture is unavailable.")
    if any(
        item.materialized
        for item in fixtures.fixtures
        if item.scenario_id != MODEL_SCENARIO_ID
    ):
        raise PrimitiveBasisLiveRuntimeError("Unexpected live fixture rows were materialized.")
    return fixtures


def _resolve_project_root(value: str) -> Path:
    candidate = Path(str(value or "").strip())
    if not candidate.is_absolute():
        raise PrimitiveBasisLiveRuntimeError("The disposable fixture project is invalid.")
    try:
        resolved = candidate.resolve(strict=True)
    except (OSError, RuntimeError, ValueError) as exc:
        raise PrimitiveBasisLiveRuntimeError("The disposable fixture project is invalid.") from exc
    if _is_reparse_point(resolved) or not resolved.is_dir():
        raise PrimitiveBasisLiveRuntimeError("The disposable fixture project is invalid.")
    for name in ("Assets", "Packages", "ProjectSettings", "VRCForgeFixture"):
        child = resolved / name
        if not child.is_dir() or _is_reparse_point(child):
            raise PrimitiveBasisLiveRuntimeError("The disposable fixture project is incomplete.")
    return resolved


def _verify_connection_binding(
    payload: Mapping[str, Any], *, project_root: Path
) -> str:
    digest = str(payload.get("connectionBindingDigest") or "")
    expected = {
        "ok": True,
        "schema": "vrcforge.primitive_basis_connection_binding.v1",
        "frozen": True,
        "projectPathDigest": _hash_text(_normalize_project_root(project_root)),
    }
    if (
        any(payload.get(key) != value for key, value in expected.items())
        or len(digest) != 64
        or any(character not in "0123456789abcdef" for character in digest)
    ):
        raise PrimitiveBasisLiveRuntimeError("The fixed Unity connection binding is invalid.")
    return digest


def compute_fixed_project_input_digest(project_root: Path) -> str:
    root = _resolve_project_root(str(project_root))
    contract = _read_json_object(root / FIXTURE_CONTRACT, 128 * 1024)
    if (
        contract.get("schema") != "vrcforge.primitive_basis_model_part_fixture.v1"
        or contract.get("scenarioId") != MODEL_SCENARIO_ID
        or contract.get("primitiveId") != MODEL_PRIMITIVE_ID
    ):
        raise PrimitiveBasisLiveRuntimeError("The fixed fixture contract is invalid.")

    project_files = contract.get("projectFiles")
    if not isinstance(project_files, list):
        raise PrimitiveBasisLiveRuntimeError("The fixed project file pins are invalid.")
    file_projection: list[dict[str, Any]] = []
    seen_paths: set[str] = set()
    for item in project_files:
        if not isinstance(item, Mapping):
            raise PrimitiveBasisLiveRuntimeError("The fixed project file pins are invalid.")
        relative = str(item.get("path") or "")
        expected_digest = str(item.get("sha256") or "")
        if (
            relative not in _EXPECTED_PROJECT_FILES
            or relative in seen_paths
            or len(expected_digest) != 64
            or any(character not in "0123456789abcdef" for character in expected_digest)
        ):
            raise PrimitiveBasisLiveRuntimeError("The fixed project file pins are invalid.")
        seen_paths.add(relative)
        actual_digest = _stable_file_digest(root.joinpath(*Path(relative).parts))
        if actual_digest != expected_digest:
            raise PrimitiveBasisLiveRuntimeError("A fixed project file changed.")
        file_projection.append({"path": relative, "sha256": actual_digest})
    if seen_paths != _EXPECTED_PROJECT_FILES:
        raise PrimitiveBasisLiveRuntimeError("The fixed project file set is incomplete.")

    required_packages = contract.get("requiredPackages")
    if not isinstance(required_packages, list):
        raise PrimitiveBasisLiveRuntimeError("The fixed package pins are invalid.")
    declared_packages: dict[str, str] = {}
    for item in required_packages:
        if not isinstance(item, Mapping):
            raise PrimitiveBasisLiveRuntimeError("The fixed package pins are invalid.")
        package_id = str(item.get("id") or "")
        version = str(item.get("version") or "")
        if (
            package_id in declared_packages
            or _REQUIRED_PACKAGE_VERSIONS.get(package_id) != version
            or item.get("provisioning") != "exact_artifact"
        ):
            raise PrimitiveBasisLiveRuntimeError("The fixed package pins are invalid.")
        declared_packages[package_id] = version
    if declared_packages != _REQUIRED_PACKAGE_VERSIONS:
        raise PrimitiveBasisLiveRuntimeError("The fixed package set is incomplete.")

    package_projection: list[dict[str, Any]] = []
    for package_id, version in sorted(declared_packages.items()):
        package_root = root / "Packages" / package_id
        if not package_root.is_dir() or _is_reparse_point(package_root):
            raise PrimitiveBasisLiveRuntimeError("A fixed package artifact is missing.")
        package_json = _read_json_object(package_root / "package.json", 1024 * 1024)
        if package_json.get("name") != package_id or str(package_json.get("version") or "") != version:
            raise PrimitiveBasisLiveRuntimeError("A fixed package artifact changed.")
        package_projection.append(
            {
                "id": package_id,
                "version": version,
                "treeDigest": _directory_inventory_digest(package_root),
            }
        )
    return _hash_json(
        {
            "contractDigest": _stable_file_digest(root / FIXTURE_CONTRACT),
            "projectFiles": sorted(file_projection, key=lambda item: str(item["path"])),
            "packages": package_projection,
            "editorToolTreeDigest": _directory_inventory_digest(
                root / FIXED_EDITOR_TOOL_TREE
            ),
            "editorToolRootMetaDigest": _stable_file_digest(
                root / FIXED_EDITOR_TOOL_TREE_META
            ),
        }
    )


def _require_fixed_project_inputs(project_root: Path) -> None:
    contract = _read_json_object(project_root / FIXTURE_CONTRACT, 128 * 1024)
    for item in contract.get("projectFiles") or []:
        if not isinstance(item, Mapping):
            raise PrimitiveBasisLiveRuntimeError("The fixed project file pins are invalid.")
        relative = str(item.get("path") or "")
        if relative not in _EXPECTED_PROJECT_FILES or _stable_file_digest(
            project_root.joinpath(*Path(relative).parts)
        ) != str(item.get("sha256") or ""):
            raise PrimitiveBasisLiveRuntimeError("A fixed project file changed.")
    for package_id, version in _REQUIRED_PACKAGE_VERSIONS.items():
        package_json = _read_json_object(
            project_root / "Packages" / package_id / "package.json", 1024 * 1024
        )
        if package_json.get("name") != package_id or str(package_json.get("version") or "") != version:
            raise PrimitiveBasisLiveRuntimeError("A fixed package artifact changed.")
    _directory_inventory_digest(project_root / FIXED_EDITOR_TOOL_TREE)
    _stable_file_digest(project_root / FIXED_EDITOR_TOOL_TREE_META)


def _directory_inventory_digest(root: Path) -> str:
    if not root.is_dir() or _is_reparse_point(root):
        raise PrimitiveBasisLiveRuntimeError("A fixed input tree is missing or unsafe.")
    inventory: list[dict[str, Any]] = []
    total_bytes = 0
    for current_root, directory_names, file_names in os.walk(root, followlinks=False):
        current = Path(current_root)
        for directory_name in list(directory_names):
            if _is_reparse_point(current / directory_name):
                raise PrimitiveBasisLiveRuntimeError("A fixed package contains a link.")
        for file_name in sorted(file_names):
            path = current / file_name
            if _is_reparse_point(path) or not path.is_file():
                raise PrimitiveBasisLiveRuntimeError("A fixed package contains an unsafe file.")
            size = path.stat().st_size
            total_bytes += size
            if len(inventory) >= _MAX_INVENTORY_FILES or total_bytes > _MAX_INVENTORY_BYTES:
                raise PrimitiveBasisLiveRuntimeError("A fixed package artifact is too large.")
            inventory.append(
                {
                    "path": path.relative_to(root).as_posix(),
                    "size": size,
                    "sha256": _stable_file_digest(path),
                }
            )
    inventory.sort(key=lambda item: str(item["path"]))
    return _hash_json(inventory)


def _load_ready_marker(project_root: Path, challenge_digest: str) -> dict[str, Any]:
    path = project_root / FIXTURE_READY_MARKER
    payload = _read_json_object(path, 32 * 1024)
    expected_fields = {
        "schema",
        "runIdDigest",
        "sceneGuid",
        "avatarPath",
        "componentHostPath",
        "mergeTargetPath",
    }
    if set(payload) != expected_fields or payload != {
        "schema": "vrcforge.primitive_basis_fixture_ready.v1",
        "runIdDigest": challenge_digest,
        "sceneGuid": EXPECTED_SCENE_GUID,
        "avatarPath": EXPECTED_AVATAR_ROOT,
        "componentHostPath": EXPECTED_COMPONENT_HOST,
        "mergeTargetPath": EXPECTED_MERGE_TARGET,
    }:
        raise PrimitiveBasisLiveRuntimeError("The fixed fixture ready marker is invalid.")
    return payload


def _verify_unity_fixture_identity(
    payload: Mapping[str, Any],
    *,
    project_root: Path,
    marker: Mapping[str, Any],
    expected_unity_executable_digest: str,
) -> None:
    expected = {
        "schema": "vrcforge.primitive_basis_unity_fixture.v1",
        "scenarioId": MODEL_SCENARIO_ID,
        "primitiveId": MODEL_PRIMITIVE_ID,
        "projectPathDigest": _hash_text(_normalize_project_root(project_root)),
        "unityVersion": EXPECTED_UNITY_VERSION,
        "batchMode": False,
        "sceneDirty": False,
        "activeScenePath": FIXTURE_SCENE.as_posix(),
        "activeSceneGuid": EXPECTED_SCENE_GUID,
        "readyMarkerDigest": _stable_file_digest(project_root / FIXTURE_READY_MARKER),
        "readyRunIdDigest": marker["runIdDigest"],
        "contractDigest": EXPECTED_CONTRACT_DIGEST,
        "baselineManifestDigest": EXPECTED_BASELINE_FILE_DIGEST,
        "sceneDigest": EXPECTED_SCENE_DIGEST,
        "avatarRootType": EXPECTED_AVATAR_ROOT_TYPE,
        "transformPaths": list(EXPECTED_TRANSFORM_PATHS),
        "rendererPath": EXPECTED_RENDERER_PATH,
        "rendererRootBonePath": EXPECTED_RENDERER_BONE,
        "rendererBonePaths": [EXPECTED_RENDERER_BONE],
        "componentHostPath": EXPECTED_COMPONENT_HOST,
        "mergeTargetPath": EXPECTED_MERGE_TARGET,
    }
    if any(payload.get(key) != value for key, value in expected.items()):
        raise PrimitiveBasisLiveRuntimeError("Unity fixture identity did not match.")
    if (
        payload.get("unityExecutableDigest") != expected_unity_executable_digest
        or not isinstance(payload.get("unityProcessStartedAtUtc"), str)
        or not str(payload.get("unityProcessStartedAtUtc") or "").strip()
    ):
        raise PrimitiveBasisLiveRuntimeError("Unity fixture process identity did not match.")


def _require_component_absent(payload: Mapping[str, Any]) -> None:
    if (
        payload.get("ok") is not True
        or payload.get("present") is not False
        or payload.get("count") != 0
        or payload.get("gameObjectPath") != EXPECTED_COMPONENT_HOST
    ):
        raise PrimitiveBasisLiveRuntimeError("The fixed component baseline is invalid.")


def _require_component_applied(payload: Mapping[str, Any]) -> None:
    if (
        payload.get("ok") is not True
        or payload.get("present") is not True
        or payload.get("count") != 1
        or payload.get("type") != EXPECTED_COMPONENT_TYPE
        or payload.get("gameObjectPath") != EXPECTED_COMPONENT_HOST
        or payload.get("avatarPath") != EXPECTED_AVATAR_ROOT
        or payload.get("sceneDirty") is not False
    ):
        raise PrimitiveBasisLiveRuntimeError("The fixed component readback is invalid.")
    references = payload.get("references")
    if not isinstance(references, list) or len(references) != 1:
        raise PrimitiveBasisLiveRuntimeError("The fixed component reference readback is invalid.")
    matches = [
        item
        for item in references
        if isinstance(item, Mapping)
        and str(item.get("member") or "").lower() == "mergetarget"
    ]
    if len(matches) != 1:
        raise PrimitiveBasisLiveRuntimeError("The fixed merge target readback is invalid.")
    match = matches[0]
    if (
        match.get("componentIndex") != 0
        or match.get("referencePath") != "Armature"
        or match.get("resolved") is not True
        or match.get("resolvedPath") != EXPECTED_MERGE_TARGET
    ):
        raise PrimitiveBasisLiveRuntimeError("The fixed merge target did not resolve.")


def _require_arguments_digest(
    approval: Mapping[str, Any], label: str
) -> str:
    digest = str(approval.get("argumentsDigest") or "").strip().lower()
    if len(digest) != 64 or any(character not in "0123456789abcdef" for character in digest):
        raise PrimitiveBasisLiveRuntimeError(f"The {label} digest is invalid.")
    return digest


def _require_pending_approval(
    payload: Mapping[str, Any], expected_tool: str
) -> Mapping[str, Any]:
    approval = _require_mapping(payload.get("approval"), "approval")
    if (
        payload.get("ok") is not True
        or payload.get("status") != "pending"
        or approval.get("status") != "pending"
        or approval.get("targetTool") != expected_tool
    ):
        raise PrimitiveBasisLiveRuntimeError("The supervised request was not pending.")
    _safe_id(approval.get("id"), "approval")
    return approval


def _compile_projection(payload: Mapping[str, Any]) -> dict[str, Any]:
    result = _mapping_or_empty(payload.get("result"))
    source: Mapping[str, Any] = (
        _mapping_or_empty(payload.get("payload"))
        or _mapping_or_empty(result.get("payload"))
        or payload
    )
    visited: set[int] = set()
    while isinstance(source, Mapping) and id(source) not in visited:
        visited.add(id(source))
        nested = next(
            (
                candidate
                for key in ("data", "result", "payload", "value")
                if isinstance((candidate := source.get(key)), Mapping)
            ),
            None,
        )
        if nested is None:
            break
        source = nested
    exit_code = result.get("exitCode", payload.get("exitCode", 0))
    error_count = source.get("errorCount")
    has_errors = source.get("hasErrors")
    is_compiling = source.get("isCompiling")
    if (
        type(error_count) is not int
        or type(has_errors) is not bool
        or type(is_compiling) is not bool
    ):
        text = str(result.get("stdout") or payload.get("stdout") or "")
        parsed_count = _read_scalar_line(text, "errorCount")
        parsed_errors = _read_scalar_line(text, "hasErrors")
        parsed_compiling = _read_scalar_line(text, "isCompiling")
        try:
            error_count = int(parsed_count)
        except (TypeError, ValueError):
            error_count = -1
        has_errors = str(parsed_errors).strip().lower() == "true"
        is_compiling = str(parsed_compiling).strip().lower() == "true"
    if type(exit_code) is not int:
        exit_code = -1
    if (
        type(error_count) is not int
        or error_count < 0
        or type(has_errors) is not bool
        or type(is_compiling) is not bool
    ):
        raise PrimitiveBasisLiveRuntimeError("Compile status was invalid.")
    return {
        "exitCode": exit_code,
        "errorCount": error_count,
        "hasErrors": has_errors,
        "isCompiling": is_compiling,
        "source": str(source.get("source") or ""),
        "capturedAt": str(source.get("capturedAt") or ""),
        "projectPathDigest": str(source.get("projectPathDigest") or ""),
        "unityProcessId": source.get("unityProcessId"),
        "unityProcessStartedAtUtc": str(
            source.get("unityProcessStartedAtUtc") or ""
        ),
        "unityExecutableDigest": str(source.get("unityExecutableDigest") or ""),
    }


def _read_scalar_line(text: str, key: str) -> str | None:
    prefix = key.lower() + ":"
    for line in text.splitlines():
        stripped = line.strip()
        if stripped.lower().startswith(prefix):
            return stripped.split(":", 1)[1].strip()
    return None


def _component_state_digest(project_root: Path, component: Mapping[str, Any]) -> str:
    return _hash_json(
        {
            "sceneDigest": _stable_file_digest(project_root / FIXTURE_SCENE),
            "component": _component_projection(component),
        }
    )


def _component_projection(payload: Mapping[str, Any]) -> dict[str, Any]:
    references = payload.get("references")
    safe_references = []
    if isinstance(references, list):
        safe_references = [
            {
                "componentIndex": item.get("componentIndex"),
                "member": item.get("member"),
                "referencePath": item.get("referencePath"),
                "resolved": item.get("resolved"),
                "resolvedPath": item.get("resolvedPath"),
            }
            for item in references
            if isinstance(item, Mapping)
        ]
    return {
        "gameObjectPath": payload.get("gameObjectPath"),
        "avatarPath": payload.get("avatarPath"),
        "present": payload.get("present"),
        "count": payload.get("count"),
        "type": payload.get("type"),
        "sceneDirty": payload.get("sceneDirty"),
        "references": safe_references,
    }


def _expected_component_projection(payload: Mapping[str, Any]) -> dict[str, Any]:
    return {
        "gameObjectPath": EXPECTED_COMPONENT_HOST,
        "avatarPath": EXPECTED_AVATAR_ROOT,
        "present": True,
        "count": 1,
        "type": EXPECTED_COMPONENT_TYPE,
        "sceneDirty": False,
        "references": [
            {
                "componentIndex": 0,
                "member": "mergeTarget",
                "referencePath": "Armature",
                "resolved": True,
                "resolvedPath": EXPECTED_MERGE_TARGET,
            }
        ],
    }


def _public_preview_projection(payload: Mapping[str, Any]) -> dict[str, Any]:
    return {
        "ok": payload.get("ok"),
        "preview": payload.get("preview"),
        "gameObjectPath": payload.get("gameObjectPath"),
        "avatarPath": payload.get("avatarPath"),
        "componentType": payload.get("componentType"),
        "existingCount": payload.get("existingCount"),
        "saveScene": payload.get("saveScene"),
        "sceneSaved": payload.get("sceneSaved"),
        "sceneDirty": payload.get("sceneDirty"),
        "references": payload.get("references"),
        "fields": payload.get("fields"),
        "warnings": payload.get("warnings"),
    }


def _checkpoint_projection(payload: Mapping[str, Any]) -> dict[str, Any]:
    unity_prepare = _mapping_or_empty(payload.get("unityPrepare"))
    return {
        "id": payload.get("id"),
        "ok": payload.get("ok"),
        "status": payload.get("status"),
        "strategy": payload.get("strategy"),
        "unityPrepare": {
            "ok": unity_prepare.get("ok"),
            **_unity_process_identity_projection(unity_prepare),
        },
    }


def _apply_result_projection(payload: Mapping[str, Any]) -> dict[str, Any]:
    return {
        "ok": payload.get("ok"),
        "action": payload.get("action"),
        "gameObjectPath": payload.get("gameObjectPath"),
        "componentType": payload.get("componentType"),
        "addedComponent": payload.get("addedComponent"),
        "sceneSaved": payload.get("sceneSaved"),
        "sceneDirty": payload.get("sceneDirty"),
    }


def _restore_result_projection(payload: Mapping[str, Any]) -> dict[str, Any]:
    unity_reload = _mapping_or_empty(payload.get("unityReload"))
    return {
        "ok": payload.get("ok"),
        "checkpointId": payload.get("checkpointId"),
        "restored": payload.get("restored"),
        "unityReloadOk": unity_reload.get("ok"),
        "unityReloadIdentity": _unity_process_identity_projection(unity_reload),
    }


def _unity_process_identity_projection(payload: Mapping[str, Any]) -> dict[str, Any]:
    return {
        "projectPathDigest": payload.get("projectPathDigest"),
        "unityProcessId": payload.get("unityProcessId"),
        "unityProcessStartedAtUtc": payload.get("unityProcessStartedAtUtc"),
        "unityExecutableDigest": payload.get("unityExecutableDigest"),
    }


def _project_inventory_digest(project_root: Path) -> str:
    inventory: list[dict[str, Any]] = []
    total_bytes = 0
    for root_name in _INVENTORY_ROOTS:
        root = project_root / root_name
        for current_root, directory_names, file_names in os.walk(root, followlinks=False):
            current = Path(current_root)
            for directory_name in list(directory_names):
                if _is_reparse_point(current / directory_name):
                    raise PrimitiveBasisLiveRuntimeError("The fixed project contains a link.")
            for file_name in sorted(file_names):
                path = current / file_name
                if _is_reparse_point(path) or not path.is_file():
                    raise PrimitiveBasisLiveRuntimeError("The fixed project contains an unsafe file.")
                relative = path.relative_to(project_root).as_posix()
                size = path.stat().st_size
                total_bytes += size
                if len(inventory) >= _MAX_INVENTORY_FILES or total_bytes > _MAX_INVENTORY_BYTES:
                    raise PrimitiveBasisLiveRuntimeError("The fixed project inventory is too large.")
                inventory.append(
                    {
                        "path": relative,
                        "size": size,
                        "sha256": _stable_file_digest(path),
                    }
                )
    inventory.sort(key=lambda item: str(item["path"]))
    return _hash_json(inventory)


def _read_json_object(path: Path, maximum_size: int) -> dict[str, Any]:
    if not path.is_file() or _is_reparse_point(path):
        raise PrimitiveBasisLiveRuntimeError("A fixed fixture file is unavailable.")
    try:
        raw = path.read_bytes()
        if len(raw) > maximum_size:
            raise PrimitiveBasisLiveRuntimeError("A fixed fixture file is too large.")
        payload = json.loads(raw.decode("utf-8-sig"))
    except (OSError, UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise PrimitiveBasisLiveRuntimeError("A fixed fixture file is invalid.") from exc
    if not isinstance(payload, dict):
        raise PrimitiveBasisLiveRuntimeError("A fixed fixture file is invalid.")
    return payload


def _stable_file_digest(path: Path) -> str:
    if not path.is_file() or _is_reparse_point(path):
        raise PrimitiveBasisLiveRuntimeError("A fixed fixture file is unavailable.")
    digest = hashlib.sha256()
    try:
        with path.open("rb") as handle:
            before = os.fstat(handle.fileno())
            for chunk in iter(lambda: handle.read(1024 * 1024), b""):
                digest.update(chunk)
            after = os.fstat(handle.fileno())
        current = path.stat()
    except OSError as exc:
        raise PrimitiveBasisLiveRuntimeError("A fixed fixture file could not be read.") from exc
    identity = lambda value: (
        value.st_dev,
        value.st_ino,
        value.st_size,
        value.st_mtime_ns,
    )
    if identity(before) != identity(after) or identity(after) != identity(current):
        raise PrimitiveBasisLiveRuntimeError("A fixed fixture file changed during inspection.")
    return digest.hexdigest()


def _is_reparse_point(path: Path) -> bool:
    try:
        info = path.lstat()
    except OSError:
        return False
    reparse_flag = getattr(stat, "FILE_ATTRIBUTE_REPARSE_POINT", 0x400)
    return path.is_symlink() or bool(
        getattr(info, "st_file_attributes", 0) & reparse_flag
    )


def _normalize_project_root(path: Path) -> str:
    return str(path.resolve()).replace("\\", "/").rstrip("/").lower()


def _hash_text(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


def _hash_json(value: Any) -> str:
    return hashlib.sha256(
        json.dumps(
            value,
            ensure_ascii=True,
            sort_keys=True,
            separators=(",", ":"),
        ).encode("utf-8")
    ).hexdigest()


def _derived_id(prefix: str, digest: str) -> str:
    return f"{prefix}-{digest[:32]}"


def _safe_id(value: Any, label: str) -> str:
    text = str(value or "").strip()
    if not text or len(text) > 128 or any(
        not (character.isalnum() or character in "._-") for character in text
    ):
        raise PrimitiveBasisLiveRuntimeError(f"The {label} id is invalid.")
    return text


def _require_mapping(value: Any, label: str) -> Mapping[str, Any]:
    if not isinstance(value, Mapping):
        raise PrimitiveBasisLiveRuntimeError(f"The {label} payload is invalid.")
    return value


def _mapping_or_empty(value: Any) -> Mapping[str, Any]:
    return value if isinstance(value, Mapping) else {}


def _require_tcp_port_released(port: int) -> None:
    probe = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    try:
        if os.name == "nt":
            probe.setsockopt(socket.SOL_SOCKET, socket.SO_EXCLUSIVEADDRUSE, 1)
        probe.bind(("127.0.0.1", int(port)))
    except OSError as exc:
        code = int(getattr(exc, "winerror", 0) or getattr(exc, "errno", 0) or 0)
        if code in {48, 98, 10048}:
            raise PrimitiveBasisLiveRuntimeError(
                "The fixed fixture bridge port is still in use."
            ) from exc
        raise PrimitiveBasisLiveRuntimeError(
            "The fixed fixture bridge port release could not be verified."
        ) from exc
    finally:
        probe.close()


def _require_process_exited(process_id: int) -> None:
    if process_id <= 0:
        raise PrimitiveBasisLiveRuntimeError("The fixture Unity process identity is invalid.")
    if os.name == "nt":
        try:
            import ctypes

            process_query_limited_information = 0x1000
            still_active = 259
            handle = ctypes.windll.kernel32.OpenProcess(
                process_query_limited_information,
                False,
                int(process_id),
            )
            if not handle:
                error = int(ctypes.windll.kernel32.GetLastError())
                if error == 87:
                    return
                raise PrimitiveBasisLiveRuntimeError(
                    "The fixture Unity process exit could not be verified."
                )
            try:
                exit_code = ctypes.c_ulong()
                if not ctypes.windll.kernel32.GetExitCodeProcess(
                    handle, ctypes.byref(exit_code)
                ):
                    raise PrimitiveBasisLiveRuntimeError(
                        "The fixture Unity process exit could not be verified."
                    )
                if int(exit_code.value) == still_active:
                    raise PrimitiveBasisLiveRuntimeError(
                        "The fixture Unity process is still running."
                    )
                return
            finally:
                ctypes.windll.kernel32.CloseHandle(handle)
        except PrimitiveBasisLiveRuntimeError:
            raise
        except (AttributeError, OSError, ValueError) as exc:
            raise PrimitiveBasisLiveRuntimeError(
                "The fixture Unity process exit could not be verified."
            ) from exc
    try:
        os.kill(int(process_id), 0)
    except ProcessLookupError:
        return
    except (PermissionError, OSError, ValueError) as exc:
        raise PrimitiveBasisLiveRuntimeError(
            "The fixture Unity process exit could not be verified."
        ) from exc
    raise PrimitiveBasisLiveRuntimeError("The fixture Unity process is still running.")
