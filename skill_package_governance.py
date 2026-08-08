from __future__ import annotations

from contextlib import AbstractContextManager
from dataclasses import dataclass
from types import TracebackType
from typing import Any, Callable, Protocol


class SkillPackageWriteLockPort(Protocol):
    """Borrow the single app-owned package write lock for one transaction."""

    def __enter__(self) -> object: ...

    def __exit__(
        self,
        exc_type: type[BaseException] | None,
        exc: BaseException | None,
        traceback: TracebackType | None,
    ) -> bool | None: ...


class SkillPackageGovernanceStorePort(Protocol):
    """Expose only the package-store mutations needed by Governance."""

    def state_transaction(self) -> AbstractContextManager[object]: ...

    def load_registry(self) -> dict[str, Any]: ...

    def _read_current_manifest(  # noqa: SLF001 - installed-manifest lookup is part of the narrow governance store contract.
        self,
        skill_id: str,
        version: str,
    ) -> dict[str, Any] | None: ...

    def set_safe_mode(
        self,
        enabled: bool,
        *,
        reason: Any = None,
    ) -> dict[str, Any]: ...

    def trust_signer(
        self,
        fingerprint: str,
        *,
        reason: Any = None,
    ) -> dict[str, Any]: ...

    def revoke_signer(
        self,
        fingerprint: str,
        *,
        reason: Any = None,
    ) -> dict[str, Any]: ...

    def block_package(
        self,
        *,
        package_id: Any = None,
        package_sha256: Any = None,
        lock_sha256: Any = None,
        reason: Any = None,
    ) -> dict[str, Any]: ...


@dataclass(frozen=True, slots=True)
class SkillPackageGovernancePorts:
    make_service: Callable[[], SkillPackageGovernanceStorePort]
    write_lock: SkillPackageWriteLockPort
    disable_projected_skills: Callable[
        [list[dict[str, Any]]],
        list[dict[str, Any]],
    ]


class SkillPackageGovernanceService:
    """Own package governance without owning projection or lock lifecycle.

    The service creates no process, file handle, or external communication
    channel. Durable writes remain inside ``SkillPackageService`` transactions,
    serialized by the one app-owned non-reentrant package write lock.
    """

    __slots__ = ("_ports",)

    def __init__(self, ports: SkillPackageGovernancePorts) -> None:
        self._ports = ports

    def _disable_projected_skills_for_packages(
        self,
        service: SkillPackageGovernanceStorePort,
        skill_ids: list[str],
    ) -> list[dict[str, Any]]:
        registry = service.load_registry()
        manifests: list[dict[str, Any]] = []
        for skill_id in skill_ids:
            entry = registry.get("skills", {}).get(skill_id)
            if not isinstance(entry, dict):
                continue
            manifest = service._read_current_manifest(  # noqa: SLF001 - governance reads the current installed package transaction.
                skill_id,
                str(entry.get("version") or ""),
            )
            if manifest:
                manifests.append(manifest)
        return self._ports.disable_projected_skills(manifests)

    def set_safe_mode(self, params: dict[str, Any]) -> dict[str, Any]:
        service = self._ports.make_service()
        with self._ports.write_lock:
            with service.state_transaction():
                result = service.set_safe_mode(
                    bool(params.get("enabled")),
                    reason=params.get("reason"),
                )
                projected = self._disable_projected_skills_for_packages(
                    service,
                    list(result.get("disabledSkillIds") or []),
                )
        return {"ok": True, "safeMode": result, "projectedSkills": projected}

    def trust_signer(self, params: dict[str, Any]) -> dict[str, Any]:
        service = self._ports.make_service()
        with self._ports.write_lock:
            result = service.trust_signer(
                str(
                    params.get("signerFingerprint")
                    or params.get("signer_fingerprint")
                    or ""
                ),
                reason=params.get("reason"),
            )
        return {"ok": True, "signer": result}

    def revoke_signer(self, params: dict[str, Any]) -> dict[str, Any]:
        service = self._ports.make_service()
        with self._ports.write_lock:
            with service.state_transaction():
                result = service.revoke_signer(
                    str(
                        params.get("signerFingerprint")
                        or params.get("signer_fingerprint")
                        or ""
                    ),
                    reason=params.get("reason"),
                )
                projected = self._disable_projected_skills_for_packages(
                    service,
                    list(result.get("disabledSkillIds") or []),
                )
        return {"ok": True, "signer": result, "projectedSkills": projected}

    def block_package(self, params: dict[str, Any]) -> dict[str, Any]:
        service = self._ports.make_service()
        with self._ports.write_lock:
            with service.state_transaction():
                result = service.block_package(
                    package_id=params.get("packageId") or params.get("package_id"),
                    package_sha256=params.get("packageSha256")
                    or params.get("package_sha256"),
                    lock_sha256=params.get("lockSha256")
                    or params.get("lock_sha256"),
                    reason=params.get("reason"),
                )
                projected = self._disable_projected_skills_for_packages(
                    service,
                    list(result.get("disabledSkillIds") or []),
                )
        return {"ok": True, "blocklist": result, "projectedSkills": projected}
