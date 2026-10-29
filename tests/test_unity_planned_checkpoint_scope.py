import pytest


@pytest.fixture
def checkpoint_server(tmp_path, monkeypatch):
    import dashboard_server as server

    root = tmp_path / "project"
    for folder in ("Assets", "Packages", "ProjectSettings"):
        (root / folder).mkdir(parents=True)
    (root / "Assets" / "Existing.asset").write_text("asset", encoding="utf-8")
    (root / "Assets" / "Existing.asset.meta").write_text("guid: " + "a" * 32, encoding="utf-8")
    prepared = []

    def prepare(project, *, checkpoint_files):
        prepared.append((checkpoint_files, {}))
        return {"ok": True, "assetBaseline": []}

    monkeypatch.setattr(server, "prepare_unity_checkpoint_sync", prepare)
    return server, root, prepared


@pytest.mark.parametrize("tool,action,path", [
    ("vrcforge_ensure_expression_parameter", "ensure_expression_parameter", "Assets/Existing.asset"),
    ("vrcforge_ensure_expression_menu_control", "ensure_expression_menu_control", "Assets/NewMenu.asset"),
    ("vrcforge_ensure_animator_state", "ensure_animator_state", "Assets/NewState.anim"),
])
def test_direct_ensure_seals_and_reuses_core_checkpoint_plan(checkpoint_server, monkeypatch, tool, action, path):
    server, root, prepared = checkpoint_server
    preview = lambda args, preview: {"ok": True, "preview": True, "plan": {
        "action": action, "checkpointAssetPaths": [path],
    }}
    core_name = {
        "vrcforge_ensure_expression_parameter": "ensure_expression_parameter_sync",
        "vrcforge_ensure_expression_menu_control": "ensure_expression_menu_control_sync",
        "vrcforge_ensure_animator_state": "ensure_animator_state_sync",
    }[tool]
    monkeypatch.setattr(server, core_name, preview)
    handler = server.AGENT_GATEWAY._write_handlers[tool]
    args, _ = handler.request_preparer({"projectPath": str(root), "avatarPath": "Avatar"}, None)
    assert args["expectedCheckpointAssetPaths"] == [path]
    result = server.prepare_authoritative_unity_checkpoint_sync(
        root, {**args, "_checkpointTargetTool": tool},
    )
    assert result["ok"] is True, result
    assert path in (result["assetFootprint"]["archiveFiles"] + result["assetFootprint"]["archiveAbsentFiles"])
    assert prepared and prepared[0][0] == sorted(
        result["assetFootprint"]["archiveFiles"] + result["assetFootprint"]["archiveAbsentFiles"]
    )


def test_menu_missing_root_uses_core_planned_new_menu_outputs(checkpoint_server, monkeypatch):
    server, root, prepared = checkpoint_server
    plan = {"action": "create", "targetMenuExists": False,
            "rootMenuPath": "Assets/Generated/Avatar_ExpressionsMenu.asset",
            "checkpointAssetPaths": [], "newMenuAssetPaths": ["Assets/Generated/Child.asset"],
            "plannedAssetPaths": ["Assets/Generated/Avatar_ExpressionsMenu.asset",
                                  "Assets/Generated/Child.asset"]}
    monkeypatch.setattr(server, "manage_expression_menu_sync", lambda *a, **k: {"ok": True, "preview": True, "plan": plan})
    handler = server.AGENT_GATEWAY._write_handlers["vrcforge_manage_expression_menu"]
    args, _ = handler.request_preparer({"action": "create"}, None)
    result = server.prepare_authoritative_unity_checkpoint_sync(
        root, {**args, "_checkpointTargetTool": "vrcforge_manage_expression_menu"},
    )
    assert result["ok"] is True, result
    assert set(result["assetFootprint"]["archiveAbsentFiles"]) >= {
        "Assets/Generated/Avatar_ExpressionsMenu.asset", "Assets/Generated/Child.asset",
    }


def test_menu_request_binds_core_planned_paths_and_blocks_drift(checkpoint_server, monkeypatch):
    server, root, prepared = checkpoint_server
    plan = {"action": "create", "targetMenuExists": False,
            "rootMenuPath": "Assets/Generated/Avatar_ExpressionsMenu.asset",
            "checkpointAssetPaths": [], "newMenuAssetPaths": [],
            "plannedAssetPaths": ["Assets/Generated/Avatar_ExpressionsMenu.asset"]}
    monkeypatch.setattr(server, "manage_expression_menu_sync", lambda *a, **k: {
        "ok": True, "preview": True, "plan": plan,
    })
    handler = server.AGENT_GATEWAY._write_handlers["vrcforge_manage_expression_menu"]
    args, _ = handler.request_preparer({"action": "create"}, None)
    assert args["expectedPlanAssetPaths"] == plan["plannedAssetPaths"]
    result = server.prepare_authoritative_unity_checkpoint_sync(
        root, {**args, "_checkpointTargetTool": "vrcforge_manage_expression_menu"},
    )
    assert result["ok"] is True, result
    plan["plannedAssetPaths"].append("Assets/Generated/Drift.asset")
    rejected = server.prepare_authoritative_unity_checkpoint_sync(
        root, {**args, "_checkpointTargetTool": "vrcforge_manage_expression_menu"},
    )
    assert rejected["ok"] is False and len(prepared) == 1


