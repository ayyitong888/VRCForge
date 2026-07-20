from __future__ import annotations

import io
import json
import runpy
import urllib.parse
import urllib.request
import zipfile
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


def _doctor_report(checks: list[dict[str, Any]]) -> dict[str, Any]:
    normalized_checks = [
        {
            "id": str(check["id"]),
            "status": str(check["status"]),
            "fixable": bool(check.get("fixable", False)),
        }
        for check in checks
    ]
    return {
        "ok": not any(check["status"] == "error" for check in normalized_checks),
        "schema": "vrcforge.doctor.v1",
        "summary": {
            "okCount": sum(check["status"] == "ok" for check in normalized_checks),
            "warningCount": sum(check["status"] == "warning" for check in normalized_checks),
            "errorCount": sum(check["status"] == "error" for check in normalized_checks),
            "unknownCount": sum(check["status"] == "unknown" for check in normalized_checks),
        },
        "checks": normalized_checks,
    }


class DoctorClient:
    def __init__(
        self,
        reports: list[dict[str, Any]],
        *,
        fix_results: dict[str, Any] | None = None,
        failures: dict[str, str] | None = None,
    ) -> None:
        self.reports = list(reports)
        self.fix_results = fix_results or {}
        self.failures = failures or {}
        self.calls: list[tuple[str, str, dict[str, Any] | None]] = []

    def request(self, method: str, path: str, body: dict[str, Any] | None = None) -> dict[str, Any]:
        self.calls.append((method, path, body))
        failure = self.failures.get(f"{method} {path}")
        if failure:
            raise vrcforge_cli.CliError(failure)
        if method == "GET" and path == "/api/app/doctor":
            if not self.reports:
                raise AssertionError("unexpected extra Doctor report request")
            return self.reports.pop(0)
        if method == "POST" and path.startswith("/api/app/doctor/fix/"):
            return self.fix_results.get(
                path,
                {
                    "ok": True,
                    "schema": "vrcforge.doctor_fix.v1",
                    "checkId": urllib.parse.unquote(path.rsplit("/", 1)[-1]),
                    "mode": str((body or {}).get("mode") or "safe"),
                    "status": "repaired",
                    "changed": True,
                    "phases": [],
                },
            )
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


@pytest.mark.parametrize(
    ("status", "expected_rc"),
    [("ok", 0), ("warning", 1), ("unknown", 1), ("error", 2)],
)
def test_doctor_no_fix_wraps_report_and_uses_summary_exit_code(status: str, expected_rc: int) -> None:
    report = _doctor_report([{"id": "desktop.runtime", "status": status}])
    client = DoctorClient([report])
    stdout = io.StringIO()

    rc = vrcforge_cli.run(["doctor"], client=client, stdout=stdout)  # type: ignore[arg-type]

    assert rc == expected_rc
    payload = json.loads(stdout.getvalue())
    assert payload["schema"] == "vrcforge.cli-doctor.v1"
    assert payload["fixRequested"] is False
    assert payload["initialReport"] == report
    assert payload["report"] == report
    assert payload["summary"] == report["summary"]
    assert payload["exitCode"] == expected_rc
    assert payload["ok"] is (expected_rc == 0)
    assert client.calls == [("GET", "/api/app/doctor", None)]


def test_doctor_fix_posts_only_fixable_non_ok_checks_in_report_order_and_rechecks() -> None:
    initial = _doctor_report(
        [
            {"id": "skills.bad/package", "status": "warning", "fixable": True},
            {"id": "doctor.already_ok", "status": "ok", "fixable": True},
            {"id": "security.warn_only", "status": "error", "fixable": False},
            {"id": "doctor.second id", "status": "unknown", "fixable": True},
        ]
    )
    final = _doctor_report([{"id": "desktop.runtime", "status": "ok"}])
    client = DoctorClient([initial, final])
    stdout = io.StringIO()

    rc = vrcforge_cli.run(
        ["doctor", "--fix", "--yes", "--json"],
        client=client,  # type: ignore[arg-type]
        stdout=stdout,
    )

    assert rc == 0
    assert client.calls == [
        ("GET", "/api/app/doctor", None),
        ("POST", "/api/app/doctor/fix/skills.bad%2Fpackage", {"mode": "safe"}),
        ("POST", "/api/app/doctor/fix/doctor.second%20id", {"mode": "safe"}),
        ("GET", "/api/app/doctor", None),
    ]
    payload = json.loads(stdout.getvalue())
    assert payload["report"] == final
    assert [fix["checkId"] for fix in payload["fixes"]] == ["skills.bad/package", "doctor.second id"]
    assert all(fix["result"]["schema"] == "vrcforge.doctor_fix.v1" for fix in payload["fixes"])


