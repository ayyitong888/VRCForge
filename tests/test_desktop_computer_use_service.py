from __future__ import annotations

import json
import os
from pathlib import Path
from unittest.mock import patch

import pytest

from agent_gateway import AgentGateway, AgentGatewayError
from desktop_worker import DesktopActionBrokerError


def _enabled_gateway(root: Path) -> AgentGateway:
    gateway = AgentGateway(root / "config" / "gateway.json", root / "audit")
    config = gateway.ensure_config()
    config.developer_options_enabled = True
    config.computer_use_enabled = True
    gateway.save_config(config)
    return gateway


def test_desktop_owner_holds_all_state_and_embedded_worker(tmp_path: Path) -> None:
    gateway = _enabled_gateway(tmp_path)
    owner = gateway.desktop

    assert owner._lock is gateway._lock  # noqa: SLF001
    assert owner._worker.broker is owner  # noqa: SLF001
    for name in (
        "_desktop_bridges",
        "_desktop_action_payloads",
        "_desktop_action_results",
        "_runtime_computer_use_context",
        "_desktop_action_condition",
        "_computer_use_turn_grants",
    ):
        assert name not in gateway.__dict__
        assert name in owner.__dict__


def test_configure_paths_rebinds_owner_and_clears_transient_authority(tmp_path: Path) -> None:
    gateway = _enabled_gateway(tmp_path)
    owner = gateway.desktop
    registration = gateway.desktop.register_desktop_bridge(
        {
            "name": "fixture",
            "provider": "test",
            "capabilities": ["computer_use"],
        }
    )
    grant = gateway.desktop.issue_computer_use_turn_grant({"clientTurnId": "turn-before-rebind"})
    assert registration["bridgeCredential"]
    assert grant["grantId"]
    assert owner._desktop_bridges  # noqa: SLF001
    assert owner._computer_use_turn_grants  # noqa: SLF001

    next_audit = tmp_path / "rebound" / "audit"
    gateway.configure_paths(tmp_path / "rebound" / "gateway.json", next_audit)

    assert owner.audit_dir == next_audit
    assert owner.capture_dir == next_audit / "desktop-captures"
    assert owner._worker.capture_dir == owner.capture_dir  # noqa: SLF001
    assert owner._desktop_bridges == {}  # noqa: SLF001
    assert owner._desktop_action_payloads == {}  # noqa: SLF001
    assert owner._desktop_action_results == {}  # noqa: SLF001
    assert owner._computer_use_turn_grants == {}  # noqa: SLF001


