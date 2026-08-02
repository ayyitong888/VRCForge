"""Single-owner subprocess execution with bounded captured output.

The caller supplies the exact executable, argv, cwd, environment, timeout and
Windows creation flags.  This module owns the child and both pipe handles until
exit; on timeout it kills the child before returning control.
"""

from __future__ import annotations

from dataclasses import dataclass
import subprocess
from threading import Thread
from typing import Mapping, Sequence


@dataclass(frozen=True)
class BoundedProcessResult:
    returncode: int
    stdout: str
    stderr: str
    stdout_truncated: bool
    stderr_truncated: bool


def _drain_tail(stream: object, limit: int, output: dict[str, object], key: str) -> None:
    tail = bytearray()
    truncated = False
    try:
        while True:
            chunk = stream.read(65536)  # type: ignore[attr-defined]
            if not chunk:
                break
            if len(chunk) >= limit:
                tail[:] = chunk[-limit:]
                truncated = True
            else:
                overflow = len(tail) + len(chunk) - limit
                if overflow > 0:
                    del tail[:overflow]
                    truncated = True
                tail.extend(chunk)
    finally:
        stream.close()  # type: ignore[attr-defined]
    output[key] = bytes(tail)
    output[f"{key}_truncated"] = truncated


def run_bounded_process(
    argv: Sequence[str],
    *,
    cwd: str,
    env: Mapping[str, str],
    timeout_seconds: int,
    max_output_bytes: int = 2 * 1024 * 1024,
    creationflags: int = 0,
) -> BoundedProcessResult:
    if not argv or not all(isinstance(item, str) and item for item in argv):
        raise ValueError("Bounded process argv is invalid.")
    if timeout_seconds <= 0 or max_output_bytes <= 0:
        raise ValueError("Bounded process limits are invalid.")
    process = subprocess.Popen(
        list(argv),
        cwd=cwd,
        env=dict(env),
        stdin=subprocess.DEVNULL,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        shell=False,
        creationflags=creationflags,
    )
    assert process.stdout is not None and process.stderr is not None
    captured: dict[str, object] = {}
    stdout_thread = Thread(
        target=_drain_tail,
        args=(process.stdout, max_output_bytes, captured, "stdout"),
        name="vrcforge-bounded-stdout",
        daemon=True,
    )
    stderr_thread = Thread(
        target=_drain_tail,
        args=(process.stderr, max_output_bytes, captured, "stderr"),
        name="vrcforge-bounded-stderr",
        daemon=True,
    )
    stdout_thread.start()
    stderr_thread.start()
    try:
        returncode = process.wait(timeout=timeout_seconds)
    except subprocess.TimeoutExpired as exc:
        process.kill()
        process.wait()
        stdout_thread.join()
        stderr_thread.join()
        raise subprocess.TimeoutExpired(
            list(argv),
            timeout_seconds,
            output=captured.get("stdout", b""),
            stderr=captured.get("stderr", b""),
        ) from exc
    stdout_thread.join()
    stderr_thread.join()
    return BoundedProcessResult(
        returncode=returncode,
        stdout=bytes(captured.get("stdout", b"")).decode("utf-8", errors="replace"),
        stderr=bytes(captured.get("stderr", b"")).decode("utf-8", errors="replace"),
        stdout_truncated=bool(captured.get("stdout_truncated")),
        stderr_truncated=bool(captured.get("stderr_truncated")),
    )
