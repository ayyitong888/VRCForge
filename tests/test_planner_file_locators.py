from types import SimpleNamespace

import pytest

from runtime_planner_service import PlannerCatalogSnapshot, RuntimePlannerService, planner_read_output_evidence


@pytest.mark.parametrize("tool,key", [
    ("vrcforge_list_directory", "entries"),
    ("vrcforge_find_files", "files"),
    ("vrcforge_search_text", "matches"),
])
def test_nested_unity_results_keep_complete_callable_locators(tool, key):
    root = "D:/fixture/project/Assets/VRCForgeGenerated/FinalAvatar"
    files = [root + "/Shared/Controllers/BaseFX.controller", root + "/(Gimmick)/wing_Root.controller"]
    evidence = planner_read_output_evidence(tool, {"path": root, key: [{"path": p} for p in files]})
    assert evidence["relativeTo"] == "exact_tool_call_path"
    assert evidence["source"] == root
    assert [row["source"] for row in evidence["items"]] == files


def test_general_locators_bind_to_original_call_path_without_rewriting():
    evidence = planner_read_output_evidence("vrcforge_find_files", {
        "path": "D:/private/root", "files": [{"path": "D:/private/root/(Gimmick)/file.txt"}],
    })
    assert evidence["relativeTo"] == "exact_tool_call_path"
    assert evidence["source"] == "D:/private/root"
    assert evidence["items"][0]["source"] == "D:/private/root/(Gimmick)/file.txt"


def test_file_locator_is_neither_shortened_nor_secret_redacted():
    evidence = planner_read_output_evidence("vrcforge_find_files", {
        "path": "D:/private/root", "files": [
            {"path": "D:/private/root/" + "long" * 400 + ".txt"},
            {"path": "D:/private/root/password=fixture-secret.txt"},
        ],
    })
    assert evidence["items"][0]["source"] == "D:/private/root/" + "long" * 400 + ".txt"
    assert evidence["items"][1]["source"] == "D:/private/root/password=fixture-secret.txt"
    assert not evidence["truncated"] and evidence["omittedChars"] == 0


def test_single_file_search_locator_is_same_exact_call_target():
    evidence = planner_read_output_evidence("vrcforge_search_text", {
        "path": "D:/private/file.txt", "matches": [{"path": "D:/private/file.txt", "line": 2, "text": "value"}],
    })
    assert evidence["relativeTo"] == "exact_tool_call_path"
    assert evidence["items"][0]["source"] == "D:/private/file.txt"


def test_directory_listing_complete_model_information_keeps_unicode_locators():
    root = "E:/fixture/初音のVRC小店/_VRCForge_LocalFitReview"
    planner = RuntimePlannerService.__new__(RuntimePlannerService)
    planner._catalog = SimpleNamespace(read=lambda *args, **kwargs: PlannerCatalogSnapshot())
    step = {"tool": "vrcforge_list_directory", "status": "executed", "result": {
        "path": root, "entries": [{"name": "模型1件", "path": root + "/模型1件", "type": "directory"}],
        "truncated": False, "hasMore": False, "totalCount": 1,
    }, "outcome": {"status": "ok"}}
    complete = planner.complete_model_information(step)
    assert root + "/模型1件" in complete
    assert '"relativeTo":"exact_tool_call_path"' in complete


def test_parameter_descriptions_keep_complete_callable_schema():
    from runtime_planner_service import bounded_planner_tool_schema
    schema = bounded_planner_tool_schema({"type": "object", "description": "bulk root prose", "properties": {
        f"field{i}": {"type": "string", "description": "semantic " * 100} for i in range(35)
    }})
    descriptions = [v["description"] for v in schema["properties"].values() if "description" in v]
    assert len(descriptions) == 35
    assert all(description == "semantic " * 100 for description in descriptions)
    assert schema["description"] == "bulk root prose"
