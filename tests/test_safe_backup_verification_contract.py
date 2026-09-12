from __future__ import annotations

from unittest.mock import patch
from types import SimpleNamespace

import dashboard_server


def _core_backup_payload() -> dict:
    return {
        "type": "vrcforge_safe_backup",
        "backup_id": "vrcforge_backup_test",
        "backup_path": "D:/project/Library/VRCForge/Backups/vrcforge_backup_test",
        "files": [
            {
                "project_relative_path": "Assets/Test.anim",
                "backup_relative_path": "files/Assets/Test.anim",
                "sha256": "abc123",
                "byte_count": 12,
                "after_exists": True,
                "after_sha256": "abc123",
                "status": "succeeded",
            }
        ],
    }


def _core_restore_payload() -> dict:
    rows = [
        {
            "project_relative_path": "Assets/Test.mat",
            "backup_relative_path": "files/Assets/Test.mat",
            "target_exists": True,
            "changed_since_backup": True,
            "current_sha256": "before-mat",
            "backup_sha256": "after-mat",
            "after_exists": True,
            "after_sha256": "after-mat",
            "status": "succeeded",
            "error": "",
        },
        {
            "project_relative_path": "Assets/Test.mat.meta",
            "backup_relative_path": "files/Assets/Test.mat.meta",
            "target_exists": True,
            "changed_since_backup": False,
            "current_sha256": "meta-sha",
            "backup_sha256": "meta-sha",
            "after_exists": True,
            "after_sha256": "meta-sha",
            "status": "succeeded",
            "error": "",
        },
    ]
    return {
        "type": "vrcforge_safe_backup_restore",
        "version": 1,
        "backup_id": "vrcforge_backup_test",
        "backup_path": "D:/project/.vrcforge/backups/vrcforge_backup_test",
        "confirmed": True,
        "requires_confirmation": False,
        "project_identity_matches": True,
        "planned": [dict(row) for row in rows],
        "restored": [dict(row) for row in rows],
        "skipped": [],
        "warnings": [],
        "summary": {"plannedCount": 2, "restoredCount": 2, "skippedCount": 0, "warningCount": 0},
        "ok": True,
    }


def test_create_safe_backup_projects_core_hash_readback_as_standard_receipt() -> None:
    payload = _core_backup_payload()
    with (
        patch("dashboard_server.load_dashboard_settings", return_value=object()),
        patch(
            "dashboard_server.invoke_unity_mcp",
            return_value=SimpleNamespace(payload={"data": payload}),
        ),
        patch("dashboard_server.emit_log"),
    ):
        result = dashboard_server.create_safe_backup_sync(
            {"projectPath": "D:/project", "assetPaths": ["Assets/Test.anim"]}
        )

    assert result["schema"] == "vrcforge.safe_backup_receipt.v1"
    assert result["verified"] is True
    assert result["readback"]["fileCount"] == 1
    assert result["readback"]["files"][0]["sha256"] == "abc123"
    assert result["verification"] == {
        "state": "passed",
        "checks": [
            {
                "kind": "safe_backup_file_hash_readback",
                "state": "passed",
                "fileCount": 1,
            }
        ],
    }


def test_create_safe_backup_does_not_claim_verification_for_mismatched_readback() -> None:
    payload = _core_backup_payload()
    payload["files"][0]["after_sha256"] = "different"
    dashboard_server._project_safe_backup_verification(payload)
    assert "verified" not in payload
    assert "readback" not in payload
    assert "verification" not in payload


def test_restore_safe_backup_projects_every_file_hash_readback() -> None:
    payload = _core_restore_payload()

    with (
        patch("dashboard_server.load_dashboard_settings", return_value=object()),
        patch(
            "dashboard_server.invoke_unity_mcp",
            return_value=SimpleNamespace(payload={"data": payload}),
        ),
        patch("dashboard_server.emit_log"),
    ):
        result = dashboard_server.restore_safe_backup_sync(
            {"projectPath": "D:/project", "backupId": "vrcforge_backup_test"}
        )

    assert result["schema"] == "vrcforge.safe_backup_restore_receipt.v1"
    assert result["verified"] is True
    assert result["readback"]["fileCount"] == 2
    assert result["readback"]["files"][0]["sha256"] == "after-mat"
    assert result["verification"] == {
        "state": "passed",
        "checks": [
            {
                "kind": "safe_backup_restore_file_hash_readback",
                "state": "passed",
                "fileCount": 2,
            }
        ],
    }


def test_restore_safe_backup_does_not_claim_verification_for_bad_or_partial_receipt() -> None:
    bad_hash = _core_restore_payload()
    bad_hash["planned"][0]["after_sha256"] = "wrong"
    dashboard_server._project_safe_backup_restore_verification(bad_hash)
    assert "verified" not in bad_hash

    partial = _core_restore_payload()
    partial["restored"][1]["status"] = "failed"
    partial["restored"][1]["error"] = "write failed"
    dashboard_server._project_safe_backup_restore_verification(partial)
    assert "verified" not in partial
