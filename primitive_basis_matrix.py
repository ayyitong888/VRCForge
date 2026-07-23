from __future__ import annotations

import hashlib
import json
import os
import re
import stat
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from pathlib import Path, PurePosixPath
from typing import Any, Mapping

from diagnostic_privacy import redact_public_evidence


MATRIX_SCHEMA = "vrcforge.primitive_basis_matrix.v1"
FIXTURE_SCHEMA = "vrcforge.primitive_basis_fixture.v1"
BASELINE_SCHEMA = "vrcforge.primitive_basis_baseline.v1"
EVIDENCE_SCHEMA = "vrcforge.primitive_basis_evidence.v1"
RECEIPT_SCHEMA = "vrcforge.primitive_basis_phase_receipt.v1"

REQUIRED_PHASES = (
    "detect",
    "preview",
    "request",
    "approval",
    "checkpoint",
    "apply",
    "readback",
    "validation",
    "restore_request",
    "restore_approval",
    "restore_execution",
    "baseline_comparison",
    "residue",
)

SCENARIO_DEFINITIONS: dict[str, tuple[str, ...]] = {
    "component_feature_application": (
        "typed_component_list_write",
        "feature_component_write",
    ),
    "parameter_optimization": ("parameter_bit_pack",),
    "cross_avatar_accessory_copy": (
        "duplicate_scene_object",
        "save_scene_object_as_prefab",
    ),
    "model_part_composition": ("non_destructive_part_composition",),
}
SCENARIO_ORDER = tuple(SCENARIO_DEFINITIONS)

_FIXTURE_FIELDS = {
    "schema",
    "scenarioId",
    "fixtureRoot",
    "baselineManifest",
    "expectedBaselineDigest",
    "expectedTreeDigest",
    "requiredPrimitives",
}
_BASELINE_FIELDS = {"schema", "scenarioId", "files"}
_BASELINE_FILE_FIELDS = {"path", "size", "sha256"}
_EVIDENCE_FIELDS = {"schema", "runId", "rows"}
_ROW_FIELDS = {"scenarioId", "primitiveId", "fixtureDigest", "phases"}
_PHASE_REFERENCE_FIELDS = {"receipt"}
_RECEIPT_REFERENCE_FIELDS = {"relativePath", "sha256"}
_RECEIPT_FIELDS = {
    "schema",
    "runId",
    "scenarioId",
    "primitiveId",
    "phase",
    "sequence",
    "observedAt",
    "fixtureDigest",
    "runtimeBindingDigest",
    "status",
    "facts",
}
_PHASE_STATUSES = {"passed", "failed", "blocked", "not_run"}
_SHA256_RE = re.compile(r"[0-9a-f]{64}")
_SAFE_ID_RE = re.compile(r"[A-Za-z0-9][A-Za-z0-9._-]{0,127}")
_VERSION_RE = re.compile(r"[0-9]+\.[0-9]+\.[0-9]+(?:-[0-9A-Za-z.-]+)?")
_MAX_JSON_BYTES = 4 * 1024 * 1024
_MAX_RECEIPT_AGE = timedelta(hours=24)
_MAX_RECEIPT_FUTURE_SKEW = timedelta(minutes=5)
_FACT_FIELDS: dict[str, set[str]] = {
    "detect": {"stateDigest"},
    "preview": {"mutationCount"},
    "request": {"requestId", "state"},
    "approval": {"requestId", "approvalId", "approved"},
    "checkpoint": {"approvalId", "checkpointId", "created"},
    "apply": {"checkpointId", "applied"},
    "readback": {"matched", "stateDigest"},
    "validation": {"passed", "reportDigest"},
    "restore_request": {"requestId", "state"},
    "restore_approval": {"requestId", "approvalId", "approved"},
    "restore_execution": {"approvalId", "restored"},
    "baseline_comparison": {"matched", "stateDigest"},
    "residue": {"count"},
}


class MatrixContractError(ValueError):
    """The fixed matrix contract or supplied evidence is invalid or unsafe."""


@dataclass(frozen=True)
class PrimitiveFixture:
    scenario_id: str
    fixture_root: str
    baseline_manifest: str
    required_primitives: tuple[str, ...]
    descriptor_digest: str
    digest: str
    materialized: bool
    materialization_error: str
    source_name: str


@dataclass(frozen=True)
class FixtureSet:
    fixtures: tuple[PrimitiveFixture, ...]
    descriptor_digest: str
    digest: str


@dataclass(frozen=True)
class VerificationContext:
    repository_root: Path
    receipts_root: Path
    release_manifest_path: Path
    executable_path: Path


@dataclass(frozen=True)
class RuntimeBinding:
    files_hashed: bool
    version: str
    release_manifest_digest: str
    executable_digest: str
    digest: str
    reasons: tuple[str, ...]


