import json
from unittest.mock import patch

from runtime_planner_service import PlannerModelResult, PlannerTurnMetadata, estimate_runtime_context_tokens
from test_agent_loop_p0 import AgentLoopP0Tests
from test_runtime_planner_service import FakeDesktop, FakeTurn


class SequentialLegacyModel:
    def __init__(self):
        self.prompts = []
        self.responses = [
            {"action": "shell", "shell_command": "fixture action one", "summary": "Run first fixture action."},
            {"action": "shell", "shell_command": "fixture action two", "summary": "Run second fixture action."},
            {"action": "shell", "shell_command": "fixture action three", "summary": "Run third fixture action."},
            {"action": "reply", "reply": "All three fixture actions completed.", "completion_claim": {"satisfied": True}},
        ]

    def plan(self, prompt):
        index = len(self.prompts)
        self.prompts.append(prompt)
        prompt_tokens = estimate_runtime_context_tokens(prompt)
        response = self.responses[index]
        return PlannerModelResult(json.dumps(response), usage={"exact": False})


class DeterministicLegacyCompactor:
    def __init__(self):
        self.calls = []

    def compact(self, history, request):
        self.calls.append((tuple(dict(item) for item in history), dict(request)))
        return {
            "summary": "COMPACTED_LEGACY_PREFIX_SUMMARY",
            "entryCount": len(history),
            "retainedEntryCount": 1,
            "providerAttempts": 0,
        }


def test_legacy_runtime_compaction_offsets_prompt_only_and_preserves_full_execution_ledger():
    fixture = AgentLoopP0Tests("test_loaded_skill_policy_blocks_disallowed_tool_then_allows_real_evidence")
    fixture.setUp()
    gateway = fixture.gateway
    planner = gateway.runtime_planner
    original = (planner._model, planner._compactor, planner._turn, planner._desktop)
    model = SequentialLegacyModel()
    compactor = DeterministicLegacyCompactor()
    original_maybe_compact = planner.maybe_compact_runtime_history
    compaction_inputs = []

    def capture_compaction_state(**kwargs):
        loop_state = [dict(step) for step in kwargs.get("loop_state", [])]
        compaction_inputs.append(loop_state)
        if len(loop_state) < 2:
            return kwargs["history"], None, False
        # This integration test fixes the compaction boundary to isolate gateway
        # prompt-offset wiring; automatic threshold behavior is covered by the
        # runtime planner service tests.
        params = dict(kwargs["params"])
        params["_contextCompactionLimit"] = 32_800
        return original_maybe_compact(**{**kwargs, "params": params})

    planner._model = model
    planner._compactor = compactor
    planner._turn = FakeTurn(PlannerTurnMetadata(verified_context_limit=1_000_000))
    planner._desktop = FakeDesktop()
    planner.maybe_compact_runtime_history = capture_compaction_state
    shell_results = [
        {"ok": True, "exitCode": 0, "stdout": "FIRST_OBSERVATION_MARKER " + "a" * 10_000},
        {"ok": True, "exitCode": 0, "stdout": "LATEST_OBSERVATION_MARKER " + "b" * 10_000},
        {"ok": True, "exitCode": 0, "stdout": "APPENDED_AFTER_OFFSET_MARKER " + "c" * 200},
    ]
    history = [{"role": "user", "text": "LONG_PRIOR_HISTORY_MARKER " * 2200}]
    try:
        with patch.object(gateway.shell, "execute", side_effect=[
            {"ok": True, "status": "executed", "sessionId": f"compact-shell-{i}",
             "session": {"sessionId": f"compact-shell-{i}", "status": "finished"},
             "classification": {"risk": "low", "protectionScope": "unity_project"},
             "result": result}
            for i, result in enumerate(shell_results, 1)
        ]) as shell_execute:
            result = gateway.runtime_message({
                "message": "Run the three fixture actions and report their observations.",
                "history": history,
                "session_id": "legacy-compaction-replay-session",
                "clientTurnId": "legacy-compaction-replay-client",
                "_projectContextActive": False,
                "_contextCompactionLimit": 32_800,
            })

        assert len(compactor.calls) == 1, {
            "calls": len(compactor.calls),
            "promptTokens": [estimate_runtime_context_tokens(prompt) for prompt in model.prompts],
            "promptLengths": [len(prompt) for prompt in model.prompts],
            "contextUsage": {key: result.get("contextUsage", {}).get(key) for key in ("lastInputTokens", "lastPromptEstimatedTokens", "lastUsageExact", "exact", "requestCount", "compactionCount")},
            "commands": shell_execute.call_count,
        }
        compaction_input, compaction_request = compactor.calls[0]
        assert compaction_request["phase"] == "mid_turn"
        assert len(compaction_input) == len(history) + 1, [
            [(step.get("status"), step.get("tool")) for step in state]
            for state in compaction_inputs
        ]
        assert "LONG_PRIOR_HISTORY_MARKER" in compaction_input[0]["text"]
        assert "FIRST_OBSERVATION_MARKER" in "\n".join(item["text"] for item in compaction_input)
        assert "LATEST_OBSERVATION_MARKER" not in "\n".join(item["text"] for item in compaction_input)
        assert shell_execute.call_count == 3

        assert len(model.prompts) == 4
        third_prompt = model.prompts[2]
        fourth_prompt = model.prompts[3]
        assert "COMPACTED_LEGACY_PREFIX_SUMMARY" in third_prompt
        assert "LATEST_OBSERVATION_MARKER" in third_prompt
        assert "FIRST_OBSERVATION_MARKER" not in third_prompt
        assert "COMPACTED_LEGACY_PREFIX_SUMMARY" in fourth_prompt
        assert "LATEST_OBSERVATION_MARKER" in fourth_prompt
        assert "APPENDED_AFTER_OFFSET_MARKER" in fourth_prompt
        assert "FIRST_OBSERVATION_MARKER" not in fourth_prompt

        assert result["plan"]["reply"] == "All three fixture actions completed."
        assert result["contextCompaction"]["compactedLoopStepCount"] == 1
        assert len(result["steps"]) == 3
        assert all(step.get("status") == "executed" for step in result["steps"])
        completed = [event for event in gateway.runtime_runs.read_events(
            predicate=lambda event: event.get("event") == "runtime_turn_completed"
            and event.get("sessionId") == "legacy-compaction-replay-session"
        )]
        assert len(completed) == 1
        assert completed[0]["stepCount"] == 3
    finally:
        planner._model, planner._compactor, planner._turn, planner._desktop = original
        planner.maybe_compact_runtime_history = original_maybe_compact
        fixture.tearDown()
AgentLoopP0Tests.__test__ = False
