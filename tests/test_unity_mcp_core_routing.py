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
        unity_mcp_command=["unused-external-mcp"],
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

    with pytest.raises(agent.UnityMcpError, match="Failed to call unity-mcp"):
        agent.invoke_unity_mcp(_settings(tmp_path), "vrc_write", {"projectPath": str(tmp_path)})


def test_core_installed_without_descriptor_never_falls_back_to_legacy_write(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    core_root = tmp_path / "Assets" / "VRCForge" / "Core" / "MCP"
    server_marker = tmp_path / "Assets" / "VRCForge" / "Editor" / "MCP" / "VRCForgeMcpCoreServer.cs"
    core_root.mkdir(parents=True)
    server_marker.parent.mkdir(parents=True)
    for name in (
        "VRCForgeCommandAttribute.cs",
        "VRCForgeInputAttribute.cs",
        "VRCForgeToolRegistry.cs",
        "VRCForgeToolResult.cs",
    ):
        (core_root / name).write_text("// marker", encoding="utf-8")
    server_marker.write_text("// marker", encoding="utf-8")

    with pytest.raises(agent.UnityMcpError, match="installed but not ready"):
        agent.invoke_unity_mcp(
            _settings(tmp_path),
            "vrc_write",
            {"projectPath": str(tmp_path)},
        )


def test_cli_status_reads_only_the_project_scoped_core_descriptor(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    observed: dict[str, object] = {}

    class FakeCoreClient:
        def __init__(self, project_root: Path, *, timeout_seconds: int) -> None:
            observed["projectRoot"] = project_root
            observed["timeout"] = timeout_seconds

        def list_tools(self, *, exposure_layer: str = "planning") -> list[dict[str, str]]:
            observed["exposureLayer"] = exposure_layer
            return [{"name": "vrc_read"}]

    monkeypatch.setattr(agent, "UnityMcpCoreClient", FakeCoreClient)
    status = agent.read_unity_mcp_core_status(_settings(tmp_path))

    assert status["protocolVersion"] == "2026-07-28"
    assert status["transport"] == "vrcforge-mcp-core"
    assert status["tools"] == [{"name": "vrc_read"}]
    assert observed == {"projectRoot": tmp_path, "timeout": 45, "exposureLayer": "execution"}


def test_cli_status_without_project_fails_closed() -> None:
    settings = _settings(Path("."))
    settings.unity_project_path = ""

    with pytest.raises(agent.UnityMcpError, match="No Unity project"):
        agent.read_unity_mcp_core_status(settings)


def test_core_rejects_explicit_approved_write_contexts(
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

        def call_tool(self, name: str, arguments: dict, *, execution_context=None) -> dict:
            observed.append((name, arguments, execution_context))
            return {"content": [{"type": "text", "text": "ok"}], "isError": False}

    monkeypatch.setattr(agent, "UnityMcpCoreClient", FakeCoreClient)
    settings = _settings(tmp_path)
    agent.invoke_unity_mcp(settings, "vrc_scan_avatar_items", {"outputPath": ""})

    assert observed[0] == ("vrc_scan_avatar_items", {"outputPath": ""}, None)
    with pytest.raises(agent.UnityMcpError, match="explicit contexts"):
        agent.invoke_unity_mcp(
            settings,
            "vrc_write",
            {"value": 1},
            execution_context={"lane": "approved_write"},
        )
