"""The nested ZIP seal is the selected member identity, not the whole manifest."""
from pathlib import Path
import re

ROOT = Path(__file__).resolve().parents[1]
SOURCE = (ROOT / "unity_import_checkpoint.py").read_text(encoding="utf-8-sig")


def test_seal_check_compares_the_selected_entry_not_the_whole_manifest():
    assert 'manifest != facts.get("manifest")' not in SOURCE
    match = re.search(r'if not any\(\s*selected == \{\*\*entry, "targetName": target_name\} for entry in manifest\s*\):', SOURCE)
    assert match, "selected-entry seal comparison missing"
    # The selected entry itself still pins the full identity: sha256 + crc + sizes.
    for required in ('"sha256": entry["sha256"]', '"crc": entry["crc"]'):
        in_producer = (ROOT / "prepared_archive_imports.py").read_text(encoding="utf-8-sig")
        assert required in in_producer


def test_seal_rejection_message_names_the_identity():
    assert "drifted from its sealed identity" in SOURCE
    assert "drifted from its sealed manifest" not in SOURCE
