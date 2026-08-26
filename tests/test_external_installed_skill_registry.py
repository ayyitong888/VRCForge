from __future__ import annotations

import os
from contextlib import contextmanager
from pathlib import Path
from typing import Iterator

import pytest

import dashboard_server
from agent_gateway import AgentGatewayConfig, render_skill_markdown


@contextmanager
def isolated_installed_skill_gateway(root: Path) -> Iterator[Path]:
    gateway = dashboard_server.AGENT_GATEWAY
    original_config_path = gateway.config_path
    original_audit_dir = gateway.audit_dir
    gateway.configure_paths(root / "config" / "agent_gateway.json", root / "audit")
    gateway.save_config(
        AgentGatewayConfig(
            enabled=True,
            require_token=False,
            allow_write_requests=True,
            execution_mode="full",
        )
    )
    try:
        yield root
    finally:
        gateway.configure_paths(original_config_path, original_audit_dir)


def write_installed_skill(
    root: Path,
    name: str = "avatar-head-transplant",
    *,
    enabled: bool = True,
    permission_mode: str = "approval_required",
    support_files: dict[str, str] | None = None,
) -> Path:
    skill_dir = root / "skills" / name
    skill_dir.mkdir(parents=True, exist_ok=True)
    support = support_files or {}
    for relative, content in support.items():
        path = skill_dir.joinpath(*relative.split("/"))
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(content, encoding="utf-8")
    manifest = render_skill_markdown(
        {
            "name": name,
            "title": "Avatar Head Transplant",
            "description": "Move one compatible avatar head without replacing the body.",
            "permissionMode": permission_mode,
            "riskLevel": "low",
            "allowedTools": ["vrcforge_health"],
            "supportFiles": list(support),
            "enabled": enabled,
            "instructions": "Use Modular Avatar and preserve the user's original body material.",
        }
    )
    (skill_dir / "SKILL.md").write_text(manifest, encoding="utf-8")
    return skill_dir


def external_result(tool: str, params: dict[str, object] | None = None) -> dict[str, object]:
    return dashboard_server.AGENT_GATEWAY.call_external_mcp_tool(tool, params or {})


def test_installed_skill_tools_are_lazy_read_only_in_both_exposure_layers(tmp_path: Path) -> None:
    with isolated_installed_skill_gateway(tmp_path):
        gateway = dashboard_server.AGENT_GATEWAY
        expected = {"vrcforge_list_installed_skills", "vrcforge_read_installed_skill"}

        for layer in ("planning", "execution"):
            tools = gateway.build_external_mcp_tools(layer, tool_blocks=["skills/installed"])
            expected_layer = expected | (
                {"vrcforge_create_installed_skill"} if layer == "execution" else set()
            )
            assert {item["name"] for item in tools} == expected_layer
            assert all(
                item["_meta"]["permission"] == "ReadOnly"
                for item in tools
                if item["name"] in expected
            )
            assert all(item["_meta"]["toolBlock"] == "skills/installed" for item in tools)
            assert all("When to use:" in item["description"] for item in tools)
            assert all("When NOT to use:" in item["description"] for item in tools)

        default_names = {
            item["name"] for item in gateway.build_external_mcp_tools("planning")
        }
        assert expected.isdisjoint(default_names)

        tree = gateway.external_mcp_tool_block_index({"block": "skills/installed"})
        skills = next(item for item in tree["children"] if item["block"] == "skills")
        installed = next(
            item for item in skills["children"] if item["block"] == "skills/installed"
        )
        assert set(installed["toolNames"]) == expected | {"vrcforge_create_installed_skill"}
        assert installed["writeToolCount"] == 1


