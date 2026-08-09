from __future__ import annotations

import argparse
from dataclasses import dataclass
import json
import os
from pathlib import Path
import sys
import urllib.parse
import urllib.request


ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from agent_harness import evaluate_agent_harness, load_agent_harness_matrix
from runtime_planner_service import PlannerCatalogSnapshot, PlannerTool, RuntimePlannerService
from smoke_mcp_tool_trigger_matrix import app_backend_provider


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
    parser.add_argument("--app-backend-url", help="authenticated running App backend loopback origin")
    parser.add_argument(
        "--app-session-token-env",
        default="VRCFORGE_ACCEPTANCE_APP_SESSION_TOKEN",
        help="environment variable containing the App backend bearer token",
    )
    arguments = parser.parse_args()

    matrix = load_agent_harness_matrix(arguments.matrix)
    if arguments.app_backend_url:
        token = str(os.environ.get(arguments.app_session_token_env, "")).strip()
        if not token:
            raise ValueError("the App backend session token environment variable is empty")
        parsed = urllib.parse.urlsplit(str(arguments.app_backend_url).strip())
        if (
            parsed.scheme != "http"
            or parsed.hostname not in {"127.0.0.1", "localhost", "::1"}
            or parsed.username
            or parsed.password
            or parsed.query
            or parsed.fragment
            or parsed.path not in {"", "/"}
            or parsed.port is None
        ):
            raise ValueError("--app-backend-url must be an explicit loopback http origin with a port")
        origin = urllib.parse.urlunsplit((parsed.scheme, parsed.netloc, "", "", "")).rstrip("/")
        request = urllib.request.Request(
            origin + "/api/app/tools/registry?exposure_layer=planning",
            headers={"Authorization": f"Bearer {token}"},
        )
        with urllib.request.urlopen(request, timeout=30) as response:
            registry_payload = json.loads(response.read(4 * 1024 * 1024 + 1).decode("utf-8"))
        registry_rows = registry_payload.get("tools") if isinstance(registry_payload, dict) else None
        if not isinstance(registry_rows, list) or not registry_rows:
            raise RuntimeError("App tool registry did not return a non-empty tools array")
        visible_tools = [
            {
                "name": str(item.get("name") or ""),
                "description": str(item.get("description") or ""),
                "inputSchema": item.get("inputsSchema") or {"type": "object"},
                "_meta": {"exposureLayer": "planning"},
                "annotations": {"readOnlyHint": True},
            }
            for item in registry_rows
            if isinstance(item, dict) and str(item.get("name") or "").strip()
        ]
        provider_planner, provider_verifier, _ = app_backend_provider(origin, token)

        def select_tool(prompt: str, exposure_layer: str) -> dict[str, object]:
            return provider_planner(prompt, visible_tools, exposure_layer)

        def verify_selection(
            prompt: str,
            result: dict[str, object],
            exposure_layer: str,
        ) -> bool:
            return provider_verifier(prompt, visible_tools, result, exposure_layer)

        report = evaluate_agent_harness(
            matrix,
            select_tool=select_tool,
            verify_selection=verify_selection,
            selection_source=f"app-backend:{origin}",
            trusted_selection_receipts=True,
        )
    else:
        planner = _planner_for_matrix(matrix)

        def select_tool(prompt: str, _exposure_layer: str) -> str:
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
    required_acceptance = "releaseAccepted" if arguments.app_backend_url else "accepted"
    return 0 if report[required_acceptance] else 1


if __name__ == "__main__":
    raise SystemExit(main())
