"""Preserve selective catalogue validation through the real STDIO router."""
import importlib
import json
import pytest
from test_external_mcp_tool_selection import stdio_requester, LEAF

@pytest.mark.parametrize('transport', ['standard', '2026'])
@pytest.mark.parametrize('code', [-32602, -32603, -32001])
@pytest.mark.parametrize('http_status', [None, 400, 401, 500])
def test_upstream_selection_error_only_translates_invalid_params(monkeypatch, transport, code, http_status):
    request, bridge = stdio_requester(monkeypatch, transport, 'execution', [])
    module = importlib.import_module('tools.vrcforge_agent_mcp_stdio')
    before = request()['result']['tools']
    original = bridge.manifest
    message = 'io.vrcforge/toolNames names are not visible: missing_tool. Discover and load the matching leaf first.'
    def manifest(layer='planning', blocks=None, names=None):
        if names is not None:
            raw = {'jsonrpc':'2.0','id':1,'error':{'code':code,'message':message}}
            if http_status is not None:
                raise module.ExternalHttpBridgeError(status_code=http_status, path='/mcp', body=json.dumps(raw))
            raise module.ExternalMcpBridgeError(message, raw_result=raw)
        return original(layer, blocks, names)
    monkeypatch.setattr(bridge, 'manifest', manifest)
    result = request('tools/call', name='vrcforge_load_tool_block', arguments={'block':LEAF,'toolNames':['missing_tool']})
    if code == -32602 and http_status in (None, 400):
        value = result['result']['structuredContent']
        assert value['status'] == 'invalid_tool_selection'
        assert value['error'] == message
        assert value['errorCode'] == 'external_tool_selection_invalid'
        assert value['mutationStarted'] is False and value['committed'] is False
        assert 'activationHandle' not in value
    else:
        assert result['error']['code'] == -32603
        assert 'result' not in result
    assert request()['result']['tools'] == before
    assert bridge.calls == []
