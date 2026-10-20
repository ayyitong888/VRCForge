from __future__ import annotations

import json
import hashlib
from dataclasses import dataclass, replace
from pathlib import Path
from threading import Event, RLock, Thread
from typing import Any, Mapping

import pytest

from provider_configuration_service import (
    ProviderApiConfig,
    ProviderConfigurationPersistencePorts,
    ProviderConfigurationPolicyPorts,
    ProviderConfigurationService,
    ProviderVisionConfig,
)


@dataclass
class FakeSettings:
    llm_provider: str = "openai"
    llm_api_key: str = "runtime-key"
    llm_model: str = "runtime-model"
    gemini_thinking_level: str = ""


@dataclass
class ApiRequest:
    provider: str = "openai"
    api_key: str = ""
    base_url: str | None = None
    model: str | None = None
    api_type: str | None = None
    thinking_level: str = ""
    context_window: int = 0


@dataclass
class VisionRequest:
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
        "capabilitySource": "fake",
    }


def _policy(validated: list[str] | None = None) -> ProviderConfigurationPolicyPorts:
    calls = validated if validated is not None else []

    def validate(value: str) -> None:
        calls.append(value)
        if value == "bad-saved-key":
            raise ValueError("invalid provider key")

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
        validate_provider_api_key=validate,
        provider_display_name=lambda provider: provider.title(),
        provider_auth_label=lambda _provider: "Authorization: Bearer",
        provider_requires_api_key=lambda provider: provider != "ollama",
        provider_config_descriptor=_descriptor,
    )


def _owner(
    path: Path,
    *,
    policy: ProviderConfigurationPolicyPorts | None = None,
    settings: FakeSettings | None = None,
    atomic_write=None,
    lock: RLock | None = None,
) -> ProviderConfigurationService:
    def write(path_value: Path, payload: Mapping[str, Any]) -> None:
        path_value.write_text(json.dumps(payload), encoding="utf-8")

    return ProviderConfigurationService(
        ProviderConfigurationPersistencePorts(
            config_path=path,
            load_runtime_settings=lambda: settings or FakeSettings(),
            atomic_write_json=atomic_write or write,
            path_is_reparse_or_link=lambda _path: False,
        ),
        policy or _policy(),
        lock or RLock(),
    )


@pytest.mark.parametrize("provider", ["deepseek", "openrouter", "anthropic"])
def test_save_validates_provider_catalog_after_restoring_key(tmp_path, provider) -> None:
    calls = []
    policy = replace(_policy(), catalog_reasoning_efforts=lambda *args: calls.append(args) or ["low", "max"])
    owner = _owner(tmp_path / "config.json", policy=policy)
    owner.save_api_config(ProviderApiConfig(provider, "saved-key", "https://catalog.example/v1", "model"))
    assert owner.resolve_api_request(ApiRequest(provider=provider, model="model", thinking_level="max")).thinking_level == "max"
    assert calls[-1][1] == "saved-key"
    with pytest.raises(ValueError, match="not supported"):
        owner.resolve_api_request(ApiRequest(provider=provider, model="model", thinking_level="high"))


@pytest.mark.parametrize("provider", ["deepseek", "openrouter"])
def test_missing_catalog_keeps_existing_variants_and_default_is_unspecified(tmp_path, provider) -> None:
    policy = replace(_policy(), normalize_reasoning_effort=lambda v: "" if v == "default" else str(v or ""), catalog_reasoning_efforts=lambda *args: None)
    owner = _owner(tmp_path / "config.json", policy=policy)
    assert owner.resolve_api_request(ApiRequest(provider=provider, thinking_level="low")).thinking_level == "low"
    assert owner.resolve_api_request(ApiRequest(provider=provider, thinking_level="default")).thinking_level == ""
    with pytest.raises(ValueError, match="not supported"):
        owner.resolve_api_request(ApiRequest(provider=provider, thinking_level="max"))


def test_catalog_rejects_invalid_stored_choice_without_selecting_default(tmp_path) -> None:
    policy = replace(_policy(), catalog_reasoning_efforts=lambda *args: [])
    owner = _owner(tmp_path / "config.json", policy=policy)
    with pytest.raises(ValueError, match="not supported"):
        owner.resolve_api_request(ApiRequest(provider="openrouter", thinking_level="high"))
    policy = replace(policy, normalize_reasoning_effort=lambda _value: "")
    with pytest.raises(ValueError, match="not recognized"):
        _owner(tmp_path / "unknown.json", policy=policy).resolve_api_request(ApiRequest(provider="deepseek", thinking_level="not-a-level"))


