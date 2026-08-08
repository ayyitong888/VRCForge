from __future__ import annotations

import importlib.util
import json
import shutil
from pathlib import Path

import pytest


REPO_ROOT = Path(__file__).resolve().parents[1]
SCRIPT_PATH = REPO_ROOT / "packaging" / "check_one_five_seams.py"
MANIFEST_PATH = REPO_ROOT / "packaging" / "one_five_owner_facade_manifest.json"
FIXTURES = Path(__file__).parent / "fixtures" / "one_five_seams"


def load_gate():
    spec = importlib.util.spec_from_file_location("check_one_five_seams", SCRIPT_PATH)
    assert spec is not None
    module = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    spec.loader.exec_module(module)
    return module


def load_fixture_manifest(root: Path) -> dict[str, object]:
    return json.loads((root / "manifest.json").read_text(encoding="utf-8"))


def test_pre_1_5_accepts_only_declared_fixture_migration_seams() -> None:
    gate = load_gate()
    root = FIXTURES / "migration"

    report = gate.inspect_tree(root, load_fixture_manifest(root), "1.4.0")

    assert report["ok"] is True
    assert report["status"] == "migration-allowed"
    assert report["enforced"] is False
    assert report["summary"]["remainingHitCount"] > 0
    assert report["summary"]["undeclaredCount"] == 0


def test_1_5_rejects_declared_fixture_owner_facade_and_host_proxy_seams() -> None:
    gate = load_gate()
    root = FIXTURES / "migration"

    report = gate.inspect_tree(root, load_fixture_manifest(root), "1.5.0")

    assert report["ok"] is False
    assert report["status"] == "blocked"
    assert report["enforced"] is True
    assert {item["group"] for item in report["remaining"]} == {
        "fixture.dashboard-host-proxy",
        "fixture.gateway-facade",
        "fixture.composition-class",
    }
    assert report["policy"]["migrationFacadeNamesArePublicApi"] is False


@pytest.mark.parametrize("version", ["1.5.0-rc.1", "1.5.0", "1.6.0", "2.0.0"])
def test_enforcement_never_reopens_after_1_5(version: str) -> None:
    gate = load_gate()
    root = FIXTURES / "migration"

    report = gate.inspect_tree(root, load_fixture_manifest(root), version)

    assert report["enforced"] is True
    assert report["ok"] is False


def test_1_5_accepts_retired_fixture_without_weakening_public_handler() -> None:
    gate = load_gate()
    root = FIXTURES / "clean"

    report = gate.inspect_tree(root, load_fixture_manifest(root), "1.5.0")

    assert report["ok"] is True
    assert report["status"] == "passed"
    assert report["summary"] == {
        "declaredCheckCount": 7,
        "remainingCheckCount": 0,
        "remainingHitCount": 0,
        "undeclaredCount": 0,
    }
    assert "public_route_handler" in (root / "dashboard_server.py").read_text(encoding="utf-8")


def test_undeclared_impl_facade_fails_even_before_enforcement(tmp_path: Path) -> None:
    gate = load_gate()
    root = tmp_path / "fixture"
    shutil.copytree(FIXTURES / "migration", root)
    dashboard = root / "dashboard_server.py"
    dashboard.write_text(
        dashboard.read_text(encoding="utf-8")
        + "\n\ndef undeclared_facade(value: str) -> str:\n"
        + "    return _OWNER._impl_undeclared_facade(value)\n",
        encoding="utf-8",
    )

    report = gate.inspect_tree(root, load_fixture_manifest(root), "1.4.0")

    assert report["ok"] is False
    assert report["summary"]["undeclaredCount"] == 1
    assert report["undeclared"]["dashboardImplFacades"] == ["undeclared_facade"]


