from pathlib import Path

import pytest

from agent_gateway import AgentGateway


@pytest.fixture
def archive_gateway(tmp_path: Path) -> AgentGateway:
    return AgentGateway(tmp_path / "config.json", tmp_path / "audit")


def make_archives(gateway: AgentGateway, count: int, size: int = 16) -> list[Path]:
    root = gateway.checkpoint_store_dir / "fixture"
    root.mkdir(parents=True, exist_ok=True)
    paths = [root / f"ckpt_{index}.zip" for index in range(count)]
    for path in paths:
        path.write_bytes(b"x" * size)
    return paths


def test_user_can_delete_last_two_archives_and_usage_refreshes(archive_gateway: AgentGateway) -> None:
    service = archive_gateway.checkpoint_recovery
    archives = make_archives(archive_gateway, 2)
    before = service.checkpoint_archive_usage()
    assert all(item["autoCleanupProtected"] for item in before["archives"])
    assert not any(item["protected"] for item in before["archives"])

    deleted = service.delete_checkpoint_archives([path.stem for path in archives])

    assert deleted["deletedCount"] == 2
    assert deleted["protectedSkipped"] == []
    assert deleted["archiveCount"] == 0
    assert deleted["sizeBytes"] == 0
    assert all(not path.exists() for path in archives)
    assert service.checkpoint_archive_usage()["archives"] == []


def test_manual_delete_still_protects_active_recovery(archive_gateway: AgentGateway) -> None:
    service = archive_gateway.checkpoint_recovery
    keep, drop = make_archives(archive_gateway, 2)
    service._append_apply_recovery_entry(
        {"id": "rec_active", "checkpointId": keep.stem, "status": "applying"}
    )
    before = {item["checkpointId"]: item for item in service.checkpoint_archive_usage()["archives"]}
    assert before[keep.stem]["protected"] is True
    assert before[drop.stem]["protected"] is False
    result = service.delete_checkpoint_archives([keep.stem, drop.stem])
    assert result["protectedSkipped"] == [keep.stem]
    assert result["deletedCount"] == 1
    assert keep.exists()
    assert not drop.exists()


def test_empty_selection_does_not_delete(archive_gateway: AgentGateway) -> None:
    archives = make_archives(archive_gateway, 2)
    result = archive_gateway.checkpoint_recovery.delete_checkpoint_archives([])
    assert result["deletedCount"] == 0
    assert all(path.exists() for path in archives)


def test_quota_summary_does_not_read_checkpoint_ledger(archive_gateway: AgentGateway, monkeypatch: pytest.MonkeyPatch) -> None:
    service = archive_gateway.checkpoint_recovery
    make_archives(archive_gateway, 2)
    def forbidden(*args: object, **kwargs: object) -> None:
        raise AssertionError("Quota checks must not load checkpoint history")
    monkeypatch.setattr(type(service), "_read_checkpoint_entries", forbidden)
    usage = service.checkpoint_archive_usage_summary()
    assert usage["archiveCount"] == 2
    assert usage["sizeBytes"] == 32
    assert "archives" not in usage


def test_quota_endpoint_returns_actual_summary(archive_gateway: AgentGateway, monkeypatch: pytest.MonkeyPatch) -> None:
    import dashboard_server
    monkeypatch.setattr(dashboard_server, "AGENT_GATEWAY", archive_gateway)
    make_archives(archive_gateway, 1, 100)
    result = dashboard_server.app_checkpoint_archive_usage()
    assert result["ok"] is True
    assert result["sizeBytes"] == 100
    assert result["archiveCount"] == 1


@pytest.mark.parametrize(
    ("size", "limit", "over_limit", "excess"),
    [(1_048_576, 1, False, 0), (1_048_577, 1, True, 1), (1_048_577, 0, False, 0)],
)
def test_usage_warns_only_for_actual_bytes_over_enabled_limit(
    archive_gateway: AgentGateway, size: int, limit: int, over_limit: bool, excess: int
) -> None:
    config = archive_gateway.ensure_config()
    config.checkpoint_archive_max_size_mb = limit
    make_archives(archive_gateway, 1, size)
    usage = archive_gateway.checkpoint_recovery.checkpoint_archive_usage(config)
    assert usage["overLimit"] is over_limit
    assert usage["overLimitBytes"] == excess
    assert usage["limitEnabled"] is (limit > 0)


def test_auto_cleanup_preserves_latest_two_but_reports_remaining_overage(archive_gateway: AgentGateway) -> None:
    service = archive_gateway.checkpoint_recovery
    config = archive_gateway.ensure_config()
    config.checkpoint_archive_max_size_mb = 1
    archive_gateway.save_config(config)
    archives = make_archives(archive_gateway, 2, 700_000)
    pruned = service.prune_checkpoint_archives()
    assert pruned["deletedCount"] == 0
    assert all(path.exists() for path in archives)
    usage = service.checkpoint_archive_usage()
    assert usage["overLimit"] is True
    assert usage["overLimitBytes"] == 1_400_000 - 1_048_576
    service.delete_checkpoint_archives([archives[0].stem])
    assert service.checkpoint_archive_usage()["overLimit"] is False
