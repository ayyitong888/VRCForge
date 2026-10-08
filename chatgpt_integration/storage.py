"""Protected local storage for ChatGPT OAuth registrations."""

from __future__ import annotations

import ctypes
import errno
import json
import os
import secrets
import threading
import time
import uuid
from contextlib import contextmanager
from pathlib import Path
from typing import Any, Callable, Iterator


_SCHEMA = "vrcforge.chatgpt_oauth.v1"
_MAX_STORE_BYTES = 2 * 1024 * 1024
_LOCK_WAIT_SECONDS = 10.0


class OAuthStorageError(RuntimeError):
    """A local OAuth store could not be read or written safely."""


class ChatGPTAuthStorage:
    def __init__(
        self,
        data_dir: Path,
        *,
        protector: Callable[[bytes], bytes] | None = None,
        unprotector: Callable[[bytes], bytes] | None = None,
    ) -> None:
        if (protector is None) != (unprotector is None):
            raise ValueError("Both protector and unprotector must be supplied together.")
        self.data_dir = Path(data_dir)
        self.path = self.data_dir / "accounts.dpapi"
        self.lock_path = self.data_dir / ".accounts.lock"
        self._protect = protector or _dpapi_protect
        self._unprotect = unprotector or _dpapi_unprotect
        lock_key = str(self.lock_path.resolve())
        if os.name == "nt":
            lock_key = lock_key.casefold()
        with _LOCAL_LOCKS_GUARD:
            self._thread_lock = _LOCAL_LOCKS.setdefault(lock_key, threading.RLock())

    @contextmanager
    def locked(self) -> Iterator[None]:
        with self._thread_lock:
            try:
                self.data_dir.mkdir(parents=True, exist_ok=True, mode=0o700)
                if os.name != "nt":
                    try:
                        self.data_dir.chmod(0o700)
                    except OSError:
                        pass
                with self.lock_path.open("a+b") as handle:
                    handle.seek(0, os.SEEK_END)
                    if handle.tell() == 0:
                        handle.write(b"\0")
                        handle.flush()
                        os.fsync(handle.fileno())
                    _lock_file(handle)
                    try:
                        yield
                    finally:
                        _unlock_file(handle)
            except OAuthStorageError:
                raise
            except OSError:
                raise OAuthStorageError("Local credentials are unavailable.") from None

    def load_unlocked(self) -> dict[str, Any]:
        try:
            info = self.path.lstat()
        except FileNotFoundError:
            return _empty_store()
        except OSError:
            raise OAuthStorageError("Local credentials are unavailable.") from None
        if self.path.is_symlink() or not self.path.is_file() or info.st_size > _MAX_STORE_BYTES:
            raise OAuthStorageError("Local credentials are invalid.")
        try:
            protected = self.path.read_bytes()
            plaintext = self._unprotect(protected)
            value = json.loads(plaintext.decode("utf-8"))
        except Exception:
            raise OAuthStorageError("Local credentials are unreadable.") from None
        return _validate_store(value)

    def save_unlocked(self, value: dict[str, Any]) -> None:
        store = _validate_store(value)
        try:
            payload = json.dumps(store, ensure_ascii=False, separators=(",", ":"), sort_keys=True).encode("utf-8")
            protected = self._protect(payload)
            if not isinstance(protected, bytes) or len(protected) > _MAX_STORE_BYTES:
                raise OAuthStorageError("Local credentials could not be protected.")
            self.data_dir.mkdir(parents=True, exist_ok=True, mode=0o700)
            temporary = self.data_dir / f".accounts.{secrets.token_hex(12)}.tmp"
            flags = os.O_WRONLY | os.O_CREAT | os.O_EXCL
            descriptor = os.open(temporary, flags, 0o600)
            try:
                with os.fdopen(descriptor, "wb") as handle:
                    handle.write(protected)
                    handle.flush()
                    os.fsync(handle.fileno())
                if os.name != "nt":
                    temporary.chmod(0o600)
                os.replace(temporary, self.path)
            finally:
                try:
                    temporary.unlink(missing_ok=True)
                except OSError:
                    pass
        except OAuthStorageError:
            raise
        except Exception:
            raise OAuthStorageError("Local credentials could not be saved.") from None


def _empty_store() -> dict[str, Any]:
    return {"schema": _SCHEMA, "host_id": "", "next_label_number": 1, "accounts": []}


