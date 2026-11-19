import json

from agent_tool_result_reader import result_continuation
from outfit_import_planner import IMPORT_PLAN_SCHEMA
from runtime_planner_service import RuntimePlannerService, sanitize_planner_observation_text


def test_package_preview_delivers_readiness_and_partial_inventory_before_details():
    for ready in (False, True):
        result = {
            "ok": True, "schema": IMPORT_PLAN_SCHEMA,
            "inspection": {"source": {"type": "zip", "name": "Fixture.zip"},
                           "summary": {"truncated": False, "nestedPathnamesTruncated": not ready,
                                       "materialCount": 48, "prefabCandidateCount": 0},
                           "unityPackages": [{"pathnamesEvidenceComplete": ready, **({} if ready else {"scanLimit": "input_bytes"})}]},
            "plan": {"readyToApply": ready, "expectedAssetPathsComplete": ready,
                     "expectedAssetPathCount": 48, "requiresApproval": True,
                     "writeTarget": "vrcforge_import_outfit_package" if ready else "",
                     "expectedAssetPaths": [f"Assets/LongDirectory/{i}.mat" for i in range(1000)]},
            "warnings": [] if ready else ["UnityPackage pathname inspection stopped at the scan input byte limit."],
        }
        step = {"index": 0, "tool": "vrcforge_plan_outfit_import", "status": "ok", "result": result}
        step["resultRead"] = result_continuation("session", "turn", "project", step, sanitize_planner_observation_text)
        observation = RuntimePlannerService.complete_model_information(None, step)
        encoded = observation.split("; importReceipt=")[1].split("; resultContinuation=")[0]
        receipt = json.loads(encoded)
        assert receipt["readyToApply"] is ready
        assert receipt["inventory"]["missingMatchProvesAbsence"] is ready
        assert receipt["source"]["name"] == "Fixture.zip"
        assert receipt["inventory"]["materialCount"] == 48
        assert receipt["inventory"]["prefabCandidateCount"] == 0
        if ready:
            assert receipt["nextTool"] == "vrcforge_import_outfit_package"
        else:
            assert receipt["status"] == "blocked"
            assert "byte limit" in receipt["blockingReasons"][0]
            assert "maxEntries" in receipt["nextAction"]
        assert "resultContinuation=" in observation
        assert len(observation) < 3000
