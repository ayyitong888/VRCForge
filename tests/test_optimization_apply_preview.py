from __future__ import annotations

from pathlib import Path

import pytest

from optimization_apply_preview import (
    OptimizationApplyPreviewError,
    OptimizationApplyPreviewPorts,
    OptimizationApplyPreviewService,
)


def make_unity_project(root: Path) -> None:
    (root / "Assets").mkdir(parents=True)
    (root / "Packages").mkdir()
    (root / "ProjectSettings").mkdir()
    (root / "Packages" / "manifest.json").write_text(
        '{"dependencies":{}}',
        encoding="utf-8",
    )
    (root / "ProjectSettings" / "ProjectVersion.txt").write_text(
        "m_EditorVersion: 2022.3.22f1",
        encoding="utf-8",
    )


def install_package(root: Path, package_id: str, version: str) -> None:
    package_dir = root / "Packages" / package_id
    package_dir.mkdir(parents=True)
    (package_dir / "package.json").write_text(
        f'{{"name":"{package_id}","version":"{version}"}}',
        encoding="utf-8",
    )


def build_service(
    *,
    package_plans: list[dict] | None = None,
    parameter_previews: list[dict] | None = None,
    preview_error: str = "",
) -> OptimizationApplyPreviewService:
    plans = package_plans if package_plans is not None else []
    previews = parameter_previews if parameter_previews is not None else []

    def resolve_project(params: dict) -> str:
        value = str(params.get("projectPath") or "").strip()
        return str(Path(value).resolve()) if value else ""

    def package_plan(params: dict) -> dict:
        plans.append(dict(params))
        return {
            "canExecuteCommandInstall": True,
            "repository": params.get("repository") or "",
        }

    def build_parameter_arguments(params: dict) -> dict:
        return {
            "projectPath": str(Path(params["projectPath"]).resolve()),
            "toolName": "vrc_build_parameter_bit_packed_clone",
            "arguments": {
                "sourceScenePath": params["sourceScenePath"],
                "sourceAvatarPath": params["sourceAvatarPath"],
                "outputCloneName": params["outputCloneName"],
            },
        }

    def preview_parameter(params: dict) -> dict:
        previews.append(dict(params))
        if preview_error:
            raise OptimizationApplyPreviewError(preview_error)
        return {
            "ok": True,
            "preview": {"schema": "vrcforge.parameter_bit_packing_approval.v1"},
        }

    return OptimizationApplyPreviewService(
        OptimizationApplyPreviewPorts(
            resolve_project_path=resolve_project,
            package_install_plan=package_plan,
            build_parameter_bit_packing_arguments=build_parameter_arguments,
            preview_parameter_bit_packing=preview_parameter,
        )
    )


def test_lac_preview_is_ready_without_install_plan_when_dependency_exists(
    tmp_path: Path,
) -> None:
    project = tmp_path / "UnityProject"
    make_unity_project(project)
    install_package(project, "dev.limitex.avatar-compressor", "0.8.0")
    plans: list[dict] = []
    service = build_service(package_plans=plans)

    result = service.build(
        {
            "tool": "optimization.lac.apply-request",
            "projectPath": str(project),
            "sourceAvatarPath": "Avatar",
            "targetProfile": "pc_conservative",
        }
    )

    assert result["schema"] == "vrcforge.optimization.apply_request.v1"
    assert result["readyToRequest"] is True
    assert result["hardGate"]["status"] == "pass"
    assert result["dependencyInstallPlan"] is None
    assert result["applyArguments"]["componentType"] == (
        "dev.limitex.avatar.compressor.TextureCompressor"
    )
    assert plans == []


