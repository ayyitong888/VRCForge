from __future__ import annotations

import base64
import hashlib
import json
import os
import re
import stat
import threading
from dataclasses import dataclass, replace
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Mapping

from cryptography.exceptions import InvalidSignature
from cryptography.hazmat.primitives import hashes
from cryptography.hazmat.primitives.asymmetric import ec, utils

from diagnostic_privacy import redact_public_evidence
from primitive_basis_live_attestation import (
    LiveAttestationError,
    LivePublicBinding,
    VerifiedLiveRun,
    verify_origin_bound_live_finalization,
)


ORIGIN_TRUST_SCHEMA = "vrcforge.primitive_basis_origin_trust.v1"
ORIGIN_TICKET_SCHEMA = "vrcforge.primitive_basis_origin_ticket.v1"
ORIGIN_ENVELOPE_SCHEMA = "vrcforge.primitive_basis_live_origin.v1"
ORIGIN_PROOF_ALGORITHM = "ecdsa-p256-sha256-raw-v1"
ORIGIN_TRUST_KIND = "pinned_external_supervisor"

_DIGEST_RE = re.compile(r"[0-9a-f]{64}")
_SAFE_ID_RE = re.compile(r"[A-Za-z0-9][A-Za-z0-9._-]{0,127}")
_BASE64URL_RE = re.compile(r"[A-Za-z0-9_-]+")
_MAX_CLOCK_SKEW = timedelta(minutes=5)
_MAX_TICKET_LIFETIME = timedelta(hours=2)
_MAX_TRUST_FILE_SIZE = 64 * 1024
_PROCESS_ROLES = (
    "attestor",
    "desktop",
    "backend",
    "unity",
    "bridge_launcher",
    "bridge_listener",
)
_NETWORK_ROLES = ("app", "bridge")
_P256_ORDER = int(
    "ffffffff00000000ffffffffffffffffbce6faada7179e84f3b9cac2fc632551", 16
)

_TRUST_FIELDS = {
    "schema",
    "policyId",
    "attestorExecutableDigest",
    "signerKeyId",
    "signerPublicKey",
    "revokedSignerKeyIds",
    "notBefore",
    "notAfter",
}
_TICKET_BINDING_FIELDS = (
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
    "fixtureSetDescriptorDigest",
    "fixtureDescriptorDigest",
    "fixtureProjectInputDigest",
    "fixtureDigest",
    "runtimeBindingDigest",
)
_TICKET_FIELDS = {
    "schema",
    "policyId",
    "ticketId",
    "runId",
    "challengeDigest",
    "issuedAt",
    "expiresAt",
    "attestorExecutableDigest",
    *_TICKET_BINDING_FIELDS,
}
_PROCESS_FIELDS = {
    "role",
    "pid",
    "parentPid",
    "supervisorPid",
    "startedAt",
    "executableDigest",
    "identityDigest",
}
_NETWORK_BINDING_FIELDS = {
    "role",
    "protocol",
    "localAddress",
    "localPort",
    "ownerPid",
    "ownerIdentityDigest",
    "state",
    "observedAt",
}
_CLEANUP_FIELDS = {
    "desktopExited",
    "backendExited",
    "unityExited",
    "bridgeLauncherExited",
    "bridgeListenerExited",
    "appPortReleased",
    "bridgePortReleased",
    "projectRemoved",
    "observedAt",
}
_ENVELOPE_FIELDS = {
    "schema",
    "proofAlgorithm",
    "originTrust",
    "signerKeyId",
    "attestorExecutableDigest",
    "ticket",
    "ticketDigest",
    "finalizationDigest",
    "projectBindingDigest",
    "processGraph",
    "processGraphDigest",
    "networkBindings",
    "networkBindingsDigest",
    "cleanup",
    "cleanupDigest",
    "signedAt",
    "signature",
}


@dataclass(frozen=True)
class OriginTrustContext:
    policy_id: str
    attestor_executable_digest: str
    signer_key_id: str
    signer_public_key: bytes
    revoked_signer_key_ids: frozenset[str]
    not_before: datetime
    not_after: datetime


