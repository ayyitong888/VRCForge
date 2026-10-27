"""Keep confirmed renderer-selection corrections as small native history pairs."""
from __future__ import annotations

import copy
import json


def retained_renderer_corrections(messages: list[dict]) -> list[dict]:
    calls = {}
    actions = {}
    retained = {}
    for message in messages:
        if message.get("role") == "assistant":
            for call in message.get("tool_calls", []) or []:
                try:
                    request = json.loads(call["function"]["arguments"])
                except (KeyError, TypeError, ValueError):
                    continue
                arguments = request.get("arguments") if isinstance(request, dict) else None
                if not isinstance(arguments, dict):
                    continue
                paths = arguments.get("rendererPaths")
                if (not isinstance(paths, list) or not paths
                        or any(not isinstance(path, str) or not path for path in paths)
                        or not all(isinstance(arguments.get(key), str) and arguments[key]
                                   for key in ("projectPath", "avatarPath"))):
                    continue
                calls[call["id"]] = (call, request)
        elif message.get("role") == "tool" and message.get("tool_call_id") in calls:
            call, request = calls[message["tool_call_id"]]
            try:
                receipt = json.loads(message["content"])
            except (KeyError, TypeError, ValueError):
                continue
            if not isinstance(receipt, dict):
                continue
            for observation in receipt.get("observations", []):
                if not isinstance(observation, dict) or not observation.get("actionId"):
                    continue
                outcome = observation.get("outcome") or {}
                if not isinstance(outcome, dict):
                    continue
                status = outcome.get("status")
                detail = str(observation.get("observation") or "")
                unknown_paths = "Unknown rendererPaths in selected avatar:"
                failure_detail = detail[detail.index(unknown_paths):].split(";", 1)[0] if unknown_paths in detail else ""
                pair = [
                    {"role": "assistant", "content": None, "tool_calls": [copy.deepcopy(call)]},
                    {"role": "tool", "tool_call_id": call["id"], "content": json.dumps({
                        "observations": [{"actionId": observation["actionId"],
                                          "runtimeTool": observation.get("runtimeTool"),
                                          "outcome": {"status": status},
                                          **({"observation": failure_detail} if failure_detail else {})}]}, ensure_ascii=False)},
                ]
                previous = actions.get(request.get("correction_for_action_id"))
                if status == "ok" and request.get("action") == "correct" and previous:
                    prior_request, prior_status, prior_pair, prior_failure = previous
                    arguments = request["arguments"]
                    prior_arguments = prior_request["arguments"]
                    if (prior_status == "failed" and prior_failure and request.get("tool") == prior_request.get("tool")
                            and all(arguments[key] == prior_arguments[key] for key in ("projectPath", "avatarPath"))
                            and arguments["rendererPaths"] != prior_arguments["rendererPaths"]):
                        # These are historical selection sets, never path aliases or authorization.
                        retained[prior_pair[1]["tool_call_id"]] = prior_pair
                        retained[call["id"]] = pair
                actions[observation["actionId"]] = (request, status, pair, failure_detail)
    return [message for pair in retained.values() for message in pair]
