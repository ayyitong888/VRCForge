"""Fail-closed source contract for protected primitive-basis evidence.

The adapter is intentionally not wired to a native producer. It can prove the
raw-byte verification and projection contract in tests, but it is not packaged
runtime evidence and must not be presented as a production matrix result.
"""

from __future__ import annotations

import base64
import hashlib
import json
import re
import threading
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from typing import Any, Mapping, Sequence

from cryptography.exceptions import InvalidSignature
from cryptography.hazmat.primitives import hashes
from cryptography.hazmat.primitives.asymmetric import ec, utils

from diagnostic_privacy import redact_public_evidence
from primitive_basis_live_attestation import (
    MODEL_PRIMITIVE_ID,
    MODEL_SCENARIO_ID,
    LiveAttestationError,
)
from primitive_basis_matrix import (
    MATRIX_SCHEMA,
    SCENARIO_DEFINITIONS,
    FixtureSet,
    PrimitiveFixture,
)
from primitive_basis_origin_attestation import (
    ORIGIN_ENVELOPE_SCHEMA_V1,
    ORIGIN_ENVELOPE_SCHEMA_V2,
    ORIGIN_PROOF_ALGORITHM,
    OriginExpectedBinding,
    OriginTrustContext,
    verify_trusted_live_origin,
)


AUTHORITY_BUNDLE_SCHEMA = "vrcforge.primitive_basis_authority_evidence_bundle.v1"
AUTHORITY_BINDING_SCHEMA = "vrcforge.primitive_basis_authority_binding.v1"
PACKAGE_BINDING_SCHEMA = "vrcforge.primitive_basis_package_binding.v1"
AUTHORITY_ROW_SCHEMA = "vrcforge.primitive_basis_authority_row.v1"
LEDGER_SNAPSHOT_SCHEMA = "vrcforge.primitive_basis_authority_ledger_snapshot.v1"
LEDGER_RECEIPT_SCHEMA_V1 = (
    "vrcforge.primitive_basis_authority_completed_receipt.v1"
)
LEDGER_RECEIPT_SCHEMA_V2 = (
    "vrcforge.primitive_basis_authority_completed_receipt.v2"
)
LEDGER_RECEIPT_SCHEMA = LEDGER_RECEIPT_SCHEMA_V2
PROTECTED_PROJECTION_SCHEMA = (
    "vrcforge.primitive_basis_protected_evidence_projection.v1"
)
AUTHORITY_SERVICE_RESPONSE_SCHEMA = (
    "vrcforge.primitive_evidence_authority_response.v1"
)
PROJECTION_COMMIT_RECEIPT_SCHEMA = (
    "vrcforge.primitive_evidence_authority_projection_commit_receipt.v2"
)
PROJECTION_COMMIT_READBACK_SCHEMA = (
    "vrcforge.primitive_evidence_authority_projection_commit_readback.v1"
)
PROJECTION_COMMIT_PROOF_ALGORITHM = "p256-sha256-raw-rs-low-s"
PROJECTION_RESPONSE_SUMMARY_SCHEMA = (
    "vrcforge.primitive_basis_verified_projection_response_summary.v1"
)
BINARY_LEDGER_TERMINAL_SCHEMA = (
    "vrcforge.primitive_basis_binary_ledger_terminal.v1"
)
BINARY_LEDGER_READBACK_SCHEMA = (
    "vrcforge.primitive_basis_binary_ledger_reopen_readback.v1"
)
_RUN_ADMISSION_DOMAIN = b"vrcforge-primitive-basis-run-admission-v1\0"
_PROJECTION_COMMIT_RECEIPT_DIGEST_DOMAIN = (
    b"vrcforge-authority-projection-commit-receipt-v2\0"
)
_LEDGER_IDENTITY_DIGEST_DOMAIN = b"vrcforge-authority-ledger-identity-v1\0"
_RUNTIME_IDENTITY_DIGEST_DOMAIN = b"vrcforge-authority-runtime-identity-v1\0"
_RUNTIME_TICKET_DIGEST_DOMAIN = b"vrcforge-authority-runtime-ticket-v1\0"

_DIGEST_RE = re.compile(r"[0-9a-f]{64}")
_SAFE_ID_RE = re.compile(r"[A-Za-z0-9][A-Za-z0-9._-]{0,127}")
_REQUEST_ID_RE = re.compile(r"[A-Za-z0-9][A-Za-z0-9._:-]{0,127}")
_VERSION_RE = re.compile(r"[0-9]+\.[0-9]+\.[0-9]+(?:-[0-9A-Za-z.-]+)?")
_BASE64URL_RE = re.compile(r"[A-Za-z0-9_-]+")
_RAW_P256_SIGNATURE_RE = re.compile(r"[0-9a-f]{128}")
_MAX_BUNDLE_BYTES = 8 * 1024 * 1024
_MAX_LEDGER_BYTES = 2 * 1024 * 1024
_MAX_AUTHORITY_SERVICE_RESPONSE_BYTES = 16 * 1024 * 1024
_MAX_PROJECTION_COMMIT_RECEIPT_BYTES = 64 * 1024
_MAX_VERIFIED_AUTHORITY_RESULT_PROJECTION_BYTES = 10 * 1024 * 1024 + 64 * 1024
_MAX_CLOCK_SKEW = timedelta(minutes=5)
_MAX_EVIDENCE_AGE = timedelta(hours=24)
_MAX_U64 = (1 << 64) - 1
_P256_ORDER = int(
    "ffffffff00000000ffffffffffffffffbce6faada7179e84f3b9cac2fc632551", 16
)

_AUTHORITY_BINDING_FIELDS = {
    "schema",
    "policyId",
    "authorityGenerationDigest",
    "protectedManifestDigest",
    "installedLayoutDigest",
    "serviceExecutableDigest",
    "controllerExecutableDigest",
    "installHelperExecutableDigest",
    "ledgerIdentityDigest",
}
_PACKAGE_BINDING_FIELDS = {
    "schema",
    "version",
    "manifestDigest",
    "portableDigest",
    "desktopExecutableDigest",
    "backendExecutableDigest",
    "backendTreeDigest",
    "runnerDigest",
    "unityPackageDigest",
    "packagedUnityToolTreeDigest",
    "runtimeUnityToolTreeDigest",
    "unityEditorDigest",
    "bridgeLauncherExecutableDigest",
    "bridgeListenerExecutableDigest",
    "connectorDigest",
    "serverDigest",
    "dependencySetDigest",
    "runtimeBindingDigest",
}
_BUNDLE_FIELDS = {
    "schema",
    "bundleId",
    "proofAlgorithm",
    "policyId",
    "signerKeyId",
    "authorityBinding",
    "authorityBindingDigest",
    "packageBinding",
    "packageBindingDigest",
    "fixtureSetDescriptorDigest",
    "fixtureSetDigest",
    "ledgerSnapshotDigest",
    "rows",
    "signedAt",
    "signature",
}
_ROW_FIELDS = {
    "schema",
    "scenarioId",
    "primitiveId",
    "fixtureDescriptorDigest",
    "fixtureDigest",
    "fixtureProjectInputDigest",
    "projectBindingDigest",
    "finalization",
    "finalizationDigest",
    "originEnvelope",
    "originEnvelopeDigest",
}
_LEDGER_SNAPSHOT_FIELDS = {
    "schema",
    "authorityGenerationDigest",
    "ledgerIdentityDigest",
    "firstReceiptOrdinal",
    "lastReceiptOrdinal",
    "initialReceiptDigest",
    "terminalReceiptDigest",
    "receipts",
}
_LEDGER_RECEIPT_FIELDS_V1 = {
    "schema",
    "ordinal",
    "previousReceiptDigest",
    "receiptDigest",
    "ticketDigest",
    "runId",
    "scenarioId",
    "primitiveId",
    "state",
    "resultDigest",
    "originEnvelopeDigest",
    "cleanupDigest",
    "issuedAt",
    "consumedAt",
    "completedAt",
    "binaryLedgerTerminal",
    "binaryLedgerTerminalDigest",
}
_LEDGER_RECEIPT_FIELDS_V2 = _LEDGER_RECEIPT_FIELDS_V1 | {"originTicketDigest"}
_BINARY_LEDGER_TERMINAL_FIELDS = {
    "schema",
    "event",
    "authorityGenerationDigest",
    "ledgerIdentityDigest",
    "predecessorSequence",
    "terminalSequence",
    "predecessorFrameDigest",
    "terminalFrameDigest",
    "terminalTicketDigest",
    "terminalResultDigest",
    "anchorSequence",
    "anchorFrameDigest",
    "anchorTicketDigest",
    "runBindingDigest",
    "preparedReceiptDigest",
    "armedReceiptDigest",
    "policySnapshotDigest",
    "recoveryBundleDigest",
    "runAdmissionDigest",
    "originEnvelopeDigest",
    "cleanupDigest",
    "anchorRecordDigest",
    "reopenReadback",
    "reopenReadbackDigest",
}
_BINARY_LEDGER_READBACK_FIELDS = {
    "schema",
    "readbackKind",
    "authorityGenerationDigest",
    "ledgerIdentityDigest",
    "ledgerFileDigest",
    "anchorFileDigest",
    "ledgerFileIdentityDigest",
    "anchorFileIdentityDigest",
    "ledgerLength",
    "anchorLength",
    "frameCount",
    "activeTicketCount",
    "latestFrameDigest",
    "anchorRecordDigest",
    "terminalSequence",
    "terminalFrameDigest",
    "terminalTicketDigest",
}
_AUTHORITY_SERVICE_RESPONSE_FIELDS = {"schema", "ok", "command", "result"}
_AUTHORITY_SERVICE_RESULT_FIELDS = {
    "state",
    "requestId",
    "size",
    "sha256",
    "encoding",
    "bytesBase64Url",
    "projectionCommitReceipt",
}
_PROTECTED_PROJECTION_FIELDS = {
    "schema",
    "authorityBundle",
    "authorityBundleDigest",
    "ledgerSnapshot",
    "ledgerSnapshotDigest",
}
_PROJECTION_COMMIT_RECEIPT_FIELDS = {
    "schema",
    "event",
    "proofAlgorithm",
    "signerKeyId",
    "authorityGenerationDigest",
    "ledgerIdentityDigest",
    "ticketDigest",
    "runBindingDigest",
    "projectionDigest",
    "projectionLength",
    "terminalSequence",
    "terminalFrameDigest",
    "terminalTicketDigest",
    "anchorSequence",
    "anchorFrameDigest",
    "anchorTicketDigest",
    "anchorRecordDigest",
    "reopenReadback",
    "receiptDigest",
    "signatureP256",
}
_PROJECTION_COMMIT_READBACK_FIELDS = {
    "schema",
    "readbackKind",
    "ledgerFileDigest",
    "anchorFileDigest",
    "ledgerFileIdentityDigest",
    "anchorFileIdentityDigest",
    "ledgerLength",
    "anchorLength",
    "frameCount",
    "activeTicketCount",
    "latestFrameDigest",
}


class ProtectedEvidenceError(ValueError):
    """A raw protected-authority artifact failed a closed verification gate."""

    def __init__(self, code: str) -> None:
        super().__init__(code)
        self.code = code


@dataclass(frozen=True)
class ProtectedAuthorityBinding:
    policy_id: str
    authority_generation_digest: str
    protected_manifest_digest: str
    installed_layout_digest: str
    service_executable_digest: str
    controller_executable_digest: str
    install_helper_executable_digest: str
    ledger_identity_digest: str

    def __post_init__(self) -> None:
        _require_safe_id(self.policy_id, "authority_binding_invalid")
        for field_name in self.__dataclass_fields__:
            if field_name != "policy_id":
                _require_digest(
                    getattr(self, field_name), "authority_binding_invalid"
                )

    def to_payload(self) -> dict[str, str]:
        return {
            "schema": AUTHORITY_BINDING_SCHEMA,
            "policyId": self.policy_id,
            "authorityGenerationDigest": self.authority_generation_digest,
            "protectedManifestDigest": self.protected_manifest_digest,
            "installedLayoutDigest": self.installed_layout_digest,
            "serviceExecutableDigest": self.service_executable_digest,
            "controllerExecutableDigest": self.controller_executable_digest,
            "installHelperExecutableDigest": self.install_helper_executable_digest,
            "ledgerIdentityDigest": self.ledger_identity_digest,
        }


