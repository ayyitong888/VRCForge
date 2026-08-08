from __future__ import annotations

import copy
from dataclasses import dataclass
from pathlib import Path, PurePosixPath
from typing import Any, Callable

from prepared_unity_execution import (
    PREPARED_UNITY_EXECUTION_ARGUMENT_KEY,
    install_prepared_calls,
    prepared_call,
    prepared_evidence,
)
from wardrobe_outfit_workflow_service import build_add_wardrobe_outfit_request


ADD_OUTFIT_CONTINUATION_NONCE_KEY = "__vrcforgeAddOutfitContinuationNonce"

PreparedAddOutfitCall = tuple[str, dict[str, Any]]


def _workflow_bool(
    params: dict[str, Any], keys: tuple[str, ...], default: bool
) -> bool:
    for key in keys:
        if key not in params or params.get(key) is None:
            continue
        raw = params.get(key)
        if isinstance(raw, bool):
            return raw
        if isinstance(raw, (int, float)):
            return raw != 0
        text = str(raw).strip().lower()
        if text in {"1", "true", "yes", "y", "on"}:
            return True
        if text in {"0", "false", "no", "n", "off"}:
            return False
    return default


def _workflow_parameter_name(params: dict[str, Any]) -> tuple[str, bool]:
    for key in (
        "parameter_name",
        "parameterName",
        "wardrobe_parameter",
        "wardrobeParameter",
    ):
        value = str(params.get(key) or "").strip()
        if value:
            return value, True
    return "Clothes", False


def _coerce_path_list(params: dict[str, Any], *keys: str) -> list[str]:
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


def canonical_add_outfit_asset(payload: dict[str, Any]) -> dict[str, Any]:
    asset_path = str(payload.get("assetPath") or "").replace("\\", "/").strip()
    guid = str(payload.get("guid") or "").strip().lower()
    dependency_hash = str(payload.get("dependencyHash") or "").strip().lower()
    if (
        payload.get("ok") is not True
        or payload.get("isPrefab") is not True
        or not asset_path.startswith("Assets/")
        or ".." in PurePosixPath(asset_path).parts
        or len(guid) != 32
        or any(character not in "0123456789abcdef" for character in guid)
        or len(dependency_hash) != 32
        or any(
            character not in "0123456789abcdef"
            for character in dependency_hash
        )
    ):
        raise RuntimeError("Add Outfit prefab asset identity is incomplete or invalid.")
    return {
        "assetPath": asset_path,
        "guid": guid,
        "dependencyHash": dependency_hash,
        "name": str(payload.get("name") or payload.get("prefabRootName") or "").strip(),
        "assetType": str(payload.get("assetType") or "").strip(),
        "prefabAssetType": str(payload.get("prefabAssetType") or "").strip(),
    }


def canonical_add_outfit_gameobject(
    payload: dict[str, Any], label: str
) -> dict[str, Any]:
    path = (
        str(payload.get("gameObjectPath") or "")
        .replace("\\", "/")
        .strip()
        .strip("/")
    )
    global_id = str(payload.get("globalObjectId") or "").strip()
    scene_path = str(payload.get("scenePath") or "").replace("\\", "/").strip()
    count = int(payload.get("hierarchyPathCount", 0) or 0)
    if (
        payload.get("ok") is not True
        or not path
        or not global_id
        or not scene_path
        or count != 1
    ):
        raise RuntimeError(f"Add Outfit {label} identity is incomplete or ambiguous.")
    raw_children = payload.get("children")
    if not isinstance(raw_children, list):
        raise RuntimeError(f"Add Outfit {label} child readback is invalid.")
    children = sorted(
        str(item.get("gameObjectPath") or "")
        .replace("\\", "/")
        .strip()
        .strip("/")
        for item in raw_children
        if isinstance(item, dict)
    )
    return {
        "gameObjectPath": path,
        "globalObjectId": global_id,
        "scenePath": scene_path,
        "children": children,
    }


