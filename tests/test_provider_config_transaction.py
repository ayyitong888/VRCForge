from __future__ import annotations

import asyncio
import hashlib
import json
from pathlib import Path
from threading import Event, Lock, Thread

import pytest

import dashboard_server


def _old_state(monkeypatch: pytest.MonkeyPatch, tmp_path: Path):
    config_path = tmp_path / "config.json"
    old_bytes = (
        b'{"api":{"provider":"openai","api_key":"old-api-key","base_url":"https://old.example/v1",'
        b'"model":"old-model"},"vision":{"provider":"openai","api_key":"old-vision-key",'
        b'"base_url":"https://old-vision.example/v1","model":"old-vision-model","enabled":true}}'
    )
    config_path.write_bytes(old_bytes)
    old_api = dashboard_server.DashboardApiConfig(
        provider="openai",
        api_key="old-api-key",
        base_url="https://old.example/v1",
        model="old-model",
    )
    old_vision = dashboard_server.DashboardVisionConfig(
        provider="openai",
        api_key="old-vision-key",
        base_url="https://old-vision.example/v1",
        model="old-vision-model",
        enabled=True,
    )
    monkeypatch.setattr(dashboard_server, "CONFIG_PATH", config_path)
    monkeypatch.setattr(dashboard_server, "DASHBOARD_API_CONFIG", old_api)
    monkeypatch.setattr(dashboard_server, "DASHBOARD_VISION_CONFIG", old_vision)
    return config_path, old_bytes, old_api, old_vision


def _new_api() -> dashboard_server.DashboardApiConfig:
    return dashboard_server.DashboardApiConfig(
        provider="openai",
        api_key="new-api-key",
        base_url="https://new.example/v1",
        model="new-model",
    )


def _new_vision() -> dashboard_server.DashboardVisionConfig:
    return dashboard_server.DashboardVisionConfig(
        provider="openai",
        api_key="new-vision-key",
        base_url="https://new-vision.example/v1",
        model="new-vision-model",
        enabled=False,
    )


def _invoke(entry: str) -> None:
    if entry == "direct_api":
        dashboard_server.save_dashboard_api_config(_new_api())
        return
    if entry == "direct_vision":
        dashboard_server.save_dashboard_vision_config(_new_vision())
        return
    if entry == "route_api":
        asyncio.run(
            dashboard_server.update_api_config(
                dashboard_server.ApiConfigRequest(
                    provider="openai",
                    api_key="new-api-key",
                    base_url="https://new.example/v1",
                    model="new-model",
                )
            )
        )
        return
    if entry == "route_vision":
        asyncio.run(
            dashboard_server.update_vision_config(
                dashboard_server.VisionConfigRequest(
                    provider="openai",
                    api_key="new-vision-key",
                    base_url="https://new-vision.example/v1",
                    model="new-vision-model",
                    enabled=False,
                )
            )
        )
        return
    raise AssertionError(f"Unknown entry: {entry}")


@pytest.mark.parametrize("entry", ["direct_api", "direct_vision", "route_api", "route_vision"])
@pytest.mark.parametrize("failure", ["backup", "atomic"])
def test_failed_provider_config_commit_preserves_memory_and_disk(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    entry: str,
    failure: str,
) -> None:
    config_path, old_bytes, old_api, old_vision = _old_state(monkeypatch, tmp_path)
    atomic_calls: list[bool] = []
    if failure == "backup":
        digest = hashlib.sha256(old_bytes).hexdigest()
        config_path.with_name(f"{config_path.name}.backup-{digest}.bak").write_bytes(b"wrong-backup")

        def unexpected_atomic(*_args, **_kwargs) -> None:
            atomic_calls.append(True)

        monkeypatch.setattr(dashboard_server, "atomic_write_json", unexpected_atomic)
    else:

        def failed_atomic(*_args, **_kwargs) -> None:
            atomic_calls.append(True)
            raise OSError("simulated atomic provider config failure")

        monkeypatch.setattr(dashboard_server, "atomic_write_json", failed_atomic)

    with pytest.raises(OSError) as caught:
        _invoke(entry)

    assert config_path.read_bytes() == old_bytes
    assert dashboard_server.DASHBOARD_API_CONFIG is old_api
    assert dashboard_server.DASHBOARD_VISION_CONFIG is old_vision
    assert "new-api-key" not in str(caught.value)
    assert "new-vision-key" not in str(caught.value)
    assert atomic_calls == ([] if failure == "backup" else [True])