@dataclass(frozen=True)
class VerifiedAuthorityProjectionResponse:
    """A fixed service response whose projection receipt was fully verified."""

    projection_bytes: bytes = field(repr=False)
    response_digest: str
    projection_digest: str
    projection_length: int
    receipt_digest: str
    receipt_body_digest: str
    request_id: str
    ticket_digest: str
    run_binding_digest: str
    authority_generation_digest: str
    signer_key_id: str
    ledger_identity_digest: str
    terminal_sequence: int
    terminal_frame_digest: str
    anchor_record_digest: str
    readback_digest: str
    authority_bundle_digest: str
    ledger_snapshot_digest: str

    def __post_init__(self) -> None:
        if (
            type(self.projection_bytes) is not bytes
            or not self.projection_bytes
            or len(self.projection_bytes)
            > _MAX_VERIFIED_AUTHORITY_RESULT_PROJECTION_BYTES
            or type(self.projection_length) is not int
            or self.projection_length != len(self.projection_bytes)
            or self.projection_digest
            != hashlib.sha256(self.projection_bytes).hexdigest()
        ):
            raise ProtectedEvidenceError("authority_verified_projection_invalid")
        _require_request_id(self.request_id, "authority_verified_projection_invalid")
        _require_nonnegative_int(
            self.terminal_sequence, "authority_verified_projection_invalid"
        )
        for field_name in (
            "response_digest",
            "projection_digest",
            "receipt_digest",
            "receipt_body_digest",
            "ticket_digest",
            "run_binding_digest",
            "authority_generation_digest",
            "signer_key_id",
            "ledger_identity_digest",
            "terminal_frame_digest",
            "anchor_record_digest",
            "readback_digest",
            "authority_bundle_digest",
            "ledger_snapshot_digest",
        ):
            _require_digest(
                getattr(self, field_name), "authority_verified_projection_invalid"
            )

    def evidence_summary(self) -> dict[str, Any]:
        """Return the public-safe binding summary without raw evidence or signatures."""

        summary = {
            "schema": PROJECTION_RESPONSE_SUMMARY_SCHEMA,
            "responseDigest": self.response_digest,
            "requestId": self.request_id,
            "projectionDigest": self.projection_digest,
            "projectionLength": self.projection_length,
            "authorityBundleDigest": self.authority_bundle_digest,
            "ledgerSnapshotDigest": self.ledger_snapshot_digest,
            "receiptDigest": self.receipt_digest,
            "receiptBodyDigest": self.receipt_body_digest,
            "ticketDigest": self.ticket_digest,
            "runBindingDigest": self.run_binding_digest,
            "authorityGenerationDigest": self.authority_generation_digest,
            "signerKeyId": self.signer_key_id,
            "ledgerIdentityDigest": self.ledger_identity_digest,
            "terminalSequence": self.terminal_sequence,
            "terminalFrameDigest": self.terminal_frame_digest,
            "anchorRecordDigest": self.anchor_record_digest,
            "readbackDigest": self.readback_digest,
        }
        _require_public_safe(summary, "authority_projection_summary_private_value")
        return summary


@dataclass(frozen=True)
class _ProjectionCommitLedgerBinding:
    authority_generation_digest: str
    ledger_identity_digest: str
    ticket_digest: str
    run_binding_digest: str
    terminal_sequence: int
    terminal_frame_digest: str
    anchor_record_digest: str
    ledger_file_digest: str
    anchor_file_digest: str
    ledger_file_identity_digest: str
    anchor_file_identity_digest: str
    ledger_length: int
    anchor_length: int
    frame_count: int
    active_ticket_count: int


@dataclass(frozen=True)
class _VerifiedProjectionCommitReadback:
    readback_digest: str
    ledger_file_digest: str
    anchor_file_digest: str
    ledger_file_identity_digest: str
    anchor_file_identity_digest: str
    ledger_length: int
    anchor_length: int
    frame_count: int
    active_ticket_count: int


@dataclass(frozen=True)
class _VerifiedProjectionCommitReceipt:
    receipt_digest: str
    receipt_body_digest: str
    readback_digest: str
    binding: _ProjectionCommitLedgerBinding


@dataclass(frozen=True)
class _VerifiedBinaryLedgerTerminal:
    terminal_digest: str
    binding: _ProjectionCommitLedgerBinding


@dataclass(frozen=True)
class _VerifiedProjectionWrapper:
    authority_bundle_bytes: bytes = field(repr=False)
    ledger_snapshot_bytes: bytes = field(repr=False)
    authority_bundle_digest: str
    ledger_snapshot_digest: str


@dataclass(frozen=True)
class _VerifiedFixedAuthorityProjection:
    response: VerifiedAuthorityProjectionResponse
    receipt: _VerifiedProjectionCommitReceipt
    wrapper: _VerifiedProjectionWrapper


@dataclass(frozen=True)
class ProtectedPackageBinding:
    version: str
    manifest_digest: str
    portable_digest: str
    desktop_executable_digest: str
    backend_executable_digest: str
    backend_tree_digest: str
    runner_digest: str
    unity_package_digest: str
    packaged_unity_tool_tree_digest: str
    runtime_unity_tool_tree_digest: str
    unity_editor_digest: str
    bridge_launcher_executable_digest: str
    bridge_listener_executable_digest: str
    connector_digest: str
    server_digest: str
    dependency_set_digest: str
    runtime_binding_digest: str

    def __post_init__(self) -> None:
        if not isinstance(self.version, str) or _VERSION_RE.fullmatch(self.version) is None:
            raise ProtectedEvidenceError("authority_package_binding_invalid")
        for field_name in self.__dataclass_fields__:
            if field_name != "version":
                _require_digest(
                    getattr(self, field_name), "authority_package_binding_invalid"
                )

    def to_payload(self) -> dict[str, str]:
        return {
            "schema": PACKAGE_BINDING_SCHEMA,
            "version": self.version,
            "manifestDigest": self.manifest_digest,
            "portableDigest": self.portable_digest,
            "desktopExecutableDigest": self.desktop_executable_digest,
            "backendExecutableDigest": self.backend_executable_digest,
            "backendTreeDigest": self.backend_tree_digest,
            "runnerDigest": self.runner_digest,
            "unityPackageDigest": self.unity_package_digest,
            "packagedUnityToolTreeDigest": self.packaged_unity_tool_tree_digest,
            "runtimeUnityToolTreeDigest": self.runtime_unity_tool_tree_digest,
            "unityEditorDigest": self.unity_editor_digest,
            "bridgeLauncherExecutableDigest": self.bridge_launcher_executable_digest,
            "bridgeListenerExecutableDigest": self.bridge_listener_executable_digest,
            "connectorDigest": self.connector_digest,
            "serverDigest": self.server_digest,
            "dependencySetDigest": self.dependency_set_digest,
            "runtimeBindingDigest": self.runtime_binding_digest,
        }

    def origin_binding(
        self,
        *,
        fixture_set_descriptor_digest: str,
        fixture_descriptor_digest: str,
        fixture_project_input_digest: str,
        fixture_digest: str,
    ) -> OriginExpectedBinding:
        return OriginExpectedBinding(
            manifest_digest=self.manifest_digest,
            portable_digest=self.portable_digest,
            desktop_executable_digest=self.desktop_executable_digest,
            backend_executable_digest=self.backend_executable_digest,
            backend_tree_digest=self.backend_tree_digest,
            runner_digest=self.runner_digest,
            unity_package_digest=self.unity_package_digest,
            packaged_unity_tool_tree_digest=self.packaged_unity_tool_tree_digest,
            runtime_unity_tool_tree_digest=self.runtime_unity_tool_tree_digest,
            unity_editor_digest=self.unity_editor_digest,
            bridge_launcher_executable_digest=self.bridge_launcher_executable_digest,
            bridge_listener_executable_digest=self.bridge_listener_executable_digest,
            connector_digest=self.connector_digest,
            server_digest=self.server_digest,
            dependency_set_digest=self.dependency_set_digest,
            fixture_set_descriptor_digest=fixture_set_descriptor_digest,
            fixture_descriptor_digest=fixture_descriptor_digest,
            fixture_project_input_digest=fixture_project_input_digest,
            fixture_digest=fixture_digest,
            runtime_binding_digest=self.runtime_binding_digest,
        )


@dataclass(frozen=True)
class ProtectedRowBinding:
    scenario_id: str
    primitive_id: str
    fixture_project_input_digest: str
    project_binding_digest: str

    def __post_init__(self) -> None:
        if self.scenario_id not in SCENARIO_DEFINITIONS or self.primitive_id not in (
            SCENARIO_DEFINITIONS[self.scenario_id]
        ):
            raise ProtectedEvidenceError("authority_expected_row_binding_invalid")
        _require_digest(
            self.fixture_project_input_digest,
            "authority_expected_row_binding_invalid",
        )
        _require_digest(
            self.project_binding_digest,
            "authority_expected_row_binding_invalid",
        )


class ProtectedEvidenceReplayGuard:
    """Atomic process-local replay guard for already verified raw bundles.

    Production integration must retain the protected service ledger as the
    durable source of truth. This guard additionally prevents the same raw
    bundle, ticket, or terminal frame from being accepted twice in one verifier
    lifetime.
    """

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._bundle_digests: set[str] = set()
        self._ticket_digests: set[str] = set()
        self._receipt_digests: set[str] = set()
        self._binary_terminal_digests: set[str] = set()

    def consume(
        self,
        *,
        bundle_digest: str,
        ticket_digests: Sequence[str],
        receipt_digests: Sequence[str],
        binary_terminal_digests: Sequence[str],
    ) -> None:
        bundle = _require_digest(bundle_digest, "authority_bundle_replayed")
        tickets = tuple(
            _require_digest(value, "authority_bundle_replayed")
            for value in ticket_digests
        )
        receipts = tuple(
            _require_digest(value, "authority_bundle_replayed")
            for value in receipt_digests
        )
        terminals = tuple(
            _require_digest(value, "authority_bundle_replayed")
            for value in binary_terminal_digests
        )
        if (
            len(set(tickets)) != len(tickets)
            or len(set(receipts)) != len(receipts)
            or len(set(terminals)) != len(terminals)
        ):
            raise ProtectedEvidenceError("authority_bundle_replayed")
        with self._lock:
            if (
                bundle in self._bundle_digests
                or self._ticket_digests.intersection(tickets)
                or self._receipt_digests.intersection(receipts)
                or self._binary_terminal_digests.intersection(terminals)
            ):
                raise ProtectedEvidenceError("authority_bundle_replayed")
            self._bundle_digests.add(bundle)
            self._ticket_digests.update(tickets)
            self._receipt_digests.update(receipts)
            self._binary_terminal_digests.update(terminals)


def verify_fixed_authority_projection_response(
    raw_response: bytes,
    *,
    trust_context: OriginTrustContext,
    authority_binding: ProtectedAuthorityBinding,
    expected_request_id: str,
    expected_ticket_digest: str,
    expected_run_binding_digest: str,
    verified_at: datetime | None = None,
) -> VerifiedAuthorityProjectionResponse:
    """Verify one exact fixed-service projection response and its signed receipt.

    This lower-level inspection gate deliberately returns the still-canonical
    projection bytes without deriving the runtime ticket or invoking the matrix
    projector. Matrix verdict callers must use
    ``verify_fixed_authority_projection_matrix`` instead of composing the two
    verifiers themselves.
    """

    return _verify_fixed_authority_projection_response(
        raw_response,
        trust_context=trust_context,
        authority_binding=authority_binding,
        expected_request_id=expected_request_id,
        expected_ticket_digest=expected_ticket_digest,
        expected_run_binding_digest=expected_run_binding_digest,
        verified_at=verified_at,
    ).response


