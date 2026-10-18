"""Checkpoint scopes derived from server/Core-owned plans and sealed imports."""
from pathlib import Path, PurePosixPath

from checkpoint_asset_footprint import validate_asset_footprint


def prepare_planned_asset_checkpoint(project_root: Path, asset_paths: list[str], *, prepare,
                                     directory_paths: list[str] | None = None) -> dict:
    """Snapshot exact existing and create-new assets, including Unity metadata."""
    def reject(message):
        return {"ok": False, "blocking": True, "code": "checkpoint_scope_unresolved",
                "error": message, "mutationStarted": False, "saved": False}

    try:
        if not isinstance(asset_paths, list) or not asset_paths or len(asset_paths) > 2048:
            raise ValueError("The approved plan did not provide a bounded asset footprint.")
        files = set()
        directories = set()
        for name in [*asset_paths, *(directory_paths or [])]:
            if not isinstance(name, str) or not name.startswith("Assets/"):
                raise ValueError("The approved plan contains a non-Assets path.")
            is_directory = name in (directory_paths or [])
            if is_directory:
                absolute = project_root / name
                if not absolute.exists():
                    directories.add(name)
                    files.add(name + ".meta")
                elif not absolute.is_dir() or not (project_root / (name + ".meta")).is_file():
                    raise ValueError("An existing planned asset folder lacks valid Unity metadata.")
            elif not name.endswith(".meta"):
                files.add(name + ".meta")
                files.add(name)
            parent = PurePosixPath(name).parent
            while parent.as_posix() != "Assets":
                absolute = project_root / parent.as_posix()
                if not absolute.exists():
                    directories.add(parent.as_posix())
                    files.add(parent.as_posix() + ".meta")
                elif not absolute.is_dir() or not (project_root / (parent.as_posix() + ".meta")).is_file():
                    raise ValueError("An existing planned asset folder lacks valid Unity metadata.")
                parent = parent.parent
        existing = sorted(name for name in files if (project_root / name).is_file())
        absent = sorted(files - set(existing))
        marker = {"schema": "vrcforge.asset_checkpoint_footprint.v1"}
        footprint = {**marker, "archiveFiles": existing, "archiveAbsentFiles": absent,
                     "archiveAbsentDirectories": sorted(directories)}
        validate_asset_footprint(project_root, {"assetFootprint": footprint, **footprint}, preparing=True)
        result = prepare(project_root, checkpoint_files=sorted(files))
        if result.get("ok") is not True:
            return result
        return {**result, "assetFootprint": footprint,
                "archiveScopeReason": "revalidated_authoritative_plan_asset_footprint"}
    except (TypeError, ValueError, RuntimeError, OSError) as exc:
        return reject(str(exc))