def test_doctor_force_uses_force_mode_but_final_report_still_controls_exit() -> None:
    initial = _doctor_report([{"id": "checkpoint.backend", "status": "error", "fixable": True}])
    final = _doctor_report([{"id": "security.posture", "status": "warning", "fixable": False}])
    client = DoctorClient([initial, final])
    stdout = io.StringIO()

    rc = vrcforge_cli.run(
        ["doctor", "--fix", "--force", "--yes"],
        client=client,  # type: ignore[arg-type]
        stdout=stdout,
    )

    assert rc == 1
    assert client.calls == [
        ("GET", "/api/app/doctor", None),
        ("POST", "/api/app/doctor/fix/checkpoint.backend", {"mode": "force"}),
        ("GET", "/api/app/doctor", None),
    ]
    assert all("/approvals/" not in path for _, path, _ in client.calls)
    payload = json.loads(stdout.getvalue())
    assert payload["mode"] == "force"
    assert payload["exitCode"] == 1


@pytest.mark.parametrize(
    "arguments",
    [
        ["doctor", "--fix", "--json"],
        ["--json", "doctor", "--fix"],
        ["doctor", "--force"],
    ],
)
def test_doctor_argument_errors_are_structured_and_do_not_call_runtime(arguments: list[str]) -> None:
    client = DoctorClient([])
    stdout = io.StringIO()

    rc = vrcforge_cli.run(arguments, client=client, stdout=stdout, stdin=io.StringIO("FIX\n"))  # type: ignore[arg-type]

    assert rc == 2
    payload = json.loads(stdout.getvalue())
    assert payload["schema"] == "vrcforge.cli-doctor.v1"
    assert payload["error"]["code"] in {"confirmation_required", "invalid_arguments"}
    assert payload["exitCode"] == 2
    assert client.calls == []


@pytest.mark.parametrize("arguments", [["--json", "doctor"], ["doctor", "--json"]])
def test_doctor_accepts_json_before_or_after_subcommand(arguments: list[str]) -> None:
    client = DoctorClient([_doctor_report([{"id": "desktop.runtime", "status": "ok"}])])
    stdout = io.StringIO()

    rc = vrcforge_cli.run(arguments, client=client, stdout=stdout)  # type: ignore[arg-type]

    assert rc == 0
    assert json.loads(stdout.getvalue())["schema"] == "vrcforge.cli-doctor.v1"


def test_doctor_non_json_fix_prompts_once_before_contacting_runtime() -> None:
    report = _doctor_report([{"id": "desktop.runtime", "status": "ok"}])
    client = DoctorClient([report, report])
    stdout = io.StringIO()

    rc = vrcforge_cli.run(
        ["doctor", "--fix"],
        client=client,  # type: ignore[arg-type]
        stdin=io.StringIO("FIX\n"),
        stdout=stdout,
    )

    assert rc == 0
    assert stdout.getvalue().count("Type FIX to continue") == 1
    assert client.calls == [
        ("GET", "/api/app/doctor", None),
        ("GET", "/api/app/doctor", None),
    ]


@pytest.mark.parametrize(
    ("client", "error_code"),
    [
        (DoctorClient([], failures={"GET /api/app/doctor": "offline"}), "transport_error"),
        (DoctorClient([{"schema": "unexpected"}]), "schema_error"),
    ],
)
def test_doctor_transport_or_schema_failure_returns_two(client: DoctorClient, error_code: str) -> None:
    stdout = io.StringIO()

    rc = vrcforge_cli.run(["doctor", "--json"], client=client, stdout=stdout)  # type: ignore[arg-type]

    assert rc == 2
    payload = json.loads(stdout.getvalue())
    assert payload["error"]["code"] == error_code
    assert payload["exitCode"] == 2