def verify_fixed_authority_projection_matrix(
    raw_response: bytes,
    *,
    trust_context: OriginTrustContext,
    authority_binding: ProtectedAuthorityBinding,
    package_binding: ProtectedPackageBinding,
    fixtures: FixtureSet,
    expected_rows: Sequence[ProtectedRowBinding],
    replay_guard: ProtectedEvidenceReplayGuard,
    expected_request_id: str,
    expected_ticket_digest: str,
    expected_run_binding_digest: str,
    verified_at: datetime | None = None,
) -> dict[str, Any]:
    """Verify the fixed response and project its exact embedded matrix evidence."""

    try:
        verified = _verify_fixed_authority_projection_response(
            raw_response,
            trust_context=trust_context,
            authority_binding=authority_binding,
            expected_request_id=expected_request_id,
            expected_ticket_digest=expected_ticket_digest,
            expected_run_binding_digest=expected_run_binding_digest,
            verified_at=verified_at,
        )
        derived_ticket_digest = _projection_runtime_ticket_digest(
            authority_binding=authority_binding,
            signer_key_id=trust_context.signer_key_id,
            request_id=verified.response.request_id,
        )
        if derived_ticket_digest != verified.response.ticket_digest:
            raise ProtectedEvidenceError(
                "authority_projection_commit_runtime_ticket_mismatch"
            )
        return _verify_and_project(
            verified.wrapper.authority_bundle_bytes,
            verified.wrapper.ledger_snapshot_bytes,
            trust_context=trust_context,
            authority_binding=authority_binding,
            package_binding=package_binding,
            fixtures=fixtures,
            expected_rows=expected_rows,
            replay_guard=replay_guard,
            verified_at=verified_at,
            expected_projection_commit=verified.receipt,
        )
    except ProtectedEvidenceError as exc:
        return _safe_projection_blocked_report(fixtures, exc.code)
    except LiveAttestationError:
        return _safe_projection_blocked_report(fixtures, "authority_origin_invalid")
    except RecursionError:
        return _safe_projection_blocked_report(
            fixtures, "authority_bundle_nesting_invalid"
        )
    except Exception:
        return _safe_projection_blocked_report(
            fixtures, "authority_projection_verification_failed"
        )


def _verify_fixed_authority_projection_response(
    raw_response: bytes,
    *,
    trust_context: OriginTrustContext,
    authority_binding: ProtectedAuthorityBinding,
    expected_request_id: str,
    expected_ticket_digest: str,
    expected_run_binding_digest: str,
    verified_at: datetime | None,
) -> _VerifiedFixedAuthorityProjection:
    public_key = _validate_projection_commit_trust(
        trust_context=trust_context,
        authority_binding=authority_binding,
        verified_at=verified_at,
    )
    request_id = _require_request_id(
        expected_request_id, "authority_service_request_mismatch"
    )
    ticket_digest = _require_digest(
        expected_ticket_digest,
        "authority_projection_commit_request_binding_mismatch",
    )
    run_binding_digest = _require_digest(
        expected_run_binding_digest,
        "authority_projection_commit_request_binding_mismatch",
    )
    response = _parse_canonical_object(
        raw_response,
        maximum_size=_MAX_AUTHORITY_SERVICE_RESPONSE_BYTES,
        invalid_code="authority_service_response_invalid",
        canonical_code="authority_service_response_not_canonical",
        raw_code="authority_raw_service_response_required",
    )
    _require_exact_fields(
        response,
        _AUTHORITY_SERVICE_RESPONSE_FIELDS,
        "authority_service_response_shape_invalid",
    )
    if (
        response.get("schema") != AUTHORITY_SERVICE_RESPONSE_SCHEMA
        or response.get("command") != "getResult"
        or response.get("ok") is not True
    ):
        raise ProtectedEvidenceError("authority_service_response_policy_invalid")

    result = response.get("result")
    if not isinstance(result, Mapping):
        raise ProtectedEvidenceError("authority_service_result_shape_invalid")
    _require_exact_fields(
        result,
        _AUTHORITY_SERVICE_RESULT_FIELDS,
        "authority_service_result_shape_invalid",
    )
    if result.get("state") != "exact":
        raise ProtectedEvidenceError("authority_service_result_policy_invalid")
    result_request_id = _require_request_id(
        result.get("requestId"), "authority_service_request_mismatch"
    )
    if result_request_id != request_id:
        raise ProtectedEvidenceError("authority_service_request_mismatch")
    if result.get("encoding") != "base64url-no-pad":
        raise ProtectedEvidenceError("authority_service_projection_encoding_invalid")

    projection_length = _require_nonnegative_int(
        result.get("size"), "authority_service_projection_size_invalid"
    )
    if (
        projection_length == 0
        or projection_length > _MAX_VERIFIED_AUTHORITY_RESULT_PROJECTION_BYTES
    ):
        raise ProtectedEvidenceError("authority_service_projection_size_invalid")
    projection_bytes = _decode_bounded_base64url(
        result.get("bytesBase64Url"),
        maximum_size=_MAX_VERIFIED_AUTHORITY_RESULT_PROJECTION_BYTES,
        code="authority_service_projection_encoding_invalid",
    )
    if len(projection_bytes) != projection_length:
        raise ProtectedEvidenceError("authority_service_projection_size_mismatch")
    projection_digest = _require_digest(
        result.get("sha256"), "authority_service_projection_digest_mismatch"
    )
    if hashlib.sha256(projection_bytes).hexdigest() != projection_digest:
        raise ProtectedEvidenceError("authority_service_projection_digest_mismatch")

    wrapper = _verify_projection_wrapper(projection_bytes)
    receipt = result.get("projectionCommitReceipt")
    receipt_summary = _verify_projection_commit_receipt(
        receipt,
        public_key=public_key,
        trust_context=trust_context,
        authority_binding=authority_binding,
        expected_ticket_digest=ticket_digest,
        expected_run_binding_digest=run_binding_digest,
        projection_digest=projection_digest,
        projection_length=projection_length,
    )
    verified_response = VerifiedAuthorityProjectionResponse(
        projection_bytes=projection_bytes,
        response_digest=hashlib.sha256(raw_response).hexdigest(),
        projection_digest=projection_digest,
        projection_length=projection_length,
        receipt_digest=receipt_summary.receipt_digest,
        receipt_body_digest=receipt_summary.receipt_body_digest,
        request_id=result_request_id,
        ticket_digest=ticket_digest,
        run_binding_digest=run_binding_digest,
        authority_generation_digest=authority_binding.authority_generation_digest,
        signer_key_id=trust_context.signer_key_id,
        ledger_identity_digest=authority_binding.ledger_identity_digest,
        terminal_sequence=receipt_summary.binding.terminal_sequence,
        terminal_frame_digest=receipt_summary.binding.terminal_frame_digest,
        anchor_record_digest=receipt_summary.binding.anchor_record_digest,
        readback_digest=receipt_summary.readback_digest,
        authority_bundle_digest=wrapper.authority_bundle_digest,
        ledger_snapshot_digest=wrapper.ledger_snapshot_digest,
    )
    return _VerifiedFixedAuthorityProjection(
        response=verified_response,
        receipt=receipt_summary,
        wrapper=wrapper,
    )


def verify_and_project_protected_matrix(
    raw_bundle: bytes,
    raw_ledger_snapshot: bytes,
    *,
    trust_context: OriginTrustContext,
    authority_binding: ProtectedAuthorityBinding,
    package_binding: ProtectedPackageBinding,
    fixtures: FixtureSet,
    expected_rows: Sequence[ProtectedRowBinding],
    replay_guard: ProtectedEvidenceReplayGuard,
    verified_at: datetime | None = None,
) -> dict[str, Any]:
    """Reverify raw protected evidence and directly project matrix verdicts.

    No deserialized report, ``VerifiedLiveRun`` instance, or caller-supplied
    success flag is accepted. Any malformed or mismatched present row blocks
    the entire raw bundle. Missing rows remain explicitly BLOCKED.
    """

    try:
        return _verify_and_project(
            raw_bundle,
            raw_ledger_snapshot,
            trust_context=trust_context,
            authority_binding=authority_binding,
            package_binding=package_binding,
            fixtures=fixtures,
            expected_rows=expected_rows,
            replay_guard=replay_guard,
            verified_at=verified_at,
        )
    except ProtectedEvidenceError as exc:
        return _blocked_report(fixtures, exc.code)
    except LiveAttestationError:
        return _blocked_report(fixtures, "authority_origin_invalid")
    except RecursionError:
        return _blocked_report(fixtures, "authority_bundle_nesting_invalid")


