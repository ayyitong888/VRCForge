"""Both planner protocols must retain read-only evidence limitations."""

import pytest

from runtime_planner_service import PlannerCatalogSnapshot, RuntimePlannerService


class Catalog:
    def read(self, *args, **kwargs):
        return PlannerCatalogSnapshot()


@pytest.mark.parametrize("native", [True, False])
@pytest.mark.parametrize("project", [True, False])
def test_inspection_claims_follow_visible_evidence_scope(native, project):
    planner = RuntimePlannerService(catalog=Catalog(), desktop=None)
    kwargs = dict(observe={}, exposure_layer="planning",
                  project_context_active=project, internal_tool_blocks=["core"],
                  project_path="C:/fixture" if project else None,
                  global_instructions="", project_instructions="")
    if native:
        request, _ = planner._build_native_plan_request([], **kwargs)
        prompt = request["instructions"]
    else:
        prompt = planner._build_llm_plan_prompt("Inspect only", [], **kwargs)
    for rule in (
        "A retained result or child reference is not evidence you have read",
        "Do not recast ambiguous or analysis-required findings as normal or safe",
        "Distinguish configured behavior from observed runtime effects",
    ):
        assert (rule in prompt) is project