def test_native_binding_is_explicit_stable_and_credential_bound() -> None:
    config = ProviderApiConfig(
        provider="custom",
        api_key="secret-a",
        base_url="https://provider.example/v1",
        model="model-a",
        api_type="chat_completions",
        thinking_level="medium",
        context_window=128_000,
    )
    assert config.native_binding() == replace(config).native_binding()
    assert config.native_binding()
    assert replace(config, context_window=256_000).native_binding() == config.native_binding()
    for changed in (
        replace(config, api_key="secret-b"),
        replace(config, provider="other"),
        replace(config, base_url="https://other.example/v1"),
        replace(config, model="model-b"),
        replace(config, api_type="responses"),
        replace(config, thinking_level="high"),
    ):
        assert changed.native_binding() != config.native_binding()


def test_clear_active_chatgpt_account_removes_only_matching_active_credential(tmp_path: Path) -> None:
    path = tmp_path / "config.json"
    owner = _owner(path, settings=FakeSettings(llm_provider="chatgpt", llm_api_key="account-1", llm_model="gpt-test"))
    owner.save_api_config(ProviderApiConfig("chatgpt", "account-1", "https://api.openai.com/v1", "gpt-test", "responses"))
    owner.save_vision_config(ProviderVisionConfig("chatgpt", "account-1", "https://api.openai.com/v1", "vision-test"))
    owner.save_api_config(ProviderApiConfig("openai", "other-provider-key", "https://api.openai.com/v1", "other-model", "responses"))
    owner.save_api_config(ProviderApiConfig("chatgpt", "account-1", "https://api.openai.com/v1", "gpt-test", "responses"))

    assert owner.clear_active_chatgpt_account("other-account") is False
    assert owner.current_api_config().api_key == "account-1"
    assert owner.clear_active_chatgpt_account("account-1") is True
    assert owner.current_api_config().provider == "chatgpt"
    assert owner.current_api_config().api_key == ""
    document = json.loads(path.read_text(encoding="utf-8"))
    assert document["provider_keys"] == {"api": {"openai": "other-provider-key"}, "vision": {}}
    reopened = _owner(path)
    assert reopened.resolve_api_request(ApiRequest(provider="chatgpt")).api_key == ""
    assert reopened.resolve_api_request(ApiRequest(provider="openai")).api_key == "other-provider-key"
    projection = owner.serialize_api_config(include_secret=False)
    assert projection["configured"] is False
    assert projection["credentialFingerprint"] == ""


def test_clear_chatgpt_account_reference_when_gemini_is_current(tmp_path: Path) -> None:
    path = tmp_path / "config.json"
    owner = _owner(path, settings=FakeSettings(llm_provider="gemini", llm_api_key="gemini-key", llm_model="gemini-model"))
    owner.save_api_config(ProviderApiConfig("chatgpt", "stale-account", "https://api.openai.com/v1", "gpt-test", "responses"))
    owner.save_vision_config(ProviderVisionConfig("chatgpt", "stale-account", "https://api.openai.com/v1", "vision-test"))
    owner.save_api_config(ProviderApiConfig("gemini", "gemini-key", "https://generativelanguage.googleapis.com/v1beta", "gemini-model", "responses"))

    assert owner.current_api_config().provider == "gemini"
    assert owner.clear_active_chatgpt_account("stale-account") is True
    document = json.loads(path.read_text(encoding="utf-8"))
    assert document["provider_keys"] == {"api": {"gemini": "gemini-key"}, "vision": {}}
    reopened = _owner(path, settings=FakeSettings(llm_provider="gemini", llm_api_key="gemini-key", llm_model="gemini-model"))
    assert reopened.current_api_config().provider == "gemini"
    assert reopened.current_api_config().api_key == "gemini-key"
    assert reopened.resolve_api_request(ApiRequest(provider="chatgpt")).api_key == ""
    assert reopened.resolve_api_request(ApiRequest(provider="gemini")).api_key == "gemini-key"


