from pathlib import Path


ROOT = Path(__file__).parents[1]


def test_packaged_skill_probe_observes_projected_write_skills_in_execution_layer() -> None:
    source = (
        ROOT / "scripts" / "diagnose_packaged_skill_ecosystem.mjs"
    ).read_text(encoding="utf-8")

    assert 'agentApi("/api/agent/skills")' not in source
    assert 'agentApi("/api/agent/skills",' not in source
    assert source.count(
        'agentApi("/api/agent/skills?exposure_layer=execution"'
    ) == 8


def test_agent_skill_route_remains_default_planning_for_product_callers() -> None:
    source = (ROOT / "dashboard_server.py").read_text(encoding="utf-8")

    assert (
        'exposure_layer: Literal["planning", "execution"] = "planning"'
        in source
    )
    assert "AGENT_GATEWAY.skills.build_skill_registry(exposure_layer=exposure_layer)" in source


def test_packaged_skill_probe_keeps_approval_packages_request_only() -> None:
    source = (
        ROOT / "scripts" / "diagnose_packaged_skill_ecosystem.mjs"
    ).read_text(encoding="utf-8")

    entrypoint_block = source.split(
        "const requiredPackageEntrypoints = new Map([", 1
    )[1].split("]);", 1)[0]
    request_only_block = source.split(
        "const requestOnlyPackageSlugs = new Set([", 1
    )[1].split("]);", 1)[0]
    assert '"outfit-naming-helper"' not in entrypoint_block
    assert '"material-preset-pack"' in request_only_block
    assert '"outfit-naming-helper"' in request_only_block
    assert '"vrcforge_unity_mcp_write"' in source
    assert '"vrc_atomic_reference_rename"' in source
    assert '"post-safe-mode request-only outfit package"' in source
