import uuid

import pytest

import agent_scan_scope_client as scope
from agent_scan_result_store import ScanResultStore


@pytest.mark.parametrize("tool,args", [
    ("vrcforge_scan_wardrobe", {"avatarPath": "random-avatar"}),
    ("vrcforge_scan_avatar_items", {"source_avatar_path": "random-avatar"}),
    ("vrcforge_scan_fx_animator", {"avatar_path": "random-avatar"}),
    ("vrcforge_scan_avatar_controls", {"avatar_path": "random-avatar"}),
    ("vrcforge_scan_animation_bindings", {"avatarPath": "random-avatar", "clipPaths": ["Assets/random.anim"]}),
    ("vrcforge_scan_animation_bindings", {"includeAllProjectClips": True, "clipPaths": ["Assets/random.anim"]}),
])
def test_unbounded_first_capture_never_dispatches_core_work(monkeypatch, tmp_path, tool, args):
    args = {key: (["Assets/" + uuid.uuid4().hex + ".anim"] if isinstance(value, list)
                  else "Avatar-" + uuid.uuid4().hex if isinstance(value, str) else value)
            for key, value in args.items()}
    def forbidden(*unused, **kwargs):
        pytest.fail("Unbounded capture must be skipped before constructing a Core client")
    monkeypatch.setattr(scope, "UnityMcpCoreClient", forbidden)
    for _ in range(2):
        result = ScanResultStore(tmp_path / "store").start(tmp_path, tool, args)
        assert result["proof"]["verified"] is False
        assert result["proof"]["reason"] == "scope_capture_unbounded"
        assert "token" not in result["proof"]


def test_explicit_asset_capture_and_existing_validation_keep_core_fast_path(monkeypatch, tmp_path):
    asset = tmp_path / "Assets" / (uuid.uuid4().hex + ".controller")
    asset.parent.mkdir()
    asset.write_text("temporary fixture", encoding="utf-8")
    asset.with_name(asset.name + ".meta").write_text("guid: " + uuid.uuid4().hex, encoding="utf-8")
    path = asset.relative_to(tmp_path).as_posix()
    calls = []
    editor = str(uuid.uuid4())
    class Core:
        def __init__(self, *unused, **kwargs):
            pass
        def core_info(self):
            return {"instanceId": uuid.uuid4().hex, "scanScope": {
                "schema": scope.SCAN_SCOPE_SCHEMA, "implementation": "scan-scope-v1",
                "tools": list(scope.SCAN_SCOPE_TOOLS)}}
        def call_tool(self, name, arguments):
            calls.append((name, arguments))
            data = {"schema": scope.SCAN_SCOPE_SCHEMA, "editorSession": editor,
                    "valid": True, "reason": "", "changedAssetPaths": []}
            if name == scope.CAPTURE_SCAN_SCOPE_TOOL:
                data.update(verified=True, token="scope_" + uuid.uuid4().hex, assetPaths=[path],
                            capturedAt="2026-10-07T00:00:00Z", dependencyCoverage="complete")
            return {"structuredContent": {"success": True, "data": data}}
    monkeypatch.setattr(scope, "UnityMcpCoreClient", Core)
    store = ScanResultStore(tmp_path / "store")
    result = store.start(tmp_path, "vrcforge_scan_fx_animator", {"controller_path": path})
    assert result["proof"]["verified"] is True
    client = scope.AgentScanScopeClient(tmp_path)
    client.capture_budget = store._capture_budget
    assert client.validate(result["proof"]["token"])["valid"] is True
    assert [name for name, _ in calls] == list(scope.SCAN_SCOPE_TOOLS)
