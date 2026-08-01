from __future__ import annotations

from pathlib import Path

import pytest

import vrchat_blendshape_agent as agent


def _settings(project: Path) -> agent.Settings:
    return agent.Settings(
        llm_provider="openai",
        llm_api_key="",
        llm_base_url="https://example.invalid/v1",
        llm_model="test",
        llm_api_key_env="",
        gemini_thinking_level="",
        unity_mcp_command=["legacy-unity-mcp"],
        unity_mcp_host="127.0.0.1",
        unity_mcp_port=8080,
        unity_mcp_instance="",
        unity_mcp_retries=1,
        unity_mcp_retry_backoff_seconds=0,
        unity_mcp_timeout_seconds=45,
        export_tool_name="vrc_read",
        execute_tool_name="vrc_write",
        export_path=project / "export.json",
        min_confidence=0.5,
        unity_project_path=str(project),
    )


def test_invoke_routes_to_project_bound_core_when_descriptor_exists(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    descriptor = tmp_path / "Library" / "VRCForge" / "mcp-core.json"
    descriptor.parent.mkdir(parents=True)
    descriptor.write_text("{}", encoding="utf-8")
    observed: dict[str, object] = {}

    class FakeCoreClient:
        def __init__(self, project_root: str, *, timeout_seconds: int) -> None:
            observed["projectRoot"] = project_root
            observed["timeout"] = timeout_seconds

        def call_tool(self, name: str, arguments: dict) -> dict:
            observed["name"] = name
            observed["arguments"] = arguments
            return {"content": [{"type": "text", "text": "ok"}], "isError": False}

    monkeypatch.setattr(agent, "UnityMcpCoreClient", FakeCoreClient)
    monkeypatch.setattr(agent, "run_unity_mcp_process", lambda *_args, **_kwargs: pytest.fail("legacy transport used"))

    result = agent.invoke_unity_mcp(_settings(tmp_path), "vrc_read", {"projectPath": str(tmp_path), "value": "汉字"})

    assert result.exit_code == 0
    assert result.payload["isError"] is False
    assert observed == {
        "projectRoot": str(tmp_path),
        "timeout": 45,
        "name": "vrc_read",
        "arguments": {"projectPath": str(tmp_path), "value": "汉字"},
    }


def test_core_tool_error_never_falls_back_to_legacy_transport(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    descriptor = tmp_path / "Library" / "VRCForge" / "mcp-core.json"
    descriptor.parent.mkdir(parents=True)
    descriptor.write_text("{}", encoding="utf-8")

    class FakeCoreClient:
        def __init__(self, _project_root: str, *, timeout_seconds: int) -> None:
            assert timeout_seconds == 45

        def call_tool(self, _name: str, _arguments: dict) -> dict:
            return {"content": [{"type": "text", "text": "rejected"}], "isError": True}

    monkeypatch.setattr(agent, "UnityMcpCoreClient", FakeCoreClient)
    monkeypatch.setattr(agent, "run_unity_mcp_process", lambda *_args, **_kwargs: pytest.fail("legacy transport used"))

    with pytest.raises(agent.UnityMcpError, match="Failed to call unity-mcp"):
        agent.invoke_unity_mcp(_settings(tmp_path), "vrc_write", {"projectPath": str(tmp_path)})


def test_core_installed_without_descriptor_never_falls_back_to_legacy_write(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    core_marker = tmp_path / "Assets" / "VRCForge" / "Core" / "MCP" / "VRCForgeToolAttribute.cs"
    server_marker = tmp_path / "Assets" / "VRCForge" / "Editor" / "MCP" / "VRCForgeMcpCoreServer.cs"
    core_marker.parent.mkdir(parents=True)
    server_marker.parent.mkdir(parents=True)
    core_marker.write_text("// marker", encoding="utf-8")
    server_marker.write_text("// marker", encoding="utf-8")
    monkeypatch.setattr(
        agent,
        "run_unity_mcp_process",
        lambda *_args, **_kwargs: pytest.fail("legacy transport used"),
    )

    with pytest.raises(agent.UnityMcpError, match="legacy connector fallback is disabled"):
        agent.invoke_unity_mcp(
            _settings(tmp_path),
            "vrc_write",
            {"projectPath": str(tmp_path)},
        )


def test_core_calls_never_receive_an_out_of_band_execution_context(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    descriptor = tmp_path / "Library" / "VRCForge" / "mcp-core.json"
    descriptor.parent.mkdir(parents=True)
    descriptor.write_text("{}", encoding="utf-8")
    observed = []

    class FakeCoreClient:
        def __init__(self, _project_root: str, *, timeout_seconds: int) -> None:
            assert timeout_seconds == 45

        def call_tool(self, name: str, arguments: dict) -> dict:
            observed.append((name, arguments))
            return {"content": [{"type": "text", "text": "ok"}], "isError": False}

    monkeypatch.setattr(agent, "UnityMcpCoreClient", FakeCoreClient)
    settings = _settings(tmp_path)
    agent.invoke_unity_mcp(settings, "vrc_scan_avatar_items", {"outputPath": ""})
    agent.invoke_unity_mcp(settings, "vrc_write", {"value": 1})

    assert observed == [
        ("vrc_scan_avatar_items", {"outputPath": ""}),
        ("vrc_write", {"value": 1}),
    ]
