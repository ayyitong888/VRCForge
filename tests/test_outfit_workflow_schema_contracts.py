"""Source-only registered outfit schema regressions; no Unity tool calls."""
import pytest
from jsonschema import Draft202012Validator

@pytest.fixture(scope='module')
def catalog():
    import dashboard_server as d
    return d.AGENT_GATEWAY, {t['name']:t for t in d.AGENT_GATEWAY.build_external_mcp_tools('execution',tool_blocks=['*'])}

@pytest.mark.parametrize('suffix',['setup_outfit','preview_setup_outfit','add_outfit','preview_add_outfit'])
def test_public_outfit_fields_and_internal_parity(catalog,suffix):
    gateway,tools=catalog;name='vrcforge_'+suffix
    schema=tools[name]['inputSchema']
    assert 'outfitPath' in schema['properties'] if 'setup' in suffix else 'assetPath' in schema['properties']
    assert schema==gateway.shared_agent_tool_descriptor(name,write=name in gateway._write_handlers)['inputSchema']
    assert not any(k.startswith('__') for k in schema['properties'])
    Draft202012Validator.check_schema(schema)

@pytest.mark.parametrize('suffix',['setup_outfit','add_outfit'])
def test_preview_and_write_are_exact_pairs(catalog,suffix):
    _,tools=catalog
    assert tools['vrcforge_'+suffix]['inputSchema']==tools['vrcforge_preview_'+suffix]['inputSchema']

def test_setup_existing_bool_conversion_and_optional_avatar(catalog):
    from wardrobe_outfit_workflow_service import build_setup_outfit_request
    _,tools=catalog;schema=tools['vrcforge_setup_outfit']['inputSchema']
    target={'schema':'vrcforge.execution_target.v1','namespace':{},'scope':{},'project':{},'editor':{}}
    for value in ('false',0,None,[],True):
        p={'outfit_path':'Avatar/Clothes','save_scene':value,'executionTarget':target}
        Draft202012Validator(schema).validate(p)
        assert build_setup_outfit_request(p,False)['saveScene']==bool(value)
    assert 'avatarPath' not in schema.get('required',[])

def test_add_aliases_required_identity_and_bool_coercion(catalog):
    from prepared_add_outfit_workflow_service import _workflow_bool
    _,tools=catalog;schema=tools['vrcforge_add_outfit']['inputSchema']
    target={'schema':'vrcforge.execution_target.v1','namespace':{},'scope':{},'project':{},'editor':{}}
    for value in ('false',0,None,[],True):
        p={'avatar_path':'Avatar','asset_query':'Clothes','manage_wardrobe':value,'executionTarget':target}
        Draft202012Validator(schema).validate(p)
        assert isinstance(_workflow_bool(p,('manage_wardrobe',),True),bool)
    assert 'parentPath' not in schema.get('required',[])
    assert list(Draft202012Validator(schema).iter_errors({'avatarPath':'Avatar','executionTarget':target}))
