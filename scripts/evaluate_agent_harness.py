from __future__ import annotations

import argparse
from dataclasses import dataclass
import json
import os
from pathlib import Path
import sys
import time
import urllib.parse
import urllib.request


ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))
SCRIPT_DIR = Path(__file__).resolve().parent
if str(SCRIPT_DIR) not in sys.path:
    sys.path.insert(0, str(SCRIPT_DIR))

from agent_harness import evaluate_agent_harness, load_agent_harness_matrix
from agent_task_loop import canonical_task_id
from runtime_planner_service import PlannerCatalogSnapshot, PlannerTool, RuntimePlannerService
from smoke_mcp_tool_trigger_matrix import app_backend_provider


DEFAULT_MATRIX = ROOT / "tests" / "fixtures" / "agent_harness_matrix.json"


@dataclass(frozen=True)
class _HarnessCatalog:
    planning: PlannerCatalogSnapshot
    execution: PlannerCatalogSnapshot

    def read(self, exposure_layer: str) -> PlannerCatalogSnapshot:
        return self.execution if exposure_layer == "execution" else self.planning


class _HarnessDesktop:
    @staticmethod
    def summarize_action_result(_value: object) -> str:
        return "desktop-result"


def _planner_for_matrix(matrix: dict[str, object]) -> RuntimePlannerService:
    action_kinds: dict[str, str] = {}
    names: set[str] = set()
    for case in matrix["selectionCases"]:
        name = str(case["expectedTool"]).strip()
        action_kind = str(case["expectedActionKind"])
        if name:
            names.add(name)
            previous = action_kinds.setdefault(name, action_kind)
            if previous != action_kind:
                raise ValueError(f"Harness tool {name} has conflicting action kinds")
        for item in case.get("forbiddenTools", []):
            names.add(str(item))
    for name in names:
        action_kinds.setdefault(name, "skill")
    tools = tuple(
        PlannerTool(
            name=name,
            description=f"Harness selection contract for {name}.",
            category="supervised-write" if action_kind == "write" else "read/debug",
            write=action_kind == "write",
        )
        for name, action_kind in sorted(action_kinds.items())
    )
    planning_tools = tuple(tool for tool in tools if not tool.write)
    return RuntimePlannerService(
        catalog=_HarnessCatalog(
            planning=PlannerCatalogSnapshot(
                visible_tools=planning_tools,
                routable_tools=tools,
            ),
            execution=PlannerCatalogSnapshot(visible_tools=tools, routable_tools=tools),
        ),
        desktop=_HarnessDesktop(),
    )