@pytest.mark.parametrize("api_type", [None, "auto", "responses", "messages", "gemini"])
def test_native_binding_requires_explicit_chat_completions(api_type: str | None) -> None:
    config = ProviderApiConfig("custom", "secret", "https://provider.example/v1", "model", api_type=api_type)
    assert config.native_binding() == ""


def _write_old_document(path: Path, *, api_key: str = "old-api-key") -> bytes:
    original = json.dumps(
        {
            "api": {
                "provider": "openai",
                "api_key": api_key,
                "base_url": "https://old.example/v1",
                "model": "old-model",
                "api_type": "chat_completions",
                "thinking_level": "low",
            },
            "vision": {
                "provider": "openai",
                "api_key": "old-vision-key",
                "base_url": "https://vision.example/v1",
                "model": "vision-model",
                "enabled": True,
            },
        },
        separators=(",", ":"),
    ).encode()
    path.write_bytes(original)
    return original


def test_configuration_owner_has_no_dashboard_host_or_implementation_facade() -> None:
    source = Path("provider_configuration_service.py").read_text(encoding="utf-8")
    assert "_host" not in source
    assert "__getattr__" not in source
    assert "_impl_" not in source
    assert "sys.modules" not in source
    assert "dashboard_server" not in source
    assert ProviderConfigurationPolicyPorts.__dataclass_params__.frozen is True
    assert ProviderConfigurationPersistencePorts.__dataclass_params__.frozen is True


def test_configuration_loads_both_sections_and_safe_projection_drops_secrets(
    tmp_path: Path,
) -> None:
    path = tmp_path / "config.json"
    _write_old_document(path)
    owner = _owner(path)

    assert owner.current_api_config().model == "old-model"
    assert owner.current_vision_config().configured is True
    assert owner.serialize_api_config(include_secret=True)["api_key"] == "old-api-key"
    assert owner.serialize_api_config(include_secret=False)["api_key"] == "old-****-key"
    assert owner.serialize_app_api_config()["credentialFingerprint"] == hashlib.sha256(b"old-api-key").hexdigest()
    assert "api_key" not in owner.serialize_app_api_config()
    assert "api_key" not in owner.serialize_app_vision_config()
    assert "old-api-key" not in json.dumps(owner.serialize_app_api_config())
    assert "old-vision-key" not in json.dumps(owner.serialize_app_vision_config())


def test_vision_profile_requires_explicit_enable_flag(tmp_path: Path) -> None:
    path = tmp_path / "config.json"
    path.write_text(json.dumps({"vision": {
        "provider": "openai",
        "api_key": "vision-key",
        "base_url": "https://api.openai.com/v1",
        "model": "vision-model",
    }}), encoding="utf-8")
    owner = _owner(path)
    assert owner.current_vision_config().configured is True
    assert owner.current_vision_config().enabled is False


def test_configuration_resolves_same_provider_saved_key_then_revalidates(
    tmp_path: Path,
) -> None:
    path = tmp_path / "config.json"
    _write_old_document(path)
    validated: list[str] = []
    owner = _owner(path, policy=_policy(validated))

    resolved = owner.resolve_api_request(ApiRequest(model="new-model"))
    vision = owner.resolve_vision_request(VisionRequest(model="new-vision"))

    assert resolved.api_key == "old-api-key"
    assert vision.api_key == "old-vision-key"
    assert validated == ["", "old-api-key", "", "old-vision-key"]


@pytest.mark.parametrize(
    ("provider", "key", "expected"),
    [("openai", "", False), ("openai", "saved-key", True), ("ollama", "", True)],
)
def test_configured_projection_uses_provider_auth_policy_without_claiming_verification(
    tmp_path: Path, provider: str, key: str, expected: bool,
) -> None:
    owner = _owner(
        tmp_path / "config.json",
        settings=FakeSettings(llm_provider=provider, llm_api_key=key),
    )
    projection = owner.serialize_app_api_config()
    assert projection["configured"] is expected
    assert "verified" not in projection
    assert "api_key" not in projection