def test_doctor_rejects_summary_counts_that_disagree_with_checks() -> None:
    report = _doctor_report([{"id": "runtime.bad", "status": "error"}])
    report["summary"]["errorCount"] = 0
    report["summary"]["okCount"] = 1
    client = DoctorClient([report])
    stdout = io.StringIO()

    rc = vrcforge_cli.run(["doctor", "--json"], client=client, stdout=stdout)  # type: ignore[arg-type]

    assert rc == 2
    assert json.loads(stdout.getvalue())["error"]["code"] == "schema_error"


def test_doctor_fix_transport_failure_returns_two_without_continuing() -> None:
    initial = _doctor_report([{"id": "checkpoint.backend", "status": "error", "fixable": True}])
    path = "/api/app/doctor/fix/checkpoint.backend"
    client = DoctorClient([initial], failures={f"POST {path}": "repair unavailable"})
    stdout = io.StringIO()

    rc = vrcforge_cli.run(
        ["doctor", "--fix", "--yes", "--json"],
        client=client,  # type: ignore[arg-type]
        stdout=stdout,
    )

    assert rc == 2
    payload = json.loads(stdout.getvalue())
    assert payload["error"]["code"] == "fix_transport_error"
    assert payload["fixes"] == []
    assert client.calls == [
        ("GET", "/api/app/doctor", None),
        ("POST", path, {"mode": "safe"}),
    ]


def test_doctor_rejects_non_object_fix_response_without_rechecking() -> None:
    initial = _doctor_report([{"id": "skills.registry", "status": "warning", "fixable": True}])
    path = "/api/app/doctor/fix/skills.registry"
    client = DoctorClient([initial], fix_results={path: ["not", "an", "object"]})
    stdout = io.StringIO()

    rc = vrcforge_cli.run(
        ["doctor", "--fix", "--yes", "--json"],
        client=client,  # type: ignore[arg-type]
        stdout=stdout,
    )

    assert rc == 2
    payload = json.loads(stdout.getvalue())
    assert payload["error"]["code"] == "fix_schema_error"
    assert payload["fixes"] == []
    assert client.calls == [
        ("GET", "/api/app/doctor", None),
        ("POST", path, {"mode": "safe"}),
    ]


def test_doctor_rejects_fix_response_with_mismatched_identity() -> None:
    initial = _doctor_report([{"id": "skills.registry", "status": "warning", "fixable": True}])
    path = "/api/app/doctor/fix/skills.registry"
    client = DoctorClient(
        [initial],
        fix_results={
            path: {
                "ok": True,
                "schema": "vrcforge.doctor_fix.v1",
                "checkId": "other.check",
                "mode": "safe",
                "status": "repaired",
                "changed": True,
                "phases": [],
            }
        },
    )
    stdout = io.StringIO()

    rc = vrcforge_cli.run(["doctor", "--fix", "--yes", "--json"], client=client, stdout=stdout)  # type: ignore[arg-type]

    assert rc == 2
    assert json.loads(stdout.getvalue())["error"]["code"] == "fix_schema_error"
    assert client.calls == [
        ("GET", "/api/app/doctor", None),
        ("POST", path, {"mode": "safe"}),
    ]


def test_skill_init_generates_valid_one_tool_source_and_smoke(tmp_path: Path) -> None:
    source = tmp_path / "sample-skill"
    stdout = io.StringIO()

    rc = vrcforge_cli.run(
        [
            "--json",
            "skill",
            "init",
            str(source),
            "--id",
            "community.example.avatar-report",
            "--title",
            "Avatar Report",
            "--tool",
            "vrcforge_run_validation_report",
            "--permission",
            "read_project",
        ],
        client=FakeClient(),  # type: ignore[arg-type]
        stdout=stdout,
    )

    assert rc == 0
    result = json.loads(stdout.getvalue())
    assert result["toolCallCount"] == 1
    assert result["files"] == [
        "README.md",
        "SKILL.md",
        "manifest.json",
        "tests/test_skill_smoke.py",
        "workflows/avatar-report.json",
    ]
    manifest = json.loads((source / "manifest.json").read_text(encoding="utf-8"))
    workflow = json.loads((source / manifest["entrypoints"]["workflow"]).read_text(encoding="utf-8"))
    assert manifest["min_vrcforge_version"] == "1.3.0"
    assert len(workflow["steps"]) == 1
    assert workflow["steps"][0] == {
        "name": "run",
        "tool": "vrcforge_run_validation_report",
        "writes": False,
    }
    assert workflow["approval"]["required"] is False
    assert workflow["checkpoint"] == {"required": False, "scope": []}
    assert workflow["rollback"]["required"] is False
    skill_markdown = (source / "SKILL.md").read_text(encoding="utf-8")
    assert "\nentrypoint-tool: vrcforge_run_validation_report\n" in skill_markdown
    assert "\nsupport-files:\n  - workflows/avatar-report.json\n" in skill_markdown


