"""Append a tar.zst application payload to the self-drawn Windows setup.

Only packaging is performed. This script never installs the application, edits
the registry, or reads a user's VRCForge data.
"""
from __future__ import annotations

import argparse
from compression import zstd
import json
from pathlib import Path
import shutil
import struct
import tarfile
import tempfile

MAGIC = b"VRCFORGE_SETUP_PAYLOAD_V1"
REMOTE_MAGIC = b"VRCFORGE_SETUP_REMOTE__V1"
FOOTER = struct.Struct("<QQ")


def executable_size(path: Path) -> int:
    total = path.stat().st_size
    footer_bytes = FOOTER.size + len(MAGIC)
    if total >= footer_bytes:
        with path.open("rb") as stream:
            stream.seek(-footer_bytes, 2)
            lengths, marker = stream.read(FOOTER.size), stream.read(len(MAGIC))
        if marker in (MAGIC, REMOTE_MAGIC):
            payload_bytes, _ = FOOTER.unpack(lengths)
            native_bytes = total - footer_bytes - payload_bytes
            if native_bytes <= 0:
                raise ValueError("Existing setup payload length is invalid")
            return native_bytes
    return total


def build(app_dir: Path, setup_exe: Path, output: Path, *,
          payload_output: Path | None = None, web_output: Path | None = None,
          web_url: str | None = None) -> dict[str, int | str]:
    app_dir, setup_exe, output = app_dir.resolve(), setup_exe.resolve(), output.resolve()
    if not (app_dir / "VRCForge.exe").is_file():
        raise ValueError(f"App directory must contain VRCForge.exe: {app_dir}")
    if output == setup_exe or output.is_relative_to(app_dir):
        raise ValueError("Output must be separate from the source setup and application directory")
    if bool(web_output) != bool(web_url) or (web_output and not payload_output):
        raise ValueError("A web installer requires --web-url, --web-output and --payload-output")
    extra_outputs = [path.resolve() for path in (payload_output, web_output) if path]
    if len(set([output, *extra_outputs])) != 1 + len(extra_outputs):
        raise ValueError("Installer and payload outputs must have distinct filenames")
    if any(path == setup_exe or path.is_relative_to(app_dir) for path in extra_outputs):
        raise ValueError("Outputs must be separate from the source setup and application directory")
    files = sorted(app_dir.rglob("*"))
    if any(path.is_symlink() or path.is_junction() for path in files):
        raise ValueError("Application payload must contain files and directories, not links")
    app_bytes = sum(path.stat().st_size for path in files if path.is_file())
    native_bytes = executable_size(setup_exe)
    output.parent.mkdir(parents=True, exist_ok=True)
    # The temporary archive belongs only to this build and is deleted on success
    # or failure. Stream it instead of keeping the packaged app in memory.
    with tempfile.TemporaryDirectory(prefix="vrcforge-setup-payload-") as temporary:
        archive = Path(temporary) / "payload.tar.zst"
        with zstd.ZstdFile(archive, "wb", level=10) as compressed:
            with tarfile.open(fileobj=compressed, mode="w|", format=tarfile.PAX_FORMAT) as tar:
                for path in files:
                    tar.add(path, arcname=path.relative_to(app_dir).as_posix(), recursive=False)
        payload_bytes = archive.stat().st_size
        created_paths: list[Path] = []
        try:
            result = output.open("xb")
            created_paths.append(output)
            with result, setup_exe.open("rb") as source:
                left = native_bytes
                while left:
                    chunk = source.read(min(left, 1024 * 1024))
                    if not chunk:
                        raise ValueError("Source setup ended before its executable prefix")
                    result.write(chunk)
                    left -= len(chunk)
                with archive.open("rb") as payload:
                    shutil.copyfileobj(payload, result, 1024 * 1024)
                result.write(FOOTER.pack(payload_bytes, app_bytes))
                result.write(MAGIC)
            if payload_output:
                payload_output = payload_output.resolve()
                payload_output.parent.mkdir(parents=True, exist_ok=True)
                destination = payload_output.open("xb")
                created_paths.append(payload_output)
                with archive.open("rb") as source, destination:
                    shutil.copyfileobj(source, destination, 1024 * 1024)
            if web_output:
                web_output = web_output.resolve()
                web_output.parent.mkdir(parents=True, exist_ok=True)
                metadata = json.dumps({"url": web_url, "payloadBytes": payload_bytes,
                                       "appBytes": app_bytes}, separators=(",", ":")).encode("utf-8")
                destination = web_output.open("xb")
                created_paths.append(web_output)
                with setup_exe.open("rb") as source, destination:
                    left = native_bytes
                    while left:
                        chunk = source.read(min(left, 1024 * 1024))
                        if not chunk:
                            raise ValueError("Source setup ended before its executable prefix")
                        destination.write(chunk)
                        left -= len(chunk)
                    destination.write(metadata)
                    destination.write(FOOTER.pack(len(metadata), app_bytes))
                    destination.write(REMOTE_MAGIC)
        except BaseException:
            # Only remove the new output this call created, never a preexisting
            # release file (xb above rejects that case).
            for path in created_paths:
                if path.exists():
                    path.unlink()
            raise
    return {"output": str(output), "appBytes": app_bytes, "payloadBytes": payload_bytes,
            "executableBytes": native_bytes, "requiredBytes": app_bytes + native_bytes}


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--app-dir", type=Path, required=True)
    parser.add_argument("--setup-exe", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--payload-output", type=Path)
    parser.add_argument("--web-output", type=Path)
    parser.add_argument("--web-url")
    args = parser.parse_args()
    print(json.dumps(build(args.app_dir, args.setup_exe, args.output,
                           payload_output=args.payload_output, web_output=args.web_output,
                           web_url=args.web_url), ensure_ascii=False))


if __name__ == "__main__":
    main()