@dataclass(frozen=True)
class OriginExpectedBinding:
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
    fixture_set_descriptor_digest: str
    fixture_descriptor_digest: str
    fixture_project_input_digest: str
    fixture_digest: str
    runtime_binding_digest: str

    def __post_init__(self) -> None:
        for field_name in self.__dataclass_fields__:
            _require_digest(getattr(self, field_name), field_name)

    def ticket_values(self) -> dict[str, str]:
        return {
            field_name: getattr(self, _camel_to_snake(field_name))
            for field_name in _TICKET_BINDING_FIELDS
        }


class OriginReplayGuard:
    """One-process replay guard for a live attestor verification session."""

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._consumed: set[str] = set()

    def consume(self, ticket_digest: str) -> None:
        digest = _require_digest(ticket_digest, "origin ticket digest")
        with self._lock:
            if digest in self._consumed:
                raise LiveAttestationError("origin ticket was replayed")
            self._consumed.add(digest)


def load_origin_trust_context(path: Path | str) -> OriginTrustContext:
    trust_path = Path(path).expanduser().resolve(strict=True)
    raw = _stable_read(trust_path, _MAX_TRUST_FILE_SIZE)
    try:
        value = json.loads(raw.decode("utf-8-sig"))
    except (UnicodeDecodeError, json.JSONDecodeError, RecursionError, ValueError) as exc:
        raise LiveAttestationError("origin trust context is invalid") from exc
    if not isinstance(value, Mapping):
        raise LiveAttestationError("origin trust context must be an object")
    return parse_origin_trust_context(value)


def parse_origin_trust_context(value: Mapping[str, Any]) -> OriginTrustContext:
    _require_exact_fields(value, _TRUST_FIELDS, "origin trust context")
    if value.get("schema") != ORIGIN_TRUST_SCHEMA:
        raise LiveAttestationError("origin trust schema mismatch")
    policy_id = _require_safe_id(value.get("policyId"), "origin policy")
    attestor_digest = _require_digest(
        value.get("attestorExecutableDigest"), "attestor executable digest"
    )
    signer_key_id = _require_digest(value.get("signerKeyId"), "origin signer key id")
    public_key = _decode_base64url(
        value.get("signerPublicKey"), expected_size=65, label="origin signer public key"
    )
    if public_key[0] != 0x04:
        raise LiveAttestationError("origin signer public key encoding is invalid")
    try:
        ec.EllipticCurvePublicKey.from_encoded_point(ec.SECP256R1(), public_key)
    except ValueError as exc:
        raise LiveAttestationError("origin signer public key is invalid") from exc
    if hashlib.sha256(public_key).hexdigest() != signer_key_id:
        raise LiveAttestationError("origin signer key id mismatch")
    raw_revoked = value.get("revokedSignerKeyIds")
    if not isinstance(raw_revoked, list):
        raise LiveAttestationError("origin revocation list is invalid")
    revoked = frozenset(
        _require_digest(item, "revoked origin signer key id") for item in raw_revoked
    )
    if len(revoked) != len(raw_revoked):
        raise LiveAttestationError("origin revocation list contains duplicates")
    not_before = _require_timestamp(value.get("notBefore"), "origin trust not-before")
    not_after = _require_timestamp(value.get("notAfter"), "origin trust not-after")
    if not_before >= not_after:
        raise LiveAttestationError("origin trust validity window is invalid")
    return OriginTrustContext(
        policy_id=policy_id,
        attestor_executable_digest=attestor_digest,
        signer_key_id=signer_key_id,
        signer_public_key=public_key,
        revoked_signer_key_ids=revoked,
        not_before=not_before,
        not_after=not_after,
    )