def _wardrobe_candidate_parameter_names(scan_payload: dict[str, Any]) -> list[str]:
    candidates = (
        scan_payload.get("wardrobeCandidates")
        if isinstance(scan_payload.get("wardrobeCandidates"), list)
        else []
    )
    names: list[str] = []
    for candidate in candidates:
        if not isinstance(candidate, dict):
            continue
        name = str(candidate.get("parameterName") or "").strip()
        if name and name not in names:
            names.append(name)
    return names


def select_add_outfit_wardrobe(
    scan: dict[str, Any], parameter_name: str, explicit: bool
) -> tuple[dict[str, Any], str, int]:
    if scan.get("ok") is not True:
        raise RuntimeError(
            scan.get("error") or "Wardrobe scan failed during Add Outfit preparation."
        )
    fingerprint = str(scan.get("fingerprint") or "").strip().lower()
    if len(fingerprint) != 64 or any(
        character not in "0123456789abcdef" for character in fingerprint
    ):
        raise RuntimeError("Wardrobe scan fingerprint is invalid.")
    wardrobes = [
        item for item in (scan.get("wardrobes") or []) if isinstance(item, dict)
    ]
    if not wardrobes:
        candidates = _wardrobe_candidate_parameter_names(scan)
        detail = f" Candidate groups: {', '.join(candidates)}." if candidates else ""
        raise RuntimeError(
            "Add Outfit requires an existing verified wardrobe. Approve "
            "vrcforge_create_wardrobe first, then retry." + detail
        )
    selected: dict[str, Any] | None = None
    if explicit:
        selected = next(
            (
                item
                for item in wardrobes
                if str(item.get("parameterName") or "") == parameter_name
            ),
            None,
        )
        if selected is None:
            raise RuntimeError(
                f"Verified wardrobe '{parameter_name}' was not found. Create or "
                "repair it in a separate approved action, then retry."
            )
    else:
        selected = wardrobes[0]
        parameter_name = str(selected.get("parameterName") or "").strip()
    outfits = selected.get("outfits")
    if not parameter_name or not isinstance(outfits, list):
        raise RuntimeError("Selected wardrobe readback is invalid.")
    values = [
        int(item.get("value"))
        for item in outfits
        if isinstance(item, dict) and item.get("value") is not None
    ]
    assigned_value = (max(values) if values else 0) + 1
    return copy.deepcopy(selected), fingerprint, assigned_value


@dataclass(frozen=True, slots=True)
class PreparedAddOutfitStatePorts:
    resolve_project_root: Callable[[dict[str, Any]], Path]
    capture_project_identity: Callable[[Path], dict[str, Any]]
    find_assets: Callable[[dict[str, Any]], dict[str, Any]]
    get_asset_info: Callable[[dict[str, Any]], dict[str, Any]]
    get_gameobject: Callable[[dict[str, Any]], dict[str, Any]]
    scan_wardrobe: Callable[[dict[str, Any]], dict[str, Any]]
    ensure_dict: Callable[[Any, str], dict[str, Any]]
    digest: Callable[[Any], str]


