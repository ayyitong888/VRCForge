"""Exact receipt reconciliation and owned-input release for package imports.

Only the owning approval service may finish a recovery. Verification opens
short-lived local read handles under its project lock. After durable verified
resolution it may release only the original sealed owned temporary inputs;
it performs no Core writes, restores, or approval transitions.
"""
from __future__ import annotations

import json
import copy
import hashlib
import re
import tarfile
import zipfile
from datetime import datetime
from pathlib import Path, PurePosixPath
from typing import Any

from approved_unity_execution import freeze_approved_unity_execution_plan, validate_frozen_approved_unity_execution_plan
from prepared_file_imports import capture_directory, capture_regular_file, verify_regular_file, verify_directory
from prepared_unity_execution import PREPARED_UNITY_EXECUTION_ARGUMENT_KEY, build_prepared_execution_plan, prepared_evidence
from prepared_outfit_import_workflow_service import PreparedOutfitImportApprovedWriteService, verify_unitypackage_asset_content


IMPORT_OPERATION_SCHEMA = "vrcforge.import-operation.v1"


def _action_context(context: dict) -> dict:
    return {key: context[key] for key in ("sessionId", "turnId", "actionId", "tool") if key in context}


def import_operation_evidence(arguments: dict) -> dict | None:
    """Recognize sealed package semantics, independently of the facade name."""
    if PREPARED_UNITY_EXECUTION_ARGUMENT_KEY not in arguments:
        return None
    calls = build_prepared_execution_plan(arguments)
    if not any(name == "vrc_import_unitypackage" for name, _ in calls):
        return None
    evidence = prepared_evidence(arguments)
    _require(isinstance(evidence, dict), "Import evidence is missing.")
    if evidence.get("branch") == "unitypackage":
        outer = evidence
        evidence = outer.get("outfitEvidence")
        _require(isinstance(evidence, dict), "Attachment package evidence is missing.")
        targets = [item.get("identity", {}).get("sha256") for item in evidence.get("queue", []) if item.get("role") == "target"]
        _require(targets == [outer.get("payloadHash")] and bool(outer.get("payloadHash")), "Attachment payload differs from its sealed package.")
    _require(evidence.get("kind") in {"unitypackage_import", "unitypackage_import_sequence"}
             and isinstance(evidence.get("queue"), list) and bool(evidence["queue"]), "Unsupported sealed import semantics.")
    return evidence


def make_import_operation_journal(recovery: dict, approval: dict, arguments: dict) -> dict | None:
    evidence = import_operation_evidence(arguments)
    if evidence is None:
        return None
    _require(recovery.get("approvalId") == approval.get("id") == recovery.get("operationId"), "Import owner differs from its approval.")
    frozen = validate_frozen_approved_unity_execution_plan(approval.get("approvedUnityExecutionPlan"))
    _require(freeze_approved_unity_execution_plan(build_prepared_execution_plan(arguments)) == frozen.as_json(), "Import journal plan differs from approval.")
    return {"schema": IMPORT_OPERATION_SCHEMA, "recoveryId": recovery["id"], "approvalId": approval["id"],
            "operationId": recovery["operationId"], "targetTool": approval["targetTool"],
            "projectRoot": recovery["projectRoot"], "taskContext": _action_context(approval.get("taskContext") or {}),
            "preparedSeal": copy.deepcopy(arguments[PREPARED_UNITY_EXECUTION_ARGUMENT_KEY]),
            "approvedUnityExecutionPlan": frozen.as_json(), "materializations": []}


def import_operation_journal_path(approval_id: str, audit_path: Path) -> Path:
    return audit_path.parent / "import-operations" / (hashlib.sha256(approval_id.encode()).hexdigest() + ".json")


