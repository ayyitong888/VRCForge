from __future__ import annotations

import copy
import hashlib
import json
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

import primitive_basis_live_attestation as live
import primitive_basis_matrix as matrix


FIXTURE_DIR = Path(__file__).parent / "fixtures" / "primitive_basis"
DIGEST = hashlib.sha256(b"fixture").hexdigest()
PROJECT_DIGEST = hashlib.sha256(b"project").hexdigest()


class TickClock:
    def __init__(self) -> None:
        self.value = datetime.now(timezone.utc) - timedelta(minutes=1)

    def __call__(self) -> datetime:
        self.value += timedelta(seconds=1)
        return self.value


def bootstrap() -> live.LiveBootstrap:
    return live.LiveBootstrap(
        key=b"k" * 32,
        challenge=b"c" * 32,
        runtime_binding_digest=hashlib.sha256(b"runtime").hexdigest(),
        desktop_executable_digest=hashlib.sha256(b"desktop").hexdigest(),
        backend_executable_digest=hashlib.sha256(b"backend").hexdigest(),
        runner_digest=hashlib.sha256(b"runner").hexdigest(),
        unity_package_digest=hashlib.sha256(b"unity-package").hexdigest(),
        unity_editor_digest=hashlib.sha256(b"unity-editor").hexdigest(),
        fixture_project_input_digest=hashlib.sha256(b"fixture-project").hexdigest(),
        fixture_set_descriptor_digest=hashlib.sha256(b"fixture-set").hexdigest(),
        fixture_descriptor_digest=hashlib.sha256(b"fixture-descriptor").hexdigest(),
    )


