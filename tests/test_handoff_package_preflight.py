import json
import pytest

from agent_handoff_package_preflight import native_package_preflight_evidence, native_package_preflight_record
from agent_handoff_sources import package_preflight_edits
from runtime_planner_service import native_handoff_retained_evidence


def native_plan(call_id, *, ready=True, selected="Assets/Clothing/Dark.prefab", ref="a",
                verified=True, owner_scope_matches=True, target_folder=None,
                package_path="C:/workspace/package.unitypackage"):
    arguments = {
        "packagePath": package_path,
        "projectPath": "C:/workspace/project",
        "baseAvatarName": "Avatar",
        "dependencyMode": "selected_only",
    }
    if selected is not None:
        arguments["selectedPrefab"] = selected
    if target_folder is not None:
        arguments["targetFolder"] = target_folder
    call = {"role": "assistant", "tool_calls": [{"id": call_id, "function": {
        "name": "call_tool", "arguments": json.dumps({"tool": "unity_plan_outfit_import", "arguments": arguments}),
    }}]}
    selected_ref = "result_" + ref * 32
    preflight = {"schema": "vrcforge.outfit_dependency_preflight.v1", "readyForImport": ready,
                 "blockingIssueCount": 0 if ready else 1,
                 "packageOrder": {"blockingBeforeImport": not ready, "dependencyMode": "selected_only",
                                  "selectedUnityPackage": "package.unitypackage"},
                 "compatibility": {"baseAvatarName": "Avatar", "status": "matched", "blockingBeforeImport": False}}
    data = {"schema": "vrcforge.outfit_import_plan.v1", "ok": True, "preview": True, "dependencyPreflight": preflight}
    projected = json.dumps({"authority": "untrusted_tool_output", "data": data})[:-2] + ', "inspection":{"asset'
    page = {"schema": "vrcforge.tool_result_page.v1", "source": "owner_model",
            "sourceTool": "vrcforge_plan_outfit_import", "resultRef": selected_ref,
            "textOffset": 0, "offset": 0, "jsonPointer": "/text", "hasMore": True,
            "items": [{"jsonPointer": "/text", "value": "typed result; structuredEvidence=" + projected}]}
    if not owner_scope_matches:
        page["resultRef"] = "result_" + "2" * 32
    observation = "outcomeStatus=ok"
    if verified:
        observation += f"; resultContinuation={json.dumps({'resultRef': selected_ref})}; modelInformationPage={json.dumps(page)}"
    result = {"role": "tool", "tool_call_id": call_id, "content": json.dumps({"observations": [{
        "runtimeTool": "vrcforge_plan_outfit_import", "outcome": {"status": "ok"}, "observation": observation,
    }]})}
    return [call, result]


def test_partial_owner_preview_receipt_preserves_the_exact_older_variant():
    trailing = [{"role": "assistant", "tool_calls": [{
        "id": f"later-{index}", "function": {"name": "inspect", "arguments": "{}"},
    }]} for index in range(20)]
    evidence = native_handoff_retained_evidence([*native_plan("old-plan"), *trailing])
    preflight, = package_preflight_edits(evidence)["nodes"].values()
    assert preflight["status"] == "done"
    assert preflight["packagePreflight"]["selectedPrefab"] == "Assets/Clothing/Dark.prefab"
    assert preflight["packagePreflight"]["ready"] is True
    assert preflight["evidence"] == [{"type": "receipt", "id": "old-plan"},
                                     {"type": "scan", "id": "result_" + "a" * 32}]
    assert "C:/" not in json.dumps(preflight)


def test_only_exact_scope_can_supersede_verified_plan_and_ok_alone_is_insufficient():
    evidence = native_package_preflight_evidence([
        *native_plan("ready-original"),
        *native_plan("generic", selected=None, ref="b"),
        *native_plan("same-scope-now-blocked", ready=False, ref="c"),
        *native_plan("wrong-result-ref", owner_scope_matches=False),
        *native_plan("mere-ok", verified=False),
    ])
    assert {row["callId"] for row in evidence} == {"generic", "same-scope-now-blocked"}
    claims = {node["packagePreflight"]["receiptId"]: node
              for node in package_preflight_edits(evidence)["nodes"].values()}
    blocked = claims["same-scope-now-blocked"]
    assert blocked["status"] == "done"
    assert blocked["packagePreflight"]["ready"] is False
    assert "selectedPrefab" not in claims["generic"]["packagePreflight"]


