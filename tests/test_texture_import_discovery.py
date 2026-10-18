"""External texture import must be discoverable as project asset work."""
import dashboard_server as d
from tool_discovery_search import search_tool_leaves

ASSETS='project_environment/assets_packages'
IMPORT='unity_import_outfit_package'


def test_actual_external_texture_queries_route_import_to_project_assets():
    catalog=d._RuntimePlannerCatalog().read('execution',project_context_active=True)
    tools={tool.name:tool for tool in catalog.visible_tools}
    assert tools[IMPORT].block==ASSETS
    for query in ('import external texture assign material texture','copy external file into Unity project import'):
        result=search_tool_leaves(query,catalog.visible_tools,{})
        assert any(match['leafPath']==ASSETS and IMPORT in match['toolNames'] for match in result['matches'][:2])


def test_directory_and_planning_keep_import_preflight_and_write_separate():
    execution=d._RuntimePlannerCatalog().read('execution',project_context_active=True)
    relevant={tool.runtime_name:tool for tool in execution.visible_tools}
    for name in ('vrcforge_inspect_outfit_package','vrcforge_plan_outfit_import','vrcforge_import_outfit_package'):
        assert relevant[name].block==ASSETS
    planning=d._RuntimePlannerCatalog().read('planning',project_context_active=True)
    visible={tool.runtime_name for tool in planning.visible_tools}
    assert 'vrcforge_plan_outfit_import' in visible
    assert 'vrcforge_import_outfit_package' not in visible
