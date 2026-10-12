"""Local-owner Telegram configuration, protected with Windows user DPAPI.

Only the authenticated desktop settings service accesses this file. No token is
returned in status or written to the queue database. Protection primitives are
reused without coupling Telegram data to the OAuth account store.
"""
from __future__ import annotations

import json
import os
import re
import tempfile
from urllib.parse import urlsplit
from pathlib import Path
from typing import Callable

from chatgpt_integration.storage import _dpapi_protect, _dpapi_unprotect


class TelegramSettings:
    def __init__(self, path: Path, *, protect: Callable[[bytes], bytes] = _dpapi_protect,
                 unprotect: Callable[[bytes], bytes] = _dpapi_unprotect) -> None:
        self.path = path
        self._protect = protect
        self._unprotect = unprotect

    def load(self) -> dict:
        if not self.path.exists():
            return {"token": "", "ownerId": None, "projects": [], "proxyMode": "environment", "proxyUrl": ""}
        if self.path.stat().st_size > 65536:
            raise ValueError("telegram_config_invalid")
        value = json.loads(self._unprotect(self.path.read_bytes()))
        if not isinstance(value, dict):
            raise ValueError("telegram_config_invalid")
        _validate_proxy(value.get("proxyMode", "environment"), value.get("proxyUrl", ""))
        return value

    def save(self, *, token: str, owner_id: int, projects: list[str], proxy_mode: str = "environment", proxy_url: str = "") -> None:
        if type(owner_id) is not int or not 0 < owner_id < 2**53:
            raise ValueError("telegram_owner_invalid")
        token = token.strip() or self.load()["token"]
        if not re.fullmatch(r"[0-9]+:[A-Za-z0-9_-]{20,200}", token):
            raise ValueError("telegram_token_invalid")
        if not isinstance(projects, list) or len(projects) > 32:
            raise ValueError("telegram_projects_invalid")
        proxy_mode, proxy_url = _validate_proxy(proxy_mode, proxy_url)
        roots = []
        for project in projects:
            path = Path(project)
            if not path.is_absolute() or not path.is_dir():
                raise ValueError("telegram_project_unavailable")
            roots.append(str(path.resolve()))
        data = self._protect(json.dumps({"token": token, "ownerId": owner_id,
                                        "projects": list(dict.fromkeys(roots)),
                                        "proxyMode": proxy_mode, "proxyUrl": proxy_url}).encode())
        self.path.parent.mkdir(parents=True, exist_ok=True)
        # A unique temporary handle is owned by this save and always closed.
        fd, temporary = tempfile.mkstemp(prefix=".telegram-", dir=self.path.parent)
        try:
            with os.fdopen(fd, "wb") as handle:
                handle.write(data)
                handle.flush()
                os.fsync(handle.fileno())
            os.replace(temporary, self.path)
        finally:
            if os.path.exists(temporary):
                os.unlink(temporary)

    def projection(self) -> dict:
        value = self.load()
        return {"configured": bool(value.get("token")), "ownerId": value.get("ownerId"),
                "projects": value.get("projects", []), "proxyMode": value.get("proxyMode", "environment"),
                "proxyUrl": value.get("proxyUrl", "")}


def _validate_proxy(mode: object, url: object) -> tuple[str, str]:
    mode = str(mode or "environment").strip().lower()
    url = str(url or "").strip()
    if mode not in {"environment", "direct", "custom"}:
        raise ValueError("telegram_proxy_mode_invalid")
    if mode != "custom":
        if url:
            raise ValueError("telegram_proxy_url_invalid")
        return mode, ""
    parsed = urlsplit(url)
    if parsed.scheme not in {"http", "https"} or not parsed.hostname or parsed.username or parsed.password:
        raise ValueError("telegram_proxy_url_invalid")
    if parsed.query or parsed.fragment or parsed.path not in {"", "/"}:
        raise ValueError("telegram_proxy_url_invalid")
    try:
        port = parsed.port
    except ValueError:
        raise ValueError("telegram_proxy_url_invalid") from None
    if port is not None and not 1 <= port <= 65535:
        raise ValueError("telegram_proxy_url_invalid")
    return mode, url