def load_fixture_set(
    directory: Path | str,
    *,
    repository_root: Path | str | None = None,
) -> FixtureSet:
    fixture_dir = Path(directory)
    expected_names = {f"{scenario_id}.json" for scenario_id in SCENARIO_ORDER}
    actual_names = {path.name for path in fixture_dir.glob("*.json") if path.is_file()}
    if actual_names != expected_names:
        raise MatrixContractError("fixture file set drifted")

    repo_root = Path(repository_root).resolve() if repository_root is not None else None
    if repo_root is not None and not repo_root.is_dir():
        raise MatrixContractError("repository root is unavailable")

    fixtures: list[PrimitiveFixture] = []
    for scenario_id in SCENARIO_ORDER:
        source = fixture_dir / f"{scenario_id}.json"
        payload = _read_json_object(source, "fixture")
        _require_public_safe(payload)
        _require_exact_fields(payload, _FIXTURE_FIELDS, "fixture")
        if payload.get("schema") != FIXTURE_SCHEMA:
            raise MatrixContractError("fixture schema drifted")
        if payload.get("scenarioId") != scenario_id:
            raise MatrixContractError("fixture scenario id drifted")

        expected_root = f"Assets/VRCForge/PrimitiveBasis/{scenario_id}"
        fixture_root = _require_string(payload.get("fixtureRoot"), "fixture root")
        if fixture_root != expected_root:
            raise MatrixContractError("fixture root drifted")
        baseline_manifest = _require_string(
            payload.get("baselineManifest"), "baseline manifest"
        )
        if baseline_manifest != "baseline.json":
            raise MatrixContractError("baseline manifest drifted")

        primitives_payload = payload.get("requiredPrimitives")
        if not isinstance(primitives_payload, list) or not all(
            isinstance(item, str) and item for item in primitives_payload
        ):
            raise MatrixContractError("required primitives must be a string list")
        required_primitives = tuple(primitives_payload)
        if required_primitives != SCENARIO_DEFINITIONS[scenario_id]:
            raise MatrixContractError("required primitives drifted")

        descriptor_digest = _sha256_json(payload)
        expected_baseline_digest = payload.get("expectedBaselineDigest")
        expected_tree_digest = payload.get("expectedTreeDigest")
        if (expected_baseline_digest == "") != (expected_tree_digest == ""):
            raise MatrixContractError("fixture digest pins must be both set or both empty")
        if expected_baseline_digest:
            _require_sha256(expected_baseline_digest, "expected baseline digest")
            _require_sha256(expected_tree_digest, "expected tree digest")
        fixture_digest, materialization_error = _materialize_fixture_digest(
            repo_root,
            fixture_root,
            baseline_manifest,
            scenario_id,
            descriptor_digest,
            str(expected_baseline_digest),
            str(expected_tree_digest),
        )
        fixtures.append(
            PrimitiveFixture(
                scenario_id=scenario_id,
                fixture_root=fixture_root,
                baseline_manifest=baseline_manifest,
                required_primitives=required_primitives,
                descriptor_digest=descriptor_digest,
                digest=fixture_digest,
                materialized=bool(fixture_digest),
                materialization_error=materialization_error,
                source_name=source.name,
            )
        )

    descriptor_set_payload = [
        {"scenarioId": fixture.scenario_id, "descriptorDigest": fixture.descriptor_digest}
        for fixture in fixtures
    ]
    descriptor_set_digest = _sha256_json(descriptor_set_payload)
    materialized = all(fixture.materialized for fixture in fixtures)
    fixture_set_digest = (
        _sha256_json(
            [
                {"scenarioId": fixture.scenario_id, "digest": fixture.digest}
                for fixture in fixtures
            ]
        )
        if materialized
        else ""
    )
    return FixtureSet(tuple(fixtures), descriptor_set_digest, fixture_set_digest)


def derive_runtime_binding(
    verification: VerificationContext | None,
) -> RuntimeBinding:
    if verification is None:
        return RuntimeBinding(False, "", "", "", "", ("runtime_verifier_missing",))
    try:
        manifest_path = _require_regular_file(verification.release_manifest_path)
        executable_path = _require_regular_file(verification.executable_path)
        manifest_bytes = _read_stable_bytes(manifest_path, maximum_size=_MAX_JSON_BYTES)
        manifest = _parse_json_object(manifest_bytes, "release manifest")
        _require_public_safe(manifest)
        version = manifest.get("version")
        if not isinstance(version, str) or _VERSION_RE.fullmatch(version) is None:
            return RuntimeBinding(False, "", "", "", "", ("runtime_version_invalid",))
        manifest_digest = hashlib.sha256(manifest_bytes).hexdigest()
        executable_digest = _sha256_file(executable_path)
        digest = _sha256_json(
            {
                "version": version,
                "releaseManifestDigest": manifest_digest,
                "executableDigest": executable_digest,
            }
        )
        return RuntimeBinding(
            True,
            version,
            manifest_digest,
            executable_digest,
            digest,
            (),
        )
    except MatrixContractError:
        return RuntimeBinding(False, "", "", "", "", ("runtime_binding_invalid",))


