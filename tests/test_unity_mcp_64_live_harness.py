from __future__ import annotations

import argparse
import importlib.util
import json
import sys
from pathlib import Path
from types import ModuleType
from typing import Any

import pytest


ROOT = Path(__file__).resolve().parents[1]
HARNESS_PATH = ROOT / "artifacts" / "acceptance-harness" / "smoke_unity_mcp_64_live.py"
PROFILE_PATH = ROOT / "artifacts" / "acceptance-harness" / "mcp64-live-profile.json"

pytestmark = pytest.mark.skipif(
    not HARNESS_PATH.is_file(),
    reason="local Unity MCP live acceptance harness is not part of a clean source checkout",
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
    assert result["cleanup"]["kind"] == "safe_backup_chain_retained_audit"
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


def test_fake_service_can_only_claim_64_with_unique_tool_and_request_evidence(
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
        for index in range(64)
    ]
    monkeypatch.setattr(live.catalog, "load_catalog", lambda: copy_cases(cases))
    harness = live.UnityMcp64LiveHarness(make_args(tmp_path, execute=True))
    harness.profile["caseMappings"] = {
        case["tool"]: {"externalTool": f"external_{case['tool']}"} for case in cases
    }
    harness._load_credentials = lambda: live.Credentials("gateway", "app")  # type: ignore[method-assign]  # noqa: SLF001
    harness._runtime_preflight = lambda: {"ok": True, "executionToolCount": 64}  # type: ignore[method-assign]  # noqa: SLF001
    sequence = iter(range(64))

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
    assert report["all64Passed"] is True
    assert report["evidenceToolCount"] == 64
    assert report["uniqueEvidenceCount"] == 64


def copy_cases(cases: list[dict[str, Any]]) -> list[dict[str, Any]]:
    return json.loads(json.dumps(cases))
