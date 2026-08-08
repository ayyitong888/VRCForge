from __future__ import annotations

import ast
from contextlib import AbstractContextManager
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest

import dashboard_server
from agent_gateway import AgentGatewayError, LOCAL_STATE_CHECKPOINT_TARGETS
from skill_package_controller import (
    SkillPackageController,
    SkillPackageControllerPorts,
    SkillPackageControllerStorePort,
)


ROOT = Path(__file__).parents[1]
LEGACY_ROOTS = {
    "import_skill_package_sync",
    "set_skill_package_enabled_sync",
    "uninstall_skill_package_sync",
}


class _TrackingContext(AbstractContextManager[object]):
    def __init__(
        self,
        calls: list[object],
        label: str,
        value: object | None = None,
    ) -> None:
        self.calls = calls
        self.label = label
        self.value = value if value is not None else self
        self.active = False

    def __enter__(self) -> object:
        self.active = True
        self.calls.append(f"{self.label}:enter")
        return self.value

    def __exit__(self, *_args: object) -> None:
        self.calls.append(f"{self.label}:exit")
        self.active = False


def _ports(service: Any, calls: list[object]) -> SkillPackageControllerPorts:
    lock = _TrackingContext(calls, "lock")
    return SkillPackageControllerPorts(
        make_service=lambda: calls.append("factory") or service,
        write_lock=lock,
        project_installed_skill=lambda installed, manifest, enabled: calls.append(
            ("project", installed, manifest, enabled)
        )
        or {"enabled": enabled},
        set_projected_skill_enabled=lambda manifest, enabled: calls.append(
            ("set-projection", manifest, enabled)
        )
        or {"enabled": enabled},
        delete_projected_skill=lambda manifest: calls.append(
            ("delete-projection", manifest)
        )
        or _TrackingContext(calls, "delete", {"deleted": True}),
        make_bad_request=lambda message: AgentGatewayError(message, status_code=400),
    )


def test_controller_has_typed_ports_direct_routes_and_registry_handlers() -> None:
    dashboard_source = (ROOT / "dashboard_server.py").read_text(encoding="utf-8")
    owner_source = (ROOT / "skill_package_controller.py").read_text(encoding="utf-8")
    dashboard_tree = ast.parse(dashboard_source)
    dashboard_bindings = {
        node.name
        for node in dashboard_tree.body
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef))
    }

    assert LEGACY_ROOTS.isdisjoint(dashboard_bindings)
    assert "_SKILL_PACKAGE_CONTROLLER" not in dashboard_source
    assert "SKILL_PACKAGE_CONTROLLER = SkillPackageController(" in dashboard_source
    assert dashboard_source.count("SKILL_PACKAGE_CONTROLLER.") == 6
    assert SkillPackageController.__slots__ == ("_ports",)
    assert set(SkillPackageControllerPorts.__dataclass_fields__) == {
        "make_service",
        "write_lock",
        "project_installed_skill",
        "set_projected_skill_enabled",
        "delete_projected_skill",
        "make_bad_request",
    }
    assert {
        name
        for name, value in SkillPackageControllerStorePort.__dict__.items()
        if callable(value) and not name.startswith("__")
    } == {
        "preflight_import",
        "install_transaction",
        "state_transaction",
        "set_enabled",
        "uninstall_transaction",
    }
    for forbidden in (
        "_host",
        "_impl_",
        "__getattr__",
        "sys.modules",
        "dashboard_server import",
        "SkillPackageGovernanceService",
        "SkillPackageProjectionService",
        "export_dev",
        "private_key",
    ):
        assert forbidden not in owner_source

    owner = dashboard_server.SKILL_PACKAGE_CONTROLLER
    ports = owner._ports  # noqa: SLF001 - composition identity gate.
    assert ports.make_service is dashboard_server.skill_package_service
    assert ports.write_lock is dashboard_server.SKILL_PACKAGE_WRITE_LOCK
    assert ports.project_installed_skill.__defaults__ == (
        dashboard_server.SKILL_PACKAGE_PROJECTION,
    )
    assert (
        ports.set_projected_skill_enabled.__defaults__
        == (dashboard_server.SKILL_PACKAGE_PROJECTION,)
    )
    assert ports.delete_projected_skill.__self__ is (
        dashboard_server.SKILL_PACKAGE_PROJECTION
    )
    assert ports.delete_projected_skill.__func__ is type(
        dashboard_server.SKILL_PACKAGE_PROJECTION
    ).delete_transaction
    assert ports.make_bad_request.__defaults__ == (AgentGatewayError,)

    for target, method_name in {
        "vrcforge_import_skill_package": "import_package",
        "vrcforge_set_skill_package_enabled": "set_enabled",
        "vrcforge_uninstall_skill_package": "uninstall",
    }.items():
        registration = dashboard_server.AGENT_GATEWAY._write_handlers[target]  # noqa: SLF001 - registry identity gate.
        handler = registration.handler
        assert handler.__self__ is owner
        assert handler.__func__ is getattr(type(owner), method_name)
        assert registration.risk_level == "medium"
        assert registration.advanced is False
        assert registration.risk_level_resolver is None
        assert registration.request_preparer is None
        assert registration.manual_approval_resolver is None
        assert registration.checkpoint_prepare_handler is None
        assert registration.requires_approved_execution_context is False
        assert registration.approved_execution_plan_builder is None
        assert registration.approval_category == ""
        assert registration.allow_future_category is False
        assert target in LOCAL_STATE_CHECKPOINT_TARGETS
    restore = dashboard_server.AGENT_GATEWAY._write_handlers[  # noqa: SLF001 - restore remains Gateway-owned.
        "vrcforge_restore_checkpoint"
    ].handler
    assert getattr(restore, "__self__", None) is not owner
    assert "restore_checkpoint" not in owner_source


