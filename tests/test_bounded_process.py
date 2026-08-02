from __future__ import annotations

import os
from pathlib import Path
import subprocess
import sys

import pytest

from bounded_process import run_bounded_process


def test_bounded_process_caps_each_output_stream(tmp_path: Path) -> None:
    result = run_bounded_process(
        [sys.executable, "-c", "import sys;sys.stdout.write('A'*5000);sys.stderr.write('B'*4000)"],
        cwd=str(tmp_path),
        env={"SystemRoot": os.environ.get("SystemRoot", "")},
        timeout_seconds=10,
        max_output_bytes=1024,
        creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
    )
    assert result.returncode == 0
    assert result.stdout == "A" * 1024
    assert result.stderr == "B" * 1024
    assert result.stdout_truncated is True
    assert result.stderr_truncated is True


def test_bounded_process_kills_owned_child_on_timeout(tmp_path: Path) -> None:
    with pytest.raises(subprocess.TimeoutExpired):
        run_bounded_process(
            [sys.executable, "-c", "import time;time.sleep(10)"],
            cwd=str(tmp_path),
            env={"SystemRoot": os.environ.get("SystemRoot", "")},
            timeout_seconds=1,
            max_output_bytes=1024,
            creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
        )
