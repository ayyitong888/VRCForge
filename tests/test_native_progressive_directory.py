"""Behavior contract: browse one canonical directory layer without losing content."""
from copy import deepcopy
import json

import pytest

from internal_tool_blocks import CANONICAL_TOOL_BLOCKS, build_internal_tool_block_tree
from tests.test_runtime_planner_service import service


@pytest.fixture
def catalog_leaves():
    # Use the real registered catalog/handler inventory, not a hand-maintained list.
    import dashboard_server
    return dashboard_server._internal_tool_block_leaves('execution', project_context_active=True)


def raw_directory(leaves, selector=None):
    result = build_internal_tool_block_tree(selector=selector, loaded_blocks=['core'], leaves=leaves)
    result['internalToolSelections'] = {}
    return result


def project(raw, *, native=True, status='executed'):
    text = service()._llm_loop_step_observation({
        'tool': 'vrcforge_list_internal_tool_blocks', 'status': status, 'result': raw,
    }, native_contract=native)
    return json.loads(text.split('; toolBlockDirectory=', 1)[1])


def test_native_default_directory_contains_only_six_complete_root_entries(catalog_leaves):
    raw = raw_directory(catalog_leaves)
    before = deepcopy(raw)
    result = project(raw)
    assert [row['name'] for row in result['blocks']] == list(CANONICAL_TOOL_BLOCKS)
    assert len(result['blocks']) == 6
    for expected, row in zip(raw['blocks'], result['blocks']):
        for field in ('id', 'name', 'title', 'description', 'loaded', 'depth', 'expandable'):
            assert row[field] == expected[field]
        assert not row.get('children')
        assert not row.get('toolNames')
        assert row['expandArguments'] == {'block': row['id']}
        # The returned exact selector must navigate the real existing directory.
        assert raw_directory(catalog_leaves, row['name'])['tree']['name'] == row['name']
    assert not result['tree'].get('children')
    assert result['loadedBlocks'] == raw['loadedBlocks']
    assert result['internalToolSelections'] == raw['internalToolSelections']
    assert raw == before


@pytest.mark.parametrize('root', list(CANONICAL_TOOL_BLOCKS))
def test_native_selected_root_contains_only_its_direct_complete_leaves(catalog_leaves, root):
    raw = raw_directory(catalog_leaves, root)
    expected = raw['tree']['children']
    result = project(raw)
    assert [row['name'] for row in result['blocks']] == [row['name'] for row in expected]
    for row, original in zip(result['blocks'], expected):
        for key, value in original.items():
            if key in ('toolNames', 'loadCall'):
                continue
            assert row[key] == value
        assert row['expandArguments'] == {'block': row['id']}
        assert not row.get('toolNames')
        if row.get('loadCall'):
            assert 'skill_params' not in row['loadCall']
            assert row['loadCall']['arguments']['block'] == row['id']
        assert not row.get('children')
    # If tree is retained rather than a reference it may only repeat this selection.
    assert not result['tree'].get('children') or result['tree']['children'] == result['blocks']
    assert result['loadedBlocks'] == raw['loadedBlocks']
    assert result['internalToolSelections'] == raw['internalToolSelections']


def test_native_full_traversal_reaches_every_original_tool_without_global_payload(catalog_leaves):
    raw = raw_directory(catalog_leaves)
    expected = {name for root in raw['blocks'] for leaf in root['children'] for name in leaf['toolNames']}
    reached = set()
    for root in project(raw)['blocks']:
        for leaf in project(raw_directory(catalog_leaves, root['name']))['blocks']:
            selected_raw = raw_directory(catalog_leaves, leaf['name'])
            selected = project(selected_raw)
            assert selected['tree'] == selected_raw['tree']
            assert not selected.get('blocks')
            expected_leaf = next(item for branch in raw['blocks'] for item in branch['children']
                                 if item['name'] == leaf['name'])
            assert {tool['name'] for tool in selected['tree']['tools']} == set(expected_leaf['toolNames'])
            reached.update(tool['name'] for tool in selected['tree']['tools'])
    assert expected
    assert reached == expected


def test_native_leaf_keeps_long_unique_tool_metadata(catalog_leaves):
    raw = raw_directory(catalog_leaves, 'project_environment/files')
    assert raw['tree']['tools']
    raw['tree']['description'] += ' Complete leaf guidance.' * 400 + ' LEAF-END'
    raw['tree']['tools'][0]['description'] = 'Full tool guidance.' * 500 + ' TOOL-END'
    before = deepcopy(raw)
    result = project(raw)
    assert result['tree'] == before['tree']
    assert not result.get('blocks')
    assert raw == before


def test_native_root_description_is_not_shortened(catalog_leaves):
    raw = raw_directory(catalog_leaves)
    raw['blocks'][0]['description'] += ' Complete routing guidance.' * 400 + ' ROOT-END'
    raw['tree']['children'] = deepcopy(raw['blocks'])
    result = project(raw)
    assert result['blocks'][0]['description'] == raw['blocks'][0]['description']
    assert not result['blocks'][0].get('children')


@pytest.mark.parametrize('selector', [None, 'research', 'research/web_research'])
def test_both_planner_lanes_browse_one_level_and_raw_public_result_remains_complete(catalog_leaves, selector):
    raw = raw_directory(catalog_leaves, selector)
    before = deepcopy(raw)
    legacy = project(raw, native=False)
    assert legacy == project(raw, native=True)
    assert all(not row.get('children') and not row.get('toolNames') for row in legacy['blocks'])
    assert len(raw['blocks']) == 6
    assert all(root['children'] for root in raw['blocks'])
    assert raw == before


