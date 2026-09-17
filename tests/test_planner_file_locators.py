import json
import ntpath

from runtime_planner_service import planner_read_output_evidence


def test_nested_unity_find_results_keep_complete_project_relative_identity():
    root = "D:/fixture/project/Assets/VRCForgeGenerated/FinalAvatar"
    files = [root + "/Shared/Controllers/BaseFX.controller", root + "/(Gimmick)/wing_Root.controller"]
    evidence = planner_read_output_evidence("vrcforge_find_files", {"path": root, "files": [{"path": p} for p in files]})
    assert evidence["relativeTo"] == "path_prefix_before_Assets"
    assert evidence["source"] == "Assets/VRCForgeGenerated/FinalAvatar"
    assert [ntpath.normpath(ntpath.join("D:/fixture/project", row["source"])) for row in evidence["items"]] == [ntpath.normpath(p) for p in files]
    assert "D:/fixture" not in json.dumps(evidence)


def test_general_relative_locators_bind_to_original_call_path_without_host_disclosure():
    evidence = planner_read_output_evidence("vrcforge_find_files", {
        "path": "D:/private/root", "files": [{"path": "D:/private/root/(Gimmick)/file.txt"}],
    })
    assert evidence["relativeTo"] == "exact_tool_call_path"
    assert evidence["items"][0]["source"] == "(Gimmick)/file.txt"
    assert "D:/private" not in json.dumps(evidence)


def test_file_locator_is_omitted_instead_of_shortened_or_secret_redacted():
    evidence = planner_read_output_evidence("vrcforge_find_files", {
        "path": "D:/private/root", "files": [
            {"path": "D:/private/root/" + "long" * 400 + ".txt"},
            {"path": "D:/private/root/password=fixture-secret.txt"},
        ],
    })
    assert all(not row.get("source") for row in evidence["items"])
    assert "fixture-secret" not in json.dumps(evidence)
    assert evidence["truncated"] and evidence["omittedChars"] > 0


def test_single_file_search_locator_is_same_exact_call_target():
    evidence = planner_read_output_evidence("vrcforge_search_text", {
        "path": "D:/private/file.txt", "matches": [{"path": "D:/private/file.txt", "line": 2, "text": "value"}],
    })
    assert evidence["relativeTo"] == "exact_tool_call_path"
    assert evidence["items"][0]["source"] == "."


def test_parameter_descriptions_have_per_tool_count_and_text_bounds():
    from runtime_planner_service import bounded_planner_tool_schema
    schema = bounded_planner_tool_schema({"type": "object", "description": "bulk root prose", "properties": {
        f"field{i}": {"type": "string", "description": "semantic " * 100} for i in range(35)
    }})
    descriptions = [v["description"] for v in schema["properties"].values() if "description" in v]
    assert len(descriptions) == 24
    assert max(map(len, descriptions)) <= 240
    assert "description" not in schema