def test_skill_init_write_uses_request_wrapper_and_preserves_explicit_target(tmp_path: Path) -> None:
    source = tmp_path / "material-writer"
    stdout = io.StringIO()

    rc = vrcforge_cli.run(
        [
            "--json",
            "skill",
            "init",
            str(source),
            "--id",
            "community.example.material-writer",
            "--tool",
            "vrcforge_apply_shader_tuning",
            "--permission",
            "unity_modify_materials",
            "--writes",
        ],
        client=FakeClient(),  # type: ignore[arg-type]
        stdout=stdout,
    )

    assert rc == 0
    result = json.loads(stdout.getvalue())
    assert result["toolCallCount"] == 1
    assert result["entrypointTool"] is None
    assert result["workflowTool"] == "vrcforge_request_apply"
    assert result["requestOnly"] is True
    assert result["targetTool"] == "vrcforge_apply_shader_tuning"
    manifest = json.loads((source / "manifest.json").read_text(encoding="utf-8"))
    workflow = json.loads((source / manifest["entrypoints"]["workflow"]).read_text(encoding="utf-8"))
    assert workflow["steps"] == [
        {
            "name": "request_apply",
            "tool": "vrcforge_request_apply",
            "writes": True,
            "request": {"targetTool": "vrcforge_apply_shader_tuning"},
        }
    ]
    assert workflow["approval"]["required"] is True
    assert workflow["checkpoint"] == {
        "required": True,
        "scope": ["Assets", "Packages", "ProjectSettings"],
    }
    assert workflow["rollback"]["required"] is True
    skill_markdown = (source / "SKILL.md").read_text(encoding="utf-8")
    assert not any(line.startswith("entrypoint-tool:") for line in skill_markdown.splitlines())
    assert "\nentrypoint-tool: vrcforge_apply_shader_tuning\n" not in skill_markdown
    assert "Never call `vrcforge_apply_shader_tuning` directly" in skill_markdown
    generated_smoke = (source / "tests/test_skill_smoke.py").read_text(encoding="utf-8")
    assert "workflow['checkpoint']['required'] is True" in generated_smoke
    assert "'targetTool': 'vrcforge_apply_shader_tuning'" in generated_smoke
    assert "assert entrypoint_lines == []" in generated_smoke
    smoke_namespace = runpy.run_path(str(source / "tests/test_skill_smoke.py"))
    smoke_namespace["test_skill_workflow_has_one_gated_tool_call"]()
    readme = (source / "README.md").read_text(encoding="utf-8")
    assert "runtime-direct read/plan tool" in readme
    assert "`vrcforge_request_apply` approval wrapper" in readme


@pytest.mark.parametrize(
    "permissions",
    [
        [],
        ["read_project"],
        ["unity_scan_scene", "unity_run_validation"],
        ["network_access"],
        ["read_env"],
    ],
)
def test_skill_init_write_requires_explicit_mutating_permission(
    tmp_path: Path,
    permissions: list[str],
) -> None:
    source = tmp_path / "underdeclared-writer"
    arguments = [
        "skill",
        "init",
        str(source),
        "--id",
        "community.example.underdeclared-writer",
        "--tool",
        "vrcforge_apply_shader_tuning",
        "--writes",
    ]
    for permission in permissions:
        arguments.extend(["--permission", permission])

    with pytest.raises(vrcforge_cli.CliError, match="mutating --permission"):
        vrcforge_cli.run(arguments, client=FakeClient(), stdout=io.StringIO())  # type: ignore[arg-type]

    assert not source.exists()


