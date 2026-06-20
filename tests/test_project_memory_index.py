from __future__ import annotations

import json
from pathlib import Path

from project_memory_index import scan_project_memory


def make_unity_project(root: Path) -> None:
    (root / "Assets" / "Avatar").mkdir(parents=True)
    (root / "Packages").mkdir()
    (root / "ProjectSettings").mkdir()
    (root / "Assets" / "Avatar" / "Hero.prefab").write_text("%YAML prefab", encoding="utf-8")
    (root / "Assets" / "Avatar" / "Hero.prefab.meta").write_text(
        "fileFormatVersion: 2\nguid: 0123456789abcdef0123456789abcdef\n",
        encoding="utf-8",
    )
    (root / "Assets" / "Avatar" / "Body.mat").write_text("material", encoding="utf-8")
    (root / "Packages" / "manifest.json").write_text('{"dependencies":{}}', encoding="utf-8")
    (root / "ProjectSettings" / "ProjectVersion.txt").write_text("m_EditorVersion: 2022.3.22f1", encoding="utf-8")


def test_project_memory_second_scan_reuses_hashes_and_reports_no_changes(tmp_path: Path) -> None:
    project = tmp_path / "UnityProject"
    index_root = tmp_path / "indexes"
    make_unity_project(project)

    first = scan_project_memory(project, index_root)
    second = scan_project_memory(project, index_root)

    assert first["ok"] is True
    assert first["summary"]["firstScan"] is True
    assert first["summary"]["addedFiles"] >= 4
    assert second["ok"] is True
    assert second["summary"]["firstScan"] is False
    assert second["summary"]["changed"] is False
    assert second["summary"]["hashesReused"] == second["summary"]["totalFiles"]
    assert second["summary"]["hashesComputed"] == 0
    assert second["privacy"]["binaryAssetContentsReturned"] is False


def test_project_memory_reports_modified_deleted_guid_and_package_deltas(tmp_path: Path) -> None:
    project = tmp_path / "UnityProject"
    index_root = tmp_path / "indexes"
    make_unity_project(project)
    scan_project_memory(project, index_root)

    (project / "Assets" / "Avatar" / "Hero.prefab").write_text("%YAML prefab changed", encoding="utf-8")
    (project / "Assets" / "Avatar" / "Hero.prefab.meta").write_text(
        "fileFormatVersion: 2\nguid: fedcba9876543210fedcba9876543210\n",
        encoding="utf-8",
    )
    (project / "Assets" / "Avatar" / "Body.mat").unlink()
    (project / "Packages" / "manifest.json").write_text('{"dependencies":{"nadena.dev.modular-avatar":"1.0.0"}}', encoding="utf-8")

    result = scan_project_memory(project, index_root)
    rendered = json.dumps(result, ensure_ascii=False)

    assert result["ok"] is True
    assert result["summary"]["modifiedFiles"] >= 3
    assert result["summary"]["deletedFiles"] == 1
    assert result["summary"]["guidChangeCount"] == 1
    assert "avatar" in result["summary"]["scannerFamilies"]
    assert "packages" in result["summary"]["scannerFamilies"]
    assert "validation" in result["summary"]["scannerFamilies"]
    assert "Assets/Avatar/Body.mat" in {item["path"] for item in result["changes"]["deleted"]}
    assert "fedcba9876543210fedcba9876543210" in rendered
    assert "%YAML prefab changed" not in rendered
