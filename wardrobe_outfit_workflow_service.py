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


class PrimitiveLiveComponentApplyPort(Protocol):
    def apply_component(self, params: dict[str, Any]) -> dict[str, Any]: ...


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


def _coerce_wardrobe_path_list(
    params: dict[str, Any],
    *keys: str,
) -> list[str]:
    result: list[str] = []
    for key in keys:
        raw = params.get(key)
        if raw is None:
            continue
        items = raw if isinstance(raw, (list, tuple)) else [raw]
        for item in items:
            text = str(item).strip()
            if text and text not in result:
                result.append(text)
    return result


def build_add_wardrobe_outfit_request(
    params: dict[str, Any],
    preview: bool,
) -> dict[str, Any]:
    request: dict[str, Any] = {
        "avatarPath": str(
            params.get("avatar_path") or params.get("avatarPath") or ""
        ).strip(),
        "parameterName": str(
            params.get("parameter_name") or params.get("parameterName") or ""
        ).strip(),
        "outfitName": str(
            params.get("outfit_name")
            or params.get("outfitName")
            or params.get("display_name")
            or params.get("displayName")
            or ""
        ).strip(),
        "objectPaths": _coerce_wardrobe_path_list(
            params,
            "object_paths",
            "objectPaths",
            "on_object_paths",
            "onObjectPaths",
        ),
        "preview": preview,
    }
    off_objects = _coerce_wardrobe_path_list(
        params,
        "off_object_paths",
        "offObjectPaths",
    )
    if off_objects:
        request["offObjectPaths"] = off_objects
    if (
        params.get("add_menu_toggle") is not None
        or params.get("addMenuToggle") is not None
    ):
        request["addMenuToggle"] = bool(
            params.get("add_menu_toggle", params.get("addMenuToggle"))
        )
    if (
        params.get("set_objects_default_off") is not None
        or params.get("setObjectsDefaultOff") is not None
    ):
        request["setObjectsDefaultOff"] = bool(
            params.get("set_objects_default_off", params.get("setObjectsDefaultOff"))
        )
    if (
        params.get("sub_menu_overflow") is not None
        or params.get("subMenuOverflow") is not None
    ):
        request["subMenuOverflow"] = bool(
            params.get("sub_menu_overflow", params.get("subMenuOverflow"))
        )
    sub_menu_name = str(
        params.get("sub_menu_name") or params.get("subMenuName") or ""
    ).strip()
    if sub_menu_name:
        request["subMenuName"] = sub_menu_name
    clip_dir = str(
        params.get("clip_output_dir") or params.get("clipOutputDir") or ""
    ).strip()
    if clip_dir:
        request["clipOutputDir"] = clip_dir
    if params.get("value") is not None:
        request["value"] = int(params.get("value"))
    if (
        params.get("write_defaults") is not None
        or params.get("writeDefaults") is not None
    ):
        request["writeDefaults"] = bool(
            params.get("write_defaults", params.get("writeDefaults"))
        )
    return request


def build_add_outfit_part_request(
    params: dict[str, Any],
    preview: bool,
) -> dict[str, Any]:
    request: dict[str, Any] = {
        "avatarPath": str(
            params.get("avatar_path") or params.get("avatarPath") or ""
        ).strip(),
        "parameterName": str(
            params.get("parameter_name") or params.get("parameterName") or ""
        ).strip(),
        "partName": str(
            params.get("part_name")
            or params.get("partName")
            or params.get("display_name")
            or params.get("displayName")
            or ""
        ).strip(),
        "objectPaths": _coerce_wardrobe_path_list(
            params,
            "object_paths",
            "objectPaths",
            "on_object_paths",
            "onObjectPaths",
        ),
        "preview": preview,
    }
    value_raw = params.get("value")
    if value_raw is None:
        value_raw = params.get("outfit_value", params.get("outfitValue"))
    if value_raw is not None:
        request["value"] = int(value_raw)
    part_param = str(
        params.get("part_parameter_name")
        or params.get("partParameterName")
        or params.get("bool_parameter_name")
        or params.get("boolParameterName")
        or ""
    ).strip()
    if part_param:
        request["partParameterName"] = part_param
    if (
        params.get("add_menu_toggle") is not None
        or params.get("addMenuToggle") is not None
    ):
        request["addMenuToggle"] = bool(
            params.get("add_menu_toggle", params.get("addMenuToggle"))
        )
    if (
        params.get("set_objects_default_off") is not None
        or params.get("setObjectsDefaultOff") is not None
    ):
        request["setObjectsDefaultOff"] = bool(
            params.get("set_objects_default_off", params.get("setObjectsDefaultOff"))
        )
    if (
        params.get("default_on") is not None
        or params.get("defaultOn") is not None
    ):
        request["defaultOn"] = bool(
            params.get("default_on", params.get("defaultOn"))
        )
    sub_menu_name = str(
        params.get("sub_menu_name") or params.get("subMenuName") or ""
    ).strip()
    if sub_menu_name:
        request["subMenuName"] = sub_menu_name
    clip_dir = str(
        params.get("clip_output_dir") or params.get("clipOutputDir") or ""
    ).strip()
    if clip_dir:
        request["clipOutputDir"] = clip_dir
    if (
        params.get("write_defaults") is not None
        or params.get("writeDefaults") is not None
    ):
        request["writeDefaults"] = bool(
            params.get("write_defaults", params.get("writeDefaults"))
        )
    return request