def load_import_operation_journal(recovery: dict, audit_path: Path) -> dict | None:
    reference = recovery.get("importOperation")
    if reference is None:
        return None
    _require(isinstance(reference, dict) and reference.get("schema") == IMPORT_OPERATION_SCHEMA, "Invalid import operation reference.")
    path = import_operation_journal_path(str(recovery.get("approvalId") or ""), audit_path)
    _require(reference.get("journalFile") == path.name, "Import journal selector differs from its owner.")
    journal = json.loads(path.read_text(encoding="utf8"))
    _require(journal.get("schema") == IMPORT_OPERATION_SCHEMA and all(journal.get(key) == recovery.get(key)
             for key in ("approvalId", "operationId", "targetTool", "projectRoot"))
             and journal.get("recoveryId") == recovery.get("id"), "Import journal owner drifted.")
    _require(journal.get("preparedSeal", {}).get("sealSha256") == reference.get("sealSha256"), "Import source seal drifted.")
    import_operation_evidence({PREPARED_UNITY_EXECUTION_ARGUMENT_KEY: journal["preparedSeal"]})
    return journal


def _verify_operation_sources(journal: dict, package: str) -> list[dict]:
    """Revalidate original container identity/manifest and exact owned inputs."""
    from prepared_archive_imports import _entry_manifest, _opened_source_matches
    evidence = import_operation_evidence({PREPARED_UNITY_EXECUTION_ARGUMENT_KEY: journal["preparedSeal"]})
    queue = evidence["queue"]
    _require(len(queue) == 1 and _same_path(queue[0].get("identity", {}).get("path"), package), "Recovery source queue is not the exact single package.")
    item = queue[0]
    identity = item["identity"]
    facts = evidence.get("materializations") or []
    receipts = journal.get("materializations") or []
    index = item.get("materializationIndex")
    if index is None:
        _require(not facts and not receipts, "Direct import unexpectedly owns temporary sources.")
        if item.get("folderRootIdentity") is not None:
            verify_directory(item["folderRootIdentity"], label="Recovery source folder")
        verify_regular_file({k: v for k, v in identity.items() if k != "sha256"}, identity["sha256"], label="Recovery original package")
        return []
    _require(type(index) is int and index == 0 and len(facts) == len(receipts) == 1, "Materialization ownership is incomplete.")
    fact, receipt = facts[0], receipts[0]
    source = verify_regular_file(fact["sourceIdentity"], fact["sourceSha256"], label="Recovery original archive")
    parent = verify_directory(fact["tempParent"], label="Recovery materialization parent")
    selected = fact["selected"]
    _require(len(selected) == 1 and receipt.get("schema") == "vrcforge.zip-materialization-receipt.v1"
             and len(receipt.get("ownedFiles", [])) == 1, "Materialization receipt is incomplete.")
    with source.open("rb") as handle:
        _opened_source_matches(handle, fact["sourceIdentity"], fact["sourceSha256"])
        with zipfile.ZipFile(handle) as archive:
            actual = _entry_manifest(archive, selected_paths={entry["path"] for entry in selected}, max_uncompressed_bytes=None)
    _require(actual == fact["manifest"], "Recovery original archive manifest changed.")
    member, owned = selected[0], receipt["ownedFiles"][0]
    _require(_same_path(parent / member["targetName"], package) and _same_path(owned.get("path"), package)
             and owned.get("sha256") == member["sha256"] == identity["sha256"]
             and owned.get("size") == member["size"] == identity["size"], "Owned source differs from its sealed archive member.")
    verify_regular_file({k: v for k, v in owned.items() if k != "sha256"}, owned["sha256"], label="Recovery owned package")
    # Recheck the original container after reading its manifest as well.
    verify_regular_file(fact["sourceIdentity"], fact["sourceSha256"], label="Recovery original archive")
    return receipts


