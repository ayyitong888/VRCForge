from __future__ import annotations

import ast
from contextlib import AbstractContextManager
from pathlib import Path
from types import TracebackType
from typing import Any

import pytest

import dashboard_server
from skill_package_governance import (
    SkillPackageGovernancePorts,
    SkillPackageGovernanceService,
)


ROOT = Path(__file__).parents[1]
LEGACY_ROOTS = {
    "_disable_projected_skills_for_packages",
    "set_skill_package_safe_mode_sync",
    "trust_skill_package_signer_sync",
    "revoke_skill_package_signer_sync",
    "block_skill_package_sync",
}


class _TrackingLock(AbstractContextManager[object]):
    def __init__(self, calls: list[object]) -> None:
        self.calls = calls
        self.active = False

    def __enter__(self) -> object:
        assert not self.active
        self.active = True
        self.calls.append("lock:enter")
        return self

    def __exit__(
        self,
        exc_type: type[BaseException] | None,
        exc: BaseException | None,
        traceback: TracebackType | None,
    ) -> None:
        self.calls.append("lock:exit")
        self.active = False


class _Transaction(AbstractContextManager[object]):
    def __init__(self, calls: list[object], lock: _TrackingLock) -> None:
        self.calls = calls
        self.lock = lock
        self.active = False

    def __enter__(self) -> object:
        assert self.lock.active
        self.active = True
        self.calls.append("transaction:enter")
        return self

    def __exit__(
        self,
        exc_type: type[BaseException] | None,
        exc: BaseException | None,
        traceback: TracebackType | None,
    ) -> None:
        self.calls.append("transaction:exit")
        self.active = False


def test_governance_has_one_typed_owner_and_no_dashboard_facades() -> None:
    dashboard_source = (ROOT / "dashboard_server.py").read_text(encoding="utf-8")
    owner_source = (ROOT / "skill_package_governance.py").read_text(encoding="utf-8")
    dashboard_tree = ast.parse(dashboard_source)
    dashboard_bindings = {
        node.name
        for node in dashboard_tree.body
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef))
    }

    assert LEGACY_ROOTS.isdisjoint(dashboard_bindings)
    assert "_SKILL_PACKAGE_GOVERNANCE" not in dashboard_source
    assert "SKILL_PACKAGE_GOVERNANCE = SkillPackageGovernanceService(" in dashboard_source
    assert dashboard_source.count("SKILL_PACKAGE_GOVERNANCE.") == 4
    assert SkillPackageGovernanceService.__slots__ == ("_ports",)
    assert set(SkillPackageGovernancePorts.__dataclass_fields__) == {
        "make_service",
        "write_lock",
        "disable_projected_skills",
    }
    for forbidden in (
        "_host",
        "_impl_",
        "__getattr__",
        "sys.modules",
        "dashboard_server import",
        "SkillPackageController",
        "SkillPackageProjectionService",
        "capture_path_to_skill_sync",
    ):
        assert forbidden not in owner_source

    ports = dashboard_server.SKILL_PACKAGE_GOVERNANCE._ports  # noqa: SLF001 - composition identity gate.
    assert ports.make_service is dashboard_server.skill_package_service
    assert ports.write_lock is dashboard_server.SKILL_PACKAGE_WRITE_LOCK
    assert ports.disable_projected_skills.__defaults__ == (
        dashboard_server._set_projected_skills_enabled,
    )


def test_safe_mode_keeps_lock_transaction_manifest_and_projection_order() -> None:
    calls: list[object] = []
    lock = _TrackingLock(calls)
    transaction = _Transaction(calls, lock)

    class Service:
        def state_transaction(self) -> _Transaction:
            calls.append("transaction:create")
            return transaction

        def set_safe_mode(self, enabled: bool, *, reason: object) -> dict[str, Any]:
            assert lock.active and transaction.active
            calls.append(("safe-mode", enabled, reason))
            return {"disabledSkillIds": ["one", "unknown", "not-a-dict"]}

        def load_registry(self) -> dict[str, Any]:
            assert lock.active and transaction.active
            calls.append("registry")
            return {
                "skills": {
                    "one": {"version": "1.2.3"},
                    "not-a-dict": "ignored",
                }
            }

        def _read_current_manifest(self, skill_id: str, version: str) -> dict[str, Any]:
            calls.append(("manifest", skill_id, version))
            return {"id": skill_id, "version": version}

    service = Service()

    def disable(manifests: list[dict[str, Any]]) -> list[dict[str, Any]]:
        assert lock.active and transaction.active
        calls.append(("projection", manifests))
        return [{"name": "one", "enabled": False}]

    owner = SkillPackageGovernanceService(
        SkillPackageGovernancePorts(
            make_service=lambda: service,
            write_lock=lock,
            disable_projected_skills=disable,
        )
    )

    result = owner.set_safe_mode({"enabled": 1, "reason": "operator"})

    assert result == {
        "ok": True,
        "safeMode": {"disabledSkillIds": ["one", "unknown", "not-a-dict"]},
        "projectedSkills": [{"name": "one", "enabled": False}],
    }
    assert calls == [
        "lock:enter",
        "transaction:create",
        "transaction:enter",
        ("safe-mode", True, "operator"),
        "registry",
        ("manifest", "one", "1.2.3"),
        ("projection", [{"id": "one", "version": "1.2.3"}]),
        "transaction:exit",
        "lock:exit",
    ]


