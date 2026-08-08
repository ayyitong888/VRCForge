from __future__ import annotations

import copy
import os
from dataclasses import dataclass
from pathlib import Path, PurePosixPath
from typing import Any, Callable

from prepared_unity_execution import (
    PREPARED_UNITY_EXECUTION_ARGUMENT_KEY,
    install_prepared_calls,
    prepared_call,
    prepared_evidence,
)


PreparedImportCall = tuple[str, dict[str, Any]]


@dataclass(frozen=True, slots=True)
class PreparedOutfitImportPreparerPorts:
    plan_outfit_import: Callable[[dict[str, Any]], dict[str, Any]]
    plan_error_type: type[BaseException]
    map_plan_error: Callable[[BaseException], Exception]
    resolve_project_root: Callable[[dict[str, Any], dict[str, Any]], Path]
    capture_project_identity: Callable[[Path], dict[str, Any]]
    capture_regular_file: Callable[
        [Path, str], tuple[dict[str, Any], str]
    ]
    capture_directory: Callable[[Path, str], dict[str, Any]]
    prepare_loose_import: Callable[..., dict[str, Any]]
    prepare_zip_member: Callable[..., dict[str, Any]]
    normalize_archive_name: Callable[[str], str]
    digest: Callable[[Any], str]
    ensure_dict: Callable[[Any, str], dict[str, Any]]
    nonce_hex: Callable[[int], str]
    temp_parent: Path
    allowed_loose_suffixes: frozenset[str]


