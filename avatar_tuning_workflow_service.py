from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Callable, Protocol


class RequestSupervisedUnityWritePort(Protocol):
    def __call__(
        self,
        target_tool: str,
        request: Any,
        *,
        reason: str,
        preview_callback: Callable[[], dict[str, Any]] | None = None,
        allow_mock_execute: bool = False,
        extra_arguments: dict[str, Any] | None = None,
    ) -> dict[str, Any]: ...


class PreparedWritePort(Protocol):
    def __call__(
        self,
        arguments: dict[str, Any],
        preview: Any,
    ) -> tuple[dict[str, Any], Any]: ...


@dataclass(frozen=True, slots=True)
class AvatarTuningWorkflowPorts:
    """Frozen read, persistence and supervised-write capabilities for tuning."""

    scan_scene_avatars: Callable[[Any], dict[str, Any]]
    read_avatars: Callable[[Any], dict[str, Any]]
    read_avatar_blendshapes: Callable[[Any], dict[str, Any]]
    run_face_tuning: Callable[[Any, bool], dict[str, Any]]
    preview_manual_blendshapes: Callable[[Any], dict[str, Any]]
    preview_agent_blendshape_apply: Callable[[dict[str, Any]], dict[str, Any]]
    request_supervised_write: RequestSupervisedUnityWritePort

    load_history: Callable[[], dict[str, Any]]
    load_presets: Callable[[], dict[str, Any]]
    load_locked_blendshapes: Callable[[str], list[dict[str, Any]]]
    current_avatar_path: Callable[[], str]
    create_preset: Callable[[Any], dict[str, Any]]
    rename_preset: Callable[[str, Any], dict[str, Any]]
    duplicate_preset: Callable[[str, Any], dict[str, Any]]
    delete_preset: Callable[[str], dict[str, Any]]
    update_locks: Callable[[Any], dict[str, Any]]
    ai_select_locks: Callable[[Any], dict[str, Any]]
    preview_saved_history: Callable[[str, Any], dict[str, Any]]
    preview_saved_preset: Callable[[str, Any], dict[str, Any]]


@dataclass(frozen=True, slots=True)
class AvatarTuningApprovedWriteHandlers:
    """Prepared Unity writers held only by the supervised approval registry."""

    prepare_manual_apply: PreparedWritePort
    execute_manual_apply: Callable[[dict[str, Any]], dict[str, Any]]
    prepare_manual_undo: PreparedWritePort
    execute_manual_undo: Callable[[dict[str, Any]], dict[str, Any]]
    prepare_face_tuning: PreparedWritePort
    execute_face_tuning: Callable[[dict[str, Any]], dict[str, Any]]
    prepare_reapply_history: PreparedWritePort
    execute_reapply_history: Callable[[dict[str, Any]], dict[str, Any]]
    prepare_apply_preset: PreparedWritePort
    execute_apply_preset: Callable[[dict[str, Any]], dict[str, Any]]