def test_dry_run_uses_preflight_only_and_preserves_aliases() -> None:
    calls: list[object] = []

    class Service:
        def preflight_import(self, package_path: str, **kwargs: object) -> object:
            calls.append(("preflight", package_path, kwargs))
            return SimpleNamespace(as_dict=lambda: {"packageId": "preview"})

    ports = _ports(Service(), calls)
    ports = SkillPackageControllerPorts(
        make_service=ports.make_service,
        write_lock=_TrackingContext(calls, "forbidden-lock"),
        project_installed_skill=lambda *_args: pytest.fail("no projection"),
        set_projected_skill_enabled=lambda *_args: pytest.fail("no projection"),
        delete_projected_skill=lambda *_args: pytest.fail("no projection"),
        make_bad_request=ports.make_bad_request,
    )

    result = SkillPackageController(ports).import_package(
        {
            "packagePath": "",
            "package_path": "example.vsk",
            "dryRun": False,
            "dry_run": "false",
            "allowDowngrade": False,
            "allow_downgrade": "false",
            "devMode": False,
            "dev_mode": "false",
        }
    )

    assert result == {
        "ok": True,
        "dryRun": True,
        "preview": {"packageId": "preview"},
    }
    assert calls == [
        "factory",
        (
            "preflight",
            "example.vsk",
            {"allow_downgrade": True, "dev_mode": True},
        ),
    ]


@pytest.mark.parametrize(
    ("projection_params", "expect_projection"),
    [
        ({"projectToUserSkills": False, "project_to_user_skills": True}, False),
        ({"projectToUserSkills": None, "project_to_user_skills": False}, True),
        ({"projectToUserSkills": 0}, True),
        ({"projectToUserSkills": "false"}, True),
    ],
)
def test_live_import_keeps_install_projection_order_and_is_not_false_semantics(
    projection_params: dict[str, object],
    expect_projection: bool,
) -> None:
    calls: list[object] = []
    result = SimpleNamespace(
        installed_path=Path("installed"),
        preview=SimpleNamespace(manifest={"id": "community.test"}),
        registry_entry={"enabled": 0},
        as_dict=lambda: {"id": "community.test"},
    )

    class Service:
        def install_transaction(self, package_path: str, **kwargs: object) -> object:
            calls.append(("install", package_path, kwargs))
            return _TrackingContext(calls, "install-tx", result)

    owner = SkillPackageController(_ports(Service(), calls))
    output = owner.import_package(
        {"packagePath": "package.vsk", "source": "test", **projection_params}
    )

    assert output["ok"] is True
    assert output["imported"] == {"id": "community.test"}
    assert (output["projectedSkill"] is not None) is expect_projection
    assert calls[:3] == [
        "factory",
        "lock:enter",
        (
            "install",
            "package.vsk",
            {"source": "test", "allow_downgrade": False, "dev_mode": False},
        ),
    ]
    assert calls[-2:] == ["install-tx:exit", "lock:exit"]
    assert any(isinstance(call, tuple) and call[0] == "project" for call in calls) is expect_projection