def verify_trusted_live_origin(
    finalization: Mapping[str, Any],
    envelope: Mapping[str, Any],
    *,
    trust_context: OriginTrustContext,
    expected: OriginExpectedBinding,
    project_binding_digest: str,
    verified_at: datetime | None = None,
    replay_guard: OriginReplayGuard | None = None,
) -> VerifiedLiveRun:
    if not isinstance(trust_context, OriginTrustContext):
        raise LiveAttestationError("origin trust context type is invalid")
    if not isinstance(expected, OriginExpectedBinding):
        raise LiveAttestationError("origin expected binding type is invalid")
    if not isinstance(finalization, Mapping) or not isinstance(envelope, Mapping):
        raise LiveAttestationError("origin evidence must be an object")
    _require_public_safe(finalization)
    _require_public_safe(envelope)
    _require_exact_fields(envelope, _ENVELOPE_FIELDS, "origin envelope")
    if envelope.get("schema") != ORIGIN_ENVELOPE_SCHEMA:
        raise LiveAttestationError("origin envelope schema mismatch")
    if envelope.get("proofAlgorithm") != ORIGIN_PROOF_ALGORITHM:
        raise LiveAttestationError("origin proof algorithm mismatch")
    if envelope.get("originTrust") != ORIGIN_TRUST_KIND:
        raise LiveAttestationError("origin trust kind mismatch")
    if envelope.get("signerKeyId") != trust_context.signer_key_id:
        raise LiveAttestationError("origin signer is not pinned")
    if trust_context.signer_key_id in trust_context.revoked_signer_key_ids:
        raise LiveAttestationError("origin signer is revoked")
    if envelope.get("attestorExecutableDigest") != trust_context.attestor_executable_digest:
        raise LiveAttestationError("origin attestor executable is not pinned")

    now = _utc_now(verified_at or datetime.now(timezone.utc))
    signed_at = _require_timestamp(envelope.get("signedAt"), "origin signed-at")
    if not (
        trust_context.not_before <= signed_at <= trust_context.not_after
        and trust_context.not_before <= now <= trust_context.not_after + _MAX_CLOCK_SKEW
    ):
        raise LiveAttestationError("origin trust context is outside its validity window")
    if signed_at > now + _MAX_CLOCK_SKEW:
        raise LiveAttestationError("origin signature is from the future")

    ticket = envelope.get("ticket")
    if not isinstance(ticket, Mapping):
        raise LiveAttestationError("origin ticket is invalid")
    _validate_ticket(
        ticket,
        trust_context=trust_context,
        expected=expected,
        signed_at=signed_at,
        verified_at=now,
    )
    ticket_digest = _digest_json(ticket)
    if envelope.get("ticketDigest") != ticket_digest:
        raise LiveAttestationError("origin ticket digest mismatch")
    if envelope.get("finalizationDigest") != _digest_json(finalization):
        raise LiveAttestationError("origin finalization digest mismatch")
    expected_project = _require_digest(
        project_binding_digest, "origin project binding digest"
    )
    if envelope.get("projectBindingDigest") != expected_project:
        raise LiveAttestationError("origin project binding mismatch")

    issued_at = _require_timestamp(ticket.get("issuedAt"), "origin ticket issued-at")
    expires_at = _require_timestamp(ticket.get("expiresAt"), "origin ticket expiry")
    inner_started_at, inner_finished_at, inner_finalized_at = _inner_run_window(
        finalization
    )
    if not (
        issued_at
        <= inner_started_at
        <= inner_finished_at
        <= inner_finalized_at
        <= signed_at
        <= expires_at
    ):
        raise LiveAttestationError("origin inner run escaped the ticket window")
    process_graph = envelope.get("processGraph")
    process_digest, process_identities, process_started_at = _validate_process_graph(
        process_graph,
        trust_context=trust_context,
        expected=expected,
        issued_at=issued_at,
        signed_at=signed_at,
    )
    if envelope.get("processGraphDigest") != process_digest:
        raise LiveAttestationError("origin process graph digest mismatch")
    if inner_started_at < max(
        process_started_at["backend"],
        process_started_at["bridge_listener"],
    ):
        raise LiveAttestationError("origin inner run predates required process readiness")
    network_digest, network_observed_at = _validate_network_bindings(
        envelope.get("networkBindings"),
        process_identities=process_identities,
        issued_at=issued_at,
        latest_allowed=inner_started_at,
    )
    if envelope.get("networkBindingsDigest") != network_digest:
        raise LiveAttestationError("origin network binding digest mismatch")
    cleanup = envelope.get("cleanup")
    cleanup_digest = _validate_cleanup(
        cleanup,
        signed_at=signed_at,
        earliest_observation=max(network_observed_at, inner_finalized_at),
    )
    if envelope.get("cleanupDigest") != cleanup_digest:
        raise LiveAttestationError("origin cleanup digest mismatch")

    signature = _decode_base64url(
        envelope.get("signature"), expected_size=64, label="origin signature"
    )
    unsigned = dict(envelope)
    unsigned.pop("signature", None)
    public_key = ec.EllipticCurvePublicKey.from_encoded_point(
        ec.SECP256R1(), trust_context.signer_public_key
    )
    raw_r = int.from_bytes(signature[:32], "big")
    raw_s = int.from_bytes(signature[32:], "big")
    if (
        raw_r <= 0
        or raw_r >= _P256_ORDER
        or raw_s <= 0
        or raw_s > _P256_ORDER // 2
    ):
        raise LiveAttestationError("origin signature is not canonical")
    der_signature = utils.encode_dss_signature(raw_r, raw_s)
    try:
        public_key.verify(
            der_signature,
            _canonical_bytes(unsigned),
            ec.ECDSA(hashes.SHA256()),
        )
    except InvalidSignature as exc:
        raise LiveAttestationError("origin signature mismatch") from exc

    binding = LivePublicBinding(
        run_id=str(ticket.get("runId") or ""),
        challenge_digest=str(ticket.get("challengeDigest") or ""),
        runtime_binding_digest=expected.runtime_binding_digest,
        desktop_executable_digest=expected.desktop_executable_digest,
        backend_executable_digest=expected.backend_executable_digest,
        runner_digest=expected.runner_digest,
        unity_package_digest=expected.unity_package_digest,
        unity_editor_digest=expected.unity_editor_digest,
        fixture_project_input_digest=expected.fixture_project_input_digest,
        fixture_set_descriptor_digest=expected.fixture_set_descriptor_digest,
        fixture_descriptor_digest=expected.fixture_descriptor_digest,
        origin_ticket_digest=ticket_digest,
    )
    inner = verify_origin_bound_live_finalization(
        finalization,
        binding=binding,
        fixture_digest=expected.fixture_digest,
        project_binding_digest=expected_project,
        verified_at=now,
    )
    if replay_guard is not None:
        replay_guard.consume(ticket_digest)
    inner_digest = inner.attestation_digest
    return replace(
        inner,
        origin_verified=True,
        attestation_digest=_digest_json(envelope),
        inner_attestation_digest=inner_digest,
        origin_signer_key_id=trust_context.signer_key_id,
        origin_ticket_digest=ticket_digest,
        origin_process_graph_digest=process_digest,
        origin_network_binding_digest=network_digest,
        origin_cleanup_digest=cleanup_digest,
    )


