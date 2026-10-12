"""Small, single-owner asynchronous transport for the Telegram Bot API.

This module deliberately owns no session, update cursor, retry loop, or provider
policy.  The backend service owns the transport instance and must call ``close``
when its lifecycle ends.  A token is retained only in the instance memory needed
to form Telegram requests; it is never included in exceptions or representations.
"""

from __future__ import annotations

import asyncio
import json
from urllib.parse import urlsplit
from typing import Any, Mapping, Sequence

import httpx


TELEGRAM_ORIGIN = "https://api.telegram.org"
MAX_LONG_POLL_SECONDS = 25
MAX_RETRY_AFTER_SECONDS = 60
MAX_TEXT_UTF16_UNITS = 4096


class TelegramBotError(Exception):
    """Safe, classifiable transport/protocol failure."""

    def __init__(self, code: str, message: str, *, retry_after: int | None = None) -> None:
        self.code = code
        self.retry_after = retry_after
        super().__init__(message)

    def __repr__(self) -> str:  # Never make a token-bearing request part of diagnostics.
        return f"TelegramBotError(code={self.code!r}, retry_after={self.retry_after!r})"


def split_telegram_text(text: str, max_utf16_units: int = MAX_TEXT_UTF16_UNITS) -> list[str]:
    """Split text without exceeding Telegram's UTF-16 limit or lone surrogates."""

    if max_utf16_units <= 0:
        raise ValueError("max_utf16_units must be positive")
    if not text:
        return [""]
    chunks: list[str] = []
    current: list[str] = []
    used = 0
    for character in text:
        codepoint = ord(character)
        if 0xD800 <= codepoint <= 0xDFFF:
            raise ValueError("text contains an isolated UTF-16 surrogate")
        units = 2 if codepoint > 0xFFFF else 1
        if units > max_utf16_units:
            raise ValueError("text contains a character larger than max_utf16_units")
        if current and used + units > max_utf16_units:
            chunks.append("".join(current))
            current = []
            used = 0
        # A single astral code point cannot fit a one-unit custom bound. Keep it
        # intact: callers never receive an invalid UTF-16 half.
        current.append(character)
        used += units
    if current:
        chunks.append("".join(current))
    return chunks