class PreparedAddOutfitStateBuilder:
    """Build sealed Add Outfit facts and calls from read-only fixed capabilities."""

    def __init__(self, ports: PreparedAddOutfitStatePorts) -> None:
        self._ports = ports

    def _resolve_asset(
        self, params: dict[str, Any], project_params: dict[str, Any]
    ) -> dict[str, Any]:
        asset_path = str(
            params.get("asset_path") or params.get("assetPath") or ""
        ).strip()
        guid = str(params.get("guid") or "").strip()
        if asset_path or guid:
            return {"assetPath": asset_path, "guid": guid, "source": "explicit"}
        query = str(
            params.get("query")
            or params.get("asset_query")
            or params.get("assetQuery")
            or ""
        ).strip()
        if not query:
            raise RuntimeError("assetPath, guid, or assetQuery/query is required.")
        search = self._ports.find_assets(
            {
                **project_params,
                "query": query,
                "typeName": str(
                    params.get("type_name") or params.get("typeName") or "Prefab"
                ).strip()
                or "Prefab",
                "folder": str(params.get("folder") or "").strip(),
                "limit": 1,
            }
        )
        if not search.get("ok"):
            raise RuntimeError(
                str(search.get("error") or "Add Outfit prefab could not be resolved.")
            )
        assets = search.get("assets") if isinstance(search.get("assets"), list) else []
        if not assets:
            raise RuntimeError(f"No prefab asset matched query '{query}'.")
        first = self._ports.ensure_dict(assets[0], "workflow asset")
        return {
            "assetPath": str(first.get("assetPath") or ""),
            "guid": str(first.get("guid") or ""),
            "name": str(first.get("name") or ""),
            "source": "query",
            "query": query,
        }

    def build(self, params: dict[str, Any] | None = None) -> dict[str, Any]:
        normalized = params or {}
        continuation_nonce = str(
            normalized.get(ADD_OUTFIT_CONTINUATION_NONCE_KEY) or ""
        ).strip().lower()
        if continuation_nonce and (
            len(continuation_nonce) != 64
            or any(
                character not in "0123456789abcdef"
                for character in continuation_nonce
            )
        ):
            raise RuntimeError("Prepared Add Outfit continuation nonce is invalid.")
        project_root = self._ports.resolve_project_root(normalized)
        project_identity = self._ports.capture_project_identity(project_root)
        project_params = {"projectPath": project_identity["projectPath"]}
        asset_ref = self._resolve_asset(normalized, project_params)
        asset = canonical_add_outfit_asset(
            self._ports.get_asset_info(
                {
                    **project_params,
                    "assetPath": asset_ref.get("assetPath"),
                    "guid": asset_ref.get("guid"),
                }
            )
        )
        avatar_requested = str(
            normalized.get("avatar_path") or normalized.get("avatarPath") or ""
        ).strip()
        parent_requested = str(
            normalized.get("parent_path")
            or normalized.get("parentPath")
            or avatar_requested
        ).strip()
        if not avatar_requested or not parent_requested:
            raise RuntimeError(
                "avatarPath and a resolvable parentPath are required for Add Outfit."
            )
        parent = canonical_add_outfit_gameobject(
            self._ports.get_gameobject(
                {**project_params, "gameObjectPath": parent_requested}
            ),
            "parent",
        )
        avatar = (
            parent
            if parent["gameObjectPath"] == avatar_requested
            else canonical_add_outfit_gameobject(
                self._ports.get_gameobject(
                    {**project_params, "gameObjectPath": avatar_requested}
                ),
                "avatar",
            )
        )
        outfit_name = str(
            normalized.get("outfit_name")
            or normalized.get("outfitName")
            or normalized.get("name")
            or asset.get("name")
            or "Outfit"
        ).strip()
        if not outfit_name or "/" in outfit_name or "\\" in outfit_name:
            raise RuntimeError(
                "Add Outfit name must be a non-empty single hierarchy segment."
            )
        outfit_path = f"{parent['gameObjectPath'].rstrip('/')}/{outfit_name}"
        if outfit_path in parent["children"]:
            raise RuntimeError("Approval-bound Add Outfit target already exists.")

        manage_wardrobe = _workflow_bool(
            normalized, ("manage_wardrobe", "manageWardrobe"), True
        )
        setup_outfit = _workflow_bool(
            normalized, ("setup_outfit", "setupOutfit"), True
        )
        unpack_prefab = _workflow_bool(
            normalized, ("unpack_prefab", "unpackPrefab"), False
        )
        parameter_name, parameter_explicit = _workflow_parameter_name(normalized)
        selected_wardrobe: dict[str, Any] | None = None
        wardrobe_fingerprint = ""
        assigned_value: int | None = None
        if manage_wardrobe:
            scan = self._ports.scan_wardrobe(
                {**project_params, "avatarPath": avatar["gameObjectPath"]}
            )
            selected_wardrobe, wardrobe_fingerprint, assigned_value = (
                select_add_outfit_wardrobe(
                    scan, parameter_name, parameter_explicit
                )
            )
            parameter_name = str(selected_wardrobe.get("parameterName") or "")

        continuation_tools: list[str] = []
        if unpack_prefab:
            continuation_tools.append("vrc_unpack_prefab")
        if setup_outfit:
            continuation_tools.append("vrc_setup_outfit")
        if manage_wardrobe:
            continuation_tools.append("vrc_add_wardrobe_outfit")

        instantiate_arguments = {
            **project_params,
            "assetPath": asset["assetPath"],
            "guid": asset["guid"],
            "parentPath": parent["gameObjectPath"],
            "name": outfit_name,
            "worldPositionStays": _workflow_bool(
                normalized,
                ("world_position_stays", "worldPositionStays"),
                True,
            ),
            "expectedPrefabGuid": asset["guid"],
            "expectedAssetDependencyHash": asset["dependencyHash"],
            "expectedScenePath": parent["scenePath"],
            "expectedParentGlobalObjectId": parent["globalObjectId"],
            "expectedResultPath": outfit_path,
            "preview": False,
        }
        if continuation_nonce and continuation_tools:
            instantiate_arguments.update(
                {
                    "approvedObjectReceiptNonce": continuation_nonce,
                    "approvedContinuationTools": continuation_tools,
                }
            )
        calls: list[PreparedAddOutfitCall] = [
            ("vrc_instantiate_prefab", instantiate_arguments)
        ]
        if unpack_prefab:
            mode = str(
                normalized.get("unpack_mode")
                or normalized.get("unpackMode")
                or "outermost"
            ).strip().lower()
            if mode not in {"outermost", "completely"}:
                raise RuntimeError(
                    "Add Outfit unpack mode must be outermost or completely."
                )
            unpack_arguments = {
                **project_params,
                "gameObjectPath": outfit_path,
                "expectedPrefabGuid": asset["guid"],
                "expectedAssetDependencyHash": asset["dependencyHash"],
                "expectedScenePath": parent["scenePath"],
                "mode": mode,
                "preview": False,
            }
            if continuation_nonce:
                unpack_arguments["approvedObjectReceiptNonce"] = continuation_nonce
            calls.append(("vrc_unpack_prefab", unpack_arguments))
        if setup_outfit:
            setup_arguments = {
                **project_params,
                "avatarPath": avatar["gameObjectPath"],
                "outfitPath": outfit_path,
                "confirmSetup": True,
                "saveScene": _workflow_bool(
                    normalized, ("save_scene", "saveScene"), True
                ),
            }
            if continuation_nonce:
                setup_arguments["approvedObjectReceiptNonce"] = continuation_nonce
            calls.append(("vrc_setup_outfit", setup_arguments))
        if manage_wardrobe:
            assert assigned_value is not None
            wardrobe_source: dict[str, Any] = {
                **project_params,
                "avatarPath": avatar["gameObjectPath"],
                "parameterName": parameter_name,
                "outfitName": outfit_name,
                "objectPaths": [outfit_path],
                "value": assigned_value,
                "offObjectPaths": _coerce_path_list(
                    normalized, "off_object_paths", "offObjectPaths"
                ),
                "addMenuToggle": _workflow_bool(
                    normalized, ("add_menu_toggle", "addMenuToggle"), True
                ),
                "setObjectsDefaultOff": _workflow_bool(
                    normalized,
                    ("set_objects_default_off", "setObjectsDefaultOff"),
                    True,
                ),
                "subMenuOverflow": _workflow_bool(
                    normalized, ("sub_menu_overflow", "subMenuOverflow"), True
                ),
                "subMenuName": str(
                    normalized.get("sub_menu_name")
                    or normalized.get("subMenuName")
                    or "Wardrobe"
                ).strip()
                or "Wardrobe",
            }
            clip_output_dir = str(
                normalized.get("clip_output_dir")
                or normalized.get("clipOutputDir")
                or ""
            ).strip()
            if clip_output_dir:
                wardrobe_source["clipOutputDir"] = clip_output_dir
            if (
                normalized.get("write_defaults") is not None
                or normalized.get("writeDefaults") is not None
            ):
                wardrobe_source["writeDefaults"] = _workflow_bool(
                    normalized, ("write_defaults", "writeDefaults"), True
                )
            wardrobe_arguments = build_add_wardrobe_outfit_request(
                wardrobe_source, False
            )
            wardrobe_arguments.update(
                {
                    **project_params,
                    "expectedAssignedValue": assigned_value,
                    "expectedWardrobeFingerprint": wardrobe_fingerprint,
                }
            )
            if continuation_nonce:
                wardrobe_arguments[
                    "approvedObjectReceiptNonce"
                ] = continuation_nonce
            calls.append(("vrc_add_wardrobe_outfit", wardrobe_arguments))

        read_facts = {
            "asset": asset,
            "avatar": avatar,
            "parent": parent,
            "outfitPath": outfit_path,
            "wardrobeFingerprint": wardrobe_fingerprint,
            "selectedWardrobe": selected_wardrobe,
            "assignedValue": assigned_value,
        }
        evidence = {
            "schema": "vrcforge.prepared-add-outfit.v1",
            "projectIdentity": project_identity,
            "readFacts": read_facts,
            "readFactsSha256": self._ports.digest(read_facts),
            "callsSha256": self._ports.digest(
                [
                    {"tool": tool, "arguments": arguments}
                    for tool, arguments in calls
                ]
            ),
            "manageWardrobe": manage_wardrobe,
            "setupOutfit": setup_outfit,
            "unpackPrefab": unpack_prefab,
            "parameterName": parameter_name if manage_wardrobe else "",
            "outfitName": outfit_name,
        }
        preview = {
            "ok": True,
            "preview": True,
            "plan": {
                "action": "add_outfit_workflow",
                "projectPath": project_identity["projectPath"],
                "avatarPath": avatar["gameObjectPath"],
                "parentPath": parent["gameObjectPath"],
                "outfitPath": outfit_path,
                "outfitName": outfit_name,
                "asset": asset,
                "manageWardrobe": manage_wardrobe,
                "parameterName": parameter_name if manage_wardrobe else None,
                "assignedValue": assigned_value,
                "wardrobeFingerprint": wardrobe_fingerprint or None,
                "steps": [
                    {"tool": tool, "write": True} for tool, _arguments in calls
                ],
            },
        }
        return {"calls": calls, "evidence": evidence, "preview": preview}


