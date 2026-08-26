from __future__ import annotations

import argparse
import hashlib
import importlib.util
import json
import sys
import tarfile
from pathlib import Path
from types import ModuleType
from typing import Any

import pytest

from unity_mcp_tool_contract import EXPECTED_TOOL_COUNT


ROOT = Path(__file__).resolve().parents[1]
HARNESS_PATH = ROOT / "artifacts" / "acceptance-harness" / "smoke_unity_mcp_64_live.py"
PROFILE_PATH = ROOT / "artifacts" / "acceptance-harness" / "mcp64-live-profile.json"
SEED_CONTEXT_PATH = ROOT / "artifacts" / "acceptance-harness" / "mcp80-live-context.seed.json"
SEED_GAP_PATH = ROOT / "artifacts" / "acceptance-harness" / "mcp80-live-context-gap-report.json"
SEED_PROJECT_PATH = (
    ROOT / "artifacts" / "acceptance-harness" / "disposable-projects" / "live80-20260824-seed"
)
CLONE_CONTEXT_PATH = ROOT / "artifacts" / "acceptance-harness" / "mcp80-live-context.dogfood-clone.json"
CLONE_GAP_PATH = (
    ROOT / "artifacts" / "acceptance-harness" / "mcp80-live-context-dogfood-clone-gap-report.json"
)
CLONE_PROJECT_PATH = (
    ROOT / "artifacts" / "acceptance-harness" / "disposable-projects" / "live80-20260824-dogfood-clone"
)
CLONE_PACKAGE_PATH = ROOT / "artifacts" / "acceptance-harness" / "live80-import-fixture.unitypackage"

pytestmark = pytest.mark.skipif(
    not HARNESS_PATH.is_file(),
    reason="local Unity MCP live acceptance harness is not part of a clean source checkout",
)
requires_seed_fixtures = pytest.mark.skipif(
    not (
        SEED_PROJECT_PATH.is_dir()
        and SEED_CONTEXT_PATH.is_file()
        and SEED_GAP_PATH.is_file()
    ),
    reason="local Unity MCP seed project fixtures are not part of a clean source checkout",
)
requires_clone_fixtures = pytest.mark.skipif(
    not (
        CLONE_PROJECT_PATH.is_dir()
        and CLONE_CONTEXT_PATH.is_file()
        and CLONE_GAP_PATH.is_file()
    ),
    reason="local Unity MCP dogfood clone fixtures are not part of a clean source checkout",
)
requires_clone_package_fixtures = pytest.mark.skipif(
    not (CLONE_CONTEXT_PATH.is_file() and CLONE_PACKAGE_PATH.is_file()),
    reason="local Unity MCP package fixtures are not part of a clean source checkout",
)


def load_harness_module() -> ModuleType:
    spec = importlib.util.spec_from_file_location("vrcforge_mcp64_live_harness", HARNESS_PATH)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


@pytest.fixture()
def live() -> ModuleType:
    return load_harness_module()


def make_args(tmp_path: Path, **overrides: Any) -> argparse.Namespace:
    values: dict[str, Any] = {
        "base_url": "http://127.0.0.1:8757",
        "gateway_config": "",
        "app_token_file": "",
        "context_json": None,
        "profile_json": PROFILE_PATH,
        "project_root": str(tmp_path),
        "tool": [],
        "execute": False,
        "allow_approved_writes": False,
        "output": None,
        "timeout": 1.0,
    }
    values.update(overrides)
    return argparse.Namespace(**values)


def test_live_endpoint_is_restricted_to_loopback(live: ModuleType, tmp_path: Path) -> None:
    with pytest.raises(live.HarnessError, match="loopback"):
        live.UnityMcp64LiveHarness(make_args(tmp_path, base_url="https://gateway.example.test:8757"))


def test_approved_live_run_rejects_unmarked_project_outside_disposable_root(
    live: ModuleType, tmp_path: Path
) -> None:
    harness = live.UnityMcp64LiveHarness(make_args(tmp_path, execute=True, allow_approved_writes=True))
    with pytest.raises(live.HarnessError, match="disposable-projects"):
        harness.run()


