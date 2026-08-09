from __future__ import annotations

import argparse
from dataclasses import dataclass
import json
from pathlib import Path
import sys


ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from agent_harness import evaluate_agent_harness, load_agent_harness_matrix
from runtime_planner_service import PlannerCatalogSnapshot, PlannerTool, RuntimePlannerService


DEFAULT_MATRIX = ROOT / "tests" / "fixtures" / "agent_harness_matrix.json"


@dataclass(frozen=True)
class _HarnessCatalog:
    snapshot: PlannerCatalogSnapshot

    def read(self, _exposure_layer: str) -> PlannerCatalogSnapshot:
        return self.snapshot


class _HarnessDesktop:
    @staticmethod
    def summarize_action_result(_value: object) -> str:
        return "desktop-result"


def _planner_for_matrix(matrix: dict[str, object]) -> RuntimePlannerService:
    names: set[str] = set()
    for case in matrix["selectionCases"]:
        names.add(str(case["expectedTool"]))
        names.update(str(item) for item in case.get("forbiddenTools", []))
    tools = tuple(
        PlannerTool(
            name=name,
            description=f"Harness selection contract for {name}.",
            category="read/debug",
        )
        for name in sorted(names)
    )
    snapshot = PlannerCatalogSnapshot(visible_tools=tools, routable_tools=tools)
    return RuntimePlannerService(catalog=_HarnessCatalog(snapshot), desktop=_HarnessDesktop())


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Evaluate VRCForge runtime tool selection and completion without executing tools."
    )
    parser.add_argument("--matrix", type=Path, default=DEFAULT_MATRIX)
    parser.add_argument("--output", type=Path)
    arguments = parser.parse_args()

    matrix = load_agent_harness_matrix(arguments.matrix)
    planner = _planner_for_matrix(matrix)

    def select_tool(prompt: str) -> str:
        plan = planner.plan_agent_turn(prompt, {}, {})
        if plan.get("nextStep") != "call_skill":
            return ""
        return str(plan.get("skillTool") or "")

    report = evaluate_agent_harness(matrix, select_tool=select_tool)
    rendered = json.dumps(report, ensure_ascii=False, indent=2) + "\n"
    if arguments.output is not None:
        arguments.output.parent.mkdir(parents=True, exist_ok=True)
        arguments.output.write_text(rendered, encoding="utf-8")
    else:
        print(rendered, end="")
    return 0 if report["accepted"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
