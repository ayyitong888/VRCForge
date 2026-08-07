from __future__ import annotations

from typing import Any

from skill_packages import SkillPackageService


class SkillPackageGovernanceService:
    """Keep package-governance transactions behind Dashboard late-bound facades."""

    __slots__ = ("_host",)

    def __init__(self, host: Any) -> None:
        self._host = host

    def __getattr__(self, name: str) -> Any:
        return getattr(self._host, name)

    def _impl_disable_projected_skills_for_packages(
        self,
        service: SkillPackageService,
        skill_ids: list[str],
    ) -> list[dict[str, Any]]:
        registry = service.load_registry()
        manifests: list[dict[str, Any]] = []
        for skill_id in skill_ids:
            entry = registry.get("skills", {}).get(skill_id)
            if not isinstance(entry, dict):
                continue
            manifest = service._read_current_manifest(
                skill_id,
                str(entry.get("version") or ""),
            )
            if manifest:
                manifests.append(manifest)
        return self._host._set_projected_skills_enabled(manifests, False)

    def _impl_set_skill_package_safe_mode_sync(self, params: dict[str, Any]) -> dict[str, Any]:
        service = self._host.skill_package_service()
        with self._host.SKILL_PACKAGE_WRITE_LOCK:
            with service.state_transaction():
                result = service.set_safe_mode(bool(params.get("enabled")), reason=params.get("reason"))
                projected = self._host._disable_projected_skills_for_packages(
                    service,
                    list(result.get("disabledSkillIds") or []),
                )
        return {"ok": True, "safeMode": result, "projectedSkills": projected}

    def _impl_trust_skill_package_signer_sync(self, params: dict[str, Any]) -> dict[str, Any]:
        service = self._host.skill_package_service()
        with self._host.SKILL_PACKAGE_WRITE_LOCK:
            result = service.trust_signer(
                str(
                    params.get("signerFingerprint")
                    or params.get("signer_fingerprint")
                    or ""
                ),
                reason=params.get("reason"),
            )
        return {"ok": True, "signer": result}

    def _impl_revoke_skill_package_signer_sync(self, params: dict[str, Any]) -> dict[str, Any]:
        service = self._host.skill_package_service()
        with self._host.SKILL_PACKAGE_WRITE_LOCK:
            with service.state_transaction():
                result = service.revoke_signer(
                    str(
                        params.get("signerFingerprint")
                        or params.get("signer_fingerprint")
                        or ""
                    ),
                    reason=params.get("reason"),
                )
                projected = self._host._disable_projected_skills_for_packages(
                    service,
                    list(result.get("disabledSkillIds") or []),
                )
        return {"ok": True, "signer": result, "projectedSkills": projected}

    def _impl_block_skill_package_sync(self, params: dict[str, Any]) -> dict[str, Any]:
        service = self._host.skill_package_service()
        with self._host.SKILL_PACKAGE_WRITE_LOCK:
            with service.state_transaction():
                result = service.block_package(
                    package_id=params.get("packageId") or params.get("package_id"),
                    package_sha256=params.get("packageSha256")
                    or params.get("package_sha256"),
                    lock_sha256=params.get("lockSha256")
                    or params.get("lock_sha256"),
                    reason=params.get("reason"),
                )
                projected = self._host._disable_projected_skills_for_packages(
                    service,
                    list(result.get("disabledSkillIds") or []),
                )
        return {"ok": True, "blocklist": result, "projectedSkills": projected}
