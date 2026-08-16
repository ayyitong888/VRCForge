from __future__ import annotations

from project_instruction_context import (
    MAX_PROJECT_INSTRUCTIONS_BYTES,
    load_project_instructions,
    global_instruction_prompt_block,
    project_instruction_prompt_block,
)


def test_loads_only_bounded_root_agents_file(tmp_path) -> None:
    (tmp_path / "AGENTS.md").write_text("- Inspect before editing.\n", encoding="utf-8")

    snapshot = load_project_instructions(tmp_path)

    assert snapshot.status == "loaded"
    assert snapshot.content == "- Inspect before editing."


def test_missing_invalid_and_oversized_project_instructions_fail_closed(tmp_path) -> None:
    assert load_project_instructions(tmp_path).status == "missing"

    (tmp_path / "AGENTS.md").write_bytes(b"\xff\xfe\xfa")
    assert load_project_instructions(tmp_path).status == "unreadable"

    (tmp_path / "AGENTS.md").write_bytes(b"x" * (MAX_PROJECT_INSTRUCTIONS_BYTES + 1))
    assert load_project_instructions(tmp_path).status == "too_large"


def test_prompt_block_keeps_project_rules_below_runtime_and_current_user_intent() -> None:
    block = project_instruction_prompt_block("- Read AGENTS.md first.")

    assert "lower priority than Runtime safety" in block
    assert "user's current request" in block
    assert "never authorize a write" in block
    assert "<project_instructions>" in block

    global_block = global_instruction_prompt_block("- Reply concisely.")
    assert "Global user instructions" in global_block
    assert "never authorize a write" in global_block