@dataclass(frozen=True, slots=True)
class PreparedAddOutfitPreviewService:
    state_builder: PreparedAddOutfitStateBuilder
    handled_errors: tuple[type[BaseException], ...]

    def preview(self, params: dict[str, Any] | None = None) -> dict[str, Any]:
        try:
            return self.state_builder.build(params or {})["preview"]
        except self.handled_errors as exc:
            return {"ok": False, "preview": True, "error": str(exc)}


@dataclass(frozen=True, slots=True)
class PreparedAddOutfitPreparer:
    state_builder: PreparedAddOutfitStateBuilder
    nonce_hex: Callable[[int], str]

    def prepare(
        self, arguments: dict[str, Any], preview: Any
    ) -> tuple[dict[str, Any], Any]:
        if PREPARED_UNITY_EXECUTION_ARGUMENT_KEY in arguments:
            raise RuntimeError(
                "Caller may not provide the reserved prepared Unity execution key."
            )
        if ADD_OUTFIT_CONTINUATION_NONCE_KEY in arguments:
            raise RuntimeError(
                "Caller may not provide the reserved Add Outfit continuation nonce."
            )
        prepared_arguments = copy.deepcopy(arguments)
        prepared_arguments[ADD_OUTFIT_CONTINUATION_NONCE_KEY] = self.nonce_hex(32)
        state = self.state_builder.build(prepared_arguments)
        return (
            install_prepared_calls(
                prepared_arguments, state["calls"], state["evidence"]
            ),
            state["preview"],
        )