def test_configuration_persists_and_reuses_keys_for_each_provider_and_lane(
    tmp_path: Path,
) -> None:
    path = tmp_path / "config.json"
    _write_old_document(path)
    owner = _owner(path)

    owner.save_api_config(
        ProviderApiConfig(
            provider="anthropic",
            api_key="anthropic-main-key",
            base_url="https://anthropic.example/v1",
            model="claude-main",
        )
    )
    owner.save_vision_config(
        ProviderVisionConfig(
            provider="gemini",
            api_key="gemini-vision-key",
            base_url="https://gemini.example/v1",
            model="gemini-vision",
            enabled=True,
        )
    )

    document = json.loads(path.read_text(encoding="utf-8"))
    assert document["provider_keys"] == {
        "api": {
            "anthropic": "anthropic-main-key",
            "openai": "old-api-key",
        },
        "vision": {
            "gemini": "gemini-vision-key",
            "openai": "old-vision-key",
        },
    }

    reopened = _owner(path)
    assert reopened.resolve_api_request(
        ApiRequest(provider="openai", model="openai-next")
    ).api_key == "old-api-key"
    assert reopened.resolve_vision_request(
        VisionRequest(provider="openai", model="openai-vision-next")
    ).api_key == "old-vision-key"
    assert reopened.resolve_api_request(
        ApiRequest(provider="gemini", model="gemini-main")
    ).api_key == "gemini-vision-key"
    assert reopened.resolve_vision_request(
        VisionRequest(provider="anthropic", model="claude-vision")
    ).api_key == "anthropic-main-key"


def test_configuration_projects_saved_provider_ids_without_projecting_any_saved_key(
    tmp_path: Path,
) -> None:
    path = tmp_path / "config.json"
    path.write_text(
        json.dumps(
            {
                "api": {
                    "provider": "openai",
                    "api_key": "openai-current-secret",
                    "model": "gpt-4o",
                },
                "provider_keys": {
                    "api": {"anthropic": "anthropic-secret"},
                    "vision": {"gemini": "gemini-secret"},
                },
            }
        ),
        encoding="utf-8",
    )
    owner = _owner(path)

    api_projection = owner.serialize_app_api_config()
    vision_projection = owner.serialize_app_vision_config()
    projected_text = json.dumps(
        {"apiConfig": api_projection, "visionConfig": vision_projection},
        sort_keys=True,
    )

    assert api_projection["savedKeyProviders"] == ["anthropic", "gemini", "openai"]
    assert vision_projection["savedKeyProviders"] == ["anthropic", "gemini", "openai"]
    for secret in ("openai-current-secret", "anthropic-secret", "gemini-secret"):
        assert secret not in projected_text


def test_configuration_persists_and_projects_user_context_window_cap(
    tmp_path: Path,
) -> None:
    path = tmp_path / "config.json"
    _write_old_document(path)
    owner = _owner(path)

    assert owner.current_api_config().context_window == 0
    resolved = owner.resolve_api_request(ApiRequest(context_window=128_000))
    owner.save_api_config(resolved)

    assert json.loads(path.read_text(encoding="utf-8"))["api"]["context_window"] == 128_000
    assert owner.serialize_app_api_config()["contextWindow"] == 128_000
    assert _owner(path).current_api_config().context_window == 128_000

    with pytest.raises(ValueError, match="at least 4000"):
        owner.resolve_api_request(ApiRequest(context_window=1024))


def test_model_limit_and_user_context_cap_remain_distinct_in_projections(tmp_path: Path) -> None:
    policy = replace(
        _policy(),
        provider_config_descriptor=lambda config: {
            **_descriptor(config),
            "modelContextWindow": 1_000_000,
            "maxOutputTokens": 384_000,
        },
    )
    owner = _owner(tmp_path / "config.json", policy=policy)
    owner.save_api_config(
        ProviderApiConfig(
            "deepseek",
            "saved-key",
            "https://api.deepseek.com",
            "deepseek-v4-pro",
            "auto",
            context_window=128_000,
        )
    )

    projected = owner.serialize_app_api_config()
    effective = owner.build_effective_model_summary()

    assert projected["contextWindow"] == 128_000
    assert projected["modelContextWindow"] == 1_000_000
    assert effective["configuredContextWindow"] == 128_000
    assert effective["modelContextWindow"] == 1_000_000
    assert effective["effectiveContextWindow"] == 128_000
    assert effective["contextWindow"] == 128_000