def _validate_ticket(
    ticket: Mapping[str, Any],
    *,
    trust_context: OriginTrustContext,
    expected: OriginExpectedBinding,
    signed_at: datetime,
    verified_at: datetime,
) -> None:
    _require_exact_fields(ticket, _TICKET_FIELDS, "origin ticket")
    if ticket.get("schema") != ORIGIN_TICKET_SCHEMA:
        raise LiveAttestationError("origin ticket schema mismatch")
    if ticket.get("policyId") != trust_context.policy_id:
        raise LiveAttestationError("origin ticket policy mismatch")
    _require_safe_id(ticket.get("ticketId"), "origin ticket")
    run_id = _require_safe_id(ticket.get("runId"), "origin run")
    challenge_digest = _require_digest(
        ticket.get("challengeDigest"), "origin challenge digest"
    )
    if run_id != f"primitive-live-{challenge_digest[:32]}":
        raise LiveAttestationError("origin run and challenge binding mismatch")
    if ticket.get("attestorExecutableDigest") != trust_context.attestor_executable_digest:
        raise LiveAttestationError("origin ticket attestor mismatch")
    for field_name, expected_value in expected.ticket_values().items():
        if ticket.get(field_name) != expected_value:
            raise LiveAttestationError(f"origin ticket {field_name} mismatch")
    issued_at = _require_timestamp(ticket.get("issuedAt"), "origin ticket issued-at")
    expires_at = _require_timestamp(ticket.get("expiresAt"), "origin ticket expiry")
    if not (
        issued_at < expires_at
        and expires_at - issued_at <= _MAX_TICKET_LIFETIME
        and issued_at <= signed_at <= expires_at
        and issued_at <= verified_at <= expires_at + _MAX_CLOCK_SKEW
    ):
        raise LiveAttestationError("origin ticket time window is invalid")


