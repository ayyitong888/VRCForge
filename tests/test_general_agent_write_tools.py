from pathlib import Path

import pytest

from general_agent_write_tools import (
    GENERAL_AGENT_WRITE_TOOL_NAMES,
    apply_patch,
    delete_path,
    edit_file,
    move_path,
    write_file,
    general_write_manual_approval_reason,
)


@pytest.mark.parametrize(
    "tool_name",
    ["edit_file", "delete_path", "move_path", "apply_patch"],
)
def test_existing_file_mutations_require_manual_approval_in_auto_mode(tool_name: str) -> None:
    assert general_write_manual_approval_reason(tool_name, {})


def test_only_in_project_new_file_creation_is_eligible_for_independent_auto_review(tmp_path: Path) -> None:
    project = tmp_path / "project"
    project.mkdir()
    assert general_write_manual_approval_reason(
        "write_file",
        {
            "path": str(project / "new.txt"),
            "overwrite": False,
            "_generalAllowedRoots": [str(project)],
        },
    ) == ""
    assert general_write_manual_approval_reason("write_file", {"overwrite": True})
    assert general_write_manual_approval_reason(
        "write_file",
        {
            "path": str(tmp_path / "outside.txt"),
            "overwrite": False,
            "_generalAllowedRoots": [str(project)],
        },
    )


def guard_calls():
    calls = []

    def guard(path, *, operation, capability, current_project):
        calls.append((Path(path), operation, capability, current_project))
        return True

    return calls, guard


def test_write_edit_and_guard_context(tmp_path):
    calls, guard = guard_calls()
    path = tmp_path / "note.txt"
    assert write_file(path, "one", path_guard=guard, capability="write", current_project="p")["readbackVerified"]
    assert edit_file(path, "two", path_guard=guard, capability="write", current_project="p")["readbackVerified"]
    assert path.read_text() == "two"
    assert [item[1] for item in calls] == ["write_file", "edit_file"]
    assert all(item[2:] == ("write", "p") for item in calls)


def test_rejecting_guard_prevents_mutation(tmp_path):
    path = tmp_path / "blocked.txt"
    def reject(*args, **kwargs):
        return False
    with pytest.raises(PermissionError):
        write_file(path, "nope", path_guard=reject, capability="x", current_project="p")
    assert not path.exists()


def test_move_refuses_overwrite_unless_explicit(tmp_path):
    _, guard = guard_calls()
    source, destination = tmp_path / "a.txt", tmp_path / "b.txt"
    write_file(source, "a", path_guard=guard)
    write_file(destination, "b", path_guard=guard)
    with pytest.raises(FileExistsError):
        move_path(source, destination, path_guard=guard)
    assert move_path(source, destination, path_guard=guard, overwrite=True)["readbackVerified"]
    assert destination.read_text() == "a"
    assert not source.exists()


def test_delete_only_file_or_empty_directory(tmp_path):
    _, guard = guard_calls()
    file_path = tmp_path / "x"
    write_file(file_path, "x", path_guard=guard)
    assert delete_path(file_path, path_guard=guard)["readbackVerified"]
    empty = tmp_path / "empty"
    empty.mkdir()
    delete_path(empty, path_guard=guard)
    nonempty = tmp_path / "nonempty"
    nonempty.mkdir()
    write_file(nonempty / "x", "x", path_guard=guard)
    with pytest.raises(OSError):
        delete_path(nonempty, path_guard=guard)


def test_apply_patch_valid_and_malformed_is_atomic(tmp_path):
    _, guard = guard_calls()
    path = tmp_path / "patch.txt"
    write_file(path, "a\nb\n", path_guard=guard)
    valid = "@@ -1,2 +1,2 @@\n a\n-b\n+c\n"
    assert apply_patch(path, valid, path_guard=guard)["readbackVerified"]
    assert path.read_text() == "a\nc\n"
    with pytest.raises(ValueError):
        apply_patch(path, "@@ malformed\n-bad\n+new\n", path_guard=guard)
    assert path.read_text() == "a\nc\n"


def test_write_does_not_verify_if_published_bytes_change(tmp_path, monkeypatch):
    import general_agent_write_tools as writes

    _, guard = guard_calls()
    publish = writes.os.replace

    def replace_then_change(source, destination):
        publish(source, destination)
        Path(destination).write_text("competing writer", encoding="utf-8")

    monkeypatch.setattr(writes.os, "replace", replace_then_change)
    path = tmp_path / "changed.txt"
    assert write_file(path, "expected", path_guard=guard)["readbackVerified"] is False
    assert path.read_text(encoding="utf-8") == "competing writer"


def test_registry_metadata_has_negative_use_guidance():
    assert set(GENERAL_AGENT_WRITE_TOOL_NAMES) == {
        "edit_file", "write_file", "delete_path", "move_path", "apply_patch"
    }
