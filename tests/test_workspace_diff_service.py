from __future__ import annotations

import ast
import subprocess
from pathlib import Path

import pytest

import dashboard_server
import workspace_diff_service as service


def test_owner_keeps_helpers_and_only_forward_dependencies() -> None:
    assert dashboard_server.run_workspace_git is service.run_workspace_git
    assert dashboard_server.parse_workspace_numstat is service.parse_workspace_numstat
    tree = ast.parse(Path(service.__file__).read_text(encoding="utf-8"))
    assert {node.module for node in ast.walk(tree) if isinstance(node, ast.ImportFrom)} == {
        "__future__", "pathlib", "typing",
    }
    assert {item.name for node in ast.walk(tree) if isinstance(node, ast.Import) for item in node.names} == {"subprocess"}


def test_numstat_preserves_binary_and_tabbed_paths() -> None:
    assert service.parse_workspace_numstat("3\t2\ta.txt\n-\t-\timage.png\n1\t0\twith\ttab.txt\ninvalid") == {
        "a.txt": {"additions": 3, "deletions": 2, "binary": False},
        "image.png": {"additions": 0, "deletions": 0, "binary": True},
        "with\ttab.txt": {"additions": 1, "deletions": 0, "binary": False},
    }


@pytest.mark.parametrize("failure,code,message", [
    (FileNotFoundError(), 127, "git executable was not found."),
    (subprocess.TimeoutExpired("git", 10), 124, "git command timed out."),
])
def test_git_failure_envelopes_and_process_scope(tmp_path, monkeypatch, failure, code, message) -> None:
    def fail(argv, **kwargs):
        assert argv == ["git", "status", "--short"]
        assert kwargs == {
            "cwd": str(tmp_path), "capture_output": True, "text": True,
            "encoding": "utf-8", "errors": "replace", "timeout": 10, "check": False,
        }
        raise failure

    monkeypatch.setattr(service.subprocess, "run", fail)
    assert service.run_workspace_git(tmp_path, ["status", "--short"]) == {
        "ok": False, "stdout": "", "stderr": message, "returncode": code,
    }


def test_missing_root_does_not_start_git(tmp_path, monkeypatch) -> None:
    monkeypatch.setattr(dashboard_server, "run_workspace_git", lambda *_a, **_k: pytest.fail("missing root must not start git"))
    result = dashboard_server.build_workspace_diff_summary(str(tmp_path / "absent"))
    assert result["status"] == "missing"
    assert result["files"] == []
    assert result["ok"] is False


def test_root_ports_remain_late_bound_and_patch_is_bounded(tmp_path, monkeypatch) -> None:
    calls = []

    def git(root, args, timeout_seconds=10):
        assert root == tmp_path
        calls.append((args, timeout_seconds))
        output = {
            "rev-parse": str(tmp_path), "status": " M image.png\n M text.txt\n",
            "branch": "topic", "--numstat": "-\t-\timage.png\n2\t1\ttext.txt\n",
            "--shortstat": "2 files changed", "--patch": "x" * 40001,
        }
        key = args[1] if args[0] == "diff" else args[0]
        return {"ok": True, "stdout": output[key], "stderr": "", "returncode": 0}

    monkeypatch.setattr(dashboard_server.runtime_paths, "ROOT_DIR", tmp_path)
    monkeypatch.setattr(dashboard_server, "run_workspace_git", git)
    result = dashboard_server.build_workspace_diff_summary(include_patch=True)
    assert result["requestedRoot"] == str(tmp_path)
    assert result["additions"] == 2 and result["deletions"] == 1
    assert result["files"][0]["binary"] is True
    assert result["patchTruncated"] is True
    assert result["patch"] == "x" * 40000 + "\n\n[diff truncated]"
    assert calls[-1] == (["diff", "--patch", "--stat", "HEAD"], 15)
    monkeypatch.setattr(dashboard_server, "parse_workspace_numstat", lambda _text: {})
    assert dashboard_server.build_workspace_diff_summary()["additions"] == 0
