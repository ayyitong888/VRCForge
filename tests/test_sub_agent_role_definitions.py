from __future__ import annotations

import json
import logging
import threading
from unittest.mock import Mock

import pytest

import sub_agent_delegate
from sub_agent_delegate import build_sub_agent_role_handlers, build_sub_agent_roles
from sub_agent_roles import load_sub_agent_roles
from sub_agent_tasks import SubAgentRole


def _write_role(directory, filename, payload):
    directory.mkdir(parents=True, exist_ok=True)
    path = directory / filename
    path.write_text(json.dumps(payload), encoding="utf-8")
    return path


def test_packaged_json_definitions_load_the_eight_builtin_roles_in_legacy_order():
    roles = build_sub_agent_roles(data_dir=None)
    assert [role.id for role in roles] == [
        "project_index_review",
        "outfit_package_inspection",
        "validation_triage",
        "selected_context_review",
        "package_install_diagnosis",
        "outfit_import_plan_review",
        "skill_delegate",
        "llm_readonly",
    ]
    assert roles[0].handler == "project_index_review"
    assert roles[6].handler == "skill_delegate"
    assert roles[7].handler == "llm_readonly"
    # The built-in role retains the existing planning read-only pool. Custom
    # declarations may narrow it; shipping declarations must not drop paths.
    assert roles[7].tools is None


def test_delegate_schema_uses_injected_role_snapshot_without_reloading(monkeypatch):
    roles = [SubAgentRole("fixture_role", "Fixture role", "Use the fixture role snapshot.")]
    loader = Mock(side_effect=AssertionError("must use injected roles"))
    monkeypatch.setattr(sub_agent_delegate, "load_sub_agent_roles", loader)

    schema = sub_agent_delegate.build_sub_agent_input_schema(roles)
    role_property = schema["properties"]["role"]
    assert role_property["enum"] == ["fixture_role"]
    assert "fixture_role (Fixture role): Use the fixture role snapshot." in role_property["description"]
    loader.assert_not_called()


def test_user_role_files_add_generic_readonly_agent_with_declared_defaults(tmp_path):
    role_dir = tmp_path / "sub-agents" / "roles"
    _write_role(role_dir, "shader_research.json", {
        "id": "shader_research", "name": "Shader research",
        "description": "Inspect shader references and report evidence.",
        "model": "configured-light-model", "systemPrompt": "Cite observed values.",
        "maxSteps": 5, "tools": ["vrcforge_read_text_file", "vrcforge_search_text"],
    })
    roles = load_sub_agent_roles(data_dir=tmp_path)
    role = next(role for role in roles if role.id == "shader_research")
    assert role.handler == "llm_readonly"
    assert role.tool_profile == "read-only" and role.read_only is True
    assert role.model == "configured-light-model"
    assert role.system_prompt == "Cite observed values."
    assert role.max_steps == 5
    assert role.tools == ("vrcforge_read_text_file", "vrcforge_search_text")
    assert "shader_research" in build_sub_agent_role_handlers(object(), roles)


def test_custom_role_handler_uses_generic_runner_and_forwards_declared_tool_allowlist(tmp_path):
    role_dir = tmp_path / "sub-agents" / "roles"
    _write_role(role_dir, "shader_research.json", {
        "id": "shader_research", "title": "Shader research",
        "description": "Inspect shader references.", "tools": ["vrcforge_read_text_file"],
    })
    roles = build_sub_agent_roles(data_dir=tmp_path)

    class Gateway:
        def __init__(self):
            self.call = None

        def run_sub_agent_llm(self, **kwargs):
            self.call = kwargs
            return {
                "ok": True,
                "finalReport": "Reviewed selected shader docs.",
                "stepCount": 2,
                "providerRoute": {"provider": "fixture-provider", "model": "resolved-model", "apiKey": "must-not-pass"},
            }

    gateway = Gateway()
    handlers = build_sub_agent_role_handlers(gateway, roles)
    result = handlers["shader_research"]({
        "task": "Inspect the shader settings.", "childSessionId": "child-1",
        "taskId": "task-1", "isResume": False, "provider": "fixture-provider", "model": "fixture-model",
        "systemPrompt": "Report sources.", "maxSteps": 4,
        "tools": ["vrcforge_read_text_file"], "selectedMaterials": [],
    }, threading.Event())

    assert gateway.call["allowed_tools"] == ["vrcforge_read_text_file"]
    assert gateway.call["provider"] == "fixture-provider"
    assert gateway.call["model"] == "fixture-model"
    assert result["role"] == "shader_research"
    assert result["finalReport"] == "Reviewed selected shader docs."
    assert result["providerRoute"] == {"provider": "fixture-provider", "model": "resolved-model"}


