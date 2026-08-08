from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from pathlib import Path
from threading import Event, Lock, RLock, Thread
from typing import Any, Mapping

import pytest

from provider_configuration_service import (
    ProviderApiConfig,
    ProviderConfigurationPersistencePorts,
    ProviderConfigurationPolicyPorts,
    ProviderConfigurationService,
    ProviderVisionConfig,
)


@dataclass(frozen=True)
class _RuntimeSettings:
    llm_provider: str = "openai"
    llm_api_key: str = ""
    llm_model: str = "default-model"
    gemini_thinking_level: str = ""


@dataclass(frozen=True)
class _ApiRequest:
    provider: str = "openai"
    api_key: str = ""
    base_url: str | None = None
    model: str | None = None
    api_type: str | None = None
    thinking_level: str = ""


@dataclass(frozen=True)
class _VisionRequest:
    provider: str = "openai"
    api_key: str = ""
    base_url: str | None = None
    model: str | None = None
    enabled: bool = True


def _descriptor(config: ProviderApiConfig) -> dict[str, Any]:
    api_type = config.api_type or "chat_completions"
    return {
        "api_type": api_type,
        "apiType": api_type,
        "resolvedApiType": api_type,
        "supportedApiTypes": [api_type],
        "capabilities": ["text"],
        "capabilitySource": "fixed-test-policy",
    }


def _policy() -> ProviderConfigurationPolicyPorts:
    def validate_key(value: str) -> None:
        if any(ord(character) < 0x21 or ord(character) > 0x7E for character in value):
            raise ValueError("API key is invalid. Re-enter the API key.")

    return ProviderConfigurationPolicyPorts(
        default_provider="openai",
        normalize_provider_name=lambda value: str(value or "openai").strip().lower(),
        get_provider_defaults=lambda provider: {
            "base_url": f"https://{provider}.example/v1",
            "model": f"{provider}-default",
        },
        normalize_base_url=lambda value, _provider, default: str(value or default).rstrip("/"),
        normalize_provider_api_type=lambda _provider, _model, value: (
            str(value or "chat_completions"),
            str(value or "chat_completions"),
        ),
        normalize_reasoning_effort=lambda value: str(value or "").strip().lower(),
        reasoning_effort_variants=lambda _provider, _model, _api_type: ["low", "high"],
        validate_provider_api_key=validate_key,
        provider_display_name=lambda provider: provider.title(),
        provider_auth_label=lambda _provider: "Authorization: Bearer",
        provider_requires_api_key=lambda provider: provider != "ollama",
        provider_config_descriptor=_descriptor,
    )


def _write_json(path: Path, payload: Mapping[str, Any]) -> None:
    path.write_text(json.dumps(payload), encoding="utf-8")


def _owner(
    path: Path,
    *,
    atomic_write=_write_json,
    lock: RLock | None = None,
) -> ProviderConfigurationService:
    return ProviderConfigurationService(
        ProviderConfigurationPersistencePorts(
            config_path=path,
            load_runtime_settings=_RuntimeSettings,
            atomic_write_json=atomic_write,
            path_is_reparse_or_link=lambda _path: False,
        ),
        _policy(),
        lock or RLock(),
    )


def _old_state(
    tmp_path: Path,
    *,
    atomic_write=_write_json,
    lock: RLock | None = None,
) -> tuple[ProviderConfigurationService, Path, bytes, ProviderApiConfig, ProviderVisionConfig]:
    config_path = tmp_path / "config.json"
    old_bytes = (
        b'{"api":{"provider":"openai","api_key":"old-api-key","base_url":"https://old.example/v1",'
        b'"model":"old-model"},"vision":{"provider":"openai","api_key":"old-vision-key",'
        b'"base_url":"https://old-vision.example/v1","model":"old-vision-model","enabled":true}}'
    )
    config_path.write_bytes(old_bytes)
    owner = _owner(config_path, atomic_write=atomic_write, lock=lock)
    return (
        owner,
        config_path,
        old_bytes,
        owner.current_api_config(),
        owner.current_vision_config(),
    )


def _new_api() -> ProviderApiConfig:
    return ProviderApiConfig(
        provider="openai",
        api_key="new-api-key",
        base_url="https://new.example/v1",
        model="new-model",
        api_type="chat_completions",
    )


def _new_vision() -> ProviderVisionConfig:
    return ProviderVisionConfig(
        provider="openai",
        api_key="new-vision-key",
        base_url="https://new-vision.example/v1",
        model="new-vision-model",
        enabled=False,
    )


