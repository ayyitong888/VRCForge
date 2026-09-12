import copy
import pytest
import runtime_observation as domain
from test_runtime_observation import args, pending_receipt, complete_receipt


def probes():
    return [{"rendererPath": "Root/Avatar/Glasses", "materialIndex": 0, "propertyNames": ["_DissolveParams", "_DissolveMask_ST"]}]


def test_probe_request_forwarding_and_legacy(tmp_path, monkeypatch):
    import dashboard_server as ds
    requested = {**args(), "rendererProbes": probes()}
    prepared, _ = ds.prepare_runtime_observation_request(requested, None)
    assert prepared["rendererProbes"] == probes()
    assert "rendererProbes" not in domain.prepare_request(args(), tmp_path)
    handler = ds.AGENT_GATEWAY._write_handlers["vrcforge_start_runtime_observation"]
    assert handler.approved_execution_plan_builder(prepared)[0][1]["rendererProbes"] == probes()
    captured = []
    monkeypatch.setattr(ds, "load_dashboard_settings", lambda _: {})
    monkeypatch.setattr(ds, "extract_tool_result_payload", lambda value: value)
    monkeypatch.setattr(ds, "invoke_unity_mcp", lambda settings, name, request, **kw: captured.append(request) or {**pending_receipt(request), "rendererProbes": probes()})
    ds.runtime_observation_start_sync(prepared)
    assert captured[0]["rendererProbes"] == probes()


def test_public_probe_schema():
    import dashboard_server as ds
    rows = ds.AGENT_GATEWAY.build_external_mcp_tools("execution", ["*"])
    schema = next(row for row in rows if row["name"] == "vrcforge_start_runtime_observation")["inputSchema"]
    probe = schema["properties"]["rendererProbes"]
    assert probe["maxItems"] == 8
    assert probe["items"]["properties"]["propertyNames"]["maxItems"] == 8
    assert "rendererProbes" not in schema["required"]


@pytest.mark.parametrize("bad", [None, {}, probes()*9,
    [{**probes()[0], "rendererPath": "Other/Glasses"}],
    [{**probes()[0], "rendererPath": "Root/Avatar/../Glasses"}],
    [{**probes()[0], "materialIndex": True}],
    [{**probes()[0], "rendererComponentIndex": -1}],
    [{**probes()[0], "propertyNames": ["_A"]*9}],
    [{**probes()[0], "propertyNames": ["bad.name"]}],
    [{**probes()[0], "extra": True}]])
def test_invalid_probe_rejected_before_job(tmp_path, bad):
    with pytest.raises(ValueError, match="rendererProbes"):
        domain.prepare_request({**args(), "rendererProbes": bad}, tmp_path)


def test_probe_acknowledgement_bound_to_request(tmp_path):
    request = domain.prepare_request({**args(), "rendererProbes": probes()}, tmp_path)
    receipt = {**pending_receipt(request), "rendererProbes": probes()}
    assert domain.validate_start_result(receipt, request) is receipt
    with pytest.raises(ValueError, match="probe"):
        domain.validate_start_result({**receipt, "rendererProbes": []}, request)


def test_complete_probe_frame_coverage_required(tmp_path):
    request, receipt = complete_receipt(tmp_path)
    request["rendererProbes"] = receipt["rendererProbes"] = probes()
    with pytest.raises(ValueError, match="probe"):
        domain.validate_result(receipt, request)


def test_public_get_and_png_resource_preserve_probes(tmp_path, monkeypatch):
    import dashboard_server as ds
    from mcp_resource_registry import McpResourceRegistry
    from contextlib import nullcontext
    request, receipt = complete_receipt(tmp_path / "latest")
    receipt.update(width=128, height=128, coreIdentity="fixed-core")
    receipt["rendererProbes"] = request["rendererProbes"] = probes()
    row = {**probes()[0], "rendererComponentIndex": 0, "rendererInstanceId": 42,
           "properties": [{"name": name, "type": "Vector", "value": [1, 2, 3, 4], "valueSource": "material_property_block"} for name in probes()[0]["propertyNames"]]}
    for frame in receipt["frames"]:
        frame["rendererProbes"] = [copy.deepcopy(row)]
    monkeypatch.setattr(ds, "DASHBOARD_ARTIFACTS_DIR", tmp_path)
    monkeypatch.setattr(ds, "load_dashboard_settings", lambda _: {})
    monkeypatch.setattr(ds, "extract_tool_result_payload", lambda value: value)
    monkeypatch.setattr(ds, "bound_editor_readback", lambda _: nullcontext())
    monkeypatch.setattr(ds, "invoke_unity_mcp", lambda *a, **kw: copy.deepcopy(receipt))
    registry = McpResourceRegistry(tmp_path / "registry")
    monkeypatch.setattr(ds.AGENT_GATEWAY, "_mcp_resources", registry)
    result = ds.runtime_observation_status_raw({"jobId": request["jobId"], "avatarPath": request["avatarPath"]})
    assert result["ok"] is True
    assert result["rendererProbes"] == probes()
    assert result["frames"][0]["rendererProbes"] == [row]
    content = registry.read(result["frames"][0]["resourceUri"])["contents"][0]
    assert content["_meta"]["rendererProbes"] == [row]
    from runtime_state_resources import project_receipt
    projected = project_receipt(registry, result, {}, state_detail="resource")
    assert projected["frames"][0]["rendererProbes"] == [row]