def release_verified_import_sources(recovery: dict, audit_path: Path) -> dict:
    """Release only the original approved owned inputs after durable resolution.

    A changed source or cleanup failure retains files. Original user sources
    never appear in ownedFiles and are never cleanup candidates.
    """
    from prepared_archive_imports import cleanup_owned_zip_materialization
    _require(recovery.get("status") == "resolved" and recovery.get("resolution") == "verified_partial_import"
             and (recovery.get("resultSummary") or {}).get("verified") is True, "Source owner has not finished verification.")
    journal = load_import_operation_journal(recovery, audit_path)
    if journal is None:
        return {"performed": False, "required": False}
    call = build_prepared_execution_plan({PREPARED_UNITY_EXECUTION_ARGUMENT_KEY: journal["preparedSeal"]})[0][1]
    receipts = _verify_operation_sources(journal, call["unityPackagePath"])
    errors = [error for receipt in reversed(receipts) if (error := cleanup_owned_zip_materialization(receipt))]
    return {"performed": bool(receipts) and not errors, "required": bool(errors), "errors": errors}


def _require(condition: bool, message: str) -> None:
    if not condition:
        raise ValueError(message)


def _same_path(left: Any, right: Any) -> bool:
    return bool(left and right) and Path(str(left)).resolve() == Path(str(right)).resolve()


def _host_entries(path: Path, contains: str):
    with path.open(encoding="utf-8") as source:
        for line in source:
            if contains in line:
                yield json.loads(line)


def _time(value: Any) -> datetime:
    return datetime.fromisoformat(str(value).replace("Z", "+00:00"))


