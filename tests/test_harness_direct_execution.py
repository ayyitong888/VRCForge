from __future__ import annotations

import dashboard_server
from internal_tool_blocks import internal_tool_block_for_name


def test_basic_reads_are_available_in_initial_catalog_without_discovery():
    planner = dashboard_server.AGENT_GATEWAY._runtime_planner
    for project_active in (False, True):
        catalog = planner._catalog.read('planning', project_context_active=project_active)
        prompt = planner._build_llm_plan_prompt('Read an explicitly named file', [],
            project_context_active=project_active, internal_tool_blocks=['core'])
        for runtime_name in ('vrcforge_read_text_file', 'vrcforge_search_text',
                             'vrcforge_find_files', 'vrcforge_list_directory'):
            tool = next(t for t in catalog.visible_tools if t.runtime_name == runtime_name)
            assert tool.block == 'core'
            assert f'- {tool.name}' in prompt
            assert not tool.write
        assert not any(t.write for t in catalog.visible_tools)


def test_basic_read_exposure_does_not_eagerly_expose_writes_or_unity_operations():
    for name in ('vrcforge_edit_file', 'vrcforge_write_file', 'vrcforge_delete_path'):
        assert internal_tool_block_for_name(name, 'general') != 'core'
    assert internal_tool_block_for_name('vrcforge_manage_fx_animator', 'unity') != 'core'


def test_action_commentary_is_optional_and_not_a_repeated_plan():
    prompt = dashboard_server.AGENT_GATEWAY._runtime_planner._build_llm_plan_prompt(
        'Read an explicitly named file', [], internal_tool_blocks=['core'])
    assert 'Non-final action commentary is optional' in prompt
    assert 'Do not repeat preparation or narrate routine tool discovery/loading' in prompt
    assert '自然地说明你理解了什么、打算怎么做' not in prompt
