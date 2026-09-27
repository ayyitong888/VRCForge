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


def test_loaded_instruction_tail_reaches_prompt_without_the_old_32000_char_cut(tmp_path) -> None:
    content = "A" * 31_990 + "\nTAIL_RULE_MUST_REACH_THE_MODEL"
    (tmp_path / "AGENTS.md").write_text(content, encoding="utf-8")

    snapshot = load_project_instructions(tmp_path)
    block = project_instruction_prompt_block(snapshot.content)

    assert snapshot.status == "loaded"
    assert snapshot.content.endswith("TAIL_RULE_MUST_REACH_THE_MODEL")
    assert block.endswith("TAIL_RULE_MUST_REACH_THE_MODEL\n</project_instructions>")


def test_global_prompt_builder_keeps_full_content_over_project_loader_limit() -> None:
    content = "x" * (MAX_PROJECT_INSTRUCTIONS_BYTES + 1) + "\nGLOBAL_TAIL_MUST_REACH_THE_MODEL"

    block = global_instruction_prompt_block(content)

    assert block.endswith("GLOBAL_TAIL_MUST_REACH_THE_MODEL\n</global_user_instructions>")
