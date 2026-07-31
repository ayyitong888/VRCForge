from __future__ import annotations

import base64
import hashlib
import importlib.metadata
import json
import os
import stat
import sys
from pathlib import Path
from typing import Any

import PyInstaller


EXPECTED_PYINSTALLER_VERSION = "6.19.0"
_FILE_ATTRIBUTE_REPARSE_POINT = 0x400


class BridgeTargetPackagerProbeError(RuntimeError):
    """Raised when a fixed packager input is not the expected immutable file."""


_REPOSITORY_ROOT = Path(__file__).resolve().parents[1]
if str(_REPOSITORY_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPOSITORY_ROOT))

import primitive_bridge_target_adapter as adapter


def _identity(metadata: os.stat_result) -> tuple[int, int, int, int, int, int]:
    return (
        int(metadata.st_dev),
        int(metadata.st_ino),
        int(metadata.st_size),
        int(metadata.st_mtime_ns),
        int(metadata.st_nlink),
        int(getattr(metadata, "st_file_attributes", 0) or 0),
    )


def _read_stable_regular_file(path: Path) -> tuple[Path, bytes]:
    before_path = path.lstat()
    if (
        not stat.S_ISREG(before_path.st_mode)
        or before_path.st_nlink != 1
        or int(getattr(before_path, "st_file_attributes", 0) or 0)
        & _FILE_ATTRIBUTE_REPARSE_POINT
    ):
        raise BridgeTargetPackagerProbeError("A fixed packager input is invalid.")

    resolved = path.resolve(strict=True)
    flags = os.O_RDONLY | getattr(os, "O_BINARY", 0) | getattr(os, "O_NOINHERIT", 0)
    flags |= getattr(os, "O_NOFOLLOW", 0)
    descriptor = os.open(resolved, flags)
    try:
        before_open = os.fstat(descriptor)
        chunks: list[bytes] = []
        while chunk := os.read(descriptor, 1024 * 1024):
            chunks.append(chunk)
        after_open = os.fstat(descriptor)
    finally:
        os.close(descriptor)

    after_path = resolved.lstat()
    identities = {
        _identity(before_path),
        _identity(before_open),
        _identity(after_open),
        _identity(after_path),
    }
    if len(identities) != 1:
        raise BridgeTargetPackagerProbeError("A fixed packager input changed during inspection.")
    return resolved, b"".join(chunks)


def _decode_record_digest(record_hash: Any) -> bytes:
    if record_hash is None or getattr(record_hash, "mode", None) != "sha256":
        raise BridgeTargetPackagerProbeError("The fixed connector record is invalid.")
    encoded = str(getattr(record_hash, "value", ""))
    padding = "=" * (-len(encoded) % 4)
    try:
        return base64.urlsafe_b64decode(encoded + padding)
    except Exception as exc:
        raise BridgeTargetPackagerProbeError(
            "The fixed connector record is invalid."
        ) from exc


def inspect_fixed_packager_inputs() -> dict[str, object]:
    if PyInstaller.__version__ != EXPECTED_PYINSTALLER_VERSION:
        raise BridgeTargetPackagerProbeError("The fixed packager version is invalid.")
    if adapter.FIXED_CONNECTOR_MODULE in sys.modules:
        raise BridgeTargetPackagerProbeError(
            "The fixed connector loaded before its package identity was inspected."
        )

    _read_stable_regular_file(_REPOSITORY_ROOT / "primitive_bridge_target_entry.py")
    _read_stable_regular_file(_REPOSITORY_ROOT / "primitive_bridge_target_adapter.py")

    distribution = importlib.metadata.distribution(
        adapter.FIXED_CONNECTOR_DISTRIBUTION
    )
    if distribution.version != adapter.FIXED_CONNECTOR_VERSION:
        raise BridgeTargetPackagerProbeError("The fixed connector version is invalid.")
    matches = [
        item
        for item in distribution.files or ()
        if str(item).replace("\\", "/") == f"{adapter.FIXED_CONNECTOR_MODULE}.py"
    ]
    if len(matches) != 1:
        raise BridgeTargetPackagerProbeError("The fixed connector record is invalid.")

    record = matches[0]
    record_digest = _decode_record_digest(getattr(record, "hash", None))
    connector_source, source_bytes = _read_stable_regular_file(
        Path(distribution.locate_file(record))
    )
    if (
        getattr(record, "size", None) != adapter.FIXED_CONNECTOR_MODULE_BYTES
        or len(source_bytes) != adapter.FIXED_CONNECTOR_MODULE_BYTES
        or record_digest != adapter.FIXED_CONNECTOR_MODULE_SHA256
        or hashlib.sha256(source_bytes).digest()
        != adapter.FIXED_CONNECTOR_MODULE_SHA256
    ):
        raise BridgeTargetPackagerProbeError("The fixed connector identity is invalid.")

    return {
        "ok": True,
        "schema": "vrcforge.bridge_target_packager_probe.v1",
        "packagerVersion": EXPECTED_PYINSTALLER_VERSION,
        "distribution": adapter.FIXED_CONNECTOR_DISTRIBUTION,
        "connectorVersion": adapter.FIXED_CONNECTOR_VERSION,
        "module": adapter.FIXED_CONNECTOR_MODULE,
        "connectorSource": str(connector_source),
    }


def main() -> int:
    try:
        payload = inspect_fixed_packager_inputs()
    except Exception:
        print("The fixed bridge packager input probe was rejected.", file=sys.stderr)
        return 1
    print(json.dumps(payload, ensure_ascii=True, separators=(",", ":")))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
