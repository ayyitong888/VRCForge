from __future__ import annotations

import base64
import copy
import hashlib
import json
from dataclasses import dataclass, replace
from typing import Callable

import pytest
from cryptography.hazmat.primitives import hashes
from cryptography.hazmat.primitives.asymmetric import ec, utils

import primitive_basis_protected_evidence as protected
from tests import test_primitive_basis_origin_attestation as source_contract


RECEIPT_DOMAIN = b"vrcforge-authority-projection-commit-receipt-v2\0"
LEDGER_IDENTITY_DOMAIN = b"vrcforge-authority-ledger-identity-v1\0"
P256_ORDER = int(
    "ffffffff00000000ffffffffffffffffbce6faada7179e84f3b9cac2fc632551", 16
)


def canonical(value: object) -> bytes:
    return json.dumps(
        value,
        allow_nan=False,
        ensure_ascii=True,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")


def digest(value: bytes) -> str:
    return hashlib.sha256(value).hexdigest()


def digest_json(value: object) -> str:
    return digest(canonical(value))


def ledger_identity(generation: str, signer_key_id: str) -> str:
    value = hashlib.sha256()
    value.update(LEDGER_IDENTITY_DOMAIN)
    value.update(bytes.fromhex(generation))
    value.update(bytes.fromhex(signer_key_id))
    return value.hexdigest()


def raw_low_s_signature(
    private_key: ec.EllipticCurvePrivateKey, receipt_digest: bytes
) -> str:
    der = private_key.sign(
        receipt_digest,
        ec.ECDSA(utils.Prehashed(hashes.SHA256())),
    )
    r_value, s_value = utils.decode_dss_signature(der)
    if s_value > P256_ORDER // 2:
        s_value = P256_ORDER - s_value
    return (r_value.to_bytes(32, "big") + s_value.to_bytes(32, "big")).hex()


def sign_receipt(
    value: dict[str, object], private_key: ec.EllipticCurvePrivateKey
) -> dict[str, object]:
    unsigned = copy.deepcopy(value)
    unsigned.pop("receiptDigest", None)
    unsigned.pop("signatureP256", None)
    body = canonical(unsigned)
    receipt_digest = hashlib.sha256(
        RECEIPT_DOMAIN + len(body).to_bytes(8, "big") + body
    ).digest()
    signed = copy.deepcopy(unsigned)
    signed["receiptDigest"] = receipt_digest.hex()
    signed["signatureP256"] = raw_low_s_signature(private_key, receipt_digest)
    return signed


def service_response(
    projection: bytes,
    receipt: dict[str, object],
    *,
    request_id: str,
) -> bytes:
    return canonical(
        {
            "command": "getResult",
            "ok": True,
            "result": {
                "bytesBase64Url": base64.urlsafe_b64encode(projection)
                .rstrip(b"=")
                .decode("ascii"),
                "encoding": "base64url-no-pad",
                "projectionCommitReceipt": receipt,
                "requestId": request_id,
                "sha256": digest(projection),
                "size": len(projection),
                "state": "exact",
            },
            "schema": protected.AUTHORITY_SERVICE_RESPONSE_SCHEMA,
        }
    )


@dataclass
class IntegratedSample:
    source: source_contract.ProtectedSourceContractSample
    projection: bytes
    receipt: dict[str, object]
    response: bytes
    request_id: str
    ticket_digest: str
    run_binding_digest: str


def make_integrated_sample(*, legacy_v1: bool = False) -> IntegratedSample:
    signed = source_contract.sample.__wrapped__()
    request_id = "projection:matrix:1"
    source = source_contract._make_protected_source_contract_sample(
        signed,
        request_id=request_id,
        legacy_v1=legacy_v1,
    )
    generation = source.authority_binding.authority_generation_digest
    signer_key_id = signed.trust_context.signer_key_id
    identity = ledger_identity(generation, signer_key_id)
    authority = replace(source.authority_binding, ledger_identity_digest=identity)

    ledger = copy.deepcopy(source.ledger)
    ledger["ledgerIdentityDigest"] = identity
    receipts = ledger["receipts"]
    assert isinstance(receipts, list) and len(receipts) == 1
    ledger_receipt = receipts[0]
    assert isinstance(ledger_receipt, dict)
    terminal = ledger_receipt["binaryLedgerTerminal"]
    assert isinstance(terminal, dict)
    terminal["ledgerIdentityDigest"] = identity
    readback = terminal["reopenReadback"]
    assert isinstance(readback, dict)
    readback["ledgerIdentityDigest"] = identity
    terminal["reopenReadbackDigest"] = digest_json(readback)
    ledger_receipt["binaryLedgerTerminalDigest"] = digest_json(terminal)
    unsigned_ledger_receipt = copy.deepcopy(ledger_receipt)
    unsigned_ledger_receipt.pop("receiptDigest")
    ledger_receipt["receiptDigest"] = digest_json(unsigned_ledger_receipt)
    ledger["terminalReceiptDigest"] = ledger_receipt["receiptDigest"]
    raw_ledger = canonical(ledger)

    bundle = copy.deepcopy(source.bundle)
    authority_payload = authority.to_payload()
    bundle["authorityBinding"] = authority_payload
    bundle["authorityBindingDigest"] = digest_json(authority_payload)
    bundle["ledgerSnapshotDigest"] = digest(raw_ledger)
    source_contract._sign_envelope(bundle, signed.private_key)
    raw_bundle = canonical(bundle)

    source.authority_binding = authority
    source.bundle = bundle
    source.ledger = ledger
    source.raw_bundle = raw_bundle
    source.raw_ledger = raw_ledger
    projection = canonical(
        {
            "authorityBundle": bundle,
            "authorityBundleDigest": digest(raw_bundle),
            "ledgerSnapshot": ledger,
            "ledgerSnapshotDigest": digest(raw_ledger),
            "schema": protected.PROTECTED_PROJECTION_SCHEMA,
        }
    )
    projection_readback = {
        "activeTicketCount": readback["activeTicketCount"],
        "anchorFileDigest": readback["anchorFileDigest"],
        "anchorFileIdentityDigest": readback["anchorFileIdentityDigest"],
        "anchorLength": readback["anchorLength"],
        "frameCount": readback["frameCount"],
        "latestFrameDigest": readback["latestFrameDigest"],
        "ledgerFileDigest": readback["ledgerFileDigest"],
        "ledgerFileIdentityDigest": readback["ledgerFileIdentityDigest"],
        "ledgerLength": readback["ledgerLength"],
        "readbackKind": "heldAndReopenedStable",
        "schema": protected.PROJECTION_COMMIT_READBACK_SCHEMA,
    }
    receipt_unsigned: dict[str, object] = {
        "anchorFrameDigest": terminal["anchorFrameDigest"],
        "anchorRecordDigest": terminal["anchorRecordDigest"],
        "anchorSequence": terminal["anchorSequence"],
        "anchorTicketDigest": terminal["anchorTicketDigest"],
        "authorityGenerationDigest": generation,
        "event": "projectionCommit",
        "ledgerIdentityDigest": identity,
        "projectionDigest": digest(projection),
        "projectionLength": len(projection),
        "proofAlgorithm": protected.PROJECTION_COMMIT_PROOF_ALGORITHM,
        "reopenReadback": projection_readback,
        "runBindingDigest": terminal["runBindingDigest"],
        "schema": protected.PROJECTION_COMMIT_RECEIPT_SCHEMA,
        "signerKeyId": signer_key_id,
        "terminalFrameDigest": terminal["terminalFrameDigest"],
        "terminalSequence": terminal["terminalSequence"],
        "terminalTicketDigest": terminal["terminalTicketDigest"],
        "ticketDigest": protected._projection_runtime_ticket_digest(
            authority_binding=authority,
            signer_key_id=signer_key_id,
            request_id=request_id,
        ),
    }
    projection_ticket_digest = str(receipt_unsigned["ticketDigest"])
    receipt_unsigned["terminalTicketDigest"] = projection_ticket_digest
    receipt_unsigned["anchorTicketDigest"] = projection_ticket_digest
    receipt = sign_receipt(receipt_unsigned, signed.private_key)
    return IntegratedSample(
        source=source,
        projection=projection,
        receipt=receipt,
        response=service_response(projection, receipt, request_id=request_id),
        request_id=request_id,
        ticket_digest=projection_ticket_digest,
        run_binding_digest=str(terminal["runBindingDigest"]),
    )


@pytest.fixture
def integrated_sample() -> IntegratedSample:
    return make_integrated_sample()


def integrated_report(
    sample: IntegratedSample,
    *,
    response: bytes | None = None,
    package_binding: protected.ProtectedPackageBinding | None = None,
    replay_guard: protected.ProtectedEvidenceReplayGuard | None = None,
    expected_request_id: str | None = None,
    expected_ticket_digest: str | None = None,
    expected_run_binding_digest: str | None = None,
) -> dict[str, object]:
    source = sample.source
    return protected.verify_fixed_authority_projection_matrix(
        sample.response if response is None else response,
        trust_context=source.signed.trust_context,
        authority_binding=source.authority_binding,
        package_binding=(
            source.package_binding if package_binding is None else package_binding
        ),
        fixtures=source.signed.fixtures,
        expected_rows=(source.row_binding,),
        replay_guard=(
            protected.ProtectedEvidenceReplayGuard()
            if replay_guard is None
            else replay_guard
        ),
        expected_request_id=(
            sample.request_id
            if expected_request_id is None
            else expected_request_id
        ),
        expected_ticket_digest=(
            sample.ticket_digest
            if expected_ticket_digest is None
            else expected_ticket_digest
        ),
        expected_run_binding_digest=(
            sample.run_binding_digest
            if expected_run_binding_digest is None
            else expected_run_binding_digest
        ),
        verified_at=source.signed.verified_at,
    )


def assert_blocked(report: dict[str, object], reason: str) -> None:
    source_contract._assert_protected_blocked(report, reason)
    serialized = json.dumps(report, sort_keys=True)
    assert "signatureP256" not in serialized
    assert "bytesBase64Url" not in serialized


def rebuild_response(
    sample: IntegratedSample,
    mutator: Callable[[dict[str, object]], None],
) -> tuple[bytes, dict[str, object]]:
    unsigned = copy.deepcopy(sample.receipt)
    unsigned.pop("receiptDigest")
    unsigned.pop("signatureP256")
    mutator(unsigned)
    receipt = sign_receipt(unsigned, sample.source.signed.private_key)
    return (
        service_response(sample.projection, receipt, request_id=sample.request_id),
        receipt,
    )


def runtime_ticket(
    sample: IntegratedSample, *, request_id: str | None = None
) -> str:
    source = sample.source
    return protected._projection_runtime_ticket_digest(
        authority_binding=source.authority_binding,
        signer_key_id=source.signed.trust_context.signer_key_id,
        request_id=sample.request_id if request_id is None else request_id,
    )


def response_with_ticket(
    sample: IntegratedSample,
    ticket_digest: str,
    *,
    request_id: str | None = None,
) -> bytes:
    def mutate(receipt: dict[str, object]) -> None:
        receipt["ticketDigest"] = ticket_digest
        receipt["terminalTicketDigest"] = ticket_digest
        receipt["anchorTicketDigest"] = ticket_digest

    _, receipt = rebuild_response(sample, mutate)
    return service_response(
        sample.projection,
        receipt,
        request_id=sample.request_id if request_id is None else request_id,
    )


def verified_receipt(
    sample: IntegratedSample,
    response: bytes,
    *,
    expected_ticket_digest: str,
    expected_run_binding_digest: str,
):
    source = sample.source
    return protected._verify_fixed_authority_projection_response(
        response,
        trust_context=source.signed.trust_context,
        authority_binding=source.authority_binding,
        expected_request_id=sample.request_id,
        expected_ticket_digest=expected_ticket_digest,
        expected_run_binding_digest=expected_run_binding_digest,
        verified_at=source.signed.verified_at,
    ).receipt


def verified_binary_terminal(sample: IntegratedSample):
    source = sample.source
    rows = source.bundle["rows"]
    receipts = source.ledger["receipts"]
    assert isinstance(rows, list) and len(rows) == 1
    assert isinstance(receipts, list) and len(receipts) == 1
    row = rows[0]
    ledger_receipt = receipts[0]
    assert isinstance(row, dict) and isinstance(ledger_receipt, dict)
    fixture = next(
        item
        for item in source.signed.fixtures.fixtures
        if item.scenario_id == source.row_binding.scenario_id
    )
    verified_row = protected._verify_raw_row(
        row,
        fixture=fixture,
        expected_row=source.row_binding,
        fixtures=source.signed.fixtures,
        package_binding=source.package_binding,
        trust_context=source.signed.trust_context,
        verified_at=source.signed.verified_at,
    )
    return protected._verify_binary_ledger_terminal(
        ledger_receipt,
        row=row,
        verified=verified_row,
        authority_binding=source.authority_binding,
    )


def test_dual_ticket_v2_fixed_response_projects_the_verified_row(
    integrated_sample: IntegratedSample,
) -> None:
    report = integrated_report(integrated_sample)
    assert report["summary"]["fullRowCount"] == 1
    assert report["runtimeBinding"]["protectedAuthorityVerified"] is True
    [attestation] = report["attestations"]
    assert attestation["ticketDigest"] == integrated_sample.ticket_digest
    assert (
        attestation["originTicketDigest"]
        == integrated_sample.source.origin_ticket_digest
    )
    assert attestation["ticketDigest"] != attestation["originTicketDigest"]


def test_legacy_v1_projection_cannot_enter_the_integrated_full_path() -> None:
    legacy = make_integrated_sample(legacy_v1=True)

    report = integrated_report(legacy)

    assert_blocked(report, "authority_projection_dual_ticket_v2_required")


def test_fixed_response_failure_returns_only_a_safe_blocked_report(
    integrated_sample: IntegratedSample,
) -> None:
    report = integrated_report(integrated_sample, response=b"{}")
    assert_blocked(report, "authority_service_response_shape_invalid")


def test_matrix_failure_after_valid_response_stays_inside_the_same_boundary(
    integrated_sample: IntegratedSample,
) -> None:
    derived_ticket = runtime_ticket(integrated_sample)
    wrong_package = replace(
        integrated_sample.source.package_binding,
        manifest_digest="91" * 32,
    )
    report = integrated_report(
        integrated_sample,
        response=response_with_ticket(integrated_sample, derived_ticket),
        package_binding=wrong_package,
        expected_ticket_digest=derived_ticket,
    )
    assert_blocked(report, "authority_package_binding_mismatch")


def test_runtime_ticket_uses_identity_domain_and_request_length_binding(
    integrated_sample: IntegratedSample,
) -> None:
    source = integrated_sample.source
    authority = source.authority_binding
    signer_key_id = source.signed.trust_context.signer_key_id
    identity = hashlib.sha256()
    identity.update(b"vrcforge-authority-runtime-identity-v1\0")
    for value in (
        authority.authority_generation_digest,
        signer_key_id,
        authority.protected_manifest_digest,
        authority.installed_layout_digest,
        authority.service_executable_digest,
    ):
        identity.update(bytes.fromhex(value))
    request_bytes = integrated_sample.request_id.encode("utf-8")
    expected = hashlib.sha256(
        b"vrcforge-authority-runtime-ticket-v1\0"
        + identity.digest()
        + len(request_bytes).to_bytes(8, "big")
        + request_bytes
    ).hexdigest()
    assert expected == protected._projection_runtime_ticket_digest(
        authority_binding=authority,
        signer_key_id=signer_key_id,
        request_id=integrated_sample.request_id,
    )


def test_replacing_request_id_and_external_ticket_cannot_reframe_the_run(
    integrated_sample: IntegratedSample,
) -> None:
    replacement_request = "projection:matrix:replacement"
    replacement_ticket = "96" * 32

    def mutate(receipt: dict[str, object]) -> None:
        receipt["ticketDigest"] = replacement_ticket
        receipt["terminalTicketDigest"] = replacement_ticket
        receipt["anchorTicketDigest"] = replacement_ticket

    _, receipt = rebuild_response(integrated_sample, mutate)
    response = service_response(
        integrated_sample.projection,
        receipt,
        request_id=replacement_request,
    )
    report = integrated_report(
        integrated_sample,
        response=response,
        expected_request_id=replacement_request,
        expected_ticket_digest=replacement_ticket,
    )
    assert_blocked(report, "authority_projection_commit_runtime_ticket_mismatch")


def test_derived_runtime_ticket_matches_the_verified_v2_ledger_ticket(
    integrated_sample: IntegratedSample,
) -> None:
    source = integrated_sample.source
    derived_ticket = protected._projection_runtime_ticket_digest(
        authority_binding=source.authority_binding,
        signer_key_id=source.signed.trust_context.signer_key_id,
        request_id=integrated_sample.request_id,
    )

    def mutate(receipt: dict[str, object]) -> None:
        receipt["ticketDigest"] = derived_ticket
        receipt["terminalTicketDigest"] = derived_ticket
        receipt["anchorTicketDigest"] = derived_ticket

    response, _ = rebuild_response(integrated_sample, mutate)
    report = integrated_report(
        integrated_sample,
        response=response,
        expected_ticket_digest=derived_ticket,
    )
    assert report["summary"]["fullRowCount"] == 1
    assert report["runtimeBinding"]["protectedAuthorityVerified"] is True


def test_verified_receipt_and_binary_terminal_share_one_exact_internal_binding(
    integrated_sample: IntegratedSample,
) -> None:
    receipt = verified_receipt(
        integrated_sample,
        integrated_sample.response,
        expected_ticket_digest=integrated_sample.ticket_digest,
        expected_run_binding_digest=integrated_sample.run_binding_digest,
    )
    terminal = verified_binary_terminal(integrated_sample)
    protected._cross_bind_projection_commit_to_ledger(receipt, (terminal,))


@pytest.mark.parametrize(
    "field",
    [
        "terminal_sequence",
        "terminal_frame",
        "anchor_record",
        "ledger_file",
        "anchor_file",
        "ledger_identity",
        "anchor_identity",
        "ledger_length",
        "anchor_length",
        "ticket",
        "run",
    ],
)
def test_projection_commit_must_cross_bind_to_the_verified_binary_terminal(
    integrated_sample: IntegratedSample,
    field: str,
) -> None:
    expected_ticket = integrated_sample.ticket_digest
    expected_run = integrated_sample.run_binding_digest

    def mutate(receipt: dict[str, object]) -> None:
        nonlocal expected_ticket, expected_run
        readback = receipt["reopenReadback"]
        assert isinstance(readback, dict)
        if field == "terminal_sequence":
            receipt["terminalSequence"] = int(receipt["terminalSequence"]) + 1
            receipt["anchorSequence"] = receipt["terminalSequence"]
            readback["frameCount"] = int(receipt["terminalSequence"]) + 1
        elif field == "terminal_frame":
            receipt["terminalFrameDigest"] = "97" * 32
            receipt["anchorFrameDigest"] = receipt["terminalFrameDigest"]
            readback["latestFrameDigest"] = receipt["terminalFrameDigest"]
        elif field == "anchor_record":
            receipt["anchorRecordDigest"] = "98" * 32
        elif field == "ledger_file":
            readback["ledgerFileDigest"] = "92" * 32
        elif field == "anchor_file":
            readback["anchorFileDigest"] = "99" * 32
        elif field == "ledger_identity":
            readback["ledgerFileIdentityDigest"] = "a1" * 32
        elif field == "anchor_identity":
            readback["anchorFileIdentityDigest"] = "a2" * 32
        elif field == "ledger_length":
            readback["ledgerLength"] = int(readback["ledgerLength"]) + 1
        elif field == "anchor_length":
            readback["anchorLength"] = int(readback["anchorLength"]) + 1
        elif field == "ticket":
            expected_ticket = "93" * 32
            receipt["ticketDigest"] = expected_ticket
            receipt["terminalTicketDigest"] = expected_ticket
            receipt["anchorTicketDigest"] = expected_ticket
        else:
            expected_run = "94" * 32
            receipt["runBindingDigest"] = expected_run

    response, _ = rebuild_response(integrated_sample, mutate)
    receipt = verified_receipt(
        integrated_sample,
        response,
        expected_ticket_digest=expected_ticket,
        expected_run_binding_digest=expected_run,
    )
    terminal = verified_binary_terminal(integrated_sample)
    with pytest.raises(
        protected.ProtectedEvidenceError,
        match="authority_projection_commit_ledger_binding_mismatch",
    ):
        protected._cross_bind_projection_commit_to_ledger(receipt, (terminal,))


def test_cross_binding_failure_does_not_consume_the_matrix_replay_guard(
    integrated_sample: IntegratedSample,
) -> None:
    derived_ticket = runtime_ticket(integrated_sample)

    def mutate(receipt: dict[str, object]) -> None:
        readback = receipt["reopenReadback"]
        assert isinstance(readback, dict)
        receipt["terminalFrameDigest"] = "a3" * 32
        receipt["anchorFrameDigest"] = receipt["terminalFrameDigest"]
        readback["latestFrameDigest"] = receipt["terminalFrameDigest"]

    response, _ = rebuild_response(integrated_sample, mutate)
    replay_guard = protected.ProtectedEvidenceReplayGuard()
    assert_blocked(
        integrated_report(
            integrated_sample,
            response=response,
            replay_guard=replay_guard,
            expected_ticket_digest=derived_ticket,
        ),
        "authority_projection_commit_ledger_binding_mismatch",
    )
    recovered = source_contract._protected_report(
        integrated_sample.source,
        replay_guard=replay_guard,
    )
    assert recovered["summary"]["fullRowCount"] == 1
    assert recovered["runtimeBinding"]["protectedAuthorityVerified"] is True


def test_unexpected_internal_failure_is_converted_to_a_generic_blocked_report(
    integrated_sample: IntegratedSample,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def fail(*args, **kwargs):
        raise RuntimeError("internal detail must not escape")

    monkeypatch.setattr(protected, "_verify_and_project", fail)
    derived_ticket = runtime_ticket(integrated_sample)
    report = integrated_report(
        integrated_sample,
        response=response_with_ticket(integrated_sample, derived_ticket),
        expected_ticket_digest=derived_ticket,
    )
    assert_blocked(report, "authority_projection_verification_failed")
    assert "internal detail" not in json.dumps(report)