class TelegramBotTransport:
    """One Telegram Bot API client with bounded, non-retrying requests."""

    def __init__(
        self,
        token: str,
        *,
        client: httpx.AsyncClient | None = None,
        transport: httpx.AsyncBaseTransport | None = None,
        timeout: float | httpx.Timeout = 30.0,
        proxy_mode: str = "environment",
        proxy_url: str = "",
    ) -> None:
        if not isinstance(token, str) or not token:
            raise ValueError("Telegram bot token is required")
        self._token = token
        self._closed = False
        self._owns_client = client is None
        # The backend service owns this client and its close lifecycle.  Injected
        # clients/transports are test or service-owned and are never closed here.
        mode, proxy = _validate_proxy(proxy_mode, proxy_url)
        kwargs = {"base_url": TELEGRAM_ORIGIN, "transport": transport, "timeout": timeout}
        if mode == "direct":
            kwargs["trust_env"] = False
        elif mode == "custom":
            kwargs["proxy"] = proxy
            kwargs["trust_env"] = False
        self._client = client or httpx.AsyncClient(**kwargs)

    def __repr__(self) -> str:
        return "TelegramBotTransport(closed=%s)" % self._closed

    async def close(self) -> None:
        if self._closed:
            return
        self._closed = True
        if self._owns_client:
            await self._client.aclose()

    async def _request(self, method: str, payload: Mapping[str, Any] | None = None,
                       *, photo: bytes | None = None) -> Any:
        if self._closed:
            raise TelegramBotError("closed", "Telegram transport is closed")
        try:
            # The token is required by Telegram's URL shape, but is never copied
            # into an exception, log, repr, or returned error detail.
            body = ({"data": dict(payload or {}), "files": {"photo": ("preview.png", photo, "image/png")}}
                    if photo is not None else {"json": dict(payload or {})})
            response = await self._client.post(
                f"{TELEGRAM_ORIGIN}/bot{self._token}/{method}",
                **body,
                follow_redirects=False,
            )
        except asyncio.CancelledError:
            raise
        except (httpx.TimeoutException, httpx.NetworkError) as exc:
            raise TelegramBotError("network_error", "Telegram request failed") from None
        except httpx.HTTPError as exc:
            raise TelegramBotError("network_error", "Telegram request failed") from None

        if response.status_code == 401:
            raise TelegramBotError("unauthorized", "Telegram bot authorization failed")
        if response.status_code == 429:
            retry_after = _bounded_retry_after(response)
            raise TelegramBotError("rate_limited", "Telegram rate limit reached", retry_after=retry_after)
        if response.status_code < 200 or response.status_code >= 300:
            raise TelegramBotError("protocol_error", "Telegram returned an unexpected response")
        try:
            body = response.json()
        except (ValueError, json.JSONDecodeError) as exc:
            raise TelegramBotError("protocol_error", "Telegram returned invalid JSON") from None
        if not isinstance(body, dict) or body.get("ok") is not True:
            error_code = body.get("error_code") if isinstance(body, dict) else None
            if error_code == 401:
                raise TelegramBotError("unauthorized", "Telegram bot authorization failed")
            if error_code == 429:
                raise TelegramBotError(
                    "rate_limited", "Telegram rate limit reached", retry_after=_bounded_retry_after_body(body)
                )
            raise TelegramBotError("protocol_error", "Telegram returned an unsuccessful response")
        return body.get("result")

    async def get_me(self) -> Any:
        return await self._request("getMe")

    async def set_chat_menu_button(self, owner_id: int) -> Any:
        return await self._request("setChatMenuButton", {"chat_id": owner_id,
            "menu_button": {"type": "commands"}})

    async def send_photo(self, chat_id: int, image: bytes, caption: str = "") -> Any:
        """Upload caller-authorized PNG bytes; never opens a path or fetches a URL."""
        if type(chat_id) is not int or not isinstance(image, bytes):
            raise ValueError("Invalid photo destination or data")
        if not image.startswith(b"\x89PNG\r\n\x1a\n") or not 8 < len(image) <= 8 * 1024 * 1024:
            raise ValueError("Preview must be a PNG no larger than 8 MiB")
        if len(caption.encode("utf-16-le")) // 2 > 1024:
            raise ValueError("Preview caption is too long")
        return await self._request("sendPhoto", {"chat_id": str(chat_id), "caption": caption}, photo=image)

    async def get_updates(
        self,
        offset: int | None = None,
        timeout: int = 0,
        allowed_updates: Sequence[str] | None = None,
    ) -> Any:
        if not isinstance(timeout, int) or isinstance(timeout, bool) or not 0 <= timeout <= MAX_LONG_POLL_SECONDS:
            raise ValueError(f"timeout must be between 0 and {MAX_LONG_POLL_SECONDS} seconds")
        payload: dict[str, Any] = {"timeout": timeout}
        if offset is not None:
            payload["offset"] = offset
        if allowed_updates is not None:
            payload["allowed_updates"] = list(allowed_updates)
        return await self._request("getUpdates", payload)

    async def send_message(self, chat_id: int | str, text: str, **kwargs: Any) -> list[Any]:
        chunks = split_telegram_text(text)
        results = []
        for chunk in chunks:
            payload = {**kwargs, "chat_id": chat_id, "text": chunk}
            results.append(await self._request("sendMessage", payload))
        return results

    async def send_chat_action(self, chat_id: int | str, action: str) -> Any:
        return await self._request("sendChatAction", {"chat_id": chat_id, "action": action})

    async def edit_message_text(self, chat_id: int | str, message_id: int, text: str, **kwargs: Any) -> Any:
        return await self._request("editMessageText", {**kwargs, "chat_id": chat_id, "message_id": message_id, "text": text})

    async def answer_callback_query(self, callback_query_id: str, **kwargs: Any) -> Any:
        return await self._request("answerCallbackQuery", {**kwargs, "callback_query_id": callback_query_id})

    async def set_my_commands(self, commands: Sequence[Mapping[str, str]], *, owner_id: int) -> Any:
        return await self._request("setMyCommands", {"commands": list(commands),
            "scope": {"type": "chat", "chat_id": owner_id}})

def _bounded_retry_after(response: httpx.Response) -> int | None:
    try:
        body = response.json()
    except (ValueError, json.JSONDecodeError):
        return None
    return _bounded_retry_after_body(body)


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


def _bounded_retry_after_body(body: Any) -> int | None:
    parameters = body.get("parameters") if isinstance(body, dict) else None
    value = parameters.get("retry_after") if isinstance(parameters, dict) else None
    try:
        parsed = int(value)
        if parsed < 0 or parsed > MAX_RETRY_AFTER_SECONDS:
            return None
        return parsed
    except (TypeError, ValueError):
        return None
