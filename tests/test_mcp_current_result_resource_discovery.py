"""Resource expansion through production routers/journal; no Unity/backend calls."""
import json
import pytest
from mcp_resource_registry import McpResourceRegistry
from test_external_mcp_result_projection import protocol,URI

@pytest.mark.parametrize('transport',['standard','2026'])
def test_compact_receipt_has_standard_resource_link_and_no_repeated_scan(transport):
    payload={'ok':True,'operationResource':URI,'result':{'materials':[{'name':f'Material{i}','shader':'lilToon','properties':{'x':'a'*100}} for i in range(316)]}}
    request,calls=protocol(transport,payload,descriptor={'name':'fixture','inputSchema':{'type':'object'},'write':False})
    result=request()['result']
    links=[block for block in result['content'] if block['type']=='resource_link']
    assert len(links)==1 and links[0]['uri']==URI
    assert links[0]['name'] and links[0]['mimeType']=='application/json'
    expanded=request('resources/read',uri=links[0]['uri'])['result']['structuredContent']
    assert expanded==payload and len(expanded['result']['materials'])==316
    assert len(calls)==1


def test_latest_receipt_is_on_first_page_with_all_old_resources_still_pageable(tmp_path):
    store=McpResourceRegistry(tmp_path/'resources')
    published=[]
    for i in range(105):
        published.append(store.publish(base_uri=f'vrcforge://operation/{i:04d}/receipt',name=f'Receipt{i}',resource_type='operation_receipt',data={'i':i},source_mode='test',refresh_rule='Call original tool only for fresh state')['uri'])
    first=store.list()
    assert first['resources'][0]['uri']==published[-1]
    assert len(first['resources'])==100 and first['nextCursor']=='100'
    second=store.list(cursor=first['nextCursor'])
    combined=[r['uri'] for r in first['resources']+second['resources']]
    assert combined==list(reversed(published))
    assert 'nextCursor' not in second
    # A list-first host can expand this current operation with no selector meta.
    read=store.read(first['resources'][0]['uri'])
    assert json.loads(read['contents'][0]['text'])['data']['i']==104


def test_revised_resource_moves_to_front_without_duplicating_base_identity(tmp_path):
    store=McpResourceRegistry(tmp_path/'resources')
    def publish(name,value):return store.publish(base_uri=f'vrcforge://operation/{name}/receipt',name=name,resource_type='operation_receipt',data={'value':value},source_mode='test',refresh_rule='Read')['uri']
    old=publish('first',1);other=publish('second',1);new=publish('first',2)
    assert [x['uri'] for x in store.list()['resources']]==[new,other]
    assert json.loads(store.read(old)['contents'][0]['text'])['data']['value']==1

@pytest.mark.parametrize('version',['2024-10-07','2024-11-05','2025-03-26'])
def test_legacy_negotiated_protocol_keeps_supported_content_types(version):
    from agent_mcp_standard import McpStandardRouter
    payload={'ok':True,'operationResource':URI,'result':{'items':list(range(10000))}}
    router=McpStandardRouter(lambda *_:[{'name':'read','inputSchema':{'type':'object'}}],lambda *_:payload,resource_read=lambda _: {})
    router.handle({'jsonrpc':'2.0','id':0,'method':'initialize','params':{'protocolVersion':version,'capabilities':{},'clientInfo':{'name':'legacy','version':'1'}}})
    result=router.handle({'jsonrpc':'2.0','id':1,'method':'tools/call','params':{'name':'read','arguments':{}}})['result']
    assert all(x['type']=='text' for x in result['content'])
    assert result['structuredContent']['operationResource']==URI
