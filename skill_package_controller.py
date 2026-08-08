from __future__ import annotations

from contextlib import AbstractContextManager
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable, Protocol

from skill_packages import ImportPreview, InstallResult, PackageStateResult, UninstallResult


class SkillPackageControllerStorePort(Protocol):
    """Expose only package operations used by import/toggle/uninstall."""

    def preflight_import(
        self,
        package_path: str,
        *,
        allow_downgrade: bool = False,
        dev_mode: bool = False,
    ) -> ImportPreview: ...

    def install_transaction(
        self,
        package_path: str,
        *,
        source: str,
        allow_downgrade: bool = False,
        dev_mode: bool = False,
    ) -> AbstractContextManager[InstallResult]: ...

    def state_transaction(
        self,
        skill_ids: list[str],
    ) -> AbstractContextManager[None]: ...

    def set_enabled(
        self,
        skill_id: str,
        enabled: bool,
    ) -> PackageStateResult: ...

    def uninstall_transaction(
        self,
        skill_id: str,
    ) -> AbstractContextManager[UninstallResult]: ...


@dataclass(frozen=True, slots=True)
class SkillPackageControllerPorts:
    make_service: Callable[[], SkillPackageControllerStorePort]
    write_lock: AbstractContextManager[object]
    project_installed_skill: Callable[
        [Path, dict[str, Any], bool],
        dict[str, Any] | None,
    ]
    set_projected_skill_enabled: Callable[
        [dict[str, Any], bool],
        dict[str, Any],
    ]
    delete_projected_skill: Callable[
        [dict[str, Any]],
        AbstractContextManager[dict[str, Any]],
    ]
    make_bad_request: Callable[[str], Exception]


class SkillPackageController:
    """Own package import/toggle/uninstall orchestration behind fixed ports.

    The app owns the shared lock and projection/store lifecycles. This owner
    creates no process, persistent file handle, network channel, or credential.
    """

    __slots__ = ("_ports",)

    def __init__(self, ports: SkillPackageControllerPorts) -> None:
        self._ports = ports

    def import_package(self, params: dict[str, Any]) -> dict[str, Any]:
        service = self._ports.make_service()
        if bool(params.get("dryRun") or params.get("dry_run") or False):
            preview = service.preflight_import(
                str(params.get("packagePath") or params.get("package_path") or ""),
                allow_downgrade=bool(
                    params.get("allowDowngrade")
                    or params.get("allow_downgrade")
                    or False
                ),
                dev_mode=bool(
                    params.get("devMode") or params.get("dev_mode") or False
                ),
            )
            return {"ok": True, "dryRun": True, "preview": preview.as_dict()}
        projection = None
        with self._ports.write_lock:
            with service.install_transaction(
                str(params.get("packagePath") or params.get("package_path") or ""),
                source=str(params.get("source") or "local-import"),
                allow_downgrade=bool(
                    params.get("allowDowngrade")
                    or params.get("allow_downgrade")
                    or False
                ),
                dev_mode=bool(
                    params.get("devMode") or params.get("dev_mode") or False
                ),
            ) as result:
                if (
                    params.get(
                        "projectToUserSkills",
                        params.get("project_to_user_skills", True),
                    )
                    is not False
                ):
                    projection = self._ports.project_installed_skill(
                        result.installed_path,
                        result.preview.manifest,
                        bool(result.registry_entry.get("enabled", True)),
                    )
        return {
            "ok": True,
            "imported": result.as_dict(),
            "projectedSkill": projection,
        }

    def set_enabled(self, params: dict[str, Any]) -> dict[str, Any]:
        skill_id = str(
            params.get("skillPackageId")
            or params.get("skill_package_id")
            or params.get("id")
            or ""
        ).strip()
        if not skill_id:
            raise self._ports.make_bad_request("skillPackageId is required.")
        enabled = bool(params.get("enabled"))
        service = self._ports.make_service()
        with self._ports.write_lock:
            with service.state_transaction([skill_id]):
                result = service.set_enabled(skill_id, enabled)
                projected = None
                if (
                    params.get(
                        "syncProjectedSkill",
                        params.get("sync_projected_skill", True),
                    )
                    is not False
                ):
                    projected = self._ports.set_projected_skill_enabled(
                        result.manifest,
                        enabled,
                    )
        return {
            "ok": True,
            "state": result.as_dict(),
            "projectedSkill": projected,
        }

    def uninstall(self, params: dict[str, Any]) -> dict[str, Any]:
        skill_id = str(
            params.get("skillPackageId")
            or params.get("skill_package_id")
            or params.get("id")
            or ""
        ).strip()
        if not skill_id:
            raise self._ports.make_bad_request("skillPackageId is required.")
        service = self._ports.make_service()
        with self._ports.write_lock:
            projected = None
            with service.uninstall_transaction(skill_id) as result:
                if (
                    params.get(
                        "removeProjectedSkill",
                        params.get("remove_projected_skill", True),
                    )
                    is not False
                ):
                    with self._ports.delete_projected_skill(
                        result.manifest
                    ) as projected_result:
                        projected = projected_result
        return {
            "ok": True,
            "uninstalled": result.as_dict(),
            "projectedSkill": projected,
        }
