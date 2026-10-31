from pathlib import Path

from tests.test_external_installed_skill_registry import isolated_installed_skill_gateway, external_result


def _committed(response, *, changed=True):
    assert response["ok"] is True, response
    outcome = response["outcome"]
    assert outcome["commitState"] == ("committed" if changed else "no_change"), response
    assert outcome["committed"] is True
    assert outcome["mutationStarted"] is changed
    assert outcome["readbackState"] == "verified"
    assert not outcome.get("recovery", {}).get("required", False)


def test_actual_mcp_creator_receipt_reuses_created_registry_readback(tmp_path: Path, monkeypatch):
    monkeypatch.delenv("VRCFORGE_USER_DATA_DIR", raising=False)
    with isolated_installed_skill_gateway(tmp_path):
        created = external_result("vrcforge_create_installed_skill", {
            "name": "receipt-created-skill", "description": "Receipt fixture.",
            "instructions": "Read project state.",
        })
        _committed(created)
        assert (tmp_path / "skills/receipt-created-skill/SKILL.md").is_file()


def test_actual_mcp_path_write_export_import_and_reinstall_receipts(tmp_path: Path, monkeypatch):
    monkeypatch.delenv("VRCFORGE_USER_DATA_DIR", raising=False)
    with isolated_installed_skill_gateway(tmp_path):
        identity = {
            "summary": {"status": "passed", "workflow": "captured_workflow", "steps": ["inspect"]},
            "packageId": "community.receipt.workflow", "skillName": "receipt-workflow",
            "title": "Receipt Workflow",
        }
        no_write = external_result("vrcforge_write_path_to_skill", identity)
        _committed(no_write, changed=False)
        package = tmp_path / "workflow.vsk"
        written = external_result("vrcforge_write_path_to_skill", {
            **identity, "outputPath": str(tmp_path / "source"),
            "exportVsk": True, "confirmExport": True, "packageOutputPath": str(package),
        })
        _committed(written)
        assert package.is_file()
        _committed(external_result("vrcforge_import_skill_package", {"packagePath": str(package)}))
        # The current projector replaces its files even for an unchanged store.
        _committed(external_result("vrcforge_import_skill_package", {"packagePath": str(package)}))
        _committed(external_result("vrcforge_import_skill_package", {"packagePath": str(package), "projectToUserSkills": False}), changed=False)
        exported = tmp_path / "reexport.vsk"
        _committed(external_result("vrcforge_export_skill_package", {"skillName": "receipt-workflow", "outputPath": str(exported)}))
        assert exported.is_file()