def test_configuration_revalidates_invalid_saved_key_and_never_returns_it(
    tmp_path: Path,
) -> None:
    path = tmp_path / "config.json"
    _write_old_document(path, api_key="bad-saved-key")
    owner = _owner(path, policy=_policy())

    with pytest.raises(ValueError, match="invalid provider key"):
        owner.resolve_api_request(ApiRequest())


def test_failed_atomic_commit_preserves_disk_and_published_state(tmp_path: Path) -> None:
    path = tmp_path / "config.json"
    original = _write_old_document(path)

    def fail_atomic(_path: Path, _payload: Mapping[str, Any]) -> None:
        raise OSError("simulated atomic failure")

    owner = _owner(path, atomic_write=fail_atomic)
    old_api = owner.current_api_config()
    old_vision = owner.current_vision_config()
    new_api = ProviderApiConfig(
        provider="openai",
        api_key="new-api-key",
        base_url="https://new.example/v1",
        model="new-model",
        api_type="chat_completions",
    )

    with pytest.raises(OSError, match="simulated atomic failure"):
        owner.save_api_config(new_api)

    assert path.read_bytes() == original
    assert owner.current_api_config() is old_api
    assert owner.current_vision_config() is old_vision
    backups = list(tmp_path.glob("config.json.backup-*.bak"))
    assert len(backups) == 1
    assert backups[0].read_bytes() == original


def test_backup_collision_fails_before_atomic_write_or_memory_publish(tmp_path: Path) -> None:
    path = tmp_path / "config.json"
    original = _write_old_document(path)
    owner = _owner(path)
    old_api = owner.current_api_config()
    digest = __import__("hashlib").sha256(original).hexdigest()
    path.with_name(f"{path.name}.backup-{digest}.bak").write_bytes(b"wrong")
    calls: list[dict[str, Any]] = []

    def unexpected_atomic(_path: Path, payload: Mapping[str, Any]) -> None:
        calls.append(dict(payload))

    owner = _owner(path, atomic_write=unexpected_atomic)
    assert owner.current_api_config() == old_api
    with pytest.raises(OSError, match="backup collision"):
        owner.save_api_config(
            ProviderApiConfig("openai", "new", "https://new", "new-model")
        )
    assert calls == []
    assert path.read_bytes() == original
    assert owner.current_api_config() == old_api


def test_api_and_vision_saves_share_one_lock_and_preserve_both_sections(
    tmp_path: Path,
) -> None:
    path = tmp_path / "config.json"
    _write_old_document(path)
    first_entered = Event()
    release_first = Event()
    second_entered = Event()
    writes: list[dict[str, Any]] = []

    def blocking_atomic(path_value: Path, payload: Mapping[str, Any]) -> None:
        call_number = len(writes) + 1
        writes.append(dict(payload))
        if call_number == 1:
            first_entered.set()
            assert release_first.wait(5)
        else:
            second_entered.set()
        path_value.write_text(json.dumps(payload), encoding="utf-8")

    owner = _owner(path, atomic_write=blocking_atomic, lock=RLock())
    new_api = ProviderApiConfig("openai", "new-api", "https://api", "new-model")
    new_vision = ProviderVisionConfig(
        "openai",
        "new-vision",
        "https://vision",
        "vision-model",
        False,
    )
    failures: list[BaseException] = []

    def run(target) -> None:
        try:
            target()
        except BaseException as exc:  # noqa: BLE001
            failures.append(exc)

    api_thread = Thread(target=run, args=(lambda: owner.save_api_config(new_api),))
    vision_thread = Thread(
        target=run,
        args=(lambda: owner.save_vision_config(new_vision),),
    )
    api_thread.start()
    assert first_entered.wait(5)
    vision_thread.start()
    assert not second_entered.wait(0.1)
    release_first.set()
    api_thread.join(5)
    vision_thread.join(5)

    assert failures == []
    assert second_entered.is_set()
    payload = json.loads(path.read_text(encoding="utf-8"))
    assert payload["api"]["api_key"] == "new-api"
    assert payload["vision"]["api_key"] == "new-vision"
    assert owner.current_api_config() is new_api
    assert owner.current_vision_config() is new_vision


