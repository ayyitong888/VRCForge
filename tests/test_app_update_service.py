from __future__ import annotations

import json
import threading
import time

import pytest

from app_update_service import (
    APP_UPDATE_SCHEMA,
    GITHUB_RELEASE_API_HOST,
    GITHUB_RELEASE_API_PATH,
    GITHUB_RELEASE_API_URL,
    MAX_RELEASE_RESPONSE_BYTES,
    AppUpdateService,
    GitHubReleaseClient,
    StableVersion,
)


class StaticReleaseClient:
    def __init__(self, payload: object | Exception) -> None:
        self.payload = payload
        self.calls = 0
        self.cancelled = False

    def fetch(self, cancel_event: threading.Event) -> bytes:
        self.calls += 1
        if isinstance(self.payload, Exception):
            raise self.payload
        return json.dumps(self.payload).encode("utf-8")

    def cancel(self) -> None:
        self.cancelled = True


class BlockingReleaseClient:
    def __init__(self, payload: object) -> None:
        self.payload = payload
        self.calls = 0
        self.entered = threading.Event()
        self.release = threading.Event()

    def fetch(self, cancel_event: threading.Event) -> bytes:
        self.calls += 1
        self.entered.set()
        while not self.release.wait(0.01):
            if cancel_event.is_set():
                raise RuntimeError("cancelled with secret response details")
        return json.dumps(self.payload).encode("utf-8")

    def cancel(self) -> None:
        self.release.set()


def release_payload(version: str, **overrides: object) -> dict[str, object]:
    payload: dict[str, object] = {
        "tag_name": version,
        "draft": False,
        "prerelease": False,
        "html_url": "https://attacker.invalid/never-trust-response-links",
        "body": "raw release notes must never enter the app payload",
    }
    payload.update(overrides)
    return payload


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        ("1.6.0", (1, 6, 0)),
        ("v2.0.13", (2, 0, 13)),
        ("0.0.0", (0, 0, 0)),
    ],
)
def test_stable_semver_accepts_only_canonical_three_part_versions(
    raw: str,
    expected: tuple[int, int, int],
) -> None:
    assert StableVersion.parse(raw).parts == expected


@pytest.mark.parametrize(
    "raw",
    [
        "",
        "1.6",
        "1.6.0.1",
        "1.6.0-beta.1",
        "1.6.0+build",
        "01.6.0",
        "v1.06.0",
        "1.6.-1",
        "9999999999.0.0",
    ],
)
def test_stable_semver_rejects_nonstable_or_ambiguous_tags(raw: str) -> None:
    with pytest.raises(ValueError):
        StableVersion.parse(raw)


def test_update_available_uses_fixed_release_url_and_never_remote_body_or_url() -> None:
    service = AppUpdateService("1.6.0", client=StaticReleaseClient(release_payload("v1.7.0")))

    result = service.check()

    assert result == {
        "ok": True,
        "schema": APP_UPDATE_SCHEMA,
        "status": "update_available",
        "currentVersion": "1.6.0",
        "latestVersion": "1.7.0",
        "releaseUrl": "https://github.com/ayyitong888/VRCForge/releases/tag/v1.7.0",
        "shouldNotify": True,
    }
    serialized = json.dumps(result)
    assert "attacker.invalid" not in serialized
    assert "raw release notes" not in serialized


def test_equal_or_older_release_is_up_to_date() -> None:
    for latest in ("v1.6.0", "v1.5.9"):
        service = AppUpdateService("1.6.0", client=StaticReleaseClient(release_payload(latest)))
        result = service.check()
        assert result["status"] == "up_to_date"
        assert result["latestVersion"] == latest.removeprefix("v")
        assert result["releaseUrl"] == ""
        assert result["shouldNotify"] is False


@pytest.mark.parametrize(
    "payload",
    [
        release_payload("v1.7.0", draft=True),
        release_payload("v1.7.0", prerelease=True),
        release_payload("v1.7.0-beta.1"),
        {"tag_name": "v1.7.0", "draft": "false", "prerelease": False},
        [release_payload("v1.7.0")],
    ],
)
def test_invalid_release_response_is_safe_and_silent(payload: object) -> None:
    result = AppUpdateService("1.6.0", client=StaticReleaseClient(payload)).check()
    assert result == {
        "ok": False,
        "schema": APP_UPDATE_SCHEMA,
        "status": "unavailable",
        "currentVersion": "1.6.0",
        "latestVersion": "",
        "releaseUrl": "",
        "shouldNotify": False,
    }


