from __future__ import annotations

import hashlib
import hmac
import json
import os
import re
import stat
import sys
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, BinaryIO, Callable, Mapping

from diagnostic_privacy import redact_public_evidence


LIVE_ATTESTATION_SCHEMA = "vrcforge.primitive_basis_live_attestation.v3"
LIVE_EVIDENCE_SCHEMA = "vrcforge.primitive_basis_live_evidence.v3"
LIVE_RECEIPT_SCHEMA = "vrcforge.primitive_basis_live_receipt.v3"
LIVE_MATRIX_SCHEMA = "vrcforge.primitive_basis_matrix.v3"
LIVE_BOOTSTRAP_MAGIC = b"VRCFPRIMLIVE3\x00\x00\x00"
TRUSTED_LIVE_ATTESTATION_SCHEMA = "vrcforge.primitive_basis_live_attestation.v4"
TRUSTED_LIVE_EVIDENCE_SCHEMA = "vrcforge.primitive_basis_live_evidence.v4"
TRUSTED_LIVE_RECEIPT_SCHEMA = "vrcforge.primitive_basis_live_receipt.v4"
TRUSTED_LIVE_BOOTSTRAP_MAGIC = b"VRCFPRIMLIVE4\x00\x00\x00"
LIVE_PROOF_ALGORITHM = "hmac-sha256-runner-self-mac-v1"
LIVE_ORIGIN_TRUST = "untrusted_runner_self_mac"
LIVE_STDIN_ENV = "VRCFORGE_PRIMITIVE_LIVE_STDIN"
MODEL_SCENARIO_ID = "model_part_composition"
MODEL_PRIMITIVE_ID = "non_destructive_part_composition"
MODEL_TARGET_TOOL = "vrcforge_add_modular_avatar_component"
RESTORE_TARGET_TOOL = "vrcforge_restore_checkpoint"

