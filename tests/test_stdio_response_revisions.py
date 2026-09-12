from __future__ import annotations
from collections import Counter
from concurrent.futures import ThreadPoolExecutor
import pytest
from agent_mcp_2026 import Mcp2026Router, PROTOCOL_VERSION
from agent_mcp_standard import McpStandardRouter
from tools.vrcforge_agent_mcp_stdio import VRCForgeBridge


def test_authenticated_tool_response_revisions_preserve_notifications_without_repeat_lists(monkeypatch, tmp_path):
    state={'resources':0,'prompts':'p0'}
    descriptor={'name':'fixture_read','inputSchema':{'type':'object'}}
    methods=[]
    def perform(name,args):
        state['resources']+=1;state['prompts']='p'+str(state['resources'])
        return {'ok':True,'mutationApplied':False}
    server=Mcp2026Router(lambda p:[descriptor],perform,
        resource_list=lambda p:{'resources':[],'resourceGeneration':state['resources']},
        prompt_list=lambda p:{'prompts':[],'promptGeneration':state['prompts']},
        resource_list_revision=lambda:state['resources'],prompt_list_revision=lambda:state['prompts'])
    bridge=VRCForgeBridge(base_url='http://127.0.0.1:8757',config_path=tmp_path/'unused.json',timeout_seconds=.1,start_runtime=False)
    monkeypatch.setattr(bridge,'require_token',lambda:'fixture-token')
    def request_json(*args,**kwargs):
        assert kwargs['token']=='fixture-token'
        methods.append(kwargs['payload']['method'])
        # Test-owned worker runs the fake HTTP server loop; no network/files, joined here.
        with ThreadPoolExecutor(max_workers=1) as worker:
            return worker.submit(server.handle, kwargs['payload']).result()[0]
    monkeypatch.setattr(bridge,'request_json',request_json)
    outer=McpStandardRouter(lambda:[descriptor],bridge.call_tool,
        resource_list_revision=bridge.resource_generation,prompt_list_revision=bridge.prompt_generation)
    outer.handle({'jsonrpc':'2.0','id':0,'method':'initialize','params':{'protocolVersion':'2025-11-25','capabilities':{},'clientInfo':{'name':'fixture','version':'1'}}})
    for i in (1,2):
        if i==2:state.update(resources=9,prompts='external-change')
        result=outer.handle({'jsonrpc':'2.0','id':i,'method':'tools/call','params':{'name':'fixture_read','arguments':{}}})
        assert 'error' not in result
        assert {x['method'] for x in outer.drain_notifications()}=={'notifications/resources/list_changed','notifications/prompts/list_changed'}
        assert bridge.resource_generation()==state['resources']
        assert bridge.prompt_generation()==state['prompts']
    assert Counter(methods)=={'resources/list':1,'prompts/list':1,'tools/call':2}


@pytest.mark.parametrize('metadata',[None, {'resources':True,'prompts':'p'}, {'resources':-1,'prompts':'p'}, {'resources':1,'prompts':None}])
def test_absent_or_invalid_response_revisions_do_not_reuse_stale_values(monkeypatch,tmp_path,metadata):
    bridge=VRCForgeBridge(base_url='http://127.0.0.1:8757',config_path=tmp_path/'unused.json',timeout_seconds=.1,start_runtime=False)
    bridge._response_list_revisions={'resources':1,'prompts':'old'}
    monkeypatch.setattr(bridge,'require_token',lambda:'fixture-token')
    def response(*args,**kwargs):
        return {'result':{'structuredContent':{'ok':True},'_meta':{'io.vrcforge/listRevisions':metadata}}}
    monkeypatch.setattr(bridge,'request_json',response)
    bridge.call_tool('fixture_read',{})
    polls=[]
    monkeypatch.setattr(bridge,'resources',lambda **kw:polls.append('resources') or {'resourceGeneration':2})
    monkeypatch.setattr(bridge,'prompts',lambda **kw:polls.append('prompts') or {'promptGeneration':'new'})
    assert bridge.resource_generation()==2 and bridge.prompt_generation()=='new'
    assert polls==['resources','prompts']
