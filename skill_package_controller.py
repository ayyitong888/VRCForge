from __future__ import annotations

from typing import Any


class SkillPackageController:
    __slots__ = ("_host",)

    def __init__(self, host: Any) -> None:
        self._host = host

    def __getattr__(self, name: str) -> Any:
        return getattr(self._host, name)

    def _impl_import_skill_package_sync(self, params: dict[str, Any]) -> dict[str, Any]:
        service = self._host.skill_package_service()
        if bool(params.get("dryRun") or params.get("dry_run") or False):
            preview = service.preflight_import(
                str(params.get("packagePath") or params.get("package_path") or ""),
                allow_downgrade=bool(params.get("allowDowngrade") or params.get("allow_downgrade") or False),
                dev_mode=bool(params.get("devMode") or params.get("dev_mode") or False),
            )
            return {"ok": True, "dryRun": True, "preview": preview.as_dict()}
        projection = None
        with self._host.SKILL_PACKAGE_WRITE_LOCK:
            with service.install_transaction(
                str(params.get("packagePath") or params.get("package_path") or ""),
                source=str(params.get("source") or "local-import"),
                allow_downgrade=bool(params.get("allowDowngrade") or params.get("allow_downgrade") or False),
                dev_mode=bool(params.get("devMode") or params.get("dev_mode") or False),
            ) as result:
                if params.get("projectToUserSkills", params.get("project_to_user_skills", True)) is not False:
                    projection = self._host._project_installed_skill(
                        result.installed_path,
                        result.preview.manifest,
                        enabled=bool(result.registry_entry.get("enabled", True)),
                    )
        return {"ok": True, "imported": result.as_dict(), "projectedSkill": projection}

    def _impl_set_skill_package_enabled_sync(self, params: dict[str, Any]) -> dict[str, Any]:
        skill_id = str(params.get("skillPackageId") or params.get("skill_package_id") or params.get("id") or "").strip()
        if not skill_id:
            raise self._host.AgentGatewayError("skillPackageId is required.", status_code=400)
        enabled = bool(params.get("enabled"))
        service = self._host.skill_package_service()
        with self._host.SKILL_PACKAGE_WRITE_LOCK:
            with service.state_transaction([skill_id]):
                result = service.set_enabled(skill_id, enabled)
                projected = None
                if params.get("syncProjectedSkill", params.get("sync_projected_skill", True)) is not False:
                    projected = self._host._set_projected_skills_enabled([result.manifest], enabled)[0]
        return {"ok": True, "state": result.as_dict(), "projectedSkill": projected}

    def _impl_uninstall_skill_package_sync(self, params: dict[str, Any]) -> dict[str, Any]:
        skill_id = str(params.get("skillPackageId") or params.get("skill_package_id") or params.get("id") or "").strip()
        if not skill_id:
            raise self._host.AgentGatewayError("skillPackageId is required.", status_code=400)
        service = self._host.skill_package_service()
        with self._host.SKILL_PACKAGE_WRITE_LOCK:
            projected = None
            with service.uninstall_transaction(skill_id) as result:
                if params.get("removeProjectedSkill", params.get("remove_projected_skill", True)) is not False:
                    with self._host._delete_projected_skill_transaction(result.manifest) as projected_result:
                        projected = projected_result
        return {"ok": True, "uninstalled": result.as_dict(), "projectedSkill": projected}
