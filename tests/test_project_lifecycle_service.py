from __future__ import annotations

import json
from pathlib import Path

import pytest

from project_lifecycle_service import ProjectLifecycleError, ProjectLifecycleService


def _unity_project(root: Path) -> Path:
    (root / "Assets").mkdir(parents=True)
    (root / "Packages").mkdir()
    (root / "ProjectSettings").mkdir()
    (root / "Packages" / "manifest.json").write_text(
        json.dumps({"dependencies": {"com.vrchat.avatars": "3.10.0"}}),
        encoding="utf-8",
    )
    (root / "ProjectSettings" / "ProjectVersion.txt").write_text(
        "m_EditorVersion: 2022.3.22f1\n",
        encoding="utf-8",
    )
    (root / "Assets" / "Template.txt").write_text("template", encoding="utf-8")
    return root


def _service(tmp_path: Path) -> ProjectLifecycleService:
    return ProjectLifecycleService(
        prefs_path=tmp_path / "state" / "custom-projects.json",
        receipts_dir=tmp_path / "state" / "project-lifecycle-receipts",
        template_roots=[tmp_path / "templates"],
    )


def test_create_project_is_staged_registered_and_rollback_is_recoverable(tmp_path: Path) -> None:
    template = _unity_project(tmp_path / "templates" / "Avatar")
    service = _service(tmp_path)
    target = tmp_path / "projects" / "Sapphy Manuka Dogfood"
    target.parent.mkdir()

    prepared, preview = service.prepare_create(
        {
            "projectPath": str(target),
            "projectName": target.name,
            "template": "Avatar",
            "templatePath": str(template),
        },
        None,
    )
    assert preview["mutationStarted"] is False
    assert preview["backend"] == "installed_template"

    created = service.create_project(prepared)

    assert created["ok"] is True
    assert created["commitState"] == "complete"
    assert created["registeredInVRCForge"] is True
    assert (target / "Assets" / "Template.txt").read_text(encoding="utf-8") == "template"
    assert not list(target.parent.glob(".*.vrcforge-create-*"))
    prefs = json.loads((tmp_path / "state" / "custom-projects.json").read_text(encoding="utf-8"))
    assert prefs["customProjects"] == [{"path": str(target), "projectType": "unity"}]

    rolled_back = service.rollback({"receiptId": created["rollback"]["receiptId"]})

    assert rolled_back["ok"] is True
    assert rolled_back["commitState"] == "complete"
    assert not target.exists()
    recovery_path = Path(rolled_back["recoveryPath"])
    assert recovery_path.is_dir()
    assert recovery_path.name.startswith("VRCForge_Rollback_Sapphy Manuka Dogfood_")
    prefs_after = json.loads((tmp_path / "state" / "custom-projects.json").read_text(encoding="utf-8"))
    assert prefs_after["customProjects"] == []


def test_create_project_refuses_existing_target_without_mutation(tmp_path: Path) -> None:
    template = _unity_project(tmp_path / "templates" / "Avatar")
    service = _service(tmp_path)
    target = tmp_path / "projects" / "Existing"
    target.mkdir(parents=True)
    sentinel = target / "keep.txt"
    sentinel.write_text("keep", encoding="utf-8")

    with pytest.raises(ProjectLifecycleError, match="already exists"):
        service.prepare_create(
            {"projectPath": str(target), "templatePath": str(template)},
            None,
        )

    assert sentinel.read_text(encoding="utf-8") == "keep"


def test_create_failure_removes_staging_and_leaves_target_absent(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    template = _unity_project(tmp_path / "templates" / "Avatar")
    service = _service(tmp_path)
    target = tmp_path / "projects" / "Failure"
    target.parent.mkdir()
    prepared, _preview = service.prepare_create(
        {"projectPath": str(target), "templatePath": str(template)},
        None,
    )

    def fail_registration(_project_path: Path) -> dict[str, object]:
        raise OSError("injected prefs failure")

    monkeypatch.setattr(service, "_register_project", fail_registration)
    with pytest.raises(ProjectLifecycleError, match="injected prefs failure"):
        service.create_project(prepared)

    assert not target.exists()
    assert not list(target.parent.glob(".*.vrcforge-create-*"))


def test_register_project_is_idempotent_and_has_exact_rollback(tmp_path: Path) -> None:
    project = _unity_project(tmp_path / "projects" / "Existing")
    service = _service(tmp_path)

    first = service.register_project({"projectPath": str(project)})
    second = service.register_project({"projectPath": str(project)})

    assert first["ok"] is True and first["mutationStarted"] is True
    assert second["ok"] is True and second["mutationStarted"] is False
    rolled_back = service.rollback({"receiptId": first["rollback"]["receiptId"]})
    assert rolled_back["ok"] is True
    assert project.is_dir()
    prefs = json.loads((tmp_path / "state" / "custom-projects.json").read_text(encoding="utf-8"))
    assert prefs["customProjects"] == []


def test_rollback_refuses_created_project_after_user_changes(tmp_path: Path) -> None:
    template = _unity_project(tmp_path / "templates" / "Avatar")
    service = _service(tmp_path)
    target = tmp_path / "projects" / "Changed"
    target.parent.mkdir()
    prepared, _preview = service.prepare_create(
        {"projectPath": str(target), "templatePath": str(template)},
        None,
    )
    created = service.create_project(prepared)
    (target / "Assets" / "UserEdit.txt").write_text("do not remove", encoding="utf-8")

    with pytest.raises(ProjectLifecycleError, match="changed after creation"):
        service.rollback({"receiptId": created["rollback"]["receiptId"]})

    assert (target / "Assets" / "UserEdit.txt").is_file()


def test_status_and_plan_report_backend_capability_without_claiming_hub_registration(tmp_path: Path) -> None:
    template = _unity_project(tmp_path / "templates" / "Avatar")
    service = _service(tmp_path)
    target = tmp_path / "projects" / "Planned"
    target.parent.mkdir()

    status = service.status({})
    plan = service.plan_create(
        {"projectPath": str(target), "template": "Avatar", "templatePath": str(template)}
    )

    assert status["ok"] is True
    assert status["createCapable"] is True
    assert plan["ok"] is True
    assert plan["backend"] == "installed_template"
    assert plan["managerRegistration"]["vrcforge"] == "automatic"
    assert plan["managerRegistration"]["unityHub"] == "handoff_required"
    assert plan["mutationStarted"] is False
