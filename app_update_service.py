"""One bounded startup check for a newer stable VRCForge release."""

from __future__ import annotations

import http.client
import json
import re
import threading
from dataclasses import dataclass
from typing import Any, Callable, Protocol


APP_UPDATE_SCHEMA = "vrcforge.app_update.v1"
GITHUB_RELEASE_API_HOST = "api.github.com"
GITHUB_RELEASE_API_PATH = "/repos/ayyitong888/VRCForge/releases/latest"
GITHUB_RELEASE_API_URL = f"https://{GITHUB_RELEASE_API_HOST}{GITHUB_RELEASE_API_PATH}"
GITHUB_RELEASE_PAGE_PREFIX = "https://github.com/ayyitong888/VRCForge/releases/tag/v"
MAX_RELEASE_RESPONSE_BYTES = 64 * 1024
RELEASE_REQUEST_TIMEOUT_SECONDS = 4.0

_STABLE_VERSION_PATTERN = re.compile(r"^v?(0|[1-9][0-9]{0,8})\.(0|[1-9][0-9]{0,8})\.(0|[1-9][0-9]{0,8})$")


@dataclass(frozen=True, order=True)
class StableVersion:
    major: int
    minor: int
    patch: int

    @property
    def parts(self) -> tuple[int, int, int]:
        return (self.major, self.minor, self.patch)

    def __str__(self) -> str:
        return f"{self.major}.{self.minor}.{self.patch}"

    @classmethod
    def parse(cls, raw: str) -> "StableVersion":
        match = _STABLE_VERSION_PATTERN.fullmatch(str(raw or "").strip())
        if match is None:
            raise ValueError("version must be a canonical stable MAJOR.MINOR.PATCH value")
        return cls(*(int(part) for part in match.groups()))


class ReleaseClient(Protocol):
    def fetch(self, cancel_event: threading.Event) -> bytes: ...

    def cancel(self) -> None: ...


class GitHubReleaseClient:
    """Perform one bounded HTTPS GET against the compile-time GitHub endpoint."""

    def __init__(
        self,
        *,
        connection_factory: Callable[[str, float], Any] | None = None,
    ) -> None:
        self._connection_factory = connection_factory or (
            lambda host, timeout: http.client.HTTPSConnection(host, timeout=timeout)
        )
        self._lock = threading.Lock()
        self._active_connection: Any | None = None

    def fetch(self, cancel_event: threading.Event) -> bytes:
        if cancel_event.is_set():
            raise RuntimeError("request cancelled")
        connection = self._connection_factory(
            GITHUB_RELEASE_API_HOST,
            RELEASE_REQUEST_TIMEOUT_SECONDS,
        )
        with self._lock:
            self._active_connection = connection
        try:
            connection.request(
                "GET",
                GITHUB_RELEASE_API_PATH,
                None,
                {
                    "Accept": "application/vnd.github+json",
                    "Connection": "close",
                    "User-Agent": "VRCForge-App-Update",
                    "X-GitHub-Api-Version": "2022-11-28",
                },
            )
            response = connection.getresponse()
            if int(response.status) != 200:
                raise RuntimeError("release endpoint returned a non-success status")
            declared_length = response.getheader("Content-Length")
            if declared_length is not None:
                try:
                    length = int(declared_length)
                except (TypeError, ValueError) as exc:
                    raise RuntimeError("release endpoint returned an invalid response length") from exc
                if length < 0 or length > MAX_RELEASE_RESPONSE_BYTES:
                    raise RuntimeError("release endpoint response exceeded the byte limit")
            body = response.read(MAX_RELEASE_RESPONSE_BYTES + 1)
            if len(body) > MAX_RELEASE_RESPONSE_BYTES:
                raise RuntimeError("release endpoint response exceeded the byte limit")
            if cancel_event.is_set():
                raise RuntimeError("request cancelled")
            return bytes(body)
        finally:
            with self._lock:
                if self._active_connection is connection:
                    self._active_connection = None
            try:
                connection.close()
            except Exception:  # noqa: BLE001 - best-effort transport cleanup.
                pass

    def cancel(self) -> None:
        with self._lock:
            connection = self._active_connection
        if connection is not None:
            try:
                connection.close()
            except Exception:  # noqa: BLE001 - cancellation remains best effort.
                pass


