from pathlib import Path

from agent_gateway import AgentGateway


def test_browser_action_without_executor_is_not_reported_as_executed(tmp_path: Path) -> None:
    gateway = AgentGateway(tmp_path / "config.json", tmp_path / "audit")
    result = gateway.desktop.request_desktop_action_and_wait({
        "action": "browser",
        "sessionId": "browser-reading-session",
        "params": {"url": "https://example.test/manual"},
    })

    assert result["ok"] is False
    assert result["status"] == "failed"
    assert result["actionId"] == ""
    assert result["result"] == {}
    assert "browser executor" in result["error"]
    assert "computer_use" in result["error"]
    assert result["event"]["status"] == "failed"


def test_annotation_remains_a_record_only_action(tmp_path: Path) -> None:
    gateway = AgentGateway(tmp_path / "config.json", tmp_path / "audit")
    result = gateway.desktop.request_desktop_action_and_wait({
        "action": "annotation",
        "params": {"text": "Observed the manual link."},
    })

    assert result["ok"] is True
    assert result["status"] == "recorded"
    assert result["actionId"] == ""
    assert result["error"] == ""