def _inner_run_window(
    finalization: Mapping[str, Any],
) -> tuple[datetime, datetime, datetime]:
    attestation = finalization.get("attestation")
    if not isinstance(attestation, Mapping):
        raise LiveAttestationError("origin inner attestation is invalid")
    return (
        _require_timestamp(attestation.get("startedAt"), "origin inner started-at"),
        _require_timestamp(attestation.get("finishedAt"), "origin inner finished-at"),
        _require_timestamp(
            attestation.get("finalizedAt"), "origin inner finalized-at"
        ),
    )


def _validate_process_graph(
    value: Any,
    *,
    trust_context: OriginTrustContext,
    expected: OriginExpectedBinding,
    issued_at: datetime,
    signed_at: datetime,
) -> tuple[
    str,
    dict[str, dict[str, Any]],
    dict[str, datetime],
]:
    if not isinstance(value, list) or len(value) != len(_PROCESS_ROLES):
        raise LiveAttestationError("origin process graph is incomplete")
    expected_digests = {
        "attestor": trust_context.attestor_executable_digest,
        "desktop": expected.desktop_executable_digest,
        "backend": expected.backend_executable_digest,
        "unity": expected.unity_editor_digest,
        "bridge_launcher": expected.bridge_launcher_executable_digest,
        "bridge_listener": expected.bridge_listener_executable_digest,
    }
    rows: list[dict[str, Any]] = []
    pids: set[int] = set()
    by_role: dict[str, dict[str, Any]] = {}
    started_by_role: dict[str, datetime] = {}
    for index, item in enumerate(value):
        if not isinstance(item, Mapping):
            raise LiveAttestationError("origin process entry is invalid")
        _require_exact_fields(item, _PROCESS_FIELDS, "origin process entry")
        role = str(item.get("role") or "")
        if index >= len(_PROCESS_ROLES) or role != _PROCESS_ROLES[index]:
            raise LiveAttestationError("origin process role order is invalid")
        pid = item.get("pid")
        parent_pid = item.get("parentPid")
        supervisor_pid = item.get("supervisorPid")
        if (
            not isinstance(pid, int)
            or isinstance(pid, bool)
            or pid <= 0
            or not isinstance(parent_pid, int)
            or isinstance(parent_pid, bool)
            or parent_pid < 0
            or not isinstance(supervisor_pid, int)
            or isinstance(supervisor_pid, bool)
            or supervisor_pid < 0
            or pid in pids
        ):
            raise LiveAttestationError("origin process identity is invalid")
        pids.add(pid)
        started_at = _require_timestamp(item.get("startedAt"), "origin process start")
        earliest_start = (
            issued_at - _MAX_CLOCK_SKEW if role == "attestor" else issued_at
        )
        if started_at < earliest_start or started_at > signed_at:
            raise LiveAttestationError("origin process escaped the ticket window")
        if item.get("executableDigest") != expected_digests[role]:
            raise LiveAttestationError("origin process executable mismatch")
        unsigned = dict(item)
        identity_digest = unsigned.pop("identityDigest", None)
        if identity_digest != _digest_json(unsigned):
            raise LiveAttestationError("origin process identity digest mismatch")
        copied = json.loads(json.dumps(item, ensure_ascii=True))
        rows.append(copied)
        by_role[role] = copied
        started_by_role[role] = started_at
    attestor_pid = by_role["attestor"]["pid"]
    desktop_pid = by_role["desktop"]["pid"]
    unity_pid = by_role["unity"]["pid"]
    bridge_launcher_pid = by_role["bridge_launcher"]["pid"]
    if (
        by_role["attestor"]["supervisorPid"] != 0
        or by_role["desktop"]["supervisorPid"] != attestor_pid
        or by_role["desktop"]["parentPid"] != attestor_pid
        or by_role["backend"]["supervisorPid"] != desktop_pid
        or by_role["backend"]["parentPid"] != desktop_pid
        or by_role["unity"]["supervisorPid"] != attestor_pid
        or by_role["unity"]["parentPid"] != attestor_pid
        or by_role["bridge_launcher"]["supervisorPid"] != unity_pid
        or by_role["bridge_launcher"]["parentPid"] != unity_pid
        or by_role["bridge_listener"]["supervisorPid"] != bridge_launcher_pid
        or by_role["bridge_listener"]["parentPid"] != bridge_launcher_pid
    ):
        raise LiveAttestationError("origin process supervision chain mismatch")
    if started_by_role["attestor"] > issued_at:
        raise LiveAttestationError("origin attestor started after ticket issuance")
    parent_roles = {
        "desktop": "attestor",
        "backend": "desktop",
        "unity": "attestor",
        "bridge_launcher": "unity",
        "bridge_listener": "bridge_launcher",
    }
    for child_role, parent_role in parent_roles.items():
        if started_by_role[child_role] < started_by_role[parent_role]:
            raise LiveAttestationError("origin child process predates its parent")
    return _digest_json(rows), by_role, started_by_role


