from __future__ import annotations

import os
import threading
from pathlib import Path
from typing import BinaryIO, Callable


class BackendOwnerLease:
    """Process-lifetime ownership for one VRCForge user-data runtime.

    The lock file is intentionally retained after release. The operating
    system owns lock lifetime through the open file descriptor, so a crashed
    backend cannot leave a stale logical owner behind.
    """

    def __init__(self, path: Path | Callable[[], Path]) -> None:
        self._path_provider = path if callable(path) else lambda: path
        self._handle: BinaryIO | None = None
        self._owned_path: Path | None = None
        self._lock = threading.Lock()

    @property
    def owned(self) -> bool:
        with self._lock:
            return self._handle is not None

    @property
    def path(self) -> Path:
        with self._lock:
            return self._owned_path or Path(self._path_provider())

    def acquire(self) -> bool:
        with self._lock:
            if self._handle is not None:
                return True
            path = Path(self._path_provider())
            handle: BinaryIO | None = None
            try:
                path.parent.mkdir(parents=True, exist_ok=True)
                handle = path.open("a+b")
                handle.seek(0, os.SEEK_END)
                if handle.tell() == 0:
                    handle.write(b"\0")
                    handle.flush()
                    os.fsync(handle.fileno())
                handle.seek(0)
                self._lock_handle(handle)
            except OSError:
                try:
                    if handle is not None:
                        handle.close()
                except OSError:
                    pass
                return False
            assert handle is not None
            self._handle = handle
            self._owned_path = path
            return True

    def release(self) -> bool:
        with self._lock:
            handle = self._handle
            if handle is None:
                return False
            self._handle = None
            self._owned_path = None
            try:
                self._unlock_handle(handle)
            except OSError:
                # Closing the descriptor still releases an OS-owned lock.
                pass
            finally:
                try:
                    handle.close()
                except OSError:
                    pass
            return True

    @staticmethod
    def _lock_handle(handle: BinaryIO) -> None:
        handle.seek(0)
        if os.name == "nt":
            import msvcrt

            msvcrt.locking(handle.fileno(), msvcrt.LK_NBLCK, 1)
            return
        import fcntl

        fcntl.flock(handle.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)

    @staticmethod
    def _unlock_handle(handle: BinaryIO) -> None:
        handle.seek(0)
        if os.name == "nt":
            import msvcrt

            msvcrt.locking(handle.fileno(), msvcrt.LK_UNLCK, 1)
            return
        import fcntl

        fcntl.flock(handle.fileno(), fcntl.LOCK_UN)