def _cancelled_base() -> dict[str, Any]:
    return {
        "ok": False,
        "status": "cancelled",
        "latestVersion": "",
        "releaseUrl": "",
    }


def _unavailable_base() -> dict[str, Any]:
    return {
        "ok": False,
        "status": "unavailable",
        "latestVersion": "",
        "releaseUrl": "",
    }


class AppUpdateService:
    """Own at most one GitHub request during an App process lifetime."""

    def __init__(self, current_version: str, *, client: ReleaseClient | None = None) -> None:
        self._current = StableVersion.parse(current_version)
        self._client: ReleaseClient = client or GitHubReleaseClient()
        self._cancel_event = threading.Event()
        self._condition = threading.Condition()
        self._closed = False
        self._inflight = False
        self._result: dict[str, Any] | None = None

    @property
    def current_version(self) -> str:
        return str(self._current)

    def _perform_check(self) -> dict[str, Any]:
        if self._cancel_event.is_set():
            return _cancelled_base()
        try:
            raw = self._client.fetch(self._cancel_event)
            if self._cancel_event.is_set():
                return _cancelled_base()
            if len(raw) > MAX_RELEASE_RESPONSE_BYTES:
                raise ValueError("response exceeded limit")
            document = json.loads(raw.decode("utf-8"))
            if not isinstance(document, dict):
                raise ValueError("release response must be an object")
            if type(document.get("draft")) is not bool or type(document.get("prerelease")) is not bool:
                raise ValueError("release flags must be boolean")
            if document["draft"] or document["prerelease"]:
                raise ValueError("latest release must be stable")
            latest = StableVersion.parse(str(document.get("tag_name") or ""))
            if latest > self._current:
                return {
                    "ok": True,
                    "status": "update_available",
                    "latestVersion": str(latest),
                    "releaseUrl": f"{GITHUB_RELEASE_PAGE_PREFIX}{latest}",
                }
            return {
                "ok": True,
                "status": "up_to_date",
                "latestVersion": str(latest),
                "releaseUrl": "",
            }
        except Exception:  # noqa: BLE001 - raw transport/response details must not cross this boundary.
            return _cancelled_base() if self._cancel_event.is_set() else _unavailable_base()

    def check(self) -> dict[str, Any]:
        owner = False
        with self._condition:
            if self._closed:
                base = _cancelled_base()
            elif self._result is not None:
                return dict(self._result)
            elif self._inflight:
                while self._inflight and not self._closed:
                    self._condition.wait()
                if self._result is not None:
                    return dict(self._result)
                base = _cancelled_base() if self._closed else _unavailable_base()
            else:
                self._inflight = True
                owner = True
                base = {}
        if owner:
            base = self._perform_check()
        result = {
            "ok": bool(base.get("ok")),
            "schema": APP_UPDATE_SCHEMA,
            "status": str(base.get("status") or "unavailable"),
            "currentVersion": self.current_version,
            "latestVersion": str(base.get("latestVersion") or ""),
            "releaseUrl": str(base.get("releaseUrl") or ""),
            "shouldNotify": base.get("status") == "update_available",
        }
        if owner:
            with self._condition:
                self._result = dict(result)
                self._inflight = False
                self._condition.notify_all()
        return result

    def close(self) -> None:
        with self._condition:
            self._closed = True
            self._cancel_event.set()
            self._condition.notify_all()
        self._client.cancel()


__all__ = [
    "APP_UPDATE_SCHEMA",
    "GITHUB_RELEASE_API_HOST",
    "GITHUB_RELEASE_API_PATH",
    "GITHUB_RELEASE_API_URL",
    "MAX_RELEASE_RESPONSE_BYTES",
    "AppUpdateService",
    "GitHubReleaseClient",
    "StableVersion",
]
