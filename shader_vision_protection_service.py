"""Typed application owner for Shader, Vision audit, and protection entrypoints.

The controller deliberately owns only user-facing read/plan/request routing.
It cannot execute an approved Unity write or call the private protection addon:
those handlers remain in the Gateway approval/checkpoint execution registry and
must be composed separately after approval.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Callable, Protocol


Payload = dict[str, Any]


SHADER_APPLY_TOOL = "vrcforge_apply_shader_tuning"
SHADER_RESTORE_TOOL = "vrcforge_restore_shader_tuning"
SHADER_HISTORY_REAPPLY_TOOL = "vrcforge_reapply_shader_tuning_history"
SHADER_PRESET_APPLY_TOOL = "vrcforge_apply_shader_tuning_preset"
VISION_CAPTURE_TOOL = "vrcforge_capture_screenshot"
VISION_MULTI_CAPTURE_TOOL = "vrcforge_capture_multi_screenshot"


class SupervisedWriteRequestPort(Protocol):
    """Create an approval request; this port never executes the target tool."""

    def __call__(
        self,
        target_tool: str,
        request: Any,
        *,
        reason: str,
        extra_arguments: Payload | None = None,
    ) -> Payload: ...


class SupervisedCaptureRequestPort(Protocol):
    """Create a capture approval request; this port cannot execute a capture."""

    def __call__(
        self,
        target_tool: str,
        request: Any,
        *,
        reason: str,
    ) -> Payload: ...


@dataclass(frozen=True, slots=True)
class ShaderWorkflowPorts:
    scan: Callable[[Any], Payload]
    plan: Callable[[Any], Payload]
    preview_apply: Callable[[Payload], Payload]
    preview_material_assignment: Callable[[Payload], Payload]
    request_supervised_write: SupervisedWriteRequestPort
    load_history_store: Callable[[], Payload]
    load_preset_store: Callable[[], Payload]
    create_preset: Callable[[Any], Payload]
    rename_preset: Callable[[str, Any], Payload]
    duplicate_preset: Callable[[str, Any], Payload]
    delete_preset: Callable[[str], Payload]
    current_avatar_path: Callable[[], str]
    load_locks: Callable[[str], Payload]
    update_locks: Callable[[Any], Payload]
    review_vision: Callable[[Any], Payload]


@dataclass(frozen=True, slots=True)
class VisionAuditWorkflowPorts:
    request_supervised_capture: SupervisedCaptureRequestPort
    read_capture_status: Callable[[Any], Payload]
    request_supervised_multi_capture: SupervisedCaptureRequestPort
    audit_capture: Callable[[Any], Payload]
    audit_multi_capture: Callable[[Any], Payload]


@dataclass(frozen=True, slots=True)
class ProtectionWorkflowPorts:
    research_report: Callable[[Any], Payload]
    scan: Callable[[Any], Payload]
    plan: Callable[[Any], Payload]
    preview: Callable[[Any], Payload]
    addon_status: Callable[[], Payload]
    request_supervised_apply: Callable[[Payload, str, str], Payload]
    request_supervised_remove: Callable[[Payload, str], Payload]


class ShaderVisionProtectionService:
    """Own the stable route/MCP workflows without owning a direct-write lane."""

    def __init__(
        self,
        shader: ShaderWorkflowPorts,
        vision: VisionAuditWorkflowPorts,
        protection: ProtectionWorkflowPorts,
    ) -> None:
        self._shader = shader
        self._vision = vision
        self._protection = protection

    # Shader read/plan and approval-request entrypoints.
    def scan_shader_materials(self, request: Any) -> Payload:
        return self._shader.scan(request)

    def generate_shader_material_plan(self, request: Any) -> Payload:
        return self._shader.plan(request)

    def preview_shader_apply(self, payload: Payload) -> Payload:
        return self._shader.preview_apply(payload)

    def preview_material_shader_assignment(self, payload: Payload) -> Payload:
        return self._shader.preview_material_assignment(payload)

    def request_shader_material_apply(self, request: Any) -> Payload:
        return self._shader.request_supervised_write(
            SHADER_APPLY_TOOL,
            request,
            reason="Apply the validated shader/material tuning plan to Unity.",
        )

    def request_shader_material_restore(self, request: Any) -> Payload:
        return self._shader.request_supervised_write(
            SHADER_RESTORE_TOOL,
            request,
            reason="Restore the last approved shader/material tuning undo point.",
        )

    def read_shader_tuning_history(self, avatar_path: str | None = None) -> Payload:
        records = list(self._shader.load_history_store().get("records") or [])
        if avatar_path:
            records = [
                record
                for record in records
                if record.get("avatar_path") == avatar_path or record.get("avatar_name") == avatar_path
            ]
        return {"ok": True, "records": records, "count": len(records)}

    def request_shader_history_reapply(self, history_id: str, request: Any) -> Payload:
        return self._shader.request_supervised_write(
            SHADER_HISTORY_REAPPLY_TOOL,
            request,
            reason="Reapply the selected saved shader tuning history record to Unity.",
            extra_arguments={"historyId": history_id},
        )

    def read_shader_tuning_presets(self, avatar_path: str | None = None) -> Payload:
        presets = list(self._shader.load_preset_store().get("presets") or [])
        if avatar_path:
            presets = [
                preset
                for preset in presets
                if preset.get("avatar_path") == avatar_path or preset.get("avatar_name") == avatar_path
            ]
        return {"ok": True, "presets": presets, "count": len(presets)}

    def create_shader_tuning_preset(self, request: Any) -> Payload:
        return self._shader.create_preset(request)

    def request_shader_preset_apply(self, preset_id: str, request: Any) -> Payload:
        return self._shader.request_supervised_write(
            SHADER_PRESET_APPLY_TOOL,
            request,
            reason="Apply the selected saved shader tuning preset to Unity.",
            extra_arguments={"presetId": preset_id},
        )

    def rename_shader_tuning_preset(self, preset_id: str, request: Any) -> Payload:
        return self._shader.rename_preset(preset_id, request)

    def duplicate_shader_tuning_preset(self, preset_id: str, request: Any) -> Payload:
        return self._shader.duplicate_preset(preset_id, request)

    def delete_shader_tuning_preset(self, preset_id: str) -> Payload:
        return self._shader.delete_preset(preset_id)

    def read_shader_tuning_locks(self, avatar_path: str | None = None) -> Payload:
        resolved_avatar = avatar_path or self._shader.current_avatar_path()
        return {"ok": True, "avatarPath": resolved_avatar, **self._shader.load_locks(resolved_avatar)}

    def update_shader_tuning_locks(self, request: Any) -> Payload:
        return self._shader.update_locks(request)

    def review_shader_material_vision(self, request: Any) -> Payload:
        return self._shader.review_vision(request)

    # Vision capture writes remain approval requests; audits/status are reads.
    def request_avatar_screenshot(self, request: Any) -> Payload:
        return self._vision.request_supervised_capture(
            VISION_CAPTURE_TOOL,
            request,
            reason="Capture one approved Unity scene-view artifact.",
        )

    def read_vision_capture_status(self, request: Any) -> Payload:
        return self._vision.read_capture_status(request)

    def request_avatar_multi_screenshot(self, request: Any) -> Payload:
        return self._vision.request_supervised_multi_capture(
            VISION_MULTI_CAPTURE_TOOL,
            request,
            reason="Capture approved fixed-angle Unity scene-view artifacts.",
        )

    def audit_avatar_screenshot(self, request: Any) -> Payload:
        return self._vision.audit_capture(request)

    def audit_avatar_multi_screenshot(self, request: Any) -> Payload:
        return self._vision.audit_multi_capture(request)

    # Protection reads/plans are separate from supervised apply/remove requests.
    def build_protection_research_report(self, request: Any) -> Payload:
        return self._protection.research_report(request)

    def scan_protection_candidates(self, request: Any) -> Payload:
        return self._protection.scan(request)

    def plan_protection(self, request: Any) -> Payload:
        return self._protection.plan(request)

    def preview_protection(self, request: Any) -> Payload:
        return self._protection.preview(request)

    def read_protection_addon_status(self) -> Payload:
        return self._protection.addon_status()

    def request_protection_apply(
        self,
        payload: Payload,
        target_shader_family: str,
        *,
        agent_name: str,
    ) -> Payload:
        return self._protection.request_supervised_apply(payload, target_shader_family, agent_name)

    def request_protection_remove(self, payload: Payload, *, agent_name: str) -> Payload:
        return self._protection.request_supervised_remove(payload, agent_name)