def test_installed_skill_list_tracks_the_one_live_registry_without_eager_content(
    tmp_path: Path,
) -> None:
    with isolated_installed_skill_gateway(tmp_path):
        write_installed_skill(
            tmp_path,
            support_files={"references/workflow.md": "Sensitive workflow body is lazy."},
        )
        write_installed_skill(tmp_path, "disabled-workflow", enabled=False)

        listing = external_result("vrcforge_list_installed_skills")
        assert listing["ok"] is True
        result = listing["result"]
        assert result["count"] == 1
        assert [item["name"] for item in result["skills"]] == ["avatar-head-transplant"]
        assert "instructions" not in result["skills"][0]
        assert "supportFiles" not in result["skills"][0]
        assert "content" not in result["skills"][0]
        assert "storagePath" not in result["skills"][0]
        assert str(tmp_path) not in str(result)

        write_installed_skill(tmp_path, "disabled-workflow", enabled=True)
        updated = external_result("vrcforge_list_installed_skills")
        assert {item["name"] for item in updated["result"]["skills"]} == {
            "avatar-head-transplant",
            "disabled-workflow",
        }

        write_installed_skill(tmp_path, enabled=False)
        disabled = external_result("vrcforge_list_installed_skills")
        assert [item["name"] for item in disabled["result"]["skills"]] == [
            "disabled-workflow"
        ]
        visible_disabled = external_result(
            "vrcforge_list_installed_skills", {"includeDisabled": True}
        )
        indexed = {item["name"]: item for item in visible_disabled["result"]["skills"]}
        assert indexed["avatar-head-transplant"]["enabled"] is False
        assert indexed["disabled-workflow"]["enabled"] is True