def main() -> int:
    parser = argparse.ArgumentParser(
        description=(
            "Evaluate VRCForge selection and completion gates. Tools execute only when an "
            "explicit --journey-request is supplied to an authenticated App backend."
        )
    )
    parser.add_argument("--matrix", type=Path, default=DEFAULT_MATRIX)
    parser.add_argument("--output", type=Path)
    parser.add_argument("--app-backend-url", help="authenticated running App backend loopback origin")
    parser.add_argument(
        "--app-session-token-env",
        default="VRCFORGE_ACCEPTANCE_APP_SESSION_TOKEN",
        help="environment variable containing the App backend bearer token",
    )
    journey_source = parser.add_mutually_exclusive_group()
    journey_source.add_argument(
        "--journey-request",
        type=Path,
        help=(
            "JSON body for one explicit real /api/app/agent runtime task. Required for "
            "releaseAccepted; may execute tools and therefore remains subject to product permissions."
        ),
    )
    journey_source.add_argument(
        "--journey-receipt",
        type=Path,
        help="JSON receipt captured from one already-terminal App Runtime continuation.",
    )
    parser.add_argument(
        "--journey-wait-seconds",
        type=int,
        default=300,
        help="bounded wait for an approval/background journey to reach a terminal continuation",
    )
    arguments = parser.parse_args()

    matrix = load_agent_harness_matrix(arguments.matrix)
    if arguments.journey_request is not None and not arguments.app_backend_url:
        raise ValueError("--journey-request requires --app-backend-url")
    if arguments.journey_receipt is not None and not arguments.app_backend_url:
        raise ValueError("--journey-receipt requires --app-backend-url")
    if not 1 <= int(arguments.journey_wait_seconds) <= 1800:
        raise ValueError("--journey-wait-seconds must be between 1 and 1800")
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
            origin + "/api/app/tools/registry?exposure_layer=execution",
            headers={"Authorization": f"Bearer {token}"},
        )
        with urllib.request.urlopen(request, timeout=30) as response:
            registry_payload = json.loads(response.read(4 * 1024 * 1024 + 1).decode("utf-8"))
        registry_rows = registry_payload.get("tools") if isinstance(registry_payload, dict) else None
        if not isinstance(registry_rows, list) or not registry_rows:
            raise RuntimeError("App tool registry did not return a non-empty tools array")
        visible_tools = []
        for item in registry_rows:
            if not isinstance(item, dict) or not str(item.get("name") or "").strip():
                continue
            write = bool(item.get("requiresApproval"))
            visible_tools.append(
                {
                    "name": str(item.get("name") or ""),
                    "description": str(item.get("description") or ""),
                    "inputSchema": item.get("inputsSchema") or {"type": "object"},
                    "_meta": {
                        "exposureLayer": "execution" if write else "planning",
                        "actionKind": "write" if write else "skill",
                    },
                    "annotations": {"readOnlyHint": not write},
                }
            )
        provider_planner, provider_verifier, _ = app_backend_provider(origin, token)

        runtime_receipts: list[dict[str, object]] = []
        if arguments.journey_receipt is not None:
            loaded_receipt = json.loads(
                arguments.journey_receipt.read_text(encoding="utf-8")
            )
            if not isinstance(loaded_receipt, dict):
                raise ValueError("--journey-receipt must contain one JSON object")
            receipt = (
                loaded_receipt.get("harnessJourneyReceipt")
                or loaded_receipt.get("receipt")
                or loaded_receipt
            )
            if not isinstance(receipt, dict):
                raise ValueError("--journey-receipt does not contain a receipt object")
            runtime_receipts.append(dict(receipt))
        if arguments.journey_request is not None:
            journey_request = json.loads(
                arguments.journey_request.read_text(encoding="utf-8")
            )
            if not isinstance(journey_request, dict):
                raise ValueError("--journey-request must contain one JSON object")
            session_id = str(
                journey_request.get("session_id")
                or journey_request.get("sessionId")
                or ""
            ).strip()
            client_turn_id = str(
                journey_request.get("clientTurnId")
                or journey_request.get("client_turn_id")
                or ""
            ).strip()
            message = str(journey_request.get("message") or "").strip()
            task_id = (
                canonical_task_id(session_id, client_turn_id, message)
                if session_id and client_turn_id and message
                else ""
            )
            excluded_receipt_ids = (
                _matching_runtime_receipt_ids(
                    _get_app_json(
                        origin,
                        token,
                        "/api/app/bootstrap?deferAgentCatalog=true",
                        timeout=30,
                    ),
                    session_id=session_id,
                    task_id=task_id,
                )
                if task_id
                else set()
            )
            issued = _post_app_json(
                origin,
                token,
                "/api/app/agent/harness/journey",
                journey_request,
                timeout=300,
                accepted_http_statuses={200, 409},
            )
            receipt = issued.get("receipt")
            if not isinstance(receipt, dict):
                if not task_id:
                    raise RuntimeError(
                        "An asynchronous journey requires explicit session_id, clientTurnId, and message identity."
                    )
                receipt = _wait_for_runtime_receipt(
                    origin,
                    token,
                    session_id=session_id,
                    task_id=task_id,
                    timeout_seconds=int(arguments.journey_wait_seconds),
                    excluded_receipt_ids=excluded_receipt_ids,
                )
            runtime_receipts.append(receipt)

        def verify_runtime_journey(receipt: dict[str, object]) -> dict[str, object]:
            response = _post_app_json(
                origin,
                token,
                "/api/app/agent/harness/journey/verify",
                {"receipt": dict(receipt)},
                timeout=30,
            )
            journey = response.get("journey")
            return dict(journey) if isinstance(journey, dict) else {}

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
            runtime_journeys=runtime_receipts,
            verify_runtime_journey=verify_runtime_journey,
            trusted_runtime_journey_receipts=bool(runtime_receipts),
        )
    else:
        planner = _planner_for_matrix(matrix)

        def select_tool(prompt: str, exposure_layer: str) -> dict[str, object]:
            plan = planner.plan_agent_turn(
                prompt,
                {},
                {},
                exposure_layer=exposure_layer,
            )
            if plan.get("nextStep") == "call_skill":
                return {
                    "toolCalls": [str(plan.get("skillTool") or "")],
                    "actionKind": "skill",
                }
            if plan.get("nextStep") == "request_write":
                return {
                    "toolCalls": [str(plan.get("writeTool") or "")],
                    "actionKind": "write",
                }
            return {"toolCalls": [], "actionKind": "none"}

        report = evaluate_agent_harness(matrix, select_tool=select_tool)
    rendered = json.dumps(report, ensure_ascii=False, indent=2) + "\n"
    if arguments.output is not None:
        arguments.output.parent.mkdir(parents=True, exist_ok=True)
        arguments.output.write_text(rendered, encoding="utf-8")
    else:
        print(rendered, end="")
    required_acceptance = "releaseAccepted" if arguments.app_backend_url else "accepted"
    return 0 if report[required_acceptance] else 1


