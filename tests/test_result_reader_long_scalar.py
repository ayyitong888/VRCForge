"""P1 regression: a retained exact scalar read must retrieve its full text.

These strings fit the existing page budget. Offset/limit remain collection-item
coordinates; they must not silently become character coordinates for strings.
"""
import json

import pytest

from agent_tool_result_reader import (
    MAX_PAGE_CHARS, bind_tool_result_context, read_tool_result, result_continuation, page_next_request_arguments,
)
from runtime_planner_service import sanitize_planner_observation_text


def retained_step(description):
    step = {"index": 0, "actionId": "long-scalar-fixture", "tool": "vrcforge_tool_registry",
            "result": {"description": description}}
    step["resultRead"] = result_continuation("owner", "turn", "project", step, sanitize_planner_observation_text)
    return step


def read(step, **arguments):
    return read_tool_result({"resultRef": step["resultRead"]["resultRef"],
                             "jsonPointer": "/description", **arguments},
                            sanitize=sanitize_planner_observation_text)


@pytest.mark.parametrize("length", [501, 1200, 4500])
@pytest.mark.parametrize("limit", [1, 6, 20])
def test_exact_description_fitting_page_is_complete(length, limit):
    description = "D" * (length - len(" UNIQUE-END")) + " UNIQUE-END"
    step = retained_step(description)
    with bind_tool_result_context("owner", "turn", "project", [step]):
        page = read(step, offset=0, limit=limit)
    assert len(json.dumps(page, ensure_ascii=False, separators=(",", ":"))) <= MAX_PAGE_CHARS
    assert page["totalItems"] == 1 and page["returnedItems"] == 1
    assert page["items"][0]["jsonPointer"] == "/description"
    assert page["items"][0]["value"] == description, "exact retained read lost the description tail"
    assert page["previewTruncated"] is False
    assert page["hasMore"] is False


def test_scalar_offset_remains_item_offset_and_limit_is_validated():
    step = retained_step("D" * 1200)
    with bind_tool_result_context("owner", "turn", "project", [step]):
        end = read(step, offset=1, limit=1)
        assert end["items"] == [] and end["hasMore"] is False
        assert end["totalItems"] == 1
        for arguments in ({"offset": 2}, {"offset": -1}, {"offset": True}, {"limit": 0}, {"limit": 21}, {"limit": True}):
            with pytest.raises(ValueError):
                read(step, **arguments)


@pytest.mark.parametrize("session,turn,project", [("other", "turn", "project"), ("owner", "other", "project"), ("owner", "turn", "other")])
def test_long_scalar_never_bypasses_result_reference_scope(session, turn, project):
    step = retained_step("D" * 1200)
    with bind_tool_result_context(session, turn, project, [step]):
        with pytest.raises(PermissionError):
            read(step)


def test_long_scalar_requires_live_reference_and_keeps_private_fields_hidden():
    step = retained_step("D" * 1200)
    step["result"]["apiKey"] = "fixture-only"
    with pytest.raises(PermissionError):
        read(step)
    with bind_tool_result_context("owner", "turn", "project", [step]):
        with pytest.raises(PermissionError):
            read(step, resultRef="result_" + "0" * 32)
        with pytest.raises(PermissionError):
            read(step, jsonPointer="/apiKey")


def test_large_unicode_text_reassembles_with_explicit_text_cursor_after_redaction():
    source = ('段落😀 "quoted"\n' * 1600) + ' password=fixture-secret ' + ('尾部\t' * 2500)
    expected = sanitize_planner_observation_text(source, len(source) * 4 + 100)
    step = retained_step(source)
    chunks = []
    args = {"offset": 0, "limit": 1}
    with bind_tool_result_context("owner", "turn", "project", [step]):
        for _ in range(100):
            page = read(step, **args)
            assert len(json.dumps(page, ensure_ascii=False, separators=(",", ":"))) <= MAX_PAGE_CHARS
            assert page["offset"] == 0 and page["totalItems"] == 1
            assert page["textOffset"] == sum(map(len, chunks))
            chunks.append(page["items"][0]["value"])
            if not page["hasMore"]:
                break
            args = page_next_request_arguments(page)
            assert args == page["nextRequest"]["arguments"]
            assert args["offset"] == 0 and args["textOffset"] == sum(map(len, chunks))
        else:
            pytest.fail("text paging did not terminate")
    assert len(chunks) > 1
    assert ''.join(chunks) == expected
    assert 'fixture-secret' not in ''.join(chunks)


@pytest.mark.parametrize("arguments", [{"textOffset": -1}, {"textOffset": True}, {"textOffset": 1201}, {"textOffset": 1, "offset": 1}, {"textOffset": 0, "jsonPointer": ""}])
def test_text_cursor_rejects_invalid_or_collection_coordinates(arguments):
    step = retained_step('D' * 1200)
    with bind_tool_result_context("owner", "turn", "project", [step]):
        with pytest.raises(ValueError):
            read(step, **arguments)


def test_text_continuation_rejects_forged_progress_and_preserves_identity_rules():
    from copy import deepcopy
    step = retained_step('D' * 10000)
    with bind_tool_result_context("owner", "turn", "project", [step]):
        page = read(step)
        assert page_next_request_arguments(page)
        for change in (0, True, page['nextRequest']['arguments']['textOffset'] + 1):
            forged = deepcopy(page)
            forged['nextRequest']['arguments']['textOffset'] = change
            assert page_next_request_arguments(forged) is None
        step['result']['targetPath'] = 'C:/private/fixture.txt'
        identity = read(step, jsonPointer='/targetPath')
        assert 'value' not in identity['items'][0]
        with pytest.raises(ValueError):
            read(step, jsonPointer='/targetPath', textOffset=0)


def test_long_text_continuation_survives_planner_observation():
    from runtime_planner_service import RuntimePlannerService
    step = retained_step('D' * 10000)
    with bind_tool_result_context("owner", "turn", "project", [step]):
        page = read(step)
    observation = RuntimePlannerService._llm_loop_step_observation(None, {
        'tool': 'vrcforge_read_tool_result', 'result': page,
    })
    projected = json.loads(observation.split('; retainedResultPage=', 1)[1])
    assert projected['nextRequest']['arguments'] == page['nextRequest']['arguments']


def test_scalar_uses_existing_two_argument_sanitizer_contract():
    step = retained_step('D' * 1200)
    with bind_tool_result_context("owner", "turn", "project", [step]):
        page = read_tool_result({'resultRef': step['resultRead']['resultRef'], 'jsonPointer': '/description'},
                                sanitize=lambda value, limit: str(value)[:limit])
    assert page['items'][0]['value'] == 'D' * 1200


def test_escaped_identity_key_does_not_gain_text_chunking():
    step = retained_step('text')
    step['result']['target/Path'] = 'C:/private/fixture.txt'
    with bind_tool_result_context("owner", "turn", "project", [step]):
        with pytest.raises(ValueError):
            read(step, jsonPointer='/target~1Path', textOffset=0)