@pytest.mark.parametrize(
    "tool,writes,error",
    [
        ("arbitrary_handler", False, "static VRCForge tool id"),
        ("vrcforge_request_apply", False, "only a generated wrapper"),
        ("vrcforge_request_apply", True, "explicit write target"),
        ("vrcforge_apply_approved", True, "explicit write target"),
    ],
)
def test_skill_init_rejects_arbitrary_or_control_entrypoints(
    tmp_path: Path,
    tool: str,
    writes: bool,
    error: str,
) -> None:
    source = tmp_path / "unsafe-skill"
    arguments = [
        "skill",
        "init",
        str(source),
        "--id",
        "community.example.unsafe",
        "--tool",
        tool,
    ]
    if writes:
        arguments.append("--writes")

    with pytest.raises(vrcforge_cli.CliError, match=error):
        vrcforge_cli.run(arguments, client=FakeClient(), stdout=io.StringIO())  # type: ignore[arg-type]

    assert not source.exists()


def test_skill_init_refuses_to_overwrite_generated_files_without_force(tmp_path: Path) -> None:
    source = tmp_path / "sample-skill"
    arguments = [
        "skill",
        "init",
        str(source),
        "--id",
        "community.example.avatar-report",
        "--tool",
        "vrcforge_run_validation_report",
    ]
    vrcforge_cli.run(arguments, client=FakeClient(), stdout=io.StringIO())  # type: ignore[arg-type]

    with pytest.raises(vrcforge_cli.CliError, match="would overwrite"):
        vrcforge_cli.run(arguments, client=FakeClient(), stdout=io.StringIO())  # type: ignore[arg-type]


def test_skill_init_rejects_private_extra_without_force_and_force_replaces_closed_tree(
    tmp_path: Path,
) -> None:
    from skill_packages import SkillPackageService

    source = tmp_path / "closed-skill"
    source.mkdir()
    private_notes = source / "private-notes.txt"
    private_notes.write_text("must never enter a package", encoding="utf-8")
    arguments = [
        "skill",
        "init",
        str(source),
        "--id",
        "community.example.closed-skill",
        "--tool",
        "vrcforge_run_validation_report",
    ]

    with pytest.raises(vrcforge_cli.CliError, match=r"would overwrite.*private-notes\.txt"):
        vrcforge_cli.run(arguments, client=FakeClient(), stdout=io.StringIO())  # type: ignore[arg-type]

    assert private_notes.read_text(encoding="utf-8") == "must never enter a package"

    vrcforge_cli.run(
        [*arguments, "--force"],
        client=FakeClient(),  # type: ignore[arg-type]
        stdout=io.StringIO(),
    )

    expected_files = {
        "README.md",
        "SKILL.md",
        "manifest.json",
        "tests/test_skill_smoke.py",
        "workflows/closed-skill.json",
    }
    assert {
        path.relative_to(source).as_posix()
        for path in source.rglob("*")
        if path.is_file()
    } == expected_files
    assert not private_notes.exists()
    package = SkillPackageService(
        tmp_path / "store",
        vrcforge_version="1.3.0",
    ).export_dev(source, tmp_path / "closed-skill.vsk").package_path
    with zipfile.ZipFile(package) as archive:
        assert "private-notes.txt" not in archive.namelist()


def test_skill_init_refuses_parent_file_conflict_without_force(tmp_path: Path) -> None:
    source = tmp_path / "parent-file-conflict"
    source.mkdir()
    workflows_file = source / "workflows"
    workflows_file.write_text("keep this file", encoding="utf-8")

    with pytest.raises(vrcforge_cli.CliError, match=r"would overwrite.*workflows"):
        vrcforge_cli.run(
            [
                "skill",
                "init",
                str(source),
                "--id",
                "community.example.parent-file-conflict",
                "--tool",
                "vrcforge_run_validation_report",
            ],
            client=FakeClient(),  # type: ignore[arg-type]
            stdout=io.StringIO(),
        )

    assert workflows_file.is_file()
    assert workflows_file.read_text(encoding="utf-8") == "keep this file"
    assert not (source / "manifest.json").exists()


