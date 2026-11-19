"""Local prefab material evidence used by the package planner."""

import re
import tarfile
import zipfile
from contextlib import ExitStack
from pathlib import Path

import outfit_package_inspector as package_inspector
from outfit_package_inspector import ASSET_META_GUID, _BoundedArchiveReader, unitypackage_member_parts


_MATERIAL_REFERENCE = re.compile(rb"\{fileID: 2100000,\s*guid: ([0-9a-f]{32}),\s*type: [23]\}", re.IGNORECASE)
_BUILTIN_GUID = re.compile(r"0{16}[0def]0{15}")
_PREFAB_BYTE_LIMIT = 8 * 1024 * 1024
_PREFAB_TOTAL_BYTE_LIMIT = 32 * 1024 * 1024


def prefab_material_references(item: dict, source: Path, groups: set[str]) -> tuple[set[str], int]:
    """Read bounded Unity YAML and return only external material GUIDs and coverage."""
    if not groups:
        return set(), 0
    references: set[str] = set()
    scanned = 0
    remaining_bytes = _PREFAB_TOTAL_BYTE_LIMIT
    pending = groups.copy()
    try:
        with ExitStack() as stack:
            if item.get("sourceType") == "zip":
                container = stack.enter_context(zipfile.ZipFile(item.get("containerPath") or source))
                entries = [entry for entry in container.infolist()
                           if package_inspector.normalize_archive_name(entry.filename) == item["path"]]
                if len(entries) != 1:
                    return references, scanned
                handle = stack.enter_context(container.open(entries[0]))
            else:
                handle = stack.enter_context(Path(item.get("actualPackagePath") or source).open("rb"))
            bounded = _BoundedArchiveReader(handle, package_inspector.MAX_UNITYPACKAGE_SCAN_INPUT_BYTES)
            archive = stack.enter_context(tarfile.open(fileobj=bounded, mode="r|*"))
            for index, member in enumerate(archive):
                if index >= package_inspector.MAX_UNITYPACKAGE_MEMBERS or not pending:
                    break
                parts = unitypackage_member_parts(member.name)
                if not member.isfile() or not parts or parts[1] != "asset" or parts[0] not in pending:
                    continue
                pending.remove(parts[0])
                if member.size > min(_PREFAB_BYTE_LIMIT, remaining_bytes):
                    continue
                payload = archive.extractfile(member).read()
                remaining_bytes -= len(payload)
                if not payload.startswith(b"%YAML "):
                    continue
                scanned += 1
                references.update(guid.decode().casefold() for guid in _MATERIAL_REFERENCE.findall(payload))
    except (OSError, EOFError, tarfile.TarError, zipfile.BadZipFile):
        pass
    return {guid for guid in references if not _BUILTIN_GUID.fullmatch(guid)}, scanned


def installed_material_guids(project_root: Path | None, wanted: set[str]) -> dict[str, str]:
    if project_root is None or not wanted:
        return {}
    confirmed: dict[str, str] = {}
    for meta in (project_root / "Assets").rglob("*.mat.meta"):
        asset = meta.with_suffix("")
        if meta.is_symlink() or asset.is_symlink() or not asset.is_file():
            continue
        try:
            with meta.open("rb") as handle:
                guids = ASSET_META_GUID.findall(handle.read(65536))
        except OSError:
            continue
        if len(guids) == 1 and (guid := guids[0].decode().casefold()) in wanted:
            confirmed[guid] = asset.relative_to(project_root).as_posix()
        if len(confirmed) == len(wanted):
            break
    return confirmed