def test_successful_provider_config_commits_merge_api_and_vision_sections(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    config_path, _old_bytes, _old_api, old_vision = _old_state(monkeypatch, tmp_path)
    new_api = _new_api()
    new_vision = _new_vision()

    dashboard_server.save_dashboard_api_config(new_api)

    first = json.loads(config_path.read_text(encoding="utf-8"))
    assert dashboard_server.DASHBOARD_API_CONFIG is new_api
    assert dashboard_server.DASHBOARD_VISION_CONFIG is old_vision
    assert first["api"]["api_key"] == "new-api-key"
    assert first["vision"]["api_key"] == "old-vision-key"

    dashboard_server.save_dashboard_vision_config(new_vision)

    second = json.loads(config_path.read_text(encoding="utf-8"))
    assert dashboard_server.DASHBOARD_API_CONFIG is new_api
    assert dashboard_server.DASHBOARD_VISION_CONFIG is new_vision
    assert second["api"]["api_key"] == "new-api-key"
    assert second["vision"]["api_key"] == "new-vision-key"


def test_saved_key_fallback_is_not_published_when_api_route_commit_fails(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    config_path, old_bytes, old_api, old_vision = _old_state(monkeypatch, tmp_path)

    def failed_atomic(*_args, **_kwargs) -> None:
        raise OSError("simulated saved-key commit failure")

    monkeypatch.setattr(dashboard_server, "atomic_write_json", failed_atomic)
    request = dashboard_server.ApiConfigRequest(
        provider="openai",
        api_key="",
        base_url="https://new.example/v1",
        model="new-model",
    )

    with pytest.raises(OSError):
        asyncio.run(dashboard_server.update_api_config(request))

    assert config_path.read_bytes() == old_bytes
    assert dashboard_server.DASHBOARD_API_CONFIG is old_api
    assert dashboard_server.DASHBOARD_VISION_CONFIG is old_vision


def test_api_and_vision_commits_share_one_lock_and_preserve_both_sections(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    config_path, _old_bytes, _old_api, _old_vision = _old_state(monkeypatch, tmp_path)
    real_atomic_write = dashboard_server.atomic_write_json
    first_writer_entered = Event()
    release_first_writer = Event()
    second_writer_entered = Event()
    count_lock = Lock()
    call_count = 0
    failures: list[BaseException] = []

    def blocking_atomic(path: Path, payload: dict[str, object]) -> None:
        nonlocal call_count
        with count_lock:
            call_count += 1
            call_number = call_count
        if call_number == 1:
            first_writer_entered.set()
            if not release_first_writer.wait(5):
                raise TimeoutError("provider config concurrency fixture timed out")
        else:
            second_writer_entered.set()
        real_atomic_write(path, payload)

    def run(target) -> None:
        try:
            target()
        except BaseException as exc:  # noqa: BLE001 - surfaced on the test thread.
            failures.append(exc)

    monkeypatch.setattr(dashboard_server, "atomic_write_json", blocking_atomic)
    api_thread = Thread(target=run, args=(lambda: dashboard_server.save_dashboard_api_config(_new_api()),))
    vision_thread = Thread(target=run, args=(lambda: dashboard_server.save_dashboard_vision_config(_new_vision()),))
    api_thread.start()
    assert first_writer_entered.wait(5)
    vision_thread.start()
    assert not second_writer_entered.wait(0.2)
    release_first_writer.set()
    api_thread.join(5)
    vision_thread.join(5)

    assert not api_thread.is_alive()
    assert not vision_thread.is_alive()
    assert failures == []
    assert second_writer_entered.is_set()
    payload = json.loads(config_path.read_text(encoding="utf-8"))
    assert payload["api"]["api_key"] == "new-api-key"
    assert payload["vision"]["api_key"] == "new-vision-key"
    assert dashboard_server.DASHBOARD_API_CONFIG.api_key == "new-api-key"
    assert dashboard_server.DASHBOARD_VISION_CONFIG.api_key == "new-vision-key"