def test_undeclared_host_proxy_and_one_five_marker_fail_closed() -> None:
    gate = load_gate()
    root = FIXTURES / "migration"
    manifest = load_fixture_manifest(root)
    first_group = manifest["seamGroups"][0]
    first_group.pop("hostProxy")
    first_group.pop("markers")

    report = gate.inspect_tree(root, manifest, "1.4.0")

    assert report["ok"] is False
    assert report["undeclared"]["hostProxies"] == [
        {"source": "legacy_owner.py", "class": "LegacyOwner"}
    ]
    assert report["undeclared"]["oneFiveStopgaps"] == [
        {"source": "dashboard_server.py", "text": "# STOPGAP(1.5): fixture migration owner."}
    ]


def test_cli_exit_code_tracks_fixture_release_gate(capsys) -> None:
    gate = load_gate()
    migration_root = FIXTURES / "migration"
    clean_root = FIXTURES / "clean"

    assert gate.main(
        [
            "--repo-root",
            str(migration_root),
            "--manifest",
            "manifest.json",
            "--version",
            "1.5.0",
        ]
    ) == 1
    blocked = json.loads(capsys.readouterr().out)
    assert blocked["status"] == "blocked"

    assert gate.main(
        [
            "--repo-root",
            str(clean_root),
            "--manifest",
            "manifest.json",
            "--version",
            "1.5.0",
        ]
    ) == 0
    passed = json.loads(capsys.readouterr().out)
    assert passed["status"] == "passed"


def test_checked_in_manifest_is_exhaustive_and_keeps_exact_history() -> None:
    gate = load_gate()
    manifest = json.loads(MANIFEST_PATH.read_text(encoding="utf-8"))

    report = gate.inspect_tree(REPO_ROOT, manifest, "1.4.0")

    assert report["ok"] is True
    assert report["manifestErrors"] == []
    assert report["summary"]["undeclaredCount"] == 0
    groups = {group["id"]: group for group in manifest["seamGroups"]}
    dashboard_host_proxy_count = sum(
        len(group["facades"][0]["methods"])
        for group_id, group in groups.items()
        if group_id.startswith("dashboard.") and group.get("hostProxy")
    )
    assert dashboard_host_proxy_count == 64
    assert len(groups["dashboard.project-snapshot-root-facades"]["facades"][0]["methods"]) == 17
    assert len(groups["dashboard.unity-status-root-facades"]["facades"][0]["methods"]) == 3
    assert len(groups["gateway.approval-transaction-host-proxy"]["facades"][0]["methods"]) == 41
    assert len(groups["gateway.checkpoint-recovery-host-proxy"]["facades"][0]["methods"]) == 87
    assert len(groups["gateway.skill-registry-host-proxy"]["facades"][0]["methods"]) == 21
    assert len(groups["gateway.desktop-computer-use-stopgap-facade"]["facades"][0]["methods"]) == 20
    assert {item["id"] for item in manifest["publicApiAllowlist"]["contracts"]} == {
        "fastapi-route-request-response-openapi",
        "tauri-command-surface",
        "cli-mcp-tool-surface",
        "eventbus-surface",
        "persisted-disk-schemas",
        "genuine-imported-models",
    }


def test_build_and_publish_invoke_gate_before_build_or_remote_mutation() -> None:
    build = (REPO_ROOT / "packaging" / "build_release.ps1").read_text(encoding="utf-8-sig")
    publish = (REPO_ROOT / "packaging" / "publish_release.ps1").read_text(encoding="utf-8-sig")
    invocation = (
        "& $pythonExe .\\packaging\\check_one_five_seams.py "
        "--repo-root $repoRoot --version $Version"
    )

    assert build.count(invocation) == 1
    assert build.index("$pythonExe = Resolve-PythonExe") < build.index(invocation)
    assert build.index(invocation) < build.index("scan_release_sensitive_strings.py")
    assert build.index(invocation) < build.index("Build-TauriDesktopApp -DestinationExe")

    assert publish.count(invocation) == 1
    assert "function Resolve-PythonExe" in publish
    assert publish.index("$pythonExe = Resolve-PythonExe") < publish.index(invocation)
    assert publish.index(invocation) < publish.index("git fetch origin --tags --prune")
    assert publish.index(invocation) < publish.index("& gh @createArgs")