class PreparedOutfitImportPreparer:
    """Seal one outfit-import plan without owning any live Unity capability."""

    def __init__(self, ports: PreparedOutfitImportPreparerPorts) -> None:
        self._ports = ports

    def _path_identity(self, path: Path, label: str) -> dict[str, Any]:
        try:
            identity, digest = self._ports.capture_regular_file(
                path.expanduser(), label
            )
        except ValueError as exc:
            raise RuntimeError(str(exc)) from exc
        return {**identity, "sha256": digest}

    @staticmethod
    def _expected_asset_paths(plan_payload: dict[str, Any]) -> list[str]:
        paths: list[str] = []
        seen: set[str] = set()
        for raw_path in plan_payload.get("expectedAssetPaths") or []:
            asset_path = str(raw_path or "").replace("\\", "/").strip()
            parts = PurePosixPath(asset_path).parts
            if (
                len(parts) < 2
                or parts[0] != "Assets"
                or any(part in {"", ".", ".."} for part in parts)
                or "//" in asset_path
            ):
                raise RuntimeError("Prepared outfit expected asset path is invalid.")
            folded = asset_path.casefold()
            if folded in seen:
                raise RuntimeError(
                    "Prepared outfit expected asset paths contain a duplicate."
                )
            seen.add(folded)
            paths.append(asset_path)
        return paths

    def _temp_parent(self) -> Path:
        path = self._ports.temp_parent
        path.mkdir(parents=True, exist_ok=True)
        return path

    def _unitypackage_queue(
        self, plan_payload: dict[str, Any]
    ) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
        source = self._ports.ensure_dict(
            plan_payload.get("source"), "outfit import source"
        )
        raw_queue = source.get("importQueue")
        if not isinstance(raw_queue, list) or not raw_queue:
            dependency = (
                plan_payload.get("dependencyPreflight")
                if isinstance(plan_payload.get("dependencyPreflight"), dict)
                else {}
            )
            package_order = (
                dependency.get("packageOrder")
                if isinstance(dependency.get("packageOrder"), dict)
                else {}
            )
            raw_queue = (
                package_order.get("importQueue")
                if isinstance(package_order.get("importQueue"), list)
                else []
            )
        if not raw_queue:
            raw_queue = [
                {
                    "path": source.get("actualPackagePath") or source.get("path"),
                    "role": "target",
                    "order": 1,
                }
            ]
        queue: list[dict[str, Any]] = []
        materializations: list[dict[str, Any]] = []
        temp_parent = self._temp_parent()
        for index, raw in enumerate(raw_queue, start=1):
            if not isinstance(raw, dict):
                raise RuntimeError("Prepared outfit import queue item is invalid.")
            source_type = str(raw.get("sourceType") or "").strip().lower()
            materialization_index: int | None = None
            folder_root_identity: dict[str, Any] | None = None
            if source_type == "zip":
                source = self._ports.ensure_dict(
                    plan_payload.get("source"), "outfit import source"
                )
                container_value = str(
                    raw.get("containerPath") or source.get("path") or ""
                ).strip()
                container_path = Path(
                    os.path.abspath(Path(container_value).expanduser())
                )
                entry_path = self._ports.normalize_archive_name(
                    str(raw.get("path") or "")
                )
                target_name = (
                    f"prepared-{self._ports.nonce_hex(16)}-{index:04d}.unitypackage"
                )
                try:
                    materialization = self._ports.prepare_zip_member(
                        source=container_path,
                        temp_parent=temp_parent,
                        selected_members=[
                            {"path": entry_path, "targetName": target_name}
                        ],
                    )
                except ValueError as exc:
                    raise RuntimeError(str(exc)) from exc
                selected = self._ports.ensure_dict(
                    materialization["selected"][0],
                    "prepared nested UnityPackage",
                )
                materialization_index = len(materializations)
                materializations.append(materialization)
                identity = {
                    "path": str(temp_parent / target_name),
                    "sha256": selected["sha256"],
                    "size": selected["size"],
                }
            else:
                raw_path = str(raw.get("actualPackagePath") or "").strip()
                if not raw_path and source_type == "folder":
                    source = self._ports.ensure_dict(
                        plan_payload.get("source"), "outfit import source"
                    )
                    source_root = Path(
                        os.path.abspath(
                            Path(str(source.get("path") or "")).expanduser()
                        )
                    )
                    try:
                        folder_root_identity = self._ports.capture_directory(
                            source_root, "Prepared outfit source folder"
                        )
                    except ValueError as exc:
                        raise RuntimeError(str(exc)) from exc
                    relative = PurePosixPath(
                        self._ports.normalize_archive_name(str(raw.get("path") or ""))
                    )
                    if relative.is_absolute() or any(
                        part in {"", ".", ".."} for part in relative.parts
                    ):
                        raise RuntimeError(
                            "Prepared outfit folder queue path is unsafe."
                        )
                    path = source_root.joinpath(*relative.parts)
                else:
                    raw_path = raw_path or str(raw.get("path") or "").strip()
                    path = Path(os.path.abspath(Path(raw_path).expanduser()))
                if path.suffix.lower() != ".unitypackage":
                    raise RuntimeError(
                        f"Prepared outfit import queue item is not a UnityPackage: {path}"
                    )
                identity = self._path_identity(
                    path, "Prepared outfit UnityPackage"
                )
            queue.append(
                {
                    "order": int(raw.get("order") or index),
                    "role": str(raw.get("role") or "target"),
                    "identity": identity,
                    "materializationIndex": materialization_index,
                    "folderRootIdentity": folder_root_identity,
                }
            )
        queue.sort(key=lambda item: item["order"])
        if len({item["order"] for item in queue}) != len(queue):
            raise RuntimeError(
                "Prepared outfit import queue has duplicate order values."
            )
        return queue, materializations

    def prepare(
        self, arguments: dict[str, Any], preview: Any
    ) -> tuple[dict[str, Any], Any]:
        if PREPARED_UNITY_EXECUTION_ARGUMENT_KEY in arguments:
            raise RuntimeError(
                "Caller may not provide the reserved prepared Unity execution key."
            )
        try:
            plan = self._ports.plan_outfit_import(arguments)
        except self._ports.plan_error_type as exc:
            raise self._ports.map_plan_error(exc) from exc
        plan_payload = self._ports.ensure_dict(
            plan.get("plan"), "outfit import plan"
        )
        if not plan_payload.get("readyToApply"):
            raise RuntimeError("Outfit import plan is not ready to apply.")
        kind = str(plan_payload.get("kind") or "")
        project_root = self._ports.resolve_project_root(arguments, plan_payload)
        project_identity = self._ports.capture_project_identity(project_root)
        if kind == "loose_prefab_copy":
            source = self._ports.ensure_dict(
                plan_payload.get("source"), "outfit import source"
            )
            try:
                loose_plan = self._ports.prepare_loose_import(
                    source_root=Path(str(source.get("path") or "")),
                    project_root=project_root,
                    target_folder=str(
                        plan_payload.get("targetFolder")
                        or "Assets/VRCForge/ImportedOutfits"
                    ),
                    allowed_suffixes=self._ports.allowed_loose_suffixes,
                )
            except ValueError as exc:
                raise RuntimeError(str(exc)) from exc
            calls: list[PreparedImportCall] = [
                (
                    "vrc_refresh_asset_database",
                    {
                        "projectPath": project_identity["projectPath"],
                        "resolvePackages": False,
                        "packageResolveTimeoutSeconds": 120,
                    },
                )
            ]
            evidence = {
                "kind": kind,
                "planSha256": self._ports.digest(plan_payload),
                "plan": plan_payload,
                "projectIdentity": project_identity,
                "loosePlan": loose_plan,
            }
            return install_prepared_calls(arguments, calls, evidence), {
                "ok": True,
                "plan": plan_payload,
                "preparedFileCount": len(loose_plan["files"]),
            }
        if kind not in {"unitypackage_import", "unitypackage_import_sequence"}:
            raise RuntimeError(
                f"Unsupported prepared outfit import branch: {kind or 'unknown'}"
            )
        queue, materializations = self._unitypackage_queue(plan_payload)
        expected_asset_paths = self._expected_asset_paths(plan_payload)
        target_indexes = [
            index for index, item in enumerate(queue) if item["role"] == "target"
        ]
        if len(target_indexes) != 1:
            raise RuntimeError(
                "Prepared outfit import queue must contain exactly one target package."
            )
        calls = [
            (
                "vrc_import_unitypackage",
                {
                    "projectPath": project_identity["projectPath"],
                    "unityPackagePath": item["identity"]["path"],
                    "expectedSha256": item["identity"]["sha256"],
                    "expectedSize": item["identity"]["size"],
                    "expectedAssetPaths": (
                        expected_asset_paths if index == target_indexes[0] else []
                    ),
                    "interactive": False,
                },
            )
            for index, item in enumerate(queue)
        ]
        calls.append(
            (
                "vrc_refresh_asset_database",
                {
                    "projectPath": project_identity["projectPath"],
                    "resolvePackages": False,
                    "packageResolveTimeoutSeconds": 120,
                },
            )
        )
        evidence = {
            "kind": kind,
            "planSha256": self._ports.digest(plan_payload),
            "plan": plan_payload,
            "projectIdentity": project_identity,
            "queue": queue,
            "materializations": materializations,
            "expectedAssetPaths": expected_asset_paths,
            "targetIndex": target_indexes[0],
        }
        return install_prepared_calls(arguments, calls, evidence), {
            "ok": True,
            "plan": plan_payload,
            "preparedQueueCount": len(queue),
        }


