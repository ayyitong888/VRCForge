from pathlib import Path

import dashboard_server


def test_incomplete_directory_never_claims_complete(tmp_path: Path):
    for index in range(3):
        (tmp_path / f"item-{index}.txt").write_text("fixture", encoding="utf-8")
    result = dashboard_server.AGENT_GATEWAY._tools["vrcforge_list_directory"].handler(
        {"path": str(tmp_path), "maxCount": 1, "_generalAllowedRoots": [str(tmp_path)]}
    )
    assert result["truncated"] is True
    assert "listing is complete" not in result["notice"]
    assert "incomplete" in result["notice"]
    assert "Do not repeat" not in result["notice"]