def test_desktop_action_projection_reuses_cursor_and_invalidates_on_reset(tmp_path: Path) -> None:
    gateway = _enabled_gateway(tmp_path)
    owner = gateway.desktop
    first_event = {
        "event": "desktop_action", "actionId": "action-one", "status": "requested",
        "action": "computer_use", "createdAt": "2026-01-01T00:00:00Z",
    }
    owner._append_desktop_action_event(first_event)  # noqa: SLF001
    first = owner._project_desktop_action_rows(limit_events=0)  # noqa: SLF001
    assert first[0]["status"] == "requested"

    # An unchanged claim poll must not call the generic full-file reader.
    with patch.object(Path, "read_bytes", side_effect=AssertionError("desktop action ledger rescanned")):
        assert owner._project_desktop_action_rows(limit_events=0) == first  # noqa: SLF001

    owner._append_desktop_action_event({  # noqa: SLF001
        "event": "desktop_action_claimed", "actionId": "action-one", "status": "claimed",
        "action": "computer_use", "bridgeId": "bridge-one",
        "createdAt": "2026-01-01T00:00:01Z",
    })
    appended = owner._project_desktop_action_rows(limit_events=0)  # noqa: SLF001
    assert appended[0]["status"] == "claimed"

    owner.desktop_action_log_path.write_bytes(json.dumps(first_event, separators=(",", ":")).encode("utf-8"))
    assert owner._project_desktop_action_rows(limit_events=0) == []  # noqa: SLF001
    with owner.desktop_action_log_path.open("ab") as handle:
        handle.write(
            b"\n" + json.dumps({
                "event": "desktop_action", "actionId": "action-two", "status": "requested",
                "action": "computer_use", "createdAt": "2026-01-01T00:00:02Z",
            }, separators=(",", ":")).encode("utf-8") + b"\n"
        )
    completed_tail = owner._project_desktop_action_rows(limit_events=0)  # noqa: SLF001
    assert [row["id"] for row in completed_tail] == ["action-one", "action-two"]

    same_size_rewrite = dict(first_event, status="cancelled")
    original_bytes = (json.dumps(first_event, separators=(",", ":")) + "\n").encode("utf-8")
    rewritten_bytes = (json.dumps(same_size_rewrite, separators=(",", ":")) + "\n").encode("utf-8")
    assert len(original_bytes) == len(rewritten_bytes)
    owner.desktop_action_log_path.write_bytes(rewritten_bytes)
    rewritten = owner._project_desktop_action_rows(limit_events=0)  # noqa: SLF001
    assert rewritten[0]["status"] == "cancelled"

    owner.desktop_action_log_path.write_text(
        json.dumps({"actionId": "action-one", "status": "requested"}) + "\n",
        encoding="utf-8",
    )
    truncated = owner._project_desktop_action_rows(limit_events=0)  # noqa: SLF001
    assert truncated[0]["status"] == "requested"

    replacement = owner.desktop_action_log_path.with_suffix(".replacement")
    replacement.write_text(json.dumps({
        "event": "desktop_action", "actionId": "action-two", "status": "requested",
        "action": "computer_use", "createdAt": "2026-01-01T00:00:02Z",
    }) + "\n", encoding="utf-8")
    os.replace(replacement, owner.desktop_action_log_path)
    replaced = owner._project_desktop_action_rows(limit_events=0)  # noqa: SLF001
    assert [row["id"] for row in replaced] == ["action-two"]


def test_composed_owner_error_preserves_gateway_and_worker_status_contract(tmp_path: Path) -> None:
    gateway = _enabled_gateway(tmp_path)

    with pytest.raises(AgentGatewayError) as domain_error:
        gateway.desktop.register_desktop_bridge({"capabilities": []})
    assert domain_error.value.status_code == 400
    assert isinstance(domain_error.value, DesktopActionBrokerError)


def test_stop_blocked_keeps_bridge_authority_until_threads_are_owned_down(
    tmp_path: Path,
) -> None:
    gateway = _enabled_gateway(tmp_path)
    worker = gateway.desktop._worker  # noqa: SLF001

    class StuckThread:
        name = "vrcforge-desktop-worker"

        @staticmethod
        def is_alive() -> bool:
            return True

        @staticmethod
        def join(timeout: float) -> None:
            assert timeout >= 0.1

    worker._worker_thread = StuckThread()  # type: ignore[assignment]  # noqa: SLF001
    worker._bridge_id = "bridge-still-owned"  # noqa: SLF001
    worker._bridge_credential = "credential-still-owned"  # noqa: SLF001

    result = gateway.desktop.stop_embedded_worker()

    assert result["stopBlocked"] is True
    assert result["aliveThreads"] == ["vrcforge-desktop-worker"]
    assert worker._bridge_id == "bridge-still-owned"  # noqa: SLF001
    assert worker._bridge_credential == "credential-still-owned"  # noqa: SLF001


def test_root_modules_expose_no_second_desktop_owner() -> None:
    root = Path(__file__).resolve().parents[1]
    dashboard_source = (root / "dashboard_server.py").read_text(encoding="utf-8")
    worker_source = (root / "desktop_worker.py").read_text(encoding="utf-8")
    gateway_source = (root / "agent_gateway.py").read_text(encoding="utf-8")

    assert "DESKTOP_EXECUTOR" not in dashboard_source
    assert "DESKTOP_CAPTURE_DIR" not in dashboard_source
    assert "from agent_gateway import" not in worker_source
    assert "STOPGAP(1.5)" not in gateway_source
    for facade in (
        "_desktop_gateway_call",
        "computer_use_turn_active",
        "require_computer_use_enabled",
        "request_desktop_action",
        "list_desktop_actions",
        "register_desktop_bridge",
        "complete_desktop_action",
    ):
        assert f"def {facade}(" not in gateway_source
    assert "_impl" not in (root / "desktop_computer_use_service.py").read_text(encoding="utf-8")
