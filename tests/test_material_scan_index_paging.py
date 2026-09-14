"""Production registered material read with fixed scan I/O, not live acceptance."""
from copy import deepcopy
import pytest
from external_mcp_result_projection import project_result

@pytest.fixture
def scan(monkeypatch):
    import dashboard_server as d
    rows=[{'material_id':f'Avatar/Dress{i}::slot0','material_name':f'Dress{i}','renderer_path':f'Avatar/Dress{i}','shader_name':'lilToon','shader_family':'lilToon','properties':{'large':'x'*15000}} for i in range(316)]
    monkeypatch.setattr(d,'load_dashboard_settings',lambda _:None)
    monkeypatch.setattr(d,'emit_log',lambda *a,**kw:None)
    def direct(_settings,_avatar,material_ids=None,**kw):
        selected=[r for r in rows if not material_ids or r['material_id'] in material_ids]
        return {'materials':deepcopy(selected),'summary':{'materialCount':len(selected),'rendererCount':316}}
    monkeypatch.setattr(d,'scan_shader_materials_direct',direct)
    return d.AGENT_GATEWAY._tools['vrcforge_scan_materials'].handler,rows


def test_index_pages_survive_compact_and_cover_all_real_ids(scan):
    call,rows=scan;offset=0;ids=[]
    while True:
        result=call({'avatarPath':'Avatar','indexOnly':True,'offset':offset,'limit':100})
        compact=project_result({'ok':True,'operationResource':'vrcforge://operation/test/receipt','result':result},mode='compact',resource_readable=True)['result']
        assert compact['paging']['total']==316
        ids.extend(r['material_id'] for r in compact['index'])
        assert 'inventory' not in result and 'materials' not in result
        next_offset=compact['paging']['nextOffset']
        if next_offset is None:break
        assert next_offset>offset;offset=next_offset
    assert ids==[r['material_id'] for r in rows]


def test_default_is_full_and_index_ids_work_for_existing_detail_filter(scan):
    call,rows=scan
    result=call({'avatarPath':'Avatar'})
    assert result['materials']==rows and result['inventory']['materials']==rows
    assert 'paging' not in result
    page=call({'indexOnly':True,'limit':1,'offset':12})
    material_id=page['index'][0]['material_id']
    detail=call({'materialIds':[material_id]})
    assert detail['materials']==[rows[12]]
    paged=call({'offset':315,'limit':5})
    assert paged['materials']==[rows[-1]] and paged['paging']['nextOffset'] is None
    assert paged['summary']['materialCount']==316


def test_oversized_index_row_reports_terminal_error_not_stuck_cursor(scan):
    call,rows=scan;rows[0]['material_name']='x'*9000
    result=call({'indexOnly':True,'limit':1})
    assert result['ok'] is False and result['errorCode']=='material_index_row_too_large'
    assert result['paging']['nextOffset'] is None


def test_schema_matches_model_and_public_internal_projection():
    import dashboard_server as d
    from dashboard_api_models import ShaderMaterialScanRequest
    schema=d.AGENT_GATEWAY.shared_agent_tool_descriptor('vrcforge_scan_materials',write=False)['inputSchema']
    tools={x['name']:x for x in d.AGENT_GATEWAY.build_external_mcp_tools('execution',tool_blocks=['*'])}
    assert schema==tools['vrcforge_scan_materials']['inputSchema']
    assert {'offset','limit','indexOnly'}<=schema['properties'].keys()
    request=ShaderMaterialScanRequest(offset=2,limit=5,indexOnly=True)
    assert request.offset==2 and request.limit==5 and request.index_only is True
    with pytest.raises(ValueError):ShaderMaterialScanRequest(limit=0)