@dataclass(frozen=True, slots=True)
class PreparedOutfitImportApprovedWritePorts:
    digest: Callable[[Any], str]
    verify_project_identity: Callable[[dict[str, Any]], Path]
    require_evidence: Callable[[Any, Any, str], None]
    execute_loose_import: Callable[[dict[str, Any]], dict[str, Any]]
    execute_zip_member: Callable[[dict[str, Any]], dict[str, Any]]
    cleanup_zip_member: Callable[[dict[str, Any]], str]
    verify_regular_file: Callable[[dict[str, Any], str, str], Path]
    verify_directory: Callable[[dict[str, Any], str], Path]
    load_settings: Callable[[dict[str, Any]], Any]
    start_import: Callable[[Any, dict[str, Any]], dict[str, Any]]
    poll_import: Callable[[Any, str], dict[str, Any]]
    refresh_assets: Callable[[Any, dict[str, Any]], dict[str, Any]]
    monotonic: Callable[[], float]
    sleep: Callable[[float], None]
    timeout_seconds: Callable[[], float]
    poll_seconds: Callable[[], float]
    log: Callable[[str, str, str, dict[str, Any]], None]
    map_error: Callable[[Exception], Exception]
    handled_errors: tuple[type[BaseException], ...]


class PreparedOutfitImportApprovedWriteService:
    """Execute only approval-sealed import, poll, refresh and owned cleanup steps."""

    def __init__(self, ports: PreparedOutfitImportApprovedWritePorts) -> None:
        self._ports = ports

    @staticmethod
    def _asset_receipts(
        payload: dict[str, Any], expected_asset_paths: list[str]
    ) -> list[dict[str, str]]:
        raw_receipts = payload.get("expectedAssets")
        if not isinstance(raw_receipts, list) or len(raw_receipts) != len(
            expected_asset_paths
        ):
            raise RuntimeError(
                "Unity Core expected-asset receipt count did not match approval."
            )
        receipts: list[dict[str, str]] = []
        for expected_path, raw_receipt in zip(
            expected_asset_paths, raw_receipts, strict=True
        ):
            if not isinstance(raw_receipt, dict):
                raise RuntimeError("Unity Core expected-asset receipt is invalid.")
            asset_path = str(raw_receipt.get("assetPath") or "").replace(
                "\\", "/"
            )
            guid = str(raw_receipt.get("guid") or "").strip().lower()
            asset_type = str(raw_receipt.get("assetType") or "").strip()
            if (
                asset_path != expected_path
                or len(guid) != 32
                or any(character not in "0123456789abcdef" for character in guid)
                or not asset_type
            ):
                raise RuntimeError(
                    "Unity Core expected-asset readback did not match approval."
                )
            receipts.append(
                {"assetPath": asset_path, "guid": guid, "assetType": asset_type}
            )
        return receipts

    @staticmethod
    def _job_receipt(
        payload: dict[str, Any],
        project_identity: dict[str, Any],
        identity: dict[str, Any],
        expected_asset_paths: list[str],
    ) -> str:
        job_id = str(payload.get("jobId") or "").strip().lower()
        if len(job_id) != 32 or any(
            character not in "0123456789abcdef" for character in job_id
        ):
            raise RuntimeError("Unity Core import job receipt is invalid.")
        try:
            received_size = int(payload.get("expectedSize", -1))
            expected_size = int(identity["size"])
        except (TypeError, ValueError, KeyError) as exc:
            raise RuntimeError(
                "Unity Core import receipt project/path did not match the prepared call."
            ) from exc
        if (
            str(payload.get("projectPath") or "")
            != project_identity["projectPath"]
            or str(payload.get("unityPackagePath") or "").replace("\\", "/")
            != str(identity["path"]).replace("\\", "/")
            or str(payload.get("expectedSha256") or "").lower()
            != str(identity["sha256"]).lower()
            or received_size != expected_size
        ):
            raise RuntimeError(
                "Unity Core import receipt project/path did not match the prepared call."
            )
        raw_paths = payload.get("expectedAssetPaths")
        if not isinstance(raw_paths, list) or [
            str(path).replace("\\", "/") for path in raw_paths
        ] != expected_asset_paths:
            raise RuntimeError(
                "Unity Core import receipt asset paths did not match approval."
            )
        return job_id

    def _wait_for_job(
        self, settings: Any, initial_payload: dict[str, Any]
    ) -> dict[str, Any]:
        job_id = str(initial_payload.get("jobId") or "").strip().lower()
        if len(job_id) != 32 or any(
            character not in "0123456789abcdef" for character in job_id
        ):
            raise RuntimeError("Unity Core import job id is invalid.")
        poll_settings = copy.copy(settings)
        try:
            poll_settings.unity_mcp_timeout_seconds = min(
                int(getattr(settings, "unity_mcp_timeout_seconds", 30) or 30), 8
            )
        except Exception:  # noqa: BLE001 - minimal settings fakes are supported.
            pass
        deadline = self._ports.monotonic() + self._ports.timeout_seconds()
        payload = initial_payload
        while payload.get("pending") is True and self._ports.monotonic() < deadline:
            self._ports.sleep(
                min(
                    self._ports.poll_seconds(),
                    max(0.0, deadline - self._ports.monotonic()),
                )
            )
            if self._ports.monotonic() >= deadline:
                break
            payload = self._ports.poll_import(poll_settings, job_id)
            if str(payload.get("jobId") or "").strip().lower() != job_id:
                raise RuntimeError(
                    "Unity Core import job identity drifted while polling."
                )
        if payload.get("pending") is True:
            return {
                "ok": False,
                "pending": False,
                "status": "timeout",
                "jobId": job_id,
                "mutationStarted": True,
                "committed": True,
                "commitState": "unknown",
                "checkpointRecoveryRequired": True,
                "error": (
                    "UnityPackage import did not reach a terminal state before timeout."
                ),
            }
        return payload

    def execute(self, arguments: dict[str, Any]) -> dict[str, Any]:
        imports: list[dict[str, Any]] = []
        write_started = False
        import_readback_pending = False
        loose_copied: dict[str, Any] | None = None
        materialization_receipts: list[dict[str, Any]] = []

        def cleanup_materializations() -> str:
            errors: list[str] = []
            for receipt in reversed(materialization_receipts):
                error = self._ports.cleanup_zip_member(receipt)
                if error:
                    errors.append(error)
            materialization_receipts.clear()
            return "; ".join(errors)

        try:
            evidence = prepared_evidence(arguments)
            if not isinstance(evidence, dict):
                raise RuntimeError("Prepared outfit import evidence is invalid.")
            kind = str(evidence.get("kind") or "")
            if kind not in {
                "unitypackage_import",
                "unitypackage_import_sequence",
                "loose_prefab_copy",
            }:
                raise RuntimeError("Prepared outfit import branch is invalid.")
            plan_payload = evidence.get("plan")
            if (
                not isinstance(plan_payload, dict)
                or self._ports.digest(plan_payload) != evidence.get("planSha256")
            ):
                raise RuntimeError("Prepared outfit import plan evidence is invalid.")
            project_identity = evidence.get("projectIdentity")
            if kind == "loose_prefab_copy":
                if not isinstance(project_identity, dict):
                    raise RuntimeError(
                        "Prepared loose outfit project identity is invalid."
                    )
                self._ports.verify_project_identity(project_identity)
                loose_plan = evidence.get("loosePlan")
                if not isinstance(loose_plan, dict):
                    raise RuntimeError("Prepared loose outfit plan is missing.")
                try:
                    loose_copied = self._ports.execute_loose_import(loose_plan)
                    write_started = True
                except RuntimeError as exc:
                    return {
                        "ok": False,
                        "committed": True,
                        "commitState": "unknown",
                        "checkpointRecoveryRequired": True,
                        "kind": kind,
                        "error": str(exc),
                    }
                self._ports.verify_project_identity(project_identity)
                refresh_tool, refresh_arguments = prepared_call(arguments, 0)
                expected_refresh = {
                    "projectPath": project_identity["projectPath"],
                    "resolvePackages": False,
                    "packageResolveTimeoutSeconds": 120,
                }
                if refresh_tool != "vrc_refresh_asset_database":
                    raise RuntimeError(
                        "Prepared loose outfit refresh call is invalid."
                    )
                self._ports.require_evidence(
                    expected_refresh, refresh_arguments, "refresh arguments"
                )
                settings = self._ports.load_settings(arguments)
                settings.unity_mcp_timeout_seconds = max(
                    int(settings.unity_mcp_timeout_seconds or 30), 150
                )
                refresh = self._ports.refresh_assets(settings, refresh_arguments)
                if refresh.get("ok") is not True:
                    return {
                        "ok": False,
                        "committed": True,
                        "commitState": "partial",
                        "checkpointRecoveryRequired": True,
                        "kind": kind,
                        "copiedFiles": loose_copied.get("copiedFiles") or [],
                        "assetDatabaseRefresh": refresh,
                        "error": refresh.get("error")
                        or "Asset refresh failed after loose outfit import.",
                    }
                write_started = False
                prefab_assets = [
                    str(path)
                    for path in loose_copied.get("copiedFiles") or []
                    if str(path).lower().endswith(".prefab")
                ]
                return {
                    "ok": True,
                    "kind": kind,
                    **loose_copied,
                    "importedPrefabCandidates": prefab_assets,
                    "assetDatabaseRefresh": refresh,
                    "nextTool": "vrcforge_add_outfit",
                }
            queue = evidence.get("queue")
            if (
                not isinstance(project_identity, dict)
                or not isinstance(queue, list)
                or not queue
            ):
                raise RuntimeError("Prepared outfit import evidence is incomplete.")
            self._ports.verify_project_identity(project_identity)
            materializations = evidence.get("materializations") or []
            if not isinstance(materializations, list):
                raise RuntimeError(
                    "Prepared outfit materialization evidence is invalid."
                )
            for facts in materializations:
                if not isinstance(facts, dict):
                    raise RuntimeError(
                        "Prepared outfit materialization item is invalid."
                    )
                materialization_receipts.append(
                    self._ports.execute_zip_member(facts)
                )
            settings = self._ports.load_settings(arguments)
            settings.unity_mcp_timeout_seconds = max(
                int(settings.unity_mcp_timeout_seconds or 30), 300
            )
            for index, item in enumerate(queue):
                self._ports.verify_project_identity(project_identity)
                identity = item.get("identity") if isinstance(item, dict) else None
                if not isinstance(identity, dict):
                    raise RuntimeError(
                        "Prepared outfit import queue identity is invalid."
                    )
                materialization_index = (
                    item.get("materializationIndex") if isinstance(item, dict) else None
                )
                try:
                    if materialization_index is not None:
                        receipt = materialization_receipts[
                            int(materialization_index)
                        ]
                        owned = receipt.get("ownedFiles") if isinstance(receipt, dict) else None
                        if not isinstance(owned, list) or len(owned) != 1:
                            raise RuntimeError(
                                "Prepared nested UnityPackage receipt is invalid."
                            )
                        receipt_identity = owned[0]
                        if not isinstance(receipt_identity, dict):
                            raise RuntimeError(
                                "Prepared nested UnityPackage receipt is invalid."
                            )
                        if (
                            str(receipt_identity.get("path") or "")
                            != str(identity.get("path") or "")
                            or int(receipt_identity.get("size", -1))
                            != int(identity.get("size", -2))
                            or str(receipt_identity.get("sha256") or "")
                            != str(identity.get("sha256") or "")
                        ):
                            raise RuntimeError(
                                "Prepared nested UnityPackage receipt drifted from approval."
                            )
                        self._ports.verify_regular_file(
                            {
                                key: value
                                for key, value in receipt_identity.items()
                                if key != "sha256"
                            },
                            str(identity.get("sha256") or ""),
                            "Prepared nested UnityPackage",
                        )
                    else:
                        folder_identity = (
                            item.get("folderRootIdentity")
                            if isinstance(item, dict)
                            else None
                        )
                        if folder_identity is not None:
                            self._ports.verify_directory(
                                folder_identity,
                                "Prepared outfit source folder",
                            )
                        self._ports.verify_regular_file(
                            {
                                key: value
                                for key, value in identity.items()
                                if key != "sha256"
                            },
                            str(identity.get("sha256") or ""),
                            "Prepared outfit UnityPackage",
                        )
                except ValueError as exc:
                    raise RuntimeError(str(exc)) from exc
                tool_name, tool_arguments = prepared_call(arguments, index)
                expected_asset_paths = (
                    evidence.get("expectedAssetPaths")
                    if index == evidence.get("targetIndex")
                    else []
                )
                if not isinstance(expected_asset_paths, list):
                    raise RuntimeError(
                        "Prepared outfit expected asset evidence is invalid."
                    )
                expected = {
                    "projectPath": project_identity["projectPath"],
                    "unityPackagePath": identity["path"],
                    "expectedSha256": identity["sha256"],
                    "expectedSize": identity["size"],
                    "expectedAssetPaths": expected_asset_paths,
                    "interactive": False,
                }
                if tool_name != "vrc_import_unitypackage":
                    raise RuntimeError(
                        "Prepared outfit import Core call is invalid."
                    )
                self._ports.require_evidence(
                    expected, tool_arguments, "Core arguments"
                )
                write_started = True
                payload = self._ports.start_import(settings, tool_arguments)
                import_readback_pending = (
                    payload.get("mutationStarted") is True
                    or payload.get("pending") is True
                )
                if payload.get("ok") is not True:
                    cleanup_error = cleanup_materializations()
                    return {
                        "ok": False,
                        "committed": True,
                        "commitState": "unknown",
                        "checkpointRecoveryRequired": True,
                        "kind": kind,
                        "unityImports": imports,
                        "temporaryCleanupError": cleanup_error or None,
                        "error": payload.get("error")
                        or "UnityPackage import failed after Core invocation.",
                    }
                job_id = self._job_receipt(
                    payload, project_identity, identity, expected_asset_paths
                )
                if payload.get("pending") is True:
                    payload = self._wait_for_job(settings, payload)
                if (
                    payload.get("ok") is not True
                    or str(payload.get("status") or "") != "completed"
                ):
                    cleanup_error = cleanup_materializations()
                    return {
                        "ok": False,
                        "committed": True,
                        "commitState": "unknown",
                        "checkpointRecoveryRequired": True,
                        "kind": kind,
                        "unityImports": imports,
                        "temporaryCleanupError": cleanup_error or None,
                        "error": payload.get("error")
                        or payload.get("reason")
                        or "UnityPackage import did not complete.",
                    }
                if str(payload.get("jobId") or "").strip().lower() != job_id:
                    raise RuntimeError(
                        "Unity Core import terminal job identity drifted."
                    )
                self._job_receipt(
                    payload, project_identity, identity, expected_asset_paths
                )
                asset_receipts = self._asset_receipts(
                    payload, expected_asset_paths
                )
                imports.append(
                    {
                        "ok": True,
                        "order": item["order"],
                        "role": item["role"],
                        "path": identity["path"],
                        "expectedAssets": asset_receipts,
                        "unityImport": payload,
                    }
                )
                import_readback_pending = False
                write_started = False
            self._ports.verify_project_identity(project_identity)
            refresh_tool, refresh_arguments = prepared_call(arguments, len(queue))
            expected_refresh = {
                "projectPath": project_identity["projectPath"],
                "resolvePackages": False,
                "packageResolveTimeoutSeconds": 120,
            }
            if refresh_tool != "vrc_refresh_asset_database":
                raise RuntimeError(
                    "Prepared outfit import refresh call is invalid."
                )
            self._ports.require_evidence(
                expected_refresh, refresh_arguments, "refresh arguments"
            )
            write_started = True
            refresh = self._ports.refresh_assets(settings, refresh_arguments)
            if refresh.get("ok") is not True:
                cleanup_error = cleanup_materializations()
                return {
                    "ok": False,
                    "committed": True,
                    "commitState": "partial",
                    "checkpointRecoveryRequired": True,
                    "kind": kind,
                    "unityImports": imports,
                    "assetDatabaseRefresh": refresh,
                    "temporaryCleanupError": cleanup_error or None,
                    "error": refresh.get("error")
                    or "Asset refresh failed after UnityPackage import.",
                }
            write_started = False
            self._ports.verify_project_identity(project_identity)
            cleanup_error = cleanup_materializations()
            if cleanup_error:
                return {
                    "ok": False,
                    "committed": True,
                    "commitState": "complete",
                    "checkpointRecoveryRequired": False,
                    "temporaryCleanupRequired": True,
                    "kind": kind,
                    "unityImports": imports,
                    "assetDatabaseRefresh": refresh,
                    "error": cleanup_error,
                }
            return {
                "ok": True,
                "kind": kind,
                "unityImports": imports,
                "assetDatabaseRefresh": refresh,
                "importedPrefabCandidates": [
                    path
                    for path in evidence.get("expectedAssetPaths") or []
                    if str(path).lower().endswith(".prefab")
                ],
                "nextTool": "vrcforge_add_outfit",
            }
        except self._ports.handled_errors as exc:
            cleanup_error = cleanup_materializations()
            self._ports.log(
                "error",
                "outfit",
                "Prepared outfit import failed.",
                {"error": str(exc)},
            )
            if write_started or imports or loose_copied is not None:
                return {
                    "ok": False,
                    "committed": True,
                    "commitState": (
                        "unknown"
                        if import_readback_pending
                        or (write_started and loose_copied is None)
                        else "partial"
                    ),
                    "checkpointRecoveryRequired": True,
                    "kind": str(locals().get("kind") or ""),
                    "unityImports": imports,
                    "temporaryCleanupError": cleanup_error or None,
                    "error": str(exc),
                }
            if cleanup_error:
                mapped = RuntimeError(
                    f"{exc}; temporary cleanup failed: {cleanup_error}"
                )
                raise self._ports.map_error(mapped) from exc
            raise self._ports.map_error(exc) from exc
