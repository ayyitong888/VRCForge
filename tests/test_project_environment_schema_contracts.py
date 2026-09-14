"""Production registry schema contracts; no live tool execution."""
import pytest
from jsonschema import Draft202012Validator

NAMES = 'diagnose_package_install_errors export_interrupted_apply_incident_bundle list_checkpoints package_manager_status scan_modular_avatar scan_vrcfury unity_status unity_tools core_upgrade_status create_project project_catalog_registration_status project_create_plan register_project register_project_catalog restore_unity_core rollback_project_catalog_registration rollback_project_lifecycle scan_project_index select_project'.split()
@pytest.fixture(scope='module')
def catalog():
    import dashboard_server as d
    return d.AGENT_GATEWAY, {t['name']:t for t in d.AGENT_GATEWAY.build_external_mcp_tools('execution', tool_blocks=['*'])}

@pytest.mark.parametrize('suffix', NAMES)
def test_registered_public_parameters_and_parity(catalog, suffix):
    gateway, tools = catalog
    name='vrcforge_'+suffix
    schema=tools[name]['inputSchema']
    assert set(schema['properties'])-{'promptSkillProvenance','executionTarget'}
    assert schema == gateway.shared_agent_tool_descriptor(name,write=name in gateway._write_handlers)['inputSchema']
    assert not {'settings_path','unity_host','unity_port','unity_instance','api_key','model'} & schema['properties'].keys()
    Draft202012Validator.check_schema(schema)

@pytest.mark.parametrize('suffix,field,default',[('list_checkpoints','limit',50),('diagnose_package_install_errors','maxCompileErrors',30),('scan_project_index','maxFiles',100000)])
def test_existing_numeric_coercion_is_not_tightened(catalog,suffix,field,default):
    _,tools=catalog
    prop=tools['vrcforge_'+suffix]['inputSchema']['properties'][field]
    assert prop['default']==default
    for value in (3,3.0,'3',None,False,0):
        Draft202012Validator(prop).validate(value)

def test_create_plan_and_write_share_domain_fields(catalog):
    _,tools=catalog
    read=tools['vrcforge_project_create_plan']['inputSchema']
    write=tools['vrcforge_create_project']['inputSchema']
    for key in ('projectPath','projectRoot','projectName','template','templatePath','template_path'):
        assert read['properties'][key]==write['properties'][key]
    assert read['properties']['template']['default']=='Avatar'
    assert read['anyOf']==write['anyOf']

def test_receipt_selectors_and_restore_exact_fields(catalog):
    _,tools=catalog
    for suffix in ('rollback_project_catalog_registration','rollback_project_lifecycle'):
        schema=tools['vrcforge_'+suffix]['inputSchema']
        Draft202012Validator(schema).validate({'receipt_id':'receipt'})
        assert list(Draft202012Validator(schema).iter_errors({}))
    restore=tools['vrcforge_restore_unity_core']['inputSchema']
    assert {'projectPath','backupPath','backupSha256','installedSha256'} <= set(restore['required'])

def test_application_project_writes_preserve_registered_identity_policy(catalog):
    from unity_tool_schema_projection import _PROJECT_APPLICATION_WRITE_TOOLS
    gateway,tools=catalog
    for name in _PROJECT_APPLICATION_WRITE_TOOLS:
        assert gateway._write_handlers[name].requires_approved_execution_context is False
        assert 'executionTarget' not in tools[name]['inputSchema'].get('required',[])
    Draft202012Validator(tools['vrcforge_create_project']['inputSchema']).validate({'projectRoot':'D:/NotCreated','template':'Avatar'})
    Draft202012Validator(tools['vrcforge_select_project']['inputSchema']).validate({'project_root':'D:/Existing'})
