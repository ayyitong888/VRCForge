from __future__ import annotations

from typing import Any


class PathToSkillDashboardController:
    """Own Path-to-Skill capture orchestration while Dashboard keeps the HTTP contract."""

    __slots__ = ("_host",)

    def __init__(self, host: Any) -> None:
        self._host = host

    def __getattr__(self, name: str) -> Any:
        return getattr(self._host, name)

    def _impl_path_to_skill_kwargs(self, params: dict[str, Any]) -> dict[str, Any]:
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

    def _impl_path_to_skill_file_list(self, source_files: dict[str, str]) -> list[dict[str, Any]]:
        return [
            {"path": relative, "bytes": len(content.encode("utf-8"))}
            for relative, content in sorted(source_files.items())
        ]

    def _impl_path_to_skill_vsk_filename(self, manifest: dict[str, Any]) -> str:
        raw = str(manifest.get("skill_name") or manifest.get("id") or "captured-skill")
        name = self._host.re.sub(r"[^A-Za-z0-9_.-]+", "-", raw).strip("-._")
        return f"{name or 'captured-skill'}.vsk"

    def _impl_capture_path_to_skill_sync(
        self,
        params: dict[str, Any],
        *,
        allow_write: bool = False,
    ) -> dict[str, Any]:
        summary = params.get("summary")
        if not isinstance(summary, dict) or not summary:
            raise self._host.AgentGatewayError("summary is required and must be a JSON object.", status_code=400)
        export_vsk = bool(params.get("exportVsk") or params.get("export_vsk") or False)
        if allow_write and export_vsk and not bool(params.get("confirmExport") or params.get("confirm_export") or False):
            raise self._host.AgentGatewayError("confirmExport=true is required before exporting a .vsk package.", status_code=400)
        package_text = str(params.get("packageOutputPath") or params.get("package_output_path") or "").strip()
        if allow_write and export_vsk and package_text:
            requested_package_output = self._host.Path(package_text).expanduser()
            if requested_package_output.suffix.lower() != ".vsk":
                requested_package_output = requested_package_output.with_name(requested_package_output.name + ".vsk")
            if self._host.os.path.lexists(requested_package_output):
                raise self._host.AgentGatewayError(
                    "packageOutputPath already exists; Path-to-Skill export requires a new package path.",
                    status_code=400,
                )

        captured = self._host.build_path_to_skill_source(
            summary,
            **self._host._path_to_skill_kwargs(params),
        )
        result: dict[str, Any] = {
            "ok": True,
            "schema": "vrcforge.path_to_skill.capture_result.v1",
            "dryRun": not allow_write,
            "manifest": captured.manifest,
            "workflow": captured.workflow,
            "skillMarkdown": captured.skill_markdown,
            "sourceFiles": dict(captured.source_files),
            "files": self._host._path_to_skill_file_list(captured.source_files),
        }
        if not allow_write:
            if any(
                bool(params.get(key))
                for key in ("writeSource", "write_source", "outputPath", "output_path", "exportVsk", "export_vsk")
            ):
                result["writeSuppressed"] = True
            return result

        write_requested = bool(
            params.get("writeSource")
            or params.get("write_source")
            or params.get("outputPath")
            or params.get("output_path")
            or export_vsk
        )
        source_dir: Any = None
        if write_requested:
            output_text = str(params.get("outputPath") or params.get("output_path") or "").strip()
            if output_text:
                source_dir = self._host.Path(output_text).expanduser()
            elif bool(params.get("useTempOutput", params.get("use_temp_output", True))):
                temp_root = self._host.Path(self._host.tempfile.mkdtemp(prefix="vrcforge-path-to-skill-"))
                source_dir = temp_root / "source"
            else:
                raise self._host.AgentGatewayError("outputPath is required when useTempOutput=false.", status_code=400)
            captured.write_to(source_dir)
            result["dryRun"] = False
            result["writtenSource"] = {
                "path": str(source_dir),
                "files": self._host._path_to_skill_file_list(captured.source_files),
            }

        if export_vsk:
            if source_dir is None:
                temp_root = self._host.Path(self._host.tempfile.mkdtemp(prefix="vrcforge-path-to-skill-"))
                source_dir = temp_root / "source"
                captured.write_to(source_dir)
                result["dryRun"] = False
                result["writtenSource"] = {
                    "path": str(source_dir),
                    "files": self._host._path_to_skill_file_list(captured.source_files),
                }
            package_output = (
                self._host.Path(package_text).expanduser()
                if package_text
                else source_dir.parent / self._host._path_to_skill_vsk_filename(captured.manifest)
            )
            if package_output.suffix.lower() != ".vsk":
                package_output = package_output.with_name(package_output.name + ".vsk")
            if self._host.os.path.lexists(package_output):
                raise self._host.AgentGatewayError(
                    "packageOutputPath already exists; Path-to-Skill export requires a new package path.",
                    status_code=400,
                )
            package_output.parent.mkdir(parents=True, exist_ok=True)
            exported = self._host.skill_package_service().export_dev(source_dir, package_output, overwrite=False)
            result["exported"] = exported.as_dict()
        return result
