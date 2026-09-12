"""Exact-name discovery is presentation only; real routers and STDIO retain authority."""
from copy import deepcopy
import importlib
import json
import os
from pathlib import Path
import pytest
from agent_mcp_2026 import Mcp2026Router, PROTOCOL_VERSION
from agent_mcp_standard import McpStandardRouter, LATEST_PROTOCOL_VERSION
KEY='io.vrcforge/toolNames'
LEAF='behavior/animator_clips_bindings'
ROOT=Path(__file__).resolve().parents[1]
ARCHIVE_ROOT=Path(os.environ['VRCFORGE_REGRESSION_ARCHIVE_ROOT']) if os.environ.get('VRCFORGE_REGRESSION_ARCHIVE_ROOT') else ROOT/'.tmp'/'regression-archives'
NAMES=['vrcforge_scan_animation_bindings','vrcforge_scan_fx_animator']

def requester(router, transport):
    if transport=='standard':
        router.handle({'jsonrpc':'2.0','id':0,'method':'initialize','params':{'protocolVersion':LATEST_PROTOCOL_VERSION,'capabilities':{},'clientInfo':{'name':'test','version':'1'}}})
    def request(method='tools/list', selection=None, **params):
        meta={'io.modelcontextprotocol/protocolVersion':PROTOCOL_VERSION,'io.modelcontextprotocol/clientCapabilities':{}} if transport=='2026' else {}
        if selection is not None: meta[KEY]=selection
        response=router.handle({'jsonrpc':'2.0','id':1,'method':method,'params':{'_meta':meta,**params}})
        return response[0] if transport=='2026' else response
    return request

@pytest.fixture
def archived_tools():
    path=ARCHIVE_ROOT/'569.json'
    if not path.exists(): pytest.skip('Local public archive not distributed')
    return next(row['tools'] for row in json.loads(path.read_text('utf-8-sig'))['results'] if 'tools' in row)

@pytest.mark.parametrize('transport',['2026','standard'])
def test_569_exact_selection_preserves_whole_descriptors_and_call_catalog(transport,archived_tools):
    calls=[]
    cls=Mcp2026Router if transport=='2026' else McpStandardRouter
    router=cls(lambda *_:deepcopy(archived_tools),lambda name,args:calls.append((name,args)) or {'ok':True})
    request=requester(router,transport)
    before=request()['result']
    selected=request(selection=NAMES)['result']
    assert selected['tools']==[tool for tool in before['tools'] if tool['name'] in NAMES]
    assert len(selected['tools'])==2
    assert selected['_meta']['io.vrcforge/toolSelection']['requestMetaKey']==KEY
    assert request()['result']['tools']==before['tools']
    # Selection neither hides a previously callable tool nor forwards metadata to arguments.
    request('tools/call',name='vrcforge_health',arguments={})
    assert calls==[('vrcforge_health',{})]

@pytest.mark.parametrize('transport',['2026','standard'])
@pytest.mark.parametrize('names',[[], 'fixture', [''], [' fixture'], [1], ['fixture','fixture'], ['fixture']*33, ['missing']])
def test_invalid_or_unavailable_selection_is_explicit_and_never_dispatches(transport,names):
    calls=[]
    cls=Mcp2026Router if transport=='2026' else McpStandardRouter
    router=cls(lambda *_:[{'name':'fixture','inputSchema':{'type':'object'}}],lambda *args:calls.append(args))
    response=requester(router,transport)(selection=names)
    assert response['error']['code']==-32602
    assert KEY in response['error']['message']
    assert calls==[]

@pytest.mark.parametrize('transport',['2026','standard'])
def test_selection_supports_full_descriptors_and_rejects_use_on_calls(transport):
    calls=[]
    descriptor={'name':'fixture','inputSchema':{'type':'object','required':['exactIdentity']},
                '_meta':{'requiredIdentity':['exactIdentity'],'approval':{'required':True}}}
    cls=Mcp2026Router if transport=='2026' else McpStandardRouter
    router=cls(lambda *_:[descriptor],lambda *args:calls.append(args))
    request=requester(router,transport)
    meta={'io.modelcontextprotocol/protocolVersion':PROTOCOL_VERSION,'io.modelcontextprotocol/clientCapabilities':{},'io.vrcforge/resultMode':'full'}
    full=request(_meta=meta)['result']['tools']
    selected=request(_meta={**meta,KEY:['fixture']})['result']['tools']
    assert selected==full
    assert selected[0]['inputSchema']==descriptor['inputSchema']
    assert selected[0]['_meta']['approval']==descriptor['_meta']['approval']
    assert request('tools/call',selection=['fixture'],name='fixture',arguments={})['error']['code']==-32602
    assert calls==[]

