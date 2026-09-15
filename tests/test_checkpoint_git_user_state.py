"""Real Git checkpoints must preserve user history, index, and working files."""
import subprocess

import pytest

from agent_gateway import AgentGateway


@pytest.mark.parametrize("state", ["dirty", "partial", "staged", "deleted", "clean", "unborn"])
def test_checkpoint_preserves_git_user_state_and_captures_worktree(tmp_path, state):
    # Current-user temporary repository; synchronous child processes, no remotes.
    repo = tmp_path / "repo"
    repo.mkdir()
    project = repo / "Unity"
    for name in ("Assets", "Packages", "ProjectSettings"):
        (project / name).mkdir(parents=True)
    asset = project / "Assets" / "fixture.txt"
    asset.write_bytes(b"baseline\n")
    deleted_asset = project / "Assets" / "deleted.txt"
    deleted_asset.write_bytes(b"delete fixture\n")
    outside = repo / "outside.txt"
    outside.write_bytes(b"outside baseline\n")

    def git(*args):
        return subprocess.run(
            ["git", "--no-optional-locks", *args], cwd=repo,
            check=True, capture_output=True, timeout=30,
        ).stdout

    git("init")
    git("add", ".")
    if state != "unborn":
        git("-c", "user.name=Fixture", "-c", "user.email=fixture@example.invalid",
            "commit", "-m", "baseline")
    outside.write_bytes(b"outside staged\n")
    git("add", "outside.txt")
    outside.write_bytes(b"outside unstaged\n")
    if state == "partial":
        asset.write_bytes(b"asset staged\n")
        git("add", "Unity/Assets/fixture.txt")
    if state != "clean":
        asset.write_bytes(b"asset current\n")
        (project / "Assets" / "new.txt").write_bytes(b"untracked\n")
    if state == "staged":
        git("add", "Unity/Assets")
    if state == "deleted":
        deleted_asset.unlink()
        git("add", "Unity/Assets/deleted.txt")

    def user_state():
        return {
            "head": (repo / ".git" / "HEAD").read_bytes(),
            "refs": git("show-ref", "--head") if state != "unborn" else b"",
            "index": (repo / ".git" / "index").read_bytes(),
            "asset": asset.read_bytes(),
            "outside": outside.read_bytes(),
        }

    before = user_state()
    gateway = AgentGateway(tmp_path / "config.json", tmp_path / "audit")
    gateway.approval_transactions.checkpoint_prepare_handler = lambda _: {"ok": True}
    checkpoint = gateway.approval_transactions._create_pre_write_checkpoint(
        {"id": "approval_git_state", "targetTool": "vrcforge_fixture_write"},
        {"projectRoot": str(project)},
    )
    assert checkpoint["ok"], checkpoint
    assert user_state() == before
    assert checkpoint["strategy"] == ("git" if state == "clean" else "archive")
    if state != "clean":
        asset.write_bytes(b"after approved write\n")
        restored = gateway.checkpoint_recovery._restore_archive_checkpoint(checkpoint)
        assert restored["ok"], restored
        assert asset.read_bytes() == before["asset"]
        assert (project / "Assets" / "new.txt").read_bytes() == b"untracked\n"
        assert deleted_asset.exists() is (state != "deleted")
        assert user_state() == before