def _validate_store(value: Any) -> dict[str, Any]:
    if not isinstance(value, dict) or value.get("schema") != _SCHEMA:
        raise OAuthStorageError("Local credentials are invalid.")
    host_id = value.get("host_id")
    next_label_number = value.get("next_label_number")
    accounts = value.get("accounts")
    if not isinstance(host_id, str) or (host_id and not host_id.startswith("urn:uuid:")):
        raise OAuthStorageError("Local credentials are invalid.")
    if host_id:
        try:
            uuid.UUID(host_id.removeprefix("urn:uuid:"))
        except ValueError:
            raise OAuthStorageError("Local credentials are invalid.") from None
    if not isinstance(next_label_number, int) or next_label_number < 1:
        raise OAuthStorageError("Local credentials are invalid.")
    if not isinstance(accounts, list) or len(accounts) > 100:
        raise OAuthStorageError("Local credentials are invalid.")
    seen_ids: set[str] = set()
    for account in accounts:
        if not isinstance(account, dict):
            raise OAuthStorageError("Local credentials are invalid.")
        account_id = account.get("id")
        if not isinstance(account_id, str) or not account_id or account_id in seen_ids:
            raise OAuthStorageError("Local credentials are invalid.")
        seen_ids.add(account_id)
        for field in ("label", "subject", "client_id"):
            if not isinstance(account.get(field), str):
                raise OAuthStorageError("Local credentials are invalid.")
        if not isinstance(account.get("email", ""), str):
            raise OAuthStorageError("Local credentials are invalid.")
        for field in ("access_token", "refresh_token", "id_token", "token_type"):
            if field in account and not isinstance(account[field], str):
                raise OAuthStorageError("Local credentials are invalid.")
        scopes = account.get("scopes", [])
        if not isinstance(scopes, list) or any(not isinstance(scope, str) for scope in scopes):
            raise OAuthStorageError("Local credentials are invalid.")
    return {
        "schema": _SCHEMA,
        "host_id": host_id,
        "next_label_number": next_label_number,
        "accounts": accounts,
    }


def _lock_file(handle: Any) -> None:
    deadline = time.monotonic() + _LOCK_WAIT_SECONDS
    while True:
        handle.seek(0)
        try:
            if os.name == "nt":
                import msvcrt

                msvcrt.locking(handle.fileno(), msvcrt.LK_NBLCK, 1)
            else:
                import fcntl

                fcntl.flock(handle.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
            return
        except OSError as exc:
            contention_errnos = {
                errno.EACCES,
                errno.EAGAIN,
                getattr(errno, "EDEADLK", errno.EACCES),
                getattr(errno, "EDEADLOCK", errno.EACCES),
            }
            winerror = getattr(exc, "winerror", None)
            is_contention = winerror == 33 if os.name == "nt" and winerror is not None else exc.errno in contention_errnos
            if not is_contention:
                raise
            if time.monotonic() >= deadline:
                raise OAuthStorageError("The local credential store is busy.") from None
            time.sleep(min(0.05, max(0.0, deadline - time.monotonic())))


def _unlock_file(handle: Any) -> None:
    handle.seek(0)
    if os.name == "nt":
        import msvcrt

        msvcrt.locking(handle.fileno(), msvcrt.LK_UNLCK, 1)
    else:
        import fcntl

        fcntl.flock(handle.fileno(), fcntl.LOCK_UN)


class _DataBlob(ctypes.Structure):
    _fields_ = [("cbData", ctypes.c_uint32), ("pbData", ctypes.POINTER(ctypes.c_ubyte))]


def _dpapi_transform(data: bytes, *, protect: bool) -> bytes:
    if os.name != "nt":
        raise OAuthStorageError("Windows user protection is unavailable.")
    from ctypes import wintypes

    crypt32 = ctypes.WinDLL("crypt32", use_last_error=True)
    kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
    blob_pointer = ctypes.POINTER(_DataBlob)
    crypt32.CryptProtectData.argtypes = [
        blob_pointer, wintypes.LPCWSTR, blob_pointer, wintypes.LPVOID,
        wintypes.LPVOID, wintypes.DWORD, blob_pointer,
    ]
    crypt32.CryptProtectData.restype = wintypes.BOOL
    crypt32.CryptUnprotectData.argtypes = [
        blob_pointer, ctypes.POINTER(wintypes.LPWSTR), blob_pointer,
        wintypes.LPVOID, wintypes.LPVOID, wintypes.DWORD, blob_pointer,
    ]
    crypt32.CryptUnprotectData.restype = wintypes.BOOL
    kernel32.LocalFree.argtypes = [wintypes.HLOCAL]
    kernel32.LocalFree.restype = wintypes.HLOCAL
    source_buffer = ctypes.create_string_buffer(data, len(data))
    source = _DataBlob(len(data), ctypes.cast(source_buffer, ctypes.POINTER(ctypes.c_ubyte)))
    destination = _DataBlob()
    if protect:
        result = crypt32.CryptProtectData(
            ctypes.byref(source), "VRCForge ChatGPT credentials", None, None, None,
            0x1, ctypes.byref(destination),
        )
    else:
        description = wintypes.LPWSTR()
        result = crypt32.CryptUnprotectData(
            ctypes.byref(source), ctypes.byref(description), None, None, None,
            0x1, ctypes.byref(destination),
        )
    if not result:
        raise OAuthStorageError("Windows user protection failed.")
    try:
        return ctypes.string_at(destination.pbData, destination.cbData)
    finally:
        kernel32.LocalFree(destination.pbData)
        if not protect and description:
            kernel32.LocalFree(description)


def _dpapi_protect(data: bytes) -> bytes:
    return _dpapi_transform(data, protect=True)


def _dpapi_unprotect(data: bytes) -> bytes:
    return _dpapi_transform(data, protect=False)


_LOCAL_LOCKS_GUARD = threading.Lock()
_LOCAL_LOCKS: dict[str, threading.RLock] = {}
