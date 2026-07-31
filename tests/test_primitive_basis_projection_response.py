from __future__ import annotations

import base64
import copy
import hashlib
import json
from dataclasses import fields, replace
from datetime import datetime, timedelta, timezone

import pytest
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import ec, utils

import primitive_basis_protected_evidence as protected
from primitive_basis_origin_attestation import OriginTrustContext


NOW = datetime(2026, 7, 27, 12, 0, tzinfo=timezone.utc)
GENERATION = "11" * 32
TICKET = "31" * 32
RUN_BINDING = "32" * 32
P256_ORDER = int(
    "ffffffff00000000ffffffffffffffffbce6faada7179e84f3b9cac2fc632551", 16
)
RECEIPT_DOMAIN = b"vrcforge-authority-projection-commit-receipt-v2\0"
LEDGER_IDENTITY_DOMAIN = b"vrcforge-authority-ledger-identity-v1\0"


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


def base64url(value: bytes) -> str:
    return base64.urlsafe_b64encode(value).rstrip(b"=").decode("ascii")


def public_key_bytes(private_key: ec.EllipticCurvePrivateKey) -> bytes:
    return private_key.public_key().public_bytes(
        serialization.Encoding.X962,
        serialization.PublicFormat.UncompressedPoint,
    )


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


def ledger_identity(generation: str, signer_key_id: str) -> str:
    value = hashlib.sha256()
    value.update(LEDGER_IDENTITY_DOMAIN)
    value.update(bytes.fromhex(generation))
    value.update(bytes.fromhex(signer_key_id))
    return value.hexdigest()


def projection_bytes() -> bytes:
    bundle = {"schema": protected.AUTHORITY_BUNDLE_SCHEMA}
    ledger = {"schema": protected.LEDGER_SNAPSHOT_SCHEMA}
    return canonical(
        {
            "authorityBundle": bundle,
            "authorityBundleDigest": digest(canonical(bundle)),
            "ledgerSnapshot": ledger,
            "ledgerSnapshotDigest": digest(canonical(ledger)),
            "schema": protected.PROTECTED_PROJECTION_SCHEMA,
        }
    )


