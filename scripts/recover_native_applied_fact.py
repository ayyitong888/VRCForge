"""Repair one offline native history from an existing applied approval summary.

Run only while its runtime is idle; this does not refresh a running process's
in-memory history. No tool execution or approval restoration is performed.
"""
from __future__ import annotations

import argparse
import copy
import hashlib
import json
import os
from pathlib import Path

from agent_native_history_store import NativeHistoryStore


def sha256(raw: bytes) -> str:
    return hashlib.sha256(raw).hexdigest()


def matching_events(path: Path, approval_id: str):
    with path.open(encoding="utf-8") as stream:
        for number, line in enumerate(stream, 1):
            event = json.loads(line)
            approval = event.get("approval", {})
            if event.get("approvalId") == approval_id or approval.get("id") == approval_id:
                yield number, event


def candidate(history: Path, audit_root: Path, approval_id: str) -> tuple[dict, dict]:
    raw = history.read_bytes()
    current = json.loads(raw)
    frozen_path = history.parent / ("approval-" + sha256(approval_id.encode()) + ".json")
    frozen = json.loads(frozen_path.read_bytes())
    if not frozen['approvalId'] == approval_id:
        raise ValueError('Recovery evidence does not match the selected history')
    if not current['sessionId'] == frozen['sessionId']:
        raise ValueError('Recovery evidence does not match the selected history')
    native, old = current["native"], frozen["native"]
    if not (native['binding'], native['turnId']) == (old['binding'], old['turnId']):
        raise ValueError('Recovery evidence does not match the selected history')
    store = NativeHistoryStore(history.parent)
    if not store._path(current['sessionId'], native['binding']).resolve() == history.resolve():
        raise ValueError('Recovery evidence does not match the selected history')
    if not native['messages'][:len(old['messages'])] == old['messages']:
        raise ValueError('Recovery evidence does not match the selected history')
    answered = {m.get("tool_call_id") for m in old["messages"] if m.get("role") == "tool"}
    pending = [c for m in old["messages"] for c in m.get("tool_calls", []) if c["id"] not in answered]
    if not pending:
        raise ValueError('Frozen approval has no dispatched pending call')
    # The approval snapshot is captured for the first pending dispatch; later
    # calls in that same receipt are still proposals, not execution evidence.
    call = pending[0]
    applied = [(n, e) for n, e in matching_events(audit_root / "approvals.jsonl", approval_id)
               if e.get("event") == "approval_applied"]
    if not len(applied) == 1:
        raise ValueError('Applied approval evidence is missing or ambiguous')
    approval_line, event = applied[0]
    approval = event["approval"]
    context = approval["taskContext"]
    function = call["function"]
    request = json.loads(function["arguments"])
    request_tool = request["tool"] if function["name"] == "call_tool" else function["name"]
    request_arguments = request["arguments"] if function["name"] == "call_tool" else request
    if json.dumps(request_arguments, sort_keys=True) != json.dumps(context.get("requestedArguments"), sort_keys=True):
        raise ValueError("Pending call arguments do not match the approved request")
    # Reuse actual dispatch pairs in this frozen conversation to resolve its
    # historical advertised name. Do not invent prefix aliases or path mappings.
    names = {}
    for message in old["messages"]:
        for prior in message.get("tool_calls", []):
            prior_function = prior["function"]
            args = json.loads(prior_function["arguments"])
            names[prior["id"]] = args.get("tool") if prior_function["name"] == "call_tool" else prior_function["name"]
    observed_owners = set()
    for message in old["messages"]:
        if message.get("role") == "tool" and names.get(message.get("tool_call_id")) == request_tool:
            for observation in json.loads(message["content"]).get("observations", []):
                if observation.get("runtimeTool"):
                    observed_owners.add(observation["runtimeTool"])
    if request_tool != approval["targetTool"] and observed_owners != {approval["targetTool"]}:
        raise ValueError("Pending call tool cannot be bound to the approved target")
    if not (context['sessionId'], context['turnId']) == (current['sessionId'], native['turnId']):
        raise ValueError('Recovery evidence does not match the selected history')
    if not context['actionId'] == context['requestedActionId']:
        raise ValueError('Recovery evidence does not match the selected history')
    if not approval['status'] == 'applied':
        raise ValueError('Recovery evidence does not match the selected history')
    runs = list(matching_events(audit_root / "runtime-runs.jsonl", approval_id))
    completed = [(n, e) for n, e in runs if e.get("event") == "approval_applied"]
    if not len(completed) == 1:
        raise ValueError('Recovery evidence does not match the selected history')
    run_line, run = completed[0]
    if not run['targetTool'] == approval['targetTool'] == context['tool']:
        raise ValueError('Recovery evidence does not match the selected history')
    if not run['resultSummary'] == approval['resultSummary']:
        raise ValueError('Recovery evidence does not match the selected history')
    summary = approval["resultSummary"]
    if not (summary.get('ok') is True and run.get('transactionStatus') == 'applied'):
        raise ValueError('Recovery evidence does not match the selected history')
    # Summarized nested objects are not reconstructed as original result data.
    facts = {k: v for k, v in summary.items() if v is None or isinstance(v, (str, bool, int, float))}
    content = {
        "status": "historical_applied_evidence",
        "authority": "untrusted_tool_output",
        "approvalId": approval_id,
        "actionId": context["actionId"],
        "runtimeTool": approval["targetTool"],
        "facts": facts,
        "evidence": {"approvalAuditLine": approval_line, "runtimeRunLine": run_line,
                     "appliedAt": approval.get("appliedAt")},
        "limitations": "Recovered from persisted approval summaries, not the complete original tool result. "
                       "This records historical execution only; current scene state and persistence were not reverified. "
                       "No tool was re-executed.",
    }
    reply = {"role": "tool", "tool_call_id": call["id"], "content": json.dumps(content, ensure_ascii=False)}
    existing = [m for m in native["messages"] if m.get("tool_call_id") == call["id"]]
    if not (not existing or existing == [reply]):
        raise ValueError('Call already has a different response; preserve it')
    result = copy.deepcopy(current)
    if not existing:
        result["native"]["messages"].append(reply)
    return result, {"status": "already_repaired" if existing else "candidate", "beforeSha256": sha256(raw),
                    "sessionId": current["sessionId"], "turnId": native["turnId"],
                    "callId": call["id"], "recovered": content}


