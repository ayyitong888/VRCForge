from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Callable, Protocol


class WardrobeOutfitWorkflowError(RuntimeError):
    def __init__(self, message: str, status_code: int = 400) -> None:
        super().__init__(message)
        self.status_code = status_code


class InspectOutfitPackagePort(Protocol):
    def __call__(self, package_path: str, *, max_entries: int = 5000) -> dict[str, Any]: ...


class BuildOutfitImportPlanPort(Protocol):
    def __call__(
        self,
        *,
        package_path: str,
        project_path: str | None = None,
        target_folder: str | None = None,
        selected_unitypackage: str | None = None,
        selected_prefab: str | None = None,
        base_avatar_name: str | None = None,
        max_entries: int = 5000,
    ) -> dict[str, Any]: ...


class CreateApplyRequestPort(Protocol):
    def __call__(
        self,
        params: dict[str, Any],
        *,
        internal_wrapper: bool = False,
    ) -> dict[str, Any]: ...


class PreparedWritePort(Protocol):
    def __call__(
        self,
        arguments: dict[str, Any],
        preview: Any,
    ) -> tuple[dict[str, Any], Any]: ...


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


@dataclass(frozen=True, slots=True)
class WardrobeOutfitWorkflowPorts:
    """Capabilities supplied to the wardrobe/outfit owner at composition time.

    Read and preview ports may inspect the selected Unity project. This owner has no
    project-write, checkpoint, approval-store, process, transport, or auth-token
    authority of its own.
    """

    selected_project_path: Callable[[], str]
    inspect_package: InspectOutfitPackagePort
    build_import_plan: BuildOutfitImportPlanPort
    create_apply_request: CreateApplyRequestPort
    request_supervised_write: RequestSupervisedUnityWritePort

    scan_avatar_items: Callable[[dict[str, Any]], dict[str, Any]]
    scan_avatar_controls: Callable[[dict[str, Any]], dict[str, Any]]
    scan_wardrobe: Callable[[dict[str, Any]], dict[str, Any]]
    scan_clothes: Callable[[Any], dict[str, Any]]
    generate_clothing_fx: Callable[[Any], dict[str, Any]]
    preview_apply_clothing_fx: Callable[[Any], dict[str, Any]]

    preview_setup_outfit: Callable[[dict[str, Any]], dict[str, Any]]
    preview_add_wardrobe_outfit: Callable[[dict[str, Any]], dict[str, Any]]
    preview_add_outfit_part: Callable[[dict[str, Any]], dict[str, Any]]
    preview_add_modular_avatar_component: Callable[[dict[str, Any]], dict[str, Any]]
    preview_manage_wardrobe: Callable[[dict[str, Any]], dict[str, Any]]
    preview_create_wardrobe: Callable[[dict[str, Any]], dict[str, Any]]

    preview_add_outfit: Callable[[dict[str, Any]], dict[str, Any]]


@dataclass(frozen=True, slots=True)
class WardrobeOutfitApprovedWriteHandlers:
    """Write capabilities handed only to the supervised approval registry."""

    setup_outfit: Callable[[dict[str, Any]], dict[str, Any]]
    add_wardrobe_outfit: Callable[[dict[str, Any]], dict[str, Any]]
    add_outfit_part: Callable[[dict[str, Any]], dict[str, Any]]
    add_modular_avatar_component: Callable[[dict[str, Any]], dict[str, Any]]
    manage_wardrobe: Callable[[dict[str, Any]], dict[str, Any]]
    create_wardrobe: Callable[[dict[str, Any]], dict[str, Any]]
    prepare_add_outfit: PreparedWritePort
    add_outfit: Callable[[dict[str, Any]], dict[str, Any]]
    prepare_import_package: PreparedWritePort
    import_package: Callable[[dict[str, Any]], dict[str, Any]]