def _normalize_wardrobe_bool(value: Any, default: bool = False) -> bool:
    if value is None:
        return default
    if isinstance(value, bool):
        return value
    text = str(value).strip().lower()
    if text in {"1", "true", "yes", "y", "on"}:
        return True
    if text in {"0", "false", "no", "n", "off"}:
        return False
    return default


def build_add_modular_avatar_component_request(
    params: dict[str, Any],
    preview: bool,
) -> dict[str, Any]:
    request: dict[str, Any] = {
        "gameObjectPath": str(
            params.get("game_object_path")
            or params.get("gameObjectPath")
            or params.get("target_path")
            or params.get("targetPath")
            or ""
        ).strip(),
        "componentType": str(
            params.get("component_type") or params.get("componentType") or ""
        ).strip(),
        "preview": preview,
        "saveScene": _normalize_wardrobe_bool(
            params.get("save_scene", params.get("saveScene")),
            False,
        ),
    }
    avatar_path = str(
        params.get("avatar_path") or params.get("avatarPath") or ""
    ).strip()
    if avatar_path:
        request["avatarPath"] = avatar_path
    if (
        params.get("allow_duplicate") is not None
        or params.get("allowDuplicate") is not None
    ):
        request["allowDuplicate"] = bool(
            params.get("allow_duplicate", params.get("allowDuplicate"))
        )
    references = params.get("references")
    if isinstance(references, dict) and references:
        request["references"] = references
    fields = params.get("fields")
    if isinstance(fields, dict) and fields:
        request["fields"] = fields
    return request


def _coerce_wardrobe_gateway_bool(value: Any, default: bool) -> bool:
    if value is None:
        return default
    if isinstance(value, bool):
        return value
    if isinstance(value, (int, float)):
        return value != 0
    text = str(value).strip().lower()
    if text in {"1", "true", "yes", "y", "on"}:
        return True
    if text in {"0", "false", "no", "n", "off"}:
        return False
    return default


def _coerce_wardrobe_int_list(
    params: dict[str, Any],
    *keys: str,
) -> list[int]:
    result: list[int] = []
    for key in keys:
        raw = params.get(key)
        if raw is None:
            continue
        if isinstance(raw, (list, tuple)):
            for item in raw:
                try:
                    value = int(item)
                except (TypeError, ValueError):
                    continue
                if value not in result:
                    result.append(value)
            continue
        for part in str(raw).replace(";", ",").replace(" ", ",").split(","):
            if not part.strip():
                continue
            try:
                value = int(part.strip())
            except ValueError:
                continue
            if value not in result:
                result.append(value)
    return result


