"""1.3.2 attachment vault test matrix.

Covers the roadmap acceptance rows: magic/renamed-file rejection, zip-slip and
decompression-bomb fixtures, per-format caps with honest metadata fallback,
per-chat quota LRU with live-reference protection and orphan grace, restart
rehydration, and the unitypackage / image import handoffs into the supervised
approval lane.
"""

import hashlib
import io
import stat
import struct
import tarfile
import tempfile
import time
import unittest
import zipfile
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from unittest.mock import patch

from fastapi.testclient import TestClient

import chat_attachment_vault
import dashboard_server
from agent_gateway import planner_safe_tool_result_fields
from chat_attachment_vault import (
    ChatAttachmentVault,
    ChatAttachmentVaultError,
    classify_upload,
    extract_archive_entry_text,
    guard_archive_listing,
    guard_vault_archive,
    inspect_image_bytes,
)


def make_zip_bytes(entries: dict[str, bytes]) -> bytes:
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w", compression=zipfile.ZIP_DEFLATED) as archive:
        for name, data in entries.items():
            archive.writestr(name, data)
    return buffer.getvalue()


def make_unitypackage_bytes(entries: dict[str, bytes]) -> bytes:
    buffer = io.BytesIO()
    with tarfile.open(fileobj=buffer, mode="w:gz") as archive:
        for name, data in entries.items():
            info = tarfile.TarInfo(name=name)
            info.size = len(data)
            archive.addfile(info, io.BytesIO(data))
    return buffer.getvalue()


def make_png_bytes(width: int = 10, height: int = 20, pad: bytes = b"") -> bytes:
    return (
        b"\x89PNG\r\n\x1a\n"
        + b"\x00\x00\x00\x0d"
        + b"IHDR"
        + struct.pack(">II", width, height)
        + b"\x08\x02\x00\x00\x00"
        + pad
    )


class ClassifyUploadTests(unittest.TestCase):
    def test_rejects_unlisted_extension(self) -> None:
        with self.assertRaises(ChatAttachmentVaultError) as ctx:
            classify_upload("notes.txt", b"hello world head")
        self.assertEqual(ctx.exception.reason, "format_not_allowed")
        self.assertEqual(ctx.exception.status_code, 415)

    def test_rejects_renamed_file_on_magic_mismatch(self) -> None:
        # A text file renamed to .zip must be rejected, never guessed.
        with self.assertRaises(ChatAttachmentVaultError) as ctx:
            classify_upload("evil.zip", b"plain text, not an archive")
        self.assertEqual(ctx.exception.reason, "magic_mismatch")
        self.assertEqual(ctx.exception.status_code, 415)
        # A PNG body renamed to .jpg is also a mismatch.
        with self.assertRaises(ChatAttachmentVaultError) as ctx:
            classify_upload("photo.jpg", make_png_bytes()[:16])
        self.assertEqual(ctx.exception.reason, "magic_mismatch")

    def test_accepts_each_allowlisted_format(self) -> None:
        cases = [
            ("pack.zip", make_zip_bytes({"a.txt": b"x"})[:16], "zip", "archive"),
            ("outfit.unitypackage", make_unitypackage_bytes({"a": b"x"})[:16], "unitypackage", "archive"),
            ("img.png", make_png_bytes()[:16], "image_png", "image"),
            ("img.jpeg", b"\xff\xd8\xff\xe0" + b"\x00" * 12, "image_jpeg", "image"),
            ("img.webp", b"RIFF\x00\x00\x00\x00WEBPVP8 ", "image_webp", "image"),
        ]
        for name, head, kind, category in cases:
            with self.subTest(name=name):
                resolved = classify_upload(name, head)
                self.assertEqual(resolved["kind"], kind)
                self.assertEqual(resolved["category"], category)


