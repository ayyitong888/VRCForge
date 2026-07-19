"""Content-addressed local vault for large chat attachment bodies (1.3.2).

聊天附件的二进制体不进 prompt、不进 transcript：前端通过有界分块上传，
字节以内容寻址方式落到本模块管理的磁盘文件库；durable message 只保留
metadata + ``payloadHash``。

安全与生命周期规则：
- 显式格式白名单 + 魔数嗅探，扩展名与魔数不一致一律拒绝，绝不猜测；
- 逐格式上限：zip/unitypackage 512MB，图片 64MB；
- per-chat 配额 + LRU 清理，活引用（仍被 durable transcript 引用）永不清；
- 归档检查有界：条目数上限、总解压体积上限、压缩比炸弹守卫、zip-slip 拒绝；
- 重启后从 index.json 重建（文件缺失的条目被丢弃）。
"""

from __future__ import annotations

import hashlib
import json
import os
import re
import stat
import struct
import tarfile
import threading
import time
import zipfile
from pathlib import Path
from typing import Any

VAULT_SCHEMA = "vrcforge.chat_attachment_vault.v1"
INSPECTION_SCHEMA = "vrcforge.chat_attachment_inspection.v1"

ARCHIVE_MAX_BYTES = 512 * 1024 * 1024
IMAGE_MAX_BYTES = 64 * 1024 * 1024
PER_CHAT_QUOTA_BYTES = 2 * 1024 * 1024 * 1024
GLOBAL_QUOTA_BYTES = 8 * 1024 * 1024 * 1024
# 新条目的孤儿宽限：上传后消息可能尚未持久化到 transcript，宽限期内不因
# "无活引用"被 retain/配额清理。
ORPHAN_GRACE_SECONDS = 6 * 60 * 60

INSPECT_MAX_ENTRIES = 10_000
INSPECT_TOTAL_UNCOMPRESSED_MAX_BYTES = 2 * 1024 * 1024 * 1024
INSPECT_BOMB_RATIO = 200.0
INSPECT_BOMB_MIN_UNCOMPRESSED_BYTES = 64 * 1024 * 1024
ENTRY_TEXT_MAX_BYTES = 64 * 1024
ZIP_CENTRAL_DIRECTORY_MAX_BYTES = 16 * 1024 * 1024

_MAGIC_ZIP = b"PK\x03\x04"
_MAGIC_GZIP = b"\x1f\x8b"
_MAGIC_PNG = b"\x89PNG\r\n\x1a\n"
_MAGIC_JPEG = b"\xff\xd8\xff"

_SHA256_RE = re.compile(r"^[0-9a-f]{64}$")

# 白名单：扩展名 -> (kind, category)。不在表里的格式由调用侧降级 metadata-only。
_EXTENSION_FORMATS: dict[str, tuple[str, str]] = {
    ".zip": ("zip", "archive"),
    ".unitypackage": ("unitypackage", "archive"),
    ".png": ("image_png", "image"),
    ".jpg": ("image_jpeg", "image"),
    ".jpeg": ("image_jpeg", "image"),
    ".webp": ("image_webp", "image"),
}


class ChatAttachmentVaultError(RuntimeError):
    """Vault 拒绝的原因携带机器可读 reason，调用侧据此做诚实降级。"""

    def __init__(self, message: str, *, reason: str, status_code: int = 400) -> None:
        super().__init__(message)
        self.reason = reason
        self.status_code = status_code


def classify_upload(name: str, head: bytes) -> dict[str, str]:
    """Resolve the allowlisted format for an upload; extension and magic must agree."""

    extension = Path(str(name or "")).suffix.lower()
    entry = _EXTENSION_FORMATS.get(extension)
    if entry is None:
        raise ChatAttachmentVaultError(
            f"Attachment format is not allowlisted for vault storage: {extension or '(no extension)'}",
            reason="format_not_allowed",
            status_code=415,
        )
    kind, category = entry
    if not _magic_matches(kind, head):
        raise ChatAttachmentVaultError(
            f"File content does not match the declared {extension} format (magic-byte mismatch).",
            reason="magic_mismatch",
            status_code=415,
        )
    return {"kind": kind, "category": category, "extension": extension}