def build_manage_wardrobe_request(
    params: dict[str, Any],
    preview: bool,
) -> dict[str, Any]:
    request: dict[str, Any] = {
        "action": str(params.get("action") or "").strip(),
        "avatarPath": str(
            params.get("avatar_path") or params.get("avatarPath") or ""
        ).strip(),
        "parameterName": str(
            params.get("parameter_name")
            or params.get("parameterName")
            or params.get("wardrobe_parameter")
            or params.get("wardrobeParameter")
            or ""
        ).strip(),
        "preview": preview,
    }
    for source_key, target_key in (
        ("outfit_name", "outfitName"),
        ("outfitName", "outfitName"),
        ("target_name", "targetName"),
        ("targetName", "targetName"),
        ("state_name", "stateName"),
        ("stateName", "stateName"),
        ("control_name", "controlName"),
        ("controlName", "controlName"),
        ("new_name", "newName"),
        ("newName", "newName"),
        ("new_outfit_name", "newOutfitName"),
        ("newOutfitName", "newOutfitName"),
        ("asset_dir", "assetDir"),
        ("assetDir", "assetDir"),
        ("clip_output_dir", "clipOutputDir"),
        ("clipOutputDir", "clipOutputDir"),
    ):
        value = str(params.get(source_key) or "").strip()
        if value:
            request[target_key] = value
    for source_key, target_key in (
        ("target_value", "targetValue"),
        ("targetValue", "targetValue"),
        ("outfit_value", "outfitValue"),
        ("outfitValue", "outfitValue"),
        ("value", "value"),
    ):
        if params.get(source_key) is not None:
            request[target_key] = int(params.get(source_key))
            break
    order_values = _coerce_wardrobe_int_list(
        params,
        "order_values",
        "orderValues",
    )
    if order_values:
        request["orderValues"] = order_values
    target_values = _coerce_wardrobe_int_list(
        params,
        "target_values",
        "targetValues",
        "values",
    )
    if target_values:
        request["targetValues"] = target_values
    for source_key, target_key, default in (
        ("delete_objects", "deleteObjects", False),
        ("deleteObjects", "deleteObjects", False),
        ("deactivate_objects", "deactivateObjects", True),
        ("deactivateObjects", "deactivateObjects", True),
        ("delete_generated_assets", "deleteGeneratedAssets", False),
        ("deleteGeneratedAssets", "deleteGeneratedAssets", False),
        ("confirm_delete_wardrobe", "confirmDeleteWardrobe", False),
        ("confirmDeleteWardrobe", "confirmDeleteWardrobe", False),
    ):
        if params.get(source_key) is not None:
            request[target_key] = _coerce_wardrobe_gateway_bool(
                params.get(source_key),
                default,
            )
    return request


def build_create_wardrobe_request(
    params: dict[str, Any],
    preview: bool,
) -> dict[str, Any]:
    request: dict[str, Any] = {
        "avatarPath": str(
            params.get("avatar_path") or params.get("avatarPath") or ""
        ).strip(),
        "parameterName": str(
            params.get("parameter_name")
            or params.get("parameterName")
            or params.get("wardrobe_parameter")
            or params.get("wardrobeParameter")
            or "Clothes"
        ).strip(),
        "preview": preview,
    }
    menu_name = str(
        params.get("menu_name")
        or params.get("menuName")
        or params.get("sub_menu_name")
        or params.get("subMenuName")
        or ""
    ).strip()
    if menu_name:
        request["menuName"] = menu_name
    default_control_name = str(
        params.get("default_control_name")
        or params.get("defaultControlName")
        or ""
    ).strip()
    if default_control_name:
        request["defaultControlName"] = default_control_name
    layer_name = str(
        params.get("layer_name") or params.get("layerName") or ""
    ).strip()
    if layer_name:
        request["layerName"] = layer_name
    asset_dir = str(
        params.get("asset_dir")
        or params.get("assetDir")
        or params.get("clip_output_dir")
        or params.get("clipOutputDir")
        or ""
    ).strip()
    if asset_dir:
        request["assetDir"] = asset_dir
    if (
        params.get("write_defaults") is not None
        or params.get("writeDefaults") is not None
    ):
        request["writeDefaults"] = _coerce_wardrobe_gateway_bool(
            params.get("write_defaults", params.get("writeDefaults")),
            True,
        )
    if params.get("saved") is not None:
        request["saved"] = _coerce_wardrobe_gateway_bool(
            params.get("saved"),
            True,
        )
    if (
        params.get("network_synced") is not None
        or params.get("networkSynced") is not None
    ):
        request["networkSynced"] = _coerce_wardrobe_gateway_bool(
            params.get("network_synced", params.get("networkSynced")),
            True,
        )
    return request


