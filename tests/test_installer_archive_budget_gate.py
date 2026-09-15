from pathlib import Path
import zipfile

import pytest

from scripts.smoke_packaged_backend import validate_installer_archive_entry_count


@pytest.mark.parametrize(("limit", "accepted"), [(4, False), (5, True), (8, True)])
def test_release_archive_is_checked_against_its_own_installer_limit(
    tmp_path: Path, limit: int, accepted: bool,
) -> None:
    payload = tmp_path / "payload.zip"
    with zipfile.ZipFile(payload, "w") as archive:
        archive.writestr("installer/VRCForge_WebPayload.ps1", f"[int]$script:MaxEntryCount = {limit}\n")
        for index in range(4):
            archive.writestr(f"backend/{index}.txt", b"content")
    if accepted:
        assert validate_installer_archive_entry_count(payload) == {"entries": 5, "limit": limit}
    else:
        with pytest.raises(ValueError, match="installer entry limit"):
            validate_installer_archive_entry_count(payload)


def test_release_archive_without_a_readable_installer_limit_is_rejected(tmp_path: Path) -> None:
    payload = tmp_path / "payload.zip"
    with zipfile.ZipFile(payload, "w") as archive:
        archive.writestr("installer/VRCForge_WebPayload.ps1", "# no limit\n")
    with pytest.raises(ValueError, match="installer entry limit"):
        validate_installer_archive_entry_count(payload)
