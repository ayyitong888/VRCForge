from tool_usage_contract import tool_usage_description


def test_write_tool_describes_explicit_checkpoint_opt_out_only() -> None:
    description = tool_usage_description(
        "vrcforge_example_write",
        "Edit the selected Unity object.",
        write=True,
    )
    assert "only when the user explicitly said not to use checkpoints" in description
    assert "never decide this yourself" in description
    assert "exact user instruction" in description
    assert "Omit checkpointChoice to keep the current session setting" in description
    assert "For a checkpointed write, skipping loses the retained restore point" in description
    assert "new sessions default to enabled" in description
    assert "mode:'require'" in description
    assert "atomic write behavior is unchanged" in description
    assert tool_usage_description("vrcforge_example_write", description, write=True) == description


def test_read_tool_description_does_not_advertise_checkpoint_choice() -> None:
    description = tool_usage_description(
        "vrcforge_example_read",
        "Inspect the selected Unity object.",
        write=False,
    )
    assert "checkpointChoice" not in description