def _verify_and_project(
    raw_bundle: bytes,
    raw_ledger_snapshot: bytes,
    *,
    trust_context: OriginTrustContext,
    authority_binding: ProtectedAuthorityBinding,
    package_binding: ProtectedPackageBinding,
    fixtures: FixtureSet,
    expected_rows: Sequence[ProtectedRowBinding],
    replay_guard: ProtectedEvidenceReplayGuard,
    verified_at: datetime | None,
    expected_projection_commit: _VerifiedProjectionCommitReceipt | None = None,
) -> dict[str, Any]:
    if not isinstance(trust_context, OriginTrustContext):
        raise ProtectedEvidenceError("authority_trust_context_invalid")
    if not isinstance(authority_binding, ProtectedAuthorityBinding):
        raise ProtectedEvidenceError("authority_binding_invalid")
    if not isinstance(package_binding, ProtectedPackageBinding):
        raise ProtectedEvidenceError("authority_package_binding_invalid")
    if not isinstance(fixtures, FixtureSet):
        raise ProtectedEvidenceError("authority_fixture_set_invalid")
    if not isinstance(replay_guard, ProtectedEvidenceReplayGuard):
        raise ProtectedEvidenceError("authority_replay_guard_missing")

    expected_by_key = _index_expected_rows(expected_rows)
    fixture_by_key = _index_materialized_fixtures(fixtures)
    now = _utc_now(verified_at or datetime.now(timezone.utc))
    bundle = _parse_canonical_object(
        raw_bundle,
        maximum_size=_MAX_BUNDLE_BYTES,
        invalid_code="authority_bundle_invalid",
        canonical_code="authority_bundle_not_canonical",
        raw_code="authority_raw_bundle_required",
    )
    ledger = _parse_canonical_object(
        raw_ledger_snapshot,
        maximum_size=_MAX_LEDGER_BYTES,
        invalid_code="authority_ledger_snapshot_invalid",
        canonical_code="authority_ledger_snapshot_not_canonical",
        raw_code="authority_raw_ledger_required",
    )
    _require_public_safe(bundle, "authority_bundle_private_value")
    _require_public_safe(ledger, "authority_ledger_private_value")
    _require_exact_fields(bundle, _BUNDLE_FIELDS, "authority_bundle_shape_invalid")
    if bundle.get("schema") != AUTHORITY_BUNDLE_SCHEMA:
        raise ProtectedEvidenceError("authority_bundle_schema_invalid")
    _require_safe_id(bundle.get("bundleId"), "authority_bundle_identity_invalid")
    if bundle.get("proofAlgorithm") != ORIGIN_PROOF_ALGORITHM:
        raise ProtectedEvidenceError("authority_bundle_algorithm_invalid")
    if bundle.get("policyId") != trust_context.policy_id:
        raise ProtectedEvidenceError("authority_bundle_policy_mismatch")
    if bundle.get("signerKeyId") != trust_context.signer_key_id:
        raise ProtectedEvidenceError("authority_bundle_signer_mismatch")
    if trust_context.signer_key_id in trust_context.revoked_signer_key_ids:
        raise ProtectedEvidenceError("authority_bundle_signer_revoked")
    if authority_binding.policy_id != trust_context.policy_id or (
        authority_binding.service_executable_digest
        != trust_context.attestor_executable_digest
    ):
        raise ProtectedEvidenceError("authority_trust_binding_mismatch")

    signed_at = _require_timestamp(
        bundle.get("signedAt"), "authority_bundle_timestamp_invalid"
    )
    if not (
        trust_context.not_before <= signed_at <= trust_context.not_after
        and trust_context.not_before <= now <= trust_context.not_after + _MAX_CLOCK_SKEW
        and signed_at <= now + _MAX_CLOCK_SKEW
        and now - signed_at <= _MAX_EVIDENCE_AGE
    ):
        raise ProtectedEvidenceError("authority_bundle_timestamp_invalid")
    _verify_bundle_signature(bundle, trust_context)

    authority_payload = bundle.get("authorityBinding")
    if not isinstance(authority_payload, Mapping):
        raise ProtectedEvidenceError("authority_binding_invalid")
    _require_exact_fields(
        authority_payload,
        _AUTHORITY_BINDING_FIELDS,
        "authority_binding_invalid",
    )
    if authority_payload != authority_binding.to_payload() or (
        bundle.get("authorityBindingDigest") != _digest_json(authority_payload)
    ):
        raise ProtectedEvidenceError("authority_binding_mismatch")

    package_payload = bundle.get("packageBinding")
    if not isinstance(package_payload, Mapping):
        raise ProtectedEvidenceError("authority_package_binding_invalid")
    _require_exact_fields(
        package_payload,
        _PACKAGE_BINDING_FIELDS,
        "authority_package_binding_invalid",
    )
    if package_payload != package_binding.to_payload() or (
        bundle.get("packageBindingDigest") != _digest_json(package_payload)
    ):
        raise ProtectedEvidenceError("authority_package_binding_mismatch")
    if (
        bundle.get("fixtureSetDescriptorDigest") != fixtures.descriptor_digest
        or bundle.get("fixtureSetDigest") != fixtures.digest
        or not fixtures.digest
    ):
        raise ProtectedEvidenceError("authority_fixture_set_mismatch")

    ledger_digest = hashlib.sha256(raw_ledger_snapshot).hexdigest()
    if bundle.get("ledgerSnapshotDigest") != ledger_digest:
        raise ProtectedEvidenceError("authority_ledger_snapshot_mismatch")
    rows = _index_bundle_rows(bundle.get("rows"), fixtures)
    verified_rows: dict[tuple[str, str], dict[str, Any]] = {}
    for key, row in rows.items():
        expected_row = expected_by_key.get(key)
        if expected_row is None:
            raise ProtectedEvidenceError("authority_expected_row_binding_missing")
        fixture = fixture_by_key[key]
        verified_rows[key] = _verify_raw_row(
            row,
            fixture=fixture,
            expected_row=expected_row,
            fixtures=fixtures,
            package_binding=package_binding,
            trust_context=trust_context,
            verified_at=now,
        )

    ledger_result = _verify_ledger_snapshot(
        ledger,
        rows=rows,
        verified_rows=verified_rows,
        authority_binding=authority_binding,
        bundle_signed_at=signed_at,
    )
    if ledger_result["allDualTicketV2"] is not True:
        raise ProtectedEvidenceError("authority_projection_dual_ticket_v2_required")
    if expected_projection_commit is not None:
        _cross_bind_projection_commit_to_ledger(
            expected_projection_commit,
            ledger_result["binaryTerminals"],
        )
    bundle_digest = hashlib.sha256(raw_bundle).hexdigest()
    replay_guard.consume(
        bundle_digest=bundle_digest,
        ticket_digests=ledger_result["ticketDigests"],
        receipt_digests=ledger_result["receiptDigests"],
        binary_terminal_digests=ledger_result["binaryTerminalDigests"],
    )
    return _project_report(
        fixtures,
        bundle=bundle,
        bundle_digest=bundle_digest,
        ledger_digest=ledger_digest,
        authority_binding=authority_binding,
        package_binding=package_binding,
        verified_rows=verified_rows,
        ledger_receipts=ledger_result["receipts"],
        generated_at=now,
    )


def _index_expected_rows(
    values: Sequence[ProtectedRowBinding],
) -> dict[tuple[str, str], ProtectedRowBinding]:
    if isinstance(values, (str, bytes, bytearray)) or not isinstance(values, Sequence):
        raise ProtectedEvidenceError("authority_expected_row_binding_invalid")
    result: dict[tuple[str, str], ProtectedRowBinding] = {}
    for value in values:
        if not isinstance(value, ProtectedRowBinding):
            raise ProtectedEvidenceError("authority_expected_row_binding_invalid")
        key = (value.scenario_id, value.primitive_id)
        if key in result:
            raise ProtectedEvidenceError("authority_expected_row_binding_invalid")
        result[key] = value
    return result


def _index_materialized_fixtures(
    fixtures: FixtureSet,
) -> dict[tuple[str, str], PrimitiveFixture]:
    if not fixtures.descriptor_digest or not fixtures.digest:
        raise ProtectedEvidenceError("authority_fixture_set_unmaterialized")
    result: dict[tuple[str, str], PrimitiveFixture] = {}
    if tuple(item.scenario_id for item in fixtures.fixtures) != tuple(
        SCENARIO_DEFINITIONS
    ):
        raise ProtectedEvidenceError("authority_fixture_set_invalid")
    for fixture in fixtures.fixtures:
        if (
            not fixture.materialized
            or not fixture.digest
            or not fixture.descriptor_digest
            or fixture.required_primitives != SCENARIO_DEFINITIONS[fixture.scenario_id]
        ):
            raise ProtectedEvidenceError("authority_fixture_set_unmaterialized")
        for primitive_id in fixture.required_primitives:
            result[(fixture.scenario_id, primitive_id)] = fixture
    return result


def _index_bundle_rows(
    value: Any,
    fixtures: FixtureSet,
) -> dict[tuple[str, str], Mapping[str, Any]]:
    if not isinstance(value, list) or not value:
        raise ProtectedEvidenceError("authority_row_set_invalid")
    expected_order = [
        (fixture.scenario_id, primitive_id)
        for fixture in fixtures.fixtures
        for primitive_id in fixture.required_primitives
    ]
    ranks = {key: index for index, key in enumerate(expected_order)}
    result: dict[tuple[str, str], Mapping[str, Any]] = {}
    previous_rank = -1
    for row in value:
        if not isinstance(row, Mapping):
            raise ProtectedEvidenceError("authority_row_set_invalid")
        _require_exact_fields(row, _ROW_FIELDS, "authority_row_shape_invalid")
        if row.get("schema") != AUTHORITY_ROW_SCHEMA:
            raise ProtectedEvidenceError("authority_row_schema_invalid")
        scenario_id = row.get("scenarioId")
        primitive_id = row.get("primitiveId")
        key = (scenario_id, primitive_id)
        rank = ranks.get(key)
        if rank is None or key in result or rank <= previous_rank:
            raise ProtectedEvidenceError("authority_row_set_invalid")
        previous_rank = rank
        result[key] = row
    return result


def _verify_raw_row(
    row: Mapping[str, Any],
    *,
    fixture: PrimitiveFixture,
    expected_row: ProtectedRowBinding,
    fixtures: FixtureSet,
    package_binding: ProtectedPackageBinding,
    trust_context: OriginTrustContext,
    verified_at: datetime,
) -> dict[str, Any]:
    key = (expected_row.scenario_id, expected_row.primitive_id)
    if key != (MODEL_SCENARIO_ID, MODEL_PRIMITIVE_ID):
        raise ProtectedEvidenceError("authority_row_verifier_unavailable")
    expected_values = {
        "scenarioId": expected_row.scenario_id,
        "primitiveId": expected_row.primitive_id,
        "fixtureDescriptorDigest": fixture.descriptor_digest,
        "fixtureDigest": fixture.digest,
        "fixtureProjectInputDigest": expected_row.fixture_project_input_digest,
        "projectBindingDigest": expected_row.project_binding_digest,
    }
    if any(row.get(name) != value for name, value in expected_values.items()):
        raise ProtectedEvidenceError("authority_fixture_binding_mismatch")
    finalization = row.get("finalization")
    envelope = row.get("originEnvelope")
    if not isinstance(finalization, Mapping) or not isinstance(envelope, Mapping):
        raise ProtectedEvidenceError("authority_row_raw_evidence_invalid")
    finalization_digest = _digest_json(finalization)
    envelope_digest = _digest_json(envelope)
    if row.get("finalizationDigest") != finalization_digest or (
        row.get("originEnvelopeDigest") != envelope_digest
    ):
        raise ProtectedEvidenceError("authority_row_digest_mismatch")
    expected_origin = package_binding.origin_binding(
        fixture_set_descriptor_digest=fixtures.descriptor_digest,
        fixture_descriptor_digest=fixture.descriptor_digest,
        fixture_project_input_digest=expected_row.fixture_project_input_digest,
        fixture_digest=fixture.digest,
    )
    verified = verify_trusted_live_origin(
        finalization,
        envelope,
        trust_context=trust_context,
        expected=expected_origin,
        project_binding_digest=expected_row.project_binding_digest,
        verified_at=verified_at,
        replay_guard=None,
    )
    if (
        not verified.origin_verified
        or verified.scenario_id != expected_row.scenario_id
        or verified.primitive_id != expected_row.primitive_id
        or verified.fixture_digest != fixture.digest
        or verified.fixture_set_descriptor_digest != fixtures.descriptor_digest
        or verified.fixture_descriptor_digest != fixture.descriptor_digest
        or verified.project_binding_digest != expected_row.project_binding_digest
        or verified.runtime_binding_digest != package_binding.runtime_binding_digest
    ):
        raise ProtectedEvidenceError("authority_origin_binding_mismatch")
    return {
        "runId": verified.run_id,
        "originTicketDigest": verified.origin_ticket_digest,
        "authorityTicketDigest": getattr(
            verified, "authority_ticket_digest", ""
        ),
        "originEnvelopeSchema": envelope.get("schema"),
        "attestationDigest": verified.attestation_digest,
        "innerAttestationDigest": verified.inner_attestation_digest,
        "originEnvelopeDigest": envelope_digest,
        "finalizationDigest": finalization_digest,
        "cleanupDigest": verified.origin_cleanup_digest,
        "startedAt": verified.started_at,
        "finishedAt": verified.finished_at,
        "finalizedAt": verified.finalized_at,
        "signerKeyId": verified.origin_signer_key_id,
    }


