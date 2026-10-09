from types import SimpleNamespace

import pytest

import unity_status_service
from tests.test_unity_status_service import make_service
from unity_mcp_core_client import UnityMcpCoreError
from unity_mcp_tool_contract import PRODUCT_VERSION


@pytest.mark.parametrize(("version", "relation", "action"), [
    ("0.0.1", "core_older", "update_core"),
    ("999.0.0", "app_older", "update_app"),
    ("invalid", "unknown", "reinstall_pair"),
    ("", "unknown", "reinstall_pair"),
])
def test_version_mismatch_retains_actionable_versions_without_enabling_tools(
    monkeypatch, tmp_path, version, relation, action,
):
    class MismatchedCore:
        def __init__(self, *_args, **_kwargs):
            pass

        def core_info(self):
            error = UnityMcpCoreError("Core version mismatch")
            error.cause_code = "unity_core_version_mismatch"
            error.details = {"kind": "core_version_mismatch", "expectedVersion": PRODUCT_VERSION,
                             "actualVersion": version, "private": "not-a-display-field"}
            raise error

    monkeypatch.setattr(unity_status_service, "UnityMcpCoreClient", MismatchedCore)
    status = make_service().build_unity_status_snapshot(
        SimpleNamespace(unity_mcp_timeout_seconds=5), tmp_path,
    )
    assert status["executionReady"] is False
    assert status["readiness"]["ready"] is False
    notice = status["readiness"]["versionMismatch"]
    assert notice == {"appVersion": PRODUCT_VERSION, "coreVersion": version,
                      "relation": relation, "action": action}
    assert status["coreVersion"] == version


def test_unrelated_connection_error_does_not_become_a_version_mismatch(monkeypatch, tmp_path):
    class OfflineCore:
        def __init__(self, *_args, **_kwargs):
            raise UnityMcpCoreError("offline")

    monkeypatch.setattr(unity_status_service, "UnityMcpCoreClient", OfflineCore)
    status = make_service().build_unity_status_snapshot(
        SimpleNamespace(unity_mcp_timeout_seconds=5), tmp_path,
    )
    assert "versionMismatch" not in status["readiness"]
    assert status["readiness"]["ready"] is False
