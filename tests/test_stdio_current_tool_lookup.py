from __future__ import annotations
import importlib
import pytest
from agent_mcp_2026 import PROTOCOL_VERSION

@pytest.mark.parametrize('profile', ['mcp-1x', 'vrcforge-2026'])
def test_stdio_exact_lookup_is_fresh_and_revocation_blocks_routing(monkeypatch, profile):
    module = importlib.import_module('tools.vrcforge_agent_mcp_stdio')
    captured = {}
    class Bridge:
        allowed = True
        manifests = []
        calls = []
        def manifest(self, exposure, blocks=None, tool_names=None):
            self.manifests.append((exposure, tool_names))
            descriptor = {'name': 'vrcforge_bind_execution_target', 'description': 'Read target', 'inputSchema': {'type':'object'}, '_meta': {'toolBlock':'core', 'permission':'ReadOnly'}}
            return {'tools': [descriptor] if self.allowed else []}
        def call_tool(self, name, arguments, **kwargs):
            self.calls.append(name)
            return {'ok':True, 'mutationApplied':False}
    bridge=Bridge()
    loop='run_standard_stdio_loop' if profile=='mcp-1x' else 'run_stdio_loop'
    monkeypatch.setattr(module, loop, lambda router: captured.setdefault('router',router))
    module.run_stdio_server(bridge, protocol_profile=profile, exposure_layer='execution')
    router=captured['router']
    meta={'io.modelcontextprotocol/protocolVersion':PROTOCOL_VERSION, 'io.modelcontextprotocol/clientCapabilities':{}, 'io.modelcontextprotocol/clientInfo':{'name':'regression','version':'1'}}
    if profile=='mcp-1x':
        router.handle({'jsonrpc':'2.0','id':0,'method':'initialize','params':{'protocolVersion':'2025-11-25','capabilities':{},'clientInfo':{'name':'regression','version':'1'}}})
    def invoke(i):
        params={'name':'vrcforge_bind_execution_target','arguments':{}}
        if profile!='mcp-1x':params['_meta']=meta
        result=router.handle({'jsonrpc':'2.0','id':i,'method':'tools/call','params':params})
        return result[0] if isinstance(result,tuple) else result
    assert 'error' not in invoke(1)
    bridge.allowed=False
    assert 'error' in invoke(2)
    assert bridge.calls==['vrcforge_bind_execution_target']
    assert bridge.manifests==[('execution',['vrcforge_bind_execution_target'])]*2

@pytest.mark.parametrize('profile', ['mcp-1x', 'vrcforge-2026'])
def test_stdio_precise_lookup_retains_planning_exposure(monkeypatch, profile):
    module=importlib.import_module('tools.vrcforge_agent_mcp_stdio')
    captured={}; exposures=[]; calls=[]
    class Bridge:
        def manifest(self, exposure, blocks=None, tool_names=None):
            exposures.append(exposure)
            descriptor={'name':'vrcforge_create_gameobject','description':'write', 'inputSchema':{'type':'object'},'write':True,'_meta':{'toolBlock':'avatar_structure/hierarchy_components','permission':'Write'}}
            return {'tools':[descriptor] if exposure=='execution' else []}
        def call_tool(self,*args,**kwargs):calls.append(args);return {'ok':True}
    monkeypatch.setattr(module,'run_standard_stdio_loop' if profile=='mcp-1x' else 'run_stdio_loop',lambda router:captured.setdefault('router',router))
    module.run_stdio_server(Bridge(),protocol_profile=profile,exposure_layer='planning')
    router=captured['router']
    if profile=='mcp-1x':router.handle({'jsonrpc':'2.0','id':0,'method':'initialize','params':{'protocolVersion':'2025-11-25','capabilities':{},'clientInfo':{'name':'regression','version':'1'}}})
    params={'name':'vrcforge_create_gameobject','arguments':{},'exposureLayer':'execution'}
    if profile!='mcp-1x':params['_meta']={'io.modelcontextprotocol/protocolVersion':PROTOCOL_VERSION,'io.modelcontextprotocol/clientCapabilities':{},'io.modelcontextprotocol/clientInfo':{'name':'regression','version':'1'}}
    result=router.handle({'jsonrpc':'2.0','id':1,'method':'tools/call','params':params})
    response=result[0] if isinstance(result,tuple) else result
    assert 'error' in response
    assert exposures==['planning']
    assert not calls

def test_selected_manifest_preserves_internal_protocol_metadata(monkeypatch, tmp_path):
    module=importlib.import_module('tools.vrcforge_agent_mcp_stdio')
    bridge=module.VRCForgeBridge(base_url='http://127.0.0.1:8757',config_path=tmp_path/'unused.json',timeout_seconds=.1,start_runtime=False)
    monkeypatch.setattr(bridge,'require_token',lambda:'fixture-token')
    payloads=[]
    def request_json(*args,**kwargs):
        payloads.append(kwargs['payload'])
        return {'result':{'tools':[]}}
    monkeypatch.setattr(bridge,'request_json',request_json)
    bridge.manifest('execution',['*'],['vrcforge_get_asset_info'])
    meta=payloads[0]['params']['_meta']
    assert meta['io.vrcforge/toolNames']==['vrcforge_get_asset_info']
    assert meta['io.modelcontextprotocol/protocolVersion']==PROTOCOL_VERSION
    assert meta['io.modelcontextprotocol/clientCapabilities']=={}
    assert meta['io.modelcontextprotocol/clientInfo']['name']=='vrcforge-agent-stdio-bridge'
    assert meta['io.vrcforge/resultMode']=='full'
