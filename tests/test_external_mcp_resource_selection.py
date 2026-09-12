"""Scoped reads of immutable JSON Resources use the real public protocol boundaries."""
from copy import deepcopy
import importlib
import json
import os
from pathlib import Path
import pytest
from agent_mcp_2026 import Mcp2026Router, PROTOCOL_VERSION
from agent_mcp_standard import McpStandardRouter, LATEST_PROTOCOL_VERSION
KEY='io.vrcforge/resourceSelection'
ROOT=Path(__file__).resolve().parents[1]
ARCHIVE_ROOT=Path(os.environ['VRCFORGE_REGRESSION_ARCHIVE_ROOT']) if os.environ.get('VRCFORGE_REGRESSION_ARCHIVE_ROOT') else ROOT/'.tmp'/'regression-archives'
URI='vrcforge://operation/test/receipt?revision=1'
FIELDS=['clip_path','clip_local_id','path','type_name','property_name','binding_kind','keys']

def requester(router,transport):
    if transport=='standard':router.handle({'jsonrpc':'2.0','id':0,'method':'initialize','params':{'protocolVersion':LATEST_PROTOCOL_VERSION,'capabilities':{},'clientInfo':{'name':'test','version':'1'}}})
    def request(selection=None,method='resources/read',uri=URI):
        meta={'io.modelcontextprotocol/protocolVersion':PROTOCOL_VERSION,'io.modelcontextprotocol/clientCapabilities':{}} if transport=='2026' else {}
        if selection is not None:meta[KEY]=selection
        raw=router.handle({'jsonrpc':'2.0','id':1,'method':method,'params':{'_meta':meta,'uri':uri}})
        return raw[0] if transport=='2026' else raw
    return request

def resource(doc):return {'contents':[{'uri':URI,'mimeType':'application/json','text':json.dumps(doc)}],'structuredContent':deepcopy(doc)}

def router_for(transport,callback):
    cls=Mcp2026Router if transport=='2026' else McpStandardRouter
    return requester(cls(lambda *_:[],lambda *_:pytest.fail('Resource reads must not dispatch a tool'),resource_read=callback),transport)

@pytest.fixture
def archived606():
    p=ARCHIVE_ROOT/'606.json'
    if not p.exists():pytest.skip('Local public archive not distributed')
    return json.loads(p.read_text('utf-8-sig'))['results'][2]

@pytest.mark.parametrize('transport',['2026','standard'])
def test_606_two_pages_preserve_every_binding_and_original_full_resource(transport,archived606):
    calls=[]
    def read(uri):calls.append(uri);return deepcopy(archived606)
    request=router_for(transport,read)
    original=deepcopy(archived606)
    doc=json.loads(original['contents'][0]['text']); expected=doc['data']['result']['result']['bindings']
    assert len(expected)==256
    uri=original['contents'][0]['uri']
    rows=[]
    for offset in (0,128):
        result=request({'pointer':'/data/result/result/bindings','offset':offset,'limit':128},uri=uri)['result']
        selected=json.loads(result['contents'][0]['text'])
        assert selected['schema']=='vrcforge.resource_selection.v1'
        assert selected['sourceUri']==uri
        assert selected['sourceRevision']==doc['revision']
        assert selected['sourceContentHash']==doc['contentHash']
        assert 'contentHash' not in selected
        assert selected['arrayCount']==256 and selected['offset']==offset
        assert selected['nextOffset']==(128 if offset==0 else None)
        assert selected['value']==expected[offset:offset+128]
        assert result['structuredContent']=={key:value for key,value in selected.items() if key!='value'}
        rows.extend(selected['value'])
    assert rows==expected
    for pointer,key in [('/data/result/result/summary','summary'),('/data/result/result/paging','paging')]:
        assert json.loads(request({'pointer':pointer},uri=uri)['result']['contents'][0]['text'])['value']==doc['data']['result']['result'][key]
    full=request(uri=uri)['result']
    assert full['contents']==original['contents'] and full['structuredContent']==original['structuredContent']
    assert archived606==original and calls==[uri]*5

@pytest.mark.parametrize('transport',['2026','standard'])
def test_606_all_256_required_binding_fields_and_keys_are_exact(transport,archived606):
    request=router_for(transport,lambda _:deepcopy(archived606))
    doc=json.loads(archived606['contents'][0]['text'])
    expected=doc['data']['result']['result']['bindings']
    rows=[]
    for offset in (0,128):
        response=request({'pointer':'/data/result/result/bindings','offset':offset,'limit':128,'fields':FIELDS})['result']
        selected=json.loads(response['contents'][0]['text'])
        assert selected['selectedFields']==FIELDS
        assert selected['value']==[{key:row[key] for key in FIELDS} for row in expected[offset:offset+128]]
        assert 'value' not in response['structuredContent']
        rows.extend(selected['value'])
    assert len(rows)==256
    assert rows==[{key:row[key] for key in FIELDS} for row in expected]