def _magic_matches(kind: str, head: bytes) -> bool:
    head = bytes(head or b"")
    if kind == "zip":
        return head.startswith(_MAGIC_ZIP)
    if kind == "unitypackage":
        return head.startswith(_MAGIC_GZIP)
    if kind == "image_png":
        return head.startswith(_MAGIC_PNG)
    if kind == "image_jpeg":
        return head.startswith(_MAGIC_JPEG)
    if kind == "image_webp":
        return len(head) >= 12 and head[:4] == b"RIFF" and head[8:12] == b"WEBP"
    return False


def format_cap_bytes(category: str) -> int:
    return ARCHIVE_MAX_BYTES if category == "archive" else IMAGE_MAX_BYTES


def is_vault_payload_hash(value: str) -> bool:
    return bool(_SHA256_RE.match(str(value or "").strip().lower()))


class ChatAttachmentVault:
    """Content-addressed file store with a JSON reference index.

    文件名 ``{sha256}{extension}``：保留真实扩展名，下游按磁盘路径工作的管线
    （outfit import / inspector）无需感知 vault 的存在。
    """

    def __init__(self, root: Path) -> None:
        self.root = Path(root)
        self.files_dir = self.root / "files"
        self.index_path = self.root / "index.json"
        self._index: dict[str, dict[str, Any]] = {}
        self._loaded = False
        self._lock = threading.RLock()

    # ---- persistence -----------------------------------------------------

    def _load(self) -> None:
        if self._loaded:
            return
        self._loaded = True
        self._index = {}
        if not self.index_path.exists():
            return
        try:
            payload = json.loads(self.index_path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            return
        entries = payload.get("entries") if isinstance(payload, dict) else None
        if not isinstance(entries, dict):
            return
        for payload_hash, raw in entries.items():
            if not is_vault_payload_hash(payload_hash) or not isinstance(raw, dict):
                continue
            entry = self._normalize_entry(payload_hash, raw)
            if entry is None:
                continue
            # Restart rehydration:索引里有但磁盘文件已缺失的条目直接丢弃。
            if not self._file_path(payload_hash, entry["extension"]).is_file():
                continue
            self._index[payload_hash] = entry
        self._save()

    def _save(self) -> None:
        self.root.mkdir(parents=True, exist_ok=True)
        temp_path = self.index_path.with_name(
            f".{self.index_path.name}.{os.getpid()}.{threading.get_ident()}.{time.time_ns()}.tmp"
        )
        temp_path.write_text(
            json.dumps({"version": 1, "schema": VAULT_SCHEMA, "entries": self._index}, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )
        temp_path.replace(self.index_path)

    @staticmethod
    def _normalize_entry(payload_hash: str, raw: dict[str, Any]) -> dict[str, Any] | None:
        extension = str(raw.get("extension") or "").lower()
        entry_format = _EXTENSION_FORMATS.get(extension)
        if entry_format is None:
            return None
        try:
            size = max(0, int(raw.get("size") or 0))
        except (TypeError, ValueError):
            return None
        chats_raw = raw.get("chats")
        chats: dict[str, float] = {}
        if isinstance(chats_raw, dict):
            for chat_id, seen in chats_raw.items():
                key = str(chat_id or "").strip()
                if not key:
                    continue
                try:
                    chats[key] = float(seen)
                except (TypeError, ValueError):
                    chats[key] = 0.0
        live_chats_raw = raw.get("liveChats")
        live_chats: dict[str, float] = {}
        if isinstance(live_chats_raw, dict):
            for chat_id, seen in live_chats_raw.items():
                key = str(chat_id or "").strip()
                if key not in chats:
                    continue
                try:
                    live_chats[key] = float(seen)
                except (TypeError, ValueError):
                    live_chats[key] = 0.0
        return {
            "name": str(raw.get("name") or f"attachment{extension}"),
            "size": size,
            "type": str(raw.get("type") or "application/octet-stream"),
            "kind": entry_format[0],
            "category": entry_format[1],
            "extension": extension,
            "chats": chats,
            "liveChats": live_chats,
            "storedAt": float(raw.get("storedAt") or 0.0),
            "lastAccessAt": float(raw.get("lastAccessAt") or 0.0),
        }

    def _file_path(self, payload_hash: str, extension: str) -> Path:
        return self.files_dir / f"{payload_hash}{extension}"

    # ---- public API ------------------------------------------------------

    def ingest(self, *, data: bytes, name: str, declared_type: str, chat_id: str) -> dict[str, Any]:
        with self._lock:
            return self._ingest_locked(data=data, name=name, declared_type=declared_type, chat_id=chat_id)

    def ingest_file(self, *, source_path: Path, name: str, declared_type: str, chat_id: str) -> dict[str, Any]:
        """Stream a completed staged upload into the content-addressed vault."""

        with self._lock:
            return self._ingest_file_locked(
                source_path=Path(source_path),
                name=name,
                declared_type=declared_type,
                chat_id=chat_id,
            )

    def _ingest_file_locked(self, *, source_path: Path, name: str, declared_type: str, chat_id: str) -> dict[str, Any]:
        self._load()
        chat_key = str(chat_id or "").strip()
        if not chat_key:
            raise ChatAttachmentVaultError("chatId is required for vault ingestion.", reason="chat_id_required")
        try:
            size = source_path.stat().st_size
            with source_path.open("rb") as handle:
                head = handle.read(16)
                upload_format = classify_upload(name, head)
                cap = format_cap_bytes(upload_format["category"])
                if size > cap:
                    raise ChatAttachmentVaultError(
                        f"Attachment exceeds the {upload_format['category']} vault cap ({cap} bytes).",
                        reason="over_cap",
                        status_code=413,
                    )
                if size == 0:
                    raise ChatAttachmentVaultError("Empty attachment body.", reason="empty_body")
                digest = hashlib.sha256()
                digest.update(head)
                while chunk := handle.read(1024 * 1024):
                    digest.update(chunk)
        except ChatAttachmentVaultError:
            raise
        except OSError as exc:
            raise ChatAttachmentVaultError(
                "Staged chat attachment could not be read.",
                reason="staged_upload_unreadable",
                status_code=409,
            ) from exc
        payload_hash = digest.hexdigest()
        now = time.time()
        existing = self._index.get(payload_hash)
        if existing is not None:
            if chat_key not in existing["chats"]:
                self._enforce_chat_quota(chat_key, int(existing["size"]), now)
            existing["chats"][chat_key] = now
            existing["lastAccessAt"] = now
            source_path.unlink(missing_ok=True)
            self._save()
            return self.describe(payload_hash)
        self._enforce_chat_quota(chat_key, size, now)
        self._enforce_global_quota(size, now)
        self.files_dir.mkdir(parents=True, exist_ok=True)
        file_path = self._file_path(payload_hash, upload_format["extension"])
        source_path.replace(file_path)
        self._index[payload_hash] = {
            "name": str(name or f"attachment{upload_format['extension']}"),
            "size": size,
            "type": str(declared_type or "application/octet-stream"),
            "kind": upload_format["kind"],
            "category": upload_format["category"],
            "extension": upload_format["extension"],
            "chats": {chat_key: now},
            "liveChats": {},
            "storedAt": now,
            "lastAccessAt": now,
        }
        self._save()
        return self.describe(payload_hash)

    def _ingest_locked(self, *, data: bytes, name: str, declared_type: str, chat_id: str) -> dict[str, Any]:
        self._load()
        chat_key = str(chat_id or "").strip()
        if not chat_key:
            raise ChatAttachmentVaultError("chatId is required for vault ingestion.", reason="chat_id_required")
        upload_format = classify_upload(name, data[:16])
        cap = format_cap_bytes(upload_format["category"])
        if len(data) > cap:
            raise ChatAttachmentVaultError(
                f"Attachment exceeds the {upload_format['category']} vault cap ({cap} bytes).",
                reason="over_cap",
                status_code=413,
            )
        if len(data) == 0:
            raise ChatAttachmentVaultError("Empty attachment body.", reason="empty_body")
        payload_hash = hashlib.sha256(data).hexdigest()
        now = time.time()
        existing = self._index.get(payload_hash)
        if existing is not None:
            if chat_key not in existing["chats"]:
                self._enforce_chat_quota(chat_key, int(existing["size"]), now)
            existing["chats"][chat_key] = now
            existing["lastAccessAt"] = now
            self._save()
            return self.describe(payload_hash)
        self._enforce_chat_quota(chat_key, len(data), now)
        self._enforce_global_quota(len(data), now)
        self.files_dir.mkdir(parents=True, exist_ok=True)
        file_path = self._file_path(payload_hash, upload_format["extension"])
        temp_path = file_path.with_name(
            f".{file_path.name}.{os.getpid()}.{threading.get_ident()}.{time.time_ns()}.tmp"
        )
        temp_path.write_bytes(data)
        temp_path.replace(file_path)
        self._index[payload_hash] = {
            "name": str(name or f"attachment{upload_format['extension']}"),
            "size": len(data),
            "type": str(declared_type or "application/octet-stream"),
            "kind": upload_format["kind"],
            "category": upload_format["category"],
            "extension": upload_format["extension"],
            "chats": {chat_key: now},
            "liveChats": {},
            "storedAt": now,
            "lastAccessAt": now,
        }
        self._save()
        return self.describe(payload_hash)

    def _enforce_chat_quota(self, chat_key: str, incoming_bytes: int, now: float) -> None:
        usage = sum(entry["size"] for entry in self._index.values() if chat_key in entry["chats"])
        if usage + incoming_bytes <= PER_CHAT_QUOTA_BYTES:
            return
        # LRU 清理：先清全局孤儿（零引用且过宽限），再清只被本 chat 引用且过
        # 宽限的旧条目。活引用保护在 retain() 已经落实——索引里挂着 chat 引用
        # 的条目视为潜在活引用，只有本 chat 自己的旧条目可为配额让位。
        candidates = sorted(
            (
                (payload_hash, entry)
                for payload_hash, entry in self._index.items()
                if set(entry["chats"]) <= {chat_key}
                and not entry.get("liveChats")
                and now - float(entry["chats"].get(chat_key) or entry["storedAt"]) > ORPHAN_GRACE_SECONDS
            ),
            key=lambda item: item[1]["lastAccessAt"],
        )
        for payload_hash, entry in candidates:
            if usage + incoming_bytes <= PER_CHAT_QUOTA_BYTES:
                break
            self._delete_entry(payload_hash, entry)
            usage -= entry["size"]
        if usage + incoming_bytes > PER_CHAT_QUOTA_BYTES:
            raise ChatAttachmentVaultError(
                f"Per-chat attachment vault quota exceeded ({PER_CHAT_QUOTA_BYTES} bytes).",
                reason="quota_exceeded",
                status_code=413,
            )
        self._save()

    def _enforce_global_quota(self, incoming_bytes: int, now: float) -> None:
        usage = sum(int(entry["size"]) for entry in self._index.values())
        if usage + incoming_bytes <= GLOBAL_QUOTA_BYTES:
            return
        candidates = sorted(
            (
                (payload_hash, entry)
                for payload_hash, entry in self._index.items()
                if not entry.get("liveChats")
                and all(
                    now - float(last_seen or entry["storedAt"]) > ORPHAN_GRACE_SECONDS
                    for last_seen in entry["chats"].values()
                )
            ),
            key=lambda item: float(item[1]["lastAccessAt"]),
        )
        for payload_hash, entry in candidates:
            if usage + incoming_bytes <= GLOBAL_QUOTA_BYTES:
                break
            self._delete_entry(payload_hash, entry)
            usage -= int(entry["size"])
        if usage + incoming_bytes > GLOBAL_QUOTA_BYTES:
            raise ChatAttachmentVaultError(
                f"Global attachment vault quota exceeded ({GLOBAL_QUOTA_BYTES} bytes).",
                reason="global_quota_exceeded",
                status_code=413,
            )
        self._save()

    def _delete_entry(self, payload_hash: str, entry: dict[str, Any]) -> None:
        self._index.pop(payload_hash, None)
        try:
            self._file_path(payload_hash, entry["extension"]).unlink(missing_ok=True)
        except OSError:
            pass

    def resolve(self, payload_hash: str) -> dict[str, Any] | None:
        with self._lock:
            return self._resolve_locked(payload_hash)

    def _resolve_locked(self, payload_hash: str) -> dict[str, Any] | None:
        """Return entry metadata + on-disk path, or None if unknown/missing."""

        self._load()
        key = str(payload_hash or "").strip().lower()
        entry = self._index.get(key)
        if entry is None:
            return None
        path = self._file_path(key, entry["extension"])
        if not path.is_file():
            self._index.pop(key, None)
            self._save()
            return None
        digest = hashlib.sha256()
        size = 0
        try:
            with path.open("rb") as handle:
                while chunk := handle.read(1024 * 1024):
                    size += len(chunk)
                    digest.update(chunk)
        except OSError as exc:
            raise ChatAttachmentVaultError(
                "Chat attachment could not be verified.",
                reason="integrity_unreadable",
                status_code=409,
            ) from exc
        if size != int(entry["size"]) or digest.hexdigest() != key:
            self._delete_entry(key, entry)
            self._save()
            raise ChatAttachmentVaultError(
                "Chat attachment bytes no longer match the approved content hash.",
                reason="integrity_mismatch",
                status_code=409,
            )
        entry["lastAccessAt"] = time.time()
        return {**self.describe(key), "path": str(path)}

    def describe(self, payload_hash: str) -> dict[str, Any]:
        entry = self._index[payload_hash]
        return {
            "payloadHash": payload_hash,
            "name": entry["name"],
            "size": entry["size"],
            "type": entry["type"],
            "kind": entry["kind"],
            "category": entry["category"],
            "extension": entry["extension"],
            "chatCount": len(entry["chats"]),
            "storedAt": entry["storedAt"],
        }

    def retain(self, live_refs: dict[str, set[str]]) -> dict[str, int]:
        with self._lock:
            return self._retain_locked(live_refs)

    def _retain_locked(self, live_refs: dict[str, set[str]]) -> dict[str, int]:
        """Synchronize full-transcript live references without evicting them under quota."""

        self._load()
        now = time.time()
        normalized: dict[str, set[str]] = {}
        for chat_id, hashes in (live_refs or {}).items():
            key = str(chat_id or "").strip()
            if key:
                normalized[key] = {str(item or "").strip().lower() for item in hashes}
        dropped_refs = 0
        deleted_files = 0
        for payload_hash in list(self._index.keys()):
            entry = self._index[payload_hash]
            live_chats = entry.setdefault("liveChats", {})
            for chat_id in list(entry["chats"].keys()):
                if payload_hash in normalized.get(chat_id, set()):
                    entry["chats"][chat_id] = now
                    live_chats[chat_id] = now
                    continue
                was_live = chat_id in live_chats
                live_chats.pop(chat_id, None)
                last_seen = float(entry["chats"].get(chat_id) or entry["storedAt"])
                if not was_live and now - last_seen <= ORPHAN_GRACE_SECONDS:
                    continue
                entry["chats"].pop(chat_id, None)
                dropped_refs += 1
            if not entry["chats"]:
                self._delete_entry(payload_hash, entry)
                deleted_files += 1
        self._save()
        return {"droppedRefs": dropped_refs, "deletedFiles": deleted_files, "entries": len(self._index)}

    def stats(self) -> dict[str, Any]:
        with self._lock:
            return self._stats_locked()

    def _stats_locked(self) -> dict[str, Any]:
        self._load()
        return {
            "entries": len(self._index),
            "totalBytes": sum(entry["size"] for entry in self._index.values()),
            "root": str(self.root),
        }


# ---- bounded read-only inspection ---------------------------------------


def inspect_image_bytes(path: Path, kind: str, size: int) -> dict[str, Any]:
    result: dict[str, Any] = {"kind": kind, "size": size}
    if kind != "image_png":
        return result
    try:
        with open(path, "rb") as handle:
            head = handle.read(26)
        if head.startswith(_MAGIC_PNG) and head[12:16] == b"IHDR":
            width, height = struct.unpack(">II", head[16:24])
            result["width"] = int(width)
            result["height"] = int(height)
    except (OSError, struct.error):
        pass
    return result


def guard_archive_listing(entries: list[tuple[str, int, int]]) -> None:
    """Raise on entry-count, uncompressed-total, or compression-ratio violations.

    ``entries``: (name, compressed_size, uncompressed_size) 三元组。只读列目录
    也先过守卫：炸弹归档连列表都不给，直接诚实报错。
    """

    if len(entries) > INSPECT_MAX_ENTRIES:
        raise ChatAttachmentVaultError(
            f"Archive entry count exceeds the inspection cap ({INSPECT_MAX_ENTRIES}).",
            reason="entry_count_cap",
        )
    total_compressed = sum(max(0, item[1]) for item in entries)
    total_uncompressed = sum(max(0, item[2]) for item in entries)
    if total_uncompressed > INSPECT_TOTAL_UNCOMPRESSED_MAX_BYTES:
        raise ChatAttachmentVaultError(
            "Archive total uncompressed size exceeds the inspection cap.",
            reason="uncompressed_total_cap",
        )
    if (
        total_uncompressed > INSPECT_BOMB_MIN_UNCOMPRESSED_BYTES
        and total_uncompressed / max(1, total_compressed) > INSPECT_BOMB_RATIO
    ):
        raise ChatAttachmentVaultError(
            "Archive compression ratio looks like a decompression bomb.",
            reason="bomb_ratio",
        )


def _preflight_zip_entry_count(path: Path) -> int:
    """Stream-count the central directory before ZipFile allocates its entries."""

    try:
        size = path.stat().st_size
        tail_size = min(size, 65_557)
        with path.open("rb") as handle:
            handle.seek(size - tail_size)
            tail = handle.read(tail_size)
    except OSError as exc:
        raise ChatAttachmentVaultError("Archive file is not readable.", reason="bad_archive") from exc
    cursor = len(tail)
    while True:
        offset = tail.rfind(b"PK\x05\x06", 0, cursor)
        if offset < 0:
            raise ChatAttachmentVaultError("Archive is missing a valid end-of-central-directory record.", reason="bad_archive")
        if offset + 22 <= len(tail):
            comment_size = struct.unpack_from("<H", tail, offset + 20)[0]
            if offset + 22 + comment_size == len(tail):
                disk_number, central_disk, disk_entries, entry_count = struct.unpack_from("<HHHH", tail, offset + 4)
                central_size, central_offset = struct.unpack_from("<II", tail, offset + 12)
                if disk_number or central_disk or disk_entries != entry_count:
                    raise ChatAttachmentVaultError("Multi-disk ZIP archives are not supported.", reason="bad_archive")
                entry_count = struct.unpack_from("<H", tail, offset + 10)[0]
                if entry_count == 0xFFFF or entry_count > INSPECT_MAX_ENTRIES:
                    raise ChatAttachmentVaultError(
                        f"Archive entry count exceeds the inspection cap ({INSPECT_MAX_ENTRIES}).",
                        reason="entry_count_cap",
                    )
                if central_size > ZIP_CENTRAL_DIRECTORY_MAX_BYTES:
                    raise ChatAttachmentVaultError(
                        "Archive central directory exceeds the inspection memory cap.",
                        reason="entry_count_cap",
                    )
                eocd_absolute = size - tail_size + offset
                concat_bytes = eocd_absolute - int(central_size) - int(central_offset)
                central_start = int(central_offset) + concat_bytes
                if central_start < 0 or central_start + int(central_size) != eocd_absolute:
                    raise ChatAttachmentVaultError("Archive central directory offsets are invalid.", reason="bad_archive")
                count = 0
                remaining = int(central_size)
                try:
                    with path.open("rb") as handle:
                        handle.seek(central_start)
                        while remaining:
                            header = handle.read(46)
                            if len(header) != 46 or not header.startswith(b"PK\x01\x02"):
                                raise ChatAttachmentVaultError(
                                    "Archive central directory is malformed.",
                                    reason="bad_archive",
                                )
                            name_size, extra_size, entry_comment_size = struct.unpack_from("<HHH", header, 28)
                            variable_size = int(name_size) + int(extra_size) + int(entry_comment_size)
                            record_size = 46 + variable_size
                            if record_size > remaining:
                                raise ChatAttachmentVaultError(
                                    "Archive central directory record exceeds its declared size.",
                                    reason="bad_archive",
                                )
                            handle.seek(variable_size, os.SEEK_CUR)
                            remaining -= record_size
                            count += 1
                            if count > INSPECT_MAX_ENTRIES:
                                raise ChatAttachmentVaultError(
                                    f"Archive entry count exceeds the inspection cap ({INSPECT_MAX_ENTRIES}).",
                                    reason="entry_count_cap",
                                )
                except OSError as exc:
                    raise ChatAttachmentVaultError("Archive central directory is unreadable.", reason="bad_archive") from exc
                if count != int(entry_count):
                    raise ChatAttachmentVaultError("Archive entry count does not match its central directory.", reason="bad_archive")
                return count
        cursor = offset


def guard_vault_archive(path: Path, kind: str) -> dict[str, Any]:
    """Run the bounded archive-safety guards against a vault archive file.

    zip 用 central directory 的逐条 compressed/uncompressed；unitypackage
    (gzip tar) 没有逐条压缩大小，用磁盘文件大小做总压缩量，迭代中滚动累计
    解压体积并提前熔断，避免为守卫本身付出无界解压成本。
    """

    if kind == "zip":
        _preflight_zip_entry_count(path)
        try:
            with zipfile.ZipFile(path) as archive:
                infos = archive.infolist()
        except (OSError, zipfile.BadZipFile) as exc:
            raise ChatAttachmentVaultError("Archive is not a readable zip file.", reason="bad_archive") from exc
        entries = [(info.filename, max(0, info.compress_size), max(0, info.file_size)) for info in infos]
        guard_archive_listing(entries)
        symlinks = [
            info.filename
            for info in infos
            if stat.S_ISLNK((int(info.external_attr) >> 16) & 0xFFFF)
        ]
        if symlinks:
            raise ChatAttachmentVaultError(
                f"Archive contains a symbolic link: {symlinks[0]}",
                reason="unsafe_entry_type",
            )
        unsafe = [name for name, _c, _u in entries if _is_unsafe_archive_member(name)]
        if unsafe:
            raise ChatAttachmentVaultError(
                f"Archive contains unsafe member paths (zip-slip): {unsafe[0]}",
                reason="unsafe_entry_path",
            )
        return {"entryCount": len(entries), "totalUncompressedBytes": sum(item[2] for item in entries)}
    if kind == "unitypackage":
        try:
            compressed_total = max(1, path.stat().st_size)
        except OSError as exc:
            raise ChatAttachmentVaultError("Archive file is not readable.", reason="bad_archive") from exc
        count = 0
        uncompressed_total = 0
        try:
            with tarfile.open(path, mode="r:gz") as archive:
                for member in archive:
                    count += 1
                    if count > INSPECT_MAX_ENTRIES:
                        raise ChatAttachmentVaultError(
                            f"Archive entry count exceeds the inspection cap ({INSPECT_MAX_ENTRIES}).",
                            reason="entry_count_cap",
                        )
                    if _is_unsafe_archive_member(member.name):
                        raise ChatAttachmentVaultError(
                            f"Archive contains unsafe member paths: {member.name}",
                            reason="unsafe_entry_path",
                        )
                    if member.issym() or member.islnk():
                        raise ChatAttachmentVaultError(
                            f"Archive contains a symbolic link: {member.name}",
                            reason="unsafe_entry_type",
                        )
                    uncompressed_total += max(0, int(member.size or 0))
                    if uncompressed_total > INSPECT_TOTAL_UNCOMPRESSED_MAX_BYTES:
                        raise ChatAttachmentVaultError(
                            "Archive total uncompressed size exceeds the inspection cap.",
                            reason="uncompressed_total_cap",
                        )
        except tarfile.TarError as exc:
            raise ChatAttachmentVaultError("Archive is not a readable unitypackage (gzipped tar).", reason="bad_archive") from exc
        if (
            uncompressed_total > INSPECT_BOMB_MIN_UNCOMPRESSED_BYTES
            and uncompressed_total / compressed_total > INSPECT_BOMB_RATIO
        ):
            raise ChatAttachmentVaultError(
                "Archive compression ratio looks like a decompression bomb.",
                reason="bomb_ratio",
            )
        return {"entryCount": count, "totalUncompressedBytes": uncompressed_total}
    raise ChatAttachmentVaultError("Archive guard only supports zip and unitypackage.", reason="unsupported_kind")


def _is_unsafe_archive_member(name: str) -> bool:
    normalized = str(name or "").replace("\\", "/")
    if not normalized or normalized.startswith("/") or re.match(r"^[A-Za-z]:", normalized):
        return True
    return any(part == ".." for part in normalized.split("/"))


def extract_archive_entry_text(path: Path, kind: str, entry_path: str) -> dict[str, Any]:
    """Bounded single-entry text extract (max 64KB decoded, replacement on errors)."""

    target = str(entry_path or "").strip().replace("\\", "/")
    if not target:
        raise ChatAttachmentVaultError("entryPath is required.", reason="entry_path_required")
    if _is_unsafe_archive_member(target):
        raise ChatAttachmentVaultError("Archive entry path is unsafe.", reason="unsafe_entry_path")
    raw: bytes | None = None
    if kind == "zip":
        _preflight_zip_entry_count(path)
        try:
            with zipfile.ZipFile(path) as archive:
                try:
                    info = archive.getinfo(target)
                except KeyError:
                    raise ChatAttachmentVaultError("Archive entry was not found.", reason="entry_not_found") from None
                if info.is_dir():
                    raise ChatAttachmentVaultError("Archive entry is a directory.", reason="entry_is_directory")
                if info.file_size > ENTRY_TEXT_MAX_BYTES * 8:
                    raise ChatAttachmentVaultError("Archive entry is too large for text extract.", reason="entry_too_large")
                with archive.open(info) as handle:
                    raw = handle.read(ENTRY_TEXT_MAX_BYTES + 1)
        except zipfile.BadZipFile as exc:
            raise ChatAttachmentVaultError("Archive is not a readable zip file.", reason="bad_archive") from exc
    elif kind == "unitypackage":
        try:
            with tarfile.open(path, mode="r:gz") as archive:
                member: tarfile.TarInfo | None = None
                scanned = 0
                for candidate in archive:
                    scanned += 1
                    if scanned > INSPECT_MAX_ENTRIES:
                        raise ChatAttachmentVaultError(
                            f"Archive entry count exceeds the inspection cap ({INSPECT_MAX_ENTRIES}).",
                            reason="entry_count_cap",
                        )
                    if candidate.name.replace("\\", "/") == target:
                        member = candidate
                        break
                if member is None:
                    raise ChatAttachmentVaultError("Archive entry was not found.", reason="entry_not_found")
                if not member.isfile():
                    raise ChatAttachmentVaultError("Archive entry is not a regular file.", reason="entry_is_directory")
                if member.size > ENTRY_TEXT_MAX_BYTES * 8:
                    raise ChatAttachmentVaultError("Archive entry is too large for text extract.", reason="entry_too_large")
                handle = archive.extractfile(member)
                if handle is None:
                    raise ChatAttachmentVaultError("Archive entry could not be read.", reason="entry_not_found")
                with handle:
                    raw = handle.read(ENTRY_TEXT_MAX_BYTES + 1)
        except tarfile.TarError as exc:
            raise ChatAttachmentVaultError("Archive is not a readable unitypackage (gzipped tar).", reason="bad_archive") from exc
    else:
        raise ChatAttachmentVaultError("Entry text extract only supports zip and unitypackage.", reason="unsupported_kind")
    truncated = len(raw) > ENTRY_TEXT_MAX_BYTES
    text = raw[:ENTRY_TEXT_MAX_BYTES].decode("utf-8", errors="replace")
    return {
        "entryPath": target,
        "text": text,
        "textBytes": min(len(raw), ENTRY_TEXT_MAX_BYTES),
        "truncated": truncated,
        "binaryLikely": raw[: ENTRY_TEXT_MAX_BYTES].count(0) > 0,
    }
