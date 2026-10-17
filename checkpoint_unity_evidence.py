"""Project-local Core evidence references, retained for an incident's lifetime.

These are ordinary files owned by the Editor's current OS identity. No network
endpoint or new authentication boundary is introduced; the authenticated Core
response supplies the expected digest, and the backend verifies it before use.
"""
from __future__ import annotations

import hashlib
import json
import re
import stat
from collections.abc import Mapping
from pathlib import Path, PurePosixPath
from typing import Any

_REFERENCE = re.compile(r"Library/VRCForge/CheckpointEvidence/[0-9a-f]{32}\.json\Z")
_DIGEST = re.compile(r"[0-9a-f]{64}\Z")
_PROJECT_DISK_ROOTS = ("Assets", "Packages", "ProjectSettings")
_REDACT_PATH = re.compile(r"(?:[A-Za-z]:[\\/]|/)[^\s\"']{2,}")
_REDACT_SECRET = re.compile(r"(?i)(?:bearer\s+|token[=:]\s*|api[_-]?key[=:]\s*)[^\s,;]+")


def _evidence_path(project_root: Path, reference: str) -> Path:
    if not _REFERENCE.fullmatch(reference):
        raise ValueError("Invalid checkpoint evidence reference.")
    root = project_root.resolve(strict=True)
    current = root
    for part in PurePosixPath(reference).parts:
        current = current / part
        info = current.lstat()
        if stat.S_ISLNK(info.st_mode) or getattr(info, 'st_file_attributes', 0) & 0x400:
            raise ValueError("Checkpoint evidence must not traverse links or reparse points.")
    if not current.is_file() or root not in current.resolve(strict=True).parents:
        raise ValueError("Checkpoint evidence is outside the selected project.")
    return current


def validate_evidence_reference(project_root: Path, value: Any) -> dict[str, Any]:
    if not isinstance(value, dict):
        raise ValueError("Checkpoint evidence must be a reference object.")
    reference, digest = value.get('reference'), value.get('sha256')
    byte_count, object_count = value.get('byteCount'), value.get('objectCount')
    if (not isinstance(reference, str) or not isinstance(digest, str)
            or not _DIGEST.fullmatch(digest)
            or type(byte_count) is not int or byte_count < 0
            or type(object_count) is not int or object_count < 0):
        raise ValueError("Checkpoint evidence metadata is invalid.")
    path = _evidence_path(project_root, reference)
    raw = path.read_bytes()
    if len(raw) != byte_count or hashlib.sha256(raw).hexdigest() != digest:
        raise ValueError("Checkpoint evidence bytes do not match the Core receipt.")
    content = json.loads(raw.decode('utf-8'))
    if not isinstance(content, list) or len(content) != object_count:
        raise ValueError("Checkpoint evidence object count is invalid.")
    summary = value.get('summary', {})
    if len(json.dumps(summary, ensure_ascii=False, separators=(',', ':')).encode('utf-8')) > 4096:
        raise ValueError("Checkpoint evidence summary exceeds its bound.")
    return {'reference': reference, 'sha256': digest, 'byteCount': byte_count,
            'objectCount': object_count, 'summary': summary}


def collect_evidence_references(project_root: Path, result: dict[str, Any]) -> tuple[list[dict[str, Any]], list[str]]:
    references, warnings = [], []
    transport = result.get('result')
    envelope = transport.get('payload') if isinstance(transport, dict) else None
    structured = envelope.get('structuredContent') if isinstance(envelope, dict) else None
    data = structured.get('data') if isinstance(structured, dict) else None
    source = {**(data if isinstance(data, dict) else {}), **result}
    for field in (
        'dirtyAssetEvidenceBefore', 'dirtyAssetEvidenceAfter',
        'recoveryEvidenceBefore', 'recoveryEvidenceAfter',
    ):
        value = source.get(field)
        if not isinstance(value, dict) or 'reference' not in value:
            continue
        try:
            references.append(validate_evidence_reference(project_root, value))
        except (OSError, ValueError, UnicodeError) as exc:
            warnings.append(f'{field}: {exc}')
    return references, warnings


