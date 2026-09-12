"""Prompt contract for the core-only catalogue observed in a real failed turn.

These tests do not model or claim successful model tool selection.
"""
from runtime_planner_service import PlannerCatalogSnapshot, PlannerTool, RuntimePlannerService


CORE_NAMES = (
    "ask_user", "create_goal", "delegate_subagent", "get_goal",
    "list_internal_tool_blocks", "load_internal_tool_block", "progress_create",
    "progress_delete", "progress_list", "progress_replace", "progress_update",
    "unload_internal_tool_block", "update_goal",
)


class Catalog:
    def read(self, _layer, *, project_context_active=True):
        return PlannerCatalogSnapshot(visible_tools=tuple(
            PlannerTool(name=name, description="Core tool", category="core") for name in CORE_NAMES
        ))


class Desktop:
    def summarize_action_result(self, result):
        return str(result)


def prompt(message):
    return RuntimePlannerService(catalog=Catalog(), desktop=Desktop())._build_llm_plan_prompt(
        message, [], project_context_active=True, internal_tool_blocks=["core"],
    )


def test_core_only_prompt_does_not_direct_hidden_diagnostics_or_shell_log_reading():
    value = prompt("VRCForge runtime startup timed out. Please inspect retained startup logs.")
    assert "choose know_yourself before filesystem" not in value
    assert "系统级问题，如看日志/查工程外文件/git" not in value
    assert "先发现并加载相应的只读诊断工具块" in value
    assert "不可直接调用未列出的工具" in value


def test_diagnostics_guidance_keeps_actual_core_catalogue_and_explicit_shell_action():
    value = prompt("Use Shell to run git status in the external repository.")
    section = value.split("可用工具列表：\n", 1)[1].split("\n\n最近对话：", 1)[0]
    names = tuple(line[2:].split(" schema=", 1)[0].split(":", 1)[0].strip()
                  for line in section.splitlines() if line.startswith("- "))
    assert names == CORE_NAMES
    assert '"action": "shell"' in value
    assert '"shell_params": {"cwd": "<可选目录>"}' in value
    assert "shell_command" in value
