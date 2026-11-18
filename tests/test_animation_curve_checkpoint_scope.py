import pytest


@pytest.fixture
def curve_checkpoint_server(tmp_path, monkeypatch):
    import dashboard_server as server

    project = tmp_path / "project"
    (project / "Assets").mkdir(parents=True)
    calls = []

    def prepare(root, *, checkpoint_files):
        calls.append((root, checkpoint_files))
        return {"ok": True, "assetBaseline": []}

    monkeypatch.setattr(server, "prepare_unity_checkpoint_sync", prepare)
    return server, project, calls


def _clip(path, guid="a" * 32, **overrides):
    return {"clipPath": path, "exists": True, "assetGuid": guid, "fileDigest": "d" * 64,
            "fileVersion": "123:456:789:1024", **overrides}


@pytest.mark.parametrize("shape", ["single", "one_clip_batch", "multi_clip"])
def test_checkpoint_and_apply_keep_first_preview_for_every_shape(curve_checkpoint_server, monkeypatch, shape):
    from unity_execution_plans_scene import build_scene_execution_plan

    server, project, calls = curve_checkpoint_server
    paths = ["Assets/Outfits/A.anim", "Assets/Outfits/B.anim"] if shape == "multi_clip" else ["Assets/Outfits/A.anim"]
    rows = {"propertyName": "m_IsActive", "constantFloat": 1}
    if shape == "multi_clip":
        arguments = {"clips": [{"clipPath": path, "curves": [rows]} for path in paths]}
    elif shape == "one_clip_batch":
        arguments = {"clipPath": paths[0], "curves": [rows]}
    else:
        arguments = {"clipPath": paths[0], **rows}
    evidence = [_clip(path, f"{idx:032x}") for idx, path in enumerate(paths, 1)]
    preview_calls = []

    def first_preview(request, *, preview):
        assert preview and "expectedClipFiles" not in request
        preview_calls.append(request)
        return {"ok": True, "preview": True, "clipFiles": evidence}

    monkeypatch.setattr(server, "write_animation_curve_sync", first_preview)

    prepared, approval = server.prepare_animation_curve_request(arguments, None)
    assert approval["clipFiles"] == evidence
    assert prepared["expectedClipFiles"] == evidence
    result = server.prepare_authoritative_unity_checkpoint_sync(
        project, {**prepared, "_checkpointTargetTool": "vrcforge_write_animation_curve"},
    )
    assert result["ok"] is True, result
    assert len(preview_calls) == 1
    assert len(calls) == 1
    assert set(calls[0][1]) == {f"{path}{suffix}" for path in paths for suffix in ("", ".meta")} | {"Assets/Outfits.meta"}
    plan = build_scene_execution_plan("vrcforge_write_animation_curve", prepared)
    assert plan[0][1]["expectedClipFiles"] == evidence


def test_owned_missing_clip_keeps_create_checkpoint(curve_checkpoint_server):
    server, project, calls = curve_checkpoint_server
    path = "Assets/New.anim"
    result = server.prepare_authoritative_unity_checkpoint_sync(
        project, {"_checkpointTargetTool": "vrcforge_write_animation_curve", "clipPath": path,
                  "expectedClipFiles": [{"clipPath": path, "exists": False}]},
    )
    assert result["ok"] is True, result
    assert set(result["assetFootprint"]["archiveAbsentFiles"]) == {path, path + ".meta"}
    assert set(calls[0][1]) == {path, path + ".meta"}


@pytest.mark.parametrize("evidence", [
    [],
    [_clip("Assets/A.anim"), _clip("Assets/A.anim", "b" * 32)],
    [_clip("Assets/A.anim"), _clip("Assets/B.anim")],
    [_clip("Assets/../outside.anim")],
    [{"clipPath": "Assets/A.anim", "exists": True, "assetGuid": "a" * 32, "fileVersion": ""}],
])
def test_missing_or_invalid_clip_evidence_never_repreviews(curve_checkpoint_server, monkeypatch, evidence):
    server, project, calls = curve_checkpoint_server

    def fail_preview(*args, **kwargs):
        pytest.fail("Checkpoint must not refresh an approval preview.")

    monkeypatch.setattr(server, "write_animation_curve_sync", fail_preview)
    result = server.prepare_authoritative_unity_checkpoint_sync(
        project, {"_checkpointTargetTool": "vrcforge_write_animation_curve",
                  "clips": [{"clipPath": row["clipPath"]} for row in evidence],
                  "expectedClipFiles": evidence},
    )
    assert result["ok"] is False and result["blocking"] is True
    assert result["code"] == "checkpoint_scope_unresolved"
    assert result["mutationStarted"] is False and result["saved"] is False
    assert calls == []


def test_raw_wrapper_keeps_same_clip_evidence_without_checkpoint_preview(curve_checkpoint_server, monkeypatch):
    server, project, calls = curve_checkpoint_server
    from authoritative_unity_writes import prepare_authoritative_unity_write

    path = "Assets/A.anim"
    evidence = [_clip(path)]
    request = {"projectPath": str(project), "toolName": "vrc_write_animation_curve",
               "arguments": {"clipPath": path, "propertyName": "m_IsActive", "constantFloat": 0}}
    preview_calls = []

    def preview(tool, arguments):
        preview_calls.append((tool, arguments))
        return {"ok": True, "preview": True, "clipFiles": evidence}

    prepared, _ = prepare_authoritative_unity_write(request, None, preview)

    def unexpected_preview(*args, **kwargs):
        pytest.fail("Checkpoint must not refresh the raw tool preview.")

    monkeypatch.setattr(server, "prepare_unity_mcp_write_request", unexpected_preview)
    outcome = server.prepare_authoritative_unity_checkpoint_sync(
        project, {**prepared, "_checkpointTargetTool": "vrcforge_unity_mcp_write"},
    )
    assert outcome["ok"]
    assert len(preview_calls) == len(calls) == 1
    assert server.build_unity_mcp_write_execution_plan(prepared)[0][1]["expectedClipFiles"] == evidence