def _validate_network_bindings(
    value: Any,
    *,
    process_identities: Mapping[str, Mapping[str, Any]],
    issued_at: datetime,
    latest_allowed: datetime,
) -> tuple[str, datetime]:
    if not isinstance(value, list) or len(value) != len(_NETWORK_ROLES):
        raise LiveAttestationError("origin network binding is incomplete")
    expected = {
        "app": (8757, process_identities.get("backend")),
        "bridge": (8080, process_identities.get("bridge_listener")),
    }
    rows: list[dict[str, Any]] = []
    latest_observed = issued_at
    for index, item in enumerate(value):
        if not isinstance(item, Mapping):
            raise LiveAttestationError("origin network binding entry is invalid")
        _require_exact_fields(item, _NETWORK_BINDING_FIELDS, "origin network binding entry")
        role = str(item.get("role") or "")
        if index >= len(_NETWORK_ROLES) or role != _NETWORK_ROLES[index]:
            raise LiveAttestationError("origin network binding role order is invalid")
        expected_port, expected_process = expected[role]
        expected_pid = expected_process.get("pid") if expected_process else None
        expected_identity = (
            expected_process.get("identityDigest") if expected_process else None
        )
        owner_pid = item.get("ownerPid")
        local_port = item.get("localPort")
        if (
            item.get("protocol") != "tcp"
            or item.get("localAddress") != "127.0.0.1"
            or item.get("state") != "listen"
            or not isinstance(local_port, int)
            or isinstance(local_port, bool)
            or local_port != expected_port
            or not isinstance(owner_pid, int)
            or isinstance(owner_pid, bool)
            or owner_pid != expected_pid
            or item.get("ownerIdentityDigest") != expected_identity
        ):
            raise LiveAttestationError("origin network port owner mismatch")
        observed_at = _require_timestamp(
            item.get("observedAt"), "origin network observation"
        )
        if expected_process is None:
            raise LiveAttestationError("origin network process is unavailable")
        owner_started_at = _require_timestamp(
            expected_process.get("startedAt"), "origin network owner start"
        )
        if observed_at < max(issued_at, owner_started_at) or observed_at > latest_allowed:
            raise LiveAttestationError("origin network observation escaped the ticket window")
        latest_observed = max(latest_observed, observed_at)
        rows.append(json.loads(json.dumps(item, ensure_ascii=True)))
    return _digest_json(rows), latest_observed