def build_report(
    fixtures: FixtureSet,
    evidence: Mapping[str, Any],
    *,
    verification: VerificationContext | None = None,
) -> dict[str, Any]:
    if not isinstance(evidence, Mapping):
        raise MatrixContractError("evidence must be an object")
    _require_public_safe(evidence)
    _require_exact_fields(evidence, _EVIDENCE_FIELDS, "evidence")
    if evidence.get("schema") != EVIDENCE_SCHEMA:
        raise MatrixContractError("evidence schema mismatch")
    run_id = _require_safe_id(evidence.get("runId"), "run id")
    verified_at = datetime.now(timezone.utc)

    supplied_rows = evidence.get("rows")
    if not isinstance(supplied_rows, list):
        raise MatrixContractError("rows must be a list")
    indexed_rows = _index_rows(supplied_rows, fixtures)
    _validate_unique_receipt_references(indexed_rows)

    runtime_binding = derive_runtime_binding(verification)
    receipt_root = verification.receipts_root if verification is not None else None
    row_results: list[dict[str, Any]] = []
    for fixture in fixtures.fixtures:
        for primitive_id in fixture.required_primitives:
            key = (fixture.scenario_id, primitive_id)
            row_results.append(
                _evaluate_row(
                    indexed_rows.get(key),
                    fixture,
                    primitive_id,
                    run_id=run_id,
                    runtime_binding=runtime_binding,
                    receipts_root=receipt_root,
                    verified_at=verified_at,
                )
            )
    _enforce_transaction_id_uniqueness(row_results)

    scenario_results: list[dict[str, Any]] = []
    for fixture in fixtures.fixtures:
        scenario_rows = [
            row for row in row_results if row["scenarioId"] == fixture.scenario_id
        ]
        statuses = [str(row["status"]) for row in scenario_rows]
        if statuses and all(status == "full" for status in statuses):
            status = "full"
        elif statuses and all(status == "blocked" for status in statuses):
            status = "blocked"
        else:
            status = "partial"
        scenario_results.append(
            {
                "scenarioId": fixture.scenario_id,
                "status": status,
                "fixtureDigest": fixture.digest,
                "requiredPrimitives": list(fixture.required_primitives),
            }
        )

    summary = _build_summary(scenario_results, row_results)
    report = {
        "schema": MATRIX_SCHEMA,
        "ok": summary["status"] == "full",
        "generatedAt": verified_at.isoformat().replace("+00:00", "Z"),
        "fixtureSetDescriptorDigest": fixtures.descriptor_digest,
        "fixtureSetDigest": fixtures.digest,
        "runtimeBinding": {
            "filesHashed": runtime_binding.files_hashed,
            "liveRunnerAttested": False,
            "version": runtime_binding.version,
            "releaseManifestDigest": runtime_binding.release_manifest_digest,
            "executableDigest": runtime_binding.executable_digest,
            "digest": runtime_binding.digest,
            "reasons": list(runtime_binding.reasons),
        },
        "runId": run_id,
        "fixtures": [
            {
                "scenarioId": fixture.scenario_id,
                "source": fixture.source_name,
                "descriptorDigest": fixture.descriptor_digest,
                "digest": fixture.digest,
                "materialized": fixture.materialized,
                "materializationError": fixture.materialization_error,
                "requiredPrimitives": list(fixture.required_primitives),
            }
            for fixture in fixtures.fixtures
        ],
        "rows": row_results,
        "scenarios": scenario_results,
        "summary": summary,
    }
    _require_public_safe(report)
    return report


