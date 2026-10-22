"""Sorting keeps exact arguments and object identity through the real boundaries."""
import ast
from pathlib import Path
from types import SimpleNamespace
import pytest
from jsonschema import Draft202012Validator
from mcp_tool_descriptor import identity_scope, standardize_tool_descriptor
from unity_tool_schema_projection import canonical_unity_write_tool_input_schema
from unity_execution_plans_scene import build_scene_execution_plan, sibling_index_arguments
from unity_write_input_schemas import EXTERNAL_MCP_WRITE_TOOL_INPUT_SCHEMAS
from unity_mcp_core_client import UnityMcpCoreClient, UnityMcpCoreError

@pytest.mark.parametrize('index',[0,2])
def test_reparent_preserves_exact_index_through_public_and_approved_callbacks(index):
    params={'gameObjectPath':'Avatar/C','newParentPath':'Avatar','siblingIndex':index}
    plan=build_scene_execution_plan('vrcforge_reparent_gameobject',params)
    assert plan[0][1]['siblingIndex']==index
    tree=ast.parse(Path('dashboard_server.py').read_text(encoding='utf-8-sig'))
    node=next(n for n in tree.body if isinstance(n,ast.FunctionDef) and n.name=='reparent_gameobject_sync')
    calls=[]
    env={'Any':object,'sibling_index_arguments':sibling_index_arguments,'build_gameobject_target':lambda p:p['gameObjectPath'],
         'build_agent_connection_request':lambda p:p,'load_dashboard_settings':lambda p:{},
         'invoke_unity_mcp':lambda settings,tool,args,**kw:calls.append((tool,args)) or {'ok':True},
         'extract_tool_result_payload':lambda p:p,'ensure_dict_payload':lambda p,label:p,'emit_log':lambda *a:None}
    exec(compile(ast.Module(body=[node],type_ignores=[]),'dashboard_server.py','exec'),env)
    assert env['reparent_gameobject_sync'](params)['ok']
    assert calls==plan
    assert identity_scope('vrcforge_reparent_gameobject',write=True,arguments=params)=='object'

@pytest.mark.parametrize('value',[-1,True,1.2,'1'])
def test_invalid_indices_are_rejected_before_execution(value):
    params={'gameObjectPath':'Avatar/C','newParentPath':'Avatar','siblingIndex':value}
    with pytest.raises(ValueError):build_scene_execution_plan('vrcforge_reparent_gameobject',params)
    schema=EXTERNAL_MCP_WRITE_TOOL_INPUT_SCHEMAS['vrcforge_reparent_gameobject']
    assert list(Draft202012Validator(schema).iter_errors({'projectPath':'fixture',**params}))


def test_optional_index_does_not_invent_zero_or_change_legacy_request():
    call=build_scene_execution_plan('vrcforge_reparent_gameobject',{'gameObjectPath':'Avatar/C','newParentPath':'Other'})[0][1]
    assert 'siblingIndex' not in call
    assert sibling_index_arguments({'sibling_index':0})=={'siblingIndex':0}


def test_model_visible_projection_keeps_sort_argument_and_exact_identity():
    schema=canonical_unity_write_tool_input_schema('vrcforge_reparent_gameobject')
    assert schema['properties']['siblingIndex']['type']=='integer'
    assert schema['properties']['siblingIndex']['minimum']==0
    assert 'executionTarget' in schema['required']
    descriptor=standardize_tool_descriptor({'name':'vrcforge_reparent_gameobject','inputSchema':schema},write=True)
    assert descriptor['requiredIdentity']['scope']=='object'
    assert descriptor['permission']=='RequiresApproval'
    assert descriptor['checkpoint']['required'] is True

@pytest.mark.parametrize('contract',['87','174','unknown','175.0',''])
def test_old_core_cannot_silently_ignore_new_sort_argument(contract):
    client=object.__new__(UnityMcpCoreClient);client._connection=SimpleNamespace(tool_contract_version=contract)
    client._request=lambda *a,**kw:pytest.fail('must reject before tools/call')
    with pytest.raises(UnityMcpCoreError,match='Core tool contract'):
        client.call_tool('vrc_reparent_gameobject',{'gameObjectPath':'Avatar/C','newParentPath':'Avatar','siblingIndex':0})


@pytest.mark.parametrize('contract',['175','176','177','182','183'])
def test_sort_capability_survives_newer_core_contracts(contract):
    client=object.__new__(UnityMcpCoreClient)
    client._connection=SimpleNamespace(tool_contract_version=contract)
    calls=[]
    def request(method,params,**kwargs):
        calls.append((method,params,kwargs))
        return {'content':[{'type':'text','text':'ok'}],'isError':False,'structuredContent':{'success':True}}
    client._request=request
    arguments={'gameObjectPath':'Avatar/C','newParentPath':'Avatar','siblingIndex':0}
    context={'approvalId':'approved-sort','checkpointId':'exact-scene-checkpoint'}
    result=client.call_tool('vrc_reparent_gameobject',arguments,execution_context=context)
    assert result['content'][0]['text']=='ok'
    assert len(calls)==1
    assert calls[0][:2]==('tools/call',{'name':'vrc_reparent_gameobject','arguments':arguments})
    assert calls[0][2]['execution_context']==context


def test_older_core_reparent_without_sort_keeps_existing_request():
    client=object.__new__(UnityMcpCoreClient)
    client._connection=SimpleNamespace(tool_contract_version='174')
    calls=[]
    client._request=lambda method,params,**kwargs:calls.append((method,params)) or {'content':[{'type':'text','text':'ok'}],'isError':False,'structuredContent':{'success':True}}
    arguments={'gameObjectPath':'Avatar/C','newParentPath':'Other'}
    client.call_tool('vrc_reparent_gameobject',arguments)
    assert calls==[('tools/call',{'name':'vrc_reparent_gameobject','arguments':arguments})]