def test_missing_dependency_builds_read_only_install_plan_and_stays_blocked(
    tmp_path: Path,
) -> None:
    project = tmp_path / "UnityProject"
    make_unity_project(project)
    plans: list[dict] = []
    service = build_service(package_plans=plans)

    result = service.build(
        {
            "tool": "optimization.lac.apply-request",
            "projectPath": str(project),
            "avatarPath": "Avatar",
            "installMissingDependencies": True,
        }
    )

    assert result["readyToRequest"] is False
    assert result["hardGate"]["status"] == "blocked"
    assert len(plans) == 1
    assert plans[0]["packageId"] == "dev.limitex.avatar-compressor"
    assert plans[0]["allowAgentManagedDownload"] is True


def test_ttt_and_meshia_keep_exact_user_selection_gates(tmp_path: Path) -> None:
    project = tmp_path / "UnityProject"
    make_unity_project(project)
    install_package(project, "net.rs64.tex-trans-tool", "1.1.0-beta.8")
    install_package(project, "com.ramtype0.meshia.mesh-simplification", "3.2.0")
    service = build_service()

    ttt = service.build(
        {
            "tool": "optimization.ttt.atlas-apply-request",
            "projectPath": str(project),
            "avatarPath": "Avatar",
            "options": {
                "atlasTargetMaterials": ["Assets/Avatar/Materials/Body.mat"]
            },
        }
    )
    meshia = service.build(
        {
            "tool": "optimization.meshia.simplify-apply-request",
            "projectPath": str(project),
            "avatarPath": "Avatar",
            "options": {
                "rendererPath": "Avatar/HatAccessory",
                "relativeVertexCount": 0.9,
            },
        }
    )

    assert ttt["readyToRequest"] is True
    assert ttt["applyArguments"]["options"]["atlasTargetMaterials"] == [
        "Assets/Avatar/Materials/Body.mat"
    ]
    assert meshia["readyToRequest"] is True
    assert meshia["applyArguments"]["targetPath"] == "Avatar/HatAccessory"


def test_parameter_preview_uses_only_the_authoritative_preview_port(
    tmp_path: Path,
) -> None:
    project = tmp_path / "UnityProject"
    make_unity_project(project)
    install_package(project, "com.vrcfury.vrcfury", "1.1334.0")
    previews: list[dict] = []
    service = build_service(parameter_previews=previews)

    result = service.build(
        {
            "tool": "optimization.vrcfury.parameter-compressor-apply-request",
            "projectPath": str(project),
            "sourceScenePath": "Assets/Avatar.unity",
            "sourceAvatarPath": "Avatar",
            "outputCloneName": "Packed Clone",
        }
    )

    assert result["readyToRequest"] is True
    assert result["targetTool"] == "vrcforge_unity_mcp_write"
    assert result["applyArguments"]["toolName"] == (
        "vrc_build_parameter_bit_packed_clone"
    )
    assert previews == [
        {
            "projectPath": str(project.resolve()),
            "sourceScenePath": "Assets/Avatar.unity",
            "sourceAvatarPath": "Avatar",
            "outputCloneName": "Packed Clone",
        }
    ]


def test_authoritative_preview_failure_is_a_blocker_not_an_exception(
    tmp_path: Path,
) -> None:
    project = tmp_path / "UnityProject"
    make_unity_project(project)
    install_package(project, "com.vrcfury.vrcfury", "1.1334.0")
    service = build_service(preview_error="Authoritative preview is unavailable.")

    result = service.build(
        {
            "tool": "optimization.vrcfury.parameter-compressor-apply-request",
            "projectPath": str(project),
            "sourceScenePath": "Assets/Avatar.unity",
            "sourceAvatarPath": "Avatar",
            "outputCloneName": "Packed Clone",
        }
    )

    assert result["readyToRequest"] is False
    assert result["hardGate"]["status"] == "blocked"
    assert result["blockedReasons"] == ["Authoritative preview is unavailable."]


def test_unknown_apply_request_name_fails_closed() -> None:
    service = build_service()

    with pytest.raises(ValueError, match="Unknown optimization apply-request tool"):
        service.build({"tool": "optimization.unknown.apply-request"})
