from __future__ import annotations

import subprocess
import sys
from pathlib import Path

from backend_owner_lease import BackendOwnerLease


def test_backend_owner_lease_is_idempotent_and_reusable(tmp_path: Path) -> None:
    path = tmp_path / "agent-gateway" / "backend-owner.lock"
    lease = BackendOwnerLease(path)

    assert lease.acquire() is True
    assert lease.acquire() is True
    assert lease.owned is True
    assert lease.path == path
    assert lease.release() is True
    assert lease.release() is False
    assert lease.owned is False

    reopened = BackendOwnerLease(path)
    assert reopened.acquire() is True
    assert reopened.release() is True


def test_backend_owner_lease_excludes_an_independent_python_process(tmp_path: Path) -> None:
    path = tmp_path / "agent-gateway" / "backend-owner.lock"
    script = "\n".join(
        (
            "import sys",
            "from pathlib import Path",
            "from backend_owner_lease import BackendOwnerLease",
            f"lease = BackendOwnerLease(Path({str(path)!r}))",
            "print('READY' if lease.acquire() else 'FAILED', flush=True)",
            "sys.stdin.readline()",
            "lease.release()",
        )
    )
    child = subprocess.Popen(
        [sys.executable, "-c", script],
        cwd=Path(__file__).resolve().parents[1],
        stdin=subprocess.PIPE,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
    )
    try:
        assert child.stdout is not None
        assert child.stdout.readline().strip() == "READY"
        contender = BackendOwnerLease(path)
        assert contender.acquire() is False
        assert contender.owned is False
    finally:
        if child.stdin is not None:
            child.stdin.write("release\n")
            child.stdin.flush()
            child.stdin.close()
        try:
            child.wait(timeout=10)
        except subprocess.TimeoutExpired:
            child.kill()
            child.wait(timeout=5)

    assert child.returncode == 0, child.stderr.read() if child.stderr is not None else ""
    successor = BackendOwnerLease(path)
    assert successor.acquire() is True
    assert successor.release() is True
