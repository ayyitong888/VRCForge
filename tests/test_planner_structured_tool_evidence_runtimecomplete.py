import json

from planner_structured_tool_evidence import project_structured_tool_evidence
from runtime_planner_service import sanitize_planner_observation_text


def project(result, **kwargs):
    return project_structured_tool_evidence(
        result,
        sanitize_text=sanitize_planner_observation_text,
        **kwargs,
    )


def test_runtimecomplete_keeps_long_strings_and_nested_list_tails() -> None:
    tail = "RUNTIME_COMPLETE_TAIL"
    result = {
        "longText": r"C:\Private\avatar.json " + "x" * 20_000 + tail,
        "nested": {"rows": [["row-0"], ["row-1", {"tail": tail}]]},
        "items": [{"index": index} for index in range(20)],
    }
    long_key = "K" * 200
    result[long_key] = "key-value"

    evidence = project(result, mode="runtimecomplete", max_chars=1500)

    assert "<path redacted>" in evidence["data"]["longText"]
    assert evidence["data"]["longText"].endswith(tail)
    assert evidence["data"]["nested"]["rows"][1][1]["tail"] == tail
    assert len(evidence["data"]["items"]) == 20
    assert evidence["data"][long_key] == "key-value"
    assert evidence["truncated"] is False
    assert evidence["omittedFields"] == 0
    assert evidence["omittedItems"] == 0
    assert evidence["omittedChars"] == 0
    assert evidence["continuation"] == ""


def test_runtimecomplete_skips_one_unprojectable_list_item_and_keeps_following_tail() -> None:
    tail = "LIST_TAIL_AFTER_UNPROJECTABLE"
    evidence = project({"items": [{"safe": 1}, object(), {"tail": tail}]}, mode="runtimecomplete")

    assert evidence["data"]["items"] == [{"safe": 1}, {"tail": tail}]
    assert evidence["truncated"] is False


def test_runtimecomplete_preserves_deep_visible_tree_but_filters_secret_and_opaque_keys() -> None:
    result = {"safe": {"value": "visible"}, "rows": [{"safe": index} for index in range(12)]}
    cursor = result["safe"]
    for _ in range(10):
        cursor["child"] = {}
        cursor = cursor["child"]
    cursor["tail"] = "deep-visible"
    result["credentials"] = {"apiKey": "runtime-secret"}
    result["wire"] = {"stdout": "opaque-wire"}

    evidence = project(result, mode="runtimecomplete")
    rendered = json.dumps(evidence, ensure_ascii=False)

    assert "runtime-secret" not in rendered
    assert "opaque-wire" not in rendered
    assert "credentials" not in evidence["data"]
    assert evidence["data"]["wire"] == {}
    assert evidence["data"]["safe"]["child"]["child"]["child"]["child"]["child"]["child"]["child"]["child"]["child"]["child"]["tail"] == "deep-visible"
    assert len(evidence["data"]["rows"]) == 12
    assert evidence["redactedFields"] == 2


def test_bounded_mode_remains_the_default_and_still_honors_max_chars() -> None:
    result = {"items": [{"text": "x" * 2000} for _ in range(20)]}

    implicit = project(result, max_chars=1500)
    explicit = project(result, mode="bounded", max_chars=1500)

    assert implicit == explicit
    assert implicit["truncated"] is True
    assert len(json.dumps(implicit, ensure_ascii=False, separators=(",", ":"))) <= 1500
