from __future__ import annotations

import copy
import hashlib
import importlib.util
import json
import subprocess
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

import primitive_basis_matrix as matrix


REPO_ROOT = Path(__file__).resolve().parents[1]
FIXTURE_DIR = Path(__file__).parent / "fixtures" / "primitive_basis"
SCRIPT_PATH = REPO_ROOT / "scripts" / "smoke_primitive_basis_matrix.py"


def canonical_bytes(payload: object) -> bytes:
    return (
        json.dumps(payload, ensure_ascii=True, sort_keys=True, separators=(",", ":"))
        + "\n"
    ).encode("utf-8")


def digest_bytes(payload: bytes) -> str:
    return hashlib.sha256(payload).hexdigest()


def digest_json(payload: object) -> str:
    canonical = json.dumps(
        payload,
        ensure_ascii=True,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")
    return digest_bytes(canonical)


def materialize_fixture_roots(repository_root: Path) -> None:
    for scenario_id in matrix.SCENARIO_ORDER:
        root = repository_root / "Assets" / "VRCForge" / "PrimitiveBasis" / scenario_id
        root.mkdir(parents=True, exist_ok=True)
        state_bytes = canonical_bytes({"scenarioId": scenario_id, "state": "baseline"})
        (root / "state.json").write_bytes(state_bytes)
        baseline = {
            "schema": matrix.BASELINE_SCHEMA,
            "scenarioId": scenario_id,
            "files": [
                {
                    "path": "state.json",
                    "size": len(state_bytes),
                    "sha256": digest_bytes(state_bytes),
                }
            ],
        }
        (root / "baseline.json").write_bytes(canonical_bytes(baseline))


def load_materialized_fixtures(tmp_path: Path) -> tuple[Path, matrix.FixtureSet]:
    repository_root = tmp_path / "repository"
    materialize_fixture_roots(repository_root)
    descriptor_dir = tmp_path / "descriptors"
    descriptor_dir.mkdir(parents=True, exist_ok=True)
    for scenario_id in matrix.SCENARIO_ORDER:
        payload = json.loads((FIXTURE_DIR / f"{scenario_id}.json").read_text(encoding="utf-8"))
        baseline = json.loads(
            (
                repository_root
                / "Assets"
                / "VRCForge"
                / "PrimitiveBasis"
                / scenario_id
                / "baseline.json"
            ).read_text(encoding="utf-8")
        )
        payload["expectedBaselineDigest"] = digest_json(baseline)
        payload["expectedTreeDigest"] = digest_json(baseline["files"])
        (descriptor_dir / f"{scenario_id}.json").write_bytes(canonical_bytes(payload))
    return repository_root, matrix.load_fixture_set(
        descriptor_dir,
        repository_root=repository_root,
    )


def create_verification_context(
    tmp_path: Path,
    repository_root: Path,
) -> matrix.VerificationContext:
    runtime_root = tmp_path / "runtime"
    runtime_root.mkdir(parents=True, exist_ok=True)
    release_manifest = runtime_root / "release-manifest.json"
    release_manifest.write_bytes(canonical_bytes({"version": "1.4.0"}))
    executable = runtime_root / "VRCForge.exe"
    executable.write_bytes(b"synthetic executable bytes")
    receipts = tmp_path / "receipts"
    receipts.mkdir(parents=True, exist_ok=True)
    return matrix.VerificationContext(
        repository_root=repository_root,
        receipts_root=receipts,
        release_manifest_path=release_manifest,
        executable_path=executable,
    )


def phase_facts(phase: str, identity: str) -> dict[str, object]:
    baseline_digest = hashlib.sha256(f"{identity}:baseline".encode()).hexdigest()
    applied_digest = hashlib.sha256(f"{identity}:applied".encode()).hexdigest()
    report_digest = hashlib.sha256(f"{identity}:validation".encode()).hexdigest()
    normal_request = f"request-{identity}"
    normal_approval = f"approval-{identity}"
    checkpoint = f"checkpoint-{identity}"
    restore_request = f"restore-request-{identity}"
    restore_approval = f"restore-approval-{identity}"
    return {
        "detect": {"stateDigest": baseline_digest},
        "preview": {"mutationCount": 0},
        "request": {"requestId": normal_request, "state": "approval_pending"},
        "approval": {
            "requestId": normal_request,
            "approvalId": normal_approval,
            "approved": True,
        },
        "checkpoint": {
            "approvalId": normal_approval,
            "checkpointId": checkpoint,
            "created": True,
        },
        "apply": {"checkpointId": checkpoint, "applied": True},
        "readback": {"matched": True, "stateDigest": applied_digest},
        "validation": {"passed": True, "reportDigest": report_digest},
        "restore_request": {
            "requestId": restore_request,
            "state": "approval_pending",
        },
        "restore_approval": {
            "requestId": restore_request,
            "approvalId": restore_approval,
            "approved": True,
        },
        "restore_execution": {
            "approvalId": restore_approval,
            "restored": True,
        },
        "baseline_comparison": {"matched": True, "stateDigest": baseline_digest},
        "residue": {"count": 0},
    }[phase]


def passing_evidence(
    fixtures: matrix.FixtureSet,
    context: matrix.VerificationContext,
) -> dict[str, object]:
    run_id = "primitive-basis-run-1"
    binding = matrix.derive_runtime_binding(context)
    assert binding.files_hashed
    started_at = datetime.now(timezone.utc) - timedelta(minutes=1)
    rows: list[dict[str, object]] = []
    for fixture in fixtures.fixtures:
        for primitive_index, primitive_id in enumerate(fixture.required_primitives, start=1):
            identity = f"{matrix.SCENARIO_ORDER.index(fixture.scenario_id) + 1}-{primitive_index}"
            phases: dict[str, object] = {}
            for sequence, phase in enumerate(matrix.REQUIRED_PHASES, start=1):
                relative_path = (
                    f"{run_id}/{fixture.scenario_id}/{primitive_id}/{phase}.json"
                )
                receipt_path = context.receipts_root.joinpath(*Path(relative_path).parts)
                receipt_path.parent.mkdir(parents=True, exist_ok=True)
                receipt = {
                    "schema": matrix.RECEIPT_SCHEMA,
                    "runId": run_id,
                    "scenarioId": fixture.scenario_id,
                    "primitiveId": primitive_id,
                    "phase": phase,
                    "sequence": sequence,
                    "observedAt": (
                        started_at + timedelta(seconds=sequence)
                    ).isoformat(timespec="microseconds").replace("+00:00", "Z"),
                    "fixtureDigest": fixture.digest,
                    "runtimeBindingDigest": binding.digest,
                    "status": "passed",
                    "facts": phase_facts(phase, identity),
                }
                receipt_bytes = canonical_bytes(receipt)
                receipt_path.write_bytes(receipt_bytes)
                phases[phase] = {
                    "receipt": {
                        "relativePath": relative_path,
                        "sha256": digest_bytes(receipt_bytes),
                    }
                }
            rows.append(
                {
                    "scenarioId": fixture.scenario_id,
                    "primitiveId": primitive_id,
                    "fixtureDigest": fixture.digest,
                    "phases": phases,
                }
            )
    return {"schema": matrix.EVIDENCE_SCHEMA, "runId": run_id, "rows": rows}


def find_row(
    evidence: dict[str, object], scenario_id: str, primitive_id: str
) -> dict[str, object]:
    rows = evidence["rows"]
    assert isinstance(rows, list)
    return next(
        row
        for row in rows
        if row["scenarioId"] == scenario_id and row["primitiveId"] == primitive_id
    )


def mutate_receipt(
    row: dict[str, object],
    context: matrix.VerificationContext,
    phase: str,
    mutate,
) -> None:
    phases = row["phases"]
    assert isinstance(phases, dict)
    reference = phases[phase]["receipt"]
    path = context.receipts_root.joinpath(*Path(reference["relativePath"]).parts)
    payload = json.loads(path.read_text(encoding="utf-8"))
    mutate(payload)
    receipt_bytes = canonical_bytes(payload)
    path.write_bytes(receipt_bytes)
    reference["sha256"] = digest_bytes(receipt_bytes)


def test_fixed_descriptors_are_exact_but_unmaterialized_by_default() -> None:
    fixtures = matrix.load_fixture_set(FIXTURE_DIR)

    assert [fixture.scenario_id for fixture in fixtures.fixtures] == list(
        matrix.SCENARIO_ORDER
    )
    assert fixtures.descriptor_digest == "7189e1945ec594813371a628ae093f3d4c73892bd3b1102f545b0a9486887ae6"
    assert fixtures.digest == ""
    assert all(not fixture.materialized for fixture in fixtures.fixtures)
    assert fixtures.fixtures[0].required_primitives == (
        "typed_component_list_write",
        "feature_component_write",
    )


def test_materialized_digest_binds_baseline_inventory_and_file_bytes(tmp_path: Path) -> None:
    repository_root, fixtures = load_materialized_fixtures(tmp_path)

    assert len(fixtures.digest) == 64
    assert all(fixture.materialized and len(fixture.digest) == 64 for fixture in fixtures.fixtures)
    assert (
        matrix.load_fixture_set(tmp_path / "descriptors", repository_root=repository_root).digest
        == fixtures.digest
    )

    state = (
        repository_root
        / "Assets"
        / "VRCForge"
        / "PrimitiveBasis"
        / "parameter_optimization"
        / "state.json"
    )
    state.write_bytes(b"changed without baseline update")
    with pytest.raises(matrix.MatrixContractError, match="fixture file digest mismatch"):
        matrix.load_fixture_set(tmp_path / "descriptors", repository_root=repository_root)


def test_empty_evidence_is_explicitly_blocked() -> None:
    fixtures = matrix.load_fixture_set(FIXTURE_DIR)
    report = matrix.build_report(
        fixtures,
        {"schema": matrix.EVIDENCE_SCHEMA, "runId": "empty-run", "rows": []},
    )

    assert report["ok"] is False
    assert report["summary"] == {
        "status": "blocked",
        "scenarioCount": 4,
        "fullScenarioCount": 0,
        "partialScenarioCount": 0,
        "blockedScenarioCount": 4,
        "requiredRowCount": 6,
        "fullRowCount": 0,
        "partialRowCount": 0,
        "blockedRowCount": 6,
    }


def test_self_asserted_claims_without_verifiers_cannot_be_full(tmp_path: Path) -> None:
    repository_root, fixtures = load_materialized_fixtures(tmp_path)
    context = create_verification_context(tmp_path, repository_root)
    evidence = passing_evidence(fixtures, context)

    report = matrix.build_report(fixtures, evidence)

    assert report["ok"] is False
    assert report["summary"]["status"] == "blocked"
    assert all("runtime_verifier_missing" in row["reasons"] for row in report["rows"])
    assert all("receipt_verifier_missing" in row["reasons"] for row in report["rows"])


def test_self_authored_receipts_cannot_mark_any_scenario_full(tmp_path: Path) -> None:
    repository_root, fixtures = load_materialized_fixtures(tmp_path)
    context = create_verification_context(tmp_path, repository_root)
    report = matrix.build_report(
        fixtures,
        passing_evidence(fixtures, context),
        verification=context,
    )

    assert report["ok"] is False
    assert report["schema"] == matrix.MATRIX_SCHEMA
    assert report["fixtureSetDigest"] == fixtures.digest
    assert report["runtimeBinding"]["filesHashed"] is True
    assert report["runtimeBinding"]["liveRunnerAttested"] is False
    assert report["summary"]["status"] == "blocked"
    assert report["summary"]["fullScenarioCount"] == 0
    assert report["summary"]["fullRowCount"] == 0
    assert all(
        "live_runner_attestation_missing" in row["reasons"] for row in report["rows"]
    )


def test_missing_receipt_cannot_be_full(tmp_path: Path) -> None:
    repository_root, fixtures = load_materialized_fixtures(tmp_path)
    context = create_verification_context(tmp_path, repository_root)
    evidence = passing_evidence(fixtures, context)
    row = find_row(evidence, "parameter_optimization", "parameter_bit_pack")
    phases = row["phases"]
    assert isinstance(phases, dict)
    phases.pop("readback")

    report = matrix.build_report(fixtures, evidence, verification=context)
    result = next(item for item in report["rows"] if item["primitiveId"] == "parameter_bit_pack")

    assert report["ok"] is False
    assert result["status"] == "blocked"
    assert "receipt_missing:readback" in result["reasons"]


def test_skipped_receipt_is_invalid_and_blocked(tmp_path: Path) -> None:
    repository_root, fixtures = load_materialized_fixtures(tmp_path)
    context = create_verification_context(tmp_path, repository_root)
    evidence = passing_evidence(fixtures, context)
    row = find_row(evidence, "parameter_optimization", "parameter_bit_pack")
    mutate_receipt(row, context, "validation", lambda payload: payload.update(status="skipped"))

    report = matrix.build_report(fixtures, evidence, verification=context)
    result = next(item for item in report["rows"] if item["primitiveId"] == "parameter_bit_pack")

    assert report["ok"] is False
    assert result["status"] == "blocked"
    assert "receipt_invalid:validation" in result["reasons"]


def test_duplicate_or_unknown_rows_are_rejected(tmp_path: Path) -> None:
    repository_root, fixtures = load_materialized_fixtures(tmp_path)
    context = create_verification_context(tmp_path, repository_root)
    evidence = passing_evidence(fixtures, context)
    rows = evidence["rows"]
    assert isinstance(rows, list)
    rows.append(copy.deepcopy(rows[0]))

    with pytest.raises(matrix.MatrixContractError, match="duplicate evidence row"):
        matrix.build_report(fixtures, evidence, verification=context)

    unknown = passing_evidence(fixtures, context)
    unknown_rows = unknown["rows"]
    assert isinstance(unknown_rows, list)
    unknown_rows[0]["primitiveId"] = "unknown_write"
    with pytest.raises(matrix.MatrixContractError, match="unknown evidence row"):
        matrix.build_report(fixtures, unknown, verification=context)


def test_duplicate_receipt_reference_is_rejected(tmp_path: Path) -> None:
    repository_root, fixtures = load_materialized_fixtures(tmp_path)
    context = create_verification_context(tmp_path, repository_root)
    evidence = passing_evidence(fixtures, context)
    row = find_row(evidence, "parameter_optimization", "parameter_bit_pack")
    phases = row["phases"]
    assert isinstance(phases, dict)
    phases["preview"] = copy.deepcopy(phases["detect"])

    with pytest.raises(matrix.MatrixContractError, match="duplicate receipt reference"):
        matrix.build_report(fixtures, evidence, verification=context)


def test_transaction_ids_cannot_be_reused_across_rows(tmp_path: Path) -> None:
    repository_root, fixtures = load_materialized_fixtures(tmp_path)
    context = create_verification_context(tmp_path, repository_root)
    evidence = passing_evidence(fixtures, context)
    rows = evidence["rows"]
    assert isinstance(rows, list)
    first = rows[0]
    second = rows[1]
    first_phases = first["phases"]
    assert isinstance(first_phases, dict)
    first_request_path = context.receipts_root.joinpath(
        *Path(first_phases["request"]["receipt"]["relativePath"]).parts
    )
    shared_request_id = json.loads(first_request_path.read_text(encoding="utf-8"))["facts"][
        "requestId"
    ]
    mutate_receipt(
        second,
        context,
        "request",
        lambda payload: payload["facts"].update(requestId=shared_request_id),
    )
    mutate_receipt(
        second,
        context,
        "approval",
        lambda payload: payload["facts"].update(requestId=shared_request_id),
    )

    report = matrix.build_report(fixtures, evidence, verification=context)
    affected = report["rows"][:2]

    assert all("transaction_id_reused" in row["reasons"] for row in affected)
    assert all(row["status"] == "blocked" for row in affected)


@pytest.mark.parametrize(
    ("mutation", "reason"),
    [
        ("fixture_digest", "fixture_digest_mismatch"),
        ("preview", "preview_mutated"),
        ("restore_approval", "restore_approval_not_distinct"),
        ("residue", "residue_detected"),
    ],
)
def test_full_status_is_derived_from_verified_receipts(
    tmp_path: Path, mutation: str, reason: str
) -> None:
    repository_root, fixtures = load_materialized_fixtures(tmp_path)
    context = create_verification_context(tmp_path, repository_root)
    evidence = passing_evidence(fixtures, context)
    row = find_row(evidence, "model_part_composition", "non_destructive_part_composition")

    if mutation == "fixture_digest":
        row["fixtureDigest"] = "0" * 64
    elif mutation == "preview":
        mutate_receipt(
            row,
            context,
            "preview",
            lambda payload: payload["facts"].update(mutationCount=1),
        )
    elif mutation == "restore_approval":
        phases = row["phases"]
        assert isinstance(phases, dict)
        approval_path = context.receipts_root.joinpath(
            *Path(phases["approval"]["receipt"]["relativePath"]).parts
        )
        approval_id = json.loads(approval_path.read_text(encoding="utf-8"))["facts"][
            "approvalId"
        ]
        mutate_receipt(
            row,
            context,
            "restore_approval",
            lambda payload: payload["facts"].update(approvalId=approval_id),
        )
        mutate_receipt(
            row,
            context,
            "restore_execution",
            lambda payload: payload["facts"].update(approvalId=approval_id),
        )
    else:
        mutate_receipt(
            row,
            context,
            "residue",
            lambda payload: payload["facts"].update(count=1),
        )

    report = matrix.build_report(fixtures, evidence, verification=context)
    result = next(
        item for item in report["rows"] if item["primitiveId"] == "non_destructive_part_composition"
    )

    assert report["ok"] is False
    assert result["status"] == "blocked"
    assert reason in result["reasons"]


def test_runtime_binding_is_recomputed_from_real_files(tmp_path: Path) -> None:
    repository_root, fixtures = load_materialized_fixtures(tmp_path)
    context = create_verification_context(tmp_path, repository_root)
    evidence = passing_evidence(fixtures, context)
    context.executable_path.write_bytes(b"different executable")

    report = matrix.build_report(fixtures, evidence, verification=context)

    assert report["ok"] is False
    assert report["runtimeBinding"]["filesHashed"] is True
    assert all(any(reason.startswith("receipt_invalid:") for reason in row["reasons"]) for row in report["rows"])


def test_receipt_order_is_derived_from_timestamps(tmp_path: Path) -> None:
    repository_root, fixtures = load_materialized_fixtures(tmp_path)
    context = create_verification_context(tmp_path, repository_root)
    evidence = passing_evidence(fixtures, context)
    row = find_row(evidence, "parameter_optimization", "parameter_bit_pack")
    mutate_receipt(
        row,
        context,
        "validation",
        lambda payload: payload.update(
            observedAt=json.loads(
                context.receipts_root.joinpath(
                    *Path(row["phases"]["detect"]["receipt"]["relativePath"]).parts
                ).read_text(encoding="utf-8")
            )["observedAt"]
        ),
    )

    report = matrix.build_report(fixtures, evidence, verification=context)
    result = next(item for item in report["rows"] if item["primitiveId"] == "parameter_bit_pack")

    assert result["status"] == "blocked"
    assert "receipt_order_invalid" in result["reasons"]


def test_receipt_timestamp_must_be_fresh(tmp_path: Path) -> None:
    repository_root, fixtures = load_materialized_fixtures(tmp_path)
    context = create_verification_context(tmp_path, repository_root)
    evidence = passing_evidence(fixtures, context)
    row = find_row(evidence, "parameter_optimization", "parameter_bit_pack")
    mutate_receipt(
        row,
        context,
        "detect",
        lambda payload: payload.update(observedAt="2020-01-01T00:00:00Z"),
    )

    report = matrix.build_report(fixtures, evidence, verification=context)
    result = next(item for item in report["rows"] if item["primitiveId"] == "parameter_bit_pack")

    assert result["status"] == "blocked"
    assert "receipt_invalid:detect" in result["reasons"]


@pytest.mark.parametrize("bad_status", [[], {}])
def test_non_scalar_receipt_status_is_safely_rejected(
    tmp_path: Path,
    bad_status: object,
) -> None:
    repository_root, fixtures = load_materialized_fixtures(tmp_path)
    context = create_verification_context(tmp_path, repository_root)
    evidence = passing_evidence(fixtures, context)
    row = find_row(evidence, "parameter_optimization", "parameter_bit_pack")
    mutate_receipt(
        row,
        context,
        "validation",
        lambda payload: payload.update(status=bad_status),
    )

    report = matrix.build_report(fixtures, evidence, verification=context)
    result = next(item for item in report["rows"] if item["primitiveId"] == "parameter_bit_pack")

    assert result["status"] == "blocked"
    assert "receipt_invalid:validation" in result["reasons"]


def test_summary_is_derived_and_input_summary_is_rejected(tmp_path: Path) -> None:
    repository_root, fixtures = load_materialized_fixtures(tmp_path)
    context = create_verification_context(tmp_path, repository_root)
    evidence = passing_evidence(fixtures, context)
    evidence["summary"] = {"status": "full"}

    with pytest.raises(matrix.MatrixContractError, match="evidence fields mismatch"):
        matrix.build_report(fixtures, evidence, verification=context)


@pytest.mark.parametrize(
    "unsafe_value",
    [
        r"C:\\Users\\example\\private\\report.json",
        "/" + "root/private/report.json",
        "client_secret=private-value-1234567890",
        "refresh_token=private-value-1234567890",
        ".ssh/id_private",
    ],
)
def test_shared_privacy_scan_rejects_machine_paths_and_secrets(
    unsafe_value: str,
) -> None:
    fixtures = matrix.load_fixture_set(FIXTURE_DIR)
    evidence = {"schema": matrix.EVIDENCE_SCHEMA, "runId": unsafe_value, "rows": []}

    with pytest.raises(matrix.MatrixContractError, match="private value"):
        matrix.build_report(fixtures, evidence)


def test_receipt_traversal_is_rejected_before_file_access(tmp_path: Path) -> None:
    repository_root, fixtures = load_materialized_fixtures(tmp_path)
    context = create_verification_context(tmp_path, repository_root)
    evidence = passing_evidence(fixtures, context)
    row = find_row(evidence, "parameter_optimization", "parameter_bit_pack")
    phases = row["phases"]
    assert isinstance(phases, dict)
    phases["detect"]["receipt"]["relativePath"] = "../escape.json"

    with pytest.raises(matrix.MatrixContractError, match="receipt path is invalid"):
        matrix.build_report(fixtures, evidence, verification=context)


def test_fixture_contract_rejects_unexpected_drift(tmp_path: Path) -> None:
    for source in FIXTURE_DIR.glob("*.json"):
        (tmp_path / source.name).write_text(source.read_text(encoding="utf-8"), encoding="utf-8")
    target = tmp_path / "parameter_optimization.json"
    payload = json.loads(target.read_text(encoding="utf-8"))
    payload["requiredPrimitives"] = ["different_primitive"]
    target.write_text(json.dumps(payload), encoding="utf-8")

    with pytest.raises(matrix.MatrixContractError, match="required primitives drifted"):
        matrix.load_fixture_set(tmp_path)


def load_script_module():
    spec = importlib.util.spec_from_file_location("smoke_primitive_basis_matrix", SCRIPT_PATH)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_cli_evidence_loader_rejects_duplicate_json_keys(tmp_path: Path) -> None:
    module = load_script_module()
    evidence_path = tmp_path / "evidence.json"
    evidence_path.write_text(
        '{"schema":"first","schema":"second","runId":"run","rows":[]}',
        encoding="utf-8",
    )

    with pytest.raises(matrix.MatrixContractError, match="duplicate JSON field"):
        module.load_evidence(str(evidence_path))


@pytest.mark.parametrize(
    "payload",
    [
        '{"schema":' + "[" * 2000 + "0" + "]" * 2000 + "}",
        '{"schema":' + "9" * 10000 + "}",
    ],
    ids=["deep", "oversized-integer"],
)
def test_cli_pathological_json_has_no_traceback_or_machine_path(
    tmp_path: Path,
    payload: str,
) -> None:
    evidence_path = tmp_path / "private-evidence.json"
    evidence_path.write_text(payload, encoding="utf-8")
    completed = subprocess.run(
        [sys.executable, str(SCRIPT_PATH), "--evidence", str(evidence_path)],
        cwd=REPO_ROOT,
        capture_output=True,
        text=True,
        check=False,
    )

    combined = completed.stdout + completed.stderr
    assert completed.returncode == 2
    assert "Traceback" not in combined
    assert str(tmp_path) not in combined
    assert "primitive basis contract rejected" in completed.stdout


def test_core_pathological_manifest_is_structurally_rejected(tmp_path: Path) -> None:
    repository_root = tmp_path / "repository"
    context = create_verification_context(tmp_path, repository_root)
    context.release_manifest_path.write_text(
        '{"version":' + "[" * 2000 + "0" + "]" * 2000 + "}",
        encoding="utf-8",
    )

    binding = matrix.derive_runtime_binding(context)

    assert binding.files_hashed is False
    assert binding.reasons == ("runtime_binding_invalid",)


def test_cli_blocked_runs_fail_and_never_overwrite_reports(tmp_path: Path) -> None:
    artifacts = tmp_path / "reports"
    command = [
        sys.executable,
        str(SCRIPT_PATH),
        "--artifacts-dir",
        str(artifacts),
        "--repository-root",
        str(tmp_path),
    ]

    first = subprocess.run(command, cwd=REPO_ROOT, capture_output=True, text=True, check=False)
    second = subprocess.run(command, cwd=REPO_ROOT, capture_output=True, text=True, check=False)

    assert first.returncode == 1
    assert second.returncode == 1
    assert len(list(artifacts.glob("primitive-basis-matrix-*.json"))) == 2


def test_cli_report_directory_error_has_no_traceback_or_machine_path(tmp_path: Path) -> None:
    report_target = tmp_path / "private" / "report-target"
    report_target.parent.mkdir(parents=True, exist_ok=True)
    report_target.write_text("not a directory", encoding="utf-8")
    completed = subprocess.run(
        [
            sys.executable,
            str(SCRIPT_PATH),
            "--artifacts-dir",
            str(report_target),
            "--repository-root",
            str(tmp_path),
        ],
        cwd=REPO_ROOT,
        capture_output=True,
        text=True,
        check=False,
    )

    combined = completed.stdout + completed.stderr
    assert completed.returncode == 2
    assert "Traceback" not in combined
    assert str(tmp_path) not in combined
    assert "matrix report directory is unavailable" in completed.stdout
