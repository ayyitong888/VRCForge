from __future__ import annotations

import pytest

from provider_endpoint_policy import (
    ProviderEndpointError,
    endpoint_for_protocol,
    endpoint_origin,
    normalize_provider_endpoint,
    same_endpoint_origin,
)


@pytest.mark.parametrize(
    "value",
    [
        "provider.example/v1",
        "ftp://provider.example/v1",
        "https://key@provider.example/v1",
        "https://provider.example/v1?key=x",
        "https://provider.example/v1#fragment",
    ],
)
def test_endpoint_rejects_ambiguous_credentials_extras_and_remote_plain_http(value: str) -> None:
    with pytest.raises(ProviderEndpointError):
        normalize_provider_endpoint(value)


def test_endpoint_canonicalizes_origin_port_path_and_allows_loopback_http() -> None:
    assert normalize_provider_endpoint("HTTPS://Provider.Example:443/v1/") == "https://provider.example/v1"
    assert normalize_provider_endpoint("http://127.0.0.1:11434/v1/") == "http://127.0.0.1:11434/v1"
    assert normalize_provider_endpoint("http://provider.example/v1/") == "http://provider.example/v1"
    assert endpoint_origin("https://provider.example:443/v1") == "https://provider.example"
    assert same_endpoint_origin("https://provider.example/v1", "https://provider.example/alt") is True
    assert same_endpoint_origin("https://provider.example/v1", "https://other.example/v1") is False
    assert endpoint_for_protocol("https://provider.example/v1", "messages") == "https://provider.example"
    assert endpoint_for_protocol("https://provider.example/gateway/v1", "messages") == "https://provider.example/gateway"
    assert endpoint_for_protocol("https://provider.example/v1", "responses") == "https://provider.example/v1"