def _materialize_fixture_digest(
    repository_root: Path | None,
    fixture_root: str,
    baseline_manifest: str,
    scenario_id: str,
    descriptor_digest: str,
    expected_baseline_digest: str,
    expected_tree_digest: str,
) -> tuple[str, str]:
    if not expected_baseline_digest or not expected_tree_digest:
        return "", "fixture_digest_unpinned"
    if repository_root is None:
        return "", "fixture_verifier_missing"
    root = _resolve_project_relative(repository_root, fixture_root)
    if not root.is_dir():
        return "", "fixture_root_missing"
    if _is_reparse_point(root):
        raise MatrixContractError("fixture root cannot be a link")
    baseline_path = root / baseline_manifest
    if not baseline_path.is_file():
        return "", "fixture_baseline_missing"
    if _is_reparse_point(baseline_path):
        raise MatrixContractError("fixture baseline cannot be a link")

    baseline = _read_json_object(baseline_path, "fixture baseline")
    _require_public_safe(baseline)
    _require_exact_fields(baseline, _BASELINE_FIELDS, "fixture baseline")
    if baseline.get("schema") != BASELINE_SCHEMA:
        raise MatrixContractError("fixture baseline schema mismatch")
    if baseline.get("scenarioId") != scenario_id:
        raise MatrixContractError("fixture baseline scenario mismatch")
    entries = baseline.get("files")
    if not isinstance(entries, list):
        raise MatrixContractError("fixture baseline files must be a list")

    declared: list[dict[str, Any]] = []
    previous_path = ""
    for entry in entries:
        if not isinstance(entry, Mapping):
            raise MatrixContractError("fixture baseline entry must be an object")
        _require_exact_fields(entry, _BASELINE_FILE_FIELDS, "fixture baseline entry")
        relative_path = _require_safe_relative_path(entry.get("path"), "fixture file")
        if previous_path and relative_path <= previous_path:
            raise MatrixContractError("fixture baseline files must be sorted and unique")
        previous_path = relative_path
        size = entry.get("size")
        if type(size) is not int or size < 0:
            raise MatrixContractError("fixture file size is invalid")
        digest = _require_sha256(entry.get("sha256"), "fixture file digest")
        declared.append({"path": relative_path, "size": size, "sha256": digest})

    actual_paths = _inventory_fixture_files(root, baseline_manifest)
    if [item["path"] for item in actual_paths] != [item["path"] for item in declared]:
        raise MatrixContractError("fixture file inventory mismatch")
    for expected, actual in zip(declared, actual_paths, strict=True):
        if expected != actual:
            raise MatrixContractError("fixture file digest mismatch")

    baseline_digest = _sha256_json(baseline)
    tree_digest = _sha256_json(actual_paths)
    if baseline_digest != expected_baseline_digest or tree_digest != expected_tree_digest:
        raise MatrixContractError("fixture content does not match pinned digests")
    fixture_digest = _sha256_json(
        {
            "descriptorDigest": descriptor_digest,
            "baselineDigest": baseline_digest,
            "treeDigest": tree_digest,
        }
    )
    return fixture_digest, ""


def _inventory_fixture_files(root: Path, baseline_manifest: str) -> list[dict[str, Any]]:
    inventory: list[dict[str, Any]] = []
    for current_root, directory_names, file_names in os.walk(root, followlinks=False):
        current = Path(current_root)
        for directory_name in list(directory_names):
            directory = current / directory_name
            if _is_reparse_point(directory):
                raise MatrixContractError("fixture tree cannot contain links")
        for file_name in file_names:
            path = current / file_name
            if _is_reparse_point(path) or not path.is_file():
                raise MatrixContractError("fixture tree cannot contain links")
            relative = path.relative_to(root).as_posix()
            if relative == baseline_manifest:
                continue
            digest, size = _stable_file_fingerprint(path)
            inventory.append(
                {
                    "path": _require_safe_relative_path(relative, "fixture file"),
                    "size": size,
                    "sha256": digest,
                }
            )
    return sorted(inventory, key=lambda item: item["path"])


def _index_rows(
    rows: list[Any], fixtures: FixtureSet
) -> dict[tuple[str, str], Mapping[str, Any]]:
    expected_keys = {
        (fixture.scenario_id, primitive_id)
        for fixture in fixtures.fixtures
        for primitive_id in fixture.required_primitives
    }
    indexed: dict[tuple[str, str], Mapping[str, Any]] = {}
    for index, row in enumerate(rows):
        if not isinstance(row, Mapping):
            raise MatrixContractError("evidence row must be an object")
        _require_exact_fields(row, _ROW_FIELDS, "evidence row")
        scenario_id = row.get("scenarioId")
        primitive_id = row.get("primitiveId")
        if not isinstance(scenario_id, str) or not isinstance(primitive_id, str):
            raise MatrixContractError("evidence row identity is invalid")
        key = (scenario_id, primitive_id)
        if key not in expected_keys:
            raise MatrixContractError(f"unknown evidence row at index {index}")
        if key in indexed:
            raise MatrixContractError("duplicate evidence row")
        _require_sha256(row.get("fixtureDigest"), "fixture digest")
        phases = row.get("phases")
        if not isinstance(phases, Mapping):
            raise MatrixContractError("phases must be an object")
        extra_phases = set(phases) - set(REQUIRED_PHASES)
        if extra_phases:
            raise MatrixContractError("unsupported evidence phase")
        for phase_payload in phases.values():
            _validate_phase_reference_shape(phase_payload)
        indexed[key] = row
    return indexed