def _build_create_wardrobe_core_calls_from_request(
    request: dict[str, Any],
    preview: bool,
) -> list[tuple[str, dict[str, Any]]]:
    avatar_path = request["avatarPath"]
    parameter_name = request["parameterName"]
    asset_dir = request.get("assetDir", "Assets/VRCForge/Generated/Wardrobe")
    menu_name = (
        str(request.get("menuName") or request.get("subMenuName") or "Wardrobe").strip()
        or "Wardrobe"
    )
    default_control_name = (
        str(request.get("defaultControlName") or "Default").strip() or "Default"
    )
    layer_name = (
        str(request.get("layerName") or parameter_name).strip() or parameter_name
    )
    common = {"avatarPath": avatar_path, "assetDir": asset_dir}
    return [
        (
            "vrc_ensure_expression_parameter",
            {
                **common,
                "parameterName": parameter_name,
                "valueType": "Int",
                "defaultValue": 0.0,
                "saved": bool(request.get("saved", True)),
                "networkSynced": bool(request.get("networkSynced", True)),
                "preview": preview,
            },
        ),
        (
            "vrc_ensure_animator_state",
            {
                **common,
                "layerName": layer_name,
                "stateName": default_control_name,
                "parameterName": parameter_name,
                "parameterType": "Int",
                "conditionMode": "Equals",
                "threshold": 0.0,
                "writeDefaults": bool(request.get("writeDefaults", True)),
                "preview": preview,
            },
        ),
        (
            "vrc_ensure_expression_menu_control",
            {
                **common,
                "menuPath": menu_name,
                "controlName": default_control_name,
                "controlType": "Toggle",
                "parameterName": parameter_name,
                "controlValue": 0.0,
                "preview": preview,
            },
        ),
    ]


def build_create_wardrobe_core_calls(
    params: dict[str, Any],
    preview: bool,
) -> list[tuple[str, dict[str, Any]]]:
    request = build_create_wardrobe_request(params, preview)
    invalid = validate_create_wardrobe_request(request)
    if invalid is not None:
        raise ValueError(str(invalid["error"]))
    return _build_create_wardrobe_core_calls_from_request(request, preview)


def validate_create_wardrobe_request(
    request: dict[str, Any],
) -> dict[str, Any] | None:
    if not request["parameterName"]:
        return {
            "ok": False,
            "error": "parameterName is required for wardrobe creation.",
        }
    return None


AddWardrobeOutfitRequestBuilder = Callable[
    [dict[str, Any], bool],
    dict[str, Any],
]


@dataclass(frozen=True, slots=True)
class AddWardrobeOutfitPreviewPorts:
    """Fixed preview capability with no approved/live Unity port."""

    build_request: AddWardrobeOutfitRequestBuilder
    load_settings: Callable[[dict[str, Any]], Any]
    invoke_preview: Callable[[Any, dict[str, Any]], dict[str, Any]]


class AddWardrobeOutfitPreviewService:
    """Own only the read/preview Add Wardrobe Outfit path."""

    def __init__(self, ports: AddWardrobeOutfitPreviewPorts) -> None:
        self._ports = ports

    def preview(self, params: dict[str, Any] | None = None) -> dict[str, Any]:
        normalized = params or {}
        request = self._ports.build_request(normalized, True)
        invalid = validate_add_wardrobe_outfit_request(request)
        if invalid is not None:
            return invalid
        settings = self._ports.load_settings(normalized)
        payload = self._ports.invoke_preview(settings, request)
        payload.setdefault("ok", True)
        return payload


@dataclass(frozen=True, slots=True)
class AddWardrobeOutfitApprovedWritePorts:
    """Registry-only fixed live Add Wardrobe Outfit capability."""

    build_request: AddWardrobeOutfitRequestBuilder
    load_settings: Callable[[dict[str, Any]], Any]
    invoke_approved: Callable[[Any, dict[str, Any]], dict[str, Any]]
    log: ClothingFxLogPort


class AddWardrobeOutfitApprovedWriteService:
    """Own the approved Add Wardrobe Outfit execution endpoint."""

    def __init__(self, ports: AddWardrobeOutfitApprovedWritePorts) -> None:
        self._ports = ports

    def execute(self, params: dict[str, Any] | None = None) -> dict[str, Any]:
        normalized = params or {}
        request = self._ports.build_request(normalized, False)
        invalid = validate_add_wardrobe_outfit_request(request)
        if invalid is not None:
            return invalid
        settings = self._ports.load_settings(normalized)
        payload = self._ports.invoke_approved(settings, request)
        payload.setdefault("ok", True)
        self._ports.log(
            "info",
            "wardrobe",
            "Wardrobe outfit added.",
            {
                "parameterName": request["parameterName"],
                "outfitName": request["outfitName"],
            },
        )
        return payload


