from __future__ import annotations

import json

from sub_agent_tasks import SubAgentTask, SubAgentTaskRegistry


def _task_with_native_seed() -> SubAgentTask:
    return SubAgentTask(
        id="task-native-privacy",
        role="project_index_review",
        display_name="Manuka",
        task="inspect",
        params={
            "_taskSeed": {
                "taskId": "owner-task",
                "_nativeConversation": {
                    "binding": "fixture-route",
                    "turnId": "turn-native",
                    "messages": [
                        {
                            "role": "assistant",
                            "reasoning_content": "private reasoning",
                            "tool_calls": [
                                {
                                    "id": "call-1",
                                    "type": "function",
                                    "function": {
                                        "name": "fixture_write",
                                        "arguments": '{"apiKey":"raw-secret","value":"x"}',
                                    },
                                }
                            ],
                        }
                    ],
                },
            }
        },
    )


def test_public_subagent_summary_excludes_native_history_but_durable_seed_keeps_verified_copy(tmp_path):
    registry = SubAgentTaskRegistry(tmp_path, roles=[], handlers={}, reconcile_on_init=False)
    task = _task_with_native_seed()

    public = registry._serialize_task(task)
    public_text = json.dumps(public, ensure_ascii=False)
    assert "_nativeConversation" not in public_text
    assert "private reasoning" not in public_text
    assert "raw-secret" not in public_text

    durable = registry._task_snapshot(task)["params"]
    native = durable["_taskSeed"]["_nativeConversation"]
    assert native["messages"][0]["reasoning_content"] == "private reasoning"
    assert native["messages"][0]["tool_calls"][0]["function"]["arguments"] == '{"apiKey":"raw-secret","value":"x"}'