def test_installed_skill_instructions_and_one_support_file_load_independently(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    with isolated_installed_skill_gateway(tmp_path):
        write_installed_skill(
            tmp_path,
            support_files={
                "references/workflow.md": "Human-readable seam workflow.",
                "workflows/head.json": '{"steps":14}',
            },
        )

        instructions = external_result(
            "vrcforge_read_installed_skill", {"name": "avatar-head-transplant"}
        )
        assert instructions["ok"] is True
        result = instructions["result"]
        assert result["instructions"].startswith("Use Modular Avatar")
        assert result["allowedTools"] == ["vrcforge_health"]
        assert set(result["supportFiles"]) == {
            "references/workflow.md",
            "workflows/head.json",
        }
        assert "content" not in result
        assert "storagePath" not in result
        assert str(tmp_path) not in str(result)

        registry = dashboard_server.AGENT_GATEWAY.skills
        original_loader = registry.load_runtime_skill_support_files
        selected_files: list[list[str]] = []

        def recording_loader(skill: dict[str, object]) -> list[dict[str, str]]:
            selected_files.append(list(skill["supportFiles"]))
            return original_loader(skill)

        monkeypatch.setattr(type(registry), "load_runtime_skill_support_files", lambda self, skill: recording_loader(skill))
        document = external_result(
            "vrcforge_read_installed_skill",
            {"name": "avatar-head-transplant", "file": "workflows/head.json"},
        )
        assert document["ok"] is True
        assert document["result"]["file"] == "workflows/head.json"
        assert document["result"]["content"] == '{"steps":14}'
        assert selected_files == [["workflows/head.json"]]


def test_external_agentic_skill_exposes_mode_without_deterministic_metadata(
    tmp_path: Path,
) -> None:
    with isolated_installed_skill_gateway(tmp_path):
        write_installed_skill(
            tmp_path,
            support_files={"workflows/community.json": '{"steps":[{"tool":"vrcforge_health"}]}'},
        )

        indexed = external_result("vrcforge_list_installed_skills")["result"]["skills"][0]
        loaded = external_result(
            "vrcforge_read_installed_skill", {"name": "avatar-head-transplant"}
        )["result"]

        for skill in (indexed, loaded):
            assert skill["execution"] == skill["executionMode"] == "agentic"
            assert "workflowDigest" not in skill
            assert "runtimeEnforced" not in skill
            assert "workflowSteps" not in skill


@pytest.mark.parametrize(
    "requested_file",
    [
        "../secret.txt",
        "..\\secret.txt",
        "/absolute/secret.txt",
        "references/undeclared.md",
        "references/WORKFLOW.md",
    ],
)
def test_installed_skill_refuses_undeclared_or_escaping_support_files(
    tmp_path: Path,
    requested_file: str,
) -> None:
    with isolated_installed_skill_gateway(tmp_path):
        write_installed_skill(
            tmp_path,
            support_files={"references/workflow.md": "Declared safe document."},
        )
        (tmp_path / "secret.txt").write_text("private material", encoding="utf-8")

        response = external_result(
            "vrcforge_read_installed_skill",
            {"name": "avatar-head-transplant", "file": requested_file},
        )

        assert response["ok"] is False
        assert "declared" in str(response.get("error", "")).lower()
        assert "private material" not in str(response)


def test_installed_skill_refuses_disabled_unknown_and_builtin_skill_names(
    tmp_path: Path,
) -> None:
    with isolated_installed_skill_gateway(tmp_path):
        write_installed_skill(tmp_path, enabled=False)

        for name in ("avatar-head-transplant", "missing-skill", "vrcforge-health"):
            response = external_result("vrcforge_read_installed_skill", {"name": name})
            assert response["ok"] is False
            assert "unavailable or disabled" in str(response.get("error", "")).lower()


@pytest.mark.parametrize("support_count, visible", [(16, True), (17, False)])
def test_external_skill_discovery_preserves_the_runtime_support_file_limit(
    tmp_path: Path,
    support_count: int,
    visible: bool,
) -> None:
    with isolated_installed_skill_gateway(tmp_path):
        write_installed_skill(
            tmp_path,
            support_files={
                f"references/part-{index:02d}.md": f"Safe support document {index}."
                for index in range(support_count)
            },
        )
        internal = next(
            skill
            for skill in dashboard_server.AGENT_GATEWAY.skills.build_skill_registry()["skills"]
            if skill.get("name") == "avatar-head-transplant"
        )

        assert internal["enabled"] is True
        assert internal["available"] is visible
        assert internal["validation"]["status"] == ("ok" if visible else "error")
        if not visible:
            assert "16-file runtime limit" in internal["validation"]["reasons"][0]

        for arguments in ({}, {"includeDisabled": True}):
            listing = external_result("vrcforge_list_installed_skills", arguments)
            assert (
                "avatar-head-transplant"
                in {item["name"] for item in listing["result"]["skills"]}
            ) is visible

        loaded = external_result(
            "vrcforge_read_installed_skill", {"name": "avatar-head-transplant"}
        )
        assert loaded["ok"] is visible
        if not visible:
            assert "unavailable or disabled" in str(loaded.get("error", "")).lower()


def test_installed_skill_refuses_symlinked_declared_support_file(tmp_path: Path) -> None:
    with isolated_installed_skill_gateway(tmp_path):
        skill_dir = write_installed_skill(
            tmp_path,
            support_files={"references/workflow.md": "Initially safe."},
        )
        outside = tmp_path / "outside.md"
        outside.write_text("outside private data", encoding="utf-8")
        declared = skill_dir / "references" / "workflow.md"
        declared.unlink()
        try:
            os.symlink(outside, declared)
        except OSError as exc:
            pytest.skip(f"Host does not permit creating a test symlink: {exc}")

        response = external_result(
            "vrcforge_read_installed_skill",
            {"name": "avatar-head-transplant", "file": "references/workflow.md"},
        )

        assert response["ok"] is False
        assert "outside private data" not in str(response)


def test_external_agent_creates_and_immediately_installs_one_shared_registry_skill(
    tmp_path: Path,
) -> None:
    with isolated_installed_skill_gateway(tmp_path):
        arguments = {
            "name": "agent-created-workflow",
            "title": "Agent Created Workflow",
            "description": "Create a reusable safe avatar workflow.",
            "instructions": "Inspect the avatar, request approval, then validate.",
            "allowedTools": ["vrcforge_health"],
            "permissionMode": "read_only",
        }
        created = external_result("vrcforge_create_installed_skill", arguments)

        assert created["ok"] is True
        assert created["result"]["installed"] is True
        assert created["result"]["installedSkill"]["name"] == "agent-created-workflow"
        assert "storagePath" not in str(created["result"])
        assert (tmp_path / "skills" / "agent-created-workflow" / "SKILL.md").is_file()

        internal = dashboard_server.AGENT_GATEWAY.skills.build_skill_registry()
        assert any(item["name"] == "agent-created-workflow" for item in internal["skills"])
        external = external_result("vrcforge_list_installed_skills")
        assert [item["name"] for item in external["result"]["skills"]] == [
            "agent-created-workflow"
        ]
        instructions = external_result(
            "vrcforge_read_installed_skill", {"name": "agent-created-workflow"}
        )
        assert instructions["result"]["instructions"] == arguments["instructions"]

        duplicate = external_result(
            "vrcforge_create_installed_skill",
            {**arguments, "instructions": "Must not replace the original."},
        )
        assert duplicate["ok"] is False
        preserved = external_result(
            "vrcforge_read_installed_skill", {"name": "agent-created-workflow"}
        )
        assert preserved["result"]["instructions"] == arguments["instructions"]


def test_external_agent_creates_installs_disables_and_reenables_vsk(
    tmp_path: Path,
) -> None:
    with isolated_installed_skill_gateway(tmp_path):
        package_path = tmp_path / "agent-authored.vsk"
        source_path = tmp_path / "agent-authored-source"
        identity = {
            "summary": {
                "status": "passed",
                "workflow": "captured_workflow",
                "steps": ["captured.read"],
            },
            "packageId": "community.agent.created-workflow",
            "skillName": "agent-captured-workflow",
            "title": "Agent Captured Workflow",
        }

        preview = external_result("vrcforge_preview_path_to_skill", identity)
        assert preview["ok"] is True
        assert preview["result"]["dryRun"] is True
        assert not package_path.exists()

        authored = external_result(
            "vrcforge_write_path_to_skill",
            {
                **identity,
                "outputPath": str(source_path),
                "exportVsk": True,
                "confirmExport": True,
                "packageOutputPath": str(package_path),
            },
        )
        assert authored["ok"] is True, authored
        assert package_path.is_file()

        imported = external_result(
            "vrcforge_import_skill_package", {"packagePath": str(package_path)}
        )
        assert imported["ok"] is True, imported

        listing = external_result(
            "vrcforge_list_installed_skills", {"includeDisabled": True}
        )
        installed = next(
            item
            for item in listing["result"]["skills"]
            if item["name"] == "agent-captured-workflow"
        )
        assert installed["packageId"] == "community.agent.created-workflow"
        if not installed["enabled"]:
            first_enable = external_result(
                "vrcforge_set_skill_package_enabled",
                {"skillPackageId": installed["packageId"], "enabled": True},
            )
            assert first_enable["ok"] is True, first_enable

        enabled_listing = external_result("vrcforge_list_installed_skills")
        assert any(
            item["name"] == "agent-captured-workflow" and item["enabled"] is True
            for item in enabled_listing["result"]["skills"]
        )

        disabled = external_result(
            "vrcforge_set_skill_package_enabled",
            {"skillPackageId": installed["packageId"], "enabled": False},
        )
        assert disabled["ok"] is True, disabled
        assert not any(
            item["name"] == "agent-captured-workflow"
            for item in external_result("vrcforge_list_installed_skills")["result"][
                "skills"
            ]
        )
        visible_disabled = external_result(
            "vrcforge_list_installed_skills", {"includeDisabled": True}
        )
        disabled_skill = next(
            item
            for item in visible_disabled["result"]["skills"]
            if item["name"] == "agent-captured-workflow"
        )
        assert disabled_skill["enabled"] is False
        assert disabled_skill["packageId"] == "community.agent.created-workflow"
        rejected = external_result(
            "vrcforge_read_installed_skill", {"name": "agent-captured-workflow"}
        )
        assert rejected["ok"] is False

        reenabled = external_result(
            "vrcforge_set_skill_package_enabled",
            {"skillPackageId": disabled_skill["packageId"], "enabled": True},
        )
        assert reenabled["ok"] is True, reenabled
        restored = external_result(
            "vrcforge_read_installed_skill", {"name": "agent-captured-workflow"}
        )
        assert restored["ok"] is True
        internal = dashboard_server.AGENT_GATEWAY.skills.build_skill_registry()
        assert any(
            item["name"] == "agent-captured-workflow"
            and item["enabled"] is True
            and item["packageId"] == "community.agent.created-workflow"
            for item in internal["skills"]
        )