def _invoke(owner: ProviderConfigurationService, entry: str) -> None:
    if entry == "direct_api":
        owner.save_api_config(_new_api())
        return
    if entry == "direct_vision":
        owner.save_vision_config(_new_vision())
        return
    if entry == "route_api":
        owner.save_api_config(
            owner.resolve_api_request(
                _ApiRequest(
                    api_key="new-api-key",
                    base_url="https://new.example/v1",
                    model="new-model",
                )
            )
        )
        return
    if entry == "route_vision":
        owner.save_vision_config(
            owner.resolve_vision_request(
                _VisionRequest(
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
    tmp_path: Path,
    entry: str,
    failure: str,
) -> None:
    atomic_calls: list[bool] = []

    def failed_atomic(*_args, **_kwargs) -> None:
        atomic_calls.append(True)
        raise OSError("simulated atomic provider config failure")

    atomic_write = _write_json if failure == "backup" else failed_atomic
    owner, config_path, old_bytes, old_api, old_vision = _old_state(
        tmp_path,
        atomic_write=atomic_write,
    )
    if failure == "backup":
        digest = hashlib.sha256(old_bytes).hexdigest()
        config_path.with_name(f"{config_path.name}.backup-{digest}.bak").write_bytes(
            b"wrong-backup"
        )

    with pytest.raises(OSError) as caught:
        _invoke(owner, entry)

    assert config_path.read_bytes() == old_bytes
    assert owner.current_api_config() is old_api
    assert owner.current_vision_config() is old_vision
    assert "new-api-key" not in str(caught.value)
    assert "new-vision-key" not in str(caught.value)
    assert atomic_calls == ([] if failure == "backup" else [True])


def test_successful_provider_config_commits_merge_api_and_vision_sections(
    tmp_path: Path,
) -> None:
    owner, config_path, _old_bytes, _old_api, old_vision = _old_state(tmp_path)
    new_api = _new_api()
    new_vision = _new_vision()

    owner.save_api_config(new_api)

    first = json.loads(config_path.read_text(encoding="utf-8"))
    assert owner.current_api_config() is new_api
    assert owner.current_vision_config() is old_vision
    assert first["api"]["api_key"] == "new-api-key"
    assert first["vision"]["api_key"] == "old-vision-key"

    owner.save_vision_config(new_vision)

    second = json.loads(config_path.read_text(encoding="utf-8"))
    assert owner.current_api_config() is new_api
    assert owner.current_vision_config() is new_vision
    assert second["api"]["api_key"] == "new-api-key"
    assert second["vision"]["api_key"] == "new-vision-key"


def test_saved_key_fallback_is_not_published_when_api_route_commit_fails(
    tmp_path: Path,
) -> None:
    def failed_atomic(*_args, **_kwargs) -> None:
        raise OSError("simulated saved-key commit failure")

    owner, config_path, old_bytes, old_api, old_vision = _old_state(
        tmp_path,
        atomic_write=failed_atomic,
    )
    request = _ApiRequest(
        api_key="",
        base_url="https://new.example/v1",
        model="new-model",
    )

    with pytest.raises(OSError):
        owner.save_api_config(owner.resolve_api_request(request))

    assert config_path.read_bytes() == old_bytes
    assert owner.current_api_config() is old_api
    assert owner.current_vision_config() is old_vision


def test_api_and_vision_commits_share_one_lock_and_preserve_both_sections(
    tmp_path: Path,
) -> None:
    first_writer_entered = Event()
    release_first_writer = Event()
    second_writer_entered = Event()
    count_lock = Lock()
    call_count = 0
    failures: list[BaseException] = []

    def blocking_atomic(path: Path, payload: Mapping[str, Any]) -> None:
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
        _write_json(path, payload)

    owner, config_path, _old_bytes, _old_api, _old_vision = _old_state(
        tmp_path,
        atomic_write=blocking_atomic,
        lock=RLock(),
    )

    def run(target) -> None:
        try:
            target()
        except BaseException as exc:  # noqa: BLE001 - surfaced on the test thread.
            failures.append(exc)

    api_thread = Thread(target=run, args=(lambda: owner.save_api_config(_new_api()),))
    vision_thread = Thread(target=run, args=(lambda: owner.save_vision_config(_new_vision()),))
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
    assert owner.current_api_config().api_key == "new-api-key"
    assert owner.current_vision_config().api_key == "new-vision-key"