@pytest.mark.parametrize('transport',['2026','standard'])
def test_fields_project_only_top_level_and_reject_missing_or_non_objects(transport):
    request=router_for(transport,lambda _:resource({'row':{'keep':{'all':[1,2]},'omit':3},'mixed':[{'keep':1},2]}))
    response=request({'pointer':'/row','fields':['keep']})['result']
    assert json.loads(response['contents'][0]['text'])['value']=={'keep':{'all':[1,2]}}
    for selection in [{'pointer':'/row','fields':['missing']},{'pointer':'/mixed','fields':['keep']},
                      {'pointer':'/row','fields':[]},{'pointer':'/row','fields':['keep','keep']},
                      {'pointer':'/row','fields':[str(i) for i in range(17)]},{'pointer':'/row','fields':[2]}]:
        assert request(selection)['error']['code']==-32602

@pytest.mark.parametrize('transport',['2026','standard'])
@pytest.mark.parametrize('pointer,expected',[('/a~1b/~0key',7),('/a~1b/empty',None),('/array/0',{'x':1}),('/01','object key'),('/e\u0301','combining'),('',{'x':2})])
def test_rfc6901_exact_keys_and_scalar_values(transport,pointer,expected):
    doc={'a/b':{'~key':7,'empty':None},'array':[{'x':1}],'01':'object key','e\u0301':'combining'} if pointer else {'x':2}
    result=json.loads(router_for(transport,lambda _:resource(doc))({'pointer':pointer})['result']['contents'][0]['text'])
    assert result['value']==expected

@pytest.mark.parametrize('transport',['2026','standard'])
@pytest.mark.parametrize('selection',[{'pointer':'/missing'},{'pointer':'/array/00'},{'pointer':'/array/-1'},{'pointer':'/array/-'},{'pointer':'/array/1'}, {'pointer':'/s/0'},{'pointer':'/a~2b'}, {'pointer':'#/'}, {'pointer':'/array','offset':1,'limit':1},{'pointer':'/array','offset':True},{'pointer':'/array','limit':0},{'pointer':'/array','limit':129},{'pointer':'/s','limit':1},{'pointer':'/array','extra':1},{'pointer':'/\u00e9'}])
def test_invalid_selection_never_falls_back_to_full(transport,selection):
    result=router_for(transport,lambda _:resource({'array':[3],'s':'abc','e\u0301':4}))(selection)
    assert result['error']['code']==-32602
    assert KEY in result['error']['message'] and 'result' not in result

@pytest.mark.parametrize('transport',['2026','standard'])
def test_non_json_and_ambiguous_contents_rejected_but_original_read_unchanged(transport):
    for original in [{'contents':[{'uri':URI,'text':'not JSON'}]}, {'contents':[{'uri':URI,'text':'{}'},{'uri':URI,'text':'{}'}]}]:
        request=router_for(transport,lambda _:deepcopy(original))
        assert request({'pointer':''})['error']['code']==-32602
        assert request()['result']['contents']==original['contents']

@pytest.mark.parametrize('transport',['2026','standard'])
def test_existing_resource_permission_failure_is_not_bypassed(transport):
    def denied(uri):raise ValueError('Resource unavailable for this authenticated session')
    result=router_for(transport,denied)({'pointer':'/data'})
    assert result['error']['code']==-32002
    assert 'result' not in result

@pytest.mark.parametrize('transport',['2026','standard'])
def test_stdio_selects_once_after_original_resource_read(monkeypatch,transport):
    module=importlib.import_module('tools.vrcforge_agent_mcp_stdio')
    captured={}
    monkeypatch.setattr(module,'run_stdio_loop',lambda router:captured.setdefault('router',router))
    monkeypatch.setattr(module,'run_standard_stdio_loop',lambda router:captured.setdefault('router',router))
    class Bridge:
        calls=[]
        def read_resource(self,uri):self.calls.append(uri);return resource({'array':[{'value':1},{'value':2}]})
        def preflight(self):return {'runtimeOnline':False}
    bridge=Bridge()
    module.run_stdio_server(bridge,protocol_profile='vrcforge-2026' if transport=='2026' else 'mcp-1x')
    result=json.loads(requester(captured['router'],transport)({'pointer':'/array','offset':1,'limit':1})['result']['contents'][0]['text'])
    assert result['value']==[{'value':2}]
    assert bridge.calls==[URI]
