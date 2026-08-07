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
    assert "AGENT_GATEWAY.build_skill_registry(exposure_layer=exposure_layer)" in source