def _verify_ledger_snapshot(
    ledger: Mapping[str, Any],
    *,
    rows: Mapping[tuple[str, str], Mapping[str, Any]],
    verified_rows: Mapping[tuple[str, str], Mapping[str, Any]],
    authority_binding: ProtectedAuthorityBinding,
    bundle_signed_at: datetime,
) -> dict[str, Any]:
    _require_exact_fields(
        ledger,
        _LEDGER_SNAPSHOT_FIELDS,
        "authority_ledger_snapshot_shape_invalid",
    )
    if ledger.get("schema") != LEDGER_SNAPSHOT_SCHEMA:
        raise ProtectedEvidenceError("authority_ledger_snapshot_schema_invalid")
    if (
        ledger.get("authorityGenerationDigest")
        != authority_binding.authority_generation_digest
        or ledger.get("ledgerIdentityDigest") != authority_binding.ledger_identity_digest
    ):
        raise ProtectedEvidenceError("authority_ledger_identity_mismatch")
    first_ordinal = _require_nonnegative_int(
        ledger.get("firstReceiptOrdinal"), "authority_ledger_ordinal_invalid"
    )
    last_ordinal = _require_nonnegative_int(
        ledger.get("lastReceiptOrdinal"), "authority_ledger_ordinal_invalid"
    )
    if first_ordinal == 0 or last_ordinal < first_ordinal:
        raise ProtectedEvidenceError("authority_ledger_ordinal_invalid")
    initial_receipt = _require_digest(
        ledger.get("initialReceiptDigest"), "authority_ledger_receipt_chain_invalid"
    )
    terminal_receipt = _require_digest(
        ledger.get("terminalReceiptDigest"), "authority_ledger_receipt_chain_invalid"
    )
    receipts = ledger.get("receipts")
    if not isinstance(receipts, list) or len(receipts) != len(rows):
        raise ProtectedEvidenceError("authority_ledger_receipt_set_mismatch")

    row_items = list(rows.items())
    previous_receipt = initial_receipt
    previous_ordinal = first_ordinal - 1
    checked: dict[tuple[str, str], dict[str, Any]] = {}
    ticket_digests: list[str] = []
    receipt_digests: list[str] = []
    binary_terminal_digests: list[str] = []
    binary_terminals: list[_VerifiedBinaryLedgerTerminal] = []
    all_dual_ticket_v2 = True
    for (key, row), receipt in zip(row_items, receipts, strict=True):
        if not isinstance(receipt, Mapping):
            raise ProtectedEvidenceError("authority_ledger_receipt_invalid")
        receipt_schema = receipt.get("schema")
        if receipt_schema == LEDGER_RECEIPT_SCHEMA_V2:
            receipt_fields = _LEDGER_RECEIPT_FIELDS_V2
            dual_ticket_v2 = True
        elif receipt_schema == LEDGER_RECEIPT_SCHEMA_V1:
            receipt_fields = _LEDGER_RECEIPT_FIELDS_V1
            dual_ticket_v2 = False
            all_dual_ticket_v2 = False
        else:
            raise ProtectedEvidenceError("authority_ledger_receipt_invalid")
        _require_exact_fields(
            receipt,
            receipt_fields,
            "authority_ledger_receipt_invalid",
        )
        ordinal = _require_nonnegative_int(
            receipt.get("ordinal"), "authority_ledger_ordinal_invalid"
        )
        if ordinal != previous_ordinal + 1 or (
            receipt.get("previousReceiptDigest") != previous_receipt
        ):
            raise ProtectedEvidenceError("authority_ledger_receipt_chain_invalid")
        unsigned = dict(receipt)
        receipt_digest = unsigned.pop("receiptDigest", None)
        if receipt_digest != _digest_json(unsigned):
            raise ProtectedEvidenceError("authority_ledger_receipt_digest_invalid")
        if (
            receipt.get("scenarioId") != key[0]
            or receipt.get("primitiveId") != key[1]
            or receipt.get("state") != "completed"
        ):
            raise ProtectedEvidenceError("authority_ledger_row_mismatch")
        verified = verified_rows[key]
        if dual_ticket_v2:
            ticket_binding_valid = (
                verified["originEnvelopeSchema"] == ORIGIN_ENVELOPE_SCHEMA_V2
                and receipt.get("ticketDigest")
                == verified["authorityTicketDigest"]
                and receipt.get("originTicketDigest")
                == verified["originTicketDigest"]
            )
        else:
            ticket_binding_valid = (
                verified["originEnvelopeSchema"] == ORIGIN_ENVELOPE_SCHEMA_V1
                and receipt.get("ticketDigest") == verified["originTicketDigest"]
            )
        if (
            not ticket_binding_valid
            or receipt.get("runId") != verified["runId"]
            or receipt.get("resultDigest") != row.get("finalizationDigest")
            or receipt.get("resultDigest") != verified["finalizationDigest"]
            or receipt.get("originEnvelopeDigest") != row.get("originEnvelopeDigest")
            or receipt.get("originEnvelopeDigest") != verified["originEnvelopeDigest"]
            or receipt.get("cleanupDigest") != verified["cleanupDigest"]
        ):
            raise ProtectedEvidenceError("authority_ledger_binding_mismatch")

        binary_terminal = _verify_binary_ledger_terminal(
            receipt,
            row=row,
            verified=verified,
            authority_binding=authority_binding,
        )

        envelope = row["originEnvelope"]
        ticket = envelope.get("ticket") if isinstance(envelope, Mapping) else None
        if not isinstance(ticket, Mapping):
            raise ProtectedEvidenceError("authority_ledger_binding_mismatch")
        issued_at = _require_timestamp(
            receipt.get("issuedAt"), "authority_ledger_timestamp_invalid"
        )
        consumed_at = _require_timestamp(
            receipt.get("consumedAt"), "authority_ledger_timestamp_invalid"
        )
        completed_at = _require_timestamp(
            receipt.get("completedAt"), "authority_ledger_timestamp_invalid"
        )
        inner_started_at = _require_timestamp(
            verified["startedAt"], "authority_ledger_timestamp_invalid"
        )
        origin_signed_at = _require_timestamp(
            envelope.get("signedAt"), "authority_ledger_timestamp_invalid"
        )
        ticket_issued_at = _require_timestamp(
            ticket.get("issuedAt"), "authority_ledger_timestamp_invalid"
        )
        if not (
            issued_at == ticket_issued_at
            and issued_at <= consumed_at <= inner_started_at
            and origin_signed_at <= completed_at <= bundle_signed_at
        ):
            raise ProtectedEvidenceError("authority_ledger_timestamp_invalid")
        checked[key] = json.loads(json.dumps(receipt, ensure_ascii=True))
        ticket_digests.append(str(receipt["ticketDigest"]))
        if dual_ticket_v2:
            ticket_digests.append(str(receipt["originTicketDigest"]))
        receipt_digests.append(str(receipt_digest))
        binary_terminal_digests.append(binary_terminal.terminal_digest)
        binary_terminals.append(binary_terminal)
        previous_ordinal = ordinal
        previous_receipt = str(receipt_digest)

    if last_ordinal != previous_ordinal or terminal_receipt != previous_receipt:
        raise ProtectedEvidenceError("authority_ledger_receipt_chain_invalid")
    return {
        "receipts": checked,
        "ticketDigests": tuple(ticket_digests),
        "receiptDigests": tuple(receipt_digests),
        "binaryTerminalDigests": tuple(binary_terminal_digests),
        "binaryTerminals": tuple(binary_terminals),
        "allDualTicketV2": all_dual_ticket_v2,
    }


def _verify_binary_ledger_terminal(
    receipt: Mapping[str, Any],
    *,
    row: Mapping[str, Any],
    verified: Mapping[str, Any],
    authority_binding: ProtectedAuthorityBinding,
) -> _VerifiedBinaryLedgerTerminal:
    terminal = receipt.get("binaryLedgerTerminal")
    if not isinstance(terminal, Mapping):
        raise ProtectedEvidenceError("authority_binary_terminal_invalid")
    _require_exact_fields(
        terminal,
        _BINARY_LEDGER_TERMINAL_FIELDS,
        "authority_binary_terminal_invalid",
    )
    if (
        terminal.get("schema") != BINARY_LEDGER_TERMINAL_SCHEMA
        or terminal.get("event") != "resultCommit"
        or terminal.get("authorityGenerationDigest")
        != authority_binding.authority_generation_digest
        or terminal.get("ledgerIdentityDigest")
        != authority_binding.ledger_identity_digest
    ):
        raise ProtectedEvidenceError("authority_binary_terminal_invalid")
    predecessor_sequence = _require_nonnegative_int(
        terminal.get("predecessorSequence"),
        "authority_binary_terminal_sequence_invalid",
    )
    terminal_sequence = _require_nonnegative_int(
        terminal.get("terminalSequence"),
        "authority_binary_terminal_sequence_invalid",
    )
    anchor_sequence = _require_nonnegative_int(
        terminal.get("anchorSequence"),
        "authority_binary_terminal_sequence_invalid",
    )
    if predecessor_sequence >= terminal_sequence or anchor_sequence != terminal_sequence:
        raise ProtectedEvidenceError("authority_binary_terminal_sequence_invalid")
    digest_fields = (
        "predecessorFrameDigest",
        "terminalFrameDigest",
        "terminalTicketDigest",
        "terminalResultDigest",
        "anchorFrameDigest",
        "anchorTicketDigest",
        "runBindingDigest",
        "preparedReceiptDigest",
        "armedReceiptDigest",
        "policySnapshotDigest",
        "recoveryBundleDigest",
        "runAdmissionDigest",
        "originEnvelopeDigest",
        "cleanupDigest",
        "anchorRecordDigest",
        "reopenReadbackDigest",
    )
    for field_name in digest_fields:
        _require_digest(
            terminal.get(field_name), "authority_binary_terminal_digest_invalid"
        )
    if (
        terminal.get("terminalFrameDigest") != terminal.get("anchorFrameDigest")
        or terminal.get("terminalTicketDigest") != terminal.get("anchorTicketDigest")
        or terminal.get("terminalTicketDigest") != receipt.get("ticketDigest")
        or terminal.get("terminalResultDigest") != receipt.get("resultDigest")
        or terminal.get("terminalResultDigest") != row.get("finalizationDigest")
        or terminal.get("originEnvelopeDigest")
        != receipt.get("originEnvelopeDigest")
        or terminal.get("originEnvelopeDigest") != verified["originEnvelopeDigest"]
        or terminal.get("cleanupDigest") != receipt.get("cleanupDigest")
        or terminal.get("cleanupDigest") != verified["cleanupDigest"]
    ):
        raise ProtectedEvidenceError("authority_binary_terminal_binding_mismatch")
    expected_admission = _run_admission_digest(
        str(terminal["runBindingDigest"]),
        str(terminal["preparedReceiptDigest"]),
        str(terminal["armedReceiptDigest"]),
        str(terminal["policySnapshotDigest"]),
        str(terminal["recoveryBundleDigest"]),
    )
    if terminal.get("runAdmissionDigest") != expected_admission:
        raise ProtectedEvidenceError("authority_binary_admission_binding_mismatch")

    readback = terminal.get("reopenReadback")
    if not isinstance(readback, Mapping):
        raise ProtectedEvidenceError("authority_binary_readback_invalid")
    _require_exact_fields(
        readback,
        _BINARY_LEDGER_READBACK_FIELDS,
        "authority_binary_readback_invalid",
    )
    if (
        readback.get("schema") != BINARY_LEDGER_READBACK_SCHEMA
        or readback.get("readbackKind") != "heldAndReopenedStable"
        or readback.get("authorityGenerationDigest")
        != authority_binding.authority_generation_digest
        or readback.get("ledgerIdentityDigest")
        != authority_binding.ledger_identity_digest
    ):
        raise ProtectedEvidenceError("authority_binary_readback_invalid")
    for field_name in (
        "ledgerFileDigest",
        "anchorFileDigest",
        "ledgerFileIdentityDigest",
        "anchorFileIdentityDigest",
        "latestFrameDigest",
        "anchorRecordDigest",
        "terminalFrameDigest",
        "terminalTicketDigest",
    ):
        _require_digest(
            readback.get(field_name), "authority_binary_readback_invalid"
        )
    ledger_length = _require_nonnegative_int(
        readback.get("ledgerLength"), "authority_binary_readback_invalid"
    )
    anchor_length = _require_nonnegative_int(
        readback.get("anchorLength"), "authority_binary_readback_invalid"
    )
    frame_count = _require_nonnegative_int(
        readback.get("frameCount"), "authority_binary_readback_invalid"
    )
    active_count = _require_nonnegative_int(
        readback.get("activeTicketCount"), "authority_binary_readback_invalid"
    )
    readback_terminal_sequence = _require_nonnegative_int(
        readback.get("terminalSequence"), "authority_binary_readback_invalid"
    )
    if (
        ledger_length == 0
        or anchor_length == 0
        or frame_count != terminal_sequence + 1
        or active_count != 0
        or readback_terminal_sequence != terminal_sequence
        or readback.get("latestFrameDigest") != terminal.get("terminalFrameDigest")
        or readback.get("anchorRecordDigest") != terminal.get("anchorRecordDigest")
        or readback.get("terminalFrameDigest") != terminal.get("terminalFrameDigest")
        or readback.get("terminalTicketDigest") != terminal.get("terminalTicketDigest")
    ):
        raise ProtectedEvidenceError("authority_binary_readback_mismatch")
    readback_digest = _digest_json(readback)
    if terminal.get("reopenReadbackDigest") != readback_digest:
        raise ProtectedEvidenceError("authority_binary_readback_mismatch")
    terminal_digest = _digest_json(terminal)
    if receipt.get("binaryLedgerTerminalDigest") != terminal_digest:
        raise ProtectedEvidenceError("authority_binary_terminal_digest_mismatch")
    return _VerifiedBinaryLedgerTerminal(
        terminal_digest=terminal_digest,
        binding=_ProjectionCommitLedgerBinding(
            authority_generation_digest=authority_binding.authority_generation_digest,
            ledger_identity_digest=authority_binding.ledger_identity_digest,
            ticket_digest=str(receipt["ticketDigest"]),
            run_binding_digest=str(terminal["runBindingDigest"]),
            terminal_sequence=terminal_sequence,
            terminal_frame_digest=str(terminal["terminalFrameDigest"]),
            anchor_record_digest=str(terminal["anchorRecordDigest"]),
            ledger_file_digest=str(readback["ledgerFileDigest"]),
            anchor_file_digest=str(readback["anchorFileDigest"]),
            ledger_file_identity_digest=str(readback["ledgerFileIdentityDigest"]),
            anchor_file_identity_digest=str(readback["anchorFileIdentityDigest"]),
            ledger_length=ledger_length,
            anchor_length=anchor_length,
            frame_count=frame_count,
            active_ticket_count=active_count,
        ),
    )