def _validate_phase_reference_shape(value: Any) -> None:
    if not isinstance(value, Mapping):
        raise MatrixContractError("phase reference must be an object")
    _require_exact_fields(value, _PHASE_REFERENCE_FIELDS, "phase reference")
    receipt = value.get("receipt")
    if not isinstance(receipt, Mapping):
        raise MatrixContractError("receipt reference must be an object")
    _require_exact_fields(receipt, _RECEIPT_REFERENCE_FIELDS, "receipt reference")
    _require_safe_relative_path(receipt.get("relativePath"), "receipt")
    _require_sha256(receipt.get("sha256"), "receipt digest")


def _validate_unique_receipt_references(
    rows: Mapping[tuple[str, str], Mapping[str, Any]],
) -> None:
    paths: set[str] = set()
    digests: set[str] = set()
    for row in rows.values():
        phases = row["phases"]
        for phase_payload in phases.values():
            receipt = phase_payload["receipt"]
            relative_path = str(receipt["relativePath"])
            digest = str(receipt["sha256"])
            if relative_path in paths or digest in digests:
                raise MatrixContractError("duplicate receipt reference")
            paths.add(relative_path)
            digests.add(digest)


def _evaluate_row(
    row: Mapping[str, Any] | None,
    fixture: PrimitiveFixture,
    primitive_id: str,
    *,
    run_id: str,
    runtime_binding: RuntimeBinding,
    receipts_root: Path | None,
    verified_at: datetime,
) -> dict[str, Any]:
    result: dict[str, Any] = {
        "scenarioId": fixture.scenario_id,
        "primitiveId": primitive_id,
        "status": "blocked",
        "fixtureDigest": fixture.digest,
        "receipts": [],
        "reasons": [],
    }
    reasons: list[str] = result["reasons"]
    # The first contract slice validates integrity but cannot establish that
    # receipts originated from a packaged live-project run. The live runner
    # must add and verify that provenance before any row can become FULL.
    reasons.append("live_runner_attestation_missing")
    provenance_blocked = True
    if not fixture.materialized:
        reasons.append(fixture.materialization_error or "fixture_not_materialized")
    if not runtime_binding.files_hashed:
        reasons.extend(runtime_binding.reasons)
    if receipts_root is None:
        reasons.append("receipt_verifier_missing")
    if row is None:
        reasons.append("evidence_row_missing")
        return result
    if row.get("fixtureDigest") != fixture.digest or not fixture.digest:
        reasons.append("fixture_digest_mismatch")

    phases = row["phases"]
    receipts: dict[str, dict[str, Any]] = {}
    receipt_blocked = False
    for sequence, phase in enumerate(REQUIRED_PHASES, start=1):
        phase_payload = phases.get(phase)
        if phase_payload is None:
            reasons.append(f"receipt_missing:{phase}")
            receipt_blocked = True
            continue
        if not fixture.materialized or not runtime_binding.files_hashed or receipts_root is None:
            receipt_blocked = True
            continue
        try:
            receipt = _verify_receipt(
                phase_payload["receipt"],
                receipts_root,
                run_id=run_id,
                scenario_id=fixture.scenario_id,
                primitive_id=primitive_id,
                phase=phase,
                sequence=sequence,
                fixture_digest=fixture.digest,
                runtime_binding_digest=runtime_binding.digest,
                verified_at=verified_at,
            )
        except MatrixContractError:
            reasons.append(f"receipt_invalid:{phase}")
            receipt_blocked = True
            continue
        receipts[phase] = receipt
        result["receipts"].append(
            {
                "phase": phase,
                "status": receipt["status"],
                "digest": phase_payload["receipt"]["sha256"],
            }
        )
        if receipt["status"] == "failed":
            reasons.append(f"phase_failed:{phase}")
        elif receipt["status"] in {"blocked", "not_run"}:
            reasons.append(f"phase_{receipt['status']}:{phase}")
            receipt_blocked = True

    if len(receipts) == len(REQUIRED_PHASES) and all(
        receipt["status"] == "passed" for receipt in receipts.values()
    ):
        reasons.extend(_derive_receipt_invariant_failures(receipts))
        result["_transactionIds"] = [
            receipts["request"]["facts"]["requestId"],
            receipts["approval"]["facts"]["approvalId"],
            receipts["checkpoint"]["facts"]["checkpointId"],
            receipts["restore_request"]["facts"]["requestId"],
            receipts["restore_approval"]["facts"]["approvalId"],
        ]

    if not reasons:
        result["status"] = "full"
    elif provenance_blocked or receipt_blocked or not fixture.materialized or not runtime_binding.files_hashed:
        result["status"] = "blocked"
    else:
        result["status"] = "partial"
    return result