def validate_add_wardrobe_outfit_request(
    request: dict[str, Any],
) -> dict[str, Any] | None:
    if not request["parameterName"]:
        return {
            "ok": False,
            "error": "parameterName is required (the existing int wardrobe parameter).",
        }
    if not request["outfitName"]:
        return {
            "ok": False,
            "error": "outfitName is required (display name for the new outfit).",
        }
    if not request["objectPaths"]:
        return {
            "ok": False,
            "error": "objectPaths is required (the new outfit's scene objects to turn on).",
        }
    return None


AddOutfitPartRequestBuilder = Callable[
    [dict[str, Any], bool],
    dict[str, Any],
]


@dataclass(frozen=True, slots=True)
class AddOutfitPartPreviewPorts:
    """Fixed preview capability with no approved/live Unity port."""

    build_request: AddOutfitPartRequestBuilder
    load_settings: Callable[[dict[str, Any]], Any]
    invoke_preview: Callable[[Any, dict[str, Any]], dict[str, Any]]


class AddOutfitPartPreviewService:
    """Own only the read/preview Add Outfit Part path."""

    def __init__(self, ports: AddOutfitPartPreviewPorts) -> None:
        self._ports = ports

    def preview(self, params: dict[str, Any] | None = None) -> dict[str, Any]:
        normalized = params or {}
        request = self._ports.build_request(normalized, True)
        invalid = validate_add_outfit_part_request(request)
        if invalid is not None:
            return invalid
        settings = self._ports.load_settings(normalized)
        payload = self._ports.invoke_preview(settings, request)
        payload.setdefault("ok", True)
        return payload


@dataclass(frozen=True, slots=True)
class AddOutfitPartApprovedWritePorts:
    """Registry-only fixed live Add Outfit Part capability."""

    build_request: AddOutfitPartRequestBuilder
    load_settings: Callable[[dict[str, Any]], Any]
    invoke_approved: Callable[[Any, dict[str, Any]], dict[str, Any]]
    log: ClothingFxLogPort


class AddOutfitPartApprovedWriteService:
    """Own the approved Add Outfit Part execution endpoint."""

    def __init__(self, ports: AddOutfitPartApprovedWritePorts) -> None:
        self._ports = ports

    def execute(self, params: dict[str, Any] | None = None) -> dict[str, Any]:
        normalized = params or {}
        request = self._ports.build_request(normalized, False)
        invalid = validate_add_outfit_part_request(request)
        if invalid is not None:
            return invalid
        settings = self._ports.load_settings(normalized)
        payload = self._ports.invoke_approved(settings, request)
        payload.setdefault("ok", True)
        self._ports.log(
            "info",
            "wardrobe",
            "Outfit part added.",
            {
                "parameterName": request["parameterName"],
                "partName": request["partName"],
                "value": request.get("value"),
            },
        )
        return payload


def validate_add_outfit_part_request(
    request: dict[str, Any],
) -> dict[str, Any] | None:
    if not request["parameterName"]:
        return {
            "ok": False,
            "error": (
                "parameterName is required (the existing int wardrobe parameter "
                "the part is gated on)."
            ),
        }
    if not request["partName"]:
        return {
            "ok": False,
            "error": "partName is required (display name for the new part toggle).",
        }
    if "value" not in request:
        return {
            "ok": False,
            "error": (
                "value is required (the wardrobe int value N this part belongs to)."
            ),
        }
    if not request["objectPaths"]:
        return {
            "ok": False,
            "error": "objectPaths is required (the part's scene objects to toggle on/off).",
        }
    return None


AddModularAvatarComponentRequestBuilder = Callable[
    [dict[str, Any], bool],
    dict[str, Any],
]


@dataclass(frozen=True, slots=True)
class AddModularAvatarComponentPreviewPorts:
    """Fixed preview capability with no approved/live Unity port."""

    build_request: AddModularAvatarComponentRequestBuilder
    load_settings: Callable[[dict[str, Any]], Any]
    invoke_preview: Callable[[Any, dict[str, Any]], dict[str, Any]]


class AddModularAvatarComponentPreviewService:
    """Own only the read/preview Modular Avatar component path."""

    def __init__(self, ports: AddModularAvatarComponentPreviewPorts) -> None:
        self._ports = ports

    def preview(self, params: dict[str, Any] | None = None) -> dict[str, Any]:
        normalized = params or {}
        request = self._ports.build_request(normalized, True)
        invalid = validate_add_modular_avatar_component_request(request)
        if invalid is not None:
            return invalid
        settings = self._ports.load_settings(normalized)
        payload = self._ports.invoke_preview(settings, request)
        payload.setdefault("ok", True)
        return payload