def require_add_outfit_receipt(
    expected: dict[str, Any], actual: dict[str, Any], label: str
) -> None:
    for key, value in expected.items():
        if actual.get(key) != value:
            raise RuntimeError(
                f"Add Outfit {label} receipt did not match approved {key}."
            )


@dataclass(frozen=True, slots=True)
class PreparedAddOutfitApprovedWritePorts:
    state_builder: PreparedAddOutfitStateBuilder
    digest: Callable[[Any], str]
    verify_project_identity: Callable[[dict[str, Any]], Path]
    require_evidence: Callable[[Any, Any, str], None]
    load_settings: Callable[[dict[str, Any]], Any]
    instantiate: Callable[[Any, dict[str, Any]], dict[str, Any]]
    unpack: Callable[[Any, dict[str, Any]], dict[str, Any]]
    start_setup: Callable[[Any, dict[str, Any]], dict[str, Any]]
    poll_setup: Callable[[Any, dict[str, Any], dict[str, Any]], dict[str, Any]]
    add_wardrobe: Callable[[Any, dict[str, Any]], dict[str, Any]]
    read_gameobject: Callable[[dict[str, Any]], dict[str, Any]]
    read_wardrobe: Callable[[dict[str, Any]], dict[str, Any]]
    log: Callable[[str, str, str, dict[str, Any]], None]
    map_error: Callable[[Exception], Exception]
    handled_errors: tuple[type[BaseException], ...]