def _enforce_transaction_id_uniqueness(rows: list[dict[str, Any]]) -> None:
    owners: dict[str, list[dict[str, Any]]] = {}
    for row in rows:
        for transaction_id in row.pop("_transactionIds", []):
            owners.setdefault(str(transaction_id), []).append(row)
    for shared_rows in owners.values():
        if len(shared_rows) < 2:
            continue
        for row in shared_rows:
            reasons = row["reasons"]
            if "transaction_id_reused" not in reasons:
                reasons.append("transaction_id_reused")
            row["status"] = "blocked"


def _verify_receipt(
    reference: Mapping[str, Any],
    receipts_root: Path,
    *,
    run_id: str,
    scenario_id: str,
    primitive_id: str,
    phase: str,
    sequence: int,
    fixture_digest: str,
    runtime_binding_digest: str,
    verified_at: datetime,
) -> dict[str, Any]:
    relative_path = _require_safe_relative_path(reference.get("relativePath"), "receipt")
    expected_digest = _require_sha256(reference.get("sha256"), "receipt digest")
    root = receipts_root.resolve()
    if not root.is_dir() or _is_reparse_point(root):
        raise MatrixContractError("receipt root is unavailable")
    path = _resolve_project_relative(root, relative_path)
    if not path.is_file() or _is_reparse_point(path):
        raise MatrixContractError("receipt is unavailable")
    receipt_bytes = _read_stable_bytes(path, maximum_size=_MAX_JSON_BYTES)
    if hashlib.sha256(receipt_bytes).hexdigest() != expected_digest:
        raise MatrixContractError("receipt digest mismatch")

    receipt = _parse_json_object(receipt_bytes, "receipt")
    _require_public_safe(receipt)
    _require_exact_fields(receipt, _RECEIPT_FIELDS, "receipt")
    expected_values = {
        "schema": RECEIPT_SCHEMA,
        "runId": run_id,
        "scenarioId": scenario_id,
        "primitiveId": primitive_id,
        "phase": phase,
        "sequence": sequence,
        "fixtureDigest": fixture_digest,
        "runtimeBindingDigest": runtime_binding_digest,
    }
    if any(receipt.get(key) != value for key, value in expected_values.items()):
        raise MatrixContractError("receipt binding mismatch")
    status = receipt.get("status")
    if not isinstance(status, str) or status not in _PHASE_STATUSES:
        raise MatrixContractError("receipt status is invalid")
    observed_at = _parse_utc(receipt.get("observedAt"))
    if observed_at is None:
        raise MatrixContractError("receipt timestamp is invalid")
    if (
        observed_at < verified_at - _MAX_RECEIPT_AGE
        or observed_at > verified_at + _MAX_RECEIPT_FUTURE_SKEW
    ):
        raise MatrixContractError("receipt timestamp is outside the verification window")
    if not isinstance(receipt.get("facts"), Mapping):
        raise MatrixContractError("receipt facts must be an object")
    if status == "passed":
        _validate_passed_fact_shape(phase, receipt["facts"])
    return receipt


def _validate_passed_fact_shape(phase: str, facts: Mapping[str, Any]) -> None:
    _require_exact_fields(facts, _FACT_FIELDS[phase], "receipt facts")
    for key in ("stateDigest", "reportDigest"):
        if key in facts:
            _require_sha256(facts[key], "fact digest")
    for key in ("requestId", "approvalId", "checkpointId"):
        if key in facts:
            _require_safe_id(facts[key], "fact id")
    if "state" in facts and facts["state"] != "approval_pending":
        raise MatrixContractError("request state is invalid")
    for key in (
        "approved",
        "created",
        "applied",
        "matched",
        "passed",
        "restored",
    ):
        if key in facts and type(facts[key]) is not bool:
            raise MatrixContractError("receipt boolean fact is invalid")
    for key in ("mutationCount", "count"):
        if key in facts and (type(facts[key]) is not int or facts[key] < 0):
            raise MatrixContractError("receipt count fact is invalid")


