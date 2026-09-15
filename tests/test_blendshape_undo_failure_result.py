"""Returned Core failures must not consume the real service's undo stack."""
import pytest

from test_avatar_tuning_state_service import _prepared_service


@pytest.mark.parametrize("failed", [
    {"ok": False, "error": "Target renderer disappeared"},
    {"payload": {"isError": True, "structuredContent": {
        "success": False, "message": "Target renderer disappeared"}}},
])
def test_returned_failure_preserves_undo_and_allows_retry(tmp_path, failed):
    service, _, undo, live = _prepared_service(tmp_path)
    items = [{"rendererPath": "Face", "blendshapeName": "Smile", "targetWeight": 10.0}]
    undo.push("Avatar/Path", items)
    prepared, _ = service.prepare_manual_undo({"avatar_path": "Avatar/Path"}, None)
    live["unity_result"] = failed
    with pytest.raises(RuntimeError, match="Target renderer disappeared"):
        service.execute_manual_undo(prepared)
    assert undo.depth("Avatar/Path") == 1
    assert undo.capture("Avatar/Path")[0] == items

    live["unity_result"] = {"status": "ok"}
    result = service.execute_manual_undo(prepared)
    assert result["ok"] is True
    assert undo.depth("Avatar/Path") == 0