LIVE_PHASES = (
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

_DIGEST_RE = re.compile(r"[0-9a-f]{64}")
_SAFE_ID_RE = re.compile(r"[A-Za-z0-9][A-Za-z0-9._-]{0,127}")
_MAX_RUN_DURATION = timedelta(hours=2)
_MAX_CLOCK_SKEW = timedelta(minutes=5)
_MAX_EVIDENCE_AGE = timedelta(hours=24)
_BOOTSTRAP_DIGEST_FIELDS = (
    "runtime_binding_digest",
    "desktop_executable_digest",
    "backend_executable_digest",
    "runner_digest",
    "unity_package_digest",
    "unity_editor_digest",
    "fixture_project_input_digest",
    "fixture_set_descriptor_digest",
    "fixture_descriptor_digest",
)

_COMMON_RECEIPT_FIELDS = {
    "schema",
    "runId",
    "scenarioId",
    "primitiveId",
    "phase",
    "sequence",
    "monotonicOrdinal",
    "observedAt",
    "fixtureDigest",
    "runtimeBindingDigest",
    "projectBindingDigest",
    "status",
    "facts",
    "authoritativeEventDigest",
}
_TRUSTED_RECEIPT_FIELDS = _COMMON_RECEIPT_FIELDS | {"originTicketDigest"}
_ATTESTATION_FIELDS = {
    "schema",
    "runId",
    "challengeDigest",
    "scenarioId",
    "primitiveId",
    "fixtureSetDescriptorDigest",
    "fixtureDescriptorDigest",
    "fixtureDigest",
    "projectBindingDigest",
    "runtimeBindingDigest",
    "desktopExecutableDigest",
    "backendExecutableDigest",
    "runnerDigest",
    "unityPackageDigest",
    "unityEditorDigest",
    "fixtureProjectInputDigest",
    "orderedReceiptSetDigest",
    "authoritativeEventChainDigest",
    "startedAt",
    "finishedAt",
    "finalizedAt",
    "proofAlgorithm",
    "originTrust",
    "originVerified",
    "proof",
}
_TRUSTED_ATTESTATION_FIELDS = _ATTESTATION_FIELDS | {"originTicketDigest"}
_FINALIZATION_FIELDS = {"schema", "evidence", "attestation"}
_EVIDENCE_FIELDS = {"schema", "runId", "rows"}
_ROW_FIELDS = {"scenarioId", "primitiveId", "fixtureDigest", "receipts"}

_FACT_FIELDS: dict[str, set[str]] = {
    "detect": {"stateDigest", "inventoryDigest", "componentPresent"},
    "preview": {"beforeStateDigest", "afterStateDigest", "mutationCount"},
    "request": {
        "requestId",
        "targetTool",
        "argumentsDigest",
        "operationDigest",
        "projectBindingDigest",
        "state",
    },
    "approval": {
        "requestId",
        "approvalId",
        "targetTool",
        "argumentsDigest",
        "operationDigest",
        "projectBindingDigest",
        "pendingObserved",
        "approved",
    },
    "checkpoint": {
        "approvalId",
        "checkpointId",
        "targetTool",
        "argumentsDigest",
        "operationDigest",
        "projectBindingDigest",
        "fixtureProjectInputDigest",
        "unityProcessIdentityDigest",
        "created",
    },
    "apply": {
        "executionId",
        "approvalId",
        "checkpointId",
        "targetTool",
        "argumentsDigest",
        "operationDigest",
        "projectBindingDigest",
        "fixtureProjectInputDigest",
        "applied",
    },
    "readback": {
        "checkpointId",
        "expectedStateDigest",
        "actualStateDigest",
        "matched",
    },
    "validation": {"checkpointId", "passed", "reportDigest"},
    "restore_request": {
        "requestId",
        "targetTool",
        "checkpointId",
        "projectBindingDigest",
        "argumentsDigest",
        "state",
    },
    "restore_approval": {
        "requestId",
        "approvalId",
        "targetTool",
        "checkpointId",
        "projectBindingDigest",
        "argumentsDigest",
        "pendingObserved",
        "approved",
    },
    "restore_execution": {
        "executionId",
        "approvalId",
        "targetTool",
        "checkpointId",
        "projectBindingDigest",
        "argumentsDigest",
        "unityProcessIdentityDigest",
        "restored",
    },
    "baseline_comparison": {
        "checkpointId",
        "expectedBaselineDigest",
        "actualStateDigest",
        "matched",
    },
    "residue": {
        "checkpointId",
        "inventoryDigest",
        "count",
        "projectRemoved",
        "unityProcessExited",
        "bridgePortReleased",
    },
}


class LiveAttestationError(ValueError):
    """The live primitive evidence is incomplete, replayed, or not origin-bound."""


@dataclass(frozen=True)
class LiveBootstrap:
    key: bytes
    challenge: bytes
    runtime_binding_digest: str
    desktop_executable_digest: str
    backend_executable_digest: str
    runner_digest: str
    unity_package_digest: str
    unity_editor_digest: str
    fixture_project_input_digest: str
    fixture_set_descriptor_digest: str
    fixture_descriptor_digest: str
    origin_ticket_digest: str = ""

    def __post_init__(self) -> None:
        if len(self.key) != 32 or len(self.challenge) != 32:
            raise LiveAttestationError("live bootstrap secret length is invalid")
        for field_name in _BOOTSTRAP_DIGEST_FIELDS:
            _require_digest(getattr(self, field_name), field_name)
        if self.origin_ticket_digest:
            _require_digest(self.origin_ticket_digest, "origin ticket digest")

    @property
    def challenge_digest(self) -> str:
        return hashlib.sha256(self.challenge).hexdigest()

    @property
    def run_id(self) -> str:
        return f"primitive-live-{self.challenge_digest[:32]}"


@dataclass(frozen=True)
class VerifiedLiveRun:
    run_id: str
    scenario_id: str
    primitive_id: str
    fixture_set_descriptor_digest: str
    fixture_descriptor_digest: str
    fixture_digest: str
    project_binding_digest: str
    runtime_binding_digest: str
    attestation_digest: str
    origin_verified: bool
    started_at: str
    finished_at: str
    finalized_at: str
    receipts: tuple[dict[str, Any], ...]
    inner_attestation_digest: str = ""
    origin_signer_key_id: str = ""
    origin_ticket_digest: str = ""
    origin_process_graph_digest: str = ""
    origin_network_binding_digest: str = ""
    origin_cleanup_digest: str = ""


@dataclass(frozen=True)
class LivePublicBinding:
    """Public inner-run fields authenticated by a separate origin envelope."""

    run_id: str
    challenge_digest: str
    runtime_binding_digest: str
    desktop_executable_digest: str
    backend_executable_digest: str
    runner_digest: str
    unity_package_digest: str
    unity_editor_digest: str
    fixture_project_input_digest: str
    fixture_set_descriptor_digest: str
    fixture_descriptor_digest: str
    origin_ticket_digest: str = ""

    def __post_init__(self) -> None:
        _require_safe_id(self.run_id, "live run")
        for field_name in (
            "challenge_digest",
            "runtime_binding_digest",
            "desktop_executable_digest",
            "backend_executable_digest",
            "runner_digest",
            "unity_package_digest",
            "unity_editor_digest",
            "fixture_project_input_digest",
            "fixture_set_descriptor_digest",
            "fixture_descriptor_digest",
        ):
            _require_digest(getattr(self, field_name), field_name)
        if self.run_id != f"primitive-live-{self.challenge_digest[:32]}":
            raise LiveAttestationError("live run and challenge binding mismatch")
        if self.origin_ticket_digest:
            _require_digest(self.origin_ticket_digest, "origin ticket digest")

    @classmethod
    def from_bootstrap(cls, bootstrap: LiveBootstrap) -> "LivePublicBinding":
        return cls(
            run_id=bootstrap.run_id,
            challenge_digest=bootstrap.challenge_digest,
            runtime_binding_digest=bootstrap.runtime_binding_digest,
            desktop_executable_digest=bootstrap.desktop_executable_digest,
            backend_executable_digest=bootstrap.backend_executable_digest,
            runner_digest=bootstrap.runner_digest,
            unity_package_digest=bootstrap.unity_package_digest,
            unity_editor_digest=bootstrap.unity_editor_digest,
            fixture_project_input_digest=bootstrap.fixture_project_input_digest,
            fixture_set_descriptor_digest=bootstrap.fixture_set_descriptor_digest,
            fixture_descriptor_digest=bootstrap.fixture_descriptor_digest,
            origin_ticket_digest=bootstrap.origin_ticket_digest,
        )


class PrimitiveBasisLiveSession:
    """One-shot, backend-owned receipt collector for the fixed first live row."""

    def __init__(
        self,
        bootstrap: LiveBootstrap,
        *,
        now: Callable[[], datetime] | None = None,
    ) -> None:
        self._bootstrap = bootstrap
        self._key = bytearray(bootstrap.key)
        self._now = now or (lambda: datetime.now(timezone.utc))
        self._state = "issued"
        self._fixture_digest = ""
        self._project_binding_digest = ""
        self._started_at: datetime | None = None
        self._receipts: list[dict[str, Any]] = []
        self._event_digests: list[str] = []
        self._finalization: dict[str, Any] | None = None

    @property
    def state(self) -> str:
        return self._state

    @property
    def run_id(self) -> str:
        return self._bootstrap.run_id

    @property
    def challenge_digest(self) -> str:
        return self._bootstrap.challenge_digest

    @property
    def fixture_set_descriptor_digest(self) -> str:
        return self._bootstrap.fixture_set_descriptor_digest

    @property
    def fixture_descriptor_digest(self) -> str:
        return self._bootstrap.fixture_descriptor_digest

    @property
    def unity_editor_digest(self) -> str:
        return self._bootstrap.unity_editor_digest

    @property
    def fixture_project_input_digest(self) -> str:
        return self._bootstrap.fixture_project_input_digest

    @property
    def receipt_count(self) -> int:
        return len(self._receipts)

    def begin(self, *, fixture_digest: str, project_binding_digest: str) -> str:
        if self._state != "issued":
            raise LiveAttestationError("live challenge cannot be started twice")
        self._fixture_digest = _require_digest(fixture_digest, "fixture digest")
        self._project_binding_digest = _require_digest(
            project_binding_digest, "project binding digest"
        )
        self._started_at = _utc_now(self._now)
        self._state = "running"
        return self._bootstrap.run_id

    def record(
        self,
        phase: str,
        facts: Mapping[str, Any],
        *,
        authoritative_event: Mapping[str, Any],
        observed_at: datetime | None = None,
    ) -> dict[str, Any]:
        if self._state != "running" or self._started_at is None:
            raise LiveAttestationError("live challenge is not running")
        sequence = len(self._receipts) + 1
        if sequence > len(LIVE_PHASES) or phase != LIVE_PHASES[sequence - 1]:
            raise LiveAttestationError("live receipt phase order is invalid")
        safe_facts = _copy_public_mapping(facts, "receipt facts")
        safe_event = _copy_public_mapping(authoritative_event, "authoritative event")
        _validate_facts(phase, safe_facts)
        event_binding = {
            "runId": self._bootstrap.run_id,
            "phase": phase,
            "sequence": sequence,
            "event": safe_event,
        }
        if self._bootstrap.origin_ticket_digest:
            event_binding["originTicketDigest"] = self._bootstrap.origin_ticket_digest
        event_digest = _digest_json(event_binding)
        timestamp = _utc_now(lambda: observed_at or self._now())
        if self._receipts:
            previous = _parse_utc(self._receipts[-1]["observedAt"])
            if previous is not None and timestamp <= previous:
                timestamp = previous + timedelta(microseconds=1)
        receipt = {
            "schema": (
                TRUSTED_LIVE_RECEIPT_SCHEMA
                if self._bootstrap.origin_ticket_digest
                else LIVE_RECEIPT_SCHEMA
            ),
            "runId": self._bootstrap.run_id,
            "scenarioId": MODEL_SCENARIO_ID,
            "primitiveId": MODEL_PRIMITIVE_ID,
            "phase": phase,
            "sequence": sequence,
            "monotonicOrdinal": sequence,
            "observedAt": _format_utc(timestamp),
            "fixtureDigest": self._fixture_digest,
            "runtimeBindingDigest": self._bootstrap.runtime_binding_digest,
            "projectBindingDigest": self._project_binding_digest,
            "status": "passed",
            "facts": safe_facts,
            "authoritativeEventDigest": event_digest,
        }
        if self._bootstrap.origin_ticket_digest:
            receipt["originTicketDigest"] = self._bootstrap.origin_ticket_digest
        self._receipts.append(receipt)
        self._event_digests.append(event_digest)
        return json.loads(json.dumps(receipt))

    def finalize(self) -> dict[str, Any]:
        if self._state == "finalized" and self._finalization is not None:
            return json.loads(json.dumps(self._finalization))
        if self._state != "running" or self._started_at is None:
            raise LiveAttestationError("live challenge cannot be finalized")
        if len(self._receipts) != len(LIVE_PHASES):
            raise LiveAttestationError("live challenge is incomplete")
        _validate_receipt_invariants(
            self._receipts,
            expected_fixture_project_input_digest=(
                self._bootstrap.fixture_project_input_digest
            ),
        )
        finished_at = _parse_utc(self._receipts[-1]["observedAt"])
        if finished_at is None:
            raise LiveAttestationError("live receipt timestamp is invalid")
        finalized_at = _utc_now(self._now)
        if finalized_at <= finished_at:
            finalized_at = finished_at + timedelta(microseconds=1)
        ordered_receipts = [
            {
                "sequence": receipt["sequence"],
                "phase": receipt["phase"],
                "receiptDigest": _digest_json(receipt),
            }
            for receipt in self._receipts
        ]
        attestation_schema = (
            TRUSTED_LIVE_ATTESTATION_SCHEMA
            if self._bootstrap.origin_ticket_digest
            else LIVE_ATTESTATION_SCHEMA
        )
        evidence_schema = (
            TRUSTED_LIVE_EVIDENCE_SCHEMA
            if self._bootstrap.origin_ticket_digest
            else LIVE_EVIDENCE_SCHEMA
        )
        attestation = {
            "schema": attestation_schema,
            "runId": self._bootstrap.run_id,
            "challengeDigest": self._bootstrap.challenge_digest,
            "scenarioId": MODEL_SCENARIO_ID,
            "primitiveId": MODEL_PRIMITIVE_ID,
            "fixtureSetDescriptorDigest": self._bootstrap.fixture_set_descriptor_digest,
            "fixtureDescriptorDigest": self._bootstrap.fixture_descriptor_digest,
            "fixtureDigest": self._fixture_digest,
            "projectBindingDigest": self._project_binding_digest,
            "runtimeBindingDigest": self._bootstrap.runtime_binding_digest,
            "desktopExecutableDigest": self._bootstrap.desktop_executable_digest,
            "backendExecutableDigest": self._bootstrap.backend_executable_digest,
            "runnerDigest": self._bootstrap.runner_digest,
            "unityPackageDigest": self._bootstrap.unity_package_digest,
            "unityEditorDigest": self._bootstrap.unity_editor_digest,
            "fixtureProjectInputDigest": self._bootstrap.fixture_project_input_digest,
            "orderedReceiptSetDigest": _digest_json(ordered_receipts),
            "authoritativeEventChainDigest": _digest_json(self._event_digests),
            "startedAt": _format_utc(self._started_at),
            "finishedAt": _format_utc(finished_at),
            "finalizedAt": _format_utc(finalized_at),
            "proofAlgorithm": LIVE_PROOF_ALGORITHM,
            "originTrust": LIVE_ORIGIN_TRUST,
            "originVerified": False,
        }
        if self._bootstrap.origin_ticket_digest:
            attestation["originTicketDigest"] = self._bootstrap.origin_ticket_digest
        proof = hmac.new(
            bytes(self._key),
            _canonical_bytes(attestation),
            hashlib.sha256,
        ).hexdigest()
        attestation["proof"] = proof
        finalization = {
            "schema": attestation_schema,
            "evidence": {
                "schema": evidence_schema,
                "runId": self._bootstrap.run_id,
                "rows": [
                    {
                        "scenarioId": MODEL_SCENARIO_ID,
                        "primitiveId": MODEL_PRIMITIVE_ID,
                        "fixtureDigest": self._fixture_digest,
                        "receipts": json.loads(json.dumps(self._receipts)),
                    }
                ],
            },
            "attestation": attestation,
        }
        _require_public_safe(finalization)
        self._state = "finalized"
        self._finalization = finalization
        for index in range(len(self._key)):
            self._key[index] = 0
        return json.loads(json.dumps(finalization))


def verify_live_finalization(
    payload: Mapping[str, Any],
    *,
    bootstrap: LiveBootstrap,
    fixture_digest: str,
    project_binding_digest: str,
    verified_at: datetime | None = None,
) -> VerifiedLiveRun:
    return _verify_live_finalization_common(
        payload,
        binding=LivePublicBinding.from_bootstrap(bootstrap),
        proof_key=bootstrap.key,
        fixture_digest=fixture_digest,
        project_binding_digest=project_binding_digest,
        verified_at=verified_at,
    )


def verify_origin_bound_live_finalization(
    payload: Mapping[str, Any],
    *,
    binding: LivePublicBinding,
    fixture_digest: str,
    project_binding_digest: str,
    verified_at: datetime | None = None,
) -> VerifiedLiveRun:
    """Validate inner semantics already authenticated by a trusted envelope.

    This function never upgrades origin trust. The caller must first verify a
    report-independent origin signature over the exact finalization bytes and
    only then replace the returned false origin state with that derived result.
    """

    if not binding.origin_ticket_digest:
        raise LiveAttestationError("trusted live origin ticket binding is missing")
    return _verify_live_finalization_common(
        payload,
        binding=binding,
        proof_key=None,
        fixture_digest=fixture_digest,
        project_binding_digest=project_binding_digest,
        verified_at=verified_at,
    )


def _verify_live_finalization_common(
    payload: Mapping[str, Any],
    *,
    binding: LivePublicBinding,
    proof_key: bytes | None,
    fixture_digest: str,
    project_binding_digest: str,
    verified_at: datetime | None,
) -> VerifiedLiveRun:
    if not isinstance(payload, Mapping):
        raise LiveAttestationError("live finalization must be an object")
    _require_public_safe(payload)
    trusted = bool(binding.origin_ticket_digest)
    attestation_schema = (
        TRUSTED_LIVE_ATTESTATION_SCHEMA if trusted else LIVE_ATTESTATION_SCHEMA
    )
    evidence_schema = TRUSTED_LIVE_EVIDENCE_SCHEMA if trusted else LIVE_EVIDENCE_SCHEMA
    attestation_fields = _TRUSTED_ATTESTATION_FIELDS if trusted else _ATTESTATION_FIELDS
    _require_exact_fields(payload, _FINALIZATION_FIELDS, "live finalization")
    if payload.get("schema") != attestation_schema:
        raise LiveAttestationError("live finalization schema mismatch")
    evidence = payload.get("evidence")
    attestation = payload.get("attestation")
    if not isinstance(evidence, Mapping) or not isinstance(attestation, Mapping):
        raise LiveAttestationError("live finalization sections are invalid")
    _require_exact_fields(evidence, _EVIDENCE_FIELDS, "live evidence")
    _require_exact_fields(attestation, attestation_fields, "live attestation")
    if evidence.get("schema") != evidence_schema:
        raise LiveAttestationError("live evidence schema mismatch")
    if attestation.get("schema") != attestation_schema:
        raise LiveAttestationError("live attestation schema mismatch")

    expected_fixture = _require_digest(fixture_digest, "fixture digest")
    expected_project = _require_digest(project_binding_digest, "project binding digest")
    expected_values = {
        "runId": binding.run_id,
        "challengeDigest": binding.challenge_digest,
        "scenarioId": MODEL_SCENARIO_ID,
        "primitiveId": MODEL_PRIMITIVE_ID,
        "fixtureSetDescriptorDigest": binding.fixture_set_descriptor_digest,
        "fixtureDescriptorDigest": binding.fixture_descriptor_digest,
        "fixtureDigest": expected_fixture,
        "projectBindingDigest": expected_project,
        "runtimeBindingDigest": binding.runtime_binding_digest,
        "desktopExecutableDigest": binding.desktop_executable_digest,
        "backendExecutableDigest": binding.backend_executable_digest,
        "runnerDigest": binding.runner_digest,
        "unityPackageDigest": binding.unity_package_digest,
        "unityEditorDigest": binding.unity_editor_digest,
        "fixtureProjectInputDigest": binding.fixture_project_input_digest,
        "proofAlgorithm": LIVE_PROOF_ALGORITHM,
        "originTrust": LIVE_ORIGIN_TRUST,
        "originVerified": False,
    }
    if trusted:
        expected_values["originTicketDigest"] = binding.origin_ticket_digest
    if any(attestation.get(key) != value for key, value in expected_values.items()):
        raise LiveAttestationError("live attestation binding mismatch")
    if evidence.get("runId") != binding.run_id:
        raise LiveAttestationError("live evidence run mismatch")

    proof = _require_digest(attestation.get("proof"), "live proof")
    if proof_key is not None:
        if len(proof_key) != 32:
            raise LiveAttestationError("live proof key length is invalid")
        unsigned = dict(attestation)
        unsigned.pop("proof", None)
        expected_proof = hmac.new(
            proof_key,
            _canonical_bytes(unsigned),
            hashlib.sha256,
        ).hexdigest()
        if not hmac.compare_digest(proof, expected_proof):
            raise LiveAttestationError("live proof mismatch")

    rows = evidence.get("rows")
    if not isinstance(rows, list) or len(rows) != 1 or not isinstance(rows[0], Mapping):
        raise LiveAttestationError("live evidence must contain exactly one row")
    row = rows[0]
    _require_exact_fields(row, _ROW_FIELDS, "live evidence row")
    if (
        row.get("scenarioId") != MODEL_SCENARIO_ID
        or row.get("primitiveId") != MODEL_PRIMITIVE_ID
        or row.get("fixtureDigest") != expected_fixture
    ):
        raise LiveAttestationError("live evidence row binding mismatch")
    receipts = row.get("receipts")
    if not isinstance(receipts, list) or len(receipts) != len(LIVE_PHASES):
        raise LiveAttestationError("live receipt set is incomplete")
    checked_receipts = [
        _validate_receipt(
            receipt,
            sequence=index,
            bootstrap=binding,
            fixture_digest=expected_fixture,
            project_binding_digest=expected_project,
        )
        for index, receipt in enumerate(receipts, start=1)
    ]
    _validate_receipt_invariants(
        checked_receipts,
        expected_fixture_project_input_digest=binding.fixture_project_input_digest,
    )
    ordered_receipts = [
        {
            "sequence": receipt["sequence"],
            "phase": receipt["phase"],
            "receiptDigest": _digest_json(receipt),
        }
        for receipt in checked_receipts
    ]
    if attestation.get("orderedReceiptSetDigest") != _digest_json(ordered_receipts):
        raise LiveAttestationError("live ordered receipt digest mismatch")
    event_digests = [receipt["authoritativeEventDigest"] for receipt in checked_receipts]
    if attestation.get("authoritativeEventChainDigest") != _digest_json(event_digests):
        raise LiveAttestationError("live authoritative event chain mismatch")

    started_at = _parse_utc(attestation.get("startedAt"))
    finished_at = _parse_utc(attestation.get("finishedAt"))
    finalized_at = _parse_utc(attestation.get("finalizedAt"))
    now = _utc_now(lambda: verified_at or datetime.now(timezone.utc))
    if started_at is None or finished_at is None or finalized_at is None:
        raise LiveAttestationError("live attestation timestamp is invalid")
    if not (started_at <= finished_at <= finalized_at <= now + _MAX_CLOCK_SKEW):
        raise LiveAttestationError("live attestation time order is invalid")
    if finished_at - started_at > _MAX_RUN_DURATION:
        raise LiveAttestationError("live attestation duration is invalid")
    if now - finalized_at > _MAX_EVIDENCE_AGE:
        raise LiveAttestationError("live attestation is stale")
    for receipt in checked_receipts:
        observed_at = _parse_utc(receipt["observedAt"])
        if observed_at is None or observed_at < started_at or observed_at > finished_at:
            raise LiveAttestationError("live receipt escaped the signed run window")

    return VerifiedLiveRun(
        run_id=binding.run_id,
        scenario_id=MODEL_SCENARIO_ID,
        primitive_id=MODEL_PRIMITIVE_ID,
        fixture_set_descriptor_digest=binding.fixture_set_descriptor_digest,
        fixture_descriptor_digest=binding.fixture_descriptor_digest,
        fixture_digest=expected_fixture,
        project_binding_digest=expected_project,
        runtime_binding_digest=binding.runtime_binding_digest,
        attestation_digest=_digest_json(attestation),
        origin_verified=False,
        started_at=_format_utc(started_at),
        finished_at=_format_utc(finished_at),
        finalized_at=_format_utc(finalized_at),
        receipts=tuple(json.loads(json.dumps(checked_receipts))),
    )


def build_live_matrix_report(fixtures: Any, verified: VerifiedLiveRun) -> dict[str, Any]:
    """Build a diagnostic matrix that can never grant trusted-origin FULL.

    ``VerifiedLiveRun`` is intentionally a public value object. A future
    independent gate must revalidate raw signed evidence and own replay state;
    caller-supplied fields and driver-produced matrix JSON are never authority.
    """

    if fixtures.descriptor_digest != verified.fixture_set_descriptor_digest:
        raise LiveAttestationError("fixture set descriptor mismatch")
    fixture_by_id = {fixture.scenario_id: fixture for fixture in fixtures.fixtures}
    fixture = fixture_by_id.get(MODEL_SCENARIO_ID)
    if (
        fixture is None
        or fixture.descriptor_digest != verified.fixture_descriptor_digest
        or not fixture.materialized
        or fixture.digest != verified.fixture_digest
    ):
        raise LiveAttestationError("live fixture is not the pinned materialized fixture")

    rows: list[dict[str, Any]] = []
    for candidate in fixtures.fixtures:
        for primitive_id in candidate.required_primitives:
            if (
                candidate.scenario_id == verified.scenario_id
                and primitive_id == verified.primitive_id
            ):
                rows.append(
                    {
                        "scenarioId": candidate.scenario_id,
                        "primitiveId": primitive_id,
                        "status": "blocked",
                        "transcriptStatus": "passed",
                        "fixtureDigest": candidate.digest,
                        "attestationDigest": verified.attestation_digest,
                        "reasons": ["live_runner_origin_not_trusted"],
                    }
                )
            else:
                rows.append(
                    {
                        "scenarioId": candidate.scenario_id,
                        "primitiveId": primitive_id,
                        "status": "blocked",
                        "transcriptStatus": "not_run",
                        "fixtureDigest": candidate.digest,
                        "attestationDigest": "",
                        "reasons": ["live_run_not_executed"],
                    }
                )
    scenarios: list[dict[str, Any]] = []
    for candidate in fixtures.fixtures:
        scenarios.append(
            {
                "scenarioId": candidate.scenario_id,
                "status": "blocked",
                "fixtureDigest": candidate.digest,
                "requiredPrimitives": list(candidate.required_primitives),
            }
        )
    full_rows = 0
    full_scenarios = 0
    report = {
        "schema": LIVE_MATRIX_SCHEMA,
        "ok": False,
        "targetOk": False,
        "transcriptOk": True,
        "runId": verified.run_id,
        "fixtureSetDescriptorDigest": fixtures.descriptor_digest,
        "runtimeBinding": {
            "filesHashed": True,
            "liveRunnerAttested": False,
            "transcriptMacVerified": True,
            "digest": verified.runtime_binding_digest,
            "attestedRows": [f"{verified.scenario_id}/{verified.primitive_id}"],
        },
        "rows": rows,
        "scenarios": scenarios,
        "summary": {
            "status": "blocked",
            "scenarioCount": len(scenarios),
            "fullScenarioCount": full_scenarios,
            "partialScenarioCount": 0,
            "blockedScenarioCount": len(scenarios) - full_scenarios,
            "requiredRowCount": len(rows),
            "fullRowCount": full_rows,
            "partialRowCount": 0,
            "blockedRowCount": len(rows) - full_rows,
        },
        "attestation": {
            "digest": verified.attestation_digest,
            "innerDigest": verified.inner_attestation_digest,
            "originSignerKeyId": verified.origin_signer_key_id,
            "originTicketDigest": verified.origin_ticket_digest,
            "originProcessGraphDigest": verified.origin_process_graph_digest,
            "originNetworkBindingDigest": verified.origin_network_binding_digest,
            "originCleanupDigest": verified.origin_cleanup_digest,
            "startedAt": verified.started_at,
            "finishedAt": verified.finished_at,
            "finalizedAt": verified.finalized_at,
        },
    }
    _require_public_safe(report)
    return report


def encode_bootstrap_frame(bootstrap: LiveBootstrap) -> bytes:
    values = [bootstrap.key, bootstrap.challenge]
    values.extend(bytes.fromhex(getattr(bootstrap, field)) for field in _BOOTSTRAP_DIGEST_FIELDS)
    if bootstrap.origin_ticket_digest:
        values.append(bytes.fromhex(bootstrap.origin_ticket_digest))
        return TRUSTED_LIVE_BOOTSTRAP_MAGIC + b"".join(values)
    return LIVE_BOOTSTRAP_MAGIC + b"".join(values)


def read_bootstrap_frame(stream: BinaryIO) -> LiveBootstrap:
    legacy_size = len(LIVE_BOOTSTRAP_MAGIC) + (2 + len(_BOOTSTRAP_DIGEST_FIELDS)) * 32
    trusted_size = legacy_size + 32
    payload = stream.read(trusted_size + 1)
    if payload.startswith(LIVE_BOOTSTRAP_MAGIC) and len(payload) == legacy_size:
        trusted = False
    elif payload.startswith(TRUSTED_LIVE_BOOTSTRAP_MAGIC) and len(payload) == trusted_size:
        trusted = True
    else:
        raise LiveAttestationError("live bootstrap frame is invalid")
    offset = len(LIVE_BOOTSTRAP_MAGIC)
    chunks = [payload[index : index + 32] for index in range(offset, len(payload), 32)]
    key, challenge, *digests = chunks
    origin_ticket_digest = digests.pop().hex() if trusted else ""
    values = dict(zip(_BOOTSTRAP_DIGEST_FIELDS, (item.hex() for item in digests), strict=True))
    return LiveBootstrap(
        key=key,
        challenge=challenge,
        origin_ticket_digest=origin_ticket_digest,
        **values,
    )


def load_packaged_live_session_from_stdin(
    *,
    environ: Mapping[str, str] | None = None,
    stream: BinaryIO | None = None,
    executable_path: Path | str | None = None,
    frozen: bool | None = None,
    now: Callable[[], datetime] | None = None,
) -> PrimitiveBasisLiveSession | None:
    environment = os.environ if environ is None else environ
    mode = environment.get(LIVE_STDIN_ENV)
    if mode is None:
        return None
    if mode != "1":
        raise LiveAttestationError("packaged live bootstrap mode is invalid")
    is_frozen = bool(getattr(sys, "frozen", False)) if frozen is None else bool(frozen)
    if not is_frozen:
        raise LiveAttestationError("packaged live bootstrap requires a frozen runtime")
    input_stream = sys.stdin.buffer if stream is None else stream
    bootstrap = read_bootstrap_frame(input_stream)
    backend_path = Path(sys.executable if executable_path is None else executable_path)
    if _stable_file_digest(backend_path) != bootstrap.backend_executable_digest:
        raise LiveAttestationError("packaged live backend digest mismatch")
    if environ is None:
        os.environ.pop(LIVE_STDIN_ENV, None)
    return PrimitiveBasisLiveSession(bootstrap, now=now)


def _validate_receipt(
    value: Any,
    *,
    sequence: int,
    bootstrap: LiveBootstrap | LivePublicBinding,
    fixture_digest: str,
    project_binding_digest: str,
) -> dict[str, Any]:
    if not isinstance(value, Mapping):
        raise LiveAttestationError("live receipt must be an object")
    trusted = bool(bootstrap.origin_ticket_digest)
    _require_exact_fields(
        value,
        _TRUSTED_RECEIPT_FIELDS if trusted else _COMMON_RECEIPT_FIELDS,
        "live receipt",
    )
    phase = LIVE_PHASES[sequence - 1]
    expected = {
        "schema": TRUSTED_LIVE_RECEIPT_SCHEMA if trusted else LIVE_RECEIPT_SCHEMA,
        "runId": bootstrap.run_id,
        "scenarioId": MODEL_SCENARIO_ID,
        "primitiveId": MODEL_PRIMITIVE_ID,
        "phase": phase,
        "sequence": sequence,
        "monotonicOrdinal": sequence,
        "fixtureDigest": fixture_digest,
        "runtimeBindingDigest": bootstrap.runtime_binding_digest,
        "projectBindingDigest": project_binding_digest,
        "status": "passed",
    }
    if trusted:
        expected["originTicketDigest"] = bootstrap.origin_ticket_digest
    if any(value.get(key) != expected_value for key, expected_value in expected.items()):
        raise LiveAttestationError("live receipt binding mismatch")
    if _parse_utc(value.get("observedAt")) is None:
        raise LiveAttestationError("live receipt timestamp is invalid")
    _require_digest(value.get("authoritativeEventDigest"), "authoritative event digest")
    facts = value.get("facts")
    if not isinstance(facts, Mapping):
        raise LiveAttestationError("live receipt facts must be an object")
    _validate_facts(phase, facts)
    return json.loads(json.dumps(value))


def _validate_facts(phase: str, facts: Mapping[str, Any]) -> None:
    _require_exact_fields(facts, _FACT_FIELDS[phase], "live receipt facts")
    for key, value in facts.items():
        if key.endswith("Digest"):
            _require_digest(value, key)
        elif key.endswith("Id"):
            _require_safe_id(value, key)
    for key in (
        "componentPresent",
        "pendingObserved",
        "approved",
        "created",
        "applied",
        "matched",
        "passed",
        "restored",
        "projectRemoved",
        "unityProcessExited",
        "bridgePortReleased",
    ):
        if key in facts and type(facts[key]) is not bool:
            raise LiveAttestationError("live boolean fact is invalid")
    for key in ("mutationCount", "count"):
        if key in facts and (type(facts[key]) is not int or facts[key] < 0):
            raise LiveAttestationError("live count fact is invalid")
    if "state" in facts and facts["state"] != "approval_pending":
        raise LiveAttestationError("live request state is invalid")
    if "targetTool" in facts:
        expected_tool = RESTORE_TARGET_TOOL if phase.startswith("restore_") else MODEL_TARGET_TOOL
        if facts["targetTool"] != expected_tool:
            raise LiveAttestationError("live target tool is invalid")


def _validate_receipt_invariants(
    receipts: list[Mapping[str, Any]],
    *,
    expected_fixture_project_input_digest: str,
) -> None:
    if len(receipts) != len(LIVE_PHASES):
        raise LiveAttestationError("live receipt set is incomplete")
    timestamps = [_parse_utc(receipt.get("observedAt")) for receipt in receipts]
    if any(value is None for value in timestamps) or any(
        left >= right for left, right in zip(timestamps, timestamps[1:])
    ):
        raise LiveAttestationError("live receipt timestamps are not strictly ordered")
    by_phase = {str(receipt["phase"]): receipt["facts"] for receipt in receipts}
    detect = by_phase["detect"]
    preview = by_phase["preview"]
    request = by_phase["request"]
    approval = by_phase["approval"]
    checkpoint = by_phase["checkpoint"]
    apply = by_phase["apply"]
    readback = by_phase["readback"]
    validation = by_phase["validation"]
    restore_request = by_phase["restore_request"]
    restore_approval = by_phase["restore_approval"]
    restore_execution = by_phase["restore_execution"]
    baseline = by_phase["baseline_comparison"]
    residue = by_phase["residue"]
    project_binding_digest = str(receipts[0]["projectBindingDigest"])

    for phase, facts in (
        ("request", request),
        ("approval", approval),
        ("checkpoint", checkpoint),
        ("apply", apply),
        ("restore_request", restore_request),
        ("restore_approval", restore_approval),
        ("restore_execution", restore_execution),
    ):
        if facts["projectBindingDigest"] != project_binding_digest:
            raise LiveAttestationError(
                f"live {phase} escaped the receipt project binding"
            )

    if detect["componentPresent"] is not False:
        raise LiveAttestationError("live baseline already contains the component")
    if (
        preview["mutationCount"] != 0
        or preview["beforeStateDigest"] != detect["stateDigest"]
        or preview["afterStateDigest"] != detect["stateDigest"]
    ):
        raise LiveAttestationError("live preview mutated state")
    binding_keys = ("targetTool", "argumentsDigest", "operationDigest", "projectBindingDigest")
    if any(request[key] != approval[key] for key in binding_keys):
        raise LiveAttestationError("live approval changed the requested operation")
    if any(request[key] != checkpoint[key] for key in binding_keys):
        raise LiveAttestationError("live checkpoint changed the requested operation")
    if any(request[key] != apply[key] for key in binding_keys):
        raise LiveAttestationError("live apply changed the requested operation")
    if checkpoint["fixtureProjectInputDigest"] != apply["fixtureProjectInputDigest"]:
        raise LiveAttestationError("live apply changed the fixed project inputs")
    if checkpoint["fixtureProjectInputDigest"] != expected_fixture_project_input_digest:
        raise LiveAttestationError("live apply escaped the fixed project inputs")
    if (
        checkpoint["unityProcessIdentityDigest"]
        != restore_execution["unityProcessIdentityDigest"]
    ):
        raise LiveAttestationError("live checkpoint restore changed Unity process identity")
    if approval["requestId"] != request["requestId"] or approval["pendingObserved"] is not True:
        raise LiveAttestationError("live approval did not observe the pending request")
    if approval["approved"] is not True or checkpoint["created"] is not True or apply["applied"] is not True:
        raise LiveAttestationError("live apply chain is incomplete")
    if checkpoint["approvalId"] != approval["approvalId"] or apply["approvalId"] != approval["approvalId"]:
        raise LiveAttestationError("live apply approval continuity failed")
    if apply["checkpointId"] != checkpoint["checkpointId"]:
        raise LiveAttestationError("live apply checkpoint continuity failed")
    checkpoint_id = checkpoint["checkpointId"]
    if readback["checkpointId"] != checkpoint_id or validation["checkpointId"] != checkpoint_id:
        raise LiveAttestationError("live post-apply evidence used another checkpoint")
    if readback["matched"] is not True or readback["expectedStateDigest"] != readback["actualStateDigest"]:
        raise LiveAttestationError("live readback did not match")
    if readback["actualStateDigest"] == detect["stateDigest"]:
        raise LiveAttestationError("live apply state did not change")
    if validation["passed"] is not True:
        raise LiveAttestationError("live validation failed")

    restore_binding_keys = (
        "targetTool",
        "checkpointId",
        "projectBindingDigest",
        "argumentsDigest",
    )
    if any(restore_request[key] != restore_approval[key] for key in restore_binding_keys):
        raise LiveAttestationError("live restore approval changed the request")
    if any(restore_request[key] != restore_execution[key] for key in restore_binding_keys):
        raise LiveAttestationError("live restore execution changed the request")
    if restore_request["checkpointId"] != checkpoint_id:
        raise LiveAttestationError("live restore targeted another checkpoint")
    if restore_approval["requestId"] != restore_request["requestId"] or restore_approval["pendingObserved"] is not True:
        raise LiveAttestationError("live restore approval did not observe pending")
    if restore_approval["approved"] is not True or restore_execution["restored"] is not True:
        raise LiveAttestationError("live restore chain is incomplete")
    if restore_execution["approvalId"] != restore_approval["approvalId"]:
        raise LiveAttestationError("live restore approval continuity failed")
    if request["requestId"] == restore_request["requestId"]:
        raise LiveAttestationError("live restore request was reused")
    if approval["approvalId"] == restore_approval["approvalId"]:
        raise LiveAttestationError("live restore approval was reused")
    if apply["executionId"] == restore_execution["executionId"]:
        raise LiveAttestationError("live restore execution was reused")
    if baseline["checkpointId"] != checkpoint_id or residue["checkpointId"] != checkpoint_id:
        raise LiveAttestationError("live cleanup used another checkpoint")
    if (
        baseline["matched"] is not True
        or baseline["expectedBaselineDigest"] != detect["stateDigest"]
        or baseline["actualStateDigest"] != detect["stateDigest"]
    ):
        raise LiveAttestationError("live baseline was not restored")
    if (
        residue["count"] != 0
        or residue["inventoryDigest"] != detect["inventoryDigest"]
        or residue["projectRemoved"] is not True
        or residue["unityProcessExited"] is not True
        or residue["bridgePortReleased"] is not True
    ):
        raise LiveAttestationError("live residue remains")


def _copy_public_mapping(value: Mapping[str, Any], label: str) -> dict[str, Any]:
    if not isinstance(value, Mapping):
        raise LiveAttestationError(f"{label} must be an object")
    copied = json.loads(json.dumps(value, ensure_ascii=True))
    _require_public_safe(copied)
    return copied


def _stable_file_digest(path: Path) -> str:
    if not path.is_file() or _is_reparse_point(path):
        raise LiveAttestationError("packaged live backend is unavailable")
    digest = hashlib.sha256()
    try:
        with path.open("rb") as handle:
            before = os.fstat(handle.fileno())
            for chunk in iter(lambda: handle.read(1024 * 1024), b""):
                digest.update(chunk)
            after = os.fstat(handle.fileno())
        current = path.stat()
    except OSError as exc:
        raise LiveAttestationError("packaged live backend could not be hashed") from exc
    identity = lambda value: (
        value.st_dev,
        value.st_ino,
        value.st_size,
        value.st_mtime_ns,
    )
    if identity(before) != identity(after) or identity(after) != identity(current):
        raise LiveAttestationError("packaged live backend changed during verification")
    return digest.hexdigest()


def _is_reparse_point(path: Path) -> bool:
    try:
        info = path.lstat()
    except OSError:
        return False
    reparse_flag = getattr(stat, "FILE_ATTRIBUTE_REPARSE_POINT", 0x400)
    return path.is_symlink() or bool(getattr(info, "st_file_attributes", 0) & reparse_flag)


def _require_exact_fields(value: Mapping[str, Any], expected: set[str], label: str) -> None:
    if set(value) != expected:
        raise LiveAttestationError(f"{label} fields mismatch")


def _require_digest(value: Any, label: str) -> str:
    if not isinstance(value, str) or _DIGEST_RE.fullmatch(value) is None:
        raise LiveAttestationError(f"{label} is invalid")
    return value


def _require_safe_id(value: Any, label: str) -> str:
    if not isinstance(value, str) or _SAFE_ID_RE.fullmatch(value) is None:
        raise LiveAttestationError(f"{label} is invalid")
    return value


def _require_public_safe(value: Any) -> None:
    if redact_public_evidence(value) != value:
        raise LiveAttestationError("private live evidence rejected")


def _canonical_bytes(value: Any) -> bytes:
    return json.dumps(
        value,
        ensure_ascii=True,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")


def _digest_json(value: Any) -> str:
    return hashlib.sha256(_canonical_bytes(value)).hexdigest()


def _utc_now(clock: Callable[[], datetime]) -> datetime:
    value = clock()
    if value.tzinfo is None:
        raise LiveAttestationError("live timestamp must be timezone-aware")
    return value.astimezone(timezone.utc)


def _format_utc(value: datetime) -> str:
    return value.astimezone(timezone.utc).isoformat(timespec="microseconds").replace(
        "+00:00", "Z"
    )


def _parse_utc(value: Any) -> datetime | None:
    if not isinstance(value, str) or not value.endswith("Z"):
        return None
    try:
        parsed = datetime.fromisoformat(value[:-1] + "+00:00")
    except ValueError:
        return None
    return parsed.astimezone(timezone.utc)