def _derive_receipt_invariant_failures(
    receipts: Mapping[str, Mapping[str, Any]],
) -> list[str]:
    reasons: list[str] = []
    facts = {phase: receipt["facts"] for phase, receipt in receipts.items()}

    timestamps = [_parse_utc(receipts[phase]["observedAt"]) for phase in REQUIRED_PHASES]
    if any(timestamp is None for timestamp in timestamps) or any(
        left >= right for left, right in zip(timestamps, timestamps[1:])
    ):
        reasons.append("receipt_order_invalid")

    if facts["preview"]["mutationCount"] != 0:
        reasons.append("preview_mutated")
    if facts["request"]["state"] != "approval_pending":
        reasons.append("request_not_approval_pending")
    if facts["approval"]["approved"] is not True:
        reasons.append("approval_missing")
    if facts["approval"]["requestId"] != facts["request"]["requestId"]:
        reasons.append("approval_request_mismatch")
    if facts["checkpoint"]["created"] is not True:
        reasons.append("checkpoint_missing")
    if facts["checkpoint"]["approvalId"] != facts["approval"]["approvalId"]:
        reasons.append("checkpoint_approval_mismatch")
    if facts["apply"]["applied"] is not True:
        reasons.append("apply_missing")
    if facts["apply"]["checkpointId"] != facts["checkpoint"]["checkpointId"]:
        reasons.append("apply_checkpoint_mismatch")
    if facts["readback"]["matched"] is not True:
        reasons.append("readback_mismatch")
    if facts["readback"]["stateDigest"] == facts["detect"]["stateDigest"]:
        reasons.append("apply_state_unchanged")
    if facts["validation"]["passed"] is not True:
        reasons.append("validation_failed")
    if facts["restore_request"]["state"] != "approval_pending":
        reasons.append("restore_request_not_approval_pending")
    if facts["restore_approval"]["approved"] is not True:
        reasons.append("restore_approval_missing")
    if facts["restore_approval"]["requestId"] != facts["restore_request"]["requestId"]:
        reasons.append("restore_approval_request_mismatch")
    if facts["restore_execution"]["restored"] is not True:
        reasons.append("restore_execution_missing")
    if facts["restore_execution"]["approvalId"] != facts["restore_approval"]["approvalId"]:
        reasons.append("restore_execution_approval_mismatch")
    if facts["request"]["requestId"] == facts["restore_request"]["requestId"]:
        reasons.append("restore_request_not_distinct")
    if facts["approval"]["approvalId"] == facts["restore_approval"]["approvalId"]:
        reasons.append("restore_approval_not_distinct")
    if facts["baseline_comparison"]["matched"] is not True:
        reasons.append("baseline_not_restored")
    if facts["baseline_comparison"]["stateDigest"] != facts["detect"]["stateDigest"]:
        reasons.append("baseline_digest_mismatch")
    if facts["residue"]["count"] != 0:
        reasons.append("residue_detected")
    return reasons


def _build_summary(
    scenarios: list[dict[str, Any]], rows: list[dict[str, Any]]
) -> dict[str, Any]:
    scenario_counts = {
        status: sum(item["status"] == status for item in scenarios)
        for status in ("full", "partial", "blocked")
    }
    row_counts = {
        status: sum(item["status"] == status for item in rows)
        for status in ("full", "partial", "blocked")
    }
    if scenario_counts["full"] == len(scenarios):
        status = "full"
    elif scenario_counts["blocked"] == len(scenarios):
        status = "blocked"
    else:
        status = "partial"
    return {
        "status": status,
        "scenarioCount": len(scenarios),
        "fullScenarioCount": scenario_counts["full"],
        "partialScenarioCount": scenario_counts["partial"],
        "blockedScenarioCount": scenario_counts["blocked"],
        "requiredRowCount": len(rows),
        "fullRowCount": row_counts["full"],
        "partialRowCount": row_counts["partial"],
        "blockedRowCount": row_counts["blocked"],
    }


def _read_json_object(path: Path, label: str) -> dict[str, Any]:
    return _parse_json_object(
        _read_stable_bytes(path, maximum_size=_MAX_JSON_BYTES),
        label,
    )


def _parse_json_object(payload_bytes: bytes, label: str) -> dict[str, Any]:
    try:
        payload = json.loads(
            payload_bytes.decode("utf-8"),
            object_pairs_hook=_unique_object,
        )
    except MatrixContractError:
        raise
    except (OSError, UnicodeError, RecursionError, ValueError) as exc:
        raise MatrixContractError(f"{label} JSON is unavailable") from exc
    if not isinstance(payload, dict):
        raise MatrixContractError(f"{label} must be an object")
    return payload


