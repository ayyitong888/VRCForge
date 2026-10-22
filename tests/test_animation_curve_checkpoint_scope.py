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


def _batch_preview(rows, **overrides):
    return {
        "ok": True,
        "preview": True,
        "batch": True,
        "clips": [
            {"clipPath": path, "assetGuid": guid, "fileDigest": "d" * 64, "curveCount": 1}
            for path, guid in rows
        ],
        **overrides,
    }


def test_actual_three_clip_preview_binds_every_asset_path(curve_checkpoint_server, monkeypatch):
    server, project, calls = curve_checkpoint_server
    clips = [(f"Assets/Outfits/Look_{i}.anim", f"{i:032x}") for i in range(1, 4)]
    monkeypatch.setattr(server, "write_animation_curve_sync", lambda *a, **k: _batch_preview(clips))

    result = server.prepare_authoritative_unity_checkpoint_sync(
        project,
        {"_checkpointTargetTool": "vrcforge_write_animation_curve",
         "clips": [{"clipPath": path} for path, _ in clips]},
    )

    assert result["ok"] is True, result
    assert set(result["assetFootprint"]["archiveAbsentFiles"]) == {
        f"{path}{suffix}" for path, _ in clips for suffix in ("", ".meta")
    } | {"Assets/Outfits.meta"}
    assert result["assetFootprint"]["archiveAbsentDirectories"] == ["Assets/Outfits"]
    assert len(calls) == 1
    assert set(calls[0][1]) == {f"{path}{suffix}" for path, _ in clips for suffix in ("", ".meta")} | {"Assets/Outfits.meta"}


def test_single_clip_plan_keeps_existing_checkpoint_semantics(curve_checkpoint_server, monkeypatch):
    server, project, calls = curve_checkpoint_server
    monkeypatch.setattr(server, "write_animation_curve_sync", lambda *a, **k: {
        "ok": True, "preview": True,
        "plan": {"action": "set_curve", "clipPath": "Assets/Existing.anim"},
    })
    result = server.prepare_authoritative_unity_checkpoint_sync(
        project, {"_checkpointTargetTool": "vrcforge_write_animation_curve", "clipPath": "Assets/Existing.anim"},
    )
    assert result["ok"] is True, result
    assert set(calls[0][1]) == {"Assets/Existing.anim", "Assets/Existing.anim.meta"}


def test_single_clip_multi_curve_preview_uses_its_exact_clip_path(curve_checkpoint_server, monkeypatch):
    server, project, calls = curve_checkpoint_server
    monkeypatch.setattr(server, "write_animation_curve_sync", lambda *a, **k: {
        "ok": True, "preview": True, "batch": True, "action": "set_curve",
        "clipPath": "Assets/New.anim", "curveCount": 2,
        "plan": [{"propertyName": "enabled"}, {"propertyName": "weight"}],
    })
    result = server.prepare_authoritative_unity_checkpoint_sync(
        project, {"_checkpointTargetTool": "vrcforge_write_animation_curve",
                  "clipPath": "Assets/New.anim", "curves": [{}, {}]},
    )
    assert result["ok"] is True, result
    assert set(result["assetFootprint"]["archiveAbsentFiles"]) == {
        "Assets/New.anim", "Assets/New.anim.meta"
    }
    assert set(calls[0][1]) == {"Assets/New.anim", "Assets/New.anim.meta"}


@pytest.mark.parametrize("preview", [
    {"ok": False, "preview": True, "batch": True, "clips": []},
    _batch_preview([]),
    _batch_preview([("Assets/A.anim", "a" * 32), ("Assets/A.anim", "b" * 32)]),
    _batch_preview([("Assets/A.anim", "a" * 32), ("Assets/B.anim", "a" * 32)]),
    _batch_preview([("Assets/../outside.anim", "a" * 32)]),
    {"ok": True, "preview": True, "batch": True,
     "clips": [{"clipPath": "Assets/A.anim", "assetGuid": "a" * 32, "curveCount": 1}]},
])
def test_invalid_or_unproven_batch_preview_is_rejected_before_checkpoint(
    curve_checkpoint_server, monkeypatch, preview
):
    server, project, calls = curve_checkpoint_server
    monkeypatch.setattr(server, "write_animation_curve_sync", lambda *a, **k: preview)
    result = server.prepare_authoritative_unity_checkpoint_sync(
        project, {"_checkpointTargetTool": "vrcforge_write_animation_curve", "clips": [{}]},
    )
    assert result["ok"] is False and result["blocking"] is True
    assert result["code"] == "checkpoint_scope_unresolved"
    assert result["mutationStarted"] is False and result["saved"] is False
    assert calls == []
