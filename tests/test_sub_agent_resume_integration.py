from __future__ import annotations

import json
import threading
from contextlib import nullcontext

import pytest

from runtime_planner_service import PlannerTurnMetadata
from tests.test_native_runtime_gateway import call
from tests.test_sub_agent_runtime_integration import _child_gateway, _finish


class RoutedTurn:
    def bind(self, request):
        provider = str(request.get("provider") or "fixture-provider")
        model = str(request.get("model") or "fixture-child-model")
        return nullcontext(PlannerTurnMetadata(
            native_binding=f"{provider}:{model}", provider=provider, model=model,
        ))


def _run(gateway, tmp_path, *, task, turn_id, provider="fixture-provider", model="fixture-child-model",
         is_resume=False):
    return gateway.run_sub_agent_llm(
        task=task,
        child_session_id="resumable-child",
        provider=provider,
        model=model,
        system_prompt="Use the child history and only the allowed read tool.",
        max_steps=4,
        project_path=str(tmp_path),
        selected_materials=[],
        cancel_event=threading.Event(),
        client_turn_id=turn_id,
        is_resume=is_resume,
        allowed_tools=("vrcforge_read_text_file",),
    )


def test_same_child_resumes_two_tool_history_and_role_scope_is_applied(tmp_path):
    gateway, model, invoked = _child_gateway(tmp_path, [
        call("read-a", "vrcforge_read_text_file", {"path": "a.txt"}),
        call("read-b", "vrcforge_read_text_file", {"path": "b.txt"}),
        _finish,
        {"role": "assistant", "content": "The saved reads plus the new question support the answer."},
    ])
    gateway.runtime_planner._turn = RoutedTurn()
    gateway.register_tool("vrcforge_read_other_file", "When to use: read. When NOT to use: write.",
                          "plan/preview", lambda params: {"text": "outside role scope"})

    first = _run(gateway, tmp_path, task="Initial child question", turn_id="initial-turn")
    assert first["ok"] is True
    assert first["providerRoute"] == {"provider": "fixture-provider", "model": "fixture-child-model"}
    assert first["resumable"] is True
    assert "_resolvedSubAgentRoute" not in json.dumps(first)
    assert [item.get("path") for item in invoked] == ["a.txt", "b.txt"]
    available = json.loads(model.requests[0]["messages"][-1]["content"].split(": ", 1)[1])["availableToolNames"]
    assert "vrcforge_read_text_file" in available
    assert "vrcforge_read_other_file" not in available

    resumed = _run(gateway, tmp_path, task="Continue from the saved findings", turn_id="resume-turn",
                   is_resume=True)
    assert resumed["ok"] is True
    assert resumed["resumable"] is True
    assert resumed["providerRoute"] == first["providerRoute"]
    assert len(model.requests) == 4
    resumed_messages = json.dumps(model.requests[-1]["messages"])
    assert "Initial child question" in resumed_messages
    assert "Continue from the saved findings" in resumed_messages
    assert "fixture-body" in resumed_messages


@pytest.mark.parametrize(
    ("child_session_id", "provider", "expected_fragment"),
    [
        ("resumable-child", "changed-provider", "original child history is unavailable"),
        ("missing-child", "fixture-provider", "original child history is unavailable"),
    ],
)
def test_resume_rejects_changed_route_or_missing_history_before_provider(
    tmp_path, child_session_id, provider, expected_fragment,
):
    gateway, model, _invoked = _child_gateway(tmp_path, [_finish])
    gateway.runtime_planner._turn = RoutedTurn()
    if child_session_id == "resumable-child":
        _run(gateway, tmp_path, task="Create history", turn_id="initial-turn")
    with pytest.raises(Exception, match=expected_fragment):
        gateway.run_sub_agent_llm(
            task="Resume history", child_session_id=child_session_id,
            provider=provider, model="fixture-child-model",
            system_prompt="Continue.", max_steps=2, project_path=str(tmp_path),
            selected_materials=[], cancel_event=threading.Event(),
            client_turn_id="resume-turn", is_resume=True,
            allowed_tools=("vrcforge_read_text_file",),
        )
    expected_count = 1 if child_session_id == "resumable-child" else 0
    assert len(model.requests) == expected_count


def test_fresh_gateway_reloads_native_history_and_resumes_same_child(tmp_path):
    first_gateway, first_model, _invoked = _child_gateway(tmp_path, [
        call("read-a", "vrcforge_read_text_file", {"path": "a.txt"}),
        call("read-b", "vrcforge_read_text_file", {"path": "b.txt"}),
        _finish,
    ])
    first_gateway.runtime_planner._turn = RoutedTurn()
    initial = _run(first_gateway, tmp_path, task="Persisted child question", turn_id="initial-turn")
    assert initial["ok"] is True
    assert initial["resumable"] is True

    reopened_gateway, reopened_model, _invoked = _child_gateway(
        tmp_path, [{"role": "assistant", "content": "Continued after Gateway restart."}],
    )
    reopened_gateway.runtime_planner._turn = RoutedTurn()
    resumed = _run(reopened_gateway, tmp_path, task="Resume after Gateway restart", turn_id="resume-turn",
                   is_resume=True)
    assert resumed["ok"] is True
    assert resumed["resumable"] is True
    assert reopened_model.requests
    history = json.dumps(reopened_model.requests[0]["messages"])
    assert "Persisted child question" in history
    assert "Resume after Gateway restart" in history
    assert "fixture-body" in history