class AvatarTuningWorkflowService:
    """Own face/avatar tuning read, local-state and approval-request orchestration.

    The service can read tuning stores and create approval requests, but it owns
    neither a Unity writer nor an approval/checkpoint store. Persisted preset and
    lock mutations remain explicit supplied capabilities because they are local
    app state, not Unity project writes.
    """

    def __init__(self, ports: AvatarTuningWorkflowPorts) -> None:
        self._ports = ports

    def scan_scene_avatars(self, request: Any) -> dict[str, Any]:
        return self._ports.scan_scene_avatars(request)

    def read_avatars(self, request: Any) -> dict[str, Any]:
        return self._ports.read_avatars(request)

    def read_avatar_blendshapes(self, request: Any) -> dict[str, Any]:
        return self._ports.read_avatar_blendshapes(request)

    def plan_face_tuning(self, request: Any) -> dict[str, Any]:
        return self._ports.run_face_tuning(request, False)

    def preview_agent_blendshape_apply(
        self,
        params: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        return self._ports.preview_agent_blendshape_apply(params or {})

    def request_face_tuning(self, request: Any) -> dict[str, Any]:
        return self._ports.request_supervised_write(
            "vrcforge_run_face_tuning",
            request,
            reason="Apply the generated face-tuning plan to the selected Unity avatar.",
            preview_callback=lambda: self._ports.run_face_tuning(request, True),
            allow_mock_execute=True,
        )

    def request_manual_blendshape_apply(self, request: Any) -> dict[str, Any]:
        return self._ports.request_supervised_write(
            "vrcforge_apply_blendshapes",
            request,
            reason="Apply the selected Blendshape adjustments to the Unity avatar.",
            preview_callback=lambda: self._ports.preview_manual_blendshapes(request),
            allow_mock_execute=True,
        )

    def request_manual_blendshape_undo(self, request: Any) -> dict[str, Any]:
        return self._ports.request_supervised_write(
            "vrcforge_undo_blendshapes",
            request,
            reason="Restore the previous approved Blendshape values for the selected avatar.",
        )

    def list_tuning_history(self, avatar_path: str | None = None) -> dict[str, Any]:
        store = self._ports.load_history()
        records = list(store.get("records") or [])
        if avatar_path:
            records = [
                record
                for record in records
                if (
                    record.get("avatar_path") == avatar_path
                    or record.get("avatar_name") == avatar_path
                )
            ]
        return {"ok": True, "records": records, "count": len(records)}

    def request_reapply_tuning_history(self, history_id: str, request: Any) -> dict[str, Any]:
        return self._ports.request_supervised_write(
            "vrcforge_reapply_tuning_history",
            request,
            reason="Reapply the selected saved face-tuning history record to Unity.",
            preview_callback=lambda: self._ports.preview_saved_history(history_id, request),
            allow_mock_execute=True,
            extra_arguments={"historyId": history_id},
        )

    def list_tuning_presets(self, avatar_path: str | None = None) -> dict[str, Any]:
        store = self._ports.load_presets()
        presets = list(store.get("presets") or [])
        if avatar_path:
            presets = [
                preset
                for preset in presets
                if (
                    preset.get("avatar_path") == avatar_path
                    or preset.get("avatar_name") == avatar_path
                )
            ]
        return {"ok": True, "presets": presets, "count": len(presets)}

    def create_tuning_preset(self, request: Any) -> dict[str, Any]:
        return self._ports.create_preset(request)

    def request_apply_tuning_preset(self, preset_id: str, request: Any) -> dict[str, Any]:
        return self._ports.request_supervised_write(
            "vrcforge_apply_tuning_preset",
            request,
            reason="Apply the selected saved face-tuning preset to Unity.",
            preview_callback=lambda: self._ports.preview_saved_preset(preset_id, request),
            allow_mock_execute=True,
            extra_arguments={"presetId": preset_id},
        )

    def rename_tuning_preset(self, preset_id: str, request: Any) -> dict[str, Any]:
        return self._ports.rename_preset(preset_id, request)

    def duplicate_tuning_preset(self, preset_id: str, request: Any) -> dict[str, Any]:
        return self._ports.duplicate_preset(preset_id, request)

    def delete_tuning_preset(self, preset_id: str) -> dict[str, Any]:
        return self._ports.delete_preset(preset_id)

    def read_tuning_locks(self, avatar_path: str | None = None) -> dict[str, Any]:
        resolved_avatar = avatar_path or self._ports.current_avatar_path()
        locked = self._ports.load_locked_blendshapes(resolved_avatar)
        return {
            "ok": True,
            "avatarPath": resolved_avatar,
            "lockedBlendshapes": locked,
            "count": len(locked),
        }

    def update_tuning_locks(self, request: Any) -> dict[str, Any]:
        return self._ports.update_locks(request)

    def ai_select_tuning_locks(self, request: Any) -> dict[str, Any]:
        return self._ports.ai_select_locks(request)
