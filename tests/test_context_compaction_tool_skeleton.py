"""Compaction must not lose which tools a turn invoked."""
from context_compaction import compact_context


def test_compaction_source_entries_keep_tool_call_skeletons():
    captured = {}

    def summarizer(prompt: str) -> str:
        captured["prompt"] = prompt
        return "summary"

    history = [
        {"role": "user", "content": "导入本体并应用写入"},
        {
            "role": "assistant",
            "content": "",
            "tool_calls": [
                {
                    "id": "call_1",
                    "function": {
                        "name": "vrcforge_import_outfit_package",
                        "arguments": '{"packagePath": "E:/pkgs/milltina.unitypackage", "projectPath": "E:/unity/zcode test", "executionTarget": {"ref": "vrcforge-target-x"}}',
                    },
                }
            ],
        },
        {"role": "tool", "tool_call_id": "call_1", "content": '{"ok": true, "status": "loaded"}'},
        {"role": "assistant", "content": "导入完成。"},
    ]
    result = compact_context(history, summarizer=summarizer, trigger="manual", phase="standalone")
    entries = result["recovery"]["sourceEntries"]
    skeletons = [e for e in entries if "[tool calls:" in e["text"]]
    assert skeletons, entries
    assert "vrcforge_import_outfit_package(" in skeletons[0]["text"]
    # argument VALUES are not copied into the summary input; only keys.
    assert "packagePath" in skeletons[0]["text"]
    assert "milltina.unitypackage" not in skeletons[0]["text"]
    # the tool result text still feeds the summarizer.
    assert any(e["role"] == "tool" and '"ok": true' in e["text"] for e in entries)