class PreparedAddOutfitApprovedWriteService:
    """Execute only approval-sealed Add Outfit calls through fixed write ports."""

    def __init__(self, ports: PreparedAddOutfitApprovedWritePorts) -> None:
        self._ports = ports

    def _validate_before_write(
        self, arguments: dict[str, Any]
    ) -> tuple[dict[str, Any], dict[str, Any], list[PreparedAddOutfitCall]]:
        evidence = prepared_evidence(arguments)
        if (
            not isinstance(evidence, dict)
            or evidence.get("schema") != "vrcforge.prepared-add-outfit.v1"
        ):
            raise RuntimeError("Prepared Add Outfit evidence is invalid.")
        read_facts = evidence.get("readFacts")
        if (
            not isinstance(read_facts, dict)
            or self._ports.digest(read_facts) != evidence.get("readFactsSha256")
        ):
            raise RuntimeError("Prepared Add Outfit read facts are invalid.")
        project_identity = evidence.get("projectIdentity")
        if not isinstance(project_identity, dict):
            raise RuntimeError("Prepared Add Outfit project identity is invalid.")
        self._ports.verify_project_identity(project_identity)
        live = self._ports.state_builder.build(
            {
                key: value
                for key, value in arguments.items()
                if key != PREPARED_UNITY_EXECUTION_ARGUMENT_KEY
            }
        )
        live_calls = live["calls"]
        if (
            self._ports.digest(live["evidence"]["readFacts"])
            != evidence.get("readFactsSha256")
        ):
            raise RuntimeError("Add Outfit read facts drifted after approval.")
        if (
            self._ports.digest(
                [
                    {"tool": tool, "arguments": call_args}
                    for tool, call_args in live_calls
                ]
            )
            != evidence.get("callsSha256")
        ):
            raise RuntimeError("Add Outfit Core calls drifted after approval.")
        return evidence, read_facts, live_calls

    def execute(self, arguments: dict[str, Any]) -> dict[str, Any]:
        steps: list[dict[str, Any]] = []
        writes_started = False
        try:
            evidence, read_facts, live_calls = self._validate_before_write(arguments)
            settings = self._ports.load_settings(arguments)
            settings.unity_mcp_timeout_seconds = max(
                int(settings.unity_mcp_timeout_seconds or 30), 300
            )
            instantiate_global_id = ""
            wardrobe_receipt_fingerprint = ""
            writers = {
                "vrc_instantiate_prefab": self._ports.instantiate,
                "vrc_unpack_prefab": self._ports.unpack,
                "vrc_setup_outfit": self._ports.start_setup,
                "vrc_add_wardrobe_outfit": self._ports.add_wardrobe,
            }
            for index, (expected_tool, expected_arguments) in enumerate(live_calls):
                tool_name, tool_arguments = prepared_call(arguments, index)
                if tool_name != expected_tool:
                    raise RuntimeError(
                        "Prepared Add Outfit Core call order is invalid."
                    )
                self._ports.require_evidence(
                    expected_arguments,
                    tool_arguments,
                    "Add Outfit Core arguments",
                )
                writes_started = True
                payload = writers[tool_name](settings, tool_arguments)
                if payload.get("ok") is not True:
                    raise RuntimeError(
                        payload.get("error") or f"{tool_name} failed."
                    )
                if tool_name == "vrc_instantiate_prefab":
                    require_add_outfit_receipt(
                        {
                            "assetPath": expected_arguments["assetPath"],
                            "gameObjectPath": expected_arguments["expectedResultPath"],
                            "prefabGuid": expected_arguments["expectedPrefabGuid"],
                            "dependencyHash": expected_arguments[
                                "expectedAssetDependencyHash"
                            ],
                            "scenePath": expected_arguments["expectedScenePath"],
                            "parentGlobalObjectId": expected_arguments[
                                "expectedParentGlobalObjectId"
                            ],
                            "continuationRegistered": bool(
                                expected_arguments.get("approvedContinuationTools")
                            ),
                            "continuationCount": len(
                                expected_arguments.get("approvedContinuationTools")
                                or []
                            ),
                        },
                        payload,
                        "instantiate",
                    )
                    instantiate_global_id = str(
                        payload.get("globalObjectId") or ""
                    ).strip()
                    if not instantiate_global_id:
                        raise RuntimeError(
                            "Add Outfit instantiate receipt omitted GlobalObjectId."
                        )
                elif tool_name == "vrc_unpack_prefab":
                    require_add_outfit_receipt(
                        {
                            "gameObjectPath": expected_arguments["gameObjectPath"],
                            "unpacked": True,
                            "continuationConsumed": bool(
                                expected_arguments.get("approvedObjectReceiptNonce")
                            ),
                        },
                        payload,
                        "unpack",
                    )
                    instantiate_global_id = str(
                        payload.get("globalObjectId") or ""
                    ).strip()
                    if not instantiate_global_id:
                        raise RuntimeError(
                            "Add Outfit unpack receipt omitted GlobalObjectId."
                        )
                elif tool_name == "vrc_setup_outfit":
                    require_add_outfit_receipt(
                        {
                            "outfitGlobalObjectId": instantiate_global_id,
                            "continuationConsumed": False,
                        },
                        payload,
                        "setup start",
                    )
                    payload = self._ports.poll_setup(settings, {}, payload)
                    if payload.get("ok") is not True or str(
                        payload.get("status") or ""
                    ).lower() in {"error", "timeout"}:
                        raise RuntimeError(
                            payload.get("error")
                            or "Setup Outfit did not complete successfully."
                        )
                    require_add_outfit_receipt(
                        {
                            "outfitGlobalObjectId": instantiate_global_id,
                            "continuationConsumed": bool(
                                expected_arguments.get("approvedObjectReceiptNonce")
                            ),
                            "committed": True,
                            "commitState": "complete",
                            "checkpointRecoveryRequired": False,
                        },
                        payload,
                        "setup completion",
                    )
                elif tool_name == "vrc_add_wardrobe_outfit":
                    require_add_outfit_receipt(
                        {
                            "parameterName": expected_arguments["parameterName"],
                            "outfitName": expected_arguments["outfitName"],
                            "assignedValue": expected_arguments[
                                "expectedAssignedValue"
                            ],
                            "continuationConsumed": bool(
                                expected_arguments.get("approvedObjectReceiptNonce")
                            ),
                        },
                        payload,
                        "wardrobe",
                    )
                    wardrobe_receipt_fingerprint = str(
                        payload.get("wardrobeFingerprint") or ""
                    ).strip().lower()
                    if (
                        len(wardrobe_receipt_fingerprint) != 64
                        or any(
                            character not in "0123456789abcdef"
                            for character in wardrobe_receipt_fingerprint
                        )
                        or wardrobe_receipt_fingerprint
                        == str(
                            expected_arguments.get("expectedWardrobeFingerprint")
                            or ""
                        ).lower()
                    ):
                        raise RuntimeError(
                            "Add Outfit wardrobe receipt fingerprint is not a valid "
                            "post-write readback."
                        )
                steps.append(
                    {"tool": tool_name, "ok": True, "receipt": payload}
                )

            project_identity = evidence["projectIdentity"]
            final_object = canonical_add_outfit_gameobject(
                self._ports.read_gameobject(
                    {
                        "projectPath": project_identity["projectPath"],
                        "gameObjectPath": read_facts["outfitPath"],
                    }
                ),
                "final object",
            )
            if (
                final_object["gameObjectPath"] != read_facts["outfitPath"]
                or final_object["scenePath"] != read_facts["parent"]["scenePath"]
            ):
                raise RuntimeError(
                    "Add Outfit final object readback drifted from approval."
                )
            if (
                instantiate_global_id
                and final_object["globalObjectId"] != instantiate_global_id
            ):
                raise RuntimeError(
                    "Add Outfit final object GlobalObjectId changed after execution."
                )
            if evidence.get("manageWardrobe") is True:
                scan = self._ports.read_wardrobe(
                    {
                        "projectPath": project_identity["projectPath"],
                        "avatarPath": read_facts["avatar"]["gameObjectPath"],
                    }
                )
                if (
                    str(scan.get("fingerprint") or "").strip().lower()
                    != wardrobe_receipt_fingerprint
                ):
                    raise RuntimeError(
                        "Add Outfit final wardrobe fingerprint did not match the Core "
                        "write receipt."
                    )
                selected = next(
                    (
                        item
                        for item in (scan.get("wardrobes") or [])
                        if isinstance(item, dict)
                        and str(item.get("parameterName") or "")
                        == evidence.get("parameterName")
                    ),
                    None,
                )
                expected_value = read_facts.get("assignedValue")
                if not isinstance(selected, dict) or not any(
                    isinstance(item, dict)
                    and int(item.get("value", -1)) == int(expected_value)
                    for item in (selected.get("outfits") or [])
                ):
                    raise RuntimeError(
                        "Add Outfit wardrobe readback did not contain the approved value."
                    )
            self._ports.log(
                "info",
                "wardrobe",
                "Prepared Add Outfit workflow executed.",
                {
                    "outfitPath": read_facts["outfitPath"],
                    "parameterName": evidence.get("parameterName"),
                },
            )
            return {
                "ok": True,
                "preview": False,
                "committed": True,
                "outfitPath": read_facts["outfitPath"],
                "steps": steps,
                "finalObject": final_object,
            }
        except self._ports.handled_errors as exc:
            self._ports.log(
                "error",
                "wardrobe",
                "Prepared Add Outfit workflow failed.",
                {"error": str(exc)},
            )
            if writes_started:
                return {
                    "ok": False,
                    "committed": True,
                    "commitState": "unknown",
                    "checkpointRecoveryRequired": True,
                    "steps": steps,
                    "error": str(exc),
                }
            raise self._ports.map_error(exc) from exc