def test_production_package_preview_emits_typed_native_preflight(tmp_path):
    from outfit_import_planner import build_outfit_import_plan
    from tests.test_outfit_import_planner import make_project, make_unitypackage

    project = tmp_path / "Project"
    make_project(project)
    package = tmp_path / "package.unitypackage"
    make_unitypackage(package)
    prefab = "Assets/Outfits/Dress.prefab"
    result = build_outfit_import_plan(package, project_path=project, selected_prefab=prefab,
                                     dependency_mode="selected_only", base_avatar_name="Avatar")
    assert result["plan"]["readyToApply"]
    ref = "result_" + "d" * 32
    record = native_package_preflight_record(result, ref)
    before, after = native_plan("current-plan", selected=prefab, verified=False)
    observation = json.loads(after["content"])
    observation["observations"][0]["packageImportPreflight"] = record
    after["content"] = json.dumps(observation)
    [proof] = native_package_preflight_evidence([before, after])
    assert proof["packagePreflight"]["ready"] is True
    assert proof["packagePreflight"]["selectedPrefab"] == prefab
    assert proof["packagePreflight"]["resultRef"] == ref
    assert set(proof["packagePreflight"]).isdisjoint({"committed", "imported", "readbackVerified"})


def test_same_package_basename_and_different_destinations_do_not_share_scope():
    proofs = native_package_preflight_evidence([
        *native_plan("ready-a", target_folder="Assets/A"),
        *native_plan("blocked-b", ready=False, target_folder="Assets/B", ref="b"),
        *native_plan("other-package", package_path="C:/another/package.unitypackage", target_folder="Assets/A", ref="c"),
    ])
    assert [row["callId"] for row in proofs] == ["ready-a", "blocked-b", "other-package"]
    assert len({row["packagePreflight"]["scopeKey"] for row in proofs}) == 3


@pytest.mark.parametrize("envelope_action", [None, "reply", "shell"], ids=["bare-graph", "reply-envelope", "other-action"])
def test_compaction_keeps_finished_preflight_and_the_exact_variant_while_import_remains_pending(envelope_action):
    from agent_handoff_graph import handoff_view, parse_handoff_graph
    from context_compaction import compact_context

    rows = native_handoff_retained_evidence(native_plan("verified-plan"))
    proof_id, = package_preflight_edits(rows)["nodes"]
    user = {"id": "request-1", "source": "user", "text": "Install the outfit"}
    verified = {"type": "receipt", "id": "verified-plan"}

    def summarize(_prompt):
        edits = {"nodes": {
            proof_id: {"status": "pending", "text": "Check another variant"},
            "install": {"kind": "task", "status": "pending", "text": "Install the outfit", "evidence": [verified]},
        }, "edges": [["install", "depends_on", proof_id, {"reason": "Install the prepared selection",
                                                       "evidence": [verified]}]]}
        return edits if envelope_action is None else {"action": envelope_action, "reply": json.dumps(edits)}

    result = compact_context([{"role": "user", "text": user["text"]}], handoff=True,
                             task_inputs=[user], retained_evidence=rows, summarizer=summarize)
    graph = parse_handoff_graph(result["summary"])
    if envelope_action == "shell":
        assert result["fallbackReason"] == "schema_error"
        assert graph is None or "install" not in graph["nodes"]
        return
    assert result["providerAttempts"] == 1 and "fallbackReason" not in result
    preflight = graph["nodes"][proof_id]
    assert preflight["status"] == "done" and preflight["packagePreflight"]["ready"]
    assert preflight["packagePreflight"]["selectedPrefab"] == "Assets/Clothing/Dark.prefab"
    assert verified in preflight["evidence"] and handoff_view(graph)["readyTaskIds"] == ["install"]