def test_offline_failure_is_silent_and_does_not_leak_exception() -> None:
    client = StaticReleaseClient(RuntimeError("token=secret raw-body=<html>oops</html>"))
    result = AppUpdateService("1.6.0", client=client).check()

    assert result["status"] == "unavailable"
    assert result["shouldNotify"] is False
    assert "secret" not in json.dumps(result)
    assert "html" not in json.dumps(result)


def test_startup_check_is_cached_for_the_app_process() -> None:
    client = StaticReleaseClient(release_payload("v1.7.0"))
    service = AppUpdateService("1.6.0", client=client)

    first = service.check()
    second = service.check()

    assert first["shouldNotify"] is True
    assert second == first
    assert client.calls == 1


def test_concurrent_startup_checks_share_one_bounded_inflight_request() -> None:
    client = BlockingReleaseClient(release_payload("v1.7.0"))
    service = AppUpdateService("1.6.0", client=client)
    results: list[dict[str, object]] = []

    threads = [threading.Thread(target=lambda: results.append(service.check())) for _ in range(2)]
    for thread in threads:
        thread.start()
    assert client.entered.wait(1)
    time.sleep(0.03)
    client.release.set()
    for thread in threads:
        thread.join(1)

    assert client.calls == 1
    assert len(results) == 2
    assert all(item["status"] == "update_available" for item in results)


def test_shutdown_cancels_the_inflight_startup_check() -> None:
    client = BlockingReleaseClient(release_payload("v1.7.0"))
    service = AppUpdateService("1.6.0", client=client)
    result: list[dict[str, object]] = []
    thread = threading.Thread(target=lambda: result.append(service.check()))
    thread.start()
    assert client.entered.wait(1)

    service.close()
    thread.join(1)

    assert result[0]["status"] == "cancelled"
    assert service.check()["status"] == "cancelled"


class FakeResponse:
    def __init__(self, *, status: int = 200, body: bytes = b"{}", content_length: str | None = None) -> None:
        self.status = status
        self._body = body
        self._cursor = 0
        self._content_length = content_length

    def getheader(self, name: str) -> str | None:
        return self._content_length if name.lower() == "content-length" else None

    def read(self, amount: int) -> bytes:
        chunk = self._body[self._cursor : self._cursor + amount]
        self._cursor += len(chunk)
        return chunk


class FakeConnection:
    def __init__(self, response: FakeResponse) -> None:
        self.response = response
        self.request_args: tuple[object, ...] | None = None
        self.closed = False

    def request(self, *args: object, **_kwargs: object) -> None:
        self.request_args = args

    def getresponse(self) -> FakeResponse:
        return self.response

    def close(self) -> None:
        self.closed = True


def test_github_client_uses_only_fixed_endpoint_and_bounded_get() -> None:
    response = FakeResponse(body=b'{"tag_name":"v1.7.0"}')
    connection = FakeConnection(response)
    observed: list[tuple[str, float]] = []

    def connection_factory(host: str, timeout: float) -> FakeConnection:
        observed.append((host, timeout))
        return connection

    client = GitHubReleaseClient(connection_factory=connection_factory)
    body = client.fetch(threading.Event())

    assert body == b'{"tag_name":"v1.7.0"}'
    assert observed == [(GITHUB_RELEASE_API_HOST, 4.0)]
    assert connection.request_args is not None
    assert connection.request_args[:2] == ("GET", GITHUB_RELEASE_API_PATH)
    assert GITHUB_RELEASE_API_URL == f"https://{GITHUB_RELEASE_API_HOST}{GITHUB_RELEASE_API_PATH}"
    assert connection.closed


@pytest.mark.parametrize(
    "response",
    [
        FakeResponse(status=500, body=b"secret backend body"),
        FakeResponse(body=b"x", content_length=str(MAX_RELEASE_RESPONSE_BYTES + 1)),
        FakeResponse(body=b"x" * (MAX_RELEASE_RESPONSE_BYTES + 1)),
    ],
)
def test_github_client_rejects_non_200_or_oversized_responses(response: FakeResponse) -> None:
    client = GitHubReleaseClient(connection_factory=lambda _host, _timeout: FakeConnection(response))
    with pytest.raises(RuntimeError):
        client.fetch(threading.Event())
