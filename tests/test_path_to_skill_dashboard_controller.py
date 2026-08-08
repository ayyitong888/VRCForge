from __future__ import annotations

import ast
from pathlib import Path

import pytest

import dashboard_server
from path_to_skill_controller import (
    PathToSkillControllerError,
    PathToSkillPreviewService,
    PathToSkillWritePorts,
    PathToSkillWriteService,
    path_to_skill_file_list,
    path_to_skill_kwargs,
    path_to_skill_vsk_filename,
)


ROOT = Path(__file__).parents[1]
LEGACY_ROOTS = {
    "_path_to_skill_kwargs",
    "_path_to_skill_file_list",
    "_path_to_skill_vsk_filename",
    "capture_path_to_skill_sync",
}


def _summary() -> dict[str, object]:
    return {
        "status": "passed",
        "workflow": "captured_workflow",
        "steps": ["captured.read"],
    }


def test_path_to_skill_has_separate_typed_preview_and_write_owners() -> None:
    source = (ROOT / "path_to_skill_controller.py").read_text(encoding="utf-8")
    dashboard_source = (ROOT / "dashboard_server.py").read_text(encoding="utf-8")
    tree = ast.parse(source)
    dashboard_tree = ast.parse(dashboard_source)
    dashboard_bindings = {
        node.name
        for node in dashboard_tree.body
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef))
    }
    dashboard_imports = {
        alias.asname or alias.name
        for node in dashboard_tree.body
        if isinstance(node, (ast.Import, ast.ImportFrom))
        for alias in node.names
    }
    preview = next(
        node
        for node in tree.body
        if isinstance(node, ast.ClassDef) and node.name == "PathToSkillPreviewService"
    )
    preview_identifiers = {
        node.id if isinstance(node, ast.Name) else node.attr
        for node in ast.walk(preview)
        if isinstance(node, (ast.Name, ast.Attribute))
    }

    assert LEGACY_ROOTS.isdisjoint(dashboard_bindings)
    assert "build_path_to_skill_source" not in dashboard_imports
    assert not hasattr(dashboard_server, "build_path_to_skill_source")
    assert "_PATH_TO_SKILL_CONTROLLER" not in dashboard_source
    assert "PATH_TO_SKILL_PREVIEW = PathToSkillPreviewService()" in dashboard_source
    assert "PATH_TO_SKILL_WRITE = PathToSkillWriteService(" in dashboard_source
    assert "PATH_TO_SKILL_PREVIEW.preview" in dashboard_source
    assert "PATH_TO_SKILL_WRITE.write" in dashboard_source
    assert PathToSkillPreviewService.__slots__ == ()
    assert PathToSkillWriteService.__slots__ == ("_ports",)
    assert set(PathToSkillWritePorts.__dataclass_fields__) == {
        "path_lexists",
        "make_temp_root",
        "write_source",
        "ensure_parent",
        "export_dev",
    }
    for forbidden in (
        "_host",
        "_impl_",
        "__getattr__",
        "sys.modules",
        "dashboard_server import",
        "agent_gateway",
        "SkillPackageController",
        "SkillPackageGovernanceService",
        "SKILL_PACKAGE_WRITE_LOCK",
    ):
        assert forbidden not in source
    for forbidden in {
        "PathToSkillWritePorts",
        "path_lexists",
        "make_temp_root",
        "write_source",
        "ensure_parent",
        "export_dev",
        "mkdir",
        "write_to",
    }:
        assert forbidden not in preview_identifiers


def test_path_to_skill_pure_helpers_preserve_aliases_sorting_and_filename() -> None:
    assert path_to_skill_kwargs(
        {
            "packageId": "  community.path-to-skill.test  ",
            "package_id": "ignored",
            "skill_name": "  skill-name  ",
            "title": "  Title  ",
            "version": "  2.0.0  ",
            "author": "  Author  ",
            "minVrcforgeVersion": "  1.4.0  ",
        }
    ) == {
        "package_id": "community.path-to-skill.test",
        "skill_name": "skill-name",
        "title": "Title",
        "version": "2.0.0",
        "author": "Author",
        "min_vrcforge_version": "1.4.0",
    }
    assert path_to_skill_file_list({"z.txt": "é", "a.txt": "abc"}) == [
        {"path": "a.txt", "bytes": 3},
        {"path": "z.txt", "bytes": 2},
    ]
    assert path_to_skill_vsk_filename({"skill_name": " .Bad name!? "}) == "Bad-name.vsk"
    assert path_to_skill_vsk_filename({}) == "captured-skill.vsk"


