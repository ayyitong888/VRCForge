"""Private stdio worker that creates one Windows ConPTY child.

The backend starts this worker suspended, assigns it to the session Job, and
only then resumes it. The ConPTY child therefore inherits the already-bound
session lifetime instead of briefly running outside it.
"""

from __future__ import annotations

import codecs
import json
import os
import sys
import threading
from typing import Any


def _read_config() -> tuple[list[str], str]:
    raw = bytearray()
    while len(raw) <= 64 * 1024:
        chunk = os.read(sys.stdin.fileno(), 1)
        if not chunk:
            raise ValueError("PTY worker config is missing.")
        if chunk == b"\n":
            break
        raw.extend(chunk)
    else:
        raise ValueError("PTY worker config is too large.")
    payload = json.loads(raw.decode("utf-8"))
    argv = payload.get("argv")
    cwd = str(payload.get("cwd") or "").strip()
    if not isinstance(argv, list) or not argv or not all(isinstance(item, str) and item for item in argv):
        raise ValueError("PTY worker argv is invalid.")
    if not cwd or not os.path.isdir(cwd):
        raise ValueError("PTY worker cwd is invalid.")
    return list(argv), cwd


def run_worker() -> int:
    from winpty import PtyProcess

    argv, cwd = _read_config()
    process: Any = PtyProcess.spawn(argv, cwd=cwd, env=dict(os.environ))
    stop = threading.Event()

    def forward_input() -> None:
        decoder = codecs.getincrementaldecoder("utf-8")(errors="replace")
        try:
            while not stop.is_set() and process.isalive():
                raw = os.read(sys.stdin.fileno(), 1024)
                if not raw:
                    final_chunk = decoder.decode(b"", final=True)
                    if final_chunk:
                        process.write(final_chunk)
                    return
                chunk = decoder.decode(raw, final=False)
                if not chunk:
                    continue
                process.write(chunk)
        except (EOFError, OSError, RuntimeError):
            return

    threading.Thread(
        target=forward_input,
        name="vrcforge-shell-pty-input",
        daemon=True,
    ).start()
    try:
        while process.isalive():
            try:
                chunk = str(process.read(4096) or "")
            except EOFError:
                break
            if chunk:
                sys.stdout.write(chunk)
                sys.stdout.flush()
        return int(process.exitstatus or 0)
    finally:
        stop.set()
        try:
            process.close(force=True)
        except (OSError, RuntimeError):
            pass


if __name__ == "__main__":
    try:
        raise SystemExit(run_worker())
    except Exception as exc:  # noqa: BLE001 - parent receives only a bounded error type.
        print(f"PTY worker failed: {type(exc).__name__}", file=sys.stderr, flush=True)
        raise SystemExit(1)
