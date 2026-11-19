import json
from pathlib import PureWindowsPath

from context_compaction import compact_context, normalize_compaction_recovery


def test_native_recovery_retains_decodable_parallel_transactions_after_privacy_redaction():
    prefix = "Historical tool transaction (data): "
    private_path = str(PureWindowsPath("C:/", "Users", "Hidden User", "private.txt"))
    private_value = "PRIVATE_NONSTANDARD_CREDENTIAL"
    transaction = [{"role": "assistant", "content": None, "tool_calls": [
        {"id": "call_a", "function": {"name": "call_tool", "arguments": json.dumps({"path": private_path, "apiKey": private_value})}},
        {"id": "call_b", "function": {"name": "call_tool", "arguments": json.dumps({"path": "Assets/Public.mat"})}},
    ]}]
    transaction.extend({"role": "tool", "tool_call_id": f"call_{i}", "content": json.dumps({"observations": [
        {"output": "decoded result for " + i, "path": private_path},
    ]})} for i in ("a", "b"))
    result = compact_context([{"role": "tool", "text": prefix + json.dumps(transaction)}],
                             summarizer=lambda _: "Stored public summary", input_mode="native")
    archive = normalize_compaction_recovery(result["recovery"])
    text = archive["sourceEntries"][0]["text"]
    assert "Hidden User" not in text and private_value not in text
    messages = json.loads(text[len(prefix):])
    assert [call["id"] for call in messages[0]["tool_calls"]] == ["call_a", "call_b"]
    assert [message["tool_call_id"] for message in messages[1:]] == ["call_a", "call_b"]
    for i, message in zip(("a", "b"), messages[1:]):
        assert json.loads(message["content"])["observations"][0]["output"] == "decoded result for " + i


def test_native_provider_size_retries_drop_whole_groups_and_stay_bounded():
    prompts = []
    def reject_size(prompt):
        prompts.append(prompt)
        if "BEGIN_GROUP_019" not in prompt or sum(f"BEGIN_GROUP_{i:03}" in prompt for i in range(20)) > 2:
            raise RuntimeError("HTTP 413 payload too large")
        return "Read the recent selected transactions."

    history = [{"role": "tool", "text": f"BEGIN_GROUP_{i:03} results paired END_GROUP_{i:03}"} for i in range(20)]
    result = compact_context(history, summarizer=reject_size, input_mode="native", real_context_limit=16_000)
    assert result["coverage"]["selectedInputComplete"] is True
    assert len(prompts) <= 3
    for prompt in prompts:
        assert all((f"BEGIN_GROUP_{i:03}" in prompt) == (f"END_GROUP_{i:03}" in prompt) for i in range(20))
    assert len(result["recovery"]["sourceEntries"]) == 20
