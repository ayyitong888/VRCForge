from __future__ import annotations

import base64
import copy
import hashlib
import hmac
import json
from dataclasses import dataclass, replace
from datetime import datetime, timedelta, timezone
import pytest
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import ec, utils

import primitive_basis_live_attestation as live
import primitive_basis_matrix as matrix
import primitive_basis_origin_attestation as origin


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
            "bridgePortReleased": True,
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
        "bridgePortReleased": True,
        "projectRemoved": True,
        "observedAt": _timestamp(BASE_TIME + timedelta(seconds=20)),
    }
    envelope: dict[str, object] = {
        "schema": origin.ORIGIN_ENVELOPE_SCHEMA,
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
