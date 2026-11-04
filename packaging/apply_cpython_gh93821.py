"""Apply CPython gh-93821 to the bundled Windows asyncio runtime.

STOPGAP: the build Python is 3.14.3 and predates the upstream fix. Removal target:
VRCForge 1.10.5, once its builder adopts a CPython release containing c99e750.
"""
from __future__ import annotations

import argparse
import os
from pathlib import Path
import py_compile
import shutil
import subprocess
import sys
import sysconfig
import tempfile

COMMIT = "c99e750c6171b49091c807435681fa58f1c4d04b"
FILES = ("proactor_events.py", "windows_events.py")


def patch_asyncio(asyncio_dir: Path) -> None:
    if sys.version_info[:2] != (3, 14):
        raise RuntimeError(f"CPython 3.14 build interpreter required, got {sys.version.split()[0]}")
    if os.name != "nt":
        raise RuntimeError("This Windows asyncio backport must be built on Windows.")
    missing = [name for name in FILES if not (asyncio_dir / name).is_file()]
    if missing:
        raise FileNotFoundError(f"Missing asyncio source files in {asyncio_dir}: {missing}")

    patch_file = Path(__file__).resolve().parent / "patches" / "cpython-gh-93821.patch"
    with tempfile.TemporaryDirectory(prefix="vrcforge-cpython-93821-") as temp_name:
        staged = Path(temp_name) / "Lib" / "asyncio"
        staged.mkdir(parents=True)
        for name in FILES:
            shutil.copy2(asyncio_dir / name, staged / name)

        applied = subprocess.run(
            ["git", "apply", "--check", str(patch_file)],
            cwd=staged.parents[1], capture_output=True, text=True,
        )
        if applied.returncode == 0:
            subprocess.run(
                ["git", "apply", str(patch_file)],
                cwd=staged.parents[1], check=True,
            )
        else:
            already = subprocess.run(
                ["git", "apply", "--reverse", "--check", str(patch_file)],
                cwd=staged.parents[1], capture_output=True, text=True,
            )
            if already.returncode != 0:
                raise RuntimeError(
                    f"CPython {COMMIT} patch does not match source: {applied.stderr.strip()}"
                )
            print(f"CPython {COMMIT} fix is already present in {asyncio_dir}")

        for name in FILES:
            destination = asyncio_dir / name
            shutil.copy2(staged / name, destination)
            py_compile.compile(
                str(destination), cfile=str(destination.with_suffix(".pyc")),
                dfile=f"asyncio/{name}",
                doraise=True,
            )


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--backend", required=True, type=Path,
        help="backend root; its _internal/asyncio directory is patched",
    )
    args = parser.parse_args()
    backend = args.backend.resolve()
    asyncio_dir = backend / "_internal" / "asyncio"
    asyncio_dir.mkdir(parents=True, exist_ok=True)
    stdlib_asyncio = Path(sysconfig.get_path("stdlib")) / "asyncio"
    for name in FILES:
        destination = asyncio_dir / name
        if not destination.exists():
            shutil.copy2(stdlib_asyncio / name, destination)
    patch_asyncio(asyncio_dir)


if __name__ == "__main__":
    main()
