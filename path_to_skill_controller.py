from __future__ import annotations

import re
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable

from path_to_skill import CapturedSkillSource, build_path_to_skill_source


_PATH_TO_SKILL_IDENTITY_PROPERTIES: dict[str, dict[str, Any]] = {
    "summary": {
        "type": "object",
        "description": "Structured completed-work evidence to sanitize into one portable Skill source.",
    },
    "packageId": {"type": "string", "description": "Optional stable package id."},
    "skillName": {"type": "string", "description": "Optional filesystem-safe Skill name."},
    "title": {"type": "string", "description": "Optional user-facing Skill title."},
    "version": {"type": "string", "default": "1.0.0"},
    "author": {"type": "string", "default": "VRCForge User"},
    "minVrcforgeVersion": {"type": "string"},
}

PATH_TO_SKILL_PREVIEW_INPUT_SCHEMA: dict[str, Any] = {
    "type": "object",
    "additionalProperties": False,
    "required": ["summary"],
    "properties": dict(_PATH_TO_SKILL_IDENTITY_PROPERTIES),
}

PATH_TO_SKILL_WRITE_INPUT_SCHEMA: dict[str, Any] = {
    "type": "object",
    "additionalProperties": False,
    "required": ["summary"],
    "properties": {
        **_PATH_TO_SKILL_IDENTITY_PROPERTIES,
        "outputPath": {
            "type": "string",
            "minLength": 1,
            "description": "Exact new source directory; existing targets are rejected.",
        },
        "writeSource": {"type": "boolean", "default": False},
        "useTempOutput": {"type": "boolean", "default": True},
        "exportVsk": {
            "type": "boolean",
            "default": False,
            "description": "Also export the captured source as a development .vsk package.",
        },
        "confirmExport": {
            "type": "boolean",
            "default": False,
            "description": "Must be true when exportVsk is true; VRCForge approval is still required before execution.",
        },
        "packageOutputPath": {
            "type": "string",
            "minLength": 1,
            "description": "Optional exact new .vsk destination; existing files are rejected.",
        },
    },
    "anyOf": [
        {
            "required": ["writeSource"],
            "properties": {"writeSource": {"const": True}},
        },
        {"required": ["outputPath"]},
        {
            "required": ["exportVsk", "confirmExport"],
            "properties": {
                "exportVsk": {"const": True},
                "confirmExport": {"const": True},
            },
        },
    ],
}


class PathToSkillControllerError(ValueError):
    def __init__(self, message: str, *, status_code: int = 400) -> None:
        super().__init__(message)
        self.status_code = status_code


def path_to_skill_kwargs(params: dict[str, Any]) -> dict[str, Any]:
    kwargs: dict[str, Any] = {}
    aliases = {
        "package_id": ("packageId", "package_id"),
        "skill_name": ("skillName", "skill_name"),
        "title": ("title",),
        "version": ("version",),
        "author": ("author",),
        "min_vrcforge_version": ("minVrcforgeVersion", "min_vrcforge_version"),
    }
    for target, keys in aliases.items():
        for key in keys:
            value = params.get(key)
            if value is not None and str(value).strip():
                kwargs[target] = str(value).strip()
                break
    return kwargs


def path_to_skill_file_list(source_files: dict[str, str]) -> list[dict[str, Any]]:
    return [
        {"path": relative, "bytes": len(content.encode("utf-8"))}
        for relative, content in sorted(source_files.items())
    ]


def path_to_skill_vsk_filename(manifest: dict[str, Any]) -> str:
    raw = str(manifest.get("skill_name") or manifest.get("id") or "captured-skill")
    name = re.sub(r"[^A-Za-z0-9_.-]+", "-", raw).strip("-._")
    return f"{name or 'captured-skill'}.vsk"


def _require_captured_source(params: dict[str, Any]) -> CapturedSkillSource:
    summary = params.get("summary")
    if not isinstance(summary, dict) or not summary:
        raise PathToSkillControllerError(
            "summary is required and must be a JSON object.",
            status_code=400,
        )
    return build_path_to_skill_source(summary, **path_to_skill_kwargs(params))


def _capture_result(captured: CapturedSkillSource, *, dry_run: bool) -> dict[str, Any]:
    return {
        "ok": True,
        "schema": "vrcforge.path_to_skill.capture_result.v1",
        "dryRun": dry_run,
        "manifest": captured.manifest,
        "workflow": captured.workflow,
        "skillMarkdown": captured.skill_markdown,
        "sourceFiles": dict(captured.source_files),
        "files": path_to_skill_file_list(captured.source_files),
    }