def test_failed_canonical_directory_is_not_presented_as_successful_browse(catalog_leaves):
    raw = raw_directory(catalog_leaves)
    raw.update(ok=False, error='fixture directory failure')
    result = project(raw, status='failed')
    assert result['ok'] is False
    assert result['blocks'] == raw['blocks']


def test_unknown_directory_shape_retains_unique_fields():
    raw = {'ok': True, 'schema': 'fixture.custom_directory.v1',
        'blocks': [{'name': 'custom', 'description': 'Complete custom route',
                    'children': [{'name': 'custom/leaf', 'toolNames': ['fixture_tool']}]}],
        'tree': {'name': 'unique', 'description': 'Unique selection', 'tools': [{'name': 'other'}]},
        'loadedBlocks': ['core'], 'internalToolSelections': {}}
    before = deepcopy(raw)
    result = project(raw)
    assert result == before
    assert raw == before


@pytest.mark.parametrize("direct_leaf", [False, True])
def test_actual_native_requests_browse_load_and_execute_without_global_directory(tmp_path, monkeypatch, direct_leaf):
    import dashboard_server as d
    from runtime_planner_service import PlannerCatalogSnapshot, PlannerTool
    from tests.test_runtime_planner_service import FakeCatalog
    from tests.test_native_runtime_gateway import setup_gateway, call, finish, run
    gateway, model, invoked = setup_gateway(tmp_path, [])
    leaf = "project_environment/files"
    tools = (
        PlannerTool("list_internal_tool_blocks", "Browse.", "read", runtime_name="vrcforge_list_internal_tool_blocks"),
        PlannerTool("load_internal_tool_block", "Browse or load.", "read", runtime_name="vrcforge_load_internal_tool_block"),
        PlannerTool("read_text_file", "Read.", "read", runtime_name="vrcforge_read_text_file", block=leaf),
    )
    leaves = [{"name": "read_text_file", "block": leaf, "mode": "read"}]
    monkeypatch.setattr(d, "AGENT_GATEWAY", gateway)
    monkeypatch.setattr(d, "_internal_tool_block_leaves", lambda *args, **kwargs: deepcopy(leaves))
    gateway.register_tool("vrcforge_list_internal_tool_blocks", "When to use: browse. When NOT to use: write.", "read/debug",
                          lambda args: raw_directory(leaves, args.get("block")))
    gateway.register_tool("vrcforge_load_internal_tool_block", "When to use: browse or load. When NOT to use: write.", "read/debug", d.load_internal_tool_block)
    gateway._runtime_planner._catalog = FakeCatalog(planning=PlannerCatalogSnapshot(visible_tools=tools, routable_tools=tools))
    selectors = [leaf] if direct_leaf else [None, "project_environment", leaf]
    replies = [call(f"browse-{i}", "load_internal_tool_block", {"block": selector} if selector else {})
               for i, selector in enumerate(selectors)]
    replies += [call("read", "read_text_file", {"path": "fixture.txt"}), finish]
    model.replies = iter(replies)
    result = run(gateway, tmp_path, maxAgenticTurns=8)
    assert result["plan"]["nextStep"] == "done"
    assert len(invoked) == 1
    for request in model.requests:
        names = {t["function"]["name"] for t in request["tools"]}
        assert "load_internal_tool_block" in names
        assert "list_internal_tool_blocks" not in names
    for i, selector in enumerate(selectors[:-1]):
        message = next(m for m in model.requests[i+1]["messages"] if m.get("tool_call_id") == f"browse-{i}")
        observation = json.loads(message["content"])["observations"][0]["observation"]
        payload = json.loads(observation.split("; toolBlockDirectory=", 1)[1])
        assert all(not row.get("children") and not row.get("toolNames") for row in payload["blocks"])
        if selector:
            assert all(row["name"].startswith(selector + "/") for row in payload["blocks"])
        else:
            assert len(payload["blocks"]) == 6
    before = model.requests[len(selectors)-1]["tools"]
    after = model.requests[len(selectors)]["tools"]
    assert all(t["function"]["name"] != "read_text_file" for t in before)
    assert after[:len(before)] == before
    assert after[-1]["function"]["name"] == "read_text_file"
    assert gateway.runtime_sessions.internal_tool_selections("native-session")[leaf] is None


@pytest.mark.parametrize('selector', [None, 'project_environment', 'project_environment/assets_packages'])
def test_package_discovery_describes_app_capabilities_and_unity_without_global_inventory(catalog_leaves, selector):
    result = project(raw_directory(catalog_leaves, selector))
    if selector is None:
        assert len(result['blocks']) == 6
        row = next(row for row in result['blocks'] if row['name'] == 'project_environment')
        assert not row.get('children') and not row.get('toolNames')
        assert row['expandArguments'] == {'block': 'project_environment'}
    elif selector == 'project_environment':
        assert {row['name'] for row in result['blocks']} == {
            'project_environment/assets_packages', 'project_environment/files', 'project_environment/shell'}
        row = next(row for row in result['blocks'] if row['name'].endswith('/assets_packages'))
        assert not row.get('children') and not row.get('toolNames')
        assert row['expandArguments'] == {'block': 'project_environment/assets_packages'}
    else:
        assert result['blocks'] == []
        row = result['tree']
        raw = raw_directory(catalog_leaves, selector)
        assert row['tools'] == raw['tree']['tools']
        assert row['tools']
    description = row['description']
    assert 'installed VRCForge .vsk capability package state' in description
    assert 'enablement' in description
    assert 'Unity' in description and 'assets' in description and 'dependencies' in description
    assert 'packages, dependencies' in description
    assert 'asset/package inventory' in description
    if selector is not None:
        assert 'prefabs, packages, dependencies, imports' in description
    assert 'Do not use when:' in description
    assert 'Planning: read, inspect, and preview only' in description
    assert 'Execution: approved' in description