class VaultStoreTests(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name) / "vault"
        self.vault = ChatAttachmentVault(self.root)

    def tearDown(self) -> None:
        self.tmp.cleanup()

    def _age_entry(self, payload_hash: str, *, seconds_past_grace: float = 60.0, last_access: float | None = None) -> None:
        entry = self.vault._index[payload_hash]
        aged_at = time.time() - chat_attachment_vault.ORPHAN_GRACE_SECONDS - seconds_past_grace
        entry["storedAt"] = aged_at
        entry["chats"] = {chat_id: aged_at for chat_id in entry["chats"]}
        if last_access is not None:
            entry["lastAccessAt"] = last_access
        self.vault._save()

    def test_ingest_stores_bytes_and_dedupes_across_chats(self) -> None:
        data = make_zip_bytes({"asset.txt": b"payload"})
        stored = self.vault.ingest(data=data, name="pack.zip", declared_type="application/zip", chat_id="chat-a")
        self.assertEqual(stored["payloadHash"], hashlib.sha256(data).hexdigest())
        self.assertEqual(stored["kind"], "zip")
        file_path = self.root / "files" / f"{stored['payloadHash']}.zip"
        self.assertTrue(file_path.is_file())
        self.assertEqual(file_path.read_bytes(), data)
        # Same bytes from another chat: dedupe on hash, add a reference.
        again = self.vault.ingest(data=data, name="copy.zip", declared_type="application/zip", chat_id="chat-b")
        self.assertEqual(again["payloadHash"], stored["payloadHash"])
        self.assertEqual(again["chatCount"], 2)
        self.assertEqual(len(list((self.root / "files").iterdir())), 1)

    def test_ingest_rejects_empty_body_and_missing_chat_id(self) -> None:
        with self.assertRaises(ChatAttachmentVaultError) as ctx:
            self.vault.ingest(data=b"", name="pack.zip", declared_type="", chat_id="chat-a")
        self.assertIn(ctx.exception.reason, {"empty_body", "magic_mismatch"})
        with self.assertRaises(ChatAttachmentVaultError) as ctx:
            self.vault.ingest(data=make_png_bytes(), name="img.png", declared_type="", chat_id="   ")
        self.assertEqual(ctx.exception.reason, "chat_id_required")

    def test_ingest_file_streams_staged_upload_into_vault(self) -> None:
        data = make_zip_bytes({"Assets/example.prefab": b"prefab"})
        staged = self.root / "staged.partial"
        staged.parent.mkdir(parents=True)
        staged.write_bytes(data)
        stored = self.vault.ingest_file(
            source_path=staged,
            name="pack.zip",
            declared_type="application/zip",
            chat_id="chat-a",
        )
        self.assertEqual(stored["payloadHash"], hashlib.sha256(data).hexdigest())
        self.assertFalse(staged.exists())
        self.assertEqual((self.root / "files" / f"{stored['payloadHash']}.zip").read_bytes(), data)

    def test_ingest_rejects_over_cap_per_format(self) -> None:
        data = make_png_bytes(pad=b"\x00" * 64)
        with patch.object(chat_attachment_vault, "IMAGE_MAX_BYTES", 16):
            with self.assertRaises(ChatAttachmentVaultError) as ctx:
                self.vault.ingest(data=data, name="big.png", declared_type="image/png", chat_id="chat-a")
        self.assertEqual(ctx.exception.reason, "over_cap")
        self.assertEqual(ctx.exception.status_code, 413)
        # Nothing was written for the rejected upload.
        self.assertEqual(self.vault.stats()["entries"], 0)

    def test_quota_evicts_oldest_sole_owned_entry_past_grace(self) -> None:
        first = make_png_bytes(pad=b"a" * 64)
        second = make_png_bytes(pad=b"b" * 64)
        stored_first = self.vault.ingest(data=first, name="a.png", declared_type="image/png", chat_id="chat-a")
        self._age_entry(stored_first["payloadHash"], last_access=1.0)
        quota = len(first) + len(second) - 1
        with patch.object(chat_attachment_vault, "PER_CHAT_QUOTA_BYTES", quota):
            stored_second = self.vault.ingest(data=second, name="b.png", declared_type="image/png", chat_id="chat-a")
        self.assertNotIn(stored_first["payloadHash"], self.vault._index)
        self.assertFalse((self.root / "files" / f"{stored_first['payloadHash']}.png").exists())
        self.assertIn(stored_second["payloadHash"], self.vault._index)

    def test_quota_never_evicts_entries_referenced_by_other_chats(self) -> None:
        first = make_png_bytes(pad=b"a" * 64)
        second = make_png_bytes(pad=b"b" * 64)
        stored_first = self.vault.ingest(data=first, name="a.png", declared_type="image/png", chat_id="chat-a")
        # Shared with another chat: not an eviction candidate even when old.
        self.vault._index[stored_first["payloadHash"]]["chats"]["chat-b"] = time.time()
        self._age_entry(stored_first["payloadHash"], last_access=1.0)
        quota = len(first) + len(second) - 1
        with patch.object(chat_attachment_vault, "PER_CHAT_QUOTA_BYTES", quota):
            with self.assertRaises(ChatAttachmentVaultError) as ctx:
                self.vault.ingest(data=second, name="b.png", declared_type="image/png", chat_id="chat-a")
        self.assertEqual(ctx.exception.reason, "quota_exceeded")
        self.assertIn(stored_first["payloadHash"], self.vault._index)

    def test_quota_keeps_fresh_entries_inside_grace(self) -> None:
        first = make_png_bytes(pad=b"a" * 64)
        second = make_png_bytes(pad=b"b" * 64)
        stored_first = self.vault.ingest(data=first, name="a.png", declared_type="image/png", chat_id="chat-a")
        quota = len(first) + len(second) - 1
        with patch.object(chat_attachment_vault, "PER_CHAT_QUOTA_BYTES", quota):
            with self.assertRaises(ChatAttachmentVaultError) as ctx:
                self.vault.ingest(data=second, name="b.png", declared_type="image/png", chat_id="chat-a")
        self.assertEqual(ctx.exception.reason, "quota_exceeded")
        self.assertIn(stored_first["payloadHash"], self.vault._index)

    def test_retain_keeps_live_refs_and_prunes_expired_orphans(self) -> None:
        data = make_png_bytes(pad=b"live" * 8)
        stored = self.vault.ingest(data=data, name="live.png", declared_type="image/png", chat_id="chat-a")
        payload_hash = stored["payloadHash"]
        self._age_entry(payload_hash)
        # Live reference in the snapshot: entry must survive.
        outcome = self.vault.retain({"chat-a": {payload_hash}})
        self.assertEqual(outcome["deletedFiles"], 0)
        self.assertIn(payload_hash, self.vault._index)
        # Reference gone from the full snapshot and past grace: entry + file removed.
        outcome = self.vault.retain({"chat-a": set()})
        self.assertEqual(outcome["deletedFiles"], 1)
        self.assertNotIn(payload_hash, self.vault._index)
        self.assertFalse((self.root / "files" / f"{payload_hash}.png").exists())

    def test_retain_keeps_unreferenced_entries_inside_grace(self) -> None:
        data = make_png_bytes(pad=b"fresh" * 8)
        stored = self.vault.ingest(data=data, name="fresh.png", declared_type="image/png", chat_id="chat-a")
        outcome = self.vault.retain({})
        self.assertEqual(outcome["deletedFiles"], 0)
        self.assertIn(stored["payloadHash"], self.vault._index)

    def test_restart_rehydration_drops_entries_with_missing_files(self) -> None:
        keep = make_png_bytes(pad=b"keep" * 8)
        lost = make_png_bytes(pad=b"lost" * 8)
        stored_keep = self.vault.ingest(data=keep, name="keep.png", declared_type="image/png", chat_id="chat-a")
        stored_lost = self.vault.ingest(data=lost, name="lost.png", declared_type="image/png", chat_id="chat-a")
        (self.root / "files" / f"{stored_lost['payloadHash']}.png").unlink()
        rebuilt = ChatAttachmentVault(self.root)
        self.assertIsNotNone(rebuilt.resolve(stored_keep["payloadHash"]))
        self.assertIsNone(rebuilt.resolve(stored_lost["payloadHash"]))
        self.assertNotIn(stored_lost["payloadHash"], rebuilt._index)

    def test_concurrent_ingest_is_atomic_and_deduplicated(self) -> None:
        data = make_zip_bytes({"asset.txt": b"payload"})

        def ingest(index: int) -> str:
            return self.vault.ingest(
                data=data,
                name="pack.zip",
                declared_type="application/zip",
                chat_id=f"chat-{index}",
            )["payloadHash"]

        with ThreadPoolExecutor(max_workers=8) as pool:
            hashes = list(pool.map(ingest, range(24)))
        self.assertEqual(set(hashes), {hashlib.sha256(data).hexdigest()})
        self.assertEqual(self.vault.stats()["entries"], 1)
        self.assertEqual(len(list((self.root / "files").iterdir())), 1)

    def test_resolve_rejects_bytes_replaced_after_ingest(self) -> None:
        data = make_png_bytes(pad=b"original")
        stored = self.vault.ingest(data=data, name="image.png", declared_type="image/png", chat_id="chat-a")
        path = self.root / "files" / f"{stored['payloadHash']}.png"
        path.write_bytes(make_png_bytes(pad=b"replaced"))
        with self.assertRaises(ChatAttachmentVaultError) as ctx:
            self.vault.resolve(stored["payloadHash"])
        self.assertEqual(ctx.exception.reason, "integrity_mismatch")
        self.assertEqual(ctx.exception.status_code, 409)
        self.assertFalse(path.exists())

    def test_quota_never_evicts_a_durable_live_reference(self) -> None:
        first = make_png_bytes(pad=b"a" * 64)
        second = make_png_bytes(pad=b"b" * 64)
        stored = self.vault.ingest(data=first, name="a.png", declared_type="image/png", chat_id="chat-a")
        self._age_entry(stored["payloadHash"], last_access=1.0)
        self.vault.retain({"chat-a": {stored["payloadHash"]}})
        with patch.object(chat_attachment_vault, "PER_CHAT_QUOTA_BYTES", len(first) + len(second) - 1):
            with self.assertRaises(ChatAttachmentVaultError) as ctx:
                self.vault.ingest(data=second, name="b.png", declared_type="image/png", chat_id="chat-a")
        self.assertEqual(ctx.exception.reason, "quota_exceeded")
        self.assertIn(stored["payloadHash"], self.vault._index)

    def test_global_quota_evicts_only_old_non_live_entries(self) -> None:
        first = make_png_bytes(pad=b"a" * 64)
        second = make_png_bytes(pad=b"b" * 64)
        stored = self.vault.ingest(data=first, name="a.png", declared_type="image/png", chat_id="chat-a")
        self._age_entry(stored["payloadHash"], last_access=1.0)
        with patch.object(chat_attachment_vault, "GLOBAL_QUOTA_BYTES", len(first) + len(second) - 1):
            next_entry = self.vault.ingest(data=second, name="b.png", declared_type="image/png", chat_id="chat-b")
        self.assertNotIn(stored["payloadHash"], self.vault._index)
        self.assertIn(next_entry["payloadHash"], self.vault._index)

    def test_global_quota_rejects_instead_of_evicting_live_entry(self) -> None:
        first = make_png_bytes(pad=b"a" * 64)
        second = make_png_bytes(pad=b"b" * 64)
        stored = self.vault.ingest(data=first, name="a.png", declared_type="image/png", chat_id="chat-a")
        self._age_entry(stored["payloadHash"], last_access=1.0)
        self.vault.retain({"chat-a": {stored["payloadHash"]}})
        with patch.object(chat_attachment_vault, "GLOBAL_QUOTA_BYTES", len(first) + len(second) - 1):
            with self.assertRaises(ChatAttachmentVaultError) as ctx:
                self.vault.ingest(data=second, name="b.png", declared_type="image/png", chat_id="chat-b")
        self.assertEqual(ctx.exception.reason, "global_quota_exceeded")
        self.assertIn(stored["payloadHash"], self.vault._index)

    def test_resolve_returns_disk_path_with_real_extension(self) -> None:
        data = make_zip_bytes({"a.txt": b"x"})
        stored = self.vault.ingest(data=data, name="pack.zip", declared_type="application/zip", chat_id="chat-a")
        resolved = self.vault.resolve(stored["payloadHash"])
        self.assertIsNotNone(resolved)
        assert resolved is not None
        self.assertTrue(resolved["path"].endswith(f"{stored['payloadHash']}.zip"))
        self.assertTrue(Path(resolved["path"]).is_file())


