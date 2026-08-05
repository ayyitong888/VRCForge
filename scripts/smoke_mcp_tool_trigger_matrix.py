"""Offline MCP tool-selection acceptance harness; it never executes a tool."""
from __future__ import annotations

import argparse
import hashlib
import importlib
import inspect
import json
import os
import sys
import urllib.error
import urllib.parse
import urllib.request
from collections.abc import Callable, Mapping, Sequence
from pathlib import Path
from typing import Any


ROOT = Path(__file__).resolve().parents[1]
DEFAULT_MATRIX = ROOT / "tests" / "fixtures" / "mcp_tool_trigger_matrix.json"
Planner = Callable[..., Any]
ReceiptVerifier = Callable[..., bool]


def load_matrix(path: Path) -> dict[str, Any]:
    value = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(value, dict) or value.get("schema") != "vrcforge.mcp_tool_trigger_matrix.v1":
        raise ValueError("matrix schema must be vrcforge.mcp_tool_trigger_matrix.v1")
    cases = value.get("cases")
    if not isinstance(cases, list):
        raise ValueError("matrix cases must be a list")
    positives = [case for case in cases if isinstance(case, dict) and case.get("kind") == "positive"]
    negatives = [case for case in cases if isinstance(case, dict) and case.get("kind") == "negative"]
    if len(positives) < 20 or len(negatives) < 20:
        raise ValueError("matrix requires at least 20 positive and 20 negative cases")
    ids = [str(case.get("id") or "") for case in cases if isinstance(case, dict)]
    if len(ids) != len(cases) or any(not item for item in ids) or len(set(ids)) != len(ids):
        raise ValueError("matrix case ids must be present and unique")
    if any(not isinstance(case.get("expectedTool"), str) or not case["expectedTool"] for case in positives):
        raise ValueError("every positive case requires expectedTool")
    if any(case.get("expectedTools") != [] for case in negatives):
        raise ValueError("every negative case requires expectedTools=[]")
    for case in cases:
        layer = str(case.get("exposureLayer") or "planning")
        if layer not in {"planning", "execution"}:
            raise ValueError("every matrix case exposureLayer must be planning or execution")
        case["exposureLayer"] = layer
    return value


def normalize_visible_tools(value: Sequence[Mapping[str, Any]] | None, matrix: Mapping[str, Any]) -> list[dict[str, Any]]:
    if value is None:
        tool_layers = {
            str(case["expectedTool"]): str(case.get("exposureLayer") or "planning")
            for case in matrix["cases"]
            if case.get("kind") == "positive"
        }
        return [
            {
                "name": name,
                "description": (
                    f"When to use: Acceptance fixture for {name}.\n"
                    "When NOT to use: Do not use for unrelated or no-tool requests.\n"
                    f"Negative example: Mention {name} without asking to inspect or change the project."
                ),
                "_meta": {"exposureLayer": tool_layers[name]},
                "annotations": {"readOnlyHint": tool_layers[name] == "planning"},
            }
            for name in sorted(tool_layers)
        ]
    tools: list[dict[str, Any]] = []
    for item in value:
        name = str(item.get("name") or "").strip()
        if not name:
            raise ValueError("visible tools require non-empty names")
        tools.append({str(key): item[key] for key in sorted(item)})
    return sorted(tools, key=lambda item: str(item["name"]))


