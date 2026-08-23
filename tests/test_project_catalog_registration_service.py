from __future__ import annotations

import json
from pathlib import Path

import pytest

from project_catalog_registration_service import (
    ProjectCatalogRegistrationError,
    ProjectCatalogRegistrationService,
)


def _project(root: Path, version: str = "2022.3.22f1") -> Path:
    (root / "Assets").mkdir(parents=True)
    (root / "Packages").mkdir()
    (root / "ProjectSettings").mkdir()
    (root / "Packages" / "manifest.json").write_text("{}", encoding="utf-8")
    (root / "ProjectSettings" / "ProjectVersion.txt").write_text(
        f"m_EditorVersion: {version}\n"
        f"m_EditorVersionWithRevision: {version} (887be4894c44)\n",
        encoding="utf-8",
    )
    return root


def _service(tmp_path: Path) -> tuple[ProjectCatalogRegistrationService, dict[str, Path]]:
    paths = {
        "vcc": tmp_path / "VCC" / "settings.json",
        "alcom": tmp_path / "ALCOM" / "settings.json",
        "unityHub": tmp_path / "UnityHub" / "projects-v1.json",
    }
    for path in paths.values():
        path.parent.mkdir(parents=True, exist_ok=True)
    paths["vcc"].write_text(json.dumps({"userProjects": [], "keep": 7}), encoding="utf-8")
    paths["alcom"].write_text(json.dumps({"projects": [], "keep": 8}), encoding="utf-8")
    paths["unityHub"].write_text(
        json.dumps({"schema_version": "v1", "data": {}, "keep": 9}),
        encoding="utf-8",
    )
    service = ProjectCatalogRegistrationService(
        receipts_dir=tmp_path / "receipts",
        catalog_paths={name: [path] for name, path in paths.items()},
    )
    return service, paths


@pytest.mark.parametrize("catalog", ["vcc", "alcom", "unityHub"])
def test_registers_exactly_one_catalog_and_is_idempotent_with_approved_rollback(
    tmp_path: Path,
    catalog: str,
) -> None:
    service, paths = _service(tmp_path)
    project = _project(tmp_path / "Avatar Project")
    untouched = {name: path.read_bytes() for name, path in paths.items() if name != catalog}

    prepared, preview = service.prepare_register(
        {"catalog": catalog, "projectPath": str(project)},
        None,
    )
    assert preview["mutationStarted"] is False
    result = service.register(prepared)

    assert result["ok"] is True
    assert result["catalog"] == catalog
    assert result["mutationStarted"] is True
    assert result["committed"] is True
    assert result["reloadRequired"] is True
    assert all(paths[name].read_bytes() == value for name, value in untouched.items())
    status = service.status({"projectPath": str(project)})
    by_catalog = {item["catalog"]: item for item in status["catalogs"]}
    assert by_catalog[catalog]["registered"] is True

    again = service.register({"catalog": catalog, "projectPath": str(project)})
    assert again["mutationStarted"] is False
    rollback = service.rollback({"receiptId": result["rollback"]["receiptId"]})
    assert rollback["committed"] is True
    assert {item["catalog"]: item for item in service.status({"projectPath": str(project)})["catalogs"]}[catalog]["registered"] is False


def test_unity_hub_entry_uses_project_version_and_exact_path(tmp_path: Path) -> None:
    service, paths = _service(tmp_path)
    project = _project(tmp_path / "Hub Avatar", "2022.3.22f1")

    service.register({"catalog": "unityHub", "projectPath": str(project)})

    payload = json.loads(paths["unityHub"].read_text(encoding="utf-8"))
    entry = payload["data"][str(project)]
    assert entry["title"] == "Hub Avatar"
    assert entry["path"] == str(project)
    assert entry["version"] == "2022.3.22f1"
    assert entry["changeset"] == "887be4894c44"
    assert payload["keep"] == 9


def test_prepared_registration_refuses_catalogue_drift_before_write(tmp_path: Path) -> None:
    service, paths = _service(tmp_path)
    project = _project(tmp_path / "Avatar Project")
    prepared, _preview = service.prepare_register(
        {"catalog": "vcc", "projectPath": str(project)},
        None,
    )
    paths["vcc"].write_text(json.dumps({"userProjects": [], "changed": True}), encoding="utf-8")

    with pytest.raises(ProjectCatalogRegistrationError, match="changed after preparation"):
        service.register(prepared)

    assert json.loads(paths["vcc"].read_text(encoding="utf-8"))["changed"] is True


def test_receipt_failure_restores_exact_catalogue_bytes(tmp_path: Path, monkeypatch) -> None:
    service, paths = _service(tmp_path)
    project = _project(tmp_path / "Avatar Project")
    before = paths["vcc"].read_bytes()
    monkeypatch.setattr(service, "_write_receipt", lambda _payload: (_ for _ in ()).throw(OSError("receipt failed")))

    with pytest.raises(ProjectCatalogRegistrationError, match="receipt failed"):
        service.register({"catalog": "vcc", "projectPath": str(project)})

    assert paths["vcc"].read_bytes() == before


def test_unknown_or_incompatible_catalogue_is_read_only_failure(tmp_path: Path) -> None:
    service, paths = _service(tmp_path)
    project = _project(tmp_path / "Avatar Project")
    paths["vcc"].write_text(json.dumps({"projects": []}), encoding="utf-8")
    before = paths["vcc"].read_bytes()

    with pytest.raises(ProjectCatalogRegistrationError, match="Unsupported VCC settings schema"):
        service.register({"catalog": "vcc", "projectPath": str(project)})

    assert paths["vcc"].read_bytes() == before