def verify_interrupted_package_import(recovery: dict, status: dict, *, audit_path: Path, runtime_path: Path) -> dict:
    """Raise on absent/ambiguous evidence; return proof of a known partial commit.

    Audit arguments may be deliberately abbreviated. Reconstruct Core arguments
    from its full receipt and require the original frozen plan hash to match,
    rather than interpreting a truncated source path as an approved path.
    """
    journal = load_import_operation_journal(recovery, audit_path)
    # Old direct-import incidents keep their exact host receipt contract. New
    # facade callers enter by their host-sealed operation, never a tool allowlist.
    _require(journal is not None or recovery.get("targetTool") == "vrcforge_import_outfit_package", "Not a sealed package-import recovery.")
    _require(recovery.get("status") == "needs_recovery" and recovery.get("resolution") == "write_failed_after_checkpoint"
             and not recovery.get("restoreAttemptCount") and not recovery.get("restoreSafetyCheckpointId")
             and not recovery.get("restoreSafetyCheckpointIds"), "Recovery already entered another recovery or restore flow.")
    approval_id = recovery.get("approvalId")
    _require(bool(approval_id) and recovery.get("operationId") == approval_id, "Recovery operation differs from its approval.")
    _require(status.get("ok") is True and status.get("pending") is False
             and status.get("status") == "completed" and status.get("committed") is True
             and status.get("commitState") == "complete" and status.get("checkpointRecoveryRequired") is False
             and not status.get("error") and not status.get("errorCode"), "Import has no successful terminal receipt.")
    job_id = status.get("jobId")
    _require(isinstance(job_id, str) and re.fullmatch(r"[0-9a-f]{32}", job_id) is not None, "Invalid import job identity.")
    _require(_same_path(status.get("projectPath"), recovery.get("projectRoot")), "Receipt belongs to another project.")
    records = [entry for entry in _host_entries(audit_path, str(approval_id))
               if isinstance(entry.get("approval"), dict) and entry["approval"].get("id") == approval_id]
    _require(bool(records), "Host approval audit is missing.")
    failed = [entry["approval"] for entry in records if entry.get("event") == "approval_failed"]
    _require(len(failed) == 1 and records[-1].get("event") == "approval_failed", "Approval failure is ambiguous or no longer current.")
    approval = failed[0]
    _require(approval.get("status") == "failed" and approval.get("targetTool") == recovery["targetTool"]
             and _same_path(approval.get("projectRoot"), recovery["projectRoot"]), "Approval scope differs from recovery.")
    context = approval.get("taskContext") or {}
    if journal is None:
        _require(all(isinstance(context.get(key), str) and context[key] for key in ("sessionId", "turnId", "actionId"))
                 and context.get("tool") == recovery["targetTool"], "Host action identity is incomplete.")
    else:
        _require(journal.get("taskContext") == _action_context(context), "Journal host action differs from approval.")
    approved = [entry["approval"] for entry in records if entry.get("event") in {"approval_approved", "approval_applying"}]
    _require({"approval_approved", "approval_applying"}.issubset({entry.get("event") for entry in records})
             and all(item.get("approvedUnityExecutionPlan") == approval.get("approvedUnityExecutionPlan")
             and all((item.get("taskContext") or {}).get(key) == context.get(key) for key in ("sessionId", "turnId", "actionId", "tool"))
             for item in approved), "Approved plan or host action drifted.")
    _require((approval.get("resultSummary") or {}).get("jobId") == job_id, "Failed approval did not witness this job.")
    frozen = validate_frozen_approved_unity_execution_plan(approval.get("approvedUnityExecutionPlan"))
    _require([call.tool_name for call in frozen.calls] == ["vrc_import_unitypackage", "vrc_refresh_asset_database"],
             "Recovery requires exactly one import and its known refresh tail.")
    paths = status.get("expectedAssetPaths")
    _require(isinstance(paths, list) and 0 < len(paths) <= 4096 and len(set(paths)) == len(paths), "Expected asset scope is incomplete.")
    for name in paths:
        _require(isinstance(name, str) and name.startswith("Assets/") and "\\" not in name and ":" not in name
                 and "\x00" not in name and all(part not in {"", ".", ".."} for part in name.split("/"))
                 and PurePosixPath(name).as_posix() == name, "Asset scope escaped canonical Assets paths.")
    project = str(Path(status["projectPath"]))
    package = str(Path(status["unityPackagePath"]))
    call = {"projectPath": project, "unityPackagePath": package, "expectedSha256": status.get("expectedSha256"),
            "expectedSize": status.get("expectedSize"), "expectedAssetPaths": paths, "interactive": False}
    refresh = {"projectPath": project, "resolvePackages": False, "packageResolveTimeoutSeconds": 120}
    reconstructed = freeze_approved_unity_execution_plan([("vrc_import_unitypackage", call), ("vrc_refresh_asset_database", refresh)])
    _require(reconstructed == frozen.as_json(), "Terminal receipt differs from the exact approved Core calls.")
    if journal is None:
        host_steps = []
        for event in _host_entries(runtime_path, context["actionId"]):
            if event.get("event") not in {"runtime_turn_completed", "runtime_turn_failed", "runtime_turn_cancelled", "runtime_turn_interrupted"}:
                continue
            if event.get("sessionId") != context["sessionId"] or event.get("turnId") != context["turnId"]:
                continue
            host_steps.extend(step for step in event.get("steps", []) if isinstance(step, dict) and step.get("actionId") == context["actionId"])
        _require(len(host_steps) == 1, "Host import action receipt is missing or ambiguous.")
        step = host_steps[0]
        result = step.get("result") or {}
        state = result.get("unityJobState") or {}
        _require(step.get("tool") == recovery["targetTool"] and step.get("status") == "failed"
                 and result.get("jobId") == job_id and state.get("jobId") == job_id
                 and state.get("startedForThisJob") is True and result.get("failureLayer") == "unity_core_job_polling"
                 and not result.get("unityImports"), "Host receipt is not this interrupted single import.")
    else:
        _require(journal.get("approvedUnityExecutionPlan") == frozen.as_json()
                 and freeze_approved_unity_execution_plan(build_prepared_execution_plan(
                     {PREPARED_UNITY_EXECUTION_ARGUMENT_KEY: journal["preparedSeal"]})) == frozen.as_json(), "Persisted import plan drifted.")
        initial = journal.get("jobReceipt") or {}
        result = journal.get("handlerResult") or {}
        _require(journal.get("activeImportIndex") == 0 and journal.get("importDispatched") is True
                 and initial.get("jobId") == job_id and result.get("jobId") == job_id
                 and result.get("ok") is False and result.get("failureLayer") == "unity_core_job_polling"
                 and not result.get("unityImports"), "Journal does not witness this interrupted single import.")
        PreparedOutfitImportApprovedWriteService._job_receipt(initial, {"projectPath": status["projectPath"]},
            {"path": status["unityPackagePath"], "sha256": status["expectedSha256"], "size": status["expectedSize"]}, paths)
        _verify_operation_sources(journal, package)
    _require(_time(approval["approvedAt"]) <= _time(status["createdUtc"]) <= _time(approval["failedAt"])
             and _time(status["createdUtc"]) <= _time(status["completedUtc"]), "Import job time does not belong to this approval.")
    identity = {"path": package, "sha256": call["expectedSha256"], "size": call["expectedSize"]}
    # Existing production validators retain their path/hash/size and GUID/type requirements.
    PreparedOutfitImportApprovedWriteService._job_receipt(status, {"projectPath": status["projectPath"]},
                                                        {**identity, "path": status["unityPackagePath"]}, paths)
    assets = PreparedOutfitImportApprovedWriteService._asset_receipts(status, paths)
    before, digest = capture_regular_file(Path(package), label="Recovery import source")
    _require(digest == identity["sha256"] and before["size"] == identity["size"], "Import source bytes changed.")
    content = verify_unitypackage_asset_content(package, project, paths,
        lambda path, label: capture_regular_file(path, label=label),
        lambda path, label: capture_directory(path, label=label))
    # Verify receipt GUIDs against both the package and the persisted metadata.
    source_guids = {}
    with tarfile.open(package, "r:*") as archive:
        for member in archive.getmembers():
            if member.isfile() and member.name.endswith("/pathname"):
                name = archive.extractfile(member).read(32769).decode("utf-8").strip()
                _require(name not in source_guids, "Duplicate package asset paths.")
                meta = archive.extractfile(member.name.rsplit("/", 1)[0] + "/asset.meta").read(32769)
                match = re.search(rb"(?m)^guid: ([0-9a-f]{32})\s*$", meta)
                _require(match is not None, "Source asset GUID is missing.")
                source_guids[name] = match.group(1).decode("ascii")
    _require(set(source_guids) == set(paths), "The expected paths do not cover the complete imported package.")
    for asset in assets:
        meta_path = Path(project) / (asset["assetPath"] + ".meta")
        capture_regular_file(meta_path, label="Recovery asset metadata")
        match = re.search(rb"(?m)^guid: ([0-9a-f]{32})\s*$", meta_path.read_bytes())
        _require(match is not None and match.group(1).decode("ascii") == asset["guid"] == source_guids.get(asset["assetPath"]),
                 "Persisted asset GUID differs from its source or Core receipt.")
    after, final_digest = capture_regular_file(Path(package), label="Recovery import source")
    _require(before == after and digest == final_digest and content.get("verified") is True, "Source changed during recovery verification.")
    return {"schema": "vrcforge.verified_partial_import.v1", "jobId": job_id, "operationId": approval_id,
            "approvalId": approval_id, "sessionId": context.get("sessionId", ""), "turnId": context.get("turnId", ""), "actionId": context.get("actionId", ""),
            "planDigest": frozen.plan_digest, "verified": True, "workflowComplete": False, "originalApprovalStatus": "failed",
            "assetCount": len(assets), "contentReadback": {"verified": True, "checked": content["checked"]},
            "remainingPostImportSteps": ["asset_database_refresh", "temporary_materialization_cleanup_if_present"],
            "sourceCleanupPerformed": False}