def _cross_bind_projection_commit_to_ledger(
    receipt: _VerifiedProjectionCommitReceipt,
    terminals: Any,
) -> None:
    code = "authority_projection_commit_ledger_binding_mismatch"
    if not isinstance(receipt, _VerifiedProjectionCommitReceipt) or not isinstance(
        terminals, tuple
    ):
        raise ProtectedEvidenceError(code)
    matches = [
        terminal
        for terminal in terminals
        if isinstance(terminal, _VerifiedBinaryLedgerTerminal)
        and terminal.binding.ticket_digest == receipt.binding.ticket_digest
    ]
    if len(matches) != 1 or matches[0].binding != receipt.binding:
        raise ProtectedEvidenceError(code)


def _project_report(
    fixtures: FixtureSet,
    *,
    bundle: Mapping[str, Any],
    bundle_digest: str,
    ledger_digest: str,
    authority_binding: ProtectedAuthorityBinding,
    package_binding: ProtectedPackageBinding,
    verified_rows: Mapping[tuple[str, str], Mapping[str, Any]],
    ledger_receipts: Mapping[tuple[str, str], Mapping[str, Any]],
    generated_at: datetime,
) -> dict[str, Any]:
    rows: list[dict[str, Any]] = []
    attestations: list[dict[str, Any]] = []
    for fixture in fixtures.fixtures:
        for primitive_id in fixture.required_primitives:
            key = (fixture.scenario_id, primitive_id)
            verified = verified_rows.get(key)
            if verified is None:
                rows.append(
                    {
                        "scenarioId": fixture.scenario_id,
                        "primitiveId": primitive_id,
                        "status": "blocked",
                        "transcriptStatus": "not_run",
                        "fixtureDigest": fixture.digest,
                        "attestationDigest": "",
                        "ledgerReceiptDigest": "",
                        "reasons": ["authority_row_missing"],
                    }
                )
                continue
            ledger_receipt = ledger_receipts[key]
            rows.append(
                {
                    "scenarioId": fixture.scenario_id,
                    "primitiveId": primitive_id,
                    "status": "full",
                    "transcriptStatus": "passed",
                    "fixtureDigest": fixture.digest,
                    "attestationDigest": verified["attestationDigest"],
                    "ledgerReceiptDigest": _digest_json(ledger_receipt),
                    "reasons": [],
                }
            )
            attestation = {
                "scenarioId": fixture.scenario_id,
                "primitiveId": primitive_id,
                "runId": verified["runId"],
                "ticketDigest": (
                    verified["authorityTicketDigest"]
                    or verified["originTicketDigest"]
                ),
                "attestationDigest": verified["attestationDigest"],
                "innerAttestationDigest": verified["innerAttestationDigest"],
                "originEnvelopeDigest": verified["originEnvelopeDigest"],
                "ledgerReceiptDigest": _digest_json(ledger_receipt),
            }
            if verified["authorityTicketDigest"]:
                attestation["originTicketDigest"] = verified["originTicketDigest"]
            attestations.append(attestation)
    scenarios = _scenario_results(fixtures, rows)
    summary = _summary(scenarios, rows)
    report = {
        "schema": MATRIX_SCHEMA,
        "ok": summary["status"] == "full",
        "generatedAt": _format_utc(generated_at),
        "fixtureSetDescriptorDigest": fixtures.descriptor_digest,
        "fixtureSetDigest": fixtures.digest,
        "runtimeBinding": {
            "filesHashed": True,
            "liveRunnerAttested": bool(verified_rows),
            "protectedAuthorityVerified": True,
            "version": package_binding.version,
            "releaseManifestDigest": package_binding.manifest_digest,
            "executableDigest": package_binding.desktop_executable_digest,
            "backendTreeDigest": package_binding.backend_tree_digest,
            "digest": _digest_json(package_binding.to_payload()),
            "authorityGenerationDigest": authority_binding.authority_generation_digest,
            "ledgerIdentityDigest": authority_binding.ledger_identity_digest,
            "bundleDigest": bundle_digest,
            "ledgerSnapshotDigest": ledger_digest,
            "signerKeyId": bundle["signerKeyId"],
            "attestedRows": [
                f"{scenario_id}/{primitive_id}"
                for scenario_id, primitive_id in verified_rows
            ],
            "reasons": [],
        },
        "runId": bundle["bundleId"],
        "fixtures": _fixture_projection(fixtures),
        "rows": rows,
        "scenarios": scenarios,
        "summary": summary,
        "attestations": attestations,
    }
    _require_public_safe(report, "authority_report_private_value")
    return report


def _blocked_report(fixtures: Any, reason: str) -> dict[str, Any]:
    safe_reason = (
        reason
        if isinstance(reason, str) and _SAFE_ID_RE.fullmatch(reason) is not None
        else "authority_bundle_invalid"
    )
    rows: list[dict[str, Any]] = []
    fixture_items = fixtures.fixtures if isinstance(fixtures, FixtureSet) else ()
    fixture_by_scenario = {
        item.scenario_id: item for item in fixture_items if isinstance(item, PrimitiveFixture)
    }
    for scenario_id, primitive_ids in SCENARIO_DEFINITIONS.items():
        fixture = fixture_by_scenario.get(scenario_id)
        fixture_digest = fixture.digest if fixture is not None else ""
        for primitive_id in primitive_ids:
            rows.append(
                {
                    "scenarioId": scenario_id,
                    "primitiveId": primitive_id,
                    "status": "blocked",
                    "transcriptStatus": "rejected",
                    "fixtureDigest": fixture_digest,
                    "attestationDigest": "",
                    "ledgerReceiptDigest": "",
                    "reasons": [safe_reason],
                }
            )
    scenarios = []
    for scenario_id, primitive_ids in SCENARIO_DEFINITIONS.items():
        fixture = fixture_by_scenario.get(scenario_id)
        scenarios.append(
            {
                "scenarioId": scenario_id,
                "status": "blocked",
                "fixtureDigest": fixture.digest if fixture is not None else "",
                "requiredPrimitives": list(primitive_ids),
            }
        )
    summary = _summary(scenarios, rows)
    report = {
        "schema": MATRIX_SCHEMA,
        "ok": False,
        "generatedAt": _format_utc(datetime.now(timezone.utc)),
        "fixtureSetDescriptorDigest": (
            fixtures.descriptor_digest if isinstance(fixtures, FixtureSet) else ""
        ),
        "fixtureSetDigest": fixtures.digest if isinstance(fixtures, FixtureSet) else "",
        "runtimeBinding": {
            "filesHashed": False,
            "liveRunnerAttested": False,
            "protectedAuthorityVerified": False,
            "version": "",
            "releaseManifestDigest": "",
            "executableDigest": "",
            "backendTreeDigest": "",
            "digest": "",
            "authorityGenerationDigest": "",
            "ledgerIdentityDigest": "",
            "bundleDigest": "",
            "ledgerSnapshotDigest": "",
            "signerKeyId": "",
            "attestedRows": [],
            "reasons": [safe_reason],
        },
        "runId": "protected-authority-rejected",
        "fixtures": _fixture_projection(fixtures),
        "rows": rows,
        "scenarios": scenarios,
        "summary": summary,
        "attestations": [],
    }
    _require_public_safe(report, "authority_report_private_value")
    return report


def _safe_projection_blocked_report(fixtures: Any, reason: str) -> dict[str, Any]:
    try:
        return _blocked_report(fixtures, reason)
    except Exception:
        return _blocked_report(None, "authority_projection_verification_failed")


def _fixture_projection(fixtures: Any) -> list[dict[str, Any]]:
    if not isinstance(fixtures, FixtureSet):
        return []
    return [
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
    ]


def _scenario_results(
    fixtures: FixtureSet, rows: Sequence[Mapping[str, Any]]
) -> list[dict[str, Any]]:
    result: list[dict[str, Any]] = []
    for fixture in fixtures.fixtures:
        statuses = [
            row["status"]
            for row in rows
            if row["scenarioId"] == fixture.scenario_id
        ]
        if statuses and all(status == "full" for status in statuses):
            status = "full"
        elif statuses and all(status == "blocked" for status in statuses):
            status = "blocked"
        else:
            status = "partial"
        result.append(
            {
                "scenarioId": fixture.scenario_id,
                "status": status,
                "fixtureDigest": fixture.digest,
                "requiredPrimitives": list(fixture.required_primitives),
            }
        )
    return result


def _summary(
    scenarios: Sequence[Mapping[str, Any]], rows: Sequence[Mapping[str, Any]]
) -> dict[str, Any]:
    scenario_counts = {
        status: sum(item["status"] == status for item in scenarios)
        for status in ("full", "partial", "blocked")
    }
    row_counts = {
        status: sum(item["status"] == status for item in rows)
        for status in ("full", "partial", "blocked")
    }
    if scenarios and scenario_counts["full"] == len(scenarios):
        status = "full"
    elif scenarios and scenario_counts["blocked"] == len(scenarios):
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