def cleanup_recovery_evidence(recovery: dict[str, Any]) -> dict[str, Any]:
    """Delete only registered exact-name, exact-digest files after incident close."""
    result: dict[str, Any] = {'deleted': [], 'preserved': [], 'errors': []}
    if recovery.get('status') in {'applying', 'needs_recovery', 'restore_failed'}:
        return result
    root = Path(str(recovery.get('projectRoot') or ''))
    for ref in recovery.get('unityEvidenceReferences', []):
        name = str(ref.get('reference') or '') if isinstance(ref, dict) else ''
        try:
            verified = validate_evidence_reference(root, ref)
            _evidence_path(root, verified['reference']).unlink()
            result['deleted'].append(name)
        except FileNotFoundError:
            # A preceding successful close may already have removed this file.
            continue
        except (OSError, ValueError, UnicodeError) as exc:
            result['preserved'].append(name)
            result['errors'].append(f'{name}: {exc}')
    return result


def checkpoint_core_error(
    exc: Exception,
    *,
    request_id: str = "",
    phase: str = "",
) -> dict[str, Any]:
    """Keep bounded transport diagnostics, including the original size receipt."""
    def bounded(value: Any, depth: int = 0) -> Any:
        if depth > 4:
            return '[truncated]'
        if isinstance(value, dict):
            return {str(k)[:100]: bounded(v, depth + 1) for k, v in list(value.items())[:16]}
        if isinstance(value, list):
            return [bounded(item, depth + 1) for item in value[:8]]
        if isinstance(value, str):
            return value[:256]
        return value if value is None or isinstance(value, (bool, int, float)) else str(value)[:256]
    result: dict[str, Any] = {
        'ok': False,
        'error': str(exc)[:2048],
        'exceptionType': type(exc).__name__,
        'requestId': str(request_id or '')[:128],
        'phase': str(phase or '')[:64],
    }
    details = getattr(exc, 'details', None)
    if isinstance(details, dict):
        result['details'] = bounded(details)
        if len(json.dumps(result['details'], ensure_ascii=True)) > 8192:
            data = details.get('data') if isinstance(details.get('data'), dict) else {}
            result['details'] = {'kind': str(details.get('kind', ''))[:100],
                                 'truncated': True, 'data': {
                key: data[key] for key in ('actualResponseBytes', 'maxResponseBytes')
                if type(data.get(key)) is int}}
    return result


def _sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        while chunk := handle.read(1024 * 1024):
            digest.update(chunk)
    return digest.hexdigest()


def _is_reparse_or_symlink(path: Path) -> bool:
    info = path.lstat()
    return stat.S_ISLNK(info.st_mode) or bool(getattr(info, "st_file_attributes", 0) & 0x400)