def test_omitted_tools_inherit_readonly_profile_and_explicit_empty_list_means_no_tools(tmp_path):
    role_dir = tmp_path / "sub-agents" / "roles"
    _write_role(role_dir, "inherit.json", {"id": "inherit_tools", "title": "Inherit", "description": "Use safe read tools."})
    _write_role(role_dir, "none.json", {"id": "no_tools", "title": "No tools", "description": "Reason only.", "tools": []})
    roles = {role.id: role for role in load_sub_agent_roles(data_dir=tmp_path)}
    assert roles["inherit_tools"].tools is None
    assert roles["no_tools"].tools == ()


def test_invalid_and_duplicate_user_files_are_skipped_with_path_and_reason_logged(tmp_path, caplog):
    role_dir = tmp_path / "sub-agents" / "roles"
    valid_path = _write_role(role_dir, "valid.json", {
        "id": "evidence_reader", "title": "Evidence reader", "description": "Read only."
    })
    duplicate_path = _write_role(role_dir, "duplicate.json", {
        "id": "llm_readonly", "title": "Replacement", "description": "Must not replace packaged role."
    })
    invalid_path = role_dir / "malformed.json"
    invalid_path.write_text("{broken", encoding="utf-8")
    caplog.set_level(logging.WARNING, logger="sub_agent_roles")
    roles = load_sub_agent_roles(data_dir=tmp_path)
    ids = {role.id for role in roles}
    assert "evidence_reader" in ids
    assert len([role for role in roles if role.id == "llm_readonly"]) == 1
    assert str(duplicate_path) in caplog.text
    assert "duplicate role id" in caplog.text
    assert str(invalid_path) in caplog.text
    assert str(valid_path) not in caplog.text


@pytest.mark.parametrize("payload", [
    {"id": "unsafe_custom", "title": "Unsafe", "description": "No", "handler": "skill_delegate"},
    {"id": "unsafe_custom", "title": "Unsafe", "description": "No", "toolProfile": "plan-only"},
    {"id": "unsafe_custom", "title": "Unsafe", "description": "No", "tools": [" "]},
    {"id": "unsafe_custom", "title": "Unsafe", "description": "No", "maxSteps": True},
])
def test_custom_role_cannot_select_legacy_handler_profile_or_invalid_metadata(tmp_path, caplog, payload):
    role_dir = tmp_path / "sub-agents" / "roles"
    role_path = _write_role(role_dir, "bad.json", payload)
    caplog.set_level(logging.WARNING, logger="sub_agent_roles")
    roles = load_sub_agent_roles(data_dir=tmp_path)
    assert "unsafe_custom" not in {role.id for role in roles}
    assert str(role_path) in caplog.text
    assert "Skipping invalid sub-agent role file" in caplog.text


def test_missing_or_corrupt_builtin_declarations_fail_package_load(tmp_path):
    with pytest.raises(RuntimeError, match="missing"):
        load_sub_agent_roles(data_dir=tmp_path, builtin_dir=tmp_path / "absent")
    builtin = tmp_path / "builtins"
    _write_role(builtin, "broken.json", {"id": "bad_builtin", "title": "Bad", "description": "bad"})
    with pytest.raises(RuntimeError, match="missing"):
        load_sub_agent_roles(data_dir=tmp_path, builtin_dir=builtin)