def facts(phase: str) -> dict[str, object]:
    baseline = hashlib.sha256(b"baseline").hexdigest()
    inventory = hashlib.sha256(b"inventory").hexdigest()
    applied = hashlib.sha256(b"applied").hexdigest()
    arguments = hashlib.sha256(b"arguments").hexdigest()
    operation = hashlib.sha256(b"operation").hexdigest()
    report = hashlib.sha256(b"report").hexdigest()
    fixture_project = hashlib.sha256(b"fixture-project").hexdigest()
    unity_process = hashlib.sha256(b"unity-process").hexdigest()
    request_id = "request-apply"
    approval_id = "approval-apply"
    checkpoint_id = "checkpoint-apply"
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
            "reportDigest": report,
        },
        "restore_request": {
            "requestId": "request-restore",
            "targetTool": live.RESTORE_TARGET_TOOL,
            "checkpointId": checkpoint_id,
            "projectBindingDigest": PROJECT_DIGEST,
            "argumentsDigest": hashlib.sha256(b"restore-arguments").hexdigest(),
            "state": "approval_pending",
        },
        "restore_approval": {
            "requestId": "request-restore",
            "approvalId": "approval-restore",
            "targetTool": live.RESTORE_TARGET_TOOL,
            "checkpointId": checkpoint_id,
            "projectBindingDigest": PROJECT_DIGEST,
            "argumentsDigest": hashlib.sha256(b"restore-arguments").hexdigest(),
            "pendingObserved": True,
            "approved": True,
        },
        "restore_execution": {
            "executionId": "execution-restore",
            "approvalId": "approval-restore",
            "targetTool": live.RESTORE_TARGET_TOOL,
            "checkpointId": checkpoint_id,
            "projectBindingDigest": PROJECT_DIGEST,
            "argumentsDigest": hashlib.sha256(b"restore-arguments").hexdigest(),
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


def finalization() -> tuple[live.LiveBootstrap, dict[str, object], datetime]:
    proof = bootstrap()
    clock = TickClock()
    session = live.PrimitiveBasisLiveSession(proof, now=clock)
    session.begin(fixture_digest=DIGEST, project_binding_digest=PROJECT_DIGEST)
    for phase in live.LIVE_PHASES:
        session.record(
            phase,
            facts(phase),
            authoritative_event={"phase": phase, "source": "backend"},
        )
    payload = session.finalize()
    return proof, payload, clock.value + timedelta(seconds=1)


def test_v3_finalization_is_one_shot_and_verifies_transaction_chain() -> None:
    proof, payload, verified_at = finalization()

    verified = live.verify_live_finalization(
        payload,
        bootstrap=proof,
        fixture_digest=DIGEST,
        project_binding_digest=PROJECT_DIGEST,
        verified_at=verified_at,
    )

    assert verified.scenario_id == live.MODEL_SCENARIO_ID
    assert verified.primitive_id == live.MODEL_PRIMITIVE_ID
    assert verified.origin_verified is False
    assert len(verified.receipts) == 13
    serialized = json.dumps(payload)
    assert proof.key.hex() not in serialized
    assert proof.challenge.hex() not in serialized


def test_self_mac_finalization_expires_after_freshness_window() -> None:
    proof, payload, verified_at = finalization()

    with pytest.raises(live.LiveAttestationError, match="stale"):
        live.verify_live_finalization(
            payload,
            bootstrap=proof,
            fixture_digest=DIGEST,
            project_binding_digest=PROJECT_DIGEST,
            verified_at=verified_at + timedelta(hours=25),
        )


def test_v1_receipts_remain_permanently_blocked() -> None:
    fixtures = matrix.load_fixture_set(FIXTURE_DIR)
    report = matrix.build_report(
        fixtures,
        {"schema": matrix.EVIDENCE_SCHEMA, "runId": "v1-cannot-upgrade", "rows": []},
    )

    assert report["runtimeBinding"]["liveRunnerAttested"] is False
    assert report["summary"]["fullRowCount"] == 0
    assert all("live_runner_attestation_missing" in row["reasons"] for row in report["rows"])


@pytest.mark.parametrize(
    ("mutate", "message"),
    [
        (
            lambda payload: payload["attestation"].update(runtimeBindingDigest="0" * 64),
            "binding mismatch",
        ),
        (
            lambda payload: payload["evidence"]["rows"][0]["receipts"][5]["facts"].update(
                checkpointId="checkpoint-other"
            ),
            "checkpoint continuity",
        ),
        (
            lambda payload: payload["attestation"].update(proof="0" * 64),
            "proof mismatch",
        ),
    ],
)
def test_cross_binding_tampering_is_rejected(mutate, message: str) -> None:
    proof, payload, verified_at = finalization()
    mutate(payload)

    with pytest.raises(live.LiveAttestationError, match=message):
        live.verify_live_finalization(
            payload,
            bootstrap=proof,
            fixture_digest=DIGEST,
            project_binding_digest=PROJECT_DIGEST,
            verified_at=verified_at,
        )


def test_restore_of_another_checkpoint_is_rejected_before_finalization() -> None:
    proof = bootstrap()
    clock = TickClock()
    session = live.PrimitiveBasisLiveSession(proof, now=clock)
    session.begin(fixture_digest=DIGEST, project_binding_digest=PROJECT_DIGEST)
    for phase in live.LIVE_PHASES:
        phase_facts = facts(phase)
        if phase in {
            "restore_request",
            "restore_approval",
            "restore_execution",
            "baseline_comparison",
            "residue",
        }:
            phase_facts["checkpointId"] = "checkpoint-other"
        session.record(
            phase,
            phase_facts,
            authoritative_event={"phase": phase, "source": "backend"},
        )

    with pytest.raises(live.LiveAttestationError, match="another checkpoint"):
        session.finalize()


def test_nested_request_cannot_escape_the_receipt_project_binding() -> None:
    proof = bootstrap()
    clock = TickClock()
    session = live.PrimitiveBasisLiveSession(proof, now=clock)
    session.begin(fixture_digest=DIGEST, project_binding_digest=PROJECT_DIGEST)
    for phase in live.LIVE_PHASES:
        phase_facts = facts(phase)
        if phase in {
            "request",
            "approval",
            "checkpoint",
            "apply",
            "restore_request",
            "restore_approval",
            "restore_execution",
        }:
            phase_facts["projectBindingDigest"] = "0" * 64
        session.record(
            phase,
            phase_facts,
            authoritative_event={"phase": phase, "source": "backend"},
        )

    with pytest.raises(live.LiveAttestationError, match="receipt project binding"):
        session.finalize()


def test_challenge_cannot_restart_or_change_after_finalize() -> None:
    proof = bootstrap()
    clock = TickClock()
    session = live.PrimitiveBasisLiveSession(proof, now=clock)
    session.begin(fixture_digest=DIGEST, project_binding_digest=PROJECT_DIGEST)
    with pytest.raises(live.LiveAttestationError, match="started twice"):
        session.begin(fixture_digest=DIGEST, project_binding_digest=PROJECT_DIGEST)
    for phase in live.LIVE_PHASES:
        session.record(
            phase,
            facts(phase),
            authoritative_event={"phase": phase, "source": "backend"},
        )
    first = session.finalize()
    assert session.finalize() == first
    with pytest.raises(live.LiveAttestationError, match="not running"):
        session.record(
            "detect",
            facts("detect"),
            authoritative_event={"phase": "detect"},
        )


def test_bootstrap_pipe_frame_is_exact_and_rejects_trailing_bytes() -> None:
    proof = bootstrap()
    encoded = live.encode_bootstrap_frame(proof)
    decoded = live.read_bootstrap_frame(__import__("io").BytesIO(encoded))
    assert decoded == proof

    with pytest.raises(live.LiveAttestationError, match="frame is invalid"):
        live.read_bootstrap_frame(__import__("io").BytesIO(encoded + b"x"))


def test_packaged_bootstrap_requires_frozen_exact_backend_bytes(tmp_path: Path) -> None:
    backend = tmp_path / "vrcforge_backend.exe"
    backend.write_bytes(b"packaged backend")
    proof = bootstrap()
    proof = live.LiveBootstrap(
        **{
            **proof.__dict__,
            "backend_executable_digest": hashlib.sha256(backend.read_bytes()).hexdigest(),
        }
    )

    session = live.load_packaged_live_session_from_stdin(
        environ={live.LIVE_STDIN_ENV: "1"},
        stream=__import__("io").BytesIO(live.encode_bootstrap_frame(proof)),
        executable_path=backend,
        frozen=True,
        now=TickClock(),
    )
    assert session is not None
    assert session.state == "issued"

    with pytest.raises(live.LiveAttestationError, match="frozen runtime"):
        live.load_packaged_live_session_from_stdin(
            environ={live.LIVE_STDIN_ENV: "1"},
            stream=__import__("io").BytesIO(live.encode_bootstrap_frame(proof)),
            executable_path=backend,
            frozen=False,
        )
    backend.write_bytes(b"changed")
    with pytest.raises(live.LiveAttestationError, match="backend digest mismatch"):
        live.load_packaged_live_session_from_stdin(
            environ={live.LIVE_STDIN_ENV: "1"},
            stream=__import__("io").BytesIO(live.encode_bootstrap_frame(proof)),
            executable_path=backend,
            frozen=True,
        )


def test_finalization_rejects_private_authoritative_event() -> None:
    proof = bootstrap()
    session = live.PrimitiveBasisLiveSession(proof, now=TickClock())
    session.begin(fixture_digest=DIGEST, project_binding_digest=PROJECT_DIGEST)
    with pytest.raises(live.LiveAttestationError, match="private live evidence"):
        session.record(
            "detect",
            facts("detect"),
            authoritative_event={"path": r"C:\\Users\\example\\private"},
        )


def test_receipt_reordering_is_rejected_even_with_recomputed_outer_proof() -> None:
    proof, payload, verified_at = finalization()
    receipts = payload["evidence"]["rows"][0]["receipts"]
    receipts[0], receipts[1] = receipts[1], receipts[0]
    unsigned = copy.deepcopy(payload["attestation"])
    unsigned.pop("proof")
    payload["attestation"]["proof"] = __import__("hmac").new(
        proof.key,
        json.dumps(unsigned, sort_keys=True, separators=(",", ":")).encode(),
        hashlib.sha256,
    ).hexdigest()

    with pytest.raises(live.LiveAttestationError, match="receipt binding mismatch"):
        live.verify_live_finalization(
            payload,
            bootstrap=proof,
            fixture_digest=DIGEST,
            project_binding_digest=PROJECT_DIGEST,
            verified_at=verified_at,
        )
