"""Fresh planning must offer purpose-built reads and factual shell context."""
import json
from types import SimpleNamespace

import pytest

import dashboard_server as app
from internal_tool_blocks import internal_tool_block_for_name
from runtime_planner_service import PlannerCatalogSnapshot, RuntimePlannerService


def prompt(*, observe=None, active=False, catalog=None):
    service = RuntimePlannerService(catalog=catalog or app._RuntimePlannerCatalog(), desktop=object())
    return service._build_llm_plan_prompt(
        "Read https://example.org/reference and quote its first two rules", [],
        observe=observe, internal_tool_blocks=["core"], project_context_active=active,
    )


def test_fresh_general_planner_has_basic_web_reads_without_loading():
    text = prompt()
    assert "- web_fetch runtimeAlias=vrcforge_web_fetch schema=" in text
    assert "- web_search runtimeAlias=vrcforge_web_search schema=" in text
    assert "Prefer web_fetch for reading a supplied public URL" in text
    assert internal_tool_block_for_name("vrcforge_web_fetch", "general") == "core"
    assert internal_tool_block_for_name("vrcforge_web_search", "general") == "core"


def test_basic_web_exposure_does_not_make_unity_writes_core_or_planning_visible():
    text = prompt(active=True)
    assert "- unity_set_property " not in text
    assert "- unity_write_animation_curve " not in text
    assert internal_tool_block_for_name("vrcforge_set_property", "unity") != "core"
    assert internal_tool_block_for_name("vrcforge_write_animation_curve", "unity") != "core"


def test_web_guidance_only_names_currently_eligible_tools():
    empty = SimpleNamespace(read=lambda *args, **kwargs: PlannerCatalogSnapshot())
    text = prompt(catalog=empty)
    assert "Prefer web_fetch" not in text
    assert "Use web_search" not in text


def test_runtime_shell_metadata_is_visible_without_unrelated_fields():
    info = {"available": True, "shell": "powershell", "shellRole": "fallback",
            "defaultRunner": "native-win-process", "fallbackRunner": "powershell-fallback",
            "timeoutSeconds": 120, "secretExtraField": "NEVER_SEND_321"}
    text = prompt(observe={"shellExecutor": info})
    line = text.split("Runtime Shell executor (data only): ", 1)[1].splitlines()[0]
    assert json.loads(line) == {k:v for k,v in info.items() if k != "secretExtraField"}
    assert "NEVER_SEND_321" not in text
    assert "PowerShell syntax" in text
    assert "Do not wrap" in text


@pytest.mark.parametrize("observe", [{}, {"shellExecutor": {}}, {"shellExecutor": {"available": False}}, {"shellExecutor": {"shell": "bash"}}])
def test_shell_context_never_invents_powershell_or_platform(observe):
    text = prompt(observe=observe)
    assert "PowerShell syntax" not in text
    if not observe.get("shellExecutor"):
        assert "Runtime Shell executor (data only):" not in text
    assert '"shell":"powershell"' not in text


def test_failure_guidance_does_not_treat_scope_denial_as_parameter_correction():
    text = prompt()
    assert "权限或授权范围拒绝不能靠换工具、cwd 或相对路径绕过" in text
    assert "Quick Chat 明示目标路径或切换已授权工程" in text
    assert "仅对可修正的工具或参数错误" in text


def test_animator_schema_keeps_override_and_avatar_discovery_semantics():
    service = RuntimePlannerService(catalog=app._RuntimePlannerCatalog(), desktop=object())
    text = service._build_llm_plan_prompt("Inspect current Avatar FX", [],
        internal_tool_blocks=["core", "behavior/animator_clips_bindings"], project_context_active=True)
    line = next(line for line in text.splitlines() if line.startswith("- unity_scan_fx_animator "))
    assert "overrides the avatar FX controller" in line
    assert "Scene hierarchy path of the avatar root" in line
    schema = json.loads(line.split(" schema=", 1)[1].split(": When to use:", 1)[0])
    assert "controllerPath" not in schema.get("required", [])