def apply(history: Path, result: dict, report: dict, expected_sha256: str) -> dict:
    if report["status"] == "already_repaired":
        return report
    raw = history.read_bytes()
    if not sha256(raw) == expected_sha256 == report['beforeSha256']:
        raise ValueError('History changed; not writing')
    backup = history.with_name(history.name + ".before-" + expected_sha256 + ".bak")
    with backup.open("xb") as stream:
        stream.write(raw)
        stream.flush()
        os.fsync(stream.fileno())
    if not sha256(history.read_bytes()) == expected_sha256:
        raise ValueError('History changed after backup; not writing')
    NativeHistoryStore(history.parent).save(result["sessionId"], result["native"], result["selections"])
    return {**report, "status": "repaired", "backup": str(backup), "afterSha256": sha256(history.read_bytes())}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--history", type=Path, required=True)
    parser.add_argument("--audit-root", type=Path, required=True)
    parser.add_argument("--approval-id", required=True)
    parser.add_argument("--apply", action="store_true")
    parser.add_argument("--expected-sha256")
    args = parser.parse_args()
    result, report = candidate(args.history, args.audit_root, args.approval_id)
    if args.apply:
        if not args.expected_sha256:
            raise ValueError('--apply requires --expected-sha256')
        report = apply(args.history, result, report, args.expected_sha256)
    print(json.dumps(report, ensure_ascii=True, indent=2))


if __name__ == "__main__":
    main()