def test_skill_init_refuses_parent_symlink_conflict_without_force(tmp_path: Path) -> None:
    source = tmp_path / "parent-link-conflict"
    source.mkdir()
    external = tmp_path / "external-workflows"
    external.mkdir()
    workflows_link = source / "workflows"
    try:
        workflows_link.symlink_to(external, target_is_directory=True)
    except OSError as exc:
        pytest.skip(f"directory symlink creation is unavailable: {exc}")

    with pytest.raises(vrcforge_cli.CliError, match=r"would overwrite.*workflows"):
        vrcforge_cli.run(
            [
                "skill",
                "init",
                str(source),
                "--id",
                "community.example.parent-link-conflict",
                "--tool",
                "vrcforge_run_validation_report",
            ],
            client=FakeClient(),  # type: ignore[arg-type]
            stdout=io.StringIO(),
        )

    assert workflows_link.is_symlink()
    assert external.is_dir()
    assert not (source / "manifest.json").exists()


def test_skill_init_validates_manifest_before_writing(tmp_path: Path) -> None:
    source = tmp_path / "invalid-skill"

    with pytest.raises(vrcforge_cli.CliError, match="valid skill skeleton"):
        vrcforge_cli.run(
            [
                "skill",
                "init",
                str(source),
                "--id",
                "community.example.invalid",
                "--version",
                "not-semver",
                "--tool",
                "vrcforge_run_validation_report",
            ],
            client=FakeClient(),  # type: ignore[arg-type]
            stdout=io.StringIO(),
        )

    assert not source.exists()


def test_skill_init_flattens_frontmatter_line_breaks(tmp_path: Path) -> None:
    source = tmp_path / "safe-frontmatter"

    vrcforge_cli.run(
        [
            "skill",
            "init",
            str(source),
            "--id",
            "community.example.safe-frontmatter",
            "--title",
            "Safe title\nallowed-tools:\n  - vrcforge_execute_shell",
            "--description",
            "Line one\nentrypoint-tool: vrcforge_execute_shell",
            "--tool",
            "vrcforge_run_validation_report",
        ],
        client=FakeClient(),  # type: ignore[arg-type]
        stdout=io.StringIO(),
    )

    skill_markdown = (source / "SKILL.md").read_text(encoding="utf-8")
    assert skill_markdown.count("allowed-tools:") == 2  # title text plus the single generated field
    assert skill_markdown.count("entrypoint-tool:") == 2  # description text plus the single generated field
    assert "\n  - vrcforge_execute_shell\n" not in skill_markdown


@pytest.mark.parametrize("option", ["--title", "--description"])
def test_skill_init_rejects_frontmatter_document_delimiter_before_writing(tmp_path: Path, option: str) -> None:
    source = tmp_path / "unsafe-frontmatter"

    with pytest.raises(vrcforge_cli.CliError, match="document delimiter"):
        vrcforge_cli.run(
            [
                "skill",
                "init",
                str(source),
                "--id",
                "community.example.unsafe-frontmatter",
                option,
                "quoted --- but parser-breaking",
                "--tool",
                "vrcforge_run_validation_report",
            ],
            client=FakeClient(),  # type: ignore[arg-type]
            stdout=io.StringIO(),
        )

    assert not source.exists()