class PathToSkillPreviewService:
    """Pure capture preview; owns no filesystem or package-export capability."""

    __slots__ = ()

    def preview(self, params: dict[str, Any]) -> dict[str, Any]:
        captured = _require_captured_source(params)
        result = _capture_result(captured, dry_run=True)
        if any(
            bool(params.get(key))
            for key in (
                "writeSource",
                "write_source",
                "outputPath",
                "output_path",
                "exportVsk",
                "export_vsk",
            )
        ):
            result["writeSuppressed"] = True
        return result


@dataclass(frozen=True, slots=True)
class PathToSkillWritePorts:
    path_lexists: Callable[[Path], bool]
    make_temp_root: Callable[[], Path]
    write_source: Callable[[CapturedSkillSource, Path], None]
    ensure_parent: Callable[[Path], None]
    export_dev: Callable[[Path, Path], dict[str, Any]]


class PathToSkillWriteService:
    """User-directed source/package writer with fixed, app-lifetime ports.

    It retains no file handles or child processes. OS-temp outputs are
    intentionally retained because their returned paths are user-visible.
    """

    __slots__ = ("_ports",)

    def __init__(self, ports: PathToSkillWritePorts) -> None:
        self._ports = ports

    def write(self, params: dict[str, Any]) -> dict[str, Any]:
        summary = params.get("summary")
        if not isinstance(summary, dict) or not summary:
            raise PathToSkillControllerError(
                "summary is required and must be a JSON object.",
                status_code=400,
            )
        export_vsk = bool(params.get("exportVsk") or params.get("export_vsk") or False)
        if export_vsk and not bool(
            params.get("confirmExport") or params.get("confirm_export") or False
        ):
            raise PathToSkillControllerError(
                "confirmExport=true is required before exporting a .vsk package.",
                status_code=400,
            )
        package_text = str(
            params.get("packageOutputPath")
            or params.get("package_output_path")
            or ""
        ).strip()
        if export_vsk and package_text:
            requested_package_output = Path(package_text).expanduser()
            if requested_package_output.suffix.lower() != ".vsk":
                requested_package_output = requested_package_output.with_name(
                    requested_package_output.name + ".vsk"
                )
            if self._ports.path_lexists(requested_package_output):
                raise PathToSkillControllerError(
                    "packageOutputPath already exists; Path-to-Skill export requires a new package path.",
                    status_code=400,
                )

        captured = build_path_to_skill_source(summary, **path_to_skill_kwargs(params))
        result = _capture_result(captured, dry_run=False)
        write_requested = bool(
            params.get("writeSource")
            or params.get("write_source")
            or params.get("outputPath")
            or params.get("output_path")
            or export_vsk
        )
        source_dir: Path | None = None
        if write_requested:
            output_text = str(
                params.get("outputPath") or params.get("output_path") or ""
            ).strip()
            if output_text:
                source_dir = Path(output_text).expanduser()
            elif bool(params.get("useTempOutput", params.get("use_temp_output", True))):
                source_dir = self._ports.make_temp_root() / "source"
            else:
                raise PathToSkillControllerError(
                    "outputPath is required when useTempOutput=false.",
                    status_code=400,
                )
            self._ports.write_source(captured, source_dir)
            result["dryRun"] = False
            result["writtenSource"] = {
                "path": str(source_dir),
                "files": path_to_skill_file_list(captured.source_files),
            }

        if export_vsk:
            if source_dir is None:
                source_dir = self._ports.make_temp_root() / "source"
                self._ports.write_source(captured, source_dir)
                result["dryRun"] = False
                result["writtenSource"] = {
                    "path": str(source_dir),
                    "files": path_to_skill_file_list(captured.source_files),
                }
            package_output = (
                Path(package_text).expanduser()
                if package_text
                else source_dir.parent / path_to_skill_vsk_filename(captured.manifest)
            )
            if package_output.suffix.lower() != ".vsk":
                package_output = package_output.with_name(package_output.name + ".vsk")
            if self._ports.path_lexists(package_output):
                raise PathToSkillControllerError(
                    "packageOutputPath already exists; Path-to-Skill export requires a new package path.",
                    status_code=400,
                )
            self._ports.ensure_parent(package_output.parent)
            result["exported"] = self._ports.export_dev(source_dir, package_output)
        return result