def _validate_projection_commit_trust(
    *,
    trust_context: OriginTrustContext,
    authority_binding: ProtectedAuthorityBinding,
    verified_at: datetime | None,
) -> ec.EllipticCurvePublicKey:
    code = "authority_projection_commit_identity_mismatch"
    if not isinstance(trust_context, OriginTrustContext):
        raise ProtectedEvidenceError("authority_projection_commit_trust_invalid")
    if not isinstance(authority_binding, ProtectedAuthorityBinding):
        raise ProtectedEvidenceError(code)

    policy_id = _require_safe_id(trust_context.policy_id, code)
    signer_key_id = _require_digest(trust_context.signer_key_id, code)
    attestor_digest = _require_digest(trust_context.attestor_executable_digest, code)
    generation_digest = _require_digest(
        authority_binding.authority_generation_digest, code
    )
    authority_ledger_identity = _require_digest(
        authority_binding.ledger_identity_digest, code
    )
    _require_safe_id(authority_binding.policy_id, code)
    for field_name in (
        "protected_manifest_digest",
        "installed_layout_digest",
        "service_executable_digest",
        "controller_executable_digest",
        "install_helper_executable_digest",
    ):
        _require_digest(getattr(authority_binding, field_name), code)
    if (
        authority_binding.policy_id != policy_id
        or authority_binding.service_executable_digest != attestor_digest
    ):
        raise ProtectedEvidenceError(code)

    revoked = trust_context.revoked_signer_key_ids
    if not isinstance(revoked, frozenset):
        raise ProtectedEvidenceError("authority_projection_commit_trust_invalid")
    for revoked_key_id in revoked:
        _require_digest(
            revoked_key_id, "authority_projection_commit_trust_invalid"
        )
    if signer_key_id in revoked:
        raise ProtectedEvidenceError("authority_projection_commit_signer_revoked")

    public_key_bytes = trust_context.signer_public_key
    if (
        type(public_key_bytes) is not bytes
        or len(public_key_bytes) != 65
        or public_key_bytes[0] != 0x04
        or hashlib.sha256(public_key_bytes).hexdigest() != signer_key_id
    ):
        raise ProtectedEvidenceError(code)
    try:
        public_key = ec.EllipticCurvePublicKey.from_encoded_point(
            ec.SECP256R1(), public_key_bytes
        )
    except ValueError as exc:
        raise ProtectedEvidenceError(code) from exc

    not_before = _projection_trust_time(
        trust_context.not_before, "authority_projection_commit_trust_invalid"
    )
    not_after = _projection_trust_time(
        trust_context.not_after, "authority_projection_commit_trust_invalid"
    )
    now = _projection_trust_time(
        verified_at or datetime.now(timezone.utc),
        "authority_projection_commit_trust_invalid",
    )
    try:
        valid_until = not_after + _MAX_CLOCK_SKEW
    except OverflowError as exc:
        raise ProtectedEvidenceError(
            "authority_projection_commit_trust_invalid"
        ) from exc
    if not_before >= not_after or not (not_before <= now <= valid_until):
        raise ProtectedEvidenceError("authority_projection_commit_trust_invalid")

    expected_ledger_identity = _projection_ledger_identity_digest(
        generation_digest, signer_key_id
    )
    if authority_ledger_identity != expected_ledger_identity:
        raise ProtectedEvidenceError(code)
    return public_key


def _projection_trust_time(value: Any, code: str) -> datetime:
    if not isinstance(value, datetime) or value.tzinfo is None:
        raise ProtectedEvidenceError(code)
    try:
        if value.utcoffset() is None:
            raise ProtectedEvidenceError(code)
        return value.astimezone(timezone.utc)
    except (OverflowError, ValueError) as exc:
        raise ProtectedEvidenceError(code) from exc


def _projection_ledger_identity_digest(
    generation_digest: str, signer_key_id: str
) -> str:
    value = hashlib.sha256()
    value.update(_LEDGER_IDENTITY_DIGEST_DOMAIN)
    value.update(bytes.fromhex(generation_digest))
    value.update(bytes.fromhex(signer_key_id))
    return value.hexdigest()


def _projection_runtime_ticket_digest(
    *,
    authority_binding: ProtectedAuthorityBinding,
    signer_key_id: str,
    request_id: str,
) -> str:
    code = "authority_projection_commit_runtime_ticket_mismatch"
    if not isinstance(authority_binding, ProtectedAuthorityBinding):
        raise ProtectedEvidenceError(code)
    request_bytes = _require_request_id(request_id, code).encode("utf-8")
    identity = hashlib.sha256()
    identity.update(_RUNTIME_IDENTITY_DIGEST_DOMAIN)
    for digest_value in (
        authority_binding.authority_generation_digest,
        signer_key_id,
        authority_binding.protected_manifest_digest,
        authority_binding.installed_layout_digest,
        authority_binding.service_executable_digest,
    ):
        identity.update(bytes.fromhex(_require_digest(digest_value, code)))
    ticket = hashlib.sha256()
    ticket.update(_RUNTIME_TICKET_DIGEST_DOMAIN)
    ticket.update(identity.digest())
    ticket.update(len(request_bytes).to_bytes(8, "big"))
    ticket.update(request_bytes)
    return ticket.hexdigest()


