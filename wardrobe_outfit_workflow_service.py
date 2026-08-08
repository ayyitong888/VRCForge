from __future__ import annotations

import copy
from dataclasses import dataclass
from typing import Any, Callable, Protocol


class WardrobeOutfitWorkflowError(RuntimeError):
    def __init__(self, message: str, status_code: int = 400) -> None:
        super().__init__(message)
        self.status_code = status_code


class ClothingFxLogPort(Protocol):
    def __call__(
        self,
        level: str,
        scope: str,
        message: str,
        data: dict[str, Any] | None = None,
    ) -> None: ...


@dataclass(frozen=True, slots=True)
class ClothingFxReadPorts:
    """Read-only capabilities for clothing discovery and FX blueprint shaping."""

    load_settings: Callable[[Any], Any]
    current_avatar_path: Callable[[], str]
    scan_controls: Callable[[Any, str | None], dict[str, Any]]
    build_blueprint: Callable[[Any, str | None], dict[str, Any]]
    build_apply_preview: Callable[[str | None, list[dict[str, Any]]], str]
    ensure_list: Callable[[Any, str], list[Any]]
    log: ClothingFxLogPort


class ClothingFxReadService:
    """Own clothing scan and FX blueprint reads without project-write authority."""

    def __init__(self, ports: ClothingFxReadPorts) -> None:
        self._ports = ports

    def scan_clothes(self, request: Any) -> dict[str, Any]:
        try:
            settings = self._ports.load_settings(request)
            avatar_path = request.avatar_path or self._ports.current_avatar_path()
            payload = self._ports.scan_controls(settings, avatar_path)
            clothes = self._ports.ensure_list(
                payload.get("items") or payload.get("clothes") or [],
                "avatar menu/parameter scan",
            )
            self._ports.log(
                "info",
                "fx",
                "Avatar menu/parameter scan completed.",
                {"avatarPath": avatar_path, "count": len(clothes)},
            )
            return {
                "ok": True,
                "avatarPath": avatar_path,
                "clothes": clothes,
                "count": len(clothes),
                "jsonPath": payload.get("jsonPath"),
            }
        except RuntimeError as exc:
            self._ports.log(
                "error",
                "fx",
                "Failed to scan clothing objects.",
                {"error": str(exc)},
            )
            raise

    def generate_clothing_fx(self, request: Any) -> dict[str, Any]:
        try:
            settings = self._ports.load_settings(request)
            avatar_path = request.avatar_path or self._ports.current_avatar_path()
            payload = self._ports.build_blueprint(settings, avatar_path)
            self._ports.log(
                "success",
                "fx",
                "Clothing FX blueprint generated.",
                {
                    "avatarPath": avatar_path,
                    "itemCount": len(payload.get("items") or []),
                },
            )
            return {"ok": True, "avatarPath": avatar_path, "fxBlueprint": payload}
        except RuntimeError as exc:
            self._ports.log(
                "error",
                "fx",
                "Failed to generate clothing FX blueprint.",
                {"error": str(exc)},
            )
            raise

    def preview_apply_clothing_fx(self, request: Any) -> dict[str, Any]:
        try:
            self._ports.load_settings(request)
            avatar_path = request.avatar_path or self._ports.current_avatar_path()
            items = request.items
            if not items:
                raise RuntimeError(
                    "No clothing items provided. Run /api/clothes/scan or "
                    "/api/clothes/generate-fx first."
                )
            apply_payload = self._ports.build_apply_preview(avatar_path, items)
            self._ports.log(
                "info",
                "fx",
                "Clothing FX apply payload generated (dry-run).",
                {"avatarPath": avatar_path, "itemCount": len(items)},
            )
            return {
                "ok": True,
                "avatarPath": avatar_path,
                "dryRun": True,
                "applyPayload": apply_payload,
                "itemCount": len(items),
            }
        except RuntimeError as exc:
            self._ports.log(
                "error",
                "fx",
                "Failed to apply clothing FX.",
                {"error": str(exc)},
            )
            raise


@dataclass(frozen=True, slots=True)
class WardrobeArtifactReadPorts:
    """Three fixed Wardrobe reads supplied at composition time.

    Each port seals its Unity read tool and artifact scope. The owner receives no
    generic Unity tool, settings, filesystem, write, approval, or checkpoint
    capability.
    """

    scan_avatar_items: Callable[[dict[str, Any]], dict[str, Any]]
    scan_avatar_controls: Callable[[dict[str, Any]], dict[str, Any]]
    scan_wardrobe: Callable[[dict[str, Any]], dict[str, Any]]


