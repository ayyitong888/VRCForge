from __future__ import annotations

import base64
import copy
import hashlib
import hmac
import json
from collections.abc import Mapping
from dataclasses import dataclass, replace
from datetime import datetime, timedelta, timezone
import pytest
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import ec, utils

import primitive_basis_live_attestation as live
import primitive_basis_matrix as matrix
import primitive_basis_origin_attestation as origin
import primitive_basis_protected_evidence as protected


BASE_TIME = datetime(2026, 7, 24, 0, 0, tzinfo=timezone.utc)
PROJECT_DIGEST = hashlib.sha256(b"project-binding").hexdigest()
P256_ORDER = int(
    "FFFFFFFF00000000FFFFFFFFFFFFFFFFBCE6FAADA7179E84F3B9CAC2FC632551", 16
)


def _digest(label: str) -> str:
    return hashlib.sha256(label.encode("utf-8")).hexdigest()


def _canonical_bytes(value: object) -> bytes:
    return json.dumps(
        value,
        ensure_ascii=True,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")


def _digest_json(value: object) -> str:
    return hashlib.sha256(_canonical_bytes(value)).hexdigest()


def _run_admission_digest(*digests: str) -> str:
    value = hashlib.sha256(b"vrcforge-primitive-basis-run-admission-v1\0")
    for digest in digests:
        value.update(bytes.fromhex(digest))
    return value.hexdigest()


def _timestamp(value: datetime) -> str:
    return value.astimezone(timezone.utc).isoformat(timespec="microseconds").replace(
        "+00:00", "Z"
    )


def _base64url(value: bytes) -> str:
    return base64.urlsafe_b64encode(value).rstrip(b"=").decode("ascii")


def _public_key_bytes(private_key: ec.EllipticCurvePrivateKey) -> bytes:
    return private_key.public_key().public_bytes(
        serialization.Encoding.X962,
        serialization.PublicFormat.UncompressedPoint,
    )


def _sign_envelope(
    envelope: dict[str, object], private_key: ec.EllipticCurvePrivateKey
) -> None:
    unsigned = copy.deepcopy(envelope)
    unsigned.pop("signature", None)
    der_signature = private_key.sign(
        _canonical_bytes(unsigned),
        ec.ECDSA(hashes.SHA256()),
    )
    r_value, s_value = utils.decode_dss_signature(der_signature)
    s_value = min(s_value, P256_ORDER - s_value)
    envelope["signature"] = _base64url(
        r_value.to_bytes(32, "big") + s_value.to_bytes(32, "big")
    )


def _refresh_ticket_and_sign(
    envelope: dict[str, object], private_key: ec.EllipticCurvePrivateKey
) -> None:
    envelope["ticketDigest"] = _digest_json(envelope["ticket"])
    _sign_envelope(envelope, private_key)


def _refresh_finalization_and_sign(
    envelope: dict[str, object],
    finalization: dict[str, object],
    private_key: ec.EllipticCurvePrivateKey,
) -> None:
    envelope["finalizationDigest"] = _digest_json(finalization)
    _sign_envelope(envelope, private_key)


def _refresh_inner_proof(finalization: dict[str, object]) -> None:
    attestation = finalization["attestation"]
    assert isinstance(attestation, dict)
    unsigned = copy.deepcopy(attestation)
    unsigned.pop("proof", None)
    attestation["proof"] = hmac.new(
        b"k" * 32,
        _canonical_bytes(unsigned),
        hashlib.sha256,
    ).hexdigest()


def _refresh_process_graph_and_sign(
    envelope: dict[str, object], private_key: ec.EllipticCurvePrivateKey
) -> None:
    process_graph = envelope["processGraph"]
    assert isinstance(process_graph, list)
    for row in process_graph:
        assert isinstance(row, dict)
        unsigned = dict(row)
        unsigned.pop("identityDigest", None)
        row["identityDigest"] = _digest_json(unsigned)
    envelope["processGraphDigest"] = _digest_json(process_graph)
    _sign_envelope(envelope, private_key)


def _refresh_network_bindings_and_sign(
    envelope: dict[str, object], private_key: ec.EllipticCurvePrivateKey
) -> None:
    envelope["networkBindingsDigest"] = _digest_json(envelope["networkBindings"])
    _sign_envelope(envelope, private_key)


def _refresh_cleanup_and_sign(
    envelope: dict[str, object], private_key: ec.EllipticCurvePrivateKey
) -> None:
    envelope["cleanupDigest"] = _digest_json(envelope["cleanup"])
    _sign_envelope(envelope, private_key)


class TickClock:
    def __init__(self, value: datetime) -> None:
        self.value = value

    def __call__(self) -> datetime:
        self.value += timedelta(seconds=1)
        return self.value


def _facts(phase: str) -> dict[str, object]:
    baseline = _digest("baseline")
    inventory = _digest("inventory")
    applied = _digest("applied")
    arguments = _digest("arguments")
    operation = _digest("operation")
    fixture_project = _digest("fixture-project")
    unity_process = _digest("unity-process")
    request_id = "request-apply"
    approval_id = "approval-apply"
    checkpoint_id = "checkpoint-apply"
    restore_arguments = _digest("restore-arguments")
    return {
        "detect": {
            "stateDigest": baseline,
            "inventoryDigest": inventory,
            "componentPresent": False,
        },
        "preview": {
            "beforeStateDigest": baseline,
            "afterStateDigest": baseline,
            "mutationCount": 0,
        },
        "request": {
            "requestId": request_id,
            "targetTool": live.MODEL_TARGET_TOOL,
            "argumentsDigest": arguments,
            "operationDigest": operation,
            "projectBindingDigest": PROJECT_DIGEST,
            "state": "approval_pending",
        },
        "approval": {
            "requestId": request_id,
            "approvalId": approval_id,
            "targetTool": live.MODEL_TARGET_TOOL,
            "argumentsDigest": arguments,
            "operationDigest": operation,
            "projectBindingDigest": PROJECT_DIGEST,
            "pendingObserved": True,
            "approved": True,
        },
        "checkpoint": {
            "approvalId": approval_id,
            "checkpointId": checkpoint_id,
            "targetTool": live.MODEL_TARGET_TOOL,
            "argumentsDigest": arguments,
            "operationDigest": operation,
            "projectBindingDigest": PROJECT_DIGEST,
            "fixtureProjectInputDigest": fixture_project,
            "unityProcessIdentityDigest": unity_process,
            "created": True,
        },
        "apply": {
            "executionId": "execution-apply",
            "approvalId": approval_id,
            "checkpointId": checkpoint_id,
            "targetTool": live.MODEL_TARGET_TOOL,
            "argumentsDigest": arguments,
            "operationDigest": operation,
            "projectBindingDigest": PROJECT_DIGEST,
            "fixtureProjectInputDigest": fixture_project,
            "applied": True,
        },
        "readback": {
            "checkpointId": checkpoint_id,
            "expectedStateDigest": applied,
            "actualStateDigest": applied,
            "matched": True,
        },
        "validation": {
            "checkpointId": checkpoint_id,
            "passed": True,
            "reportDigest": _digest("validation-report"),
        },
        "restore_request": {
            "requestId": "request-restore",
            "targetTool": live.RESTORE_TARGET_TOOL,
            "checkpointId": checkpoint_id,
            "projectBindingDigest": PROJECT_DIGEST,
            "argumentsDigest": restore_arguments,
            "state": "approval_pending",
        },
        "restore_approval": {
            "requestId": "request-restore",
            "approvalId": "approval-restore",
            "targetTool": live.RESTORE_TARGET_TOOL,
            "checkpointId": checkpoint_id,
            "projectBindingDigest": PROJECT_DIGEST,
            "argumentsDigest": restore_arguments,
            "pendingObserved": True,
            "approved": True,
        },
        "restore_execution": {
            "executionId": "execution-restore",
            "approvalId": "approval-restore",
            "targetTool": live.RESTORE_TARGET_TOOL,
            "checkpointId": checkpoint_id,
            "projectBindingDigest": PROJECT_DIGEST,
            "argumentsDigest": restore_arguments,
            "unityProcessIdentityDigest": unity_process,
            "restored": True,
        },
        "baseline_comparison": {
            "checkpointId": checkpoint_id,
            "expectedBaselineDigest": baseline,
            "actualStateDigest": baseline,
            "matched": True,
        },
        "residue": {
            "checkpointId": checkpoint_id,
            "inventoryDigest": inventory,
            "count": 0,
            "projectRemoved": True,
            "unityProcessExited": True,
            "projectMcpCoreRemoved": True,
        },
    }[phase]


def _fixture_set() -> matrix.FixtureSet:
    fixtures = []
    for scenario_id, required_primitives in matrix.SCENARIO_DEFINITIONS.items():
        fixtures.append(
            matrix.PrimitiveFixture(
                scenario_id=scenario_id,
                fixture_root=f"Assets/VRCForge/PrimitiveBasis/{scenario_id}",
                baseline_manifest="baseline.json",
                required_primitives=required_primitives,
                descriptor_digest=_digest(f"descriptor:{scenario_id}"),
                digest=_digest(f"fixture:{scenario_id}"),
                materialized=True,
                materialization_error="",
                source_name=f"{scenario_id}.json",
            )
        )
    return matrix.FixtureSet(
        fixtures=tuple(fixtures),
        descriptor_digest=_digest("fixture-set-descriptor"),
        digest=_digest("fixture-set"),
    )


@dataclass
class SignedSample:
    private_key: ec.EllipticCurvePrivateKey
    trust_payload: dict[str, object]
    trust_context: origin.OriginTrustContext
    expected: origin.OriginExpectedBinding
    finalization: dict[str, object]
    envelope: dict[str, object]
    verified_at: datetime
    fixtures: matrix.FixtureSet


def _make_signed_sample() -> SignedSample:
    fixtures = _fixture_set()
    model_fixture = next(
        item for item in fixtures.fixtures if item.scenario_id == live.MODEL_SCENARIO_ID
    )
    expected = origin.OriginExpectedBinding(
        manifest_digest=_digest("manifest"),
        portable_digest=_digest("portable"),
        desktop_executable_digest=_digest("desktop"),
        backend_executable_digest=_digest("backend"),
        backend_tree_digest=_digest("backend-tree"),
        runner_digest=_digest("runner"),
        unity_package_digest=_digest("unity-package"),
        packaged_unity_tool_tree_digest=_digest("packaged-unity-tool-tree"),
        runtime_unity_tool_tree_digest=_digest("runtime-unity-tool-tree"),
        unity_editor_digest=_digest("unity-editor"),
        bridge_launcher_executable_digest=_digest("bridge-launcher"),
        bridge_listener_executable_digest=_digest("bridge-listener"),
        connector_digest=_digest("connector"),
        server_digest=_digest("server"),
        dependency_set_digest=_digest("dependencies"),
        fixture_set_descriptor_digest=fixtures.descriptor_digest,
        fixture_descriptor_digest=model_fixture.descriptor_digest,
        fixture_project_input_digest=_digest("fixture-project"),
        fixture_digest=model_fixture.digest,
        runtime_binding_digest=_digest("runtime"),
    )
    base_bootstrap = live.LiveBootstrap(
        key=b"k" * 32,
        challenge=b"c" * 32,
        runtime_binding_digest=expected.runtime_binding_digest,
        desktop_executable_digest=expected.desktop_executable_digest,
        backend_executable_digest=expected.backend_executable_digest,
        runner_digest=expected.runner_digest,
        unity_package_digest=expected.unity_package_digest,
        unity_editor_digest=expected.unity_editor_digest,
        fixture_project_input_digest=expected.fixture_project_input_digest,
        fixture_set_descriptor_digest=expected.fixture_set_descriptor_digest,
        fixture_descriptor_digest=expected.fixture_descriptor_digest,
    )
    private_key = ec.generate_private_key(ec.SECP256R1())
    public_key = _public_key_bytes(private_key)
    signer_key_id = hashlib.sha256(public_key).hexdigest()
    attestor_digest = _digest("external-attestor")
    trust_payload: dict[str, object] = {
        "schema": origin.ORIGIN_TRUST_SCHEMA,
        "policyId": "primitive-live-policy-1",
        "attestorExecutableDigest": attestor_digest,
        "signerKeyId": signer_key_id,
        "signerPublicKey": _base64url(public_key),
        "revokedSignerKeyIds": [],
        "notBefore": _timestamp(BASE_TIME - timedelta(days=1)),
        "notAfter": _timestamp(BASE_TIME + timedelta(days=1)),
    }
    trust_context = origin.parse_origin_trust_context(trust_payload)
    ticket: dict[str, object] = {
        "schema": origin.ORIGIN_TICKET_SCHEMA,
        "policyId": trust_context.policy_id,
        "ticketId": "ticket-1",
        "runId": base_bootstrap.run_id,
        "challengeDigest": base_bootstrap.challenge_digest,
        "issuedAt": _timestamp(BASE_TIME),
        "expiresAt": _timestamp(BASE_TIME + timedelta(hours=1)),
        "attestorExecutableDigest": attestor_digest,
        **expected.ticket_values(),
    }
    ticket_digest = _digest_json(ticket)
    bootstrap = replace(base_bootstrap, origin_ticket_digest=ticket_digest)
    clock = TickClock(BASE_TIME + timedelta(seconds=4))
    session = live.PrimitiveBasisLiveSession(bootstrap, now=clock)
    session.begin(
        fixture_digest=expected.fixture_digest,
        project_binding_digest=PROJECT_DIGEST,
    )
    for phase in live.LIVE_PHASES:
        session.record(
            phase,
            _facts(phase),
            authoritative_event={"phase": phase, "source": "backend"},
        )
    finalization = session.finalize()
    process_specs = (
        ("attestor", 1001, 0, 0, -1, attestor_digest),
        ("desktop", 1002, 1001, 1001, 1, expected.desktop_executable_digest),
        ("backend", 1003, 1002, 1002, 2, expected.backend_executable_digest),
        ("unity", 1004, 1001, 1001, 1, expected.unity_editor_digest),
        (
            "bridge_launcher",
            1005,
            1004,
            1004,
            2,
            expected.bridge_launcher_executable_digest,
        ),
        (
            "bridge_listener",
            1006,
            1005,
            1005,
            3,
            expected.bridge_listener_executable_digest,
        ),
    )
    process_graph: list[dict[str, object]] = []
    for (
        role,
        pid,
        parent_pid,
        supervisor_pid,
        start_offset,
        executable_digest,
    ) in process_specs:
        process: dict[str, object] = {
            "role": role,
            "pid": pid,
            "parentPid": parent_pid,
            "supervisorPid": supervisor_pid,
            "startedAt": _timestamp(BASE_TIME + timedelta(seconds=start_offset)),
            "executableDigest": executable_digest,
        }
        process["identityDigest"] = _digest_json(process)
        process_graph.append(process)
    network_bindings: list[dict[str, object]] = [
        {
            "role": "app",
            "protocol": "tcp",
            "localAddress": "127.0.0.1",
            "localPort": 8757,
            "ownerPid": 1003,
            "ownerIdentityDigest": process_graph[2]["identityDigest"],
            "state": "listen",
            "observedAt": _timestamp(BASE_TIME + timedelta(seconds=3)),
        },
        {
            "role": "bridge",
            "protocol": "tcp",
            "localAddress": "127.0.0.1",
            "localPort": 8080,
            "ownerPid": 1006,
            "ownerIdentityDigest": process_graph[5]["identityDigest"],
            "state": "listen",
            "observedAt": _timestamp(BASE_TIME + timedelta(seconds=4)),
        },
    ]
    cleanup: dict[str, object] = {
        "desktopExited": True,
        "backendExited": True,
        "unityExited": True,
        "bridgeLauncherExited": True,
        "bridgeListenerExited": True,
        "appPortReleased": True,
        "projectRemoved": True,
        "projectMcpCoreRemoved": True,
        "observedAt": _timestamp(BASE_TIME + timedelta(seconds=20)),
    }
    envelope: dict[str, object] = {
        "schema": origin.ORIGIN_ENVELOPE_SCHEMA_V1,
        "proofAlgorithm": origin.ORIGIN_PROOF_ALGORITHM,
        "originTrust": origin.ORIGIN_TRUST_KIND,
        "signerKeyId": signer_key_id,
        "attestorExecutableDigest": attestor_digest,
        "ticket": ticket,
        "ticketDigest": _digest_json(ticket),
        "finalizationDigest": _digest_json(finalization),
        "projectBindingDigest": PROJECT_DIGEST,
        "processGraph": process_graph,
        "processGraphDigest": _digest_json(process_graph),
        "networkBindings": network_bindings,
        "networkBindingsDigest": _digest_json(network_bindings),
        "cleanup": cleanup,
        "cleanupDigest": _digest_json(cleanup),
        "signedAt": _timestamp(BASE_TIME + timedelta(seconds=30)),
        "signature": "",
    }
    _sign_envelope(envelope, private_key)
    return SignedSample(
        private_key=private_key,
        trust_payload=trust_payload,
        trust_context=trust_context,
        expected=expected,
        finalization=finalization,
        envelope=envelope,
        verified_at=BASE_TIME + timedelta(seconds=31),
        fixtures=fixtures,
    )


@pytest.fixture
def sample() -> SignedSample:
    return _make_signed_sample()


def _verify(
    sample: SignedSample,
    *,
    finalization: dict[str, object] | None = None,
    envelope: dict[str, object] | None = None,
    trust_context: origin.OriginTrustContext | None = None,
    verified_at: datetime | None = None,
    replay_guard: origin.OriginReplayGuard | None = None,
) -> live.VerifiedLiveRun:
    return origin.verify_trusted_live_origin(
        sample.finalization if finalization is None else finalization,
        sample.envelope if envelope is None else envelope,
        trust_context=sample.trust_context if trust_context is None else trust_context,
        expected=sample.expected,
        project_binding_digest=PROJECT_DIGEST,
        verified_at=sample.verified_at if verified_at is None else verified_at,
        replay_guard=replay_guard,
    )


def _v2_origin_envelope(
    sample: SignedSample,
    authority_ticket_digest: str,
) -> dict[str, object]:
    envelope = copy.deepcopy(sample.envelope)
    envelope["schema"] = origin.ORIGIN_ENVELOPE_SCHEMA_V2
    envelope["authorityTicketDigest"] = authority_ticket_digest
    _sign_envelope(envelope, sample.private_key)
    return envelope


def test_v1_origin_envelope_remains_verifiable(sample: SignedSample) -> None:
    verified = _verify(sample)

    assert type(verified) is live.VerifiedLiveRun
    assert verified.origin_ticket_digest == sample.envelope["ticketDigest"]


def test_v2_origin_envelope_carries_two_signed_ticket_digests(
    sample: SignedSample,
) -> None:
    authority_ticket_digest = _digest("authority-runtime-ticket:v2")
    envelope = _v2_origin_envelope(sample, authority_ticket_digest)

    verified = _verify(sample, envelope=envelope)

    assert isinstance(verified, origin.VerifiedOriginLiveRun)
    assert verified.origin_envelope_schema == origin.ORIGIN_ENVELOPE_SCHEMA_V2
    assert verified.origin_ticket_digest == _digest_json(envelope["ticket"])
    assert verified.authority_ticket_digest == authority_ticket_digest
    assert verified.origin_ticket_digest != verified.authority_ticket_digest


@pytest.mark.parametrize("mutation", ["missing", "extra", "zero"])
def test_v2_origin_envelope_requires_exact_nonzero_authority_ticket(
    sample: SignedSample,
    mutation: str,
) -> None:
    envelope = _v2_origin_envelope(sample, _digest("authority-runtime-ticket:v2"))
    if mutation == "missing":
        envelope.pop("authorityTicketDigest")
    elif mutation == "extra":
        envelope["authorityTicket"] = "unexpected"
    else:
        envelope["authorityTicketDigest"] = "0" * 64
    _sign_envelope(envelope, sample.private_key)

    with pytest.raises(live.LiveAttestationError):
        _verify(sample, envelope=envelope)


def test_v2_authority_ticket_is_covered_by_the_origin_signature(
    sample: SignedSample,
) -> None:
    envelope = _v2_origin_envelope(sample, _digest("authority-runtime-ticket:v2"))
    envelope["authorityTicketDigest"] = _digest("tampered-runtime-ticket")

    with pytest.raises(live.LiveAttestationError, match="origin signature mismatch"):
        _verify(sample, envelope=envelope)


def test_valid_external_origin_verifies_but_full_gate_remains_closed(
    sample: SignedSample,
) -> None:
    verified = _verify(sample)

    assert verified.origin_verified is True
    assert verified.origin_signer_key_id == sample.trust_context.signer_key_id
    assert verified.inner_attestation_digest == _digest_json(
        sample.finalization["attestation"]
    )
    assert verified.origin_network_binding_digest == sample.envelope[
        "networkBindingsDigest"
    ]
    report = live.build_live_matrix_report(sample.fixtures, verified)
    full_rows = [row for row in report["rows"] if row["status"] == "full"]
    assert full_rows == []
    assert report["targetOk"] is False
    assert report["ok"] is False
    assert report["summary"]["fullScenarioCount"] == 0
    assert all(row["status"] == "blocked" for row in report["rows"])


def test_public_verified_value_cannot_grant_full_without_raw_reverification(
    sample: SignedSample,
) -> None:
    forged = live.VerifiedLiveRun(
        **{
            **_verify(sample).__dict__,
            "origin_verified": True,
            "inner_attestation_digest": _digest("forged-inner"),
            "origin_signer_key_id": _digest("forged-signer"),
            "origin_ticket_digest": _digest("forged-ticket"),
            "origin_process_graph_digest": _digest("forged-process"),
            "origin_network_binding_digest": _digest("forged-network"),
            "origin_cleanup_digest": _digest("forged-cleanup"),
        }
    )

    report = live.build_live_matrix_report(sample.fixtures, forged)

    assert report["targetOk"] is False
    assert report["runtimeBinding"]["liveRunnerAttested"] is False
    target = next(
        row for row in report["rows"] if row["scenarioId"] == live.MODEL_SCENARIO_ID
    )
    assert target["status"] == "blocked"


def test_unknown_signer_is_not_accepted(sample: SignedSample) -> None:
    attacker = ec.generate_private_key(ec.SECP256R1())
    envelope = copy.deepcopy(sample.envelope)
    envelope["signerKeyId"] = hashlib.sha256(_public_key_bytes(attacker)).hexdigest()
    _sign_envelope(envelope, attacker)

    with pytest.raises(live.LiveAttestationError, match="not pinned"):
        _verify(sample, envelope=envelope)


def test_revoked_signer_is_not_accepted(sample: SignedSample) -> None:
    trust_payload = copy.deepcopy(sample.trust_payload)
    trust_payload["revokedSignerKeyIds"] = [sample.trust_context.signer_key_id]
    revoked_trust = origin.parse_origin_trust_context(trust_payload)

    with pytest.raises(live.LiveAttestationError, match="revoked"):
        _verify(sample, trust_context=revoked_trust)


def test_report_supplied_public_key_cannot_extend_trust(sample: SignedSample) -> None:
    attacker = ec.generate_private_key(ec.SECP256R1())
    attacker_public = _public_key_bytes(attacker)
    envelope = copy.deepcopy(sample.envelope)
    envelope["signerKeyId"] = hashlib.sha256(attacker_public).hexdigest()
    envelope["signerPublicKey"] = _base64url(attacker_public)
    _sign_envelope(envelope, attacker)

    with pytest.raises(live.LiveAttestationError, match="fields mismatch"):
        _verify(sample, envelope=envelope)


def test_proof_algorithm_downgrade_is_rejected(sample: SignedSample) -> None:
    envelope = copy.deepcopy(sample.envelope)
    envelope["proofAlgorithm"] = "ecdsa-sha256-v0"
    _sign_envelope(envelope, sample.private_key)

    with pytest.raises(live.LiveAttestationError, match="algorithm mismatch"):
        _verify(sample, envelope=envelope)


@pytest.mark.parametrize("mutation", ["inner_origin_true", "inner_extra", "envelope_extra"])
def test_origin_verified_injection_and_extra_fields_are_rejected(
    sample: SignedSample, mutation: str
) -> None:
    finalization = copy.deepcopy(sample.finalization)
    envelope = copy.deepcopy(sample.envelope)
    if mutation == "inner_origin_true":
        finalization["attestation"]["originVerified"] = True
        _refresh_finalization_and_sign(envelope, finalization, sample.private_key)
    elif mutation == "inner_extra":
        finalization["attestation"]["trustedOrigin"] = True
        _refresh_finalization_and_sign(envelope, finalization, sample.private_key)
    else:
        envelope["originVerified"] = True
        _sign_envelope(envelope, sample.private_key)

    with pytest.raises(live.LiveAttestationError):
        _verify(sample, finalization=finalization, envelope=envelope)


def test_ticket_is_consumed_exactly_once(sample: SignedSample) -> None:
    replay_guard = origin.OriginReplayGuard()
    assert _verify(sample, replay_guard=replay_guard).origin_verified is True

    with pytest.raises(live.LiveAttestationError, match="replayed"):
        _verify(sample, replay_guard=replay_guard)


def test_ticket_identity_change_cannot_reuse_an_authenticated_finalization(
    sample: SignedSample,
) -> None:
    envelope = copy.deepcopy(sample.envelope)
    envelope["ticket"]["ticketId"] = "ticket-2"
    _refresh_ticket_and_sign(envelope, sample.private_key)

    with pytest.raises(live.LiveAttestationError, match="attestation binding"):
        _verify(sample, envelope=envelope)


def test_expired_ticket_is_rejected(sample: SignedSample) -> None:
    envelope = copy.deepcopy(sample.envelope)
    envelope["ticket"]["expiresAt"] = _timestamp(BASE_TIME + timedelta(seconds=31))
    _refresh_ticket_and_sign(envelope, sample.private_key)

    with pytest.raises(live.LiveAttestationError, match="time window"):
        _verify(
            sample,
            envelope=envelope,
            verified_at=BASE_TIME + timedelta(minutes=10),
        )


def test_future_ticket_is_rejected(sample: SignedSample) -> None:
    envelope = copy.deepcopy(sample.envelope)
    verified_at = sample.verified_at
    envelope["ticket"]["issuedAt"] = _timestamp(verified_at + timedelta(minutes=4))
    envelope["ticket"]["expiresAt"] = _timestamp(verified_at + timedelta(hours=1))
    envelope["signedAt"] = _timestamp(verified_at + timedelta(minutes=4, seconds=1))
    _refresh_ticket_and_sign(envelope, sample.private_key)

    with pytest.raises(live.LiveAttestationError, match="time window"):
        _verify(sample, envelope=envelope, verified_at=verified_at)


@pytest.mark.parametrize(
    ("field", "replacement", "message"),
    [
        ("attestorExecutableDigest", _digest("other-attestor"), "attestor mismatch"),
        ("backendTreeDigest", _digest("other-backend-tree"), "backendTreeDigest mismatch"),
        ("runtimeBindingDigest", _digest("other-runtime"), "runtimeBindingDigest mismatch"),
    ],
)
def test_ticket_binding_tampering_is_rejected(
    sample: SignedSample, field: str, replacement: str, message: str
) -> None:
    envelope = copy.deepcopy(sample.envelope)
    envelope["ticket"][field] = replacement
    _refresh_ticket_and_sign(envelope, sample.private_key)

    with pytest.raises(live.LiveAttestationError, match=message):
        _verify(sample, envelope=envelope)


def test_authenticated_finalization_semantic_tampering_is_rejected(
    sample: SignedSample,
) -> None:
    finalization = copy.deepcopy(sample.finalization)
    envelope = copy.deepcopy(sample.envelope)
    finalization["evidence"]["rows"][0]["receipts"][0]["facts"][
        "stateDigest"
    ] = _digest("tampered-baseline")
    _refresh_finalization_and_sign(envelope, finalization, sample.private_key)

    with pytest.raises(live.LiveAttestationError):
        _verify(sample, finalization=finalization, envelope=envelope)


def test_process_executable_tampering_is_rejected(sample: SignedSample) -> None:
    envelope = copy.deepcopy(sample.envelope)
    envelope["processGraph"][2]["executableDigest"] = _digest("other-backend")
    _refresh_process_graph_and_sign(envelope, sample.private_key)

    with pytest.raises(live.LiveAttestationError, match="executable mismatch"):
        _verify(sample, envelope=envelope)


def test_bridge_listener_must_own_the_observed_port(sample: SignedSample) -> None:
    envelope = copy.deepcopy(sample.envelope)
    envelope["networkBindings"][1]["ownerPid"] = envelope["processGraph"][4]["pid"]
    _refresh_network_bindings_and_sign(envelope, sample.private_key)

    with pytest.raises(live.LiveAttestationError, match="port owner mismatch"):
        _verify(sample, envelope=envelope)


def test_port_observation_binds_process_start_identity(sample: SignedSample) -> None:
    envelope = copy.deepcopy(sample.envelope)
    envelope["processGraph"][5]["startedAt"] = _timestamp(
        BASE_TIME + timedelta(seconds=4)
    )
    _refresh_process_graph_and_sign(envelope, sample.private_key)

    with pytest.raises(live.LiveAttestationError, match="port owner mismatch"):
        _verify(sample, envelope=envelope)


def test_port_observation_must_precede_inner_start(sample: SignedSample) -> None:
    envelope = copy.deepcopy(sample.envelope)
    envelope["networkBindings"][1]["observedAt"] = _timestamp(
        BASE_TIME + timedelta(seconds=6)
    )
    _refresh_network_bindings_and_sign(envelope, sample.private_key)

    with pytest.raises(live.LiveAttestationError, match="escaped the ticket window"):
        _verify(sample, envelope=envelope)


def test_port_observation_cannot_predate_owner_process(sample: SignedSample) -> None:
    envelope = copy.deepcopy(sample.envelope)
    envelope["networkBindings"][1]["observedAt"] = _timestamp(
        BASE_TIME + timedelta(seconds=2)
    )
    _refresh_network_bindings_and_sign(envelope, sample.private_key)

    with pytest.raises(live.LiveAttestationError, match="escaped the ticket window"):
        _verify(sample, envelope=envelope)


def test_cleanup_cannot_predate_inner_finalization(sample: SignedSample) -> None:
    envelope = copy.deepcopy(sample.envelope)
    envelope["cleanup"]["observedAt"] = _timestamp(
        BASE_TIME + timedelta(seconds=14)
    )
    _refresh_cleanup_and_sign(envelope, sample.private_key)

    with pytest.raises(live.LiveAttestationError, match="cleanup timestamp is invalid"):
        _verify(sample, envelope=envelope)


def test_inner_run_cannot_start_before_ticket(sample: SignedSample) -> None:
    finalization = copy.deepcopy(sample.finalization)
    envelope = copy.deepcopy(sample.envelope)
    finalization["attestation"]["startedAt"] = _timestamp(
        BASE_TIME - timedelta(seconds=1)
    )
    _refresh_inner_proof(finalization)
    _refresh_finalization_and_sign(envelope, finalization, sample.private_key)

    with pytest.raises(live.LiveAttestationError, match="inner run escaped"):
        _verify(sample, finalization=finalization, envelope=envelope)


def test_inner_run_cannot_finalize_after_origin_signature(sample: SignedSample) -> None:
    finalization = copy.deepcopy(sample.finalization)
    envelope = copy.deepcopy(sample.envelope)
    finalization["attestation"]["finalizedAt"] = _timestamp(
        BASE_TIME + timedelta(seconds=31)
    )
    _refresh_inner_proof(finalization)
    _refresh_finalization_and_sign(envelope, finalization, sample.private_key)

    with pytest.raises(live.LiveAttestationError, match="inner run escaped"):
        _verify(sample, finalization=finalization, envelope=envelope)


def test_cleanup_tampering_is_rejected(sample: SignedSample) -> None:
    envelope = copy.deepcopy(sample.envelope)
    envelope["cleanup"]["backendExited"] = False
    _refresh_cleanup_and_sign(envelope, sample.private_key)

    with pytest.raises(live.LiveAttestationError, match="cleanup is incomplete"):
        _verify(sample, envelope=envelope)


def test_cleanup_rejects_legacy_bridge_port_instead_of_project_core_removal(
    sample: SignedSample,
) -> None:
    envelope = copy.deepcopy(sample.envelope)
    cleanup = envelope["cleanup"]
    cleanup.pop("projectMcpCoreRemoved")
    cleanup["bridgePortReleased"] = True
    _refresh_cleanup_and_sign(envelope, sample.private_key)

    with pytest.raises(live.LiveAttestationError, match="origin cleanup fields mismatch"):
        _verify(sample, envelope=envelope)


def test_duplicate_process_pid_is_rejected(sample: SignedSample) -> None:
    envelope = copy.deepcopy(sample.envelope)
    envelope["processGraph"][2]["pid"] = envelope["processGraph"][1]["pid"]
    _refresh_process_graph_and_sign(envelope, sample.private_key)

    with pytest.raises(live.LiveAttestationError, match="process identity"):
        _verify(sample, envelope=envelope)


def test_attestor_must_start_before_ticket_issuance(sample: SignedSample) -> None:
    envelope = copy.deepcopy(sample.envelope)
    envelope["processGraph"][0]["startedAt"] = _timestamp(
        BASE_TIME + timedelta(seconds=1)
    )
    _refresh_process_graph_and_sign(envelope, sample.private_key)

    with pytest.raises(live.LiveAttestationError, match="attestor started after"):
        _verify(sample, envelope=envelope)


def test_child_process_cannot_predate_parent(sample: SignedSample) -> None:
    envelope = copy.deepcopy(sample.envelope)
    envelope["processGraph"][5]["startedAt"] = _timestamp(
        BASE_TIME + timedelta(seconds=1)
    )
    _refresh_process_graph_and_sign(envelope, sample.private_key)

    with pytest.raises(live.LiveAttestationError, match="child process predates"):
        _verify(sample, envelope=envelope)


def test_inner_run_cannot_predate_backend_or_bridge_readiness(
    sample: SignedSample,
) -> None:
    finalization = copy.deepcopy(sample.finalization)
    envelope = copy.deepcopy(sample.envelope)
    finalization["attestation"]["startedAt"] = _timestamp(
        BASE_TIME + timedelta(seconds=1)
    )
    _refresh_inner_proof(finalization)
    _refresh_finalization_and_sign(envelope, finalization, sample.private_key)

    with pytest.raises(live.LiveAttestationError, match="predates required process"):
        _verify(sample, finalization=finalization, envelope=envelope)


@pytest.mark.parametrize("role_index", [2, 4, 5])
def test_process_parent_chain_must_match_supervision(
    sample: SignedSample, role_index: int
) -> None:
    envelope = copy.deepcopy(sample.envelope)
    envelope["processGraph"][role_index]["parentPid"] = 0
    _refresh_process_graph_and_sign(envelope, sample.private_key)

    with pytest.raises(live.LiveAttestationError, match="supervision chain"):
        _verify(sample, envelope=envelope)


def test_candidate_child_cannot_predate_the_ticket(sample: SignedSample) -> None:
    envelope = copy.deepcopy(sample.envelope)
    envelope["processGraph"][1]["startedAt"] = _timestamp(
        BASE_TIME - timedelta(seconds=1)
    )
    _refresh_process_graph_and_sign(envelope, sample.private_key)

    with pytest.raises(live.LiveAttestationError, match="escaped the ticket window"):
        _verify(sample, envelope=envelope)


def test_invalid_signature_is_rejected(sample: SignedSample) -> None:
    envelope = copy.deepcopy(sample.envelope)
    signature = envelope["signature"]
    envelope["signature"] = ("A" if signature[0] != "A" else "B") + signature[1:]

    with pytest.raises(live.LiveAttestationError, match="signature mismatch"):
        _verify(sample, envelope=envelope)


def test_high_s_signature_encoding_is_rejected(sample: SignedSample) -> None:
    envelope = copy.deepcopy(sample.envelope)
    signature = str(envelope["signature"])
    raw = base64.urlsafe_b64decode(signature + "=" * ((4 - len(signature) % 4) % 4))
    low_s = int.from_bytes(raw[32:], "big")
    high_s = P256_ORDER - low_s
    assert high_s > P256_ORDER // 2
    envelope["signature"] = _base64url(raw[:32] + high_s.to_bytes(32, "big"))

    with pytest.raises(live.LiveAttestationError, match="not canonical"):
        _verify(sample, envelope=envelope)


def test_private_key_material_never_enters_origin_envelope(sample: SignedSample) -> None:
    private_value = sample.private_key.private_numbers().private_value.to_bytes(32, "big")
    serialized = json.dumps(sample.envelope, sort_keys=True)

    assert private_value.hex() not in serialized
    assert _base64url(private_value) not in serialized
    assert "signerPublicKey" not in sample.envelope


@dataclass
class ProtectedSourceContractSample:
    signed: SignedSample
    authority_binding: protected.ProtectedAuthorityBinding
    package_binding: protected.ProtectedPackageBinding
    row_binding: protected.ProtectedRowBinding
    bundle: dict[str, object]
    ledger: dict[str, object]
    raw_bundle: bytes
    raw_ledger: bytes
    origin_ticket_digest: str
    authority_ticket_digest: str


def _parse_timestamp(value: object) -> datetime:
    assert isinstance(value, str) and value.endswith("Z")
    return datetime.fromisoformat(value[:-1] + "+00:00").astimezone(timezone.utc)


def _make_protected_source_contract_sample(
    sample: SignedSample,
    *,
    request_id: str | None = None,
    legacy_v1: bool = False,
) -> ProtectedSourceContractSample:
    authority_binding = protected.ProtectedAuthorityBinding(
        policy_id=sample.trust_context.policy_id,
        authority_generation_digest=_digest("authority-generation"),
        protected_manifest_digest=_digest("protected-manifest"),
        installed_layout_digest=_digest("installed-layout"),
        service_executable_digest=sample.trust_context.attestor_executable_digest,
        controller_executable_digest=_digest("authority-controller"),
        install_helper_executable_digest=_digest("authority-install-helper"),
        ledger_identity_digest=_digest("authority-ledger"),
    )
    expected = sample.expected
    package_binding = protected.ProtectedPackageBinding(
        version="1.4.0",
        manifest_digest=expected.manifest_digest,
        portable_digest=expected.portable_digest,
        desktop_executable_digest=expected.desktop_executable_digest,
        backend_executable_digest=expected.backend_executable_digest,
        backend_tree_digest=expected.backend_tree_digest,
        runner_digest=expected.runner_digest,
        unity_package_digest=expected.unity_package_digest,
        packaged_unity_tool_tree_digest=expected.packaged_unity_tool_tree_digest,
        runtime_unity_tool_tree_digest=expected.runtime_unity_tool_tree_digest,
        unity_editor_digest=expected.unity_editor_digest,
        bridge_launcher_executable_digest=expected.bridge_launcher_executable_digest,
        bridge_listener_executable_digest=expected.bridge_listener_executable_digest,
        connector_digest=expected.connector_digest,
        server_digest=expected.server_digest,
        dependency_set_digest=expected.dependency_set_digest,
        runtime_binding_digest=expected.runtime_binding_digest,
    )
    row_binding = protected.ProtectedRowBinding(
        scenario_id=live.MODEL_SCENARIO_ID,
        primitive_id=live.MODEL_PRIMITIVE_ID,
        fixture_project_input_digest=expected.fixture_project_input_digest,
        project_binding_digest=PROJECT_DIGEST,
    )
    fixture = next(
        item
        for item in sample.fixtures.fixtures
        if item.scenario_id == row_binding.scenario_id
    )
    finalization = copy.deepcopy(sample.finalization)
    envelope = copy.deepcopy(sample.envelope)
    origin_ticket_digest = str(envelope["ticketDigest"])
    if legacy_v1:
        authority_ticket_digest = origin_ticket_digest
    else:
        authority_ticket_digest = (
            protected._projection_runtime_ticket_digest(
                authority_binding=authority_binding,
                signer_key_id=sample.trust_context.signer_key_id,
                request_id=request_id,
            )
            if request_id is not None
            else _digest("authority-runtime-ticket")
        )
        envelope["schema"] = origin.ORIGIN_ENVELOPE_SCHEMA_V2
        envelope["authorityTicketDigest"] = authority_ticket_digest
        _sign_envelope(envelope, sample.private_key)
    row: dict[str, object] = {
        "schema": protected.AUTHORITY_ROW_SCHEMA,
        "scenarioId": row_binding.scenario_id,
        "primitiveId": row_binding.primitive_id,
        "fixtureDescriptorDigest": fixture.descriptor_digest,
        "fixtureDigest": fixture.digest,
        "fixtureProjectInputDigest": row_binding.fixture_project_input_digest,
        "projectBindingDigest": row_binding.project_binding_digest,
        "finalization": finalization,
        "finalizationDigest": _digest_json(finalization),
        "originEnvelope": envelope,
        "originEnvelopeDigest": _digest_json(envelope),
    }
    inner_started_at = _parse_timestamp(finalization["attestation"]["startedAt"])
    origin_signed_at = _parse_timestamp(envelope["signedAt"])
    bundle_signed_at = origin_signed_at + timedelta(seconds=1)
    run_binding_digest = _digest("run-binding")
    prepared_receipt_digest = _digest("prepared-receipt")
    armed_receipt_digest = _digest("armed-receipt")
    policy_snapshot_digest = _digest("policy-snapshot")
    recovery_bundle_digest = _digest("recovery-bundle")
    predecessor_frame_digest = _digest("binary-predecessor-frame")
    terminal_frame_digest = _digest("binary-terminal-frame")
    anchor_record_digest = _digest("binary-anchor-record")
    readback: dict[str, object] = {
        "schema": protected.BINARY_LEDGER_READBACK_SCHEMA,
        "readbackKind": "heldAndReopenedStable",
        "authorityGenerationDigest": authority_binding.authority_generation_digest,
        "ledgerIdentityDigest": authority_binding.ledger_identity_digest,
        "ledgerFileDigest": _digest("ledger-file"),
        "anchorFileDigest": _digest("anchor-file"),
        "ledgerFileIdentityDigest": _digest("ledger-file-identity"),
        "anchorFileIdentityDigest": _digest("anchor-file-identity"),
        "ledgerLength": 1024,
        "anchorLength": 2048,
        "frameCount": 102,
        "activeTicketCount": 0,
        "latestFrameDigest": terminal_frame_digest,
        "anchorRecordDigest": anchor_record_digest,
        "terminalSequence": 101,
        "terminalFrameDigest": terminal_frame_digest,
        "terminalTicketDigest": authority_ticket_digest,
    }
    binary_terminal: dict[str, object] = {
        "schema": protected.BINARY_LEDGER_TERMINAL_SCHEMA,
        "event": "resultCommit",
        "authorityGenerationDigest": authority_binding.authority_generation_digest,
        "ledgerIdentityDigest": authority_binding.ledger_identity_digest,
        "predecessorSequence": 100,
        "terminalSequence": 101,
        "predecessorFrameDigest": predecessor_frame_digest,
        "terminalFrameDigest": terminal_frame_digest,
        "terminalTicketDigest": authority_ticket_digest,
        "terminalResultDigest": row["finalizationDigest"],
        "anchorSequence": 101,
        "anchorFrameDigest": terminal_frame_digest,
        "anchorTicketDigest": authority_ticket_digest,
        "runBindingDigest": run_binding_digest,
        "preparedReceiptDigest": prepared_receipt_digest,
        "armedReceiptDigest": armed_receipt_digest,
        "policySnapshotDigest": policy_snapshot_digest,
        "recoveryBundleDigest": recovery_bundle_digest,
        "runAdmissionDigest": _run_admission_digest(
            run_binding_digest,
            prepared_receipt_digest,
            armed_receipt_digest,
            policy_snapshot_digest,
            recovery_bundle_digest,
        ),
        "originEnvelopeDigest": row["originEnvelopeDigest"],
        "cleanupDigest": envelope["cleanupDigest"],
        "anchorRecordDigest": anchor_record_digest,
        "reopenReadback": readback,
        "reopenReadbackDigest": _digest_json(readback),
    }
    initial_receipt_digest = _digest("derived-receipt-start")
    receipt: dict[str, object] = {
        "schema": (
            protected.LEDGER_RECEIPT_SCHEMA_V1
            if legacy_v1
            else protected.LEDGER_RECEIPT_SCHEMA_V2
        ),
        "ordinal": 1,
        "previousReceiptDigest": initial_receipt_digest,
        "ticketDigest": authority_ticket_digest,
        "runId": envelope["ticket"]["runId"],
        "scenarioId": row_binding.scenario_id,
        "primitiveId": row_binding.primitive_id,
        "state": "completed",
        "resultDigest": row["finalizationDigest"],
        "originEnvelopeDigest": row["originEnvelopeDigest"],
        "cleanupDigest": envelope["cleanupDigest"],
        "issuedAt": envelope["ticket"]["issuedAt"],
        "consumedAt": _timestamp(inner_started_at - timedelta(microseconds=1)),
        "completedAt": _timestamp(origin_signed_at + timedelta(microseconds=1)),
        "binaryLedgerTerminal": binary_terminal,
        "binaryLedgerTerminalDigest": _digest_json(binary_terminal),
    }
    if not legacy_v1:
        receipt["originTicketDigest"] = origin_ticket_digest
    receipt["receiptDigest"] = _digest_json(receipt)
    ledger: dict[str, object] = {
        "schema": protected.LEDGER_SNAPSHOT_SCHEMA,
        "authorityGenerationDigest": authority_binding.authority_generation_digest,
        "ledgerIdentityDigest": authority_binding.ledger_identity_digest,
        "firstReceiptOrdinal": 1,
        "lastReceiptOrdinal": 1,
        "initialReceiptDigest": initial_receipt_digest,
        "terminalReceiptDigest": receipt["receiptDigest"],
        "receipts": [receipt],
    }
    raw_ledger = _canonical_bytes(ledger)
    authority_payload = authority_binding.to_payload()
    package_payload = package_binding.to_payload()
    bundle: dict[str, object] = {
        "schema": protected.AUTHORITY_BUNDLE_SCHEMA,
        "bundleId": "authority-source-contract-1",
        "proofAlgorithm": origin.ORIGIN_PROOF_ALGORITHM,
        "policyId": sample.trust_context.policy_id,
        "signerKeyId": sample.trust_context.signer_key_id,
        "authorityBinding": authority_payload,
        "authorityBindingDigest": _digest_json(authority_payload),
        "packageBinding": package_payload,
        "packageBindingDigest": _digest_json(package_payload),
        "fixtureSetDescriptorDigest": sample.fixtures.descriptor_digest,
        "fixtureSetDigest": sample.fixtures.digest,
        "ledgerSnapshotDigest": hashlib.sha256(raw_ledger).hexdigest(),
        "rows": [row],
        "signedAt": _timestamp(bundle_signed_at),
        "signature": "",
    }
    _sign_envelope(bundle, sample.private_key)
    return ProtectedSourceContractSample(
        signed=sample,
        authority_binding=authority_binding,
        package_binding=package_binding,
        row_binding=row_binding,
        bundle=bundle,
        ledger=ledger,
        raw_bundle=_canonical_bytes(bundle),
        raw_ledger=raw_ledger,
        origin_ticket_digest=origin_ticket_digest,
        authority_ticket_digest=authority_ticket_digest,
    )


def _protected_report(
    value: ProtectedSourceContractSample,
    *,
    raw_bundle: object | None = None,
    raw_ledger: object | None = None,
    trust_context: origin.OriginTrustContext | None = None,
    package_binding: protected.ProtectedPackageBinding | None = None,
    replay_guard: protected.ProtectedEvidenceReplayGuard | None = None,
) -> dict[str, object]:
    return protected.verify_and_project_protected_matrix(
        value.raw_bundle if raw_bundle is None else raw_bundle,
        value.raw_ledger if raw_ledger is None else raw_ledger,
        trust_context=value.signed.trust_context if trust_context is None else trust_context,
        authority_binding=value.authority_binding,
        package_binding=value.package_binding if package_binding is None else package_binding,
        fixtures=value.signed.fixtures,
        expected_rows=(value.row_binding,),
        replay_guard=(
            protected.ProtectedEvidenceReplayGuard()
            if replay_guard is None
            else replay_guard
        ),
        verified_at=value.signed.verified_at,
    )


def _assert_protected_blocked(report: Mapping[str, object], reason: str) -> None:
    assert report["ok"] is False
    runtime = report["runtimeBinding"]
    assert isinstance(runtime, Mapping)
    assert runtime["protectedAuthorityVerified"] is False
    assert runtime["liveRunnerAttested"] is False
    assert runtime["reasons"] == [reason]
    rows = report["rows"]
    assert isinstance(rows, list) and len(rows) == 6
    assert all(row["status"] == "blocked" for row in rows)
    assert all(row["reasons"] == [reason] for row in rows)


def _resign_protected_bundle(
    bundle: dict[str, object], private_key: ec.EllipticCurvePrivateKey
) -> bytes:
    _sign_envelope(bundle, private_key)
    return _canonical_bytes(bundle)


def _refresh_ledger_snapshot(
    ledger: dict[str, object], bundle: dict[str, object]
) -> bytes:
    receipts = ledger["receipts"]
    assert isinstance(receipts, list) and receipts
    previous_receipt = ledger["initialReceiptDigest"]
    previous_ordinal = ledger["firstReceiptOrdinal"]
    assert isinstance(previous_ordinal, int)
    previous_ordinal -= 1
    for receipt in receipts:
        assert isinstance(receipt, dict)
        previous_ordinal += 1
        receipt["ordinal"] = previous_ordinal
        receipt["previousReceiptDigest"] = previous_receipt
        unsigned = copy.deepcopy(receipt)
        unsigned.pop("receiptDigest", None)
        receipt["receiptDigest"] = _digest_json(unsigned)
        previous_receipt = receipt["receiptDigest"]
    ledger["lastReceiptOrdinal"] = previous_ordinal
    ledger["terminalReceiptDigest"] = previous_receipt
    raw = _canonical_bytes(ledger)
    bundle["ledgerSnapshotDigest"] = hashlib.sha256(raw).hexdigest()
    return raw


@pytest.fixture
def protected_sample(sample: SignedSample) -> ProtectedSourceContractSample:
    return _make_protected_source_contract_sample(sample)


def test_legacy_v1_protected_source_cannot_enter_the_raw_matrix_projection(
    sample: SignedSample,
) -> None:
    legacy = _make_protected_source_contract_sample(sample, legacy_v1=True)

    report = _protected_report(legacy)

    _assert_protected_blocked(
        report,
        "authority_projection_dual_ticket_v2_required",
    )


def test_source_contract_model_adapter_proves_projection_without_release_acceptance(
    protected_sample: ProtectedSourceContractSample,
) -> None:
    report = _protected_report(protected_sample)

    full_rows = [row for row in report["rows"] if row["status"] == "full"]
    assert [(row["scenarioId"], row["primitiveId"]) for row in full_rows] == [
        (live.MODEL_SCENARIO_ID, live.MODEL_PRIMITIVE_ID)
    ]
    assert report["ok"] is False
    assert report["summary"]["status"] == "partial"
    assert report["summary"]["fullRowCount"] == 1
    assert report["runtimeBinding"]["protectedAuthorityVerified"] is True
    assert report["runtimeBinding"]["liveRunnerAttested"] is True

    public_value = _verify(protected_sample.signed)
    legacy = live.build_live_matrix_report(protected_sample.signed.fixtures, public_value)
    assert legacy["runtimeBinding"]["liveRunnerAttested"] is False
    assert all(row["status"] == "blocked" for row in legacy["rows"])


@pytest.mark.parametrize("raw_value", [{}, True, object()])
def test_protected_entry_requires_raw_bundle_bytes(
    protected_sample: ProtectedSourceContractSample, raw_value: object
) -> None:
    report = _protected_report(protected_sample, raw_bundle=raw_value)
    _assert_protected_blocked(report, "authority_raw_bundle_required")


@pytest.mark.parametrize("raw_value", [{}, True, object()])
def test_protected_entry_requires_raw_ledger_bytes(
    protected_sample: ProtectedSourceContractSample, raw_value: object
) -> None:
    report = _protected_report(protected_sample, raw_ledger=raw_value)
    _assert_protected_blocked(report, "authority_raw_ledger_required")


def test_public_verified_value_cannot_enter_the_raw_protected_gate(
    protected_sample: ProtectedSourceContractSample,
) -> None:
    report = _protected_report(
        protected_sample,
        raw_bundle=_verify(protected_sample.signed),
    )
    _assert_protected_blocked(report, "authority_raw_bundle_required")


def test_duplicate_json_fields_are_blocked_before_projection(
    protected_sample: ProtectedSourceContractSample,
) -> None:
    report = _protected_report(
        protected_sample,
        raw_bundle=b'{"schema":"a","schema":"b"}',
    )
    _assert_protected_blocked(report, "authority_duplicate_json_field")


def test_noncanonical_raw_bytes_are_blocked(
    protected_sample: ProtectedSourceContractSample,
) -> None:
    pretty = json.dumps(protected_sample.bundle, ensure_ascii=True, indent=2).encode()
    report = _protected_report(protected_sample, raw_bundle=pretty)
    _assert_protected_blocked(report, "authority_bundle_not_canonical")


def test_non_finite_json_numbers_are_blocked(
    protected_sample: ProtectedSourceContractSample,
) -> None:
    report = _protected_report(
        protected_sample,
        raw_bundle=b'{"schema":NaN}',
    )
    _assert_protected_blocked(report, "authority_bundle_invalid")


def test_near_parser_limit_nesting_cannot_escape_the_public_entry(
    protected_sample: ProtectedSourceContractSample,
) -> None:
    depth = 900
    nested = b'{"a":' * depth + b"0" + b"}" * depth
    raw_bundle = b'{"a":' + nested + b"," + protected_sample.raw_bundle[1:]

    report = _protected_report(protected_sample, raw_bundle=raw_bundle)

    assert report["ok"] is False
    assert report["summary"]["fullRowCount"] == 0
    runtime = report["runtimeBinding"]
    assert isinstance(runtime, Mapping)
    assert runtime["protectedAuthorityVerified"] is False
    assert runtime["reasons"] in (
        ["authority_bundle_invalid"],
        ["authority_bundle_nesting_invalid"],
        ["authority_bundle_private_value"],
        ["authority_bundle_shape_invalid"],
    )


@pytest.mark.parametrize("mutation", ["duplicate", "noncanonical", "unknown"])
def test_raw_ledger_snapshot_requires_unique_canonical_exact_fields(
    protected_sample: ProtectedSourceContractSample, mutation: str
) -> None:
    if mutation == "duplicate":
        raw_ledger = b'{"schema":"a","schema":"b"}'
        reason = "authority_duplicate_json_field"
    elif mutation == "noncanonical":
        raw_ledger = json.dumps(
            protected_sample.ledger,
            ensure_ascii=True,
            indent=2,
        ).encode()
        reason = "authority_ledger_snapshot_not_canonical"
    else:
        ledger = copy.deepcopy(protected_sample.ledger)
        ledger["accepted"] = True
        raw_ledger = _canonical_bytes(ledger)
        bundle = copy.deepcopy(protected_sample.bundle)
        bundle["ledgerSnapshotDigest"] = hashlib.sha256(raw_ledger).hexdigest()
        raw_bundle = _resign_protected_bundle(
            bundle, protected_sample.signed.private_key
        )
        _assert_protected_blocked(
            _protected_report(
                protected_sample,
                raw_bundle=raw_bundle,
                raw_ledger=raw_ledger,
            ),
            "authority_ledger_snapshot_shape_invalid",
        )
        return
    _assert_protected_blocked(
        _protected_report(protected_sample, raw_ledger=raw_ledger),
        reason,
    )


@pytest.mark.parametrize(
    ("field", "value", "reason"),
    [
        ("originVerified", True, "authority_bundle_shape_invalid"),
        ("signerPublicKey", "report-key", "authority_bundle_shape_invalid"),
    ],
)
def test_unknown_fields_and_report_supplied_trust_are_blocked(
    protected_sample: ProtectedSourceContractSample,
    field: str,
    value: object,
    reason: str,
) -> None:
    bundle = copy.deepcopy(protected_sample.bundle)
    bundle[field] = value
    raw = _resign_protected_bundle(bundle, protected_sample.signed.private_key)
    _assert_protected_blocked(
        _protected_report(protected_sample, raw_bundle=raw), reason
    )


def test_external_install_bundle_schema_cannot_alias_run_evidence(
    protected_sample: ProtectedSourceContractSample,
) -> None:
    bundle = copy.deepcopy(protected_sample.bundle)
    bundle["schema"] = "vrcforge.primitive_evidence_authority_bundle.v1"
    raw = _resign_protected_bundle(bundle, protected_sample.signed.private_key)
    _assert_protected_blocked(
        _protected_report(protected_sample, raw_bundle=raw),
        "authority_bundle_schema_invalid",
    )


def test_wrong_outer_signer_is_blocked(
    protected_sample: ProtectedSourceContractSample,
) -> None:
    attacker = ec.generate_private_key(ec.SECP256R1())
    bundle = copy.deepcopy(protected_sample.bundle)
    bundle["signerKeyId"] = hashlib.sha256(_public_key_bytes(attacker)).hexdigest()
    raw = _resign_protected_bundle(bundle, attacker)
    _assert_protected_blocked(
        _protected_report(protected_sample, raw_bundle=raw),
        "authority_bundle_signer_mismatch",
    )


def test_revoked_outer_signer_is_blocked(
    protected_sample: ProtectedSourceContractSample,
) -> None:
    trust = copy.deepcopy(protected_sample.signed.trust_payload)
    trust["revokedSignerKeyIds"] = [protected_sample.signed.trust_context.signer_key_id]
    revoked = origin.parse_origin_trust_context(trust)
    _assert_protected_blocked(
        _protected_report(protected_sample, trust_context=revoked),
        "authority_bundle_signer_revoked",
    )


def test_outer_algorithm_downgrade_is_blocked_even_when_resigned(
    protected_sample: ProtectedSourceContractSample,
) -> None:
    bundle = copy.deepcopy(protected_sample.bundle)
    bundle["proofAlgorithm"] = "ecdsa-sha256-v0"
    raw = _resign_protected_bundle(bundle, protected_sample.signed.private_key)
    _assert_protected_blocked(
        _protected_report(protected_sample, raw_bundle=raw),
        "authority_bundle_algorithm_invalid",
    )


def test_protected_bindings_reject_all_zero_digests(
    protected_sample: ProtectedSourceContractSample,
) -> None:
    with pytest.raises(
        protected.ProtectedEvidenceError, match="authority_binding_invalid"
    ):
        replace(
            protected_sample.authority_binding,
            authority_generation_digest="0" * 64,
        )


@pytest.mark.parametrize(
    "signed_at",
    [
        "2026-07-24T00:00:03Z",
        "2026-07-24T00:00:03.000Z",
        "2026-07-24T00:00:03.0000000Z",
    ],
)
def test_outer_timestamp_requires_the_exact_cross_runtime_format(
    protected_sample: ProtectedSourceContractSample,
    signed_at: str,
) -> None:
    bundle = copy.deepcopy(protected_sample.bundle)
    bundle["signedAt"] = signed_at
    raw = _resign_protected_bundle(bundle, protected_sample.signed.private_key)
    _assert_protected_blocked(
        _protected_report(protected_sample, raw_bundle=raw),
        "authority_bundle_timestamp_invalid",
    )


def test_outer_high_s_signature_is_blocked(
    protected_sample: ProtectedSourceContractSample,
) -> None:
    bundle = copy.deepcopy(protected_sample.bundle)
    raw_signature = base64.urlsafe_b64decode(
        str(bundle["signature"])
        + "=" * ((4 - len(str(bundle["signature"])) % 4) % 4)
    )
    low_s = int.from_bytes(raw_signature[32:], "big")
    high_s = P256_ORDER - low_s
    bundle["signature"] = _base64url(
        raw_signature[:32] + high_s.to_bytes(32, "big")
    )
    _assert_protected_blocked(
        _protected_report(protected_sample, raw_bundle=_canonical_bytes(bundle)),
        "authority_bundle_signature_invalid",
    )


@pytest.mark.parametrize(
    "field",
    [
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
    ],
)
def test_any_unsigned_change_to_a_signed_bundle_field_is_blocked(
    protected_sample: ProtectedSourceContractSample, field: str
) -> None:
    bundle = copy.deepcopy(protected_sample.bundle)
    if field == "schema":
        bundle[field] = "vrcforge.primitive_basis_authority_evidence_bundle.v0"
    elif field in {"bundleId", "policyId"}:
        bundle[field] = f"tampered-{field}"
    elif field == "proofAlgorithm":
        bundle[field] = "ecdsa-sha256-v0"
    elif field == "signerKeyId":
        bundle[field] = _digest("tampered-signer")
    elif field == "authorityBinding":
        authority_binding = bundle[field]
        assert isinstance(authority_binding, dict)
        authority_binding["ledgerIdentityDigest"] = _digest("tampered-ledger")
    elif field == "packageBinding":
        package_binding = bundle[field]
        assert isinstance(package_binding, dict)
        package_binding["manifestDigest"] = _digest("tampered-manifest")
    elif field == "rows":
        rows = bundle[field]
        assert isinstance(rows, list) and isinstance(rows[0], dict)
        rows[0]["fixtureDigest"] = _digest("tampered-fixture")
    elif field == "signedAt":
        bundle[field] = _timestamp(
            _parse_timestamp(bundle[field]) + timedelta(microseconds=1)
        )
    else:
        bundle[field] = _digest(f"tampered:{field}")

    report = _protected_report(
        protected_sample,
        raw_bundle=_canonical_bytes(bundle),
    )
    assert report["ok"] is False
    assert report["summary"]["fullRowCount"] == 0
    assert report["runtimeBinding"]["protectedAuthorityVerified"] is False


@pytest.mark.parametrize(
    "field",
    [
        "schema",
        "policyId",
        "authorityGenerationDigest",
        "protectedManifestDigest",
        "installedLayoutDigest",
        "serviceExecutableDigest",
        "controllerExecutableDigest",
        "installHelperExecutableDigest",
        "ledgerIdentityDigest",
    ],
)
def test_any_authority_binding_drift_is_blocked_after_valid_signature(
    protected_sample: ProtectedSourceContractSample, field: str
) -> None:
    bundle = copy.deepcopy(protected_sample.bundle)
    authority_binding = bundle["authorityBinding"]
    assert isinstance(authority_binding, dict)
    if field == "schema":
        authority_binding[field] = "vrcforge.primitive_basis_authority_binding.v0"
    elif field == "policyId":
        authority_binding[field] = "other-policy"
    else:
        authority_binding[field] = _digest(f"drift:{field}")
    bundle["authorityBindingDigest"] = _digest_json(authority_binding)
    raw = _resign_protected_bundle(bundle, protected_sample.signed.private_key)
    _assert_protected_blocked(
        _protected_report(protected_sample, raw_bundle=raw),
        "authority_binding_mismatch",
    )


@pytest.mark.parametrize(
    "field",
    [
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
    ],
)
def test_any_package_binding_field_drift_is_blocked_after_valid_signature(
    protected_sample: ProtectedSourceContractSample, field: str
) -> None:
    bundle = copy.deepcopy(protected_sample.bundle)
    package = bundle["packageBinding"]
    assert isinstance(package, dict)
    if field == "schema":
        package[field] = "vrcforge.primitive_basis_package_binding.v0"
    elif field == "version":
        package[field] = "1.4.1"
    else:
        package[field] = _digest(f"drift:{field}")
    bundle["packageBindingDigest"] = _digest_json(package)
    raw = _resign_protected_bundle(bundle, protected_sample.signed.private_key)
    _assert_protected_blocked(
        _protected_report(protected_sample, raw_bundle=raw),
        "authority_package_binding_mismatch",
    )


@pytest.mark.parametrize(
    "field",
    [
        "fixtureDescriptorDigest",
        "fixtureDigest",
        "fixtureProjectInputDigest",
        "projectBindingDigest",
    ],
)
def test_any_fixture_or_project_digest_drift_is_blocked_after_valid_signature(
    protected_sample: ProtectedSourceContractSample, field: str
) -> None:
    bundle = copy.deepcopy(protected_sample.bundle)
    bundle["rows"][0][field] = _digest(f"drift:{field}")
    raw = _resign_protected_bundle(bundle, protected_sample.signed.private_key)
    _assert_protected_blocked(
        _protected_report(protected_sample, raw_bundle=raw),
        "authority_fixture_binding_mismatch",
    )


@pytest.mark.parametrize(
    "field",
    [
        "ticketDigest",
        "originTicketDigest",
        "resultDigest",
        "originEnvelopeDigest",
        "cleanupDigest",
    ],
)
def test_completed_ledger_receipt_binds_ticket_result_origin_and_cleanup(
    protected_sample: ProtectedSourceContractSample, field: str
) -> None:
    bundle = copy.deepcopy(protected_sample.bundle)
    ledger = copy.deepcopy(protected_sample.ledger)
    ledger["receipts"][0][field] = _digest(f"drift:{field}")
    raw_ledger = _refresh_ledger_snapshot(ledger, bundle)
    raw_bundle = _resign_protected_bundle(bundle, protected_sample.signed.private_key)
    _assert_protected_blocked(
        _protected_report(
            protected_sample,
            raw_bundle=raw_bundle,
            raw_ledger=raw_ledger,
        ),
        "authority_ledger_binding_mismatch",
    )


def test_v2_completed_receipt_requires_the_origin_ticket_field(
    protected_sample: ProtectedSourceContractSample,
) -> None:
    bundle = copy.deepcopy(protected_sample.bundle)
    ledger = copy.deepcopy(protected_sample.ledger)
    receipt = ledger["receipts"][0]
    assert isinstance(receipt, dict)
    receipt.pop("originTicketDigest")
    raw_ledger = _refresh_ledger_snapshot(ledger, bundle)
    raw_bundle = _resign_protected_bundle(
        bundle, protected_sample.signed.private_key
    )

    _assert_protected_blocked(
        _protected_report(
            protected_sample,
            raw_bundle=raw_bundle,
            raw_ledger=raw_ledger,
        ),
        "authority_ledger_receipt_invalid",
    )


def test_resigned_origin_authority_ticket_cannot_reframe_the_ledger_ticket(
    protected_sample: ProtectedSourceContractSample,
) -> None:
    bundle = copy.deepcopy(protected_sample.bundle)
    ledger = copy.deepcopy(protected_sample.ledger)
    row = bundle["rows"][0]
    assert isinstance(row, dict)
    envelope = row["originEnvelope"]
    assert isinstance(envelope, dict)
    envelope["authorityTicketDigest"] = _digest("resigned-attacker-ticket")
    _sign_envelope(envelope, protected_sample.signed.private_key)
    row["originEnvelopeDigest"] = _digest_json(envelope)

    receipt = ledger["receipts"][0]
    assert isinstance(receipt, dict)
    receipt["originEnvelopeDigest"] = row["originEnvelopeDigest"]
    terminal = receipt["binaryLedgerTerminal"]
    assert isinstance(terminal, dict)
    terminal["originEnvelopeDigest"] = row["originEnvelopeDigest"]
    receipt["binaryLedgerTerminalDigest"] = _digest_json(terminal)
    raw_ledger = _refresh_ledger_snapshot(ledger, bundle)
    raw_bundle = _resign_protected_bundle(
        bundle, protected_sample.signed.private_key
    )

    _assert_protected_blocked(
        _protected_report(
            protected_sample,
            raw_bundle=raw_bundle,
            raw_ledger=raw_ledger,
        ),
        "authority_ledger_binding_mismatch",
    )


def test_swapping_the_two_v2_ticket_fields_is_rejected_after_resigning(
    protected_sample: ProtectedSourceContractSample,
) -> None:
    bundle = copy.deepcopy(protected_sample.bundle)
    ledger = copy.deepcopy(protected_sample.ledger)
    receipt = ledger["receipts"][0]
    assert isinstance(receipt, dict)
    receipt["ticketDigest"], receipt["originTicketDigest"] = (
        receipt["originTicketDigest"],
        receipt["ticketDigest"],
    )
    terminal = receipt["binaryLedgerTerminal"]
    assert isinstance(terminal, dict)
    terminal["terminalTicketDigest"] = receipt["ticketDigest"]
    terminal["anchorTicketDigest"] = receipt["ticketDigest"]
    readback = terminal["reopenReadback"]
    assert isinstance(readback, dict)
    readback["terminalTicketDigest"] = receipt["ticketDigest"]
    terminal["reopenReadbackDigest"] = _digest_json(readback)
    receipt["binaryLedgerTerminalDigest"] = _digest_json(terminal)
    raw_ledger = _refresh_ledger_snapshot(ledger, bundle)
    raw_bundle = _resign_protected_bundle(
        bundle, protected_sample.signed.private_key
    )

    _assert_protected_blocked(
        _protected_report(
            protected_sample,
            raw_bundle=raw_bundle,
            raw_ledger=raw_ledger,
        ),
        "authority_ledger_binding_mismatch",
    )


def test_ledger_rollback_is_blocked_even_when_bundle_is_resigned(
    protected_sample: ProtectedSourceContractSample,
) -> None:
    bundle = copy.deepcopy(protected_sample.bundle)
    ledger = copy.deepcopy(protected_sample.ledger)
    ledger["terminalReceiptDigest"] = ledger["initialReceiptDigest"]
    raw_ledger = _canonical_bytes(ledger)
    bundle["ledgerSnapshotDigest"] = hashlib.sha256(raw_ledger).hexdigest()
    raw_bundle = _resign_protected_bundle(bundle, protected_sample.signed.private_key)
    _assert_protected_blocked(
        _protected_report(
            protected_sample,
            raw_bundle=raw_bundle,
            raw_ledger=raw_ledger,
        ),
        "authority_ledger_receipt_chain_invalid",
    )


@pytest.mark.parametrize("location", ["first", "last", "receipt"])
def test_derived_receipt_ordinals_are_limited_to_the_u64_domain(
    protected_sample: ProtectedSourceContractSample, location: str
) -> None:
    bundle = copy.deepcopy(protected_sample.bundle)
    ledger = copy.deepcopy(protected_sample.ledger)
    oversized = 1 << 64
    if location == "first":
        ledger["firstReceiptOrdinal"] = oversized
    elif location == "last":
        ledger["lastReceiptOrdinal"] = oversized
    else:
        receipts = ledger["receipts"]
        assert isinstance(receipts, list) and isinstance(receipts[0], dict)
        receipts[0]["ordinal"] = oversized
        unsigned = copy.deepcopy(receipts[0])
        unsigned.pop("receiptDigest", None)
        receipts[0]["receiptDigest"] = _digest_json(unsigned)
    raw_ledger = _canonical_bytes(ledger)
    bundle["ledgerSnapshotDigest"] = hashlib.sha256(raw_ledger).hexdigest()
    raw_bundle = _resign_protected_bundle(bundle, protected_sample.signed.private_key)

    _assert_protected_blocked(
        _protected_report(
            protected_sample,
            raw_bundle=raw_bundle,
            raw_ledger=raw_ledger,
        ),
        "authority_ledger_ordinal_invalid",
    )


def test_bundle_replay_is_blocked(
    protected_sample: ProtectedSourceContractSample,
) -> None:
    guard = protected.ProtectedEvidenceReplayGuard()
    first = _protected_report(protected_sample, replay_guard=guard)
    assert first["summary"]["fullRowCount"] == 1
    second = _protected_report(protected_sample, replay_guard=guard)
    _assert_protected_blocked(second, "authority_bundle_replayed")


def test_ticket_and_terminal_frame_cannot_be_reused_by_another_bundle(
    protected_sample: ProtectedSourceContractSample,
) -> None:
    guard = protected.ProtectedEvidenceReplayGuard()
    assert _protected_report(protected_sample, replay_guard=guard)["summary"][
        "fullRowCount"
    ] == 1
    bundle = copy.deepcopy(protected_sample.bundle)
    bundle["bundleId"] = "authority-source-contract-2"
    raw_bundle = _resign_protected_bundle(bundle, protected_sample.signed.private_key)
    report = _protected_report(
        protected_sample,
        raw_bundle=raw_bundle,
        replay_guard=guard,
    )
    _assert_protected_blocked(report, "authority_bundle_replayed")


@pytest.mark.parametrize("mutation", ["unknown", "duplicate"])
def test_unknown_or_duplicate_rows_block_the_whole_bundle(
    protected_sample: ProtectedSourceContractSample, mutation: str
) -> None:
    bundle = copy.deepcopy(protected_sample.bundle)
    if mutation == "unknown":
        bundle["rows"][0]["primitiveId"] = "unknown_primitive"
    else:
        bundle["rows"].append(copy.deepcopy(bundle["rows"][0]))
    raw = _resign_protected_bundle(bundle, protected_sample.signed.private_key)
    _assert_protected_blocked(
        _protected_report(protected_sample, raw_bundle=raw),
        "authority_row_set_invalid",
    )