def _decode_bounded_base64url(
    value: Any, *, maximum_size: int, code: str
) -> bytes:
    maximum_encoded_size = ((maximum_size + 2) // 3) * 4
    if (
        not isinstance(value, str)
        or not value
        or len(value) > maximum_encoded_size
        or _BASE64URL_RE.fullmatch(value) is None
    ):
        raise ProtectedEvidenceError(code)
    padding = "=" * ((4 - len(value) % 4) % 4)
    try:
        decoded = base64.b64decode(value + padding, altchars=b"-_", validate=True)
    except (TypeError, ValueError) as exc:
        raise ProtectedEvidenceError(code) from exc
    if len(decoded) > maximum_size or _base64url(decoded) != value:
        raise ProtectedEvidenceError(code)
    return decoded


def _verify_projection_wrapper(raw_projection: bytes) -> _VerifiedProjectionWrapper:
    projection = _parse_canonical_object(
        raw_projection,
        maximum_size=_MAX_VERIFIED_AUTHORITY_RESULT_PROJECTION_BYTES,
        invalid_code="authority_projection_shape_invalid",
        canonical_code="authority_projection_shape_invalid",
        raw_code="authority_projection_shape_invalid",
    )
    _require_public_safe(projection, "authority_projection_private_value")
    _require_exact_fields(
        projection,
        _PROTECTED_PROJECTION_FIELDS,
        "authority_projection_shape_invalid",
    )
    if projection.get("schema") != PROTECTED_PROJECTION_SCHEMA:
        raise ProtectedEvidenceError("authority_projection_shape_invalid")
    authority_bundle = projection.get("authorityBundle")
    ledger_snapshot = projection.get("ledgerSnapshot")
    if not isinstance(authority_bundle, Mapping) or not isinstance(
        ledger_snapshot, Mapping
    ):
        raise ProtectedEvidenceError("authority_projection_shape_invalid")
    if (
        authority_bundle.get("schema") != AUTHORITY_BUNDLE_SCHEMA
        or ledger_snapshot.get("schema") != LEDGER_SNAPSHOT_SCHEMA
    ):
        raise ProtectedEvidenceError("authority_projection_shape_invalid")
    authority_bundle_digest = _require_digest(
        projection.get("authorityBundleDigest"),
        "authority_projection_digest_mismatch",
    )
    ledger_snapshot_digest = _require_digest(
        projection.get("ledgerSnapshotDigest"),
        "authority_projection_digest_mismatch",
    )
    authority_bundle_bytes = _canonical_bytes(authority_bundle)
    ledger_snapshot_bytes = _canonical_bytes(ledger_snapshot)
    if (
        hashlib.sha256(authority_bundle_bytes).hexdigest()
        != authority_bundle_digest
        or hashlib.sha256(ledger_snapshot_bytes).hexdigest()
        != ledger_snapshot_digest
    ):
        raise ProtectedEvidenceError("authority_projection_digest_mismatch")
    return _VerifiedProjectionWrapper(
        authority_bundle_bytes=authority_bundle_bytes,
        ledger_snapshot_bytes=ledger_snapshot_bytes,
        authority_bundle_digest=authority_bundle_digest,
        ledger_snapshot_digest=ledger_snapshot_digest,
    )


def _verify_projection_commit_receipt(
    receipt: Any,
    *,
    public_key: ec.EllipticCurvePublicKey,
    trust_context: OriginTrustContext,
    authority_binding: ProtectedAuthorityBinding,
    expected_ticket_digest: str,
    expected_run_binding_digest: str,
    projection_digest: str,
    projection_length: int,
) -> _VerifiedProjectionCommitReceipt:
    if not isinstance(receipt, Mapping):
        raise ProtectedEvidenceError(
            "authority_projection_commit_receipt_shape_invalid"
        )
    _require_exact_fields(
        receipt,
        _PROJECTION_COMMIT_RECEIPT_FIELDS,
        "authority_projection_commit_receipt_shape_invalid",
    )
    receipt_bytes = _canonical_bytes(receipt)
    if not receipt_bytes or len(receipt_bytes) > _MAX_PROJECTION_COMMIT_RECEIPT_BYTES:
        raise ProtectedEvidenceError(
            "authority_projection_commit_receipt_shape_invalid"
        )
    if (
        receipt.get("schema") != PROJECTION_COMMIT_RECEIPT_SCHEMA
        or receipt.get("event") != "projectionCommit"
        or receipt.get("proofAlgorithm") != PROJECTION_COMMIT_PROOF_ALGORITHM
    ):
        raise ProtectedEvidenceError(
            "authority_projection_commit_receipt_policy_invalid"
        )

    identity_code = "authority_projection_commit_identity_mismatch"
    signer_key_id = _require_digest(receipt.get("signerKeyId"), identity_code)
    generation_digest = _require_digest(
        receipt.get("authorityGenerationDigest"), identity_code
    )
    ledger_identity_digest = _require_digest(
        receipt.get("ledgerIdentityDigest"), identity_code
    )
    if (
        signer_key_id != trust_context.signer_key_id
        or generation_digest != authority_binding.authority_generation_digest
        or ledger_identity_digest != authority_binding.ledger_identity_digest
        or ledger_identity_digest
        != _projection_ledger_identity_digest(generation_digest, signer_key_id)
    ):
        raise ProtectedEvidenceError(identity_code)

    request_code = "authority_projection_commit_request_binding_mismatch"
    ticket_digest = _require_digest(receipt.get("ticketDigest"), request_code)
    run_binding_digest = _require_digest(
        receipt.get("runBindingDigest"), request_code
    )
    if (
        ticket_digest != expected_ticket_digest
        or run_binding_digest != expected_run_binding_digest
    ):
        raise ProtectedEvidenceError(request_code)

    projection_code = "authority_projection_commit_projection_mismatch"
    committed_projection_digest = _require_digest(
        receipt.get("projectionDigest"), projection_code
    )
    committed_projection_length = _require_nonnegative_int(
        receipt.get("projectionLength"), projection_code
    )
    if (
        committed_projection_length == 0
        or committed_projection_length
        > _MAX_VERIFIED_AUTHORITY_RESULT_PROJECTION_BYTES
    ):
        raise ProtectedEvidenceError(
            "authority_projection_commit_receipt_binding_invalid"
        )
    if (
        committed_projection_digest != projection_digest
        or committed_projection_length != projection_length
    ):
        raise ProtectedEvidenceError(projection_code)

    binding_code = "authority_projection_commit_receipt_binding_invalid"
    terminal_sequence = _require_nonnegative_int(
        receipt.get("terminalSequence"), binding_code
    )
    anchor_sequence = _require_nonnegative_int(
        receipt.get("anchorSequence"), binding_code
    )
    terminal_frame_digest = _require_digest(
        receipt.get("terminalFrameDigest"), binding_code
    )
    terminal_ticket_digest = _require_digest(
        receipt.get("terminalTicketDigest"), binding_code
    )
    anchor_frame_digest = _require_digest(
        receipt.get("anchorFrameDigest"), binding_code
    )
    anchor_ticket_digest = _require_digest(
        receipt.get("anchorTicketDigest"), binding_code
    )
    anchor_record_digest = _require_digest(
        receipt.get("anchorRecordDigest"), binding_code
    )
    if (
        terminal_sequence != anchor_sequence
        or terminal_frame_digest != anchor_frame_digest
        or terminal_ticket_digest != ticket_digest
        or anchor_ticket_digest != ticket_digest
    ):
        raise ProtectedEvidenceError(binding_code)

    readback = _verify_projection_commit_readback(
        receipt.get("reopenReadback"),
        terminal_sequence=terminal_sequence,
        terminal_frame_digest=terminal_frame_digest,
    )
    receipt_digest = _require_digest(
        receipt.get("receiptDigest"),
        "authority_projection_commit_receipt_digest_mismatch",
    )
    unsigned = dict(receipt)
    unsigned.pop("receiptDigest")
    unsigned.pop("signatureP256")
    receipt_body = _canonical_bytes(unsigned)
    receipt_body_digest = hashlib.sha256(receipt_body).hexdigest()
    digest_builder = hashlib.sha256()
    digest_builder.update(_PROJECTION_COMMIT_RECEIPT_DIGEST_DOMAIN)
    digest_builder.update(len(receipt_body).to_bytes(8, "big"))
    digest_builder.update(receipt_body)
    computed_receipt_digest = digest_builder.hexdigest()
    if computed_receipt_digest != receipt_digest:
        raise ProtectedEvidenceError(
            "authority_projection_commit_receipt_digest_mismatch"
        )
    _verify_projection_commit_signature(
        receipt.get("signatureP256"),
        bytes.fromhex(receipt_digest),
        public_key,
    )
    return _VerifiedProjectionCommitReceipt(
        receipt_digest=receipt_digest,
        receipt_body_digest=receipt_body_digest,
        readback_digest=readback.readback_digest,
        binding=_ProjectionCommitLedgerBinding(
            authority_generation_digest=generation_digest,
            ledger_identity_digest=ledger_identity_digest,
            ticket_digest=ticket_digest,
            run_binding_digest=run_binding_digest,
            terminal_sequence=terminal_sequence,
            terminal_frame_digest=terminal_frame_digest,
            anchor_record_digest=anchor_record_digest,
            ledger_file_digest=readback.ledger_file_digest,
            anchor_file_digest=readback.anchor_file_digest,
            ledger_file_identity_digest=readback.ledger_file_identity_digest,
            anchor_file_identity_digest=readback.anchor_file_identity_digest,
            ledger_length=readback.ledger_length,
            anchor_length=readback.anchor_length,
            frame_count=readback.frame_count,
            active_ticket_count=readback.active_ticket_count,
        ),
    )


def _verify_projection_commit_readback(
    readback: Any,
    *,
    terminal_sequence: int,
    terminal_frame_digest: str,
) -> _VerifiedProjectionCommitReadback:
    code = "authority_projection_commit_readback_invalid"
    if not isinstance(readback, Mapping):
        raise ProtectedEvidenceError(code)
    _require_exact_fields(readback, _PROJECTION_COMMIT_READBACK_FIELDS, code)
    if (
        readback.get("schema") != PROJECTION_COMMIT_READBACK_SCHEMA
        or readback.get("readbackKind") != "heldAndReopenedStable"
    ):
        raise ProtectedEvidenceError(code)
    for field_name in (
        "ledgerFileDigest",
        "anchorFileDigest",
        "ledgerFileIdentityDigest",
        "anchorFileIdentityDigest",
        "latestFrameDigest",
    ):
        _require_digest(readback.get(field_name), code)
    ledger_length = _require_nonnegative_int(readback.get("ledgerLength"), code)
    anchor_length = _require_nonnegative_int(readback.get("anchorLength"), code)
    frame_count = _require_nonnegative_int(readback.get("frameCount"), code)
    active_ticket_count = _require_nonnegative_int(
        readback.get("activeTicketCount"), code
    )
    if (
        ledger_length == 0
        or anchor_length == 0
        or frame_count != terminal_sequence + 1
        or active_ticket_count != 0
        or readback.get("latestFrameDigest") != terminal_frame_digest
    ):
        raise ProtectedEvidenceError(code)
    return _VerifiedProjectionCommitReadback(
        readback_digest=_digest_json(readback),
        ledger_file_digest=str(readback["ledgerFileDigest"]),
        anchor_file_digest=str(readback["anchorFileDigest"]),
        ledger_file_identity_digest=str(readback["ledgerFileIdentityDigest"]),
        anchor_file_identity_digest=str(readback["anchorFileIdentityDigest"]),
        ledger_length=ledger_length,
        anchor_length=anchor_length,
        frame_count=frame_count,
        active_ticket_count=active_ticket_count,
    )


def _verify_projection_commit_signature(
    value: Any,
    receipt_digest: bytes,
    public_key: ec.EllipticCurvePublicKey,
) -> None:
    code = "authority_projection_commit_signature_invalid"
    if not isinstance(value, str) or _RAW_P256_SIGNATURE_RE.fullmatch(value) is None:
        raise ProtectedEvidenceError(code)
    signature = bytes.fromhex(value)
    raw_r = int.from_bytes(signature[:32], "big")
    raw_s = int.from_bytes(signature[32:], "big")
    if (
        raw_r <= 0
        or raw_r >= _P256_ORDER
        or raw_s <= 0
        or raw_s > _P256_ORDER // 2
    ):
        raise ProtectedEvidenceError(code)
    try:
        public_key.verify(
            utils.encode_dss_signature(raw_r, raw_s),
            receipt_digest,
            ec.ECDSA(utils.Prehashed(hashes.SHA256())),
        )
    except (InvalidSignature, ValueError) as exc:
        raise ProtectedEvidenceError(code) from exc


def _verify_bundle_signature(
    bundle: Mapping[str, Any], trust_context: OriginTrustContext
) -> None:
    signature = _decode_base64url(
        bundle.get("signature"),
        expected_size=64,
        code="authority_bundle_signature_invalid",
    )
    raw_r = int.from_bytes(signature[:32], "big")
    raw_s = int.from_bytes(signature[32:], "big")
    if (
        raw_r <= 0
        or raw_r >= _P256_ORDER
        or raw_s <= 0
        or raw_s > _P256_ORDER // 2
    ):
        raise ProtectedEvidenceError("authority_bundle_signature_invalid")
    unsigned = dict(bundle)
    unsigned.pop("signature", None)
    try:
        public_key = ec.EllipticCurvePublicKey.from_encoded_point(
            ec.SECP256R1(), trust_context.signer_public_key
        )
        public_key.verify(
            utils.encode_dss_signature(raw_r, raw_s),
            _canonical_bytes(unsigned),
            ec.ECDSA(hashes.SHA256()),
        )
    except (InvalidSignature, ValueError) as exc:
        raise ProtectedEvidenceError("authority_bundle_signature_invalid") from exc


def _parse_canonical_object(
    raw: bytes,
    *,
    maximum_size: int,
    invalid_code: str,
    canonical_code: str,
    raw_code: str,
) -> dict[str, Any]:
    if type(raw) is not bytes:
        raise ProtectedEvidenceError(raw_code)
    if not raw or len(raw) > maximum_size:
        raise ProtectedEvidenceError(invalid_code)
    try:
        value = json.loads(
            raw.decode("utf-8"),
            object_pairs_hook=_unique_object,
            parse_constant=_reject_json_constant,
        )
    except ProtectedEvidenceError:
        raise
    except (UnicodeError, RecursionError, ValueError) as exc:
        raise ProtectedEvidenceError(invalid_code) from exc
    if not isinstance(value, dict):
        raise ProtectedEvidenceError(invalid_code)
    try:
        canonical = _canonical_bytes(value)
    except (RecursionError, TypeError, ValueError) as exc:
        raise ProtectedEvidenceError(invalid_code) from exc
    if canonical != raw:
        raise ProtectedEvidenceError(canonical_code)
    return value


def _unique_object(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for key, value in pairs:
        if key in result:
            raise ProtectedEvidenceError("authority_duplicate_json_field")
        result[key] = value
    return result


def _reject_json_constant(value: str) -> None:
    raise ValueError(f"non-finite JSON number rejected: {value}")


def _require_exact_fields(
    value: Mapping[str, Any], expected: set[str], code: str
) -> None:
    if set(value) != expected:
        raise ProtectedEvidenceError(code)


def _require_public_safe(value: Any, code: str) -> None:
    if redact_public_evidence(value) != value:
        raise ProtectedEvidenceError(code)


def _require_digest(value: Any, code: str) -> str:
    if (
        not isinstance(value, str)
        or _DIGEST_RE.fullmatch(value) is None
        or not any(character != "0" for character in value)
    ):
        raise ProtectedEvidenceError(code)
    return value


def _require_safe_id(value: Any, code: str) -> str:
    if not isinstance(value, str) or _SAFE_ID_RE.fullmatch(value) is None:
        raise ProtectedEvidenceError(code)
    return value


def _require_request_id(value: Any, code: str) -> str:
    if not isinstance(value, str) or _REQUEST_ID_RE.fullmatch(value) is None:
        raise ProtectedEvidenceError(code)
    return value


def _require_nonnegative_int(value: Any, code: str) -> int:
    if type(value) is not int or value < 0 or value > _MAX_U64:
        raise ProtectedEvidenceError(code)
    return value


def _require_timestamp(value: Any, code: str) -> datetime:
    if not isinstance(value, str) or not value.endswith("Z"):
        raise ProtectedEvidenceError(code)
    try:
        parsed = datetime.fromisoformat(value[:-1] + "+00:00")
    except ValueError as exc:
        raise ProtectedEvidenceError(code) from exc
    normalized = parsed.astimezone(timezone.utc)
    if _format_utc(normalized) != value:
        raise ProtectedEvidenceError(code)
    return normalized


def _utc_now(value: datetime) -> datetime:
    if value.tzinfo is None:
        raise ProtectedEvidenceError("authority_bundle_timestamp_invalid")
    return value.astimezone(timezone.utc)


def _format_utc(value: datetime) -> str:
    return value.astimezone(timezone.utc).isoformat(timespec="microseconds").replace(
        "+00:00", "Z"
    )


def _decode_base64url(value: Any, *, expected_size: int, code: str) -> bytes:
    if not isinstance(value, str) or not value or _BASE64URL_RE.fullmatch(value) is None:
        raise ProtectedEvidenceError(code)
    padding = "=" * ((4 - len(value) % 4) % 4)
    try:
        decoded = base64.b64decode(value + padding, altchars=b"-_", validate=True)
    except (TypeError, ValueError) as exc:
        raise ProtectedEvidenceError(code) from exc
    if len(decoded) != expected_size or _base64url(decoded) != value:
        raise ProtectedEvidenceError(code)
    return decoded


def _base64url(value: bytes) -> str:
    return base64.urlsafe_b64encode(value).rstrip(b"=").decode("ascii")


def _canonical_bytes(value: Any) -> bytes:
    return json.dumps(
        value,
        allow_nan=False,
        ensure_ascii=True,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")


def _digest_json(value: Any) -> str:
    return hashlib.sha256(_canonical_bytes(value)).hexdigest()


def _run_admission_digest(*digests: str) -> str:
    if len(digests) != 5:
        raise ProtectedEvidenceError("authority_binary_admission_binding_mismatch")
    value = hashlib.sha256()
    value.update(_RUN_ADMISSION_DOMAIN)
    for digest in digests:
        _require_digest(digest, "authority_binary_admission_binding_mismatch")
        value.update(bytes.fromhex(digest))
    return value.hexdigest()


__all__ = [
    "AUTHORITY_SERVICE_RESPONSE_SCHEMA",
    "AUTHORITY_BINDING_SCHEMA",
    "AUTHORITY_BUNDLE_SCHEMA",
    "AUTHORITY_ROW_SCHEMA",
    "BINARY_LEDGER_READBACK_SCHEMA",
    "BINARY_LEDGER_TERMINAL_SCHEMA",
    "LEDGER_RECEIPT_SCHEMA",
    "LEDGER_RECEIPT_SCHEMA_V1",
    "LEDGER_RECEIPT_SCHEMA_V2",
    "LEDGER_SNAPSHOT_SCHEMA",
    "PACKAGE_BINDING_SCHEMA",
    "PROTECTED_PROJECTION_SCHEMA",
    "PROJECTION_COMMIT_PROOF_ALGORITHM",
    "PROJECTION_COMMIT_READBACK_SCHEMA",
    "PROJECTION_COMMIT_RECEIPT_SCHEMA",
    "PROJECTION_RESPONSE_SUMMARY_SCHEMA",
    "ProtectedAuthorityBinding",
    "ProtectedEvidenceError",
    "ProtectedEvidenceReplayGuard",
    "ProtectedPackageBinding",
    "ProtectedRowBinding",
    "VerifiedAuthorityProjectionResponse",
    "verify_and_project_protected_matrix",
    "verify_fixed_authority_projection_matrix",
    "verify_fixed_authority_projection_response",
]
