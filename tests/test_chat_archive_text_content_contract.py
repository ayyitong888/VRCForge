"""The archive text reader must not label binary bytes as successful text."""
import io
import tarfile
import zipfile

import pytest

from chat_attachment_vault import (
    ENTRY_TEXT_MAX_BYTES,
    ChatAttachmentVaultError,
    extract_archive_entry_text,
)


def _archive(tmp_path, kind, data):
    target = tmp_path / ("source.zip" if kind == "zip" else "source.unitypackage")
    if kind == "zip":
        with zipfile.ZipFile(target, "w") as archive:
            archive.writestr("item", data)
    else:
        with tarfile.open(target, "w:gz") as archive:
            info = tarfile.TarInfo("item")
            info.size = len(data)
            archive.addfile(info, io.BytesIO(data))
    return target


@pytest.mark.parametrize("kind", ["zip", "unitypackage"])
@pytest.mark.parametrize("data", [b"\x89PNG\r\n\x1a\n\x00pixels", b"readable-prefix\x00binary-tail", b"readable-prefix\xffinvalid-utf8"])
def test_binary_entry_is_rejected_before_success_text(tmp_path, kind, data):
    target = _archive(tmp_path, kind, data)
    with pytest.raises(ChatAttachmentVaultError) as error:
        extract_archive_entry_text(target, kind, "item")
    assert error.value.reason == "binary_entry"


@pytest.mark.parametrize("kind", ["zip", "unitypackage"])
def test_utf8_text_preserves_body_and_declared_truncation(tmp_path, kind):
    body = "正文😀\nsecond line".encode("utf-8")
    result = extract_archive_entry_text(_archive(tmp_path, kind, body), kind, "item")
    assert result["text"] == body.decode("utf-8")
    assert result["truncated"] is False
    assert result["binaryLikely"] is False


@pytest.mark.parametrize("kind", ["zip", "unitypackage"])
def test_valid_utf8_split_at_read_limit_is_not_misclassified_binary(tmp_path, kind):
    prefix = b"A" * (ENTRY_TEXT_MAX_BYTES - 1)
    result = extract_archive_entry_text(_archive(tmp_path, kind, prefix + "😀tail".encode("utf-8")), kind, "item")
    assert result["text"] == prefix.decode("ascii")
    assert result["truncated"] is True
    assert "\ufffd" not in result["text"]


@pytest.mark.parametrize("kind", ["zip", "unitypackage"])
def test_malformed_utf8_at_true_end_is_rejected(tmp_path, kind):
    with pytest.raises(ChatAttachmentVaultError) as error:
        extract_archive_entry_text(_archive(tmp_path, kind, b"prefix\xf0\x9f"), kind, "item")
    assert error.value.reason == "binary_entry"
