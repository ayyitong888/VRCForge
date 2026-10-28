"""Status observations do not resubmit or reclassify an already accepted import."""
import pytest
import vrchat_blendshape_agent as agent
from test_approved_unity_execution import _descriptor, _settings
from test_prepared_outfit_import_writes import _approved
from types import SimpleNamespace


@pytest.mark.parametrize("lane,tool", [
    ("app_unitypackage_import_poll", "vrc_import_unitypackage"),
    ("app_setup_outfit_poll", "vrc_setup_outfit"),
    ("app_build_test_poll", "vrc_build_test_avatar"),
])
def test_exact_readonly_job_poll_does_not_depend_on_native_window_probe(tmp_path, monkeypatch, lane, tool):
    _descriptor(tmp_path)
    calls = []
    class Core:
        def __init__(self, *_args, **_kwargs): pass
        def call_tool(self, name, params, **kwargs):
            calls.append((name, params, kwargs))
            return {"isError": False, "structuredContent": {"success": True, "data": {"jobId": "a"*32, "status": "completed"}}}
    monkeypatch.setattr(agent, "UnityMcpCoreClient", Core)
    monkeypatch.setattr(agent, "probe_unity_reload_dialog", lambda _: pytest.fail("A cached read-only job poll must not depend on UI Automation"))
    result = agent.invoke_unity_mcp(_settings(tmp_path), tool, {"jobId": "a"*32}, execution_context={"lane": lane})
    assert result.exit_code == 0
    assert calls == [(tool, {"jobId": "a"*32}, {"execution_context": {"lane": lane}})]


def test_poll_lane_cannot_hide_mutating_arguments_from_probe(tmp_path, monkeypatch):
    _descriptor(tmp_path)
    monkeypatch.setattr(agent,"UnityMcpCoreClient",lambda *_a, **_kw: pytest.fail("invalid poll must not dispatch"))
    monkeypatch.setattr(agent,"probe_unity_reload_dialog",lambda _: {"probeError":{"code":"unity_editor_window_probe_failed"}})
    with pytest.raises(agent.UnityMcpError,match="inspect"):
        agent.invoke_unity_mcp(_settings(tmp_path),"vrc_import_unitypackage",{"jobId":"a"*32,"unityPackagePath":"malformed"},execution_context={"lane":"app_unitypackage_import_poll"})


def test_poll_observation_failure_explains_existing_submission_and_exact_next_read(monkeypatch):
    service = _approved(poll_seconds=0.0)
    def lost(_settings, _job):
        raise agent.UnityMcpError("No Core tool was called.", cause_code="unity_editor_window_probe_failed", core_tool="vrc_import_unitypackage")
    from dataclasses import replace
    service._ports = replace(service._ports, poll_import=lost)
    result = service._wait_for_job(SimpleNamespace(unity_mcp_timeout_seconds=30),
        {"ok":True,"pending":True,"status":"pending","jobId":"a"*32,"mutationStarted":True})
    assert result["commitState"] == "unknown"
    assert "already submitted" in result["error"]
    assert "vrcforge_get_unitypackage_import_status" in result["nextAction"]
    assert "a"*32 in result["nextAction"]
    assert result["pending"] is True
    assert result["safeToRetry"] is False
    from agent_tool_result_contract import normalize_agent_tool_result
    from agent_gateway import AgentGateway
    internal = normalize_agent_tool_result(result, fallback_summary="Import observation failed", write=True)
    external = AgentGateway._external_mcp_write_result(AgentGateway.__new__(AgentGateway),
        "vrcforge_import_outfit_package", {"ok":False,"result":result})["outcome"]
    for outcome in (internal, external):
        assert "already submitted" in outcome["summary"]
        assert outcome["error"]["code"] == "unity_editor_window_probe_failed"
        assert outcome["nextAction"] == result["nextAction"]
        assert outcome["commitState"] == "unknown"
