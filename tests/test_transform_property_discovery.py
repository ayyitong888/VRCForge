"""Existing component reads must be discoverable before proportion changes."""
import dashboard_server as host
from tool_discovery_search import search_tool_leaves


def test_original_transform_queries_discover_existing_property_reader():
    catalog=host._RuntimePlannerCatalog().read('execution',project_context_active=True)
    reader=next(tool for tool in catalog.visible_tools if tool.name=='unity_get_property')
    assert reader.block=='avatar_structure/hierarchy_components'
    for query in ('read inspect Transform localScale localPosition bone proportions',
                  'read Transform localRotation localScale'):
        result=search_tool_leaves(query,catalog.visible_tools,{})
        assert any(reader.name in match['toolNames'] for match in result['matches'][:2]), result


def test_property_reader_stays_readonly_identity_bound_and_project_scoped():
    for layer in ('planning','execution'):
        catalog=host._RuntimePlannerCatalog().read(layer,project_context_active=True)
        reader=next(tool for tool in catalog.visible_tools if tool.name=='unity_get_property')
        assert reader.write is False
        assert reader.runtime_name=='vrcforge_get_property'
        assert 'executionTarget' in reader.input_schema['required']
        assert all(name in reader.description for name in ('Transform','localScale','localPosition','localRotation'))
    general=host._RuntimePlannerCatalog().read('execution',project_context_active=False)
    assert 'unity_get_property' not in {tool.name for tool in general.visible_tools}