def test_preview_suppresses_every_write_hint_without_write_capability() -> None:
    result = PathToSkillPreviewService().preview(
        {
            "summary": _summary(),
            "packageId": "community.path-to-skill.preview",
            "outputPath": "must-not-exist",
            "writeSource": True,
            "exportVsk": True,
        }
    )

    assert result["ok"] is True
    assert result["dryRun"] is True
    assert result["writeSuppressed"] is True
    assert result["manifest"]["id"] == "community.path-to-skill.preview"
    assert "writtenSource" not in result
    assert "exported" not in result


@pytest.mark.parametrize("service", [PathToSkillPreviewService(), "write"])
def test_both_entrypoints_reject_missing_summary_before_effects(service: object) -> None:
    calls: list[str] = []
    if service == "write":
        service = PathToSkillWriteService(
            PathToSkillWritePorts(
                path_lexists=lambda _path: calls.append("lexists") or False,
                make_temp_root=lambda: calls.append("temp") or Path("temp"),
                write_source=lambda _capture, _path: calls.append("write"),
                ensure_parent=lambda _path: calls.append("mkdir"),
                export_dev=lambda _source, _target: calls.append("export") or {},
            )
        )

    with pytest.raises(PathToSkillControllerError, match="summary is required") as raised:
        (service.write if isinstance(service, PathToSkillWriteService) else service.preview)({})

    assert raised.value.status_code == 400
    assert calls == []


def test_write_owner_uses_one_temp_source_and_fixed_dev_export_ports() -> None:
    calls: list[tuple[object, ...]] = []
    service = PathToSkillWriteService(
        PathToSkillWritePorts(
            path_lexists=lambda path: calls.append(("lexists", path)) or False,
            make_temp_root=lambda: calls.append(("temp",)) or Path("temp-root"),
            write_source=lambda capture, path: calls.append(
                ("write", capture.manifest["id"], path)
            ),
            ensure_parent=lambda path: calls.append(("mkdir", path)),
            export_dev=lambda source, target: calls.append(
                ("export", source, target)
            )
            or {"package_path": str(target), "signature_status": "dev"},
        )
    )

    result = service.write(
        {
            "summary": _summary(),
            "packageId": "community.path-to-skill.write",
            "skillName": "Write Skill",
            "exportVsk": True,
            "confirmExport": True,
        }
    )

    source = Path("temp-root/source")
    package = Path("temp-root/write-skill.vsk")
    assert result["dryRun"] is False
    assert result["writtenSource"]["path"] == str(source)
    assert result["exported"] == {
        "package_path": str(package),
        "signature_status": "dev",
    }
    assert calls == [
        ("temp",),
        ("write", "community.path-to-skill.write", source),
        ("lexists", package),
        ("mkdir", Path("temp-root")),
        ("export", source, package),
    ]


def test_existing_explicit_package_fails_before_capture_or_source_write() -> None:
    calls: list[tuple[str, Path]] = []
    service = PathToSkillWriteService(
        PathToSkillWritePorts(
            path_lexists=lambda path: calls.append(("lexists", path)) or True,
            make_temp_root=lambda: (_ for _ in ()).throw(AssertionError("no temp")),
            write_source=lambda _capture, _path: (_ for _ in ()).throw(
                AssertionError("no write")
            ),
            ensure_parent=lambda _path: (_ for _ in ()).throw(AssertionError("no mkdir")),
            export_dev=lambda _source, _target: (_ for _ in ()).throw(
                AssertionError("no export")
            ),
        )
    )

    with pytest.raises(PathToSkillControllerError, match="already exists"):
        service.write(
            {
                "summary": _summary(),
                "exportVsk": True,
                "confirmExport": True,
                "packageOutputPath": "existing-package",
            }
        )

    assert calls == [("lexists", Path("existing-package.vsk"))]


def test_write_entrypoint_without_write_flags_preserves_no_effect_result() -> None:
    service = PathToSkillWriteService(
        PathToSkillWritePorts(
            path_lexists=lambda _path: (_ for _ in ()).throw(
                AssertionError("no path check")
            ),
            make_temp_root=lambda: (_ for _ in ()).throw(AssertionError("no temp")),
            write_source=lambda _capture, _path: (_ for _ in ()).throw(
                AssertionError("no write")
            ),
            ensure_parent=lambda _path: (_ for _ in ()).throw(AssertionError("no mkdir")),
            export_dev=lambda _source, _target: (_ for _ in ()).throw(
                AssertionError("no export")
            ),
        )
    )

    result = service.write({"summary": _summary()})

    assert result["ok"] is True
    assert result["dryRun"] is False
    assert "writtenSource" not in result
    assert "exported" not in result