@dataclass(frozen=True, slots=True)
class AddModularAvatarComponentApprovedWritePorts:
    """Registry-only fixed live Modular Avatar component capabilities."""

    primitive_live_connection: Callable[[], PrimitiveLiveComponentApplyPort | None]
    primitive_live_guard_fields: Callable[[dict[str, Any]], dict[str, Any]]
    build_request: AddModularAvatarComponentRequestBuilder
    load_settings: Callable[[dict[str, Any]], Any]
    invoke_approved: Callable[[Any, dict[str, Any]], dict[str, Any]]
    log: ClothingFxLogPort


class AddModularAvatarComponentApprovedWriteService:
    """Own the approved Modular Avatar component execution endpoint."""

    def __init__(self, ports: AddModularAvatarComponentApprovedWritePorts) -> None:
        self._ports = ports

    def execute(self, params: dict[str, Any] | None = None) -> dict[str, Any]:
        normalized = params or {}
        live_connection = self._ports.primitive_live_connection()
        if (
            live_connection is not None
            and self._ports.primitive_live_guard_fields(normalized)
        ):
            return live_connection.apply_component(normalized)
        request = self._ports.build_request(normalized, False)
        invalid = validate_add_modular_avatar_component_request(request)
        if invalid is not None:
            return invalid
        settings = self._ports.load_settings(normalized)
        payload = self._ports.invoke_approved(settings, request)
        payload.setdefault("ok", True)
        self._ports.log(
            "info",
            "modular_avatar",
            "Modular Avatar component added.",
            {
                "gameObjectPath": request["gameObjectPath"],
                "componentType": request["componentType"],
            },
        )
        return payload


def validate_add_modular_avatar_component_request(
    request: dict[str, Any],
) -> dict[str, Any] | None:
    if not request["gameObjectPath"]:
        return {
            "ok": False,
            "error": (
                "gameObjectPath is required (the scene object to add the Modular "
                "Avatar component to)."
            ),
        }
    if not request["componentType"]:
        return {
            "ok": False,
            "error": (
                "componentType is required (e.g. MergeArmature, BoneProxy, "
                "MenuInstaller, MergeAnimator, Parameters)."
            ),
        }
    return None


ManageWardrobeRequestBuilder = Callable[
    [dict[str, Any], bool],
    dict[str, Any],
]


@dataclass(frozen=True, slots=True)
class ManageWardrobePreviewPorts:
    """Fixed preview capability with no approved/live Unity port."""

    build_request: ManageWardrobeRequestBuilder
    load_settings: Callable[[dict[str, Any]], Any]
    invoke_preview: Callable[[Any, dict[str, Any]], dict[str, Any]]


class ManageWardrobePreviewService:
    """Own only the read/preview Manage Wardrobe path."""

    def __init__(self, ports: ManageWardrobePreviewPorts) -> None:
        self._ports = ports

    def preview(self, params: dict[str, Any] | None = None) -> dict[str, Any]:
        normalized = params or {}
        request = self._ports.build_request(normalized, True)
        invalid = validate_manage_wardrobe_request(request)
        if invalid is not None:
            return invalid
        settings = self._ports.load_settings(normalized)
        payload = self._ports.invoke_preview(settings, request)
        payload.setdefault("ok", True)
        return payload


@dataclass(frozen=True, slots=True)
class ManageWardrobeApprovedWritePorts:
    """Registry-only fixed live Manage Wardrobe capability."""

    build_request: ManageWardrobeRequestBuilder
    load_settings: Callable[[dict[str, Any]], Any]
    invoke_approved: Callable[[Any, dict[str, Any]], dict[str, Any]]
    log: ClothingFxLogPort


class ManageWardrobeApprovedWriteService:
    """Own the approved Manage Wardrobe execution endpoint."""

    def __init__(self, ports: ManageWardrobeApprovedWritePorts) -> None:
        self._ports = ports

    def execute(self, params: dict[str, Any] | None = None) -> dict[str, Any]:
        normalized = params or {}
        request = self._ports.build_request(normalized, False)
        invalid = validate_manage_wardrobe_request(request)
        if invalid is not None:
            return invalid
        settings = self._ports.load_settings(normalized)
        payload = self._ports.invoke_approved(settings, request)
        payload.setdefault("ok", True)
        self._ports.log(
            "info",
            "wardrobe",
            "Wardrobe management action executed.",
            {
                "parameterName": request["parameterName"],
                "action": request["action"],
            },
        )
        return payload


