from pathlib import Path

import pytest

from unity_mcp_core_auth import UnityMcpCoreError, load_core_diagnostic_binding, load_unity_mcp_core_connection


@pytest.mark.parametrize("loader", [load_core_diagnostic_binding, load_unity_mcp_core_connection])
def test_missing_descriptor_is_unavailable_not_invalid(tmp_path, loader):
    with pytest.raises(UnityMcpCoreError) as error:
        loader(tmp_path)
    assert error.value.cause_code == "unity_core_unavailable"
    assert error.value.retryable is True


@pytest.mark.parametrize("content", ["not-json", "[]", "{}"])
def test_invalid_descriptor_is_not_a_startup_failure(tmp_path, content):
    descriptor = tmp_path / "Library" / "VRCForge" / "mcp-core.json"
    descriptor.parent.mkdir(parents=True)
    descriptor.write_text(content, encoding="utf-8")
    with pytest.raises(UnityMcpCoreError) as error:
        load_unity_mcp_core_connection(tmp_path)
    assert error.value.cause_code == "unity_core_contract_invalid"
    assert error.value.retryable is False


def test_health_does_not_claim_connection_without_evidence():
    source = (Path(__file__).parents[1] / "dashboard_server.py").read_text(encoding="utf-8")
    assert 'else "Unity MCP is connected, but VRCForge Unity tools are not ready."' not in source
    assert 'else "VRCForge Unity tools are not ready."' in source