def _unique_object(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for key, value in pairs:
        if key in result:
            raise MatrixContractError("duplicate JSON field")
        result[key] = value
    return result


def _require_public_safe(value: Any) -> None:
    if redact_public_evidence(value) != value:
        raise MatrixContractError("private value rejected")


def _resolve_project_relative(root: Path, relative_path: str) -> Path:
    safe_relative = _require_safe_relative_path(relative_path, "relative path")
    candidate = root.joinpath(*PurePosixPath(safe_relative).parts)
    resolved_root = root.resolve()
    resolved_candidate = candidate.resolve(strict=False)
    try:
        resolved_candidate.relative_to(resolved_root)
    except ValueError as exc:
        raise MatrixContractError("relative path escaped its root") from exc
    current = resolved_root
    for part in PurePosixPath(safe_relative).parts:
        current = current / part
        if current.exists() and _is_reparse_point(current):
            raise MatrixContractError("relative path contains a link")
    return resolved_candidate


def _require_safe_relative_path(value: Any, label: str) -> str:
    if not isinstance(value, str) or not value or "\\" in value or "\x00" in value:
        raise MatrixContractError(f"{label} path is invalid")
    path = PurePosixPath(value)
    if path.is_absolute() or any(part in {"", ".", ".."} for part in path.parts):
        raise MatrixContractError(f"{label} path is invalid")
    normalized = path.as_posix()
    if normalized != value:
        raise MatrixContractError(f"{label} path is not canonical")
    _require_public_safe(normalized)
    return normalized


def _require_regular_file(path_value: Path | str) -> Path:
    path = Path(path_value)
    if not path.is_file() or _is_reparse_point(path):
        raise MatrixContractError("runtime file is unavailable")
    return path


def _is_reparse_point(path: Path) -> bool:
    try:
        info = path.lstat()
    except OSError:
        return False
    attributes = getattr(info, "st_file_attributes", 0)
    reparse_flag = getattr(stat, "FILE_ATTRIBUTE_REPARSE_POINT", 0x400)
    return path.is_symlink() or bool(attributes & reparse_flag)


def _sha256_file(path: Path) -> str:
    return _stable_file_fingerprint(path)[0]


def _stable_file_fingerprint(path: Path) -> tuple[str, int]:
    digest = hashlib.sha256()
    try:
        with path.open("rb") as handle:
            before = os.fstat(handle.fileno())
            for chunk in iter(lambda: handle.read(1024 * 1024), b""):
                digest.update(chunk)
            after = os.fstat(handle.fileno())
        current = path.stat()
    except OSError as exc:
        raise MatrixContractError("file digest could not be computed") from exc
    if _stat_identity(before) != _stat_identity(after) or _stat_identity(after) != _stat_identity(current):
        raise MatrixContractError("file changed during verification")
    return digest.hexdigest(), after.st_size


def _read_stable_bytes(path: Path, *, maximum_size: int) -> bytes:
    try:
        with path.open("rb") as handle:
            before = os.fstat(handle.fileno())
            if before.st_size > maximum_size:
                raise MatrixContractError("JSON file is too large")
            payload = handle.read(maximum_size + 1)
            after = os.fstat(handle.fileno())
        current = path.stat()
    except MatrixContractError:
        raise
    except OSError as exc:
        raise MatrixContractError("JSON file is unavailable") from exc
    if len(payload) > maximum_size:
        raise MatrixContractError("JSON file is too large")
    if _stat_identity(before) != _stat_identity(after) or _stat_identity(after) != _stat_identity(current):
        raise MatrixContractError("JSON file changed during verification")
    return payload


def _stat_identity(info: os.stat_result) -> tuple[int, int, int, int]:
    return (
        int(getattr(info, "st_dev", 0)),
        int(getattr(info, "st_ino", 0)),
        int(info.st_size),
        int(getattr(info, "st_mtime_ns", int(info.st_mtime * 1_000_000_000))),
    )


def _sha256_json(payload: Any) -> str:
    canonical = json.dumps(
        payload,
        ensure_ascii=True,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")
    return hashlib.sha256(canonical).hexdigest()


def _require_exact_fields(
    payload: Mapping[str, Any], expected: set[str], label: str
) -> None:
    if set(payload) != expected:
        raise MatrixContractError(f"{label} fields mismatch")


def _require_string(value: Any, label: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise MatrixContractError(f"{label} must be a non-empty string")
    return value


def _require_safe_id(value: Any, label: str) -> str:
    if not isinstance(value, str) or _SAFE_ID_RE.fullmatch(value) is None:
        raise MatrixContractError(f"{label} is invalid")
    _require_public_safe(value)
    return value


def _require_sha256(value: Any, label: str) -> str:
    if not isinstance(value, str) or _SHA256_RE.fullmatch(value) is None:
        raise MatrixContractError(f"{label} must be a lowercase SHA-256 digest")
    return value


def _parse_utc(value: Any) -> datetime | None:
    if not isinstance(value, str) or not value.endswith("Z"):
        return None
    try:
        parsed = datetime.fromisoformat(value[:-1] + "+00:00")
    except ValueError:
        return None
    if parsed.tzinfo is None:
        return None
    return parsed.astimezone(timezone.utc)
