from __future__ import annotations

from pathlib import Path

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
    registration = gateway.register_desktop_bridge(
        {
            "name": "fixture",
            "provider": "test",
            "capabilities": ["computer_use"],
        }
    )
    grant = gateway.issue_computer_use_turn_grant({"clientTurnId": "turn-before-rebind"})
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


def test_domain_error_stays_narrow_and_gateway_facade_preserves_http_status(tmp_path: Path) -> None:
    gateway = _enabled_gateway(tmp_path)

    with pytest.raises(DesktopActionBrokerError) as domain_error:
        gateway.desktop.register_desktop_bridge({"capabilities": []})
    assert domain_error.value.status_code == 400

    with pytest.raises(AgentGatewayError) as gateway_error:
        gateway.register_desktop_bridge({"capabilities": []})
    assert gateway_error.value.status_code == 400
    assert not isinstance(gateway_error.value, DesktopActionBrokerError)


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
    assert "STOPGAP(1.5)" in gateway_source
    assert "_impl" not in (root / "desktop_computer_use_service.py").read_text(encoding="utf-8")