def test_reload_replaces_both_sections_under_the_owned_lock(tmp_path: Path) -> None:
    path = tmp_path / "config.json"
    _write_old_document(path)
    owner = _owner(path)
    assert owner.current_api_config().model == "old-model"
    document = json.loads(path.read_text(encoding="utf-8"))
    document["api"]["model"] = "repaired-model"
    document["vision"] = {}
    path.write_text(json.dumps(document), encoding="utf-8")

    api, vision = owner.reload_from_disk()

    assert api.model == "repaired-model"
    assert vision == ProviderVisionConfig()
    assert owner.current_api_config() is api
    assert owner.current_vision_config() is vision


def _real_provider_policy() -> ProviderConfigurationPolicyPorts:
    from model_provider_adapters import normalize_provider_api_type, validate_provider_api_key
    from vrchat_blendshape_agent import (
        get_provider_defaults,
        normalize_base_url,
        normalize_provider_name,
        normalize_reasoning_effort,
        reasoning_effort_variants,
    )

    return replace(
        _policy(),
        normalize_provider_name=normalize_provider_name,
        get_provider_defaults=get_provider_defaults,
        normalize_base_url=normalize_base_url,
        normalize_provider_api_type=normalize_provider_api_type,
        normalize_reasoning_effort=normalize_reasoning_effort,
        reasoning_effort_variants=reasoning_effort_variants,
        validate_provider_api_key=validate_provider_api_key,
    )


@pytest.mark.parametrize("model", ["deepseek-chat", "deepseek-v4-flash"])
def test_real_policy_cross_provider_session_uses_destination_default_and_saved_key(tmp_path: Path, model: str) -> None:
    owner = _owner(tmp_path / "providers.json", policy=_real_provider_policy())
    owner.save_api_config(ProviderApiConfig("deepseek", "fixture-deepseek-key", "https://old-route.invalid/v1", "deepseek-chat"))
    current = ProviderApiConfig("openai", "fixture-openai-key", "https://current-route.invalid/v1", "gpt-4.1-mini")
    owner.save_api_config(current)
    before = owner.config_path.read_bytes()

    selected = owner.resolve_session_config(provider="deepseek", model=model)

    assert selected.provider == "deepseek"
    assert selected.model == model
    assert selected.base_url == "https://api.deepseek.com"
    assert selected.api_key == "fixture-deepseek-key"
    assert selected.api_type == ("auto" if model == "deepseek-v4-flash" else "chat_completions")
    assert owner.current_api_config() is current
    assert owner.config_path.read_bytes() == before


def test_real_policy_cross_provider_custom_without_default_endpoint_stays_rejected(tmp_path: Path) -> None:
    from provider_endpoint_policy import ProviderEndpointError

    owner = _owner(tmp_path / "providers.json", policy=_real_provider_policy())
    owner.save_api_config(ProviderApiConfig("custom", "fixture-custom-key", "https://custom-route.invalid/v1", "custom-model"))
    current = ProviderApiConfig("openai", "fixture-openai-key", "https://current-route.invalid/v1", "gpt-4.1-mini")
    owner.save_api_config(current)
    before = owner.config_path.read_bytes()

    with pytest.raises(ProviderEndpointError, match="Base URL is empty"):
        owner.resolve_session_config(provider="custom", model="custom-model")

    assert owner.current_api_config() is current
    assert owner.config_path.read_bytes() == before


def test_real_policy_explicit_empty_endpoint_stays_rejected(tmp_path: Path) -> None:
    from provider_endpoint_policy import ProviderEndpointError

    owner = _owner(tmp_path / "providers.json", policy=_real_provider_policy())
    with pytest.raises(ProviderEndpointError, match="Base URL is empty"):
        owner.resolve_api_request(ApiRequest(provider="deepseek", api_key="fixture-key", base_url="", model="deepseek-v4-flash"))
    assert not owner.config_path.exists()


def test_real_policy_cross_provider_still_requires_a_supported_model(tmp_path: Path) -> None:
    from model_provider_adapters import ProviderApiTypeError

    owner = _owner(tmp_path / "providers.json", policy=_real_provider_policy())
    current = ProviderApiConfig("openai", "fixture-openai-key", "https://current-route.invalid/v1", "gpt-4.1-mini")
    owner.save_api_config(current)
    before = owner.config_path.read_bytes()
    with pytest.raises(ProviderApiTypeError):
        owner.resolve_session_config(provider="deepseek")
    assert owner.current_api_config() is current
    assert owner.config_path.read_bytes() == before