class ArchiveGuardTests(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        self.dir = Path(self.tmp.name)

    def tearDown(self) -> None:
        self.tmp.cleanup()

    def _write(self, name: str, data: bytes) -> Path:
        path = self.dir / name
        path.write_bytes(data)
        return path

    def test_zip_slip_member_is_rejected(self) -> None:
        path = self._write("slip.zip", make_zip_bytes({"../evil.txt": b"boom", "ok.txt": b"fine"}))
        with self.assertRaises(ChatAttachmentVaultError) as ctx:
            guard_vault_archive(path, "zip")
        self.assertEqual(ctx.exception.reason, "unsafe_entry_path")

    def test_absolute_and_drive_member_paths_are_rejected(self) -> None:
        for name in ("/abs/evil.txt", "C:/evil.txt", "..\\evil.txt"):
            with self.subTest(name=name):
                path = self._write("bad.zip", make_zip_bytes({name: b"boom"}))
                with self.assertRaises(ChatAttachmentVaultError) as ctx:
                    guard_vault_archive(path, "zip")
                self.assertEqual(ctx.exception.reason, "unsafe_entry_path")

    def test_unitypackage_unsafe_member_is_rejected(self) -> None:
        path = self._write("slip.unitypackage", make_unitypackage_bytes({"../evil": b"boom"}))
        with self.assertRaises(ChatAttachmentVaultError) as ctx:
            guard_vault_archive(path, "unitypackage")
        self.assertEqual(ctx.exception.reason, "unsafe_entry_path")

    def test_zip_symbolic_link_is_rejected(self) -> None:
        buffer = io.BytesIO()
        with zipfile.ZipFile(buffer, "w") as archive:
            link = zipfile.ZipInfo("link")
            link.create_system = 3
            link.external_attr = (stat.S_IFLNK | 0o777) << 16
            archive.writestr(link, "../outside")
        path = self._write("link.zip", buffer.getvalue())
        with self.assertRaises(ChatAttachmentVaultError) as ctx:
            guard_vault_archive(path, "zip")
        self.assertEqual(ctx.exception.reason, "unsafe_entry_type")

    def test_unitypackage_guard_reports_entry_stats(self) -> None:
        path = self._write("ok.unitypackage", make_unitypackage_bytes({"asset/a": b"x" * 32, "asset/b": b"y" * 16}))
        outcome = guard_vault_archive(path, "unitypackage")
        self.assertEqual(outcome["entryCount"], 2)
        self.assertEqual(outcome["totalUncompressedBytes"], 48)

    def test_listing_guard_rejects_entry_count_and_bomb_ratio(self) -> None:
        too_many = [(f"f{i}", 1, 1) for i in range(chat_attachment_vault.INSPECT_MAX_ENTRIES + 1)]
        with self.assertRaises(ChatAttachmentVaultError) as ctx:
            guard_archive_listing(too_many)
        self.assertEqual(ctx.exception.reason, "entry_count_cap")
        bomb = [("bomb.bin", 1024, 300 * 1024 * 1024)]
        with self.assertRaises(ChatAttachmentVaultError) as ctx:
            guard_archive_listing(bomb)
        self.assertEqual(ctx.exception.reason, "bomb_ratio")
        with self.assertRaises(ChatAttachmentVaultError) as ctx:
            guard_archive_listing([("huge.bin", 1, chat_attachment_vault.INSPECT_TOTAL_UNCOMPRESSED_MAX_BYTES + 1)])
        self.assertEqual(ctx.exception.reason, "uncompressed_total_cap")

    def test_zip_entry_count_is_rejected_before_central_directory_allocation(self) -> None:
        data = bytearray(make_zip_bytes({"ok.txt": b"ok"}))
        eocd = data.rfind(b"PK\x05\x06")
        self.assertGreaterEqual(eocd, 0)
        struct.pack_into("<H", data, eocd + 8, chat_attachment_vault.INSPECT_MAX_ENTRIES + 1)
        struct.pack_into("<H", data, eocd + 10, chat_attachment_vault.INSPECT_MAX_ENTRIES + 1)
        path = self._write("too-many.zip", bytes(data))
        with self.assertRaises(ChatAttachmentVaultError) as ctx:
            guard_vault_archive(path, "zip")
        self.assertEqual(ctx.exception.reason, "entry_count_cap")

    def test_corrupt_archive_is_rejected_honestly(self) -> None:
        path = self._write("corrupt.zip", b"PK\x03\x04not really a zip archive")
        with self.assertRaises(ChatAttachmentVaultError) as ctx:
            guard_vault_archive(path, "zip")
        self.assertEqual(ctx.exception.reason, "bad_archive")

    def test_entry_text_extract_is_bounded_and_safe(self) -> None:
        big = b"A" * (chat_attachment_vault.ENTRY_TEXT_MAX_BYTES + 100)
        path = self._write(
            "pack.zip",
            make_zip_bytes({"note.txt": b"hello vault", "big.txt": big}),
        )
        result = extract_archive_entry_text(path, "zip", "note.txt")
        self.assertEqual(result["text"], "hello vault")
        self.assertFalse(result["truncated"])
        truncated = extract_archive_entry_text(path, "zip", "big.txt")
        self.assertTrue(truncated["truncated"])
        self.assertEqual(truncated["textBytes"], chat_attachment_vault.ENTRY_TEXT_MAX_BYTES)
        with self.assertRaises(ChatAttachmentVaultError) as ctx:
            extract_archive_entry_text(path, "zip", "missing.txt")
        self.assertEqual(ctx.exception.reason, "entry_not_found")
        with self.assertRaises(ChatAttachmentVaultError) as ctx:
            extract_archive_entry_text(path, "zip", "../etc/passwd")
        self.assertEqual(ctx.exception.reason, "unsafe_entry_path")

    def test_inspect_image_bytes_reads_png_dimensions(self) -> None:
        path = self._write("img.png", make_png_bytes(width=10, height=20))
        result = inspect_image_bytes(path, "image_png", 42)
        self.assertEqual(result["width"], 10)
        self.assertEqual(result["height"], 20)
        self.assertEqual(result["size"], 42)


class ChatAttachmentEndpointTests(unittest.TestCase):
    """HTTP wiring: upload responses carry machine-readable reasons for the
    frontend's honest metadata fallback; import hands off into the supervised
    approval lane."""

    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        tmp_root = Path(self.tmp.name)
        self.original_agent_paths = (
            dashboard_server.AGENT_GATEWAY.config_path,
            dashboard_server.AGENT_GATEWAY.audit_dir,
        )
        dashboard_server.AGENT_GATEWAY.configure_paths(
            tmp_root / "agent_gateway.json",
            tmp_root / "agent_gateway",
        )
        config = dashboard_server.AGENT_GATEWAY.ensure_config()
        config.developer_options_enabled = True
        config.developer_options_ever_enabled = True
        dashboard_server.AGENT_GATEWAY.save_config(config)
        self.vault = ChatAttachmentVault(tmp_root / "chat-attachments")
        self.vault_patcher = patch("dashboard_server.chat_attachment_vault_store", return_value=self.vault)
        self.vault_patcher.start()
        # These route tests do not need the app lifespan; starting it would
        # launch the unrelated Unity status monitor and slow shutdown.
        self.client = TestClient(dashboard_server.app)
        with dashboard_server._CHAT_ATTACHMENT_UPLOAD_LOCK:
            dashboard_server._CHAT_ATTACHMENT_UPLOADS.clear()

    def tearDown(self) -> None:
        with dashboard_server._CHAT_ATTACHMENT_UPLOAD_LOCK:
            dashboard_server._CHAT_ATTACHMENT_UPLOADS.clear()
        self.vault_patcher.stop()
        dashboard_server.AGENT_GATEWAY.configure_paths(*self.original_agent_paths)
        self.tmp.cleanup()

    def _upload(self, client: TestClient, *, name: str, data: bytes, chat_id: str = "chat-1", declared_type: str = "application/octet-stream"):
        begun = client.post(
            "/api/app/chat-attachments/uploads",
            json={"name": name, "chatId": chat_id, "declaredType": declared_type, "size": len(data)},
        )
        if begun.status_code != 200:
            return begun
        upload_id = begun.json()["uploadId"]
        appended = client.post(
            f"/api/app/chat-attachments/uploads/{upload_id}/chunks",
            params={"offset": 0},
            content=data,
            headers={"Content-Type": "application/octet-stream"},
        )
        if appended.status_code != 200:
            return appended
        return client.post("/api/app/chat-attachments/uploads/finish", json={"uploadId": upload_id})

    def test_upload_stores_allowlisted_archive(self) -> None:
        data = make_zip_bytes({"asset.prefab": b"prefab"})
        response = self._upload(self.client, name="pack.zip", data=data, declared_type="application/zip")
        self.assertEqual(response.status_code, 200)
        body = response.json()
        self.assertTrue(body["ok"])
        self.assertEqual(body["attachment"]["payloadHash"], hashlib.sha256(data).hexdigest())
        self.assertEqual(body["attachment"]["kind"], "zip")

    def test_chunked_upload_is_ordered_and_streamed_into_vault(self) -> None:
        data = make_zip_bytes({"Assets/example.prefab": b"prefab" * 1024})
        begun = self.client.post(
            "/api/app/chat-attachments/uploads",
            json={"name": "chunked.zip", "chatId": "chat-1", "declaredType": "application/zip", "size": len(data)},
        )
        self.assertEqual(begun.status_code, 200)
        upload_id = begun.json()["uploadId"]
        split = max(1, len(data) // 2)
        first = self.client.post(
            f"/api/app/chat-attachments/uploads/{upload_id}/chunks",
            params={"offset": 0},
            content=data[:split],
            headers={"Content-Type": "application/octet-stream"},
        )
        self.assertEqual(first.status_code, 200)
        wrong = self.client.post(
            f"/api/app/chat-attachments/uploads/{upload_id}/chunks",
            params={"offset": 0},
            content=data[split:],
            headers={"Content-Type": "application/octet-stream"},
        )
        self.assertEqual(wrong.status_code, 409)
        second = self.client.post(
            f"/api/app/chat-attachments/uploads/{upload_id}/chunks",
            params={"offset": split},
            content=data[split:],
            headers={"Content-Type": "application/octet-stream"},
        )
        self.assertEqual(second.status_code, 200)
        finished = self.client.post(
            "/api/app/chat-attachments/uploads/finish",
            json={"uploadId": upload_id},
        )
        self.assertEqual(finished.status_code, 200)
        self.assertEqual(finished.json()["attachment"]["payloadHash"], hashlib.sha256(data).hexdigest())

    def test_chunked_upload_sessions_are_bounded_per_chat(self) -> None:
        responses = [
            self.client.post(
                "/api/app/chat-attachments/uploads",
                json={"name": f"{index}.zip", "chatId": "chat-1", "declaredType": "application/zip", "size": 128},
            )
            for index in range(dashboard_server.CHAT_ATTACHMENT_UPLOAD_MAX_SESSIONS_PER_CHAT + 1)
        ]
        self.assertTrue(all(response.status_code == 200 for response in responses[:-1]))
        self.assertEqual(responses[-1].status_code, 429)

    def test_upload_rejections_carry_machine_readable_reasons(self) -> None:
        renamed = self._upload(self.client, name="evil.zip", data=b"plain text body")
        self.assertEqual(renamed.status_code, 415)
        self.assertEqual(renamed.json(), {"ok": False, "reason": "magic_mismatch", "error": renamed.json()["error"]})
        unlisted = self._upload(self.client, name="notes.txt", data=b"text")
        self.assertEqual(unlisted.status_code, 415)
        self.assertEqual(unlisted.json()["reason"], "format_not_allowed")
        missing_chat = self._upload(self.client, name="img.png", data=make_png_bytes(), chat_id="")
        self.assertEqual(missing_chat.status_code, 422)

    def test_import_unknown_hash_is_404(self) -> None:
        response = self.client.post("/api/app/chat-attachments/import", json={"payloadHash": "0" * 64})
        self.assertEqual(response.status_code, 404)

    def test_unitypackage_import_hands_off_to_outfit_lane_with_vault_path(self) -> None:
        data = make_unitypackage_bytes({"asset/model.fbx": b"mesh"})
        stored = self.vault.ingest(data=data, name="outfit.unitypackage", declared_type="application/gzip", chat_id="chat-1")
        preview = {"ok": True, "plan": {"readyToApply": True, "kind": "unitypackage_direct"}}
        with patch("dashboard_server.plan_outfit_import_sync", return_value=preview) as plan_mock:
            response = self.client.post(
                "/api/app/chat-attachments/import",
                json={"payloadHash": stored["payloadHash"], "projectPath": "D:/FakeProject"},
            )
        self.assertEqual(response.status_code, 200)
        body = response.json()
        self.assertTrue(body["ok"])
        self.assertEqual(body["approval"]["targetTool"], "vrcforge_import_chat_archive")
        self.assertEqual(body["approval"]["arguments"]["payloadHash"], stored["payloadHash"])
        plan_args = plan_mock.call_args.args[0]
        self.assertEqual(plan_args["packagePath"], str(self.vault.root / "files" / f"{stored['payloadHash']}.unitypackage"))
        self.assertEqual(plan_args["payloadHash"], stored["payloadHash"])

    def test_unitypackage_import_blocks_when_plan_not_ready(self) -> None:
        data = make_unitypackage_bytes({"asset/model.fbx": b"mesh"})
        stored = self.vault.ingest(data=data, name="outfit.unitypackage", declared_type="application/gzip", chat_id="chat-1")
        preview = {"ok": True, "plan": {"readyToApply": False}, "error": "unresolved conflicts"}
        with patch("dashboard_server.plan_outfit_import_sync", return_value=preview):
            response = self.client.post(
                "/api/app/chat-attachments/import",
                json={"payloadHash": stored["payloadHash"]},
            )
        self.assertEqual(response.status_code, 400)

    def test_image_import_creates_supervised_copy_approval(self) -> None:
        data = make_png_bytes(pad=b"\x00" * 32)
        stored = self.vault.ingest(data=data, name="ref.png", declared_type="image/png", chat_id="chat-1")
        response = self.client.post(
            "/api/app/chat-attachments/import",
            json={"payloadHash": stored["payloadHash"], "projectPath": "D:/FakeProject", "targetFolder": "Assets/VRCForge/Imports"},
        )
        self.assertEqual(response.status_code, 200)
        body = response.json()
        self.assertTrue(body["ok"])
        self.assertEqual(body["approval"]["targetTool"], "vrcforge_import_chat_image")

    def test_archive_execution_rejects_bytes_changed_after_approval(self) -> None:
        data = make_zip_bytes({"Assets/example.prefab": b"prefab"})
        stored = self.vault.ingest(data=data, name="pack.zip", declared_type="application/zip", chat_id="chat-1")
        path = self.vault.root / "files" / f"{stored['payloadHash']}.zip"
        path.write_bytes(make_zip_bytes({"Assets/replaced.prefab": b"changed"}))
        with self.assertRaises(dashboard_server.AgentGatewayError) as ctx:
            dashboard_server.import_chat_archive_sync({"payloadHash": stored["payloadHash"]})
        self.assertEqual(ctx.exception.status_code, 409)
        self.assertFalse(path.exists())

    def test_generic_zip_extracts_only_allowlisted_assets_into_unique_managed_folder(self) -> None:
        project = Path(self.tmp.name) / "UnityProject"
        (project / "Assets").mkdir(parents=True)
        (project / "ProjectSettings").mkdir()
        (project / "Packages").mkdir()
        data = make_zip_bytes({"Folder/example.prefab": b"prefab", "Folder/readme.txt": b"readme"})
        stored = self.vault.ingest(data=data, name="pack.zip", declared_type="application/zip", chat_id="chat-1")
        with (
            patch("dashboard_server.plan_outfit_import_sync", return_value={"ok": False, "error": "not an outfit"}),
            patch("dashboard_server.refresh_asset_database_sync", return_value={"ok": True}),
        ):
            result = dashboard_server.import_chat_archive_sync(
                {
                    "payloadHash": stored["payloadHash"],
                    "projectPath": str(project),
                    "targetFolder": "Assets/VRCForge/Imports",
                }
            )
        self.assertTrue(result["ok"])
        self.assertEqual(result["kind"], "managed_zip_extract")
        self.assertEqual(result["copiedFileCount"], 2)
        target = project / result["targetFolder"]
        self.assertEqual((target / "Folder" / "example.prefab").read_bytes(), b"prefab")

    def test_generic_zip_rejects_scripts_and_meta_guid_injection_without_residue(self) -> None:
        project = Path(self.tmp.name) / "UnsafeUnityProject"
        (project / "Assets").mkdir(parents=True)
        (project / "ProjectSettings").mkdir()
        (project / "Packages").mkdir()
        for entry_name in ("Folder/EditorPayload.cs", "Folder/example.prefab.meta"):
            data = make_zip_bytes({entry_name: b"unsafe"})
            stored = self.vault.ingest(
                data=data,
                name=f"unsafe-{Path(entry_name).suffix[1:]}.zip",
                declared_type="application/zip",
                chat_id="chat-1",
            )
            with patch("dashboard_server.plan_outfit_import_sync", return_value={"ok": False, "error": "not an outfit"}):
                with self.assertRaises(dashboard_server.AgentGatewayError):
                    dashboard_server.import_chat_archive_sync(
                        {
                            "payloadHash": stored["payloadHash"],
                            "projectPath": str(project),
                            "targetFolder": "Assets/VRCForge/Imports",
                        }
                    )
            imports = project / "Assets" / "VRCForge" / "Imports"
            self.assertFalse(imports.exists() and any(imports.iterdir()))

    def test_archive_inspection_exposes_only_bounded_semantic_fields_to_planner(self) -> None:
        data = make_zip_bytes({"Assets/example.prefab": b"prefab"})
        stored = self.vault.ingest(data=data, name="pack.zip", declared_type="application/zip", chat_id="chat-1")
        result = dashboard_server.inspect_chat_attachment_sync({"payloadHash": stored["payloadHash"]})
        projected = planner_safe_tool_result_fields(result)
        self.assertIn("summary", projected)
        self.assertIn("summaryText", projected)
        self.assertEqual(projected["entryCount"], 1)
        self.assertNotIn("listing", projected)
        self.assertNotIn("archiveGuard", projected)


if __name__ == "__main__":
    unittest.main()
