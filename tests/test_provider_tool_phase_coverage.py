import pytest

from provider_request_ledger import classify_request_phases
from provider_tool_phases import PHASE_BY_TOOL, _TOOLS_BY_PHASE


def _require_mapping(names):
    missing = set(names) - PHASE_BY_TOOL.keys()
    assert not missing, f"New exposed tools require explicit ledger phases: {sorted(missing)}"


@pytest.mark.parametrize("layer", ["planning", "execution"])
@pytest.mark.parametrize("project", [False, True])
def test_every_exposed_tool_and_native_wrapper_has_a_fixed_phase(layer, project):
    import dashboard_server as host

    catalog = host._RuntimePlannerCatalog().read(layer, project_context_active=project)
    names = {name for tool in (*catalog.visible_tools, *catalog.routable_tools)
             for name in (tool.name, tool.runtime_name) if name}
    request, _ = host.RUNTIME_PLANNER._build_native_plan_request(
        [{"role": "user", "content": "offline catalog coverage"}],
        observe={}, exposure_layer=layer, project_context_active=project,
        project_path=None, internal_tool_blocks=(), global_instructions="", project_instructions="",
    )
    names.update(t["function"]["name"] for t in request["tools"])
    _require_mapping(names)


def test_new_tool_without_a_mapping_fails_the_coverage_gate():
    with pytest.raises(AssertionError, match="new_unmapped_tool"):
        _require_mapping([*PHASE_BY_TOOL, "new_unmapped_tool"])


def test_unmapped_tool_is_unknown_even_with_model_phase_and_write_metadata():
    assert classify_request_phases(
        [{"id": "new", "name": "new_unmapped_tool", "action": "write", "stage": "discover"}],
        {"phase": "审批", "phases": ["绑定"], "toolMetadata": {"new_unmapped_tool": {"effect": "read"}}},
    ) == (["unknown"], "unknown")


def test_fixed_mapping_is_unique_and_does_not_accept_model_overrides():
    names = [name for group in _TOOLS_BY_PHASE.values() for name in group]
    assert len(names) == len(set(names))
    assert set(PHASE_BY_TOOL.values()) == {"找工具", "绑定", "预览", "审批", "恢复", "unknown"}
    assert classify_request_phases(
        [{"id": "load", "name": "load_internal_tool_block", "action": "write", "stage": "restore"}],
        {"phase": "恢复"},
    ) == (["找工具"], "找工具")