def test_disposable_marker_binds_exact_project_and_context_digest(
    live: ModuleType, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    allowed = tmp_path / "disposable-projects"
    project = allowed / "case-project"
    (project / "Assets").mkdir(parents=True)
    (project / "Packages").mkdir()
    (project / "ProjectSettings").mkdir()
    (project / "Packages" / "manifest.json").write_text("{}", encoding="utf-8")
    (project / "ProjectSettings" / "ProjectVersion.txt").write_text(
        "m_EditorVersion: 2022.3.22f1\n", encoding="utf-8"
    )
    marker = project / live.DISPOSABLE_MARKER_NAME
    marker.write_text(
        json.dumps(
            {
                "schema": live.DISPOSABLE_MARKER_SCHEMA,
                "projectRoot": str(project),
                "deleteAfterRun": True,
                "remoteUploadAllowed": False,
            }
        ),
        encoding="utf-8",
    )
    context = tmp_path / "context.json"
    context.write_text(
        json.dumps(
            {
                "DISPOSABLE_PROJECT": True,
                "DISPOSABLE_PROJECT_MARKER_SHA256": hashlib.sha256(marker.read_bytes()).hexdigest(),
            }
        ),
        encoding="utf-8",
    )
    monkeypatch.setattr(live, "DISPOSABLE_PROJECTS_ROOT", allowed.resolve())
    harness = live.UnityMcp64LiveHarness(make_args(project, context_json=context))

    attestation = harness._verify_disposable_project()  # noqa: SLF001

    assert attestation["verified"] is True
    assert attestation["projectRoot"] == str(project.resolve())
    assert attestation["remoteUploadAllowed"] is False


def test_context_audit_separates_static_missing_from_causal_deferred_bindings(
    live: ModuleType, tmp_path: Path
) -> None:
    harness = live.UnityMcp64LiveHarness(make_args(tmp_path))
    audit = harness._context_audit(harness.cases)  # noqa: SLF001
    assert "AVATAR_PATH" in audit["missingPlaceholders"]
    assert "SAFE_BACKUP_ID" in audit["deferredPlaceholders"]
    assert "UPLOAD_READINESS_DIGEST" in audit["deferredPlaceholders"]
    assert "MATERIAL_PREVIEW_RECEIPT" in audit["deferredPlaceholders"]
    assert "MATERIAL_TEXTURE_PREVIEW_RECEIPT" in audit["deferredPlaceholders"]
    assert "PROJECT_ROOT" not in audit["missingPlaceholders"]
    assert audit["contextCompleteForStart"] is False


@requires_seed_fixtures
def test_seed_context_gap_report_matches_runner_and_exact_disposable_tree(live: ModuleType) -> None:
    harness = live.UnityMcp64LiveHarness(
        make_args(SEED_PROJECT_PATH, context_json=SEED_CONTEXT_PATH)
    )
    attestation = harness._verify_disposable_project()  # noqa: SLF001
    audit = harness._context_audit(harness.cases)  # noqa: SLF001
    gap = json.loads(SEED_GAP_PATH.read_text(encoding="utf-8"))
    files = sorted(path for path in SEED_PROJECT_PATH.rglob("*") if path.is_file())
    digest = hashlib.sha256()
    for path in files:
        digest.update(path.relative_to(SEED_PROJECT_PATH).as_posix().encode())
        digest.update(b"\0")
        digest.update(path.read_bytes())
        digest.update(b"\0")

    assert attestation["verified"] is True
    assert gap["executeReady"] is False
    assert gap["projectFileCount"] == len(files)
    assert gap["projectTotalBytes"] == sum(path.stat().st_size for path in files)
    assert gap["projectTreeSha256"] == digest.hexdigest()
    assert gap["markerSha256"] == attestation["markerSha256"]
    assert gap["missingPlaceholders"] == audit["missingPlaceholders"]
    assert gap["deferredPlaceholders"] == audit["deferredPlaceholders"]


@requires_clone_fixtures
def test_isolated_clone_context_and_gap_are_bound_without_excluded_roots(live: ModuleType) -> None:
    harness = live.UnityMcp64LiveHarness(
        make_args(CLONE_PROJECT_PATH, context_json=CLONE_CONTEXT_PATH)
    )
    attestation = harness._verify_disposable_project()  # noqa: SLF001
    audit = harness._context_audit(harness.cases)  # noqa: SLF001
    gap = json.loads(CLONE_GAP_PATH.read_text(encoding="utf-8"))
    context = json.loads(CLONE_CONTEXT_PATH.read_text(encoding="utf-8"))
    marker = json.loads(
        (CLONE_PROJECT_PATH / live.DISPOSABLE_MARKER_NAME).read_text(encoding="utf-8")
    )

    assert attestation["verified"] is True
    assert attestation["copiedTree"]["fileCount"] == 8207
    assert marker["copiedRoots"] == ["Assets", "Packages", "ProjectSettings"]
    assert all(not (CLONE_PROJECT_PATH / name).exists() for name in marker["excludedRoots"])
    assert gap["missingPlaceholders"] == audit["missingPlaceholders"]
    assert gap["deferredPlaceholders"] == audit["deferredPlaceholders"]
    assert gap["providedPlaceholderCount"] == 85
    assert gap["missingPlaceholderCount"] == 14
    assert gap["deferredPlaceholderCount"] == 13
    assert gap["executeReady"] is False
    assert context["BLENDSHAPE"] == "mouth_a"
    assert context["BLENDSHAPE_RENDERER_PATH"].endswith("/SapphyHeadRig/Body")
    assert context["OUTERMOST_PREFAB_INSTANCE_PATH"].startswith("__VRCForge_")
    assert len(context["PREVIEW_PREFAB_GUID"]) == 32
    assert "fixedPrimitiveRuntime" in gap["classification"]["needsInstalledDependencies"]


@requires_clone_package_fixtures
def test_run_owned_unitypackage_has_one_exact_text_asset() -> None:
    context = json.loads(CLONE_CONTEXT_PATH.read_text(encoding="utf-8"))
    with tarfile.open(CLONE_PACKAGE_PATH, mode="r:gz") as archive:
        names = sorted(member.name for member in archive.getmembers())

    assert names == [
        "80a11ce0000000000000000000000080/asset",
        "80a11ce0000000000000000000000080/asset.meta",
        "80a11ce0000000000000000000000080/pathname",
    ]
    assert context["PACKAGE_PATH"] == str(CLONE_PACKAGE_PATH.resolve())
    assert context["PACKAGE_EXPECTED_ASSET_PATH"] == "Assets/VRCForgeAcceptance/Live80Imported.txt"
    assert context["PACKAGE_SIZE_BYTES"] == CLONE_PACKAGE_PATH.stat().st_size
    assert context["PACKAGE_SHA256_LOWER"] == hashlib.sha256(CLONE_PACKAGE_PATH.read_bytes()).hexdigest()


@requires_clone_fixtures
def test_blendshape_profile_overrides_catalog_body_with_discovered_renderer(live: ModuleType) -> None:
    harness = live.UnityMcp64LiveHarness(
        make_args(CLONE_PROJECT_PATH, context_json=CLONE_CONTEXT_PATH)
    )
    case = next(item for item in harness.cases if item["tool"] == "vrc_apply_blendshapes")
    mapping = harness.profile["caseMappings"]["vrc_apply_blendshapes"]
    arguments = harness._apply_mapping_arguments(  # noqa: SLF001
        harness._resolve_arguments(case),  # noqa: SLF001
        mapping,
    )

    assert arguments["adjustments"] == [
        {
            "rendererPath": harness.dynamic_context["BLENDSHAPE_RENDERER_PATH"],
            "blendshapeName": "mouth_a",
            "targetWeight": 50.0,
        }
    ]


def test_mcp_calls_are_execution_layer_with_unique_matching_ids(live: ModuleType, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    harness = live.UnityMcp64LiveHarness(make_args(tmp_path))
    harness.credentials = live.Credentials(gateway_token="gateway", app_token="app")
    requests: list[dict[str, Any]] = []

    class Response:
        def __init__(self, payload: dict[str, Any]) -> None:
            self.payload = payload

        def __enter__(self) -> "Response":
            return self

        def __exit__(self, *_args: Any) -> None:
            return None

        def read(self) -> bytes:
            return json.dumps(self.payload).encode("utf-8")

    def urlopen(request: Any, timeout: float) -> Response:
        assert timeout == 1.0
        body = json.loads(request.data.decode("utf-8"))
        requests.append(body)
        return Response(
            {
                "jsonrpc": "2.0",
                "id": body["id"],
                "result": {"structuredContent": {"ok": True}},
            }
        )

    monkeypatch.setattr("urllib.request.urlopen", urlopen)
    first, first_outer = harness._mcp_call("vrcforge_find_assets", {})  # noqa: SLF001
    second, second_outer = harness._mcp_call("vrcforge_get_asset_info", {})  # noqa: SLF001

    assert first == second == {"ok": True}
    assert all(item["params"]["exposureLayer"] == "execution" for item in requests)
    assert first_outer["requestId"] == requests[0]["id"]
    assert second_outer["requestId"] == requests[1]["id"]
    assert first_outer["requestId"] != second_outer["requestId"]


@pytest.mark.parametrize(
    ("payload", "message"),
    [
        ({"ok": False}, "explicit unsuccessful"),
        ({"ok": True, "pending": True}, "remained pending"),
        ({"ok": True}, "Missing success fields"),
    ],
)
def test_success_predicate_fails_closed(live: ModuleType, tmp_path: Path, payload: dict[str, Any], message: str) -> None:
    harness = live.UnityMcp64LiveHarness(make_args(tmp_path))
    record = {
        "tool": "fake_tool",
        "successFields": {"required": {"present": False, "value": None}},
        "status": "planned",
        "error": "",
        "coreAudit": None,
    }
    harness._complete_from_result(record, payload)  # noqa: SLF001
    assert record["status"] == "failed"
    assert message in record["error"]


def test_package_identity_is_derived_and_mismatch_blocks(live: ModuleType, tmp_path: Path) -> None:
    package = tmp_path / "fixture.unitypackage"
    package.write_bytes(b"fixture-package")
    context = tmp_path / "context.json"
    context.write_text(json.dumps({"PROJECT_ROOT": str(tmp_path), "PACKAGE_PATH": str(package)}), encoding="utf-8")
    harness = live.UnityMcp64LiveHarness(make_args(tmp_path, context_json=context))
    assert harness.dynamic_context["PACKAGE_SIZE_BYTES"] == len(b"fixture-package")
    assert len(harness.dynamic_context["PACKAGE_SHA256_LOWER"]) == 64

    context.write_text(
        json.dumps({"PROJECT_ROOT": str(tmp_path), "PACKAGE_PATH": str(package), "PACKAGE_SHA256_LOWER": "0" * 64}),
        encoding="utf-8",
    )
    with pytest.raises(live.HarnessError, match="does not match"):
        live.UnityMcp64LiveHarness(make_args(tmp_path, context_json=context))


def test_app_prepared_receipt_is_captured_but_never_fabricated(live: ModuleType, tmp_path: Path) -> None:
    harness = live.UnityMcp64LiveHarness(make_args(tmp_path))
    record = {"tool": "vrc_set_material_shader", "status": "planned", "error": ""}
    harness._capture_approval_receipt(  # noqa: SLF001
        record,
        {"arguments": {"arguments": {"expectedPreviewDigest": "a" * 64}}},
    )
    assert harness.dynamic_context["MATERIAL_PREVIEW_RECEIPT"]["expectedPreviewDigest"] == "a" * 64

    missing = {"tool": "vrc_set_material_shader", "status": "planned", "error": ""}
    harness._capture_approval_receipt(missing, {"arguments": {}})  # noqa: SLF001
    assert missing["status"] == "failed"
    assert "dynamic receipt" in missing["error"]

    backup = {
        "tool": "vrc_create_safe_backup",
        "successFields": {
            "backup_id": {"present": False, "value": None},
            "backup_path": {"present": False, "value": None},
        },
        "status": "planned",
        "error": "",
        "coreAudit": None,
    }
    harness._complete_from_result(backup, {"backup_id": "backup-1", "backup_path": "Library/VRCForge/Backups/backup-1"})  # noqa: SLF001
    assert backup["status"] == "passed"
    assert harness.dynamic_context["SAFE_BACKUP_ID"] == "backup-1"


def test_approved_write_uses_request_apply_and_independent_restore(live: ModuleType, tmp_path: Path) -> None:
    harness = live.UnityMcp64LiveHarness(make_args(tmp_path, execute=True, allow_approved_writes=True))
    harness.credentials = live.Credentials(gateway_token="gateway", app_token="app")
    harness.profile["caseMappings"]["fake_write"] = {
        "readback": {"kind": "result_success_fields"},
        "cleanup": {"kind": "restore_checkpoint_and_fingerprint"},
    }
    fingerprint = {"fileCount": 1, "totalBytes": 3, "sha256": "a" * 64}
    harness._project_assets_fingerprint = lambda: dict(fingerprint)  # type: ignore[method-assign]  # noqa: SLF001
    harness._checkpoint_assets_fingerprint = lambda _path: dict(fingerprint)  # type: ignore[method-assign]  # noqa: SLF001
    calls: list[tuple[str, dict[str, Any]]] = []

    def mcp_call(name: str, arguments: dict[str, Any]) -> tuple[dict[str, Any], dict[str, Any]]:
        calls.append((name, arguments))
        approval_id = "restore-approval" if arguments.get("target_tool") == "vrcforge_restore_checkpoint" else "write-approval"
        return {"ok": True, "approval": {"id": approval_id, "status": "pending"}}, {"requestId": f"request-{len(calls)}"}

    approvals = iter(
        [
            {
                "ok": True,
                "execution": {
                    "status": "applied",
                    "requestTrace": {
                        "unityCoreCallAudits": [
                            {"requestId": "core-write-1", "toolName": "fake_write"}
                        ]
                    },
                    "checkpoint": {
                        "id": "checkpoint-1",
                        "archivePath": str(tmp_path / "checkpoint.zip"),
                        "unityPrepare": {"ok": True, "projectPath": str(tmp_path), "projectPathDigest": "b" * 64},
                    },
                    "result": {"ok": True, "value": "written"},
                },
            },
            {
                "ok": True,
                "execution": {
                    "status": "applied",
                    "result": {
                        "ok": True,
                        "restored": True,
                        "unityReload": {"ok": True, "projectPath": str(tmp_path)},
                    },
                },
            },
        ]
    )
    harness._mcp_call = mcp_call  # type: ignore[method-assign]  # noqa: SLF001
    harness._approve_once = lambda _approval_id: next(approvals)  # type: ignore[method-assign]  # noqa: SLF001
    record = {
        "tool": "fake_write",
        "mode": "approved_write",
        "successFields": {"value": {"present": False, "value": None}},
        "cleanup": {"required": "restore", "status": "not_started"},
        "readback": {"status": "not_started"},
        "approvalId": "",
        "checkpointId": "",
        "outerRequestId": "",
        "coreAudit": None,
        "evidenceKey": "",
        "status": "planned",
        "error": "",
    }

    harness._run_approved_request(record, "vrcforge_unity_mcp_write", {"toolName": "fake_write"}, allow_write=True)  # noqa: SLF001

    assert record["status"] == "passed"
    assert record["cleanup"]["status"] == "passed"
    assert [name for name, _ in calls] == ["vrcforge_request_apply", "vrcforge_request_apply"]
    assert calls[1][1]["target_tool"] == "vrcforge_restore_checkpoint"
    assert harness.observed_results["vrc_prepare_checkpoint"]["ok"] is True
    assert harness.observed_results["vrc_reload_after_checkpoint_restore"]["ok"] is True


def test_cleanup_fingerprint_mismatch_is_terminal_failure(live: ModuleType, tmp_path: Path) -> None:
    harness = live.UnityMcp64LiveHarness(make_args(tmp_path, execute=True, allow_approved_writes=True))
    harness.credentials = live.Credentials(gateway_token="gateway", app_token="app")
    harness._mcp_call = lambda _name, _arguments: (  # type: ignore[method-assign]  # noqa: SLF001
        {"ok": True, "approval": {"id": "restore-approval", "status": "pending"}},
        {"requestId": "restore-request"},
    )
    harness._approve_once = lambda _approval_id: {  # type: ignore[method-assign]  # noqa: SLF001
        "ok": True,
        "execution": {"status": "applied", "result": {"ok": True, "restored": True}},
    }
    harness._project_assets_fingerprint = lambda: {  # type: ignore[method-assign]  # noqa: SLF001
        "fileCount": 2,
        "totalBytes": 4,
        "sha256": "b" * 64,
    }
    record = {
        "tool": "fake_write",
        "status": "passed",
        "error": "",
        "checkpointId": "checkpoint-1",
        "cleanup": {"required": "restore", "status": "not_started"},
        "projectAssetsBefore": {"fileCount": 1, "totalBytes": 3, "sha256": "a" * 64},
        "checkpointAssets": {"fileCount": 1, "totalBytes": 3, "sha256": "a" * 64},
    }
    assert harness._restore_checkpoint_cleanup(record) is False  # noqa: SLF001
    assert record["status"] == "failed"
    assert record["cleanup"]["status"] == "failed"
    assert harness.stop_reason == "write_checkpoint_restore_verification_failed"


def test_pending_build_test_is_polled_to_terminal_with_the_same_job_id(live: ModuleType, tmp_path: Path) -> None:
    harness = live.UnityMcp64LiveHarness(make_args(tmp_path, execute=True))
    calls: list[tuple[str, dict[str, Any]]] = []
    responses = iter(
        [
            {
                "ok": True,
                "result": {"jobId": "a" * 32, "status": "running", "pending": True},
            },
            {
                "ok": True,
                "result": {
                    "jobId": "a" * 32,
                    "status": "completed",
                    "localOnly": True,
                    "uploadAttempted": False,
                },
            },
        ]
    )

    def mcp_call(name: str, arguments: dict[str, Any]) -> tuple[dict[str, Any], dict[str, Any]]:
        calls.append((name, arguments))
        return next(responses), {"requestId": f"poll-{len(calls)}"}

    harness._mcp_call = mcp_call  # type: ignore[method-assign]  # noqa: SLF001
    mapping = harness.profile["caseMappings"]["vrc_build_test_avatar"]
    terminal, outer = harness._poll_existing_job(  # noqa: SLF001
        {"tool": "vrc_build_test_avatar"},
        {
            "status": "applied",
            "result": {"jobId": "a" * 32, "status": "pending", "pending": True},
        },
        mapping,
    )

    assert [name for name, _arguments in calls] == [
        "vrcforge_get_build_test_status",
        "vrcforge_get_build_test_status",
    ]
    assert all(arguments == {"projectPath": str(tmp_path), "jobId": "a" * 32} for _name, arguments in calls)
    assert harness._best_payload(terminal, ["status"])["status"] == "completed"  # noqa: SLF001
    assert outer["requestId"] == "poll-2"


def test_call_audit_lookup_uses_exact_catalog_tool_and_never_outer_id(
    live: ModuleType, tmp_path: Path
) -> None:
    harness = live.UnityMcp64LiveHarness(make_args(tmp_path))
    execution = {
        "requestId": "gateway-outer-must-not-count",
        "requestTrace": {
            "unityCoreCallAudits": [
                {"requestId": "core-import", "toolName": "vrc_import_unitypackage"},
                {"requestId": "core-refresh", "toolName": "vrc_refresh_asset_database"},
            ]
        },
    }

    assert harness._find_call_audit(execution, "vrc_import_unitypackage")["requestId"] == "core-import"  # noqa: SLF001
    assert harness._find_call_audit(execution, "vrc_refresh_asset_database")["requestId"] == "core-refresh"  # noqa: SLF001
    assert harness._find_call_audit(execution, "vrc_missing") == {}  # noqa: SLF001


def build_safe_backup_chain_fake(
    live: ModuleType,
    tmp_path: Path,
    *,
    create_valid: bool = True,
    restore_matches: bool = True,
) -> tuple[Any, list[str], str]:
    scene_asset_path = "Assets/VRCForgeAcceptance/Mcp64SuccessMatrix/Mcp64SuccessMatrix.unity"
    scene = tmp_path / scene_asset_path
    scene.parent.mkdir(parents=True)
    scene.write_bytes(b"baseline-scene")
    Path(str(scene) + ".meta").write_text("guid: baseline\n", encoding="utf-8")
    backup_id = "backup-1"
    backup_path = tmp_path / "Library" / "VRCForge" / "Backups" / backup_id
    backup_path.mkdir(parents=True)
    harness = live.UnityMcp64LiveHarness(make_args(tmp_path, execute=True, allow_approved_writes=True))
    calls: list[str] = []

    def approve_target_once(
        target_tool: str,
        _arguments: dict[str, Any],
        _reason: str,
        _preview: dict[str, Any],
    ) -> tuple[dict[str, Any], str, str, dict[str, Any]]:
        calls.append(target_tool)
        index = len(calls)
        harness.actual_call_count += 2
        if target_tool == "vrcforge_create_safe_backup":
            payload = {
                "backup_id": backup_id if create_valid else "",
                "backup_path": str(backup_path) if create_valid else "",
                "files": [
                    {"project_relative_path": scene_asset_path},
                    {"project_relative_path": scene_asset_path + ".meta"},
                ],
                "requested_asset_paths": [scene_asset_path],
                "summary": {"fileCount": 2},
                "restore_hints": {},
            }
            return {"status": "applied", "result": payload}, "approval-create", "checkpoint-create", {"requestId": "request-create"}
        if target_tool == "vrcforge_setup_outfit":
            scene.write_bytes(b"mutated-scene")
            return {
                "status": "applied",
                "checkpoint": {
                    "id": "checkpoint-mutation",
                    "unityPrepare": {
                        "ok": True,
                        "projectPath": str(tmp_path),
                        "projectPathDigest": "a" * 64,
                        "scenes": [scene_asset_path],
                        "unityProcessId": 1,
                    },
                },
                "result": {
                    "status": "completed",
                    "pending": False,
                    "sceneSaved": True,
                    "committed": True,
                },
            }, "approval-mutation", "checkpoint-mutation", {"requestId": "request-mutation"}
        if target_tool == "vrcforge_restore_safe_backup":
            if restore_matches:
                scene.write_bytes(b"baseline-scene")
            return {
                "status": "applied",
                "result": {
                    "confirmed": True,
                    "project_identity_matches": True,
                    "restored": [scene_asset_path, scene_asset_path + ".meta"],
                    "summary": {"restoredCount": 2},
                },
            }, "approval-safe-restore", "checkpoint-safe-restore", {"requestId": "request-safe-restore"}
        if target_tool == "vrcforge_restore_checkpoint":
            scene.write_bytes(b"baseline-scene")
            return {
                "status": "applied",
                "result": {
                    "restored": True,
                    "status": "restored",
                    "unityReload": {
                        "ok": True,
                        "projectPath": str(tmp_path),
                        "scenes": [scene_asset_path],
                        "unityProcessId": 1,
                        "projectPathDigest": "a" * 64,
                    },
                },
            }, "approval-reload", "", {"requestId": "request-reload"}
        raise AssertionError(target_tool)

    def mcp_call(name: str, _arguments: dict[str, Any]) -> tuple[dict[str, Any], dict[str, Any]]:
        assert name == "vrcforge_preview_restore_backup"
        harness.actual_call_count += 1
        return {
            "confirmed": False,
            "summary": {"plannedCount": 1, "skippedCount": 1},
            "planned": [{"project_relative_path": scene_asset_path + ".meta"}],
            "skipped": [
                {
                    "project_relative_path": scene_asset_path,
                    "reason": "Current file differs from the backup",
                }
            ],
        }, {"requestId": "request-preview"}

    harness.approve_target_once = approve_target_once  # type: ignore[method-assign]
    harness._mcp_call = mcp_call  # type: ignore[method-assign]  # noqa: SLF001
    return harness, calls, scene_asset_path


def test_safe_backup_chain_creates_real_observed_restore_without_consuming_setup_catalog_evidence(
    live: ModuleType,
    tmp_path: Path,
) -> None:
    harness, calls, scene_path = build_safe_backup_chain_fake(live, tmp_path)
    result = harness.run_safe_backup_chain(
        scene_asset_path=scene_path,
        avatar_path="MatrixRoot/MatrixAvatar",
        outfit_path="MatrixRoot/MatrixAvatar/Outfit",
    )
    assert calls == [
        "vrcforge_create_safe_backup",
        "vrcforge_setup_outfit",
        "vrcforge_restore_safe_backup",
        "vrcforge_restore_checkpoint",
    ]
    assert result["cleanup"]["status"] == "passed"
    assert result["cleanup"]["kind"] == "safe_backup_chain_removed_backup"
    assert not (tmp_path / "Library" / "VRCForge" / "Backups" / "backup-1").exists()
    assert result["actualCallCount"] == 9
    assert harness.observed_results["vrc_restore_safe_backup"]["confirmed"] is True
    setup_evidence = [item for item in result["evidence"] if item["tool"] == "vrc_setup_outfit"]
    assert len(setup_evidence) == 1
    assert setup_evidence[0]["catalogEvidence"] is False


def test_safe_backup_chain_fails_if_backup_was_not_created(live: ModuleType, tmp_path: Path) -> None:
    harness, _calls, scene_path = build_safe_backup_chain_fake(live, tmp_path, create_valid=False)
    with pytest.raises(live.HarnessError, match="exact unchanged scene-plus-meta"):
        harness.run_safe_backup_chain(
            scene_asset_path=scene_path,
            avatar_path="MatrixRoot/MatrixAvatar",
            outfit_path="MatrixRoot/MatrixAvatar/Outfit",
        )


def test_safe_backup_chain_fails_if_restore_does_not_match_baseline(live: ModuleType, tmp_path: Path) -> None:
    harness, _calls, scene_path = build_safe_backup_chain_fake(live, tmp_path, restore_matches=False)
    with pytest.raises(live.HarnessError, match="exact scene and Assets baseline"):
        harness.run_safe_backup_chain(
            scene_asset_path=scene_path,
            avatar_path="MatrixRoot/MatrixAvatar",
            outfit_path="MatrixRoot/MatrixAvatar/Outfit",
        )


def test_observed_restore_without_chain_evidence_is_blocked(live: ModuleType, tmp_path: Path) -> None:
    harness = live.UnityMcp64LiveHarness(make_args(tmp_path, execute=True, allow_approved_writes=True))
    record = {"tool": "vrc_restore_safe_backup", "status": "planned", "error": ""}
    harness._run_special_or_block(record)  # noqa: SLF001
    assert record["status"] == "blocked"
    assert "has not been observed" in record["error"]


def test_primitive_report_file_cannot_count_as_catalog_success(live: ModuleType, tmp_path: Path) -> None:
    harness = live.UnityMcp64LiveHarness(make_args(tmp_path, execute=True))
    record = {
        "tool": "vrc_reload_primitive_basis_fixture",
        "successFields": {name: {"present": False, "value": None} for name in ("schema", "reloaded", "sceneDirty", "scenePath", "unityProcessId", "projectPathDigest")},
        "status": "planned", "error": "", "coreAudit": None,
    }
    harness._run_special_or_block(record)  # noqa: SLF001
    assert record["status"] == "blocked"
    assert "fixed primitive live runtime" in record["error"]


def test_profile_maps_the_exact_current_case_catalog_and_upload_is_reject_only(live: ModuleType, tmp_path: Path) -> None:
    harness = live.UnityMcp64LiveHarness(make_args(tmp_path))
    assert len(harness.cases) == EXPECTED_TOOL_COUNT
    assert set(harness.profile["caseMappings"]) == {case["tool"] for case in harness.cases}
    upload = harness.profile["caseMappings"]["vrc_build_and_upload_avatar"]
    assert upload["approvalTarget"] == "vrcforge_build_and_upload_avatar"
    assert upload["cleanup"] == "remote_reject"
    assert harness.profile["remoteUploadPolicy"] == "proposal_then_reject_only"
    build_test = harness.profile["caseMappings"]["vrc_build_test_avatar"]
    assert build_test["cleanup"] == "retain_disposable"
    prepared_targets = {
        "vrc_build_test_avatar": "vrcforge_build_test_avatar",
        "vrc_convert_unity_constraint": "vrcforge_convert_unity_constraint",
        "vrc_export_vrm": "vrcforge_export_vrm",
        "vrc_save_current_scene": "vrcforge_save_current_scene",
        "vrc_save_new_scene": "vrcforge_save_new_scene",
        "vrc_save_scene_object_as_prefab": "vrcforge_save_scene_object_as_prefab",
        "vrc_set_constraint_sources": "vrcforge_set_constraint_sources",
        "vrc_set_material_shader": "vrcforge_set_material_shader",
        "vrc_set_material_texture": "vrcforge_set_material_texture",
        "vrc_set_texture_import_settings": "vrcforge_set_texture_import_settings",
    }
    assert {
        tool: harness.profile["caseMappings"][tool]["approvalTarget"]
        for tool in prepared_targets
    } == prepared_targets
    package_import = harness.profile["caseMappings"]["vrc_import_unitypackage"]
    assert package_import["approvalTarget"] == "vrcforge_unity_mcp_write"
    assert package_import["pollExternalTool"] == "vrcforge_get_unitypackage_import_status"
    assert "renameArguments" not in package_import
    assert "dropArguments" not in package_import


def test_restore_safe_backup_special_route_runs_the_bound_chain_once(
    live: ModuleType, tmp_path: Path
) -> None:
    harness = live.UnityMcp64LiveHarness(make_args(tmp_path, execute=True, allow_approved_writes=True))
    harness.dynamic_context.update(
        {
            "SAFE_BACKUP_SCENE_ASSET_PATH": "Assets/Acceptance/Scene.unity",
            "SAFE_BACKUP_AVATAR_PATH": "MatrixRoot/MatrixAvatar",
            "SAFE_BACKUP_OUTFIT_PATH": "MatrixRoot/MatrixAvatar/Outfit",
        }
    )
    calls: list[dict[str, str]] = []

    def run_chain(**arguments: str) -> dict[str, Any]:
        calls.append(arguments)
        harness.observed_results["vrc_restore_safe_backup"] = {
            "confirmed": True,
            "project_identity_matches": True,
            "restored": ["Assets/Acceptance/Scene.unity"],
            "summary": {"restoredCount": 1},
        }
        return {"ok": True}

    harness.run_safe_backup_chain = run_chain  # type: ignore[method-assign]
    case = next(case for case in harness.cases if case["tool"] == "vrc_restore_safe_backup")
    record = harness._new_record(case)  # noqa: SLF001

    harness._run_special_or_block(record)  # noqa: SLF001

    assert calls == [
        {
            "scene_asset_path": "Assets/Acceptance/Scene.unity",
            "avatar_path": "MatrixRoot/MatrixAvatar",
            "outfit_path": "MatrixRoot/MatrixAvatar/Outfit",
        }
    ]
    assert record["status"] == "passed"


def test_remote_upload_case_is_rejected_without_approval_or_execution(live: ModuleType, tmp_path: Path) -> None:
    harness = live.UnityMcp64LiveHarness(make_args(tmp_path, execute=True, allow_approved_writes=True))
    harness.credentials = live.Credentials(gateway_token="gateway", app_token="app")
    approval_calls: list[str] = []
    rejection_calls: list[str] = []
    harness._request_apply = lambda _params: (  # type: ignore[method-assign]  # noqa: SLF001
        {
            "ok": True,
            "approval": {
                "id": "upload-approval",
                "status": "pending",
                "requiresExplicitApproval": True,
            },
        },
        {"requestId": "upload-request"},
    )
    harness._approve_once = lambda approval_id: approval_calls.append(approval_id)  # type: ignore[method-assign]  # noqa: SLF001
    harness._reject_once = lambda approval_id: (  # type: ignore[method-assign]  # noqa: SLF001
        rejection_calls.append(approval_id)
        or {"ok": True, "approval": {"id": approval_id, "status": "denied"}}
    )
    record = harness._new_record(  # noqa: SLF001
        next(case for case in harness.cases if case["tool"] == "vrc_build_and_upload_avatar")
    )

    harness._run_remote_upload_rejection(record, {"projectPath": str(tmp_path)})  # noqa: SLF001

    assert approval_calls == []
    assert rejection_calls == ["upload-approval"]
    assert record["status"] == "passed"
    assert record["verificationKind"] == "remote_upload_rejected"
    assert record["remoteUploadExecuted"] is False
    assert record["mutationStarted"] is False
    assert record["committed"] is False
    assert record["commitState"] == "not_started"


def test_fake_service_can_only_claim_80_with_unique_tool_and_request_evidence(
    live: ModuleType,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    cases = [
        {
            "id": f"case-{index}",
            "tool": f"tool_{index:02d}",
            "mode": "read",
            "arguments": {},
            "requiredFixtures": [],
            "runtimeInjectedArguments": [],
            "successFields": ["ok"],
            "cleanup": ["none"],
        }
        for index in range(80)
    ]
    monkeypatch.setattr(live.catalog, "load_catalog", lambda: copy_cases(cases))
    profile = {
        "schema": "vrcforge.unity_mcp_80_live_profile.v1",
        "caseMappings": {
        case["tool"]: {"externalTool": f"external_{case['tool']}"} for case in cases
        },
    }
    profile_path = tmp_path / "fake-profile.json"
    profile_path.write_text(json.dumps(profile), encoding="utf-8")
    harness = live.UnityMcp64LiveHarness(make_args(tmp_path, execute=True, profile_json=profile_path))
    harness._load_credentials = lambda: live.Credentials("gateway", "app")  # type: ignore[method-assign]  # noqa: SLF001
    harness._runtime_preflight = lambda: {"ok": True, "executionToolCount": 80}  # type: ignore[method-assign]  # noqa: SLF001
    sequence = iter(range(80))

    def fake_call(_name: str, _arguments: dict[str, Any]) -> tuple[dict[str, Any], dict[str, Any]]:
        index = next(sequence)
        harness.actual_call_count += 1
        tool_name = _name.removeprefix("external_")
        return {
            "ok": True,
            "_meta": {
                "io.vrcforge/callAudit": {
                    "requestId": f"core-request-{index:02d}",
                    "toolName": tool_name,
                }
            },
        }, {"requestId": f"gateway-request-{index:02d}"}

    harness._mcp_call = fake_call  # type: ignore[method-assign]  # noqa: SLF001
    report = harness.run()
    assert report["all80CasesPassed"] is True
    assert report["all80ToolsReturnedSuccess"] is True
    assert report["all64Passed"] is False
    assert report["evidenceToolCount"] == 80
    assert report["uniqueEvidenceCount"] == 80


def copy_cases(cases: list[dict[str, Any]]) -> list[dict[str, Any]]:
    return json.loads(json.dumps(cases))