def validate_manage_wardrobe_request(
    request: dict[str, Any],
) -> dict[str, Any] | None:
    if not request["action"]:
        return {
            "ok": False,
            "error": "action is required for wardrobe management.",
        }
    if not request["parameterName"]:
        return {
            "ok": False,
            "error": "parameterName is required for wardrobe management.",
        }
    return None


CreateWardrobeRequestBuilder = Callable[
    [dict[str, Any], bool],
    dict[str, Any],
]
CreateWardrobeCallsBuilder = Callable[
    [dict[str, Any], bool],
    list[tuple[str, dict[str, Any]]],
]
CreateWardrobeStepPort = Callable[[dict[str, Any]], dict[str, Any]]


@dataclass(frozen=True, slots=True)
class CreateWardrobePreviewPorts:
    """Three fixed preview steps with no approved/live capability."""

    build_request: CreateWardrobeRequestBuilder
    build_calls: CreateWardrobeCallsBuilder
    ensure_parameter: CreateWardrobeStepPort
    ensure_animator: CreateWardrobeStepPort
    ensure_menu: CreateWardrobeStepPort


class CreateWardrobePreviewService:
    """Own the complete read/preview Wardrobe creation sequence."""

    def __init__(self, ports: CreateWardrobePreviewPorts) -> None:
        self._ports = ports

    def preview(self, params: dict[str, Any] | None = None) -> dict[str, Any]:
        normalized = params or {}
        request = self._ports.build_request(normalized, True)
        invalid = validate_create_wardrobe_request(request)
        if invalid is not None:
            return invalid
        calls = self._ports.build_calls(normalized, True)
        results = (
            self._ports.ensure_parameter(calls[0][1]),
            self._ports.ensure_animator(calls[1][1]),
            self._ports.ensure_menu(calls[2][1]),
        )
        steps = [
            {"tool": calls[index][0], "result": result}
            for index, result in enumerate(results)
        ]
        ok = all(bool(step["result"].get("ok")) for step in steps)
        return {
            "ok": ok,
            "preview": True,
            "action": "create_wardrobe",
            "parameterName": request["parameterName"],
            "steps": steps,
            "error": next(
                (
                    step["result"].get("error")
                    for step in steps
                    if not step["result"].get("ok")
                ),
                None,
            ),
        }


@dataclass(frozen=True, slots=True)
class CreateWardrobeApprovedWritePorts:
    """Registry-only fixed Wardrobe creation sequence."""

    build_request: CreateWardrobeRequestBuilder
    build_calls: CreateWardrobeCallsBuilder
    ensure_parameter: CreateWardrobeStepPort
    ensure_animator: CreateWardrobeStepPort
    ensure_menu: CreateWardrobeStepPort
    log: ClothingFxLogPort


class CreateWardrobeApprovedWriteService:
    """Own the approved fail-fast Wardrobe creation endpoint."""

    def __init__(self, ports: CreateWardrobeApprovedWritePorts) -> None:
        self._ports = ports

    def execute(self, params: dict[str, Any] | None = None) -> dict[str, Any]:
        normalized = params or {}
        request = self._ports.build_request(normalized, False)
        invalid = validate_create_wardrobe_request(request)
        if invalid is not None:
            return invalid
        calls = self._ports.build_calls(normalized, False)
        step_ports = (
            self._ports.ensure_parameter,
            self._ports.ensure_animator,
            self._ports.ensure_menu,
        )
        steps: list[dict[str, Any]] = []
        for index, invoke in enumerate(step_ports):
            result = invoke(calls[index][1])
            steps.append({"tool": calls[index][0], "result": result})
            if not result.get("ok"):
                return {
                    "ok": False,
                    "action": "create_wardrobe",
                    "parameterName": request["parameterName"],
                    "steps": steps,
                    "error": result.get("error"),
                }
        self._ports.log(
            "info",
            "wardrobe",
            "Wardrobe skeleton created.",
            {"parameterName": request["parameterName"]},
        )
        return {
            "ok": True,
            "preview": False,
            "action": "create_wardrobe",
            "parameterName": request["parameterName"],
            "steps": steps,
        }


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
