"""Prompt construction reuses one scoped snapshot, never a global catalog cache."""
from __future__ import annotations

import copy
from dataclasses import dataclass, field

import pytest

from runtime_planner_service import (
    PlannerCatalogSnapshot,
    PlannerSkill,
    PlannerTool,
    RuntimePlannerService,
)


@dataclass
class CountingCatalog:
    planning: PlannerCatalogSnapshot
    execution: PlannerCatalogSnapshot
    reads: list[tuple[str, bool]] = field(default_factory=list)

    def read(self, layer: str, *, project_context_active: bool = True):
        self.reads.append((layer, project_context_active))
        return self.execution if layer == "execution" else self.planning


class IndependentContextPlanner(RuntimePlannerService):
    """Model the former independent context read, for byte-for-byte parity."""

    def _message_with_runtime_context(self, message, observe, **kwargs):
        kwargs.pop("catalog", None)
        return super()._message_with_runtime_context(message, observe, **kwargs)


def _catalog():
    reader = PlannerTool("read_installed_skill", "Read a guide.", "read",
                         runtime_name="vrcforge_read_installed_skill", block="core")
    read = PlannerTool("read_asset", "Read an asset.", "read", block="core",
                       input_schema={"type": "object", "properties": {"path": {"type": "string"}}})
    write = PlannerTool("write_asset", "Write an asset.", "write", write=True, block="core")
    guide = PlannerSkill("asset-guide", title="Asset Guide", source="user", skill_type="package",
                         description="Inspect the selected asset.")
    hidden = PlannerSkill("hidden-guide", source="user", skill_type="package", enabled=False)
    return CountingCatalog(
        PlannerCatalogSnapshot(visible_tools=(reader, read), routable_tools=(reader, read, write),
                               skills=(guide, hidden)),
        PlannerCatalogSnapshot(visible_tools=(reader, read, write), routable_tools=(reader, read, write),
                               skills=(guide, hidden)),
    )


OBSERVE = {
    "turn": {"attachments": [{"name": "note", "text": "Exact attachment fact."}]},
    "memory": {"items": [{"scope": "user", "kind": "fact", "text": "Exact memory fact."}]},
    "goals": {"items": [{"status": "active", "title": "Inspect asset", "summary": "Read only."}]},
}


@pytest.mark.parametrize("active", [True, False])
@pytest.mark.parametrize("layer", ["planning", "execution"])
def test_legacy_prompt_uses_one_snapshot_and_preserves_context_and_schemas(active, layer):
    catalog = _catalog()
    planner = RuntimePlannerService(catalog=catalog, desktop=object())
    reference_catalog = _catalog()
    reference = IndependentContextPlanner(catalog=reference_catalog, desktop=object())
    arguments = dict(observe=copy.deepcopy(OBSERVE), exposure_layer=layer, project_context_active=active)

    prompt = planner._build_llm_plan_prompt("Inspect", [], **arguments)
    expected = reference._build_llm_plan_prompt("Inspect", [], **arguments)

    assert catalog.reads == [(layer, active)]
    assert reference_catalog.reads == [(layer, active), (layer, active)]
    assert prompt == expected
    assert "asset-guide" in prompt and "hidden-guide" not in prompt
    assert "Exact attachment fact." in prompt and "Exact memory fact." in prompt
    assert "- read_asset schema=" in prompt
    assert ("- write_asset" in prompt) is (layer == "execution")


@pytest.mark.parametrize("active", [True, False])
@pytest.mark.parametrize("layer", ["planning", "execution"])
def test_native_snapshot_reuse_preserves_fixed_prefix_tools_and_history(active, layer):
    catalog = _catalog()
    planner = RuntimePlannerService(catalog=catalog, desktop=object())
    reference = IndependentContextPlanner(catalog=_catalog(), desktop=object())
    messages = [{"role": "user", "content": "Exact request\n  with spaces"}]
    original_messages = copy.deepcopy(messages)
    arguments = dict(observe=copy.deepcopy(OBSERVE), exposure_layer=layer, project_context_active=active,
                     project_path=None, internal_tool_blocks=None, global_instructions="",
                     project_instructions="")

    request, _ = planner._build_native_plan_request(messages, **arguments)
    expected, _ = reference._build_native_plan_request(messages, **arguments)

    assert catalog.reads == ([("execution", active), ("planning", active)] if layer == "execution"
                             else [("planning", active)])
    assert request == expected
    assert messages == original_messages and request["messages"][0] == original_messages[0]
    assert "write_asset" not in request["instructions"]
    assert len(request["tools"]) == 4


def test_catalog_is_fresh_for_each_prompt_and_profile():
    catalog = _catalog()
    planner = RuntimePlannerService(catalog=catalog, desktop=object())
    first = planner._build_llm_plan_prompt("Inspect", [], project_context_active=False)
    catalog.planning = PlannerCatalogSnapshot(
        visible_tools=(PlannerTool("new_read", "Read new data.", "read", block="core"),),
        skills=(PlannerSkill("new-guide", source="user", skill_type="package"),),
    )
    second = planner._build_llm_plan_prompt("Inspect", [], project_context_active=True)

    assert catalog.reads == [("planning", False), ("planning", True)]
    assert "asset-guide" in first and "new-guide" not in first
    assert "new-guide" in second and "asset-guide" not in second
    assert "- new_read" in second and "- read_asset" not in second


def test_standalone_context_still_reads_its_own_snapshot():
    catalog = _catalog()
    planner = RuntimePlannerService(catalog=catalog, desktop=object())

    context = planner.complete_runtime_context_information(copy.deepcopy(OBSERVE), project_context_active=False)

    assert catalog.reads == [("planning", False)]
    assert "asset-guide" in context and "Exact attachment fact." in context


@pytest.mark.parametrize("active", [True, False])
def test_connection_guidance_uses_the_visible_readiness_tool_before_generic_reads(active):
    readiness = PlannerTool("custom_readiness", "Inspect readiness.", "read", block="core",
                            runtime_name="vrcforge_know_yourself")
    snapshot = PlannerCatalogSnapshot(visible_tools=(readiness,))
    planner = RuntimePlannerService(catalog=CountingCatalog(snapshot, snapshot), desktop=object())

    prompt = planner._build_llm_plan_prompt("Connection failed", [], project_context_active=active)

    assert "call custom_readiness before filesystem, Shell or repair tools" in prompt
    assert "先遵循上面的只读 readiness 路径" in prompt
    assert "After a successful report, answer from that report" in prompt
    assert "Ordinary Internet, GitHub and unrelated network support are not readiness triggers" in prompt
    assert "Do not run a mandatory readiness preflight for every ordinary Unity read" in prompt


@pytest.mark.parametrize("readiness", [
    None,
    PlannerTool("custom_readiness", "Inspect readiness.", "read", block="diagnostics",
                runtime_name="vrcforge_know_yourself"),
    PlannerTool("custom_readiness", "Mutate readiness.", "write", write=True, block="core",
                runtime_name="vrcforge_know_yourself"),
])
def test_connection_guidance_never_invents_or_exposes_an_unavailable_readiness_tool(readiness):
    snapshot = PlannerCatalogSnapshot(visible_tools=(readiness,) if readiness else ())
    planner = RuntimePlannerService(catalog=CountingCatalog(snapshot, snapshot), desktop=object())

    prompt = planner._build_llm_plan_prompt("Inspect", [], internal_tool_blocks=["core"])

    assert "Readiness and connection diagnosis:" not in prompt
    assert "call custom_readiness before" not in prompt
    assert "先遵循上面的只读 readiness 路径" not in prompt