def _post_app_json(
    origin: str,
    token: str,
    path: str,
    payload: dict[str, object],
    *,
    timeout: int,
    accepted_http_statuses: set[int] | None = None,
) -> dict[str, object]:
    body = json.dumps(payload, ensure_ascii=False, separators=(",", ":")).encode("utf-8")
    request = urllib.request.Request(
        origin + path,
        data=body,
        method="POST",
        headers={
            "Authorization": f"Bearer {token}",
            "Content-Type": "application/json; charset=utf-8",
        },
    )
    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:
            raw = response.read(2 * 1024 * 1024 + 1)
    except urllib.error.HTTPError as exc:
        if exc.code not in (accepted_http_statuses or set()):
            raise RuntimeError(f"App Harness request failed with HTTP {exc.code}") from exc
        raw = exc.read(2 * 1024 * 1024 + 1)
    if len(raw) > 2 * 1024 * 1024:
        raise RuntimeError("App Harness response exceeded 2 MiB")
    value = json.loads(raw.decode("utf-8"))
    if not isinstance(value, dict):
        raise RuntimeError("App Harness response must be a JSON object")
    return value


def _get_app_json(
    origin: str,
    token: str,
    path: str,
    *,
    timeout: float,
) -> dict[str, object]:
    request = urllib.request.Request(
        origin + path,
        headers={"Authorization": f"Bearer {token}"},
    )
    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:
            raw = response.read(2 * 1024 * 1024 + 1)
    except urllib.error.HTTPError as exc:
        raise RuntimeError(f"App Harness request failed with HTTP {exc.code}") from exc
    if len(raw) > 2 * 1024 * 1024:
        raise RuntimeError("App Harness response exceeded 2 MiB")
    value = json.loads(raw.decode("utf-8"))
    if not isinstance(value, dict):
        raise RuntimeError("App Harness response must be a JSON object")
    return value


def _find_runtime_receipt(
    payload: dict[str, object],
    *,
    session_id: str,
    task_id: str,
    excluded_receipt_ids: set[str] | None = None,
) -> dict[str, object] | None:
    continuations = payload.get("runtimeContinuations")
    if not isinstance(continuations, list):
        return None
    for item in reversed(continuations):
        if not isinstance(item, dict) or str(item.get("sessionId") or "") != session_id:
            continue
        plan = item.get("plan")
        completion = plan.get("taskCompletion") if isinstance(plan, dict) else None
        if not isinstance(completion, dict) or str(completion.get("taskId") or "") != task_id:
            continue
        receipt = item.get("harnessJourneyReceipt")
        receipt_id = str(receipt.get("receiptId") or "") if isinstance(receipt, dict) else ""
        expires_at_ms = receipt.get("expiresAtMs") if isinstance(receipt, dict) else None
        if (
            isinstance(receipt, dict)
            and receipt_id
            and receipt_id not in (excluded_receipt_ids or set())
            and isinstance(expires_at_ms, int)
            and not isinstance(expires_at_ms, bool)
            and expires_at_ms > int(time.time() * 1000)
        ):
            return dict(receipt)
    return None


def _matching_runtime_receipt_ids(
    payload: dict[str, object],
    *,
    session_id: str,
    task_id: str,
) -> set[str]:
    continuations = payload.get("runtimeContinuations")
    if not isinstance(continuations, list):
        return set()
    receipt_ids: set[str] = set()
    for item in continuations:
        if not isinstance(item, dict) or str(item.get("sessionId") or "") != session_id:
            continue
        plan = item.get("plan")
        completion = plan.get("taskCompletion") if isinstance(plan, dict) else None
        if not isinstance(completion, dict) or str(completion.get("taskId") or "") != task_id:
            continue
        receipt = item.get("harnessJourneyReceipt")
        receipt_id = str(receipt.get("receiptId") or "") if isinstance(receipt, dict) else ""
        if receipt_id:
            receipt_ids.add(receipt_id)
    return receipt_ids


def _wait_for_runtime_receipt(
    origin: str,
    token: str,
    *,
    session_id: str,
    task_id: str,
    timeout_seconds: int,
    excluded_receipt_ids: set[str] | None = None,
) -> dict[str, object]:
    deadline = time.monotonic() + timeout_seconds
    while True:
        remaining = deadline - time.monotonic()
        if remaining <= 0:
            raise RuntimeError(
                "The App Runtime journey did not produce a terminal authenticated receipt before the timeout."
            )
        receipt = _find_runtime_receipt(
            _get_app_json(
                origin,
                token,
                "/api/app/bootstrap?deferAgentCatalog=true",
                timeout=max(0.1, min(30.0, remaining)),
            ),
            session_id=session_id,
            task_id=task_id,
            excluded_receipt_ids=excluded_receipt_ids,
        )
        if receipt is not None:
            return receipt
        remaining = deadline - time.monotonic()
        if remaining <= 0:
            raise RuntimeError(
                "The App Runtime journey did not produce a terminal authenticated receipt before the timeout."
            )
        time.sleep(min(1.0, remaining))


if __name__ == "__main__":
    raise SystemExit(main())