def visible_tools_hash(tools: Sequence[Mapping[str, Any]]) -> str:
    canonical = json.dumps(list(tools), ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


def production_tool_snapshot_valid(tools: Sequence[Mapping[str, Any]]) -> bool:
    from unity_mcp_tool_contract import EXPECTED_TOOL_NAMES

    names = {str(item.get("name") or "") for item in tools}
    return (
        len(tools) == len(EXPECTED_TOOL_NAMES) == 64
        and names == set(EXPECTED_TOOL_NAMES)
        and all(
            bool(str(item.get("description") or "").strip())
            and all(
                section in str(item.get("description") or "")
                for section in ("When to use:", "When NOT to use:", "Negative example:")
            )
            and isinstance(item.get("inputSchema"), Mapping)
            and isinstance(item.get("_meta"), Mapping)
            and bool(str(item["_meta"].get("whenToUse") or "").strip())
            and bool(str(item["_meta"].get("doNotUse") or "").strip())
            for item in tools
        )
    )


def extract_tool_calls(plan: Any) -> list[str]:
    if isinstance(plan, str):
        return [plan]
    if isinstance(plan, Sequence) and not isinstance(plan, (str, bytes, bytearray)):
        return [str(item.get("name") if isinstance(item, Mapping) else item) for item in plan]
    if isinstance(plan, Mapping):
        for key in ("toolCalls", "tool_calls", "tools", "calls"):
            value = plan.get(key)
            if isinstance(value, Sequence) and not isinstance(value, (str, bytes, bytearray)):
                return [str(item.get("name") if isinstance(item, Mapping) else item) for item in value]
        if "name" in plan:
            return [str(plan["name"])]
    return []


def json_safe(value: Any) -> Any:
    try:
        json.dumps(value, ensure_ascii=False)
        return value
    except (TypeError, ValueError):
        return repr(value)


def run_matrix(
    matrix: Mapping[str, Any],
    planner: Planner,
    *,
    visible_tools: Sequence[Mapping[str, Any]] | None = None,
    planner_source: str = "injected",
    receipt_verifier: ReceiptVerifier | None = None,
    trusted_receipt_source: bool = False,
    require_production_tools: bool = False,
) -> dict[str, Any]:
    tools = normalize_visible_tools(visible_tools, matrix)
    tools_hash = visible_tools_hash(tools)
    results: list[dict[str, Any]] = []
    for case in matrix["cases"]:
        from mcp_trigger_selection import tools_for_exposure_layer

        prompt = str(case["prompt"])
        exposure_layer = str(case.get("exposureLayer") or "planning")
        case_tools = tools_for_exposure_layer(tools, exposure_layer)
        if len(inspect.signature(planner).parameters) >= 3:
            raw_plan = planner(prompt, list(case_tools), exposure_layer)
        else:
            raw_plan = planner(prompt, list(case_tools))
        actual = extract_tool_calls(raw_plan)
        evidence_ok = False
        if receipt_verifier is not None and isinstance(raw_plan, Mapping):
            try:
                if len(inspect.signature(receipt_verifier).parameters) >= 4:
                    evidence_ok = receipt_verifier(prompt, list(case_tools), raw_plan, exposure_layer) is True
                else:
                    evidence_ok = receipt_verifier(prompt, list(case_tools), raw_plan) is True
            except Exception:
                evidence_ok = False
        if case["kind"] == "negative":
            expected: list[str] = []
            passed = actual == expected
        else:
            expected = [str(case["expectedTool"])]
            passed = actual == expected
        results.append(
            {
                "id": case["id"],
                "kind": case["kind"],
                "prompt": prompt,
                "exposureLayer": exposure_layer,
                "visibleToolCount": len(case_tools),
                "expectedTools": expected,
                "actualTools": actual,
                "passed": passed,
                "providerEvidenceValid": evidence_ok,
                "rawPlan": json_safe(raw_plan),
            }
        )
    positive = [item for item in results if item["kind"] == "positive"]
    negative = [item for item in results if item["kind"] == "negative"]
    positive_rate = sum(bool(item["passed"]) for item in positive) / len(positive)
    negatives_zero = all(bool(item["passed"]) for item in negative)
    threshold = float(matrix.get("positiveThreshold", 0.95))
    passed = negatives_zero and positive_rate >= threshold
    evidence_valid = all(bool(item["providerEvidenceValid"]) for item in results)
    production_snapshot_valid = production_tool_snapshot_valid(tools)
    accepted = (
        evidence_valid
        and passed
        and trusted_receipt_source
        and (production_snapshot_valid or not require_production_tools)
    )
    if not passed:
        not_accepted_reason = "trigger accuracy thresholds were not met"
    elif not evidence_valid or not trusted_receipt_source:
        not_accepted_reason = "one-use Dashboard provider receipts were missing or invalid"
    elif require_production_tools and not production_snapshot_valid:
        not_accepted_reason = "the visible tool snapshot was not the exact 64-tool Dashboard/Core contract"
    else:
        not_accepted_reason = ""
    return {
        "schema": "vrcforge.mcp_tool_trigger_report.v1",
        "plannerSource": planner_source,
        "selectionOnly": True,
        "toolsExecuted": False,
        "visibleToolsHash": tools_hash,
        "visibleToolCount": len(tools),
        "productionToolSnapshotValid": production_snapshot_valid,
        "positiveThreshold": threshold,
        "positiveCount": len(positive),
        "negativeCount": len(negative),
        "positiveCorrectCount": sum(bool(item["passed"]) for item in positive),
        "positiveCorrectRate": positive_rate,
        "negativeZeroCallCount": sum(bool(item["passed"]) for item in negative),
        "negativeZeroCalls": negatives_zero,
        "passed": passed,
        "providerEvidenceValid": evidence_valid,
        "trustedReceiptSource": trusted_receipt_source,
        "accepted": accepted,
        "notAcceptedReason": not_accepted_reason,
        "cases": results,
    }


def import_planner(spec: str) -> Planner:
    module_name, separator, attribute = spec.partition(":")
    if not separator or not module_name or not attribute:
        raise ValueError("--planner must be module:function")
    planner = getattr(importlib.import_module(module_name), attribute)
    if not callable(planner):
        raise ValueError("--planner target must be callable")
    return planner


def load_unity_tools(project_path: Path) -> list[dict[str, Any]]:
    from unity_mcp_core_client import UnityMcpCoreClient

    return UnityMcpCoreClient(project_path.resolve()).list_tools(exposure_layer="execution")


def app_backend_provider(base_url: str, session_token: str) -> tuple[Planner, ReceiptVerifier, str]:
    parsed = urllib.parse.urlsplit(str(base_url or "").strip())
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
    token = str(session_token or "").strip()
    if not token:
        raise ValueError("the App backend session token environment variable is empty")
    origin = urllib.parse.urlunsplit((parsed.scheme, parsed.netloc, "", "", "")).rstrip("/")

    def post(path: str, payload: Mapping[str, Any]) -> dict[str, Any]:
        body = json.dumps(dict(payload), ensure_ascii=False, separators=(",", ":")).encode("utf-8")
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
            with urllib.request.urlopen(request, timeout=180) as response:
                raw = response.read(2 * 1024 * 1024 + 1)
        except urllib.error.HTTPError as exc:
            raise RuntimeError(f"App backend selection request failed with HTTP {exc.code}") from exc
        if len(raw) > 2 * 1024 * 1024:
            raise RuntimeError("App backend selection response exceeded 2 MiB")
        value = json.loads(raw.decode("utf-8"))
        if not isinstance(value, dict):
            raise RuntimeError("App backend selection response must be a JSON object")
        return value

    def planner(message: str, visible_tools: list[dict[str, Any]], exposure_layer: str) -> dict[str, Any]:
        return post(
            "/api/app/provider/mcp-selection",
            {"message": message, "visibleTools": visible_tools, "exposureLayer": exposure_layer},
        )

    def verifier(
        message: str,
        visible_tools: list[dict[str, Any]],
        result: Mapping[str, Any],
        exposure_layer: str,
    ) -> bool:
        response = post(
            "/api/app/provider/mcp-selection/verify",
            {
                "message": message,
                "visibleTools": visible_tools,
                "exposureLayer": exposure_layer,
                "result": dict(result),
            },
        )
        return response.get("schema") == "vrcforge.mcp_selection_verification.v1" and response.get("accepted") is True

    return planner, verifier, origin


def main() -> int:
    parser = argparse.ArgumentParser(description="Evaluate MCP tool selection without executing tools.")
    parser.add_argument("--matrix", type=Path, default=DEFAULT_MATRIX)
    tools_group = parser.add_mutually_exclusive_group()
    tools_group.add_argument("--tools-json", type=Path, help="tools/list JSON array or object containing tools")
    tools_group.add_argument("--unity-project", type=Path, help="read a fresh tools/list from this Unity project's Core")
    planner_group = parser.add_mutually_exclusive_group()
    planner_group.add_argument("--planner", help="offline planner import in module:function form; never release-accepted")
    planner_group.add_argument("--app-backend-url", help="authenticated running App backend loopback origin")
    parser.add_argument(
        "--app-session-token-env",
        default="VRCFORGE_ACCEPTANCE_APP_SESSION_TOKEN",
        help="environment variable containing the App backend bearer token",
    )
    parser.add_argument("--output", type=Path)
    args = parser.parse_args()
    matrix = load_matrix(args.matrix)
    if not args.planner and not args.app_backend_url:
        report = {"schema": "vrcforge.mcp_tool_trigger_report.v1", "accepted": False, "error": "no planner configured; not accepted"}
        print(json.dumps(report, ensure_ascii=False, indent=2))
        return 2
    tools = None
    if args.tools_json:
        payload = json.loads(args.tools_json.read_text(encoding="utf-8"))
        tools = payload.get("tools") if isinstance(payload, dict) else payload
        if not isinstance(tools, list):
            raise ValueError("--tools-json must contain a tools array")
    elif args.unity_project:
        tools = load_unity_tools(args.unity_project)
    if args.app_backend_url:
        if tools is None:
            raise ValueError("--app-backend-url requires --tools-json or --unity-project for a fresh tools/list response")
        planner, verifier, backend_origin = app_backend_provider(
            args.app_backend_url,
            os.environ.get(args.app_session_token_env, ""),
        )
        report = run_matrix(
            matrix,
            planner,
            visible_tools=tools,
            planner_source=f"app-backend:{backend_origin}",
            receipt_verifier=verifier,
            trusted_receipt_source=True,
            require_production_tools=True,
        )
    else:
        report = run_matrix(
            matrix,
            import_planner(args.planner),
            visible_tools=tools,
            planner_source=args.planner,
        )
    text = json.dumps(report, ensure_ascii=False, indent=2)
    if args.output:
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(text + "\n", encoding="utf-8")
    print(text)
    return 0 if report["accepted"] else 1


if __name__ == "__main__":
    sys.exit(main())
