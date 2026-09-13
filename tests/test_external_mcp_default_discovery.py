"""Default discovery may shrink; existing callable catalogues must not shrink."""
from copy import deepcopy
import pytest
from test_external_mcp_tool_selection import archived_tools,stdio_requester,LEAF,NAMES
from agent_mcp_2026 import PROTOCOL_VERSION
SEED={'vrcforge_bridge_preflight','vrcforge_list_tool_blocks','vrcforge_load_tool_block','vrcforge_unload_tool_block','vrcforge_invoke_loaded_read_tool','vrcforge_invoke_loaded_write_tool','vrcforge_list_execution_targets','vrcforge_bind_execution_target','vrcforge_list_prompts','vrcforge_get_prompt'}

def full_meta():return {'io.modelcontextprotocol/protocolVersion':PROTOCOL_VERSION,'io.modelcontextprotocol/clientCapabilities':{},'io.vrcforge/resultMode':'full'}

@pytest.mark.parametrize('transport',['2026','standard'])
def test_default_seed_and_hidden_legacy_direct_calls_remain_available(monkeypatch,transport,archived_tools):
    request,bridge=stdio_requester(monkeypatch,transport,'execution',archived_tools)
    seed=request()['result']['tools'];full=request(_meta=full_meta())['result']['tools']
    assert {t['name'] for t in seed}==SEED
    assert len(full)==20
    hidden=[t for t in full if t['name'] not in SEED]
    assert len(hidden)==10
    for tool in hidden:
        name=tool['name'];leaf=tool['_meta']['toolBlock']
        index=request('tools/call',name='vrcforge_list_tool_blocks',arguments={'block':leaf})['result']['structuredContent']
        assert name in {row['name'] for row in index['tree']['tools']}
        request('tools/call',name=name,arguments={})
        assert bridge.calls[-1]==(name,{})
        # Existing explicit-name selection must still find formerly-default descriptors.
        selected=request(selection=[name])['result']['tools'][0]
        assert selected['inputSchema']==tool['inputSchema'] and selected['outputSchema']==tool['outputSchema']

@pytest.mark.parametrize('transport',['2026','standard'])
def test_prompt_startup_controls_delegate_native_paths_with_bounded_arguments(monkeypatch,transport):
    request,bridge=stdio_requester(monkeypatch,transport,'planning',[])
    listed=request()['result']['tools']
    assert {'vrcforge_list_prompts','vrcforge_get_prompt'} <= {tool['name'] for tool in listed}

    # The fixture bridge records calls to the existing native prompt paths.
    bridge.prompt_rows = {'prompts': [{'name': 'fixture.vsk'}], 'nextCursor': 'next'}
    bridge.prompt_calls = []
    bridge.prompt_get = {
        'messages': [{'role': 'user', 'content': {'type': 'text', 'text': 'fixture'}}],
        'structuredContent': {'provenance': {'skillId': 'fixture', 'version': '1.2.3', 'contentHash': 'a' * 64, 'supportContentHash': 'b' * 64}},
        '_meta': {'schema': 'vrcforge.prompt_skill_provenance.v1', 'skillId': 'fixture', 'version': '1.2.3', 'contentHash': 'a' * 64, 'supportContentHash': 'b' * 64},
    }
    bridge.prompts = lambda **kwargs: bridge.prompt_calls.append(('list', kwargs)) or bridge.prompt_rows | {'called': kwargs}
    bridge.get_prompt = lambda name, arguments: bridge.prompt_calls.append(('get', name, arguments)) or bridge.prompt_get | {'called': (name, arguments)}

    listed_result=request('tools/call',name='vrcforge_list_prompts',arguments={'cursor':'c','pageSize':2})['result']['structuredContent']
    assert listed_result['called'] == {'cursor':'c','page_size':2}
    got_result=request('tools/call',name='vrcforge_get_prompt',arguments={'name':'fixture.vsk','arguments':{'projectPath':'x'}})['result']['structuredContent']
    assert got_result['called'] == ['fixture.vsk', {'projectPath':'x'}]
    assert got_result['promptSkillProvenance']['status'] == 'available'
    assert got_result['promptSkillProvenance']['contentHash'] == 'a' * 64
    assert got_result['promptSkillProvenance']['supportContentHash'] == 'b' * 64

    call_count = len(bridge.prompt_calls)
    assert request('tools/call',name='vrcforge_list_prompts',arguments={'pageSize':0})['error']['code']==-32603
    assert request('tools/call',name='vrcforge_get_prompt',arguments={'name':'','arguments':{}})['error']['code']==-32603
    assert len(bridge.prompt_calls) == call_count

