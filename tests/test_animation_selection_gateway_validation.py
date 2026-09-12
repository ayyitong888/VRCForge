from __future__ import annotations

from unittest.mock import patch

import dashboard_server as dashboard


def _assert_rejected(arguments):
    with patch.object(dashboard, "run_unity_artifact_scan_sync") as core:
        result = dashboard.scan_animation_bindings_sync(arguments)
    assert result["ok"] is False
    assert result["failurePhase"] == "tool_input_validation"
    assert result["mutationStarted"] is False
    assert result["toolRoutingStarted"] is False
    assert result["committed"] is False
    assert result["commitState"] == "not_started"
    assert result["details"]["issues"]
    core.assert_not_called()
    return result["details"]["issues"]


def test_selection_validation_rejects_limit_and_offsets_before_core():
    issues = _assert_rejected({"bindingLimit": 512})
    assert {"path": "bindingLimit", "code": "maximum", "expected": "256"} in issues
    _assert_rejected({"bindingLimit": 0})
    _assert_rejected({"bindingLimit": True})
    _assert_rejected({"bindingOffset": -1})
    _assert_rejected({"clipOffset": -1})
    _assert_rejected({"keyOffset": -1})


def test_selection_validation_rejects_view_and_selector_before_core():
    _assert_rejected({"bindingView": "bad"})
    _assert_rejected({"bindingSelectors": [{"path": ""}] * 33})


def test_selection_validation_allows_boundary_and_legacy_clip_aliases():
    with patch.object(dashboard, "run_unity_artifact_scan_sync", return_value={"ok": True}) as core:
        assert dashboard.scan_animation_bindings_sync({"bindingLimit": 256}) == {"ok": True}
        assert dashboard.scan_animation_bindings_sync({"clip_paths": ["Assets/Test.anim"]}) == {"ok": True}
        assert dashboard.scan_animation_bindings_sync({}) == {"ok": True}
    assert core.call_count == 3
    assert core.call_args_list[0].args[3]["bindingLimit"] == 256
    assert core.call_args_list[1].args[3]["clipPaths"] == ["Assets/Test.anim"]