class WardrobeArtifactReadService:
    """Own Wardrobe avatar-item, control, and wardrobe artifact reads."""

    def __init__(self, ports: WardrobeArtifactReadPorts) -> None:
        self._ports = ports

    def scan_avatar_items(self, params: dict[str, Any] | None = None) -> dict[str, Any]:
        normalized = params or {}
        return self._ports.scan_avatar_items(normalized)

    def scan_avatar_controls(self, params: dict[str, Any] | None = None) -> dict[str, Any]:
        normalized = params or {}
        payload = self._ports.scan_avatar_controls(normalized)
        payload.setdefault("ok", True)
        return payload

    def scan_wardrobe(self, params: dict[str, Any] | None = None) -> dict[str, Any]:
        normalized = params or {}
        return self._ports.scan_wardrobe(normalized)


@dataclass(frozen=True, slots=True)
class SetupOutfitPreviewPorts:
    """Fixed app-preview capability with no approved/live Unity port."""

    load_settings: Callable[[dict[str, Any]], Any]
    invoke_preview: Callable[[Any, dict[str, Any]], dict[str, Any]]


class SetupOutfitPreviewService:
    """Own only the read/preview Setup Outfit path."""

    def __init__(self, ports: SetupOutfitPreviewPorts) -> None:
        self._ports = ports

    def preview(self, params: dict[str, Any] | None = None) -> dict[str, Any]:
        normalized = params or {}
        request = build_setup_outfit_request(normalized, False)
        if not request["outfitPath"]:
            return {"ok": False, "error": "outfitPath is required."}
        settings = self._ports.load_settings(normalized)
        payload = self._ports.invoke_preview(settings, request)
        payload.setdefault("ok", True)
        return payload


@dataclass(frozen=True, slots=True)
class SetupOutfitApprovedWritePorts:
    """Registry-only Setup Outfit start and polling capabilities.

    ``start_approved`` is one fixed live invocation and ``poll_existing_job`` is
    one fixed peer-lane poll accepting only a job id. The service owns no generic
    Unity tool, checkpoint, approval store, process, transport, or credential.
    """

    load_settings: Callable[[dict[str, Any]], Any]
    start_approved: Callable[[Any, dict[str, Any]], dict[str, Any]]
    poll_existing_job: Callable[[Any, str], dict[str, Any]]
    retryable_poll_error: type[Exception]
    monotonic: Callable[[], float]
    sleep: Callable[[float], None]
    log: ClothingFxLogPort


class SetupOutfitApprovedWriteService:
    """Own the single approved start and authoritative existing-job poll."""

    def __init__(self, ports: SetupOutfitApprovedWritePorts) -> None:
        self._ports = ports

    def execute(self, params: dict[str, Any] | None = None) -> dict[str, Any]:
        normalized = params or {}
        request = build_setup_outfit_request(normalized, True)
        if not request["outfitPath"]:
            return {"ok": False, "error": "outfitPath is required."}
        settings = self._ports.load_settings(normalized)
        payload = self._ports.start_approved(settings, request)
        payload = self.wait_for_existing_job(settings, normalized, payload)
        if str(payload.get("status") or "").lower() in {"error", "timeout"}:
            payload["ok"] = False
        else:
            payload.setdefault("ok", True)
        self._ports.log(
            "info" if payload.get("ok") else "error",
            "wardrobe",
            (
                "Modular Avatar Setup Outfit completed."
                if payload.get("ok")
                else "Modular Avatar Setup Outfit failed."
            ),
            {
                "outfitPath": request["outfitPath"],
                "jobId": payload.get("jobId"),
                "status": payload.get("status"),
            },
        )
        return payload

    def wait_for_existing_job(
        self,
        settings: Any,
        params: dict[str, Any],
        payload: dict[str, Any],
    ) -> dict[str, Any]:
        job_id = str(payload.get("jobId") or payload.get("job_id") or "").strip()
        if not job_id or not is_setup_outfit_job_pending(payload):
            return normalize_setup_outfit_terminal_payload(payload)
        timeout_seconds = coerce_setup_outfit_float_param(
            params,
            ("setup_outfit_poll_timeout_seconds", "setupOutfitPollTimeoutSeconds"),
            180.0,
            0.0,
            3600.0,
        )
        if timeout_seconds <= 0:
            return setup_outfit_timeout_payload(job_id, payload, None)
        interval_seconds = coerce_setup_outfit_float_param(
            params,
            ("setup_outfit_poll_interval_seconds", "setupOutfitPollIntervalSeconds"),
            1.0,
            0.0,
            30.0,
        )
        request_timeout_seconds = int(
            coerce_setup_outfit_float_param(
                params,
                (
                    "setup_outfit_poll_request_timeout_seconds",
                    "setupOutfitPollRequestTimeoutSeconds",
                ),
                min(float(getattr(settings, "unity_mcp_timeout_seconds", 30) or 30), 8.0),
                1.0,
                60.0,
            )
        )
        poll_settings = copy.copy(settings)
        try:
            poll_settings.unity_mcp_timeout_seconds = request_timeout_seconds
        except Exception:
            pass
        deadline = self._ports.monotonic() + timeout_seconds
        last_payload = payload
        last_error: str | None = None
        while self._ports.monotonic() < deadline:
            if interval_seconds > 0:
                self._ports.sleep(
                    min(
                        interval_seconds,
                        max(0.0, deadline - self._ports.monotonic()),
                    )
                )
                if self._ports.monotonic() >= deadline:
                    break
            try:
                polled = self._ports.poll_existing_job(poll_settings, job_id)
                last_error = None
            except self._ports.retryable_poll_error as exc:
                last_error = str(exc)
                continue
            if not is_setup_outfit_job_pending(polled):
                return normalize_setup_outfit_terminal_payload(
                    preserve_setup_outfit_terminal_authority(last_payload, polled)
                )
            last_payload = polled
        return setup_outfit_timeout_payload(job_id, last_payload, last_error)