def _validate_cleanup(
    value: Any,
    *,
    signed_at: datetime,
    earliest_observation: datetime,
) -> str:
    if not isinstance(value, Mapping):
        raise LiveAttestationError("origin cleanup is invalid")
    _require_exact_fields(value, _CLEANUP_FIELDS, "origin cleanup")
    for field_name in _CLEANUP_FIELDS - {"observedAt"}:
        if value.get(field_name) is not True:
            raise LiveAttestationError("origin cleanup is incomplete")
    observed_at = _require_timestamp(value.get("observedAt"), "origin cleanup observation")
    if (
        observed_at < earliest_observation
        or observed_at > signed_at
        or signed_at - observed_at > _MAX_CLOCK_SKEW
    ):
        raise LiveAttestationError("origin cleanup timestamp is invalid")
    return _digest_json(value)


def _stable_read(path: Path, maximum_size: int) -> bytes:
    if not path.is_file() or _is_reparse_point(path):
        raise LiveAttestationError("origin trust file is unavailable")
    try:
        with path.open("rb") as handle:
            before = os.fstat(handle.fileno())
            content = handle.read(maximum_size + 1)
            after = os.fstat(handle.fileno())
        current = path.stat()
    except OSError as exc:
        raise LiveAttestationError("origin trust file could not be read") from exc
    identity = lambda item: (item.st_dev, item.st_ino, item.st_size, item.st_mtime_ns)
    if (
        len(content) > maximum_size
        or identity(before) != identity(after)
        or identity(after) != identity(current)
    ):
        raise LiveAttestationError("origin trust file changed during reading")
    return content


def _is_reparse_point(path: Path) -> bool:
    try:
        info = path.lstat()
    except OSError:
        return False
    reparse_flag = getattr(stat, "FILE_ATTRIBUTE_REPARSE_POINT", 0x400)
    return path.is_symlink() or bool(getattr(info, "st_file_attributes", 0) & reparse_flag)


def _decode_base64url(value: Any, *, expected_size: int, label: str) -> bytes:
    if not isinstance(value, str) or not value or _BASE64URL_RE.fullmatch(value) is None:
        raise LiveAttestationError(f"{label} encoding is invalid")
    padding = "=" * ((4 - len(value) % 4) % 4)
    try:
        decoded = base64.b64decode(value + padding, altchars=b"-_", validate=True)
    except (ValueError, TypeError) as exc:
        raise LiveAttestationError(f"{label} encoding is invalid") from exc
    if len(decoded) != expected_size or _base64url(decoded) != value:
        raise LiveAttestationError(f"{label} length is invalid")
    return decoded


def _base64url(value: bytes) -> str:
    return base64.urlsafe_b64encode(value).rstrip(b"=").decode("ascii")


def _require_public_safe(value: Any) -> None:
    if redact_public_evidence(value) != value:
        raise LiveAttestationError("private origin evidence rejected")


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


def _require_timestamp(value: Any, label: str) -> datetime:
    if not isinstance(value, str) or not value.endswith("Z"):
        raise LiveAttestationError(f"{label} is invalid")
    try:
        parsed = datetime.fromisoformat(value[:-1] + "+00:00")
    except ValueError as exc:
        raise LiveAttestationError(f"{label} is invalid") from exc
    return parsed.astimezone(timezone.utc)


def _utc_now(value: datetime) -> datetime:
    if value.tzinfo is None:
        raise LiveAttestationError("origin timestamp must be timezone-aware")
    return value.astimezone(timezone.utc)


def _canonical_bytes(value: Any) -> bytes:
    return json.dumps(
        value,
        ensure_ascii=True,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")


def _digest_json(value: Any) -> str:
    return hashlib.sha256(_canonical_bytes(value)).hexdigest()


def _camel_to_snake(value: str) -> str:
    return re.sub(r"(?<!^)(?=[A-Z])", "_", value).lower()


__all__ = [
    "ORIGIN_ENVELOPE_SCHEMA",
    "ORIGIN_PROOF_ALGORITHM",
    "ORIGIN_TICKET_SCHEMA",
    "ORIGIN_TRUST_KIND",
    "ORIGIN_TRUST_SCHEMA",
    "OriginExpectedBinding",
    "OriginReplayGuard",
    "OriginTrustContext",
    "load_origin_trust_context",
    "parse_origin_trust_context",
    "verify_trusted_live_origin",
]
