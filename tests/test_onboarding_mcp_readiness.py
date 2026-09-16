from __future__ import annotations

import json
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


def test_first_run_diagnosis_is_discoverable_and_callable_without_unity(tmp_path: Path) -> None:
    from agent_gateway import AgentGateway

    gateway = AgentGateway(tmp_path / "config.json", tmp_path / "audit")
    config = gateway.ensure_config()
    config.enabled = True
    config.allow_write_requests = False
    gateway.save_config(config)
    observed = {"projectDiscovery": {"state": "empty", "projectCount": 0}}
    gateway.register_tool(
        "vrcforge_know_yourself",
        "When to use: diagnose project startup. When NOT to use: unrelated questions.",
        "read/debug",
        lambda _params: observed,
    )
    tools = gateway.build_external_mcp_tools("planning", ["core"])
    assert "vrcforge_know_yourself" in {tool["name"] for tool in tools}
    result = gateway.call_external_mcp_tool("vrcforge_know_yourself", {})
    assert result["result"]["projectDiscovery"] == observed["projectDiscovery"]


def test_onboarding_requires_exact_selected_project_and_all_64_tools() -> None:
    app_source = (ROOT / "src" / "App.tsx").read_text(encoding="utf-8")
    overlay_source = (
        ROOT / "src" / "components" / "onboarding" / "onboarding-overlay.tsx"
    ).read_text(encoding="utf-8")

    assert "vrcForgeToolsCount === 68" in app_source
    assert "normalizeProjectPathKey(projectKey(project)) === normalizeProjectPathKey(activeProjectPath)" in app_source
    assert "onboardingProjectMatchesBackend" in app_source
    assert "onboardingSelectedProjectReady && onboardingProjectMatchesBackend && vrcForgeToolsReady" in app_source
    assert "projectItems.length" not in app_source[app_source.index("const onboardingSelectedProjectReady") : app_source.index("const onboardingUnityToolsReady")]
    assert 't("onboarding.importAndSelectProject")' in overlay_source
    assert 't("onboarding.keepUnityOpen", { count: unityToolsCount, total: 68 })' in overlay_source
    assert 't("onboarding.retryConnection")' in overlay_source
    assert 't("onboarding.toolsConnected", { count: unityToolsCount, total: 68 })' in overlay_source


def test_all_onboarding_locales_include_inline_import_connection_guidance() -> None:
    for locale_path in sorted((ROOT / "src" / "locales").glob("*.json")):
        locale = json.loads(locale_path.read_text(encoding="utf-8"))
        onboarding = locale["onboarding"]
        for key in (
            "selectProject",
            "importAndSelectProject",
            "keepUnityOpen",
            "retryConnection",
            "toolsConnected",
        ):
            assert str(onboarding.get(key) or "").strip(), f"{locale_path.name}: {key}"
        assert "{{count}}" in onboarding["keepUnityOpen"]
        assert "{{total}}" in onboarding["keepUnityOpen"]
        assert "{{count}}" in onboarding["toolsConnected"]
        assert "{{total}}" in onboarding["toolsConnected"]