@pytest.mark.parametrize('transport',['2026','standard'])
@pytest.mark.parametrize('layer',['planning','execution'])
def test_exact_load_auto_relist_and_no_relist_handle_paths(monkeypatch,transport,layer,archived_tools):
    request,bridge=stdio_requester(monkeypatch,transport,layer,archived_tools)
    expected_seed=SEED-({'vrcforge_invoke_loaded_write_tool'} if layer=='planning' else set())
    assert {t['name'] for t in request()['result']['tools']}==expected_seed
    loaded=request('tools/call',name='vrcforge_load_tool_block',arguments={'block':LEAF,'toolNames':[NAMES[0]]})['result']['structuredContent']
    selected=loaded['selectedTools']
    assert [t['name'] for t in selected]==[NAMES[0]]
    handle=loaded['activationHandle']
    # This host never re-lists: it has the complete selected schema in the load receipt.
    request('tools/call',name='vrcforge_invoke_loaded_read_tool',arguments={'activationHandle':handle,'toolName':NAMES[0],'arguments':{}})
    assert bridge.calls==[(NAMES[0],{})]
    # Auto-refreshing hosts see precisely the seed plus selected Tool.
    listed=request()['result']['tools']
    assert {t['name'] for t in listed}==expected_seed|{NAMES[0]}
    assert next(t for t in listed if t['name']==NAMES[0])==selected[0]
    # Selection is not a narrower permission grant: original loaded-leaf calls still work.
    request('tools/call',name=NAMES[1],arguments={})
    assert bridge.calls[-1]==(NAMES[1],{})
    request('tools/call',name='vrcforge_load_tool_block',arguments={'block':LEAF,'toolNames':[NAMES[1]]})
    assert {t['name'] for t in request()['result']['tools']}==expected_seed|set(NAMES)
    # Omitting toolNames preserves ordinary whole-leaf loading.
    request('tools/call',name='vrcforge_load_tool_block',arguments={'block':LEAF})
    listed=request()['result']['tools']
    assert len(listed)>len(expected_seed)+2
    request('tools/call',name='vrcforge_unload_tool_block',arguments={'block':LEAF})
    assert request('tools/call',name=NAMES[0],arguments={})['error']['code']==-32602
    before=len(bridge.calls)
    request('tools/call',name='vrcforge_invoke_loaded_read_tool',arguments={'activationHandle':handle,'toolName':NAMES[0],'arguments':{}})
    assert len(bridge.calls)==before

@pytest.mark.parametrize('transport',['2026','standard'])
@pytest.mark.parametrize('names',[[],['missing'],['vrcforge_health'],['vrcforge_write_animation_curve'],NAMES*2,'not-array'])
def test_invalid_exact_load_never_changes_visibility_or_activation(monkeypatch,transport,names,archived_tools):
    request,bridge=stdio_requester(monkeypatch,transport,'planning',archived_tools)
    before=request()['result']['tools']
    response=request('tools/call',name='vrcforge_load_tool_block',arguments={'block':LEAF,'toolNames':names})['result']['structuredContent']
    assert response['ok'] is False
    assert response['status']=='invalid_tool_selection'
    assert 'activationHandle' not in response
    assert request()['result']['tools']==before
    assert bridge.calls==[]
