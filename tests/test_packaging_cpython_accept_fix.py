"""Windows end-to-end regression for CPython gh-93821 backport."""
from __future__ import annotations

import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import sysconfig
import tempfile
import unittest


@unittest.skipUnless(os.name == "nt", "the regression is specific to Windows IOCP")
class ProactorAcceptResetRegression(unittest.TestCase):
    def test_listener_survives_reset_with_packaged_asyncio(self) -> None:
        root = Path(__file__).resolve().parents[1]
        stdlib_asyncio = Path(sysconfig.get_path("stdlib")) / "asyncio"
        repro = root / "tests" / "fixtures" / "asyncio_accept_reset.py"

        with tempfile.TemporaryDirectory(prefix="vrcforge-asyncio-regression-") as temp:
            temp_root = Path(temp)
            baseline = temp_root / "baseline" / "asyncio"
            shutil.copytree(stdlib_asyncio, baseline)

            old = self._run_repro(repro, baseline.parent)
            self.assertFalse(old["listenerSurvivesReset"], old)
            self.assertTrue(any(error["winerror"] == 64 for error in old["errors"]), old)
            self.assertTrue(
                any(error["message"] == "Accept failed on a socket" for error in old["errors"]),
                old,
            )

            backend = temp_root / "backend"
            asyncio_dir = backend / "_internal" / "asyncio"
            shutil.copytree(baseline, asyncio_dir)
            patcher = root / "packaging" / "apply_cpython_gh93821.py"
            subprocess.run(
                [sys.executable, str(patcher), "--backend", str(backend)],
                check=True,
            )

            fixed = self._run_repro(repro, asyncio_dir.parent)
            self.assertTrue(fixed["listenerSurvivesReset"], fixed)
            self.assertFalse(
                any(error["message"] == "Accept failed on a socket" for error in fixed["errors"]),
                fixed,
            )

    @staticmethod
    def _run_repro(repro: Path, asyncio_parent: Path) -> dict:
        result = subprocess.run(
            [sys.executable, str(repro), str(asyncio_parent)],
            capture_output=True, text=True, timeout=20,
        )
        if result.returncode:
            raise AssertionError(f"repro failed: {result.stderr}")
        return json.loads(result.stdout.strip().splitlines()[-1])


if __name__ == "__main__":
    unittest.main()
