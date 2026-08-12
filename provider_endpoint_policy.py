"""Canonical endpoint validation shared by Provider configuration and probes."""

from __future__ import annotations

from urllib.parse import urlsplit, urlunsplit


class ProviderEndpointError(ValueError):
    """A custom or OpenAI-compatible endpoint is not safe to use."""


def normalize_provider_endpoint(value: object, *, allow_empty: bool = False) -> str:
    """Return a canonical HTTP(S) endpoint without credentials or URL extras."""

    raw = str(value or "").strip()
    if not raw:
        if allow_empty:
            return ""
        raise ProviderEndpointError("Base URL is empty.")
    parsed = urlsplit(raw)
    if parsed.scheme not in {"http", "https"} or not parsed.hostname:
        raise ProviderEndpointError("Base URL must be an absolute HTTP(S) URL.")
    if parsed.username is not None or parsed.password is not None:
        raise ProviderEndpointError("Base URL must not contain credentials.")
    if parsed.query or parsed.fragment:
        raise ProviderEndpointError("Base URL must not contain a query or fragment.")
    # Remote HTTP remains accepted for compatibility with existing self-hosted
    # and LAN gateways.  Redirect following is disabled at the protocol client
    # boundary; the settings UI can warn about plaintext transport without
    # silently removing an already-working custom endpoint.
    try:
        port = parsed.port
    except ValueError as exc:
        raise ProviderEndpointError("Base URL port is invalid.") from exc
    host = parsed.hostname.lower()
    host_text = f"[{host}]" if ":" in host else host
    default_port = 443 if parsed.scheme == "https" else 80
    netloc = host_text if port in {None, default_port} else f"{host_text}:{port}"
    path = (parsed.path or "").rstrip("/")
    return urlunsplit((parsed.scheme, netloc, path, "", ""))


def endpoint_origin(value: object) -> str:
    normalized = normalize_provider_endpoint(value)
    parsed = urlsplit(normalized)
    return urlunsplit((parsed.scheme, parsed.netloc, "", "", ""))


def same_endpoint_origin(left: object, right: object) -> bool:
    return endpoint_origin(left) == endpoint_origin(right)


def endpoint_for_protocol(value: object, api_type: str, *, provider: str = "") -> str:
    """Adapt one configured API base path without changing its origin.

    OpenAI-compatible clients treat a configured ``/v1`` as their API base,
    while the Anthropic SDK appends ``/v1/messages`` itself.  Strip only that
    exact terminal version segment for Messages; never guess or change hosts.
    """

    normalized = normalize_provider_endpoint(value)
    if str(api_type).strip().lower() != "messages":
        return normalized
    parsed = urlsplit(normalized)
    path = parsed.path.rstrip("/")
    if str(provider).strip().lower() == "deepseek":
        if path.split("/")[-1:] == ["v1"]:
            path = path[:-3]
        if path.split("/")[-1:] != ["anthropic"]:
            path = f"{path}/anthropic" if path else "/anthropic"
        return urlunsplit((parsed.scheme, parsed.netloc, path, "", ""))
    if path.split("/")[-1:] == ["v1"]:
        path = path[:-3]
    return urlunsplit((parsed.scheme, parsed.netloc, path.rstrip("/"), "", ""))