def unsigned_receipt(
    *,
    projection: bytes,
    signer_key_id: str,
    generation: str = GENERATION,
    ticket: str = TICKET,
    run_binding: str = RUN_BINDING,
) -> dict[str, object]:
    identity = ledger_identity(generation, signer_key_id)
    return {
        "anchorFrameDigest": "71" * 32,
        "anchorRecordDigest": "72" * 32,
        "anchorSequence": 7,
        "anchorTicketDigest": ticket,
        "authorityGenerationDigest": generation,
        "event": "projectionCommit",
        "ledgerIdentityDigest": identity,
        "projectionDigest": digest(projection),
        "projectionLength": len(projection),
        "proofAlgorithm": protected.PROJECTION_COMMIT_PROOF_ALGORITHM,
        "reopenReadback": {
            "activeTicketCount": 0,
            "anchorFileDigest": "74" * 32,
            "anchorFileIdentityDigest": "76" * 32,
            "anchorLength": 16 * 576,
            "frameCount": 8,
            "latestFrameDigest": "71" * 32,
            "ledgerFileDigest": "73" * 32,
            "ledgerFileIdentityDigest": "75" * 32,
            "ledgerLength": 8 * 256,
            "readbackKind": "heldAndReopenedStable",
            "schema": protected.PROJECTION_COMMIT_READBACK_SCHEMA,
        },
        "runBindingDigest": run_binding,
        "schema": protected.PROJECTION_COMMIT_RECEIPT_SCHEMA,
        "signerKeyId": signer_key_id,
        "terminalFrameDigest": "71" * 32,
        "terminalSequence": 7,
        "terminalTicketDigest": ticket,
        "ticketDigest": ticket,
    }


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
    *,
    projection: bytes,
    receipt: dict[str, object],
    request_id: str = "request-1",
) -> bytes:
    return canonical(
        {
            "command": "getResult",
            "ok": True,
            "result": {
                "bytesBase64Url": base64url(projection),
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


@pytest.fixture
def sample() -> dict[str, object]:
    private_key = ec.generate_private_key(ec.SECP256R1())
    public_key = public_key_bytes(private_key)
    signer_key_id = digest(public_key)
    trust = OriginTrustContext(
        policy_id="vrcforge.authority.fixed.v1",
        attestor_executable_digest="21" * 32,
        signer_key_id=signer_key_id,
        signer_public_key=public_key,
        revoked_signer_key_ids=frozenset(),
        not_before=NOW - timedelta(hours=1),
        not_after=NOW + timedelta(hours=1),
    )
    authority = protected.ProtectedAuthorityBinding(
        policy_id=trust.policy_id,
        authority_generation_digest=GENERATION,
        protected_manifest_digest="12" * 32,
        installed_layout_digest="13" * 32,
        service_executable_digest=trust.attestor_executable_digest,
        controller_executable_digest="15" * 32,
        install_helper_executable_digest="16" * 32,
        ledger_identity_digest=ledger_identity(GENERATION, signer_key_id),
    )
    projection = projection_bytes()
    receipt = sign_receipt(
        unsigned_receipt(projection=projection, signer_key_id=signer_key_id),
        private_key,
    )
    return {
        "private_key": private_key,
        "trust": trust,
        "authority": authority,
        "projection": projection,
        "receipt": receipt,
        "response": service_response(projection=projection, receipt=receipt),
    }


def verify(sample: dict[str, object], raw: bytes | None = None):
    return protected.verify_fixed_authority_projection_response(
        sample["response"] if raw is None else raw,
        trust_context=sample["trust"],
        authority_binding=sample["authority"],
        expected_request_id="request-1",
        expected_ticket_digest=TICKET,
        expected_run_binding_digest=RUN_BINDING,
        verified_at=NOW,
    )


def rebuild(
    sample: dict[str, object],
    *,
    receipt: dict[str, object] | None = None,
    projection: bytes | None = None,
    request_id: str = "request-1",
) -> bytes:
    return service_response(
        projection=sample["projection"] if projection is None else projection,
        receipt=sample["receipt"] if receipt is None else receipt,
        request_id=request_id,
    )


def resign(sample: dict[str, object], mutator) -> bytes:
    receipt = copy.deepcopy(sample["receipt"])
    receipt.pop("receiptDigest")
    receipt.pop("signatureP256")
    mutator(receipt)
    receipt = sign_receipt(receipt, sample["private_key"])
    return rebuild(sample, receipt=receipt)


def test_valid_response_returns_only_verified_projection_and_binding_summaries(
    sample: dict[str, object],
) -> None:
    verified = verify(sample)
    assert isinstance(verified, protected.VerifiedAuthorityProjectionResponse)
    assert verified.projection_bytes == sample["projection"]
    assert verified.request_id == "request-1"
    assert verified.ticket_digest == TICKET
    assert verified.run_binding_digest == RUN_BINDING
    assert verified.authority_generation_digest == GENERATION
    assert verified.signer_key_id == sample["trust"].signer_key_id
    assert verified.ledger_identity_digest == sample["authority"].ledger_identity_digest
    assert verified.projection_digest == digest(sample["projection"])
    assert verified.projection_length == len(sample["projection"])

    unsigned = copy.deepcopy(sample["receipt"])
    unsigned.pop("receiptDigest")
    unsigned.pop("signatureP256")
    assert verified.receipt_body_digest == digest(canonical(unsigned))
    assert verified.receipt_digest == sample["receipt"]["receiptDigest"]
    assert verified.response_digest == digest(sample["response"])
    assert verified.readback_digest == digest(canonical(sample["receipt"]["reopenReadback"]))

    summary = verified.evidence_summary()
    assert summary["schema"] == protected.PROJECTION_RESPONSE_SUMMARY_SCHEMA
    assert set(summary) == {
        "schema",
        "responseDigest",
        "requestId",
        "projectionDigest",
        "projectionLength",
        "authorityBundleDigest",
        "ledgerSnapshotDigest",
        "receiptDigest",
        "receiptBodyDigest",
        "ticketDigest",
        "runBindingDigest",
        "authorityGenerationDigest",
        "signerKeyId",
        "ledgerIdentityDigest",
        "terminalSequence",
        "terminalFrameDigest",
        "anchorRecordDigest",
        "readbackDigest",
    }
    assert summary["projectionDigest"] == verified.projection_digest
    assert summary["receiptDigest"] == verified.receipt_digest
    assert summary["receiptBodyDigest"] == verified.receipt_body_digest
    assert "signature" not in json.dumps(summary).lower()
    assert "bytesBase64Url" not in summary
    assert all("signature" not in field.name.lower() for field in fields(verified))


@pytest.mark.parametrize(
    ("mutator", "expected"),
    [
        (lambda value: value.update(extra=True), "authority_service_response_shape_invalid"),
        (
            lambda value: value["result"].update(extra=True),
            "authority_service_result_shape_invalid",
        ),
        (
            lambda value: value["result"]["projectionCommitReceipt"].update(extra=True),
            "authority_projection_commit_receipt_shape_invalid",
        ),
        (
            lambda value: value["result"]["projectionCommitReceipt"][
                "reopenReadback"
            ].update(extra=True),
            "authority_projection_commit_readback_invalid",
        ),
    ],
)
def test_extra_fields_fail_closed(sample, mutator, expected) -> None:
    value = json.loads(sample["response"])
    mutator(value)
    with pytest.raises(protected.ProtectedEvidenceError, match=expected):
        verify(sample, canonical(value))


def test_raw_response_must_be_unique_canonical_exact_json(sample) -> None:
    with pytest.raises(
        protected.ProtectedEvidenceError, match="authority_raw_service_response_required"
    ):
        verify(sample, bytearray(sample["response"]))
    with pytest.raises(
        protected.ProtectedEvidenceError, match="authority_service_response_not_canonical"
    ):
        verify(sample, b" " + sample["response"])
    duplicate = b'{"command":"getResult",' + sample["response"][1:]
    with pytest.raises(
        protected.ProtectedEvidenceError, match="authority_duplicate_json_field"
    ):
        verify(sample, duplicate)


@pytest.mark.parametrize(
    ("field", "replacement", "expected"),
    [
        ("schema", "wrong", "authority_service_response_policy_invalid"),
        ("command", "status", "authority_service_response_policy_invalid"),
        ("ok", False, "authority_service_response_policy_invalid"),
    ],
)
def test_fixed_service_envelope_cannot_be_reframed(sample, field, replacement, expected) -> None:
    value = json.loads(sample["response"])
    value[field] = replacement
    with pytest.raises(protected.ProtectedEvidenceError, match=expected):
        verify(sample, canonical(value))


@pytest.mark.parametrize(
    ("field", "replacement", "expected"),
    [
        ("state", "pending", "authority_service_result_policy_invalid"),
        ("requestId", "request-2", "authority_service_request_mismatch"),
        ("encoding", "base64", "authority_service_projection_encoding_invalid"),
        ("size", True, "authority_service_projection_size_invalid"),
        ("size", 1, "authority_service_projection_size_mismatch"),
        ("sha256", "aa" * 32, "authority_service_projection_digest_mismatch"),
        ("bytesBase64Url", "AA", "authority_service_projection_size_mismatch"),
    ],
)
def test_fixed_result_fields_bind_exact_projection(sample, field, replacement, expected) -> None:
    value = json.loads(sample["response"])
    value["result"][field] = replacement
    with pytest.raises(protected.ProtectedEvidenceError, match=expected):
        verify(sample, canonical(value))


def test_request_id_accepts_the_fixed_contract_colon(sample) -> None:
    raw = rebuild(sample, request_id="request:1")
    verified = protected.verify_fixed_authority_projection_response(
        raw,
        trust_context=sample["trust"],
        authority_binding=sample["authority"],
        expected_request_id="request:1",
        expected_ticket_digest=TICKET,
        expected_run_binding_digest=RUN_BINDING,
        verified_at=NOW,
    )
    assert verified.request_id == "request:1"


def test_noncanonical_base64url_is_rejected_before_projection_use(sample) -> None:
    value = json.loads(sample["response"])
    value["result"]["bytesBase64Url"] += "="
    with pytest.raises(
        protected.ProtectedEvidenceError,
        match="authority_service_projection_encoding_invalid",
    ):
        verify(sample, canonical(value))


@pytest.mark.parametrize(
    ("field", "replacement", "expected"),
    [
        ("schema", "legacy", "authority_projection_commit_receipt_policy_invalid"),
        ("event", "resultCommit", "authority_projection_commit_receipt_policy_invalid"),
        ("proofAlgorithm", "replacement", "authority_projection_commit_receipt_policy_invalid"),
        ("signerKeyId", "22" * 32, "authority_projection_commit_identity_mismatch"),
        ("authorityGenerationDigest", "23" * 32, "authority_projection_commit_identity_mismatch"),
        ("ledgerIdentityDigest", "24" * 32, "authority_projection_commit_identity_mismatch"),
        ("ticketDigest", "33" * 32, "authority_projection_commit_request_binding_mismatch"),
        ("runBindingDigest", "34" * 32, "authority_projection_commit_request_binding_mismatch"),
        ("projectionDigest", "35" * 32, "authority_projection_commit_projection_mismatch"),
        ("projectionLength", 1, "authority_projection_commit_projection_mismatch"),
    ],
)
def test_resigned_receipt_still_cannot_drift_identity_or_request_binding(
    sample, field, replacement, expected
) -> None:
    raw = resign(sample, lambda receipt: receipt.__setitem__(field, replacement))
    with pytest.raises(protected.ProtectedEvidenceError, match=expected):
        verify(sample, raw)


@pytest.mark.parametrize(
    "mutator",
    [
        lambda receipt: receipt.__setitem__("anchorSequence", 8),
        lambda receipt: receipt.__setitem__("anchorFrameDigest", "81" * 32),
        lambda receipt: receipt.__setitem__("terminalTicketDigest", "82" * 32),
        lambda receipt: receipt.__setitem__("anchorTicketDigest", "83" * 32),
        lambda receipt: receipt["reopenReadback"].__setitem__("activeTicketCount", 1),
        lambda receipt: receipt["reopenReadback"].__setitem__("frameCount", 9),
        lambda receipt: receipt["reopenReadback"].__setitem__("latestFrameDigest", "84" * 32),
        lambda receipt: receipt["reopenReadback"].__setitem__("ledgerLength", 0),
        lambda receipt: receipt["reopenReadback"].__setitem__("anchorLength", 0),
    ],
)
def test_resigned_receipt_cannot_break_terminal_or_reopen_invariants(sample, mutator) -> None:
    with pytest.raises(
        protected.ProtectedEvidenceError,
        match="authority_projection_commit_(receipt_binding|readback)_invalid",
    ):
        verify(sample, resign(sample, mutator))


def test_receipt_digest_is_domain_and_u64be_length_bound(sample) -> None:
    receipt = sample["receipt"]
    unsigned = copy.deepcopy(receipt)
    unsigned.pop("receiptDigest")
    unsigned.pop("signatureP256")
    body = canonical(unsigned)
    assert receipt["receiptDigest"] == digest(
        RECEIPT_DOMAIN + len(body).to_bytes(8, "big") + body
    )
    assert receipt["receiptDigest"] != digest(body)
    assert receipt["receiptDigest"] != digest(
        RECEIPT_DOMAIN + len(body).to_bytes(8, "little") + body
    )

    value = json.loads(sample["response"])
    value["result"]["projectionCommitReceipt"]["receiptDigest"] = "ee" * 32
    with pytest.raises(
        protected.ProtectedEvidenceError,
        match="authority_projection_commit_receipt_digest_mismatch",
    ):
        verify(sample, canonical(value))


def test_signature_must_be_raw_low_s_and_verify_prehashed_digest(sample) -> None:
    for replacement in ["00" * 64, "01" * 63, "gg" * 64]:
        value = json.loads(sample["response"])
        value["result"]["projectionCommitReceipt"]["signatureP256"] = replacement
        with pytest.raises(
            protected.ProtectedEvidenceError,
            match="authority_projection_commit_signature_invalid",
        ):
            verify(sample, canonical(value))

    value = json.loads(sample["response"])
    raw = bytes.fromhex(value["result"]["projectionCommitReceipt"]["signatureP256"])
    low_s = int.from_bytes(raw[32:], "big")
    high_s = P256_ORDER - low_s
    value["result"]["projectionCommitReceipt"]["signatureP256"] = (
        raw[:32] + high_s.to_bytes(32, "big")
    ).hex()
    with pytest.raises(
        protected.ProtectedEvidenceError,
        match="authority_projection_commit_signature_invalid",
    ):
        verify(sample, canonical(value))

    other_key = ec.generate_private_key(ec.SECP256R1())
    value = json.loads(sample["response"])
    receipt_digest = bytes.fromhex(
        value["result"]["projectionCommitReceipt"]["receiptDigest"]
    )
    value["result"]["projectionCommitReceipt"]["signatureP256"] = raw_low_s_signature(
        other_key, receipt_digest
    )
    with pytest.raises(
        protected.ProtectedEvidenceError,
        match="authority_projection_commit_signature_invalid",
    ):
        verify(sample, canonical(value))


def test_projection_wrapper_is_canonical_exact_and_digest_bound(sample) -> None:
    projection = json.loads(sample["projection"])
    projection["extra"] = True
    replacement = canonical(projection)
    receipt = sign_receipt(
        unsigned_receipt(
            projection=replacement,
            signer_key_id=sample["trust"].signer_key_id,
        ),
        sample["private_key"],
    )
    with pytest.raises(
        protected.ProtectedEvidenceError, match="authority_projection_shape_invalid"
    ):
        verify(sample, rebuild(sample, projection=replacement, receipt=receipt))


@pytest.mark.parametrize("kind", ["digest", "canonical"])
def test_projection_wrapper_rejects_digest_or_canonical_drift(sample, kind) -> None:
    projection = json.loads(sample["projection"])
    if kind == "digest":
        projection["authorityBundleDigest"] = "91" * 32
        replacement = canonical(projection)
        expected = "authority_projection_digest_mismatch"
    else:
        replacement = b" " + canonical(projection)
        expected = "authority_projection_shape_invalid"
    receipt = sign_receipt(
        unsigned_receipt(
            projection=replacement,
            signer_key_id=sample["trust"].signer_key_id,
        ),
        sample["private_key"],
    )
    with pytest.raises(protected.ProtectedEvidenceError, match=expected):
        verify(sample, rebuild(sample, projection=replacement, receipt=receipt))


def test_trust_key_revocation_window_and_machine_binding_fail_closed(sample) -> None:
    trust = sample["trust"]
    revoked = OriginTrustContext(
        policy_id=trust.policy_id,
        attestor_executable_digest=trust.attestor_executable_digest,
        signer_key_id=trust.signer_key_id,
        signer_public_key=trust.signer_public_key,
        revoked_signer_key_ids=frozenset({trust.signer_key_id}),
        not_before=trust.not_before,
        not_after=trust.not_after,
    )
    sample["trust"] = revoked
    with pytest.raises(
        protected.ProtectedEvidenceError, match="authority_projection_commit_signer_revoked"
    ):
        verify(sample)

    sample["trust"] = trust
    sample["authority"] = replace(
        sample["authority"], ledger_identity_digest="99" * 32
    )
    with pytest.raises(
        protected.ProtectedEvidenceError, match="authority_projection_commit_identity_mismatch"
    ):
        verify(sample)


def test_trust_window_and_public_key_identity_are_rechecked(sample) -> None:
    trust = sample["trust"]
    sample["trust"] = replace(
        trust,
        not_before=NOW + timedelta(hours=1),
        not_after=NOW + timedelta(hours=2),
    )
    with pytest.raises(
        protected.ProtectedEvidenceError,
        match="authority_projection_commit_trust_invalid",
    ):
        verify(sample)

    other_key = ec.generate_private_key(ec.SECP256R1())
    sample["trust"] = replace(
        trust, signer_public_key=public_key_bytes(other_key)
    )
    with pytest.raises(
        protected.ProtectedEvidenceError,
        match="authority_projection_commit_identity_mismatch",
    ):
        verify(sample)
