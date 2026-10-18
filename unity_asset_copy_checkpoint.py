"""Derive an exact create-new checkpoint footprint from asset-copy preview evidence."""
from __future__ import annotations

from copy import deepcopy
import os
from pathlib import Path, PurePosixPath
from typing import Any, Callable

from project_asset_copy import GENERATED_ROOT, TOOL_NAME


_ALLOWED_EXTENSIONS = {".controller", ".asset", ".anim", ".overridecontroller", ".mat"}


def prepare_asset_copy_checkpoint(
    project_root: Path,
    arguments: dict[str, Any],
    *,
    revalidate: Callable[[dict[str, Any], Any], tuple[dict[str, Any], Any]],
    prepare: Callable[..., dict[str, Any]],
) -> dict[str, Any]:
    """Revalidate one canonical copy request and checkpoint only its new outputs."""

    def reject(message: str) -> dict[str, Any]:
        return {
            "ok": False,
            "blocking": True,
            "code": "checkpoint_scope_unresolved",
            "error": message,
            "mutationStarted": False,
            "saved": False,
        }

    approved = deepcopy(arguments) if isinstance(arguments, dict) else {}
    approved.pop("_vrcforge_user_constraints", None)
    if approved.get("toolName") != TOOL_NAME:
        return reject("The approved write is not the project asset-copy tool.")
    if _resolved_path(approved.get("projectPath")) != project_root.resolve():
        return reject("The approved asset copy targets another Unity project.")
    try:
        refreshed, _preview = revalidate(deepcopy(approved), None)
    except Exception as exc:
        return reject(f"The approved asset copy could not be revalidated: {exc}")
    if refreshed != approved:
        return reject("The approved asset-copy identity changed before checkpointing.")

    nested = refreshed.get("arguments")
    if not isinstance(nested, dict):
        return reject("The canonical asset-copy arguments are missing.")
    if _resolved_path(nested.get("expectedProjectPath")) != project_root.resolve():
        return reject("The canonical asset copy project changed before checkpointing.")
    if nested.get("preview") is not False or nested.get("overwrite") is not False:
        return reject("The canonical asset copy is not create-new only.")

    try:
        destinations, directories = _canonical_footprint(nested)
        absent_files = sorted({
            *destinations,
            *(name + ".meta" for name in destinations),
            *(name + ".meta" for name in directories),
        })
        absent_directories = sorted(set(directories))
        for name in [*absent_files, *absent_directories]:
            target = project_root.joinpath(*PurePosixPath(name).parts)
            target.resolve().relative_to(project_root.resolve())
            if os.path.lexists(target):
                raise ValueError(f"The approved absent asset-copy destination appeared: {name}")
    except (OSError, RuntimeError, ValueError) as exc:
        return reject(str(exc))

    prepared = prepare(project_root, checkpoint_files=absent_files)
    if prepared.get("ok") is not True:
        return prepared
    return {
        **prepared,
        "assetFootprint": {
            "schema": "vrcforge.asset_checkpoint_footprint.v1",
            "archiveFiles": [],
            "archiveAbsentFiles": absent_files,
            "archiveAbsentDirectories": absent_directories,
        },
        "archiveScopeReason": "revalidated_project_asset_copy_create_new_footprint",
    }


def _resolved_path(value: Any) -> Path | None:
    text = str(value or "").strip()
    return Path(text).resolve() if text and Path(text).is_absolute() else None


def _asset_path(value: Any, *, destination: bool) -> str:
    name = str(value or "")
    if (
        not name.startswith("Assets/")
        or "\\" in name
        or name != PurePosixPath(name).as_posix()
        or any(part in {"", ".", ".."} for part in name.split("/"))
        or PurePosixPath(name).suffix.casefold() not in _ALLOWED_EXTENSIONS
        or (destination and not name.startswith(GENERATED_ROOT + "/"))
    ):
        raise ValueError("The canonical asset-copy path or file type is unsupported.")
    return name


def _canonical_footprint(arguments: dict[str, Any]) -> tuple[list[str], list[str]]:
    if "copies" in arguments:
        rows = arguments.get("copies")
        previews = arguments.get("expectedCopies")
        if (
            not isinstance(rows, list)
            or not rows
            or not isinstance(previews, list)
            or len(rows) != len(previews)
        ):
            raise ValueError("The canonical asset-copy batch evidence is incomplete.")
        destinations: list[str] = []
        for row, preview in zip(rows, previews):
            if not isinstance(row, dict) or not isinstance(preview, dict):
                raise ValueError("The canonical asset-copy batch row is invalid.")
            source = _asset_path(row.get("sourceAssetPath"), destination=False)
            destination = _asset_path(row.get("destinationAssetPath"), destination=True)
            source_preview = preview.get("source") if isinstance(preview.get("source"), dict) else {}
            target_preview = preview.get("target") if isinstance(preview.get("target"), dict) else {}
            if (
                PurePosixPath(source).suffix.casefold() != PurePosixPath(destination).suffix.casefold()
                or source_preview.get("assetPath") != source
                or target_preview.get("assetPath") != destination
                or target_preview.get("generatedRootExists") is not True
                or target_preview.get("assetExists") is not False
                or target_preview.get("metaExists") is not False
                or target_preview.get("createNew") is not True
                or target_preview.get("folderCreation") is not None
            ):
                raise ValueError("The canonical asset-copy batch preview is not create-new only.")
            destinations.append(destination)
        if len({name.casefold() for name in destinations}) != len(destinations):
            raise ValueError("The canonical asset-copy batch has duplicate destinations.")
        return destinations, []

    source = _asset_path(arguments.get("sourceAssetPath"), destination=False)
    destination = _asset_path(arguments.get("destinationAssetPath"), destination=True)
    if (
        PurePosixPath(source).suffix.casefold() != PurePosixPath(destination).suffix.casefold()
        or arguments.get("expectedDestinationAbsent") is not True
    ):
        raise ValueError("The canonical asset copy is not an absent same-type destination.")
    raw_directories = arguments.get("expectedCreatedFolders", [])
    if not isinstance(raw_directories, list):
        raise ValueError("The canonical asset-copy folder plan is invalid.")
    directories = []
    for raw in raw_directories:
        name = str(raw or "")
        if (
            (name != GENERATED_ROOT and not name.startswith(GENERATED_ROOT + "/"))
            or "\\" in name
            or name != PurePosixPath(name).as_posix()
            or any(part in {"", ".", ".."} for part in name.split("/"))
        ):
            raise ValueError("The canonical asset-copy folder plan escaped the generated root.")
        directories.append(name)
    if arguments.get("expectedGeneratedRootExists") is False:
        directories.append(GENERATED_ROOT)
    elif arguments.get("expectedGeneratedRootExists") is not True:
        raise ValueError("The canonical generated-root identity is missing.")
    if len({name.casefold() for name in directories}) != len(directories):
        directories = list(dict.fromkeys(directories))
    return [destination], directories