def test_toggle_rejects_missing_id_before_factory_and_preserves_transaction() -> None:
    no_effects: list[object] = []
    owner = SkillPackageController(
        SkillPackageControllerPorts(
            make_service=lambda: pytest.fail("no service"),
            write_lock=_TrackingContext(no_effects, "lock"),
            project_installed_skill=lambda *_args: pytest.fail("no projection"),
            set_projected_skill_enabled=lambda *_args: pytest.fail("no projection"),
            delete_projected_skill=lambda *_args: pytest.fail("no projection"),
            make_bad_request=lambda message: AgentGatewayError(
                message,
                status_code=400,
            ),
        )
    )
    with pytest.raises(AgentGatewayError, match="skillPackageId is required") as exc:
        owner.set_enabled(
            {
                "skillPackageId": "  ",
                "skill_package_id": "must-not-fallback",
                "enabled": True,
            }
        )
    assert exc.value.status_code == 400
    assert no_effects == []

    calls: list[object] = []
    state = SimpleNamespace(
        manifest={"id": "skill"},
        as_dict=lambda: {"id": "skill", "enabled": True},
    )

    class Service:
        def state_transaction(self, ids: list[str]) -> object:
            calls.append(("state-transaction", ids))
            return _TrackingContext(calls, "state-tx")

        def set_enabled(self, skill_id: str, enabled: bool) -> object:
            calls.append(("set-enabled", skill_id, enabled))
            return state

    output = SkillPackageController(_ports(Service(), calls)).set_enabled(
        {
            "skill_package_id": " skill ",
            "enabled": "false",
            "syncProjectedSkill": None,
            "sync_projected_skill": False,
        }
    )
    assert output == {
        "ok": True,
        "state": {"id": "skill", "enabled": True},
        "projectedSkill": {"enabled": True},
    }
    assert calls == [
        "factory",
        "lock:enter",
        ("state-transaction", ["skill"]),
        "state-tx:enter",
        ("set-enabled", "skill", True),
        ("set-projection", {"id": "skill"}, True),
        "state-tx:exit",
        "lock:exit",
    ]

    calls = []
    skipped = SkillPackageController(_ports(Service(), calls)).set_enabled(
        {
            "id": "skill",
            "enabled": True,
            "syncProjectedSkill": False,
            "sync_projected_skill": True,
        }
    )
    assert skipped["projectedSkill"] is None
    assert not any(
        isinstance(call, tuple) and call[0] == "set-projection" for call in calls
    )


def test_uninstall_keeps_nested_projection_transaction_and_optional_skip() -> None:
    for remove_params, expect_delete in (
        ({"removeProjectedSkill": False, "remove_projected_skill": True}, False),
        ({"removeProjectedSkill": None, "remove_projected_skill": False}, True),
        ({"removeProjectedSkill": 0}, True),
        ({"removeProjectedSkill": "false"}, True),
    ):
        calls: list[object] = []
        result = SimpleNamespace(
            manifest={"id": "skill"},
            as_dict=lambda: {"id": "skill"},
        )

        class Service:
            def uninstall_transaction(self, skill_id: str) -> object:
                calls.append(("uninstall", skill_id))
                return _TrackingContext(calls, "uninstall-tx", result)

        output = SkillPackageController(_ports(Service(), calls)).uninstall(
            {"id": "skill", **remove_params}
        )
        assert output["uninstalled"] == {"id": "skill"}
        assert (output["projectedSkill"] is not None) is expect_delete
        assert calls[:3] == ["factory", "lock:enter", ("uninstall", "skill")]
        assert calls[-2:] == ["uninstall-tx:exit", "lock:exit"]
        assert any(
            isinstance(call, tuple) and call[0] == "delete-projection"
            for call in calls
        ) is expect_delete
