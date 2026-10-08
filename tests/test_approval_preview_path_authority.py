from __future__ import annotations

from pathlib import Path

import pytest

from agent_gateway import AgentGateway, summarize_params


def _manual_reason(root: Path, arguments: dict, preview: dict) -> str:
    gateway = AgentGateway(root / "config.json", root / "audit")
    return gateway.approval_transactions._write_auto_manual_approval_reason(  # noqa: SLF001
        "vrcforge_fixture_write",
        arguments,
        preview,
    )


def test_truncated_top_level_preview_summary_does_not_reject_long_project_path(
    tmp_path: Path,
) -> None:
    project = tmp_path / ("project-" + "x" * 180)
    target = project / "Assets" / "fixture.asset"
    arguments = {"projectRoot": str(project), "outputPath": str(target)}
    projection = summarize_params(arguments)

    assert len(str(project)) > 140
    assert projection["projectRoot"] != str(project)
    assert _manual_reason(
        tmp_path,
        arguments,
        {"paramsSummary": projection},
    ) == ""


def test_outside_project_owner_argument_still_requires_manual_approval(
    tmp_path: Path,
) -> None:
    project = tmp_path / "UnityProject"
    arguments = {
        "projectRoot": str(project),
        "outputPath": str(tmp_path / "outside.asset"),
    }

    assert "outside the selected project" in _manual_reason(tmp_path, arguments, {})


def test_outside_project_genuine_preview_path_still_requires_manual_approval(
    tmp_path: Path,
) -> None:
    project = tmp_path / "UnityProject"
    preview = {
        "projectRoot": str(project),
        "plan": {"outputPath": str(tmp_path / "outside.asset")},
    }

    assert "outside the selected project" in _manual_reason(
        tmp_path,
        {"projectRoot": str(project)},
        preview,
    )


def test_nested_preview_params_summary_remains_authoritative(
    tmp_path: Path,
) -> None:
    project = tmp_path / "UnityProject"
    preview = {
        "projectRoot": str(project),
        "details": {"paramsSummary": {"outputPath": str(tmp_path / "outside.asset")}},
    }

    assert "outside the selected project" in _manual_reason(
        tmp_path,
        {"projectRoot": str(project)},
        preview,
    )


@pytest.mark.parametrize("field", ["paramsSummary.outputPath", "ParamsSummary"])
def test_similar_preview_field_names_remain_authoritative(tmp_path: Path, field: str) -> None:
    project = tmp_path / "UnityProject"
    outside = str(tmp_path / "outside.asset")
    preview = {field: {"outputPath": outside}}

    assert "outside the selected project" in _manual_reason(
        tmp_path, {"projectRoot": str(project)}, preview,
    )


def test_owner_arguments_named_params_summary_remain_authoritative(tmp_path: Path) -> None:
    project = tmp_path / "UnityProject"
    arguments = {"projectRoot": str(project),
                 "paramsSummary": {"outputPath": str(tmp_path / "outside.asset")}}

    assert "outside the selected project" in _manual_reason(
        tmp_path, arguments, {"paramsSummary": summarize_params(arguments)},
    )


@pytest.mark.parametrize("tool", ["vrcforge_delete_asset", "vrcforge_restore_checkpoint", "vrcforge_export_vrm"])
def test_display_projection_exclusion_does_not_bypass_protected_tool_approval(tmp_path: Path, tool: str) -> None:
    gateway = AgentGateway(tmp_path / "config.json", tmp_path / "audit")

    assert gateway.approval_transactions._write_auto_manual_approval_reason(  # noqa: SLF001
        tool, {"projectRoot": str(tmp_path / "UnityProject")}, {"paramsSummary": {}},
    )