def capture_project_disk_snapshot(project_root: Path) -> dict[str, Any]:
    """Hash the complete user project surface used by checkpoint restore.

    Missing directories and files are represented explicitly.  This is a
    read-only probe; Library and diagnostic evidence are intentionally outside
    the snapshot because they are generated runtime material.
    """
    root = project_root.resolve()
    rows: list[dict[str, Any]] = []
    errors: list[str] = []
    try:
        if not root.is_dir():
            return {"valid": False, "error": "Unity project root is unavailable.", "roots": []}
        for name in _PROJECT_DISK_ROOTS:
            base = root / name
            try:
                base_reparse = _is_reparse_or_symlink(base)
            except FileNotFoundError:
                base_reparse = False
            except OSError as exc:
                rows.append({"path": name, "exists": True, "kind": "invalid"})
                errors.append(f"{name}: {type(exc).__name__}")
                continue
            if base_reparse:
                rows.append({"path": name, "exists": True, "kind": "link"})
                errors.append(f"{name}: linked paths are not admissible")
                continue
            if not base.exists():
                rows.append({"path": name, "exists": False, "kind": "missing"})
                continue
            if _is_reparse_or_symlink(base) or not base.is_dir():
                rows.append({"path": name, "exists": True, "kind": "invalid"})
                errors.append(f"{name}: expected a regular directory")
                continue
            rows.append({"path": name, "exists": True, "kind": "directory"})
            for path in sorted(base.rglob("*")):
                relative = path.relative_to(root).as_posix()
                try:
                    reparse = _is_reparse_or_symlink(path)
                except OSError as exc:
                    rows.append({"path": relative, "exists": True, "kind": "invalid"})
                    errors.append(f"{relative}: {type(exc).__name__}")
                    continue
                if reparse:
                    rows.append({"path": relative, "exists": True, "kind": "link"})
                    errors.append(f"{relative}: linked paths are not admissible")
                    continue
                if path.is_dir():
                    rows.append({"path": relative, "exists": True, "kind": "directory"})
                    continue
                if not path.is_file():
                    rows.append({"path": relative, "exists": True, "kind": "invalid"})
                    errors.append(f"{relative}: unsupported filesystem entry")
                    continue
                try:
                    rows.append({
                        "path": relative,
                        "exists": True,
                        "kind": "file",
                        "bytes": path.stat().st_size,
                        "sha256": _sha256_file(path),
                    })
                except OSError as exc:
                    errors.append(f"{relative}: {type(exc).__name__}")
    except OSError as exc:
        errors.append(f"snapshot: {type(exc).__name__}")
    rows.sort(key=lambda item: str(item.get("path") or ""))
    payload = {"valid": not errors, "roots": rows}
    if errors:
        payload["errors"] = errors[:32]
    payload["digest"] = hashlib.sha256(
        json.dumps(rows, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8")
    ).hexdigest() if not errors else ""
    return payload


def _result_data(result: Mapping[str, Any] | Any) -> dict[str, Any]:
    if not isinstance(result, Mapping):
        return {}
    source: dict[str, Any] = dict(result)
    transport = source.get("result")
    if isinstance(transport, Mapping):
        payload = transport.get("payload")
        if isinstance(payload, Mapping):
            structured = payload.get("structuredContent")
            if isinstance(structured, Mapping):
                data = structured.get("data")
                if isinstance(data, Mapping):
                    source = {**dict(data), **source}
    for nested_key in ("unityRestorePrepare", "unityReload", "restorePrepare", "reloadResult"):
        nested = source.get(nested_key)
        if isinstance(nested, Mapping):
            source = {**dict(nested), **source}
    return source


def _canonical(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"), default=str)


def _load_witness_reference(project_root: Path | None, value: Any) -> dict[str, Any]:
    if project_root is None:
        return {"valid": False, "error": "project root unavailable"}
    try:
        metadata = validate_evidence_reference(project_root, value)
        raw = _evidence_path(project_root, metadata["reference"]).read_bytes()
        document = json.loads(raw.decode("utf-8"))
        if not isinstance(document, list) or len(document) != metadata["objectCount"]:
            raise ValueError("witness object count is invalid")
        witness = document[0] if len(document) == 1 and isinstance(document[0], Mapping) else None
        if not isinstance(witness, Mapping) or witness.get("schema") != "vrcforge.recovery_witness.v1":
            raise ValueError("recovery witness schema is invalid")
        if witness.get("complete") is not True:
            raise ValueError("recovery witness is incomplete")
        if witness.get("serializationErrors"):
            raise ValueError("recovery witness contains serialization errors")
        dirty_assets = witness.get("dirtyAssets")
        scenes = witness.get("loadedScenes")
        identity = witness.get("coreIdentity")
        if not identity or not isinstance(dirty_assets, list) or not isinstance(scenes, list):
            raise ValueError("recovery witness identity or topology is incomplete")
        if any(item.get("serializedComplete") is not True for item in [*dirty_assets, *scenes] if isinstance(item, Mapping)):
            raise ValueError("recovery witness contains incomplete rows")
        witness_identity = {
            key: witness.get(key)
            for key in (
                "coreIdentity", "coreInstanceId", "coreProductIdentity",
                "coreVersion", "coreContractVersion", "unityProcessId",
                "unityProcessStartedAtUtc", "projectPathDigest",
            )
            if witness.get(key) not in (None, "")
        }
        if (not isinstance(witness_identity.get("coreIdentity"), str)
                or witness_identity.get("coreIdentity") != witness_identity.get("coreInstanceId")
                or not re.fullmatch(r"[0-9a-f]{32}", witness_identity["coreIdentity"])
                or type(witness_identity.get("unityProcessId")) is not int
                or witness_identity["unityProcessId"] <= 0
                or not isinstance(witness_identity.get("unityProcessStartedAtUtc"), str)
                or not witness_identity["unityProcessStartedAtUtc"]
                or not _DIGEST.fullmatch(str(witness_identity.get("projectPathDigest") or ""))):
            raise ValueError("recovery witness live identity is incomplete")
        expected_project_digest = hashlib.sha256(project_root.resolve().as_posix().encode("utf-8")).hexdigest()
        if witness_identity["projectPathDigest"] != expected_project_digest:
            raise ValueError("recovery witness project identity differs")
        for item in [*dirty_assets, *scenes]:
            if not isinstance(item, Mapping) or item.get("serializedComplete") is not True:
                raise ValueError("recovery witness contains invalid rows")
        if any(not _DIGEST.fullmatch(str(item.get("serializedFingerprint") or "")) for item in dirty_assets):
            raise ValueError("recovery witness dirty fingerprint is incomplete")
        if any(not _DIGEST.fullmatch(str(item.get("topologyFingerprint") or "")) for item in scenes):
            raise ValueError("recovery witness scene fingerprint is incomplete")
        dirty = [
            {
                key: item.get(key)
                for key in ("assetPath", "instanceId", "type", "dirty", "dirtyCount", "serializedFingerprint", "serializedComplete")
            }
            for item in dirty_assets if isinstance(item, Mapping)
        ]
        topology = [
            {
                key: item.get(key)
                for key in ("handle", "path", "name", "loaded", "active", "dirty", "knownTransientPreview", "topologyFingerprint", "serializedComplete")
            }
            for item in scenes if isinstance(item, Mapping)
        ]
        return {
            "valid": True,
            "identity": witness_identity,
            "dirty": dirty,
            "scenes": topology,
            "activeScenePath": next((item.get("path") for item in topology if item.get("active")), ""),
            "requestId": str(metadata.get("summary", {}).get("requestId") or "")[:128],
            "mutationObserved": False,
            "reference": metadata["reference"],
            "sha256": metadata["sha256"],
        }
    except (OSError, ValueError, UnicodeError, json.JSONDecodeError) as exc:
        return {"valid": False, "error": str(exc)[:256]}


def capture_core_observation(
    result: Mapping[str, Any] | Any,
    *,
    side: str = "",
    project_root: Path | None = None,
) -> dict[str, Any]:
    """Extract a bounded Core observation without treating ``ok`` as proof."""
    source = _result_data(result)
    nested = source.get("recoveryEvidence")
    if isinstance(nested, Mapping):
        source = {**dict(nested), **source}
    evidence_key = (
        "recoveryEvidenceBefore" if side == "before" else
        "recoveryEvidenceAfter" if side == "after" else
        "recoveryEvidence"
    )
    if evidence_key in source:
        # A Core descriptor is the production proof surface.  If it is
        # present but tampered/incomplete, do not fall back to weak booleans or
        # transport summaries.
        if source.get("recoveryEvidenceComplete") is not True:
            return {"valid": False, "error": "recovery witness envelope is incomplete"}
        observed = _load_witness_reference(project_root, source.get(evidence_key))
        observed["mutationObserved"] = bool(observed.get("mutationObserved") or _mutation_observed(source))
        return observed
    return {"valid": False, "error": "A complete serialized Core recovery witness is required.",
            "mutationObserved": _mutation_observed(source)}


def _mutation_observed(source: Mapping[str, Any]) -> bool:
    for key in ("mutationStarted", "mutationApplied", "committed"):
        if source.get(key) is True:
            return True
    for key in ("transaction", "sceneTransaction", "transactions", "closedScenes", "reopenedScenes", "reopenErrors"):
        value = source.get(key)
        if isinstance(value, Mapping) and value:
            return True
        if isinstance(value, (list, tuple)) and value:
            return True
    return False


def classify_restore_failure(
    before_disk: Mapping[str, Any] | None,
    after_disk: Mapping[str, Any] | None,
    before_core: Mapping[str, Any] | None,
    after_core: Mapping[str, Any] | None,
    *observations: Mapping[str, Any] | None,
) -> dict[str, Any]:
    """Classify a failed restore conservatively.

    A zero-effect exemption requires valid disk hashes, complete Core
    observations from the same Unity instance, equal dirty-asset evidence and
    loaded-scene topology, and no observed transaction.  Boolean ``ok`` or
    ``mutationStarted`` flags alone are deliberately ignored.
    """
    disk_valid = bool(before_disk and after_disk and before_disk.get("valid") and after_disk.get("valid"))
    disk_equal = bool(disk_valid and before_disk.get("digest") == after_disk.get("digest"))
    core_valid = bool(before_core and after_core and before_core.get("valid") and after_core.get("valid"))
    same_instance = bool(core_valid and _canonical(before_core.get("identity")) == _canonical(after_core.get("identity")))
    dirty_equal = bool(core_valid and _canonical(before_core.get("dirty")) == _canonical(after_core.get("dirty")))
    scenes_equal = bool(
        core_valid
        and _canonical(before_core.get("scenes")) == _canonical(after_core.get("scenes"))
        and _canonical(before_core.get("activeScenePath")) == _canonical(after_core.get("activeScenePath"))
    )
    mutation_observed = any(bool(item and item.get("mutationObserved")) for item in observations)
    if disk_valid and not disk_equal:
        classification = "changed"
    elif mutation_observed:
        classification = "changed"
    elif core_valid and same_instance and not (dirty_equal and scenes_equal):
        classification = "changed"
    elif disk_equal and core_valid and same_instance and dirty_equal and scenes_equal:
        classification = "proven_zero_effect"
    else:
        classification = "unknown"
    return {
        "classification": classification,
        "proofStatus": classification,
        "zeroEffectExempt": classification == "proven_zero_effect",
        "counted": classification != "proven_zero_effect",
        "diskProof": {"valid": disk_valid, "equal": disk_equal},
        "coreProof": {
            "valid": core_valid,
            "sameInstance": same_instance,
            "dirtyAssetsEqual": dirty_equal,
            "loadedScenesEqual": scenes_equal,
        },
        "mutationObserved": mutation_observed,
        "beforeDigest": str(before_disk.get("digest") or "") if before_disk else "",
        "afterDigest": str(after_disk.get("digest") or "") if after_disk else "",
    }


def redact_restore_failure_diagnostic(
    result: Mapping[str, Any] | None,
    proof: Mapping[str, Any],
) -> dict[str, Any]:
    source = _result_data(result or {})
    phase = str(source.get("phase") or source.get("recoveryPhase") or "restore")[:64]
    exception_type = str(source.get("exceptionType") or source.get("errorType") or "RestoreFailure")[:128]
    message = str(source.get("message") or source.get("error") or "")[:512]
    message = _REDACT_SECRET.sub("[redacted]", _REDACT_PATH.sub("[path]", message))
    message = message or "restore operation failed"
    request_id = str(source.get("requestId") or source.get("request_id") or "")[:128]
    return {
        "schema": "vrcforge.restore_failure_diagnostic.v1",
        "phase": phase,
        "exceptionType": exception_type,
        "message": message,
        "requestId": request_id,
        "classification": str(proof.get("classification") or "unknown"),
        "counted": bool(proof.get("counted", True)),
    }


def record_unity_recovery_evidence(service: Any, checkpoint: dict[str, Any], result: dict[str, Any]) -> dict[str, Any] | None:
    """Persist immediately, before a safety backup or subsequent restore can fail."""
    recovery = service._select_apply_recovery({'checkpointId': checkpoint.get('id')}, include_resolved=False)
    references, warnings = collect_evidence_references(Path(str(checkpoint.get('projectRoot') or '')), result)
    if not references and not warnings:
        return recovery
    owner = recovery or service._load_checkpoint(str(checkpoint.get('id') or '')) or checkpoint
    combined = list(owner.get('unityEvidenceReferences') or [])
    known = {(item.get('reference'), item.get('sha256')) for item in combined if isinstance(item, dict)}
    for ref in references:
        identity = (ref['reference'], ref['sha256'])
        if identity not in known:
            combined.append(ref)
            known.add(identity)
    updated = {**owner, 'unityEvidenceReferences': combined}
    if warnings:
        updated['unityEvidenceWarnings'] = [*(owner.get('unityEvidenceWarnings') or []), *warnings]
        result.update(ok=False, errorCode='checkpoint_evidence_invalid',
                      error='Core checkpoint evidence could not be verified.', evidenceWarnings=warnings)
    if recovery:
        return service._append_apply_recovery_entry(updated)
    service._append_checkpoint(updated)
    return None


def cleanup_checkpoint_evidence(service: Any, checkpoint_id: str) -> None:
    checkpoint = service._load_checkpoint(checkpoint_id)
    if not checkpoint or not checkpoint.get('unityEvidenceReferences'):
        return
    cleanup = cleanup_recovery_evidence({**checkpoint, 'status': 'closed'})
    service._append_checkpoint({**checkpoint, 'unityEvidenceCleanup': cleanup})
    service._ports.append_audit({'event': 'checkpoint_unity_evidence_cleaned',
                                 'checkpointId': checkpoint_id, **cleanup})


def restore_failure_turn_stop(payload: Any) -> dict[str, Any] | None:
    """Read the trusted restore receipt without traversing arbitrary tool text."""
    pending = [(payload, 0)]
    while pending:
        value, depth = pending.pop()
        if not isinstance(value, Mapping) or depth > 8:
            continue
        control = value.get("turnControl")
        if (isinstance(control, Mapping) and control.get("stopCurrentTurn") is True
                and control.get("automaticRetry") is False and value.get("ok") is False):
            return dict(value)
        for key in ("result", "execution", "data", "receipt"):
            if isinstance(value.get(key), Mapping):
                pending.append((value[key], depth + 1))
    return None


def restore_failure_terminal_plan(
    plan: Mapping[str, Any], tool: str, payload: Mapping[str, Any],
) -> dict[str, Any] | None:
    """Build the terminal plan that prevents retry after a restore failure."""
    tool_name = str(tool or "").strip().casefold()
    restore_tool = tool_name == "vrcforge_restore_checkpoint"
    if not restore_tool:
        return None
    trusted = restore_failure_turn_stop(payload)
    payload_status = str(payload.get("status") or "").strip().casefold().replace("-", "_")
    outcome = payload.get("outcome") if isinstance(payload.get("outcome"), Mapping) else {}
    outcome_status = str(outcome.get("status") or "").strip().casefold().replace("-", "_")
    if not (
        trusted is not None
        or payload.get("ok") is False
        or payload_status in {"failed", "error", "rejected", "denied", "cancelled"}
        or outcome_status in {"failed", "error", "rejected", "denied", "cancelled"}
    ):
        return None
    result = payload.get("result") if isinstance(payload.get("result"), Mapping) else {}
    summary = str(
        (trusted or {}).get("failureSummary")
        or payload.get("failureSummary")
        or result.get("failureSummary")
        or outcome.get("summary")
        or result.get("error")
        or payload.get("error")
        or (trusted or {}).get("message")
        or "Checkpoint restore failed; recovery remains required."
    ).strip()
    terminal = dict(plan)
    terminal.update({
        "summary": summary,
        "reply": summary,
        "failureSummary": summary,
        "continueLoop": False,
        "nextStep": "tool_failed",
        "restoreFailureTurnStop": True,
        "completionGate": {
            **(dict(terminal.get("completionGate"))
               if isinstance(terminal.get("completionGate"), Mapping) else {}),
            "status": "failed",
            "reason": "restore_failure_turn_stop",
            "checkpointRecoveryRequired": True,
        },
    })
    terminal["restoreFailure"] = dict(trusted or payload)
    return terminal
