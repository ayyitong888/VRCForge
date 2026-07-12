from __future__ import annotations

import json
import sys
from pathlib import Path
from unittest.mock import patch

from fastapi.testclient import TestClient

import dashboard_server
from external_agent_connector_installer import install_connector


def _make_source_root(tmp_path: Path) -> Path:
    root = tmp_path / "VRCForge"
    tools = root / "tools"
    tools.mkdir(parents=True)
    (tools / "vrcforge_agent_mcp_stdio.py").write_text("# test bridge placeholder\n", encoding="utf-8")
    return root


def test_generic_connector_status_query_is_scoped_to_config_path(tmp_path: Path) -> None:
    root = _make_source_root(tmp_path)
    managed = tmp_path / "managed.json"
    conflicting = tmp_path / "conflicting.json"
    install_connector("generic", root_dir=root, config_path=str(managed), run_self_test=False)
    conflicting.write_text(
        json.dumps({"mcpServers": {"vrcforge": {"command": "user-owned"}}}),
        encoding="utf-8",
    )

    with patch("dashboard_server.ROOT_DIR", root), TestClient(dashboard_server.app) as client:
        managed_response = client.get(
            "/api/app/external-agent/connectors",
            params={"configPath": str(managed)},
        )
        conflict_response = client.get(
            "/api/app/external-agent/connectors",
            params={"configPath": str(conflicting)},
        )

    assert managed_response.status_code == 200
    assert managed_response.json()["clients"]["generic"]["installed"] is True
    assert managed_response.json()["clients"]["generic"]["configPath"] == str(managed.resolve())
    assert managed_response.json()["clients"]["generic"]["bridge"]["command"] == sys.executable
    assert conflict_response.status_code == 200
    assert conflict_response.json()["clients"]["generic"]["installed"] is False
    assert conflict_response.json()["clients"]["generic"]["conflict"] is True
