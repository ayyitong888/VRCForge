"""Windows sharing-violation retries for atomic file replaces."""
import os
import sys
from pathlib import Path
from unittest import mock

import pytest
from atomic_file_replace import replace_with_retry


def _winerror(code):
    error = OSError("detinated")
    error.winerror = code
    return error


def test_retries_transient_sharing_violations_then_succeeds(tmp_path: Path):
    source, target = tmp_path / "s.json", tmp_path / "t.json"
    source.write_text("{}", encoding="utf-8")
    real_replace = os.replace
    calls = {"n": 0}

    def flaky(src, dst):
        calls["n"] += 1
        if calls["n"] <= 2:
            raise _winerror(5)
        real_replace(src, dst)

    sleeps: list[float] = []
    with mock.patch.object(os, "replace", flaky), mock.patch("time.sleep", sleeps.append):
        replace_with_retry(source, target)
    assert calls["n"] == 3
    assert target.read_text(encoding="utf-8") == "{}"
    assert len(sleeps) == 2


def test_non_sharing_errors_raise_immediately(tmp_path: Path):
    source, target = tmp_path / "s.json", tmp_path / "t.json"
    source.write_text("{}", encoding="utf-8")
    with mock.patch.object(os, "replace", mock.Mock(side_effect=_winerror(2))):
        with pytest.raises(OSError):
            replace_with_retry(source, target)


def test_exhausted_retries_raise_the_last_sharing_error(tmp_path: Path):
    source, target = tmp_path / "s.json", tmp_path / "t.json"
    source.write_text("{}", encoding="utf-8")
    calls = {"n": 0}

    def always_busy(_src, _dst):
        calls["n"] += 1
        raise _winerror(32)

    with mock.patch.object(os, "replace", always_busy), mock.patch("time.sleep", lambda _s: None):
        with pytest.raises(OSError) as excinfo:
            replace_with_retry(source, target)
    assert getattr(excinfo.value, "winerror", None) == 32
    assert calls["n"] == 5  # 1 initial + 4 retries