def test_skill_init_discards_staged_files_when_a_write_fails(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    source = tmp_path / "atomic-skill"
    original_write_text = Path.write_text

    def fail_skill_markdown(path: Path, data: str, *args: Any, **kwargs: Any) -> int:
        if path.name == "SKILL.md" and ".vrcforge-stage-" in str(path):
            raise OSError("injected scaffold write failure")
        return original_write_text(path, data, *args, **kwargs)

    monkeypatch.setattr(Path, "write_text", fail_skill_markdown)

    with pytest.raises(vrcforge_cli.CliError, match="injected scaffold write failure"):
        vrcforge_cli.run(
            [
                "skill",
                "init",
                str(source),
                "--id",
                "community.example.atomic-skill",
                "--tool",
                "vrcforge_run_validation_report",
            ],
            client=FakeClient(),  # type: ignore[arg-type]
            stdout=io.StringIO(),
        )

    assert not source.exists()
    assert not list(tmp_path.glob(".atomic-skill.vrcforge-stage-*"))


def test_skill_lock_validate_uses_package_integrity_contract(tmp_path: Path) -> None:
    from skill_packages import SkillPackageService

    source = tmp_path / "sample-skill"
    vrcforge_cli.run(
        [
            "skill",
            "init",
            str(source),
            "--id",
            "community.example.avatar-report",
            "--tool",
            "vrcforge_run_validation_report",
        ],
        client=FakeClient(),  # type: ignore[arg-type]
        stdout=io.StringIO(),
    )
    package = SkillPackageService(tmp_path / "store", vrcforge_version="1.3.0").export_dev(
        source,
        tmp_path / "avatar-report.vsk",
    ).package_path
    stdout = io.StringIO()

    rc = vrcforge_cli.run(
        ["--json", "skill", "lock-validate", str(package)],
        client=FakeClient(),  # type: ignore[arg-type]
        stdout=stdout,
    )

    assert rc == 0
    result = json.loads(stdout.getvalue())
    assert result["ok"] is True
    assert result["schema"] == "vrcforge.skill-lock-validation.v1"
    assert result["packageId"] == "community.example.avatar-report"
    assert len(result["packageSha256"]) == 64
    assert len(result["lockSha256"]) == 64


def test_skill_init_write_exports_installs_projects_and_loads_as_request_only(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    import dashboard_server
    from agent_gateway import AgentGateway
    from skill_packages import SkillPackageService

    source = tmp_path / "request-only-writer"
    vrcforge_cli.run(
        [
            "skill",
            "init",
            str(source),
            "--id",
            "community.example.request-only-writer",
            "--tool",
            "vrcforge_apply_shader_tuning",
            "--permission",
            "unity_modify_materials",
            "--writes",
        ],
        client=FakeClient(),  # type: ignore[arg-type]
        stdout=io.StringIO(),
    )

    gateway = AgentGateway(
        tmp_path / "app" / "config" / "agent_gateway.json",
        tmp_path / "audit",
    )
    service = SkillPackageService(
        gateway.user_constraints_path.parent / "skill-packages",
        vrcforge_version="1.3.0",
    )
    key_pair = service.generate_signing_keypair()
    package = service.export_release(
        source,
        tmp_path / "request-only-writer.vsk",
        key_pair.private_key_pem,
    ).package_path
    service.trust_signer(key_pair.fingerprint, reason="CLI request-only integration test")
    installed = service.install(package, source="cli-request-only-integration")

    target_calls: list[dict[str, Any]] = []
    gateway.register_tool(
        "vrcforge_request_apply",
        "Request approval for one write.",
        "supervised-write",
        gateway.create_apply_request,
        write=True,
    )
    gateway.register_write_handler(
        "vrcforge_apply_shader_tuning",
        "Apply shader tuning after approval.",
        "high",
        lambda arguments: target_calls.append(dict(arguments)) or {"ok": True},
    )
    monkeypatch.setattr(dashboard_server, "AGENT_GATEWAY", gateway)
    projection = dashboard_server._project_installed_skill(
        installed.installed_path,
        installed.preview.manifest,
    )

    assert projection is not None
    assert projection["supportFiles"] == ["workflows/request-only-writer.json"]
    loaded = gateway.execute_runtime_skill(
        "request-only-writer",
        {"arguments": "apply the reviewed material preset"},
        "cli-request-only-integration",
    )

    assert loaded["ok"] is True
    assert loaded["status"] == "loaded"
    assert loaded["write"] is True
    assert not loaded.get("entrypointTool")
    assert target_calls == []
    support = {
        item["path"]: item["content"]
        for item in loaded["result"]["supportFiles"]
    }
    workflow = json.loads(support["workflows/request-only-writer.json"])
    assert workflow["steps"] == [
        {
            "name": "request_apply",
            "tool": "vrcforge_request_apply",
            "writes": True,
            "request": {"targetTool": "vrcforge_apply_shader_tuning"},
        }
    ]

    project = tmp_path / "UnityProject"
    for name in ("Assets", "Packages", "ProjectSettings"):
        (project / name).mkdir(parents=True, exist_ok=True)
    request = gateway.create_apply_request(
        {
            "target_tool": workflow["steps"][0]["request"]["targetTool"],
            "arguments": {"projectRoot": str(project)},
            "reason": "Exercise the generated request-only contract.",
        }
    )
    assert request["approval"]["status"] == "pending"
    assert target_calls == []
    package_events = [
        event
        for event in gateway.recent_audit_logs(limit=20)
        if event.get("event") == "runtime_skill_package_loaded"
    ]
    assert package_events[-1]["packageId"] == "community.example.request-only-writer"
    assert package_events[-1]["signerFingerprint"] == key_pair.fingerprint
