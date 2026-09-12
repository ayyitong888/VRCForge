from dataclasses import replace
from pathlib import Path
from unittest.mock import Mock

import pytest
import dashboard_server
from unity_mcp_core_client import UnityMcpCoreConnection


@pytest.mark.parametrize("same_project", [True, False])
def test_reload_uses_real_core_connection_project_identity(monkeypatch, same_project):
    project = Path("D:/Acceptance/ExistingProject")
    previous = UnityMcpCoreConnection(
        project_root=project, host="127.0.0.1", port=1,
        discovery_token="test-only", instance_id="old", process_id=77,
        project_id="same-project", supported_protocol_versions=("2026-07-28",),
        minimum_protocol_version="2026-07-28", maximum_protocol_version="2026-07-28",
        negotiated_protocol_version="2026-07-28", transport="test",
        tool_count=91, core_identity="vrcforge.unity-core", handshake_protocol="test",
        product_version="1.8.0", tool_contract_version="89",
    )
    current = replace(previous, instance_id="new", project_id="same-project" if same_project else "another-project")
    monkeypatch.setattr(dashboard_server, "load_unity_mcp_core_connection", lambda _project: current)
    client = Mock()
    client.core_info.return_value = {"coreIdentity": "vrcforge.unity-core", "coreVersion": "1.8.0"}
    client.call_tool.return_value = {"resultType": "complete", "structuredContent": {"success": True}}
    constructor = Mock(return_value=client)
    monkeypatch.setattr(dashboard_server, "UnityMcpCoreClient", constructor)
    ticks = iter([0.0, 0.0, 0.1, 1.1])
    monkeypatch.setattr(dashboard_server.time, "monotonic", lambda: next(ticks))
    monkeypatch.setattr(dashboard_server.time, "sleep", lambda _seconds: None)
    result = dashboard_server._wait_for_reloaded_unity_core(project, previous, timeout_seconds=1.0, poll_seconds=0.01)
    assert result["ok"] is same_project
    if same_project:
        assert result["domainReloadObserved"] is True
        assert result["mainThreadReadVerified"] is True
        client.call_tool.assert_called_once_with("vrc_get_compile_errors", {"maxErrors": 1})
    else:
        constructor.assert_not_called()