def test_trust_signer_uses_alias_and_lock_without_state_transaction() -> None:
    calls: list[object] = []
    lock = _TrackingLock(calls)

    class Service:
        def trust_signer(self, fingerprint: str, *, reason: object) -> dict[str, Any]:
            assert lock.active
            calls.append(("trust", fingerprint, reason))
            return {"fingerprint": fingerprint, "reason": reason}

    owner = SkillPackageGovernanceService(
        SkillPackageGovernancePorts(
            make_service=lambda: Service(),
            write_lock=lock,
            disable_projected_skills=lambda _manifests: pytest.fail(
                "trust must not update projections"
            ),
        )
    )

    result = owner.trust_signer(
        {"signer_fingerprint": "a" * 64, "reason": "verified"}
    )

    assert result == {
        "ok": True,
        "signer": {"fingerprint": "a" * 64, "reason": "verified"},
    }
    assert calls == [
        "lock:enter",
        ("trust", "a" * 64, "verified"),
        "lock:exit",
    ]


@pytest.mark.parametrize("operation", ["revoke", "block"])
def test_revoke_and_block_keep_transactional_projection_shape(operation: str) -> None:
    calls: list[object] = []
    lock = _TrackingLock(calls)
    transaction = _Transaction(calls, lock)

    class Service:
        def state_transaction(self) -> _Transaction:
            calls.append("transaction:create")
            return transaction

        def revoke_signer(self, fingerprint: str, *, reason: object) -> dict[str, Any]:
            calls.append(("revoke", fingerprint, reason))
            return {"disabledSkillIds": ["skill"]}

        def block_package(self, **kwargs: object) -> dict[str, Any]:
            calls.append(("block", kwargs))
            return {"disabledSkillIds": ["skill"]}

        def load_registry(self) -> dict[str, Any]:
            return {"skills": {"skill": {"version": "4.0.0"}}}

        def _read_current_manifest(self, skill_id: str, version: str) -> dict[str, Any]:
            return {"id": skill_id, "version": version}

    service = Service()
    owner = SkillPackageGovernanceService(
        SkillPackageGovernancePorts(
            make_service=lambda: service,
            write_lock=lock,
            disable_projected_skills=lambda manifests: calls.append(
                ("projection", manifests)
            )
            or [{"name": "skill", "enabled": False}],
        )
    )

    if operation == "revoke":
        result = owner.revoke_signer(
            {"signer_fingerprint": "b" * 64, "reason": "compromised"}
        )
        assert "signer" in result and "blocklist" not in result
        assert ("revoke", "b" * 64, "compromised") in calls
    else:
        result = owner.block_package(
            {
                "package_id": "community.test",
                "package_sha256": "c" * 64,
                "lock_sha256": "d" * 64,
                "reason": "blocked",
            }
        )
        assert "blocklist" in result and "signer" not in result
        assert (
            "block",
            {
                "package_id": "community.test",
                "package_sha256": "c" * 64,
                "lock_sha256": "d" * 64,
                "reason": "blocked",
            },
        ) in calls

    assert result["ok"] is True
    assert result["projectedSkills"] == [{"name": "skill", "enabled": False}]
    assert calls[0:3] == ["lock:enter", "transaction:create", "transaction:enter"]
    assert calls[-3:] == [
        ("projection", [{"id": "skill", "version": "4.0.0"}]),
        "transaction:exit",
        "lock:exit",
    ]
