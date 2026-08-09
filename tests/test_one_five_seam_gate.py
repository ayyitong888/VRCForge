from __future__ import annotations

import ast
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
    assert dashboard_host_proxy_count == 6
    assert groups["dashboard.skill-package-projection-typed-root-owner"][
        "rootSymbols"
    ] == [
        {
            "source": "dashboard_server.py",
            "scope": "module",
            "name": "SKILL_PACKAGE_PROJECTION",
        }
    ]
    assert groups["dashboard.skill-package-controller-typed-root-owner"][
        "rootSymbols"
    ] == [
        {
            "source": "dashboard_server.py",
            "scope": "module",
            "name": "SKILL_PACKAGE_CONTROLLER",
        }
    ]
    assert groups["dashboard.skill-package-governance-typed-root-owner"][
        "rootSymbols"
    ] == [
        {
            "source": "dashboard_server.py",
            "scope": "module",
            "name": "SKILL_PACKAGE_GOVERNANCE",
        }
    ]
    assert {
        item["name"]
        for item in groups["dashboard.path-to-skill-typed-root-owners"]["rootSymbols"]
    } == {"PATH_TO_SKILL_PREVIEW", "PATH_TO_SKILL_WRITE"}
    assert groups["dashboard.project-catalog-typed-root-owner"]["rootSymbols"] == [
        {
            "source": "dashboard_server.py",
            "scope": "module",
            "name": "PROJECT_CATALOG_DISCOVERY",
        }
    ]
    assert {
        item["name"]
        for item in groups["dashboard.provider-typed-root-owners"]["rootSymbols"]
    } == {
        "PROVIDER_MODEL_CATALOG",
        "PROVIDER_CONFIGURATION",
        "PROVIDER_TEXT_PROBE",
        "PROVIDER_TESTS",
    }
    assert groups["dashboard.shader-vision-protection-typed-root-owner"][
        "rootSymbols"
    ] == [
        {
            "source": "dashboard_server.py",
            "scope": "module",
            "name": "SHADER_VISION_PROTECTION",
        }
    ]
    assert {
        item["name"]
        for item in groups["dashboard.wardrobe-outfit-root-owners"]["rootSymbols"]
    } == {
        "WARDROBE_ARTIFACT_READ",
        "CLOTHING_FX_READ",
        "CLOTHING_FX_APPROVED_WRITE",
        "SETUP_OUTFIT_PREVIEW",
        "SETUP_OUTFIT_APPROVED_WRITE",
        "ADD_WARDROBE_OUTFIT_PREVIEW",
        "ADD_WARDROBE_OUTFIT_APPROVED_WRITE",
        "ADD_OUTFIT_PART_PREVIEW",
        "ADD_OUTFIT_PART_APPROVED_WRITE",
        "ADD_MODULAR_AVATAR_COMPONENT_PREVIEW",
        "ADD_MODULAR_AVATAR_COMPONENT_APPROVED_WRITE",
        "MANAGE_WARDROBE_PREVIEW",
        "MANAGE_WARDROBE_APPROVED_WRITE",
        "CREATE_WARDROBE_PREVIEW",
        "CREATE_WARDROBE_APPROVED_WRITE",
        "PREPARED_ADD_OUTFIT_STATE",
        "PREPARED_ADD_OUTFIT_PREVIEW",
        "PREPARED_ADD_OUTFIT_PREPARER",
        "PREPARED_ADD_OUTFIT_APPROVED_WRITE",
        "PREPARED_OUTFIT_IMPORT_PREPARER",
        "PREPARED_OUTFIT_IMPORT_APPROVED_WRITE",
        "WARDROBE_OUTFIT_WORKFLOWS",
        "WARDROBE_OUTFIT_APPROVED_WRITES",
    }
    assert groups["dashboard.wardrobe-outfit-root-owners"]["facades"] == []
    assert len(groups["gateway.approval-transaction-host-proxy"]["facades"][0]["methods"]) == 41
    assert len(groups["gateway.checkpoint-recovery-host-proxy"]["facades"][0]["methods"]) == 87
    assert "gateway.skill-registry-host-proxy" not in groups
    assert "dashboard.know-yourself-root-facade" not in groups
    assert "dashboard.doctor-readiness-root-facade" not in groups
    assert "dashboard.unity-status-root-facades" not in groups
    assert "dashboard.project-snapshot-root-facades" not in groups
    assert "dashboard.late-bound-composition-context" not in groups
    assert "dashboard.optimization-root-owners" not in groups
    assert "dashboard.goal-root-owner" not in groups
    assert "dashboard.memory-review-root-graph" not in groups
    assert len(groups["gateway.desktop-computer-use-stopgap-facade"]["facades"][0]["methods"]) == 20
    assert {item["id"] for item in manifest["publicApiAllowlist"]["contracts"]} == {
        "fastapi-route-request-response-openapi",
        "tauri-command-surface",
        "cli-mcp-tool-surface",
        "eventbus-surface",
        "persisted-disk-schemas",
        "genuine-imported-models",
    }


def test_dashboard_provider_typed_roots_do_not_reexport_removed_host_seams() -> None:
    tree = ast.parse((REPO_ROOT / "dashboard_server.py").read_text(encoding="utf-8"))
    bindings: set[str] = set()
    for node in tree.body:
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
            bindings.add(node.name)
        elif isinstance(node, (ast.Import, ast.ImportFrom)):
            bindings.update(alias.asname or alias.name.rsplit(".", 1)[-1] for alias in node.names)
        elif isinstance(node, ast.Assign):
            bindings.update(
                target.id for target in node.targets if isinstance(target, ast.Name)
            )
        elif isinstance(node, ast.AnnAssign) and isinstance(node.target, ast.Name):
            bindings.add(node.target.id)

    removed = {
        "_PROVIDER_CONFIGURATION",
        "_PROVIDER_MODEL_CATALOG",
        "_PROVIDER_TEST_INTEGRATION",
        "DASHBOARD_API_CONFIG",
        "DASHBOARD_VISION_CONFIG",
        "DashboardApiConfig",
        "DashboardVisionConfig",
        "serialize_app_api_config",
        "load_initial_dashboard_api_config",
        "load_initial_dashboard_vision_config",
        "normalize_vision_config_request",
        "load_config_document",
        "normalize_api_config_request",
        "save_dashboard_config_document",
        "save_dashboard_api_config",
        "save_dashboard_vision_config",
        "serialize_api_config",
        "serialize_vision_config",
        "serialize_app_vision_config",
        "build_effective_model_summary",
        "mask_secret",
        "provider_config_descriptor",
        "enrich_provider_model_item",
        "fetch_provider_models",
        "fetch_openai_compatible_models",
        "fetch_google_ai_studio_models",
        "fetch_vertex_ai_models",
        "fetch_anthropic_models",
        "normalize_provider_model_list",
        "read_model_attr",
        "coerce_positive_int",
        "build_provider_model_info",
        "run_provider_test_sync",
        "_run_provider_text_probe",
        "_provider_probe_settings",
    }
    assert bindings.isdisjoint(removed)


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