def test_single_animation_clip_path_uses_core_preview_plan(checkpoint_server, monkeypatch):
    server, root, prepared = checkpoint_server
    monkeypatch.setattr(server, "write_animation_curve_sync", lambda *a, **k: {
        "ok": True, "preview": True,
        "plan": {"action": "set_curve", "clipPath": "Assets/Generated/New.anim", "willCreateClip": True},
    })
    result = server.prepare_authoritative_unity_checkpoint_sync(
        root, {"_checkpointTargetTool": "vrcforge_write_animation_curve",
              "clipPath": "Assets/Generated/New.anim", "action": "set_curve"},
    )
    assert result["ok"] is True, result
    assert "Assets/Generated/New.anim" in result["assetFootprint"]["archiveAbsentFiles"]


def test_unbounded_user_tool_uses_existing_project_checkpoint(checkpoint_server):
    server, root, prepared = checkpoint_server
    result = server.prepare_authoritative_unity_checkpoint_sync(
        root, {"_checkpointTargetTool": "vrcforge_invoke_user_unity_tool",
              "projectPath": str(root), "packageId": "fixture.package",
              "packageDigest": "a" * 64, "toolId": "declared.extension", "arguments": {}},
    )
    assert result == {"ok": True, "assetBaseline": []}
    assert prepared == [(None, {})]


def test_clothing_tuning_uses_existing_project_checkpoint_until_core_exposes_plan(checkpoint_server):
    server, root, prepared = checkpoint_server
    handler = server.AGENT_GATEWAY._write_handlers["vrcforge_apply_clothing_fx"]
    args, _ = handler.request_preparer({"projectPath": str(root), "avatar_path": "Avatar",
                                        "dry_run": False, "items": [{"name": "coat"}]}, None)
    result = server.prepare_authoritative_unity_checkpoint_sync(
        root, {**args, "_checkpointTargetTool": "vrcforge_apply_clothing_fx"},
    )
    assert result["ok"] is True
    assert prepared == [(None, {})]


def test_parameter_optimization_preparer_uses_full_project_checkpoint(checkpoint_server):
    server, root, prepared = checkpoint_server
    handler = server.AGENT_GATEWAY._write_handlers["vrcforge_apply_parameter_optimization"]
    args, _ = handler.request_preparer({"projectPath": str(root), "avatar_path": "Avatar",
                                        "dry_run": False, "suggestions": [{"name": "FixtureParameter"}]}, None)
    result = server.prepare_authoritative_unity_checkpoint_sync(
        root, {**args, "_checkpointTargetTool": "vrcforge_apply_parameter_optimization"},
    )
    assert result["ok"] is True
    assert prepared == [(None, {})]


def test_user_tool_install_revalidates_prepared_package_before_full_project_checkpoint(checkpoint_server, monkeypatch):
    server, root, prepared = checkpoint_server
    arguments = {"projectPath": str(root), "packageId": "fixture.package", "preparedPlan": {"planDigest": "a" * 64}}
    monkeypatch.setattr(server, "prepare_install", lambda values, _preview, _service: (dict(values), {}))
    result = server.prepare_authoritative_unity_checkpoint_sync(
        root, {**arguments, "_checkpointTargetTool": "vrcforge_install_user_unity_tools"},
    )
    assert result["ok"] is True
    assert prepared == [(None, {})]


def test_explicit_none_uses_the_core_full_project_checkpoint_contract(tmp_path, monkeypatch):
    from types import SimpleNamespace
    import inspect
    import dashboard_server as server

    calls = []
    monkeypatch.setattr(server, "UnityMcpCoreClient", lambda *a, **k: SimpleNamespace(
        core_info=lambda: {"checkpointPrepareSavesDirtyAssets": False},
    ))
    monkeypatch.setattr(server, "load_dashboard_settings", lambda _request: SimpleNamespace(
        unity_mcp_timeout_seconds=30,
    ))
    monkeypatch.setattr(server, "invoke_unity_mcp", lambda settings, tool, args, **kwargs: (
            calls.append((tool, args, kwargs)) or SimpleNamespace(
            exit_code=0, stdout="", stderr="",
            payload={"structuredContent": {"data": {"ok": True}}},
        )
    ))
    signature = inspect.signature(server.prepare_unity_checkpoint_sync)
    with pytest.raises(TypeError):
        signature.bind(tmp_path)
    result = server.prepare_unity_checkpoint_sync(tmp_path, checkpoint_files=None)
    assert result["ok"] is True
    assert calls == [("vrc_prepare_checkpoint", {"projectPath": str(tmp_path)}, {
        "execution_context": {"lane": "app_safety_control"}, "preserve_tool_error": True,
    })]


def test_scene_visibility_checkpoint_revalidates_exact_target(checkpoint_server, monkeypatch):
    server, root, prepared = checkpoint_server
    target = {
        "schema": "vrcforge.execution_target.v1", "scope": "object", "ambiguous": False,
        "resolutionCandidateCount": 1,
        "project": {"root": str(root), "projectId": "project-id"},
        "scene": {"assetPath": "Assets/Main.unity", "guid": "scene-guid", "digest": "a" * 64},
        "object": {"globalObjectId": "object-id", "exactHierarchyPath": "Avatar/Jacket"},
    }
    (root / "Assets" / "Main.unity").write_text("scene", encoding="utf-8")
    (root / "Assets" / "Main.unity.meta").write_text("guid: scene-guid", encoding="utf-8")
    monkeypatch.setattr(server, "list_execution_targets_sync", lambda query: {"ok": True, "targets": [target]})
    result = server.prepare_authoritative_unity_checkpoint_sync(root, {
        "_checkpointTargetTool": "vrcforge_toggle_scene_object", "objectPath": "Avatar/Jacket",
        "active": False, "executionTarget": target,
    })
    assert result["ok"] is True, result
    assert result["archiveAssetPaths"] == ["Assets/Main.unity"]