def normalize_setup_outfit_terminal_payload(payload: dict[str, Any]) -> dict[str, Any]:
    status = str(payload.get("status") or "").lower()
    if status in {"error", "timeout", "unavailable"}:
        payload["ok"] = False
        if any(
            payload.get(field) is True
            for field in ("mutationStarted", "continuationConsumed", "committed")
        ):
            payload["committed"] = True
            payload["commitState"] = "unknown"
            payload["checkpointRecoveryRequired"] = True
    elif status in {"completed", ""}:
        payload.setdefault("ok", True)
    return payload


def preserve_setup_outfit_terminal_authority(
    previous: dict[str, Any], terminal: dict[str, Any]
) -> dict[str, Any]:
    result = dict(terminal)
    status = str(result.get("status") or "").lower()
    if status == "completed":
        complete_receipt = (
            result.get("committed") is True
            and result.get("commitState") == "complete"
            and result.get("checkpointRecoveryRequired") is False
            and bool(str(result.get("outfitGlobalObjectId") or "").strip())
        )
        if complete_receipt:
            return result
        result["ok"] = False
        result["status"] = "error"
        result["error"] = (
            "Setup Outfit completed without its exact committed readback receipt."
        )
        status = "error"
    if status not in {"error", "timeout", "unavailable"}:
        return result
    previous_requires_recovery = any(
        previous.get(field) is True
        for field in ("mutationStarted", "continuationConsumed", "committed")
    )
    if not previous_requires_recovery:
        return result
    for field in ("avatarPath", "outfitPath", "outfitGlobalObjectId"):
        if not result.get(field) and previous.get(field):
            result[field] = previous[field]
    for field in ("continuationConsumed", "mutationStarted"):
        if previous.get(field) is True:
            result[field] = True
    result["committed"] = True
    result["commitState"] = "unknown"
    result["checkpointRecoveryRequired"] = True
    return result


def is_setup_outfit_job_pending(payload: dict[str, Any]) -> bool:
    status = str(payload.get("status") or "").lower()
    return bool(payload.get("jobId") or payload.get("job_id")) and (
        payload.get("pending") is True or status in {"pending", "running"}
    )


def setup_outfit_timeout_payload(
    job_id: str,
    last_payload: dict[str, Any],
    last_error: str | None,
) -> dict[str, Any]:
    result: dict[str, Any] = {
        "ok": False,
        "pending": False,
        "status": "timeout",
        "jobId": job_id,
        "lastStatus": last_payload.get("status"),
        "error": f"Setup Outfit job {job_id} did not finish before the poll timeout.",
        "lastPayload": last_payload,
    }
    if last_error:
        result["lastPollError"] = last_error
    for field in (
        "avatarPath",
        "outfitPath",
        "outfitGlobalObjectId",
        "continuationConsumed",
        "mutationStarted",
    ):
        if field in last_payload:
            result[field] = last_payload[field]
    if any(
        last_payload.get(field) is True
        for field in ("mutationStarted", "continuationConsumed", "committed")
    ):
        result["committed"] = True
        result["commitState"] = "unknown"
        result["checkpointRecoveryRequired"] = True
    return result


def coerce_setup_outfit_float_param(
    params: dict[str, Any],
    names: tuple[str, ...],
    default: float,
    minimum: float,
    maximum: float,
) -> float:
    raw: Any = None
    for name in names:
        if name in params:
            raw = params.get(name)
            break
    if raw is None:
        value = default
    else:
        try:
            value = float(raw)
        except (TypeError, ValueError):
            value = default
    return max(minimum, min(value, maximum))


def build_setup_outfit_request(
    params: dict[str, Any],
    confirm: bool,
) -> dict[str, Any]:
    return {
        "avatarPath": str(
            params.get("avatar_path") or params.get("avatarPath") or ""
        ).strip(),
        "outfitPath": str(
            params.get("outfit_path") or params.get("outfitPath") or ""
        ).strip(),
        "confirmSetup": confirm,
        "saveScene": bool(params.get("save_scene", params.get("saveScene", True))),
    }


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

    apply_clothing_fx: Callable[[dict[str, Any]], dict[str, Any]]
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