class WardrobeOutfitWorkflowService:
    """Own wardrobe/outfit read, preview and approval-request orchestration."""

    def __init__(self, ports: WardrobeOutfitWorkflowPorts) -> None:
        self._ports = ports

    @staticmethod
    def _params(params: dict[str, Any] | None) -> dict[str, Any]:
        return params or {}

    @staticmethod
    def _text(params: dict[str, Any], camel: str, snake: str) -> str:
        return str(params.get(camel) or params.get(snake) or "").strip()

    def inspect_outfit_package(self, params: dict[str, Any] | None = None) -> dict[str, Any]:
        normalized = self._params(params)
        package_path = self._text(normalized, "packagePath", "package_path")
        if not package_path:
            raise WardrobeOutfitWorkflowError("packagePath is required.")
        max_entries = int(normalized.get("maxEntries") or normalized.get("max_entries") or 5000)
        return self._ports.inspect_package(package_path, max_entries=max_entries)

    def plan_outfit_import(self, params: dict[str, Any] | None = None) -> dict[str, Any]:
        normalized = self._params(params)
        package_path = self._text(normalized, "packagePath", "package_path")
        if not package_path:
            raise WardrobeOutfitWorkflowError("packagePath is required.")
        project_path = self._text(normalized, "projectPath", "project_path")
        if not project_path:
            project_path = str(self._ports.selected_project_path() or "").strip()
        return self._ports.build_import_plan(
            package_path=package_path,
            project_path=project_path or None,
            target_folder=self._text(normalized, "targetFolder", "target_folder") or None,
            selected_unitypackage=self._text(
                normalized,
                "selectedUnityPackage",
                "selected_unitypackage",
            )
            or None,
            selected_prefab=self._text(normalized, "selectedPrefab", "selected_prefab") or None,
            base_avatar_name=self._text(normalized, "baseAvatarName", "base_avatar_name") or None,
            max_entries=int(normalized.get("maxEntries") or normalized.get("max_entries") or 5000),
        )

    def request_outfit_import(
        self,
        params: dict[str, Any] | None = None,
        *,
        agent_name: str = "desktop-agent",
    ) -> dict[str, Any]:
        normalized = dict(self._params(params))
        preview = self.plan_outfit_import(normalized)
        plan_payload = preview.get("plan") if isinstance(preview.get("plan"), dict) else {}
        if not preview.get("ok") or not plan_payload.get("readyToApply"):
            raise WardrobeOutfitWorkflowError(
                str(preview.get("error") or "Outfit import plan is not ready to apply.")
            )
        return self._ports.create_apply_request(
            {
                "target_tool": "vrcforge_import_outfit_package",
                "arguments": normalized,
                "reason": "Import outfit package through VRCForge supervised Golden Path.",
                "preview": preview,
                "agent_name": agent_name,
            }
        )

    def scan_avatar_items(self, params: dict[str, Any] | None = None) -> dict[str, Any]:
        return self._ports.scan_avatar_items(self._params(params))

    def scan_avatar_controls(self, params: dict[str, Any] | None = None) -> dict[str, Any]:
        return self._ports.scan_avatar_controls(self._params(params))

    def scan_wardrobe(self, params: dict[str, Any] | None = None) -> dict[str, Any]:
        return self._ports.scan_wardrobe(self._params(params))

    def scan_clothes(self, request: Any) -> dict[str, Any]:
        return self._ports.scan_clothes(request)

    def request_toggle_clothing(self, request: Any) -> dict[str, Any]:
        return self._ports.request_supervised_write(
            "vrcforge_toggle_scene_object",
            request,
            reason="Change the selected Unity clothing object's active state.",
        )

    def generate_clothing_fx(self, request: Any) -> dict[str, Any]:
        return self._ports.generate_clothing_fx(request)

    def request_apply_clothing_fx(self, request: Any) -> dict[str, Any]:
        return self._ports.request_supervised_write(
            "vrcforge_apply_clothing_fx",
            request,
            reason="Author the planned clothing FX assets in the selected Unity project.",
            preview_callback=lambda: self._ports.preview_apply_clothing_fx(request),
        )

    def preview_setup_outfit(self, params: dict[str, Any] | None = None) -> dict[str, Any]:
        return self._ports.preview_setup_outfit(self._params(params))

    def preview_add_wardrobe_outfit(self, params: dict[str, Any] | None = None) -> dict[str, Any]:
        return self._ports.preview_add_wardrobe_outfit(self._params(params))

    def preview_add_outfit_part(self, params: dict[str, Any] | None = None) -> dict[str, Any]:
        return self._ports.preview_add_outfit_part(self._params(params))

    def preview_add_modular_avatar_component(
        self,
        params: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        return self._ports.preview_add_modular_avatar_component(self._params(params))

    def preview_manage_wardrobe(self, params: dict[str, Any] | None = None) -> dict[str, Any]:
        return self._ports.preview_manage_wardrobe(self._params(params))

    def preview_create_wardrobe(self, params: dict[str, Any] | None = None) -> dict[str, Any]:
        return self._ports.preview_create_wardrobe(self._params(params))

    def preview_add_outfit(self, params: dict[str, Any] | None = None) -> dict[str, Any]:
        return self._ports.preview_add_outfit(self._params(params))
