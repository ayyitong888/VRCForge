import pytest
import dashboard_server as dashboard


@pytest.mark.parametrize("framework", ["modular_avatar", "vrcfury"])
@pytest.mark.parametrize("truncated", [True, False, None])
def test_addon_scan_preserves_actual_core_completeness(monkeypatch, framework, truncated):
    payload={"items":[], "summary":{}}
    if truncated is not None:
        payload["summary"]["truncated"]=truncated
    monkeypatch.setattr(dashboard.PACKAGE_DETECTION,"detect",lambda *_:{"installed":False})
    monkeypatch.setattr(dashboard,"load_dashboard_settings",lambda *_:object())
    monkeypatch.setattr(dashboard,"invoke_unity_mcp",lambda *_:payload)
    monkeypatch.setattr(dashboard,"extract_tool_result_payload",lambda x:x)
    monkeypatch.setattr(dashboard,"emit_log",lambda *_:None)
    result=dashboard.scan_addon_framework_sync(framework,{})
    state=result["unity"]
    assert state["truncated"] is truncated
    assert state["complete"] is (truncated is False)
    assert state["missingMatchProvesAbsence"] is (truncated is False)
    if truncated is not False:
        assert "incomplete" in result["summary"]


@pytest.mark.parametrize("payload", [{"ok":False,"error":"Core unavailable"}, {"items":None}, {}])
def test_addon_invalid_scan_is_not_successful_empty_scan(monkeypatch,payload):
    monkeypatch.setattr(dashboard.PACKAGE_DETECTION,"detect",lambda *_:{"installed":False})
    monkeypatch.setattr(dashboard,"load_dashboard_settings",lambda *_:object())
    monkeypatch.setattr(dashboard,"invoke_unity_mcp",lambda *_:payload)
    monkeypatch.setattr(dashboard,"extract_tool_result_payload",lambda x:x)
    monkeypatch.setattr(dashboard,"emit_log",lambda *_:None)
    result=dashboard.scan_addon_framework_sync("modular_avatar",{})
    assert result["unity"]["scanned"] is False
    assert result["unity"]["complete"] is False
    assert result["unity"]["missingMatchProvesAbsence"] is False
