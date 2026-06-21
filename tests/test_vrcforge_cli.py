from __future__ import annotations

import io
import json
import urllib.request
from pathlib import Path
from typing import Any

import pytest

from tools import vrcforge_cli


class FakeClient:
    def __init__(self) -> None:
        self.calls: list[tuple[str, str, dict[str, Any] | None]] = []

    def request(self, method: str, path: str, body: dict[str, Any] | None = None) -> dict[str, Any]:
        self.calls.append((method, path, body))
        if path == "/api/app/outfit-imports/plan":
            return {"ok": True, "plan": {"readyToApply": True, "expectedAssetPaths": ["Assets/VRCForge/Imported/a.prefab"]}}
        if path == "/api/app/outfit-imports/request":
            return {"ok": True, "approval": {"id": "approval-outfit", "targetTool": "vrcforge_import_outfit_package"}}
        if path.endswith("/preview"):
            return {
                "ok": True,
                "checkpoint": {"id": "ckpt_1", "projectRoot": "E:/unity/avatar"},
                "changedFiles": [{"path": "Assets/example.asset"}],
            }
        if path.endswith("/restore"):
            return {"ok": True, "approval": {"id": "approval-restore", "targetTool": "vrcforge_restore_checkpoint"}}
        if path.endswith("/approve"):
            return {"ok": True, "execution": {"ok": True, "checkpoint": {"id": "ckpt"}}}
        if path == "/api/app/validation/report":
            return {"ok": True, "schema": "vrcforge.validation.v1", "gate": {"status": "pass"}}
        raise AssertionError(f"unexpected call: {method} {path} {body}")


def test_plan_outfit_writes_request_document(tmp_path: Path) -> None:
    out = tmp_path / "plan.json"
    stdout = io.StringIO()

    rc = vrcforge_cli.run(
        [
            "--json",
            "plan",
            "outfit",
            "E:/booth/outfit.unitypackage",
            "--project",
            "E:/unity/avatar",
            "--out",
            str(out),
        ],
        client=FakeClient(),  # type: ignore[arg-type]
        stdout=stdout,
    )

    assert rc == 0
    saved = json.loads(out.read_text(encoding="utf-8"))
    assert saved["schema"] == "vrcforge.cli_plan.v1"
    assert saved["kind"] == "outfit_import"
    assert saved["readyToApply"] is True
    assert saved["request"]["packagePath"] == "E:/booth/outfit.unitypackage"
    assert saved["request"]["projectPath"] == "E:/unity/avatar"


def test_plan_outfit_rejects_plan_output_inside_project(tmp_path: Path) -> None:
    project = tmp_path / "avatar"
    project.mkdir()

    with pytest.raises(vrcforge_cli.CliError, match="inside the selected Unity project"):
        vrcforge_cli.run(
            [
                "plan",
                "outfit",
                "E:/booth/outfit.unitypackage",
                "--project",
                str(project),
                "--out",
                str(project / "plan.json"),
            ],
            client=FakeClient(),  # type: ignore[arg-type]
            stdout=io.StringIO(),
        )


def test_apply_request_queues_approval_without_execution(tmp_path: Path) -> None:
    plan = tmp_path / "plan.json"
    plan.write_text(
        json.dumps(
            {
                "schema": "vrcforge.cli_plan.v1",
                "kind": "outfit_import",
                "readyToApply": True,
                "request": {"packagePath": "E:/booth/outfit.unitypackage", "projectPath": "E:/unity/avatar"},
                "preview": {"ok": True},
            }
        ),
        encoding="utf-8",
    )
    client = FakeClient()

    rc = vrcforge_cli.run(["apply", "--request", str(plan), "--yes"], client=client, stdout=io.StringIO())

    assert rc == 0
    assert client.calls == [
        ("POST", "/api/app/outfit-imports/request", {"packagePath": "E:/booth/outfit.unitypackage", "projectPath": "E:/unity/avatar"})
    ]


def test_rollback_execute_uses_approval_endpoint() -> None:
    client = FakeClient()

    rc = vrcforge_cli.run(["rollback", "--request", "ckpt_1", "--execute", "--yes"], client=client, stdout=io.StringIO())

    assert rc == 0
    assert client.calls[0] == ("POST", "/api/app/checkpoints/ckpt_1/preview", None)
    assert client.calls[1] == ("POST", "/api/app/checkpoints/ckpt_1/restore", None)
    assert client.calls[2] == ("POST", "/api/app/agent/approvals/approval-restore/approve", None)
    assert client.calls[3] == ("POST", "/api/app/validation/report", {"projectPath": "E:/unity/avatar"})


def test_write_json_is_windows_console_safe() -> None:
    stdout = io.StringIO()

    vrcforge_cli.write_json({"message": "ok ✅"}, stdout)

    assert "\\u2705" in stdout.getvalue()


def test_client_wraps_timeout_error(monkeypatch: pytest.MonkeyPatch) -> None:
    def raise_timeout(*args: Any, **kwargs: Any) -> None:
        raise TimeoutError("timed out")

    monkeypatch.setattr(urllib.request, "urlopen", raise_timeout)

    with pytest.raises(vrcforge_cli.CliError, match="Cannot reach VRCForge runtime"):
        vrcforge_cli.VRCForgeClient(timeout=0.01).request("GET", "/api/app/doctor")