@pytest.mark.parametrize('transport',['2026','standard'])
def test_tools_call_uses_live_exact_catalogue_without_full_catalogue(transport):
    calls=[]; catalogue=[]; full_calls=[]
    descriptor={'name':'fixture','inputSchema':{'type':'object'},'_meta':{'toolBlock':'core'},'description':'fixture'}
    cls=Mcp2026Router if transport=='2026' else McpStandardRouter
    if transport=='standard':
        exact=lambda name,args: catalogue.append((name,args)) or [descriptor]
        router=cls(lambda: [descriptor],lambda name,args:calls.append((name,args)) or {'ok':True},tool_call_catalogue=lambda: full_calls.append(True) or [descriptor],tool_call_catalogue_for_call=exact)
    else:
        exact=lambda name,args: catalogue.append((name,args)) or [descriptor]
        router=cls(lambda params: [descriptor],lambda name,args:calls.append((name,args)) or {'ok':True},tool_call_catalogue=lambda params: exact(params['name'], params.get('arguments', {})))
    request=requester(router,transport)
    response=request('tools/call',name='fixture',arguments={})
    assert response['result']['structuredContent']['ok'] is True
    assert catalogue==[('fixture',{})]
    assert full_calls==[]
    assert calls==[('fixture',{})]

def stdio_requester(monkeypatch,transport,layer,archived_tools):
    module=importlib.import_module('tools.vrcforge_agent_mcp_stdio')
    controls={'vrcforge_bridge_preflight','vrcforge_list_tool_blocks','vrcforge_load_tool_block','vrcforge_unload_tool_block','vrcforge_invoke_loaded_read_tool','vrcforge_invoke_loaded_write_tool'}
    source=[]
    for original in archived_tools:
        if original['name'] in controls: continue
        item=deepcopy(original)
        if item['_meta']['toolBlock']!=LEAF: item['_meta']['toolBlock']='core'
        source.append(item)
    class Bridge:
        calls=[]
        def preflight(self):return {'runtimeOnline':True}
        def manifest(self,exposure_layer='planning',tool_blocks=None,tool_names=None):
            return {'tools':[deepcopy(item) for item in source if exposure_layer=='execution' or str(item.get('_meta',{}).get('permission','')).casefold()!='write']}
        def call_tool(self,name,arguments,**kwargs):
            self.calls.append((name,arguments));return {'ok':True}
    captured={}
    monkeypatch.setattr(module,'run_stdio_loop',lambda router:captured.setdefault('router',router))
    monkeypatch.setattr(module,'run_standard_stdio_loop',lambda router:captured.setdefault('router',router))
    bridge=Bridge()
    module.run_stdio_server(bridge,protocol_profile='vrcforge-2026' if transport=='2026' else 'mcp-1x',exposure_layer=layer)
    return requester(captured['router'],transport),bridge

@pytest.mark.parametrize('transport',['2026','standard'])
@pytest.mark.parametrize('layer',['planning','execution'])
def test_stdio_569_selection_keeps_activation_and_mode_boundaries(monkeypatch,transport,layer,archived_tools):
    request,bridge=stdio_requester(monkeypatch,transport,layer,archived_tools)
    assert request(selection=NAMES)['error']['code']==-32602
    inventory=request('tools/call',name='vrcforge_list_tool_blocks',arguments={'block':LEAF})['result']['structuredContent']
    assert set(NAMES).issubset({row['name'] for row in inventory['tree']['tools']})
    assert KEY in inventory['selectionHint']
    loaded=request('tools/call',name='vrcforge_load_tool_block',arguments={'block':LEAF})['result']['structuredContent']
    full=request()['result']['tools']
    selected=request(selection=NAMES)['result']['tools']
    assert selected==[tool for tool in full if tool['name'] in NAMES]
    assert len(selected)==2
    handle=loaded['activationHandle']
    request('tools/call',name='vrcforge_invoke_loaded_read_tool',arguments={'activationHandle':handle,'toolName':NAMES[0],'arguments':{}})
    assert bridge.calls==[(NAMES[0],{})]
    if layer=='planning':
        assert request(selection=['vrcforge_write_animation_curve'])['error']['code']==-32602
    request('tools/call',name='vrcforge_unload_tool_block',arguments={'block':LEAF})
    assert request(selection=NAMES)['error']['code']==-32602
    request('tools/call',name='vrcforge_invoke_loaded_read_tool',arguments={'activationHandle':handle,'toolName':NAMES[0],'arguments':{}})
    assert bridge.calls==[(NAMES[0],{})]
